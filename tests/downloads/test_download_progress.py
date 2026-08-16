from __future__ import annotations

import contextlib
import io
import unittest

from youtube_audio_video_downloader.services.downloads.download_progress import (
    DOWNLOAD_EVENT_PREFIX,
    accelerated_download_options,
    format_download_event,
    parse_download_event,
)


class DownloadProgressTest(unittest.TestCase):
    def test_options_enable_bounded_parallel_fragments(self) -> None:
        options = accelerated_download_options("Song", 99)
        self.assertEqual(options["concurrent_fragment_downloads"], 32)
        self.assertTrue(options["noprogress"])
        self.assertEqual(len(options["progress_hooks"]), 1)

    def test_hook_emits_structured_fragment_telemetry(self) -> None:
        hook = accelerated_download_options("Song", 8)["progress_hooks"][0]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            hook({
                "status": "downloading",
                "filename": "song.webm",
                "downloaded_bytes": 50,
                "total_bytes": 100,
                "speed": 25,
                "eta": 2,
                "fragment_index": 3,
                "fragment_count": 12,
                "info_dict": {"protocol": "m3u8_native"},
            })
        line = output.getvalue().strip()
        self.assertTrue(line.startswith(DOWNLOAD_EVENT_PREFIX))
        payload = parse_download_event(line)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["connections_used"], 8)
        self.assertEqual(payload["percent"], 50)
        self.assertIn("segment 3/12", format_download_event(payload))

    def test_progress_parser_ignores_bad_events(self) -> None:
        self.assertIsNone(parse_download_event("ordinary line"))
        self.assertIsNone(parse_download_event(DOWNLOAD_EVENT_PREFIX + "{"))


if __name__ == "__main__":
    unittest.main()
