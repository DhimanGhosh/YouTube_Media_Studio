"""Tests for album-wide metadata inspection and editing."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.gui.operations import execute_operation
from youtube_audio_video_downloader.services.album_editor import (
    AlbumEditResult,
    _rename_album_file,
    edit_album_folder,
    inspect_album_folder,
)
from youtube_audio_video_downloader.services.media_metadata import EditableMediaMetadata


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
            with pytest.raises(ValueError, match="Artist"):
                edit_album_folder(directory, {"album": "Album", "artists": ""})
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
            "ai_enabled": False,
        },
        CancellationToken(),
    )
    assert summary.operation == "edit_album"
    assert summary.total == 3
    assert summary.tagged == 2
    assert summary.failed == 1
    assert summary.failed_items == ("broken.mp3",)
