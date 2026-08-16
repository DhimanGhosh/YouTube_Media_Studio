from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.services.albums.album_splitter import (
    AlbumSongSpec,
    AlbumSplitJob,
    YouTubeAlbumSplitter,
)
from youtube_audio_video_downloader.domain.models import DownloadStatus


class AlbumIndividualTrackRangesTest(unittest.TestCase):
    def test_existing_enriched_album_track_skips_network_download(self) -> None:
        splitter = YouTubeAlbumSplitter()
        job = AlbumSplitJob(
            json_key="Kismat Konnection",
            ytb_link="",
            album="Kismat Konnection",
            release_year="2008",
        )
        track = AlbumSongSpec(
            1, 1, "Bakhuda Tumhi Ho", "https://youtu.be/example", ["Atif Aslam"]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            album_dir = root / "Kismat Konnection"
            album_dir.mkdir()
            existing = root / "Kismat Konnection (2008)" / "existing.mp3"
            existing.parent.mkdir()
            existing.write_bytes(b"existing")
            with patch(
                "youtube_audio_video_downloader.services.albums.album_splitter.find_existing_album_track",
                return_value=existing,
            ), patch.object(splitter, "_wait_before_download") as wait_mock:
                result = splitter._download_individual_album_track(
                    job, track, album_dir, "Kismat Konnection", 1, False
                )

        self.assertEqual(result.status, DownloadStatus.ALREADY_EXISTS)
        self.assertEqual(result.file_name, str(existing))
        wait_mock.assert_not_called()

    def test_individual_song_link_supports_start_and_end(self) -> None:
        errors: list[str] = []
        tracks = YouTubeAlbumSplitter._parse_individual_song_specs(
            {
                "tracks": [
                    {
                        "Disco Nachaibo": {
                            "ytb_link": "https://www.youtube.com/watch?v=dtn4-NzvpYU",
                            "artists": "Jeet Gannguli",
                            "start": "00:10",
                            "end": "3:25",
                        }
                    }
                ]
            },
            "Jaaneman",
            errors,
        )

        self.assertEqual(errors, [])
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].start_seconds, 10)
        self.assertEqual(tracks[0].end_seconds, 205)
        self.assertTrue(tracks[0].is_partial_range)

    def test_individual_song_link_supports_end_without_start(self) -> None:
        errors: list[str] = []
        tracks = YouTubeAlbumSplitter._parse_individual_song_specs(
            {
                "tracks": [
                    {
                        "Disco Nachaibo": {
                            "ytb_link": "https://www.youtube.com/watch?v=dtn4-NzvpYU",
                            "artists": "Jeet Gannguli",
                            "end": "3:25",
                        }
                    }
                ]
            },
            "Jaaneman",
            errors,
        )

        self.assertEqual(errors, [])
        self.assertEqual(tracks[0].start_seconds, 0)
        self.assertEqual(tracks[0].end_seconds, 205)
        self.assertTrue(tracks[0].is_partial_range)

    def test_invalid_individual_range_is_reported(self) -> None:
        errors: list[str] = []
        tracks = YouTubeAlbumSplitter._parse_individual_song_specs(
            {
                "tracks": [
                    {
                        "Broken Track": {
                            "ytb_link": "https://www.youtube.com/watch?v=abc",
                            "start": "03:25",
                            "end": "03:00",
                        }
                    }
                ]
            },
            "Album",
            errors,
        )

        self.assertEqual(tracks, [])
        self.assertTrue(any("end must be after start" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
