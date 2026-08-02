"""Offline validation for the Redownload workflow."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.gui.operations import execute_operation
from youtube_audio_video_downloader.services.media_redownloader import (
    _destination_for,
    _media_kind,
)


class MediaRedownloaderTest(unittest.TestCase):
    def test_source_type_is_inferred_from_extension(self) -> None:
        self.assertEqual(_media_kind(Path("track.flac")), "audio")
        self.assertEqual(_media_kind(Path("movie.mkv")), "video")
        with self.assertRaisesRegex(ValueError, "Unsupported media"):
            _media_kind(Path("notes.txt"))

    def test_copy_keeps_source_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "track.flac"
            source.touch()
            destination = _destination_for(
                source, "audio", "audio", ["audio"], False, None
            )
            self.assertEqual(destination, Path(directory) / "track_redownloaded.flac")

    def test_both_adds_companion_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "movie.mkv"
            source.touch()
            destination = _destination_for(
                source, "audio", "video", ["audio", "video"], False, None
            )
            self.assertEqual(destination, Path(directory) / "movie_redownloaded_audio.mp3")

    @patch("youtube_audio_video_downloader.gui.operations.redownload_media")
    def test_gui_operation_returns_created_files(self, redownload_mock) -> None:
        redownload_mock.return_value = [Path("refreshed.mp3")]
        summary = execute_operation(
            "redownload",
            {
                "input_path": "old.mp3",
                "youtube_url": "https://youtu.be/example",
                "media_mode": "auto",
            },
            CancellationToken(),
        )
        self.assertEqual(summary.operation, "redownload")
        self.assertEqual(summary.downloaded, 1)
        self.assertEqual(summary.completed_items, ("refreshed.mp3",))


if __name__ == "__main__":
    unittest.main()
