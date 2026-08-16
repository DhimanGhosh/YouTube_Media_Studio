"""Tests for downloader JSON normalization."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from youtube_audio_video_downloader.loaders.json_loader import load_songs, load_videos


class JsonLoaderTest(unittest.TestCase):
    def test_canonicalizes_artist_names_while_loading_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            json_path = Path(temporary_directory) / "songs.json"
            json_path.write_text(
                json.dumps(
                    {
                        "Song": {
                            "ytb_link": "https://youtu.be/example",
                            "title": "Song",
                            "album": "Album",
                            "artists": ["K.K.", "A. R. Rahman", "Arijit"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            song = load_songs(json_path)[0]

        self.assertEqual(
            song.parsed_metadata.artists,
            ["KK", "AR Rahman", "Arijit Singh"],
        )
        self.assertEqual(
            song.file_name,
            "Song - Album - KK, AR Rahman, Arijit Singh",
        )

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

    def test_loads_independent_audio_and_video_timestamp_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            audio_path = root / "songs.json"
            video_path = root / "videos.json"
            audio_path.write_text(
                json.dumps({
                    "Clip": {
                        "ytb_link": "https://youtu.be/audio",
                        "title": "Clip", "album": "Album", "artists": "Artist",
                        "start_timestamp": "00:12.5", "end_timestamp": "01:03",
                    }
                }),
                encoding="utf-8",
            )
            video_path.write_text(
                json.dumps({
                    "Video": {
                        "ytb_link": "https://youtu.be/video",
                        "start_timestamp": "02:00", "end_timestamp": "02:45",
                    }
                }),
                encoding="utf-8",
            )

            song = load_songs(audio_path)[0]
            video = load_videos(video_path)[0]

        self.assertEqual((song.start_timestamp, song.end_timestamp), ("00:12.5", "01:03"))
        self.assertEqual((video.start_timestamp, video.end_timestamp), ("02:00", "02:45"))


if __name__ == "__main__":
    unittest.main()
