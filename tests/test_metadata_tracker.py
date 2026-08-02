"""Tests for the persistent Album Enricher completion index."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.services.album_metadata_enricher import (
    enrich_folder_metadata,
)
from youtube_audio_video_downloader.services.media_metadata import EditableMediaMetadata
from youtube_audio_video_downloader.services.metadata_tracker import (
    MetadataCompletionTracker,
    verification_policy_key,
)


class MetadataCompletionTrackerTest(unittest.TestCase):
    def test_ai_off_uses_a_distinct_internet_verification_cache(self) -> None:
        self.assertEqual(
            verification_policy_key("", internet_only=True), "internet-v1"
        )
        self.assertEqual(verification_policy_key(""), "legacy")

    def setUp(self) -> None:
        self.lookup_patchers = [
            patch(
                "youtube_audio_video_downloader.services.album_metadata_enricher."
                + target,
                return_value=result,
            )
            for target, result in (
                ("find_wikipedia_song_metadata", {}),
                ("find_wikipedia_tracks", []),
                ("find_catalog_song_metadata", {}),
            )
        ]
        for patcher in self.lookup_patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    @patch(
        "youtube_audio_video_downloader.services.metadata_tracker._essential_tags_complete",
        return_value=True,
    )
    def test_persists_and_recognizes_a_rename_or_move(self, _complete_mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "tracker.json"
            original = root / "Song - Album - Artist.mp3"
            original.write_bytes(b"audio")
            MetadataCompletionTracker(index).mark_complete([original])

            moved = root / "Album (2020)" / original.name
            moved.parent.mkdir()
            original.rename(moved)

            self.assertTrue(MetadataCompletionTracker(index).is_complete(moved))

    def test_changed_file_is_not_treated_as_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "tracker.json"
            song = root / "Song - Album - Artist.mp3"
            song.write_bytes(b"audio")
            MetadataCompletionTracker(index).mark_complete([song])
            song.write_bytes(b"changed audio")

            self.assertFalse(MetadataCompletionTracker(index).is_complete(song))

    @patch(
        "youtube_audio_video_downloader.services.metadata_tracker._essential_tags_complete",
        return_value=True,
    )
    def test_legacy_completion_is_rechecked_by_agentic_policy(
        self, _complete_mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "tracker.json"
            song = root / "Song - Album (2020) - Artist.mp3"
            song.write_bytes(b"audio")
            MetadataCompletionTracker(index).mark_complete([song], "legacy")

            tracker = MetadataCompletionTracker(index)
            self.assertFalse(tracker.is_complete(song, "agentic-v1:qwen2.5:7b"))
            self.assertTrue(tracker.is_complete(song, "legacy"))

    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata"
    )
    @patch(
        "youtube_audio_video_downloader.services.metadata_tracker._essential_tags_complete",
        return_value=True,
    )
    def test_second_enrichment_validates_cached_tags_before_skipping(
        self, _complete_mock, read_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Song",
            album="Album (2020)",
            artists="Artist",
            year="2020",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tracker = root / ".state" / "tracker.json"
            song = root / "Song - Album (2020) - Artist.mp3"
            song.write_bytes(b"audio")

            first = enrich_folder_metadata(root, workers=1, tracker_path=tracker)
            self.assertEqual(first.scanned, 1)
            self.assertEqual(first.tracked, 0)
            self.assertTrue(tracker.is_file())

            read_mock.reset_mock()
            second = enrich_folder_metadata(root, workers=1, tracker_path=tracker)

            self.assertEqual(second.scanned, 0)
            self.assertEqual(second.tracked, 1)
            read_mock.assert_not_called()

    @patch(
        "youtube_audio_video_downloader.services.metadata_tracker._essential_tags_complete",
        return_value=False,
    )
    def test_cached_file_with_missing_tags_is_not_skipped(self, _complete_mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "tracker.json"
            song = root / "Song - Album (2020) - Artist.mp3"
            song.write_bytes(b"audio")
            MetadataCompletionTracker(index).mark_complete([song])

            self.assertFalse(MetadataCompletionTracker(index).is_complete(song))


if __name__ == "__main__":
    unittest.main()
