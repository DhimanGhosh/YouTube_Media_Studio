"""Tests for timestamp-bounded downloader jobs."""

from __future__ import annotations

import math
import unittest

from youtube_audio_video_downloader.services.download_range import (
    build_download_range_options,
)


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

