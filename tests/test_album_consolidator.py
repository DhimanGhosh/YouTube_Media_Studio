"""Tests for metadata-driven album folder consolidation."""

from __future__ import annotations

import tempfile
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.gui.operations import execute_operation
from youtube_audio_video_downloader.services.album_consolidator import (
    ConsolidationReport,
    _reorder_album_from_wikipedia,
    _wikipedia_track_index,
    _transactional_move,
    album_contains_artist,
    consolidate_albums,
)
from youtube_audio_video_downloader.services.media_metadata import EditableMediaMetadata
from youtube_audio_video_downloader.services.media_metadata import read_media_metadata
from youtube_audio_video_downloader.services.album_metadata_enricher import (
    MetadataEnrichmentReport,
)


class AlbumConsolidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reorder_patcher = patch(
            "youtube_audio_video_downloader.services.album_consolidator._reorder_album_from_wikipedia",
            return_value=0,
        )
        self.reorder_patcher.start()

    def tearDown(self) -> None:
        self.reorder_patcher.stop()

    def test_preserves_year_qualified_artist_hits_collection(self) -> None:
        self.assertFalse(
            album_contains_artist("Hits of Kumar Sanu (1995)", "Kumar Sanu")
        )
        self.assertTrue(album_contains_artist("Hits of Kumar Sanu", "Kumar Sanu"))

    @patch("youtube_audio_video_downloader.services.album_consolidator.read_media_metadata")
    def test_groups_audio_and_video_by_album_without_overwriting(self, read_mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "incoming"
            destination = root / "library"
            (source / "nested").mkdir(parents=True)
            first = source / "first.mp3"
            second = source / "nested" / "second.mp4"
            missing = source / "missing.flac"
            unknown = source / "unknown.ogg"
            for path in (first, second, missing, unknown):
                path.write_bytes(b"media")
            existing = destination / "Album_Name" / first.name
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"existing")

            read_mock.side_effect = lambda path: EditableMediaMetadata(
                album=(
                    "" if Path(path).name == missing.name
                    else "uNkNoWn" if Path(path).name == unknown.name
                    else "Album:Name"
                )
            )
            report = consolidate_albums(source, destination)

            moved = destination / "Album_Name" / second.name
            self.assertEqual(report.scanned, 4)
            self.assertEqual(report.moved, (moved,))
            self.assertEqual(len(report.skipped), 3)
            self.assertTrue(moved.is_file())
            self.assertTrue(first.is_file())
            self.assertTrue(missing.is_file())
            self.assertTrue(unknown.is_file())
            self.assertEqual(existing.read_bytes(), b"existing")
            self.assertNotIn(unknown, [Path(call.args[0]) for call in read_mock.call_args_list])

    @patch("youtube_audio_video_downloader.services.album_consolidator.read_media_metadata")
    def test_agentic_move_leaves_unverified_audio_in_source(self, read_mock) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Song", album="Possibly Wrong (2012)", artists="Artist"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "incoming"
            source.mkdir()
            approved = source / "Approved.mp3"
            review = source / "Needs Review.mp3"
            approved.write_bytes(b"approved")
            review.write_bytes(b"review")

            report = consolidate_albums(
                source,
                root / "library",
                verified_audio_paths=(approved,),
            )

            self.assertEqual(len(report.moved), 1)
            self.assertEqual(report.moved[0].name, approved.name)
            self.assertTrue(review.is_file())
            self.assertTrue(any("left in source for review" in item for item in report.skipped))

    @patch("youtube_audio_video_downloader.services.album_consolidator.replace_media_metadata")
    @patch("youtube_audio_video_downloader.services.album_consolidator.read_media_metadata")
    def test_move_normalizes_soundtrack_release_suffix_before_grouping(
        self, read_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Song",
            album="Byabodhan (Original Motion Picture Soundtrack) - EP",
            artists="Artist",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            track = source / "Song.mp3"
            track.write_bytes(b"audio")

            report = consolidate_albums(source, root / "destination")

            moved = root / "destination" / "Byabodhan" / "Song.mp3"
            self.assertEqual(report.moved, (moved,))
            replace_mock.assert_called_once_with(track, {"album": "Byabodhan"})

    @patch("youtube_audio_video_downloader.services.album_consolidator.replace_media_metadata")
    @patch("youtube_audio_video_downloader.services.album_consolidator.read_media_metadata")
    def test_removes_album_tag_containing_a_credited_artist(
        self, read_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Song", album="Best of Arijit Singh", artists="Arijit Singh, Anweshaa"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "incoming"
            source.mkdir()
            track = source / "Song.mp3"
            track.write_bytes(b"media")

            report = consolidate_albums(source, root / "library")

            self.assertEqual(report.moved, ())
            self.assertTrue(track.exists())
            self.assertIn("and was removed", report.skipped[0])
            replace_mock.assert_called_once_with(track, {"album": ""})

    @patch(
        "youtube_audio_video_downloader.services.album_consolidator._probe_album",
        return_value="",
    )
    @patch("youtube_audio_video_downloader.services.album_consolidator.replace_media_metadata")
    @patch("youtube_audio_video_downloader.services.album_consolidator.read_media_metadata")
    def test_does_not_tag_structured_name_when_album_contains_artist(
        self, read_mock, replace_mock, _probe_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "incoming"
            source.mkdir()
            track = source / "Song - Best of Arijit Singh - Arijit Singh.mp3"
            track.write_bytes(b"media")

            report = consolidate_albums(source, root / "library")

            self.assertEqual(report.moved, ())
            self.assertEqual(report.tagged, 0)
            self.assertTrue(track.exists())
            replace_mock.assert_not_called()

    @patch(
        "youtube_audio_video_downloader.services.album_consolidator._probe_album",
        return_value="",
    )
    @patch("youtube_audio_video_downloader.services.album_consolidator.replace_media_metadata")
    @patch("youtube_audio_video_downloader.services.album_consolidator.read_media_metadata")
    def test_untagged_structured_filename_is_tagged_before_move(
        self, read_mock, replace_mock, _probe_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "incoming"
            source.mkdir()
            track = source / "Song - Album - Artist One, Artist Two.mp3"
            track.write_bytes(b"media")
            destination = root / "library"

            report = consolidate_albums(source, destination)

            expected = destination / "Album" / track.name
            self.assertEqual(report.tagged, 1)
            self.assertEqual(report.moved, (expected,))
            self.assertTrue(expected.is_file())
            replace_mock.assert_called_once_with(
                track,
                {
                    "title": "Song",
                    "album": "Album",
                    "artists": ["Artist One", "Artist Two"],
                },
            )

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs FFmpeg")
    def test_real_untagged_mp3_is_tagged_and_consolidated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "incoming"
            source.mkdir()
            track = source / "Real Song - Real Album - Real Artist.mp3"
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=0.2", "-codec:a", "libmp3lame", str(track),
                ],
                check=True,
            )
            destination = root / "library"
            report = consolidate_albums(source, destination)
            moved = destination / "Real Album" / track.name
            metadata = read_media_metadata(moved)
            self.assertEqual(report.tagged, 1)
            self.assertEqual(metadata.title, "Real Song")
            self.assertEqual(metadata.album, "Real Album")
            self.assertEqual(metadata.artists, "Real Artist")

    def test_destination_cannot_be_inside_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "inside the source"):
                consolidate_albums(source, source / "organized")

    @patch("youtube_audio_video_downloader.services.album_consolidator.read_media_metadata")
    def test_album_already_under_destination_is_not_deleted(self, read_mock) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Song", album="Album (2020)", artists="Artist", year="2020"
        )
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / "library"
            source = library / "Album (2020)"
            source.mkdir(parents=True)
            track = source / "Song - Album (2020) - Artist.mp3"
            track.write_bytes(b"media")

            report = consolidate_albums(source, library)

            self.assertEqual(report.moved, (track,))
            self.assertEqual(report.deleted, ())
            self.assertTrue(track.is_file())

    @patch("youtube_audio_video_downloader.services.album_consolidator.read_media_metadata")
    def test_identical_existing_destination_title_deletes_source(self, read_mock) -> None:
        read_mock.return_value = EditableMediaMetadata(album="Album")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "incoming"
            source.mkdir()
            track = source / "Song - Album - Artist.mp3"
            track.write_bytes(b"identical media")
            target = root / "library" / "Album" / track.name
            target.parent.mkdir(parents=True)
            target.write_bytes(b"identical media")

            report = consolidate_albums(source, root / "library")

            self.assertEqual(report.moved, ())
            self.assertEqual(report.deleted, (track,))
            self.assertFalse(track.exists())
            self.assertEqual(target.read_bytes(), b"identical media")

    @patch("youtube_audio_video_downloader.services.album_consolidator.read_media_metadata")
    def test_existing_destination_title_deletes_source_without_copying(
        self, read_mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "incoming"
            source.mkdir()
            track = source / "new filename.mp3"
            track.write_bytes(b"new media")
            existing = root / "library" / "Album" / "old filename.flac"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"existing media")

            def metadata_for(path):
                if Path(path) == existing:
                    return EditableMediaMetadata(title="Same Song", album="Album")
                return EditableMediaMetadata(title="Same Song!", album="Album")

            read_mock.side_effect = metadata_for
            report = consolidate_albums(source, root / "library")

            self.assertEqual(report.moved, ())
            self.assertEqual(report.deleted, (track,))
            self.assertFalse(track.exists())
            self.assertEqual(existing.read_bytes(), b"existing media")
            self.assertFalse((existing.parent / track.name).exists())

    @patch("youtube_audio_video_downloader.services.album_consolidator.time.sleep")
    @patch.object(Path, "rename", side_effect=PermissionError("file is in use"))
    def test_failed_same_volume_move_never_leaves_a_destination_copy(
        self, _rename_mock, _sleep_mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp3"
            target = root / "album" / "target.mp3"
            source.write_bytes(b"media")
            target.parent.mkdir()
            with self.assertRaises(PermissionError):
                _transactional_move(source, target)
            self.assertTrue(source.is_file())
            self.assertFalse(target.exists())

    @patch(
        "youtube_audio_video_downloader.services.album_consolidator._probe_album",
        return_value="Video Album",
    )
    @patch("youtube_audio_video_downloader.services.album_consolidator.read_media_metadata")
    def test_uses_ffprobe_album_fallback_for_video_containers(
        self, read_mock, _probe_mock
    ) -> None:
        read_mock.side_effect = ValueError("Mutagen does not support MKV")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "incoming"
            source.mkdir()
            video = source / "concert.mkv"
            video.write_bytes(b"video")
            destination = root / "library"
            report = consolidate_albums(source, destination)
            expected = destination / "Video Album" / video.name
            self.assertEqual(report.moved, (expected,))
            self.assertTrue(expected.is_file())

    @patch("youtube_audio_video_downloader.gui.operations.enrich_media_files")
    @patch("youtube_audio_video_downloader.gui.operations.consolidate_albums")
    def test_gui_operation_enriches_only_moved_files_by_default(
        self, consolidate_mock, enrich_files_mock
    ) -> None:
        consolidate_mock.return_value = ConsolidationReport(
            scanned=3,
            moved=(Path("library/Album/one.mp3"), Path("library/Album/two.mp3")),
            skipped=("missing.mp3: Album metadata is empty",),
            tagged=1,
            reordered=2,
        )
        enrich_files_mock.return_value = MetadataEnrichmentReport(
            scanned=2,
            updated=(Path("library/Album/one.mp3"),),
            skipped=(),
            failed=(),
        )
        summary = execute_operation(
            "album_consolidator",
            {"source_folder": "incoming", "destination_folder": "library"},
            CancellationToken(),
        )
        self.assertEqual(summary.operation, "album_consolidator")
        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.moved, 2)
        self.assertEqual(summary.tagged, 2)
        self.assertEqual(summary.reordered, 2)
        self.assertEqual(summary.skipped, 1)
        enrich_files_mock.assert_called_once()
        self.assertEqual(
            enrich_files_mock.call_args.args[0],
            [Path("library/Album/one.mp3"), Path("library/Album/two.mp3")],
        )

    @patch("youtube_audio_video_downloader.gui.operations.enrich_folder_metadata")
    @patch("youtube_audio_video_downloader.gui.operations.enrich_media_files")
    @patch("youtube_audio_video_downloader.gui.operations.consolidate_albums")
    def test_gui_operation_can_enrich_the_complete_destination(
        self, consolidate_mock, enrich_files_mock, enrich_folder_mock
    ) -> None:
        consolidate_mock.return_value = ConsolidationReport(
            scanned=1,
            moved=(Path("library/Album/one.mp3"),),
            skipped=(),
        )
        enrich_folder_mock.return_value = MetadataEnrichmentReport(
            scanned=5,
            updated=(Path("library/Album/old.mp3"),),
            skipped=(),
            failed=(),
        )

        summary = execute_operation(
            "album_consolidator",
            {
                "source_folder": "incoming",
                "destination_folder": "library",
                "enrich_all_destination": True,
                "workers": 7,
            },
            CancellationToken(),
        )

        self.assertEqual(summary.tagged, 1)
        enrich_files_mock.assert_not_called()
        enrich_folder_mock.assert_called_once()
        self.assertEqual(enrich_folder_mock.call_args.args[0], Path("library").resolve())
        self.assertEqual(enrich_folder_mock.call_args.kwargs["workers"], 7)

    @patch("youtube_audio_video_downloader.services.album_consolidator.reorder_track_numbers")
    @patch("youtube_audio_video_downloader.services.album_consolidator.find_wikipedia_tracks")
    @patch("youtube_audio_video_downloader.services.album_consolidator.read_media_metadata")
    def test_wikipedia_order_is_compressed_to_the_local_subset(
        self, read_mock, wikipedia_mock, reorder_mock
    ) -> None:
        reorder_mock.return_value = 3
        wikipedia_mock.return_value = [
            {"title": f"Song {number}", "artists": "Artist"}
            for number in range(1, 6)
        ]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            paths = [folder / "five.mp3", folder / "one.mp3", folder / "three.mp3"]
            for path in paths:
                path.write_bytes(b"media")
            titles = {"five.mp3": "Song 5", "one.mp3": "Song 1", "three.mp3": "Song 3"}
            read_mock.side_effect = lambda path: EditableMediaMetadata(
                title=titles[Path(path).name], album="Album"
            )

            reordered = _reorder_album_from_wikipedia(folder, "Album")

            self.assertEqual(reordered, 3)
        reorder_mock.assert_called_once_with(
            [folder / "one.mp3", folder / "three.mp3", folder / "five.mp3"],
            retries=3,
            normalize_total=True,
        )

    @patch(
        "youtube_audio_video_downloader.services.album_consolidator._track_duration",
        return_value=238.0,
    )
    def test_wikipedia_order_matches_versions_and_duration_confirmed_typos(
        self, _duration_mock
    ) -> None:
        tracks = [
            {"title": "Phir Bhi Dil Hai Hindustani", "duration_seconds": 241},
            {"title": "I'm the Best (Male Version)", "duration_seconds": 259},
            {"title": "I'm the Best (Female Version)", "duration_seconds": 259},
        ]

        self.assertEqual(
            _wikipedia_track_index(
                "I Am The Best(Male)", Path("male.mp3"), tracks
            ),
            1,
        )
        self.assertEqual(
            _wikipedia_track_index(
                "I Am The Best(Female)", Path("female.mp3"), tracks
            ),
            2,
        )
        self.assertEqual(
            _wikipedia_track_index(
                "Bhir Bhi Dil Hai Hindustani", Path("title.mp3"), tracks
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
