"""Tests for timestamp-bounded downloader jobs."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from youtube_audio_video_downloader.domain.models import (
    ParsedSongMetadata, Song, VideoJob, VideoQuality,
)
from youtube_audio_video_downloader.services.audio_downloader import YouTubeAudioDownloader

from youtube_audio_video_downloader.services.download_range import (
    build_download_range_options,
)
from youtube_audio_video_downloader.services.video_downloader import YouTubeVideoDownloader


class DownloadRangeTest(unittest.TestCase):
    def test_full_source_uses_no_yt_dlp_range(self) -> None:
        self.assertEqual(build_download_range_options("00:00", ""), {})

    def test_start_and_end_are_exposed_to_yt_dlp(self) -> None:
        options = build_download_range_options("01:02.5", "02:03")
        ranges = list(options["download_ranges"]({}, None))

        self.assertTrue(options["force_keyframes_at_cuts"])
        self.assertEqual(ranges[0]["start_time"], 62.5)
        self.assertEqual(ranges[0]["end_time"], 123.0)

    def test_omitted_end_downloads_to_source_end(self) -> None:
        options = build_download_range_options("15", "")
        ranges = list(options["download_ranges"]({}, None))

        self.assertEqual(ranges[0]["start_time"], 15.0)
        self.assertTrue(math.isinf(ranges[0]["end_time"]))

    def test_invalid_range_is_rejected_before_download(self) -> None:
        with self.assertRaisesRegex(ValueError, "earlier"):
            build_download_range_options("02:00", "01:00")

    def test_audio_options_use_each_song_range(self) -> None:
        downloader = YouTubeAudioDownloader()
        metadata = ParsedSongMetadata("Song", "Album", ["Artist"])
        clipped = Song(
            "Clip", "https://youtu.be/clip", "Clip", metadata,
            start_timestamp="00:10", end_timestamp="00:20",
        )
        complete = Song("Complete", "https://youtu.be/all", "Complete", metadata)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            clipped_options = downloader._build_yt_dlp_options(root, "Clip", clipped)
            complete_options = downloader._build_yt_dlp_options(root, "Complete", complete)

        ranges = list(clipped_options["download_ranges"]({}, None))
        self.assertEqual((ranges[0]["start_time"], ranges[0]["end_time"]), (10.0, 20.0))
        self.assertNotIn("download_ranges", complete_options)

    def test_video_options_use_each_video_range(self) -> None:
        downloader = YouTubeVideoDownloader(interactive_prompts=False)
        quality = VideoQuality(
            "1080p", 1080, 1920, 30.0, "video", "audio", "mp4", "m4a", None,
        )
        video = VideoJob(
            "Clip", "https://youtu.be/clip",
            start_timestamp="01:02.5", end_timestamp="01:10",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            options = downloader._build_video_yt_dlp_options(
                Path(temporary_directory), "Clip", quality, video
            )

        ranges = list(options["download_ranges"]({}, None))
        self.assertEqual((ranges[0]["start_time"], ranges[0]["end_time"]), (62.5, 70.0))

