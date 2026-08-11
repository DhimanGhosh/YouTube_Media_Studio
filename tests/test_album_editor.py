"""Tests for album-wide metadata inspection and editing."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from mutagen.id3 import ID3, TIT2, TPE1

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.gui.operations import execute_operation
from youtube_audio_video_downloader.services.album_editor import (
    AlbumEditResult,
    _rename_album_file,
    edit_album_folder,
    inspect_album_folder,
)
from youtube_audio_video_downloader.services.media_metadata import (
    EditableMediaMetadata,
    read_media_metadata,
)


def test_inspection_reports_shared_and_mixed_album_fields() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        first = root / "one.mp3"
        second = root / "disc2" / "two.flac"
        second.parent.mkdir()
        first.touch()
        second.touch()

        def metadata(path: Path) -> EditableMediaMetadata:
            return EditableMediaMetadata(
                album="Shared Album",
                year="1999" if Path(path).name == "one.mp3" else "2000",
                artists="Shared Artist",
                artwork_present=Path(path).name == "one.mp3",
            )

        with patch(
            "youtube_audio_video_downloader.services.album_editor.read_media_metadata",
            side_effect=metadata,
        ):
            result = inspect_album_folder(root)

        assert set(result.files) == {first, second}
        assert result.album == "Shared Album"
        assert result.year == ""
        assert result.artists == "Shared Artist"
        assert result.mixed_fields == ("year",)
        assert result.artwork_files == 1


def test_edit_updates_only_requested_album_level_fields_for_every_file() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        first = root / "one.mp3"
        second = root / "two.m4a"
        ignored = root / "notes.txt"
        for path in (first, second, ignored):
            path.touch()
        with (
            patch(
                "youtube_audio_video_downloader.services.album_editor.read_media_metadata",
                return_value=EditableMediaMetadata(title="Song"),
            ),
            patch(
                "youtube_audio_video_downloader.services.album_editor.replace_media_metadata"
            ) as replace_mock,
            patch(
                "youtube_audio_video_downloader.services.album_editor._rename_album_file",
                side_effect=lambda path, _title, _values: path,
            ),
        ):
            result = edit_album_folder(
                root,
                {"album": "New Album", "year": "2026", "artists": "Solo Artist, Guest"},
            )

        assert result.updated == (first, second)
        assert result.failed == ()
        assert [call.args[0] for call in replace_mock.call_args_list] == [first, second]
        assert all(
            call.args[1]
            == {"album": "New Album", "year": "2026", "artists": "Solo Artist, Guest"}
            for call in replace_mock.call_args_list
        )


def test_blank_artist_override_preserves_each_tracks_existing_artist() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        first = root / "one.mp3"
        second = root / "two.m4a"
        first.touch()
        second.touch()

        def metadata(path: Path) -> EditableMediaMetadata:
            is_first = Path(path).name == "one.mp3"
            return EditableMediaMetadata(
                title="First Song" if is_first else "Second Song",
                artists="Artist One" if is_first else "Artist Two, Guest",
            )

        with (
            patch(
                "youtube_audio_video_downloader.services.album_editor.read_media_metadata",
                side_effect=metadata,
            ),
            patch(
                "youtube_audio_video_downloader.services.album_editor.replace_media_metadata"
            ) as replace_mock,
            patch(
                "youtube_audio_video_downloader.services.album_editor._rename_album_file",
                side_effect=lambda path, _title, _values: path,
            ) as rename_mock,
        ):
            result = edit_album_folder(
                root,
                {"album": "Soundtrack", "year": "2026", "artists": ""},
            )

        assert result.updated == (first, second)
        assert [call.args[1] for call in replace_mock.call_args_list] == [
            {"album": "Soundtrack", "year": "2026"},
            {"album": "Soundtrack", "year": "2026"},
        ]
        assert [call.args[2]["artists"] for call in rename_mock.call_args_list] == [
            "Artist One",
            "Artist Two, Guest",
        ]


def test_edit_applies_one_artwork_image_to_every_file() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        first = root / "one.mp3"
        second = root / "two.flac"
        artwork = root / "cover.jpg"
        for path in (first, second, artwork):
            path.touch()
        with (
            patch(
                "youtube_audio_video_downloader.services.album_editor.read_media_metadata",
                return_value=EditableMediaMetadata(title="Song"),
            ),
            patch(
                "youtube_audio_video_downloader.services.album_editor.replace_media_metadata"
            ) as replace_mock,
            patch(
                "youtube_audio_video_downloader.services.album_editor._rename_album_file",
                side_effect=lambda path, _title, _values: path,
            ),
        ):
            edit_album_folder(
                root,
                {"album": "Album", "year": "2026", "artists": "Artist"},
                artwork_path=artwork,
            )

        assert replace_mock.call_count == 2
        assert all(
            call.kwargs == {"artwork_path": str(artwork), "remove_artwork": False}
            for call in replace_mock.call_args_list
        )


def test_edit_rejects_replacement_and_removal_together() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "song.mp3"
        source.touch()
        with pytest.raises(ValueError, match="replacement artwork"):
            edit_album_folder(
                directory,
                {"album": "Album", "artists": "Artist"},
                artwork_path="cover.jpg",
                remove_artwork=True,
            )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg")
def test_edit_embeds_artwork_and_shared_tags_in_real_album_files() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        sources = [root / "one.mp3", root / "two.mp3"]
        for index, source in enumerate(sources, start=1):
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=frequency={400 + index}:duration=0.1",
                    "-codec:a", "libmp3lame", str(source),
                ],
                check=True,
            )
            tags = ID3(source)
            tags.add(TIT2(encoding=3, text=f"Song {index}"))
            tags.save(source)
        artwork = root / "cover.jpg"
        artwork.write_bytes(b"\xff\xd8\xff\xd9")

        result = edit_album_folder(
            root,
            {"album": "Shared Album", "year": "2026", "artists": "Shared Artist"},
            artwork_path=artwork,
        )

        assert result.failed == ()
        assert len(result.updated) == 2
        for updated_path in result.updated:
            loaded = read_media_metadata(updated_path)
            assert loaded.album == "Shared Album"
            assert loaded.year == "2026"
            assert loaded.artists == "Shared Artist"
            assert loaded.artwork_present


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg")
def test_blank_override_preserves_real_per_track_artist_tags_and_names() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        sources = [root / "one.mp3", root / "two.mp3"]
        expected = {"Song 1": "Artist One", "Song 2": "Artist Two, Guest"}
        for index, source in enumerate(sources, start=1):
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=frequency={400 + index}:duration=0.1",
                    "-codec:a", "libmp3lame", str(source),
                ],
                check=True,
            )
            title = f"Song {index}"
            tags = ID3(source)
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TPE1(encoding=3, text=expected[title]))
            tags.save(source)

        result = edit_album_folder(
            root,
            {"album": "Soundtrack", "year": "2006", "artists": ""},
        )

        assert result.failed == ()
        assert len(result.updated) == 2
        for updated_path in result.updated:
            loaded = read_media_metadata(updated_path)
            assert loaded.artists == expected[loaded.title]
            assert updated_path.name == (
                f"{loaded.title} - Soundtrack (2006) - {loaded.artists}.mp3"
            )


def test_edit_renames_each_file_from_title_and_new_album_year_and_artists() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "old-name.mp3"
        source.touch()
        with (
            patch(
                "youtube_audio_video_downloader.services.album_editor.read_media_metadata",
                return_value=EditableMediaMetadata(title="First Song"),
            ),
            patch(
                "youtube_audio_video_downloader.services.album_editor.replace_media_metadata"
            ),
        ):
            result = edit_album_folder(
                directory,
                {"album": "New Album", "year": "2026", "artists": "Solo, Guest"},
            )

        assert result.failed == ()
        assert result.updated[0].name == "First Song - New Album (2026) - Solo, Guest.mp3"
        assert result.updated[0].is_file()


def test_album_filename_collision_uses_numbered_suffix() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "old-name.mp3"
        source.touch()
        existing = root / "First Song - New Album (2026) - Solo.mp3"
        existing.touch()

        result = _rename_album_file(
            source,
            "First Song",
            {"album": "New Album", "year": "2026", "artists": "Solo"},
        )

        assert result.name == "First Song - New Album (2026) - Solo (2).mp3"
        assert result.is_file()
        assert existing.is_file()


def test_edit_validates_album_and_year_before_writing() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "song.mp3"
        source.touch()
        with patch(
            "youtube_audio_video_downloader.services.album_editor.replace_media_metadata"
        ) as replace_mock:
            with pytest.raises(ValueError, match="Album name"):
                edit_album_folder(
                    directory, {"album": "", "year": "2026", "artists": "Artist"}
                )
            with pytest.raises(ValueError, match="four-digit"):
                edit_album_folder(
                    directory, {"album": "Album", "year": "old", "artists": "Artist"}
                )
        replace_mock.assert_not_called()


@patch("youtube_audio_video_downloader.gui.operations.edit_album_folder")
def test_gui_operation_reports_album_edit_results(edit_mock) -> None:
    edit_mock.return_value = AlbumEditResult(
        (Path("one.mp3"), Path("two.mp3")),
        ((Path("broken.mp3"), "unreadable"),),
    )
    summary = execute_operation(
        "edit_album",
        {
            "folder": "album",
            "metadata": {"album": "Album", "year": "2026", "artists": "Artist"},
            "artwork_path": "https://example.test/cover.jpg",
            "remove_artwork": False,
            "ai_enabled": False,
        },
        CancellationToken(),
    )
    assert summary.operation == "edit_album"
    assert summary.total == 3
    assert summary.tagged == 2
    assert summary.failed == 1
    assert summary.failed_items == ("broken.mp3",)
    assert edit_mock.call_args.kwargs["artwork_path"] == "https://example.test/cover.jpg"
