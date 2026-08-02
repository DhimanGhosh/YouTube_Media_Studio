"""Tests for lossless local audio trimming."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mutagen.id3 import ID3, TXXX

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.gui.operations import execute_operation
from youtube_audio_video_downloader.services.audio_trimmer import (
    format_timestamp,
    parse_timestamp,
)


class AudioTrimmerTest(unittest.TestCase):
    def test_timestamp_parsing_and_formatting(self) -> None:
        self.assertEqual(parse_timestamp("01:02:03.250"), 3723.25)
        self.assertEqual(parse_timestamp("02:30"), 150)
        self.assertEqual(format_timestamp(3723.25), "01:02:03.250")
        with self.assertRaisesRegex(ValueError, "below 60"):
            parse_timestamp("00:60")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs FFmpeg")
    def test_end_must_be_after_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "earlier"):
            self._run_generated_trim("00:02", "00:01")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs FFmpeg")
    def test_copy_preserves_tags_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tagged.mp3"
            output = Path(directory) / "trimmed.mp3"
            self._make_tagged_audio(source)
            original_size = source.stat().st_size

            summary = execute_operation(
                "audio_trimmer",
                {
                    "input_path": str(source),
                    "start_timestamp": "00:01",
                    "end_timestamp": "00:03",
                    "output_path": str(output),
                },
                CancellationToken(),
            )

            self.assertTrue(output.is_file())
            self.assertEqual(source.stat().st_size, original_size)
            self.assertEqual(summary.output_path, str(output))
            metadata = self._probe(output)
            self.assertEqual(metadata["tags"]["title"], "Original Title")
            self.assertEqual(metadata["tags"]["artist"], "Original Artist")
            self.assertAlmostEqual(float(metadata["duration"]), 2.0, delta=0.1)
            custom_tags = ID3(output).getall("TXXX:Project marker")
            self.assertEqual(custom_tags[0].text, ["keep-me"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs FFmpeg")
    def test_overwrite_atomically_replaces_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tagged.mp3"
            self._make_tagged_audio(source)
            execute_operation(
                "audio_trimmer",
                {
                    "input_path": str(source),
                    "start_timestamp": "00:00.5",
                    "end_timestamp": "00:02",
                    "overwrite_source": True,
                },
                CancellationToken(),
            )
            metadata = self._probe(source)
            self.assertEqual(metadata["tags"]["title"], "Original Title")
            self.assertAlmostEqual(float(metadata["duration"]), 1.5, delta=0.1)

    def _run_generated_trim(self, start: str, end: str) -> None:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("needs FFmpeg")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp3"
            self._make_tagged_audio(source)
            execute_operation(
                "audio_trimmer",
                {"input_path": str(source), "start_timestamp": start, "end_timestamp": end},
                CancellationToken(),
            )

    @staticmethod
    def _make_tagged_audio(path: Path) -> None:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=4", "-metadata", "title=Original Title",
                "-metadata", "artist=Original Artist", "-codec:a", "libmp3lame", str(path),
            ],
            check=True,
        )
        tags = ID3(path)
        tags.add(TXXX(encoding=3, desc="Project marker", text=["keep-me"]))
        tags.save(path)

    @staticmethod
    def _probe(path: Path) -> dict:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration:format_tags=title,artist", "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(completed.stdout)["format"]


if __name__ == "__main__":
    unittest.main()
