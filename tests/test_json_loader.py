"""Tests for downloader JSON normalization."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from youtube_audio_video_downloader.loaders.json_loader import load_songs


class JsonLoaderTest(unittest.TestCase):
    def test_builds_audio_file_name_from_metadata_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            json_path = Path(temporary_directory) / "songs.json"
            json_path.write_text(
                json.dumps(
                    {
                        "Pehli Dafa": {
                            "ytb_link": "https://youtu.be/example",
                            "title": "Pehli Dafa",
                            "album": "Unknown",
                            "artists": "Atif Aslam",
                        }
                    }
                ),
                encoding="utf-8",
            )

            songs = load_songs(json_path)

        self.assertEqual(len(songs), 1)
        self.assertEqual(songs[0].file_name, "Pehli Dafa - Unknown - Atif Aslam")
        self.assertEqual(songs[0].parsed_metadata.artists, ["Atif Aslam"])


if __name__ == "__main__":
    unittest.main()
