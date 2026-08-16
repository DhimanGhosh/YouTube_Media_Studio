"""Existing enriched library files should prevent redundant downloads."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.domain.models import (
    DownloadStatus,
    ParsedSongMetadata,
    Song,
)
from youtube_audio_video_downloader.services.downloads.audio_downloader import (
    YouTubeAudioDownloader,
)


class ExistingDownloadSkipTest(unittest.TestCase):
    def test_audio_downloader_skips_track_found_in_canonical_album_folder(self) -> None:
        downloader = YouTubeAudioDownloader()
        song = Song(
            json_key="Song",
            ytb_link="https://youtu.be/example",
            file_name="Song - Album - Artist",
            parsed_metadata=ParsedSongMetadata("Song", "Album", ["Artist"]),
            release_year="2001",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "Album (2001)" / "Song.mp3"
            existing.parent.mkdir()
            existing.write_bytes(b"existing")
            with patch(
                "youtube_audio_video_downloader.services.downloads.audio_downloader.find_existing_album_track",
                return_value=existing,
            ), patch.object(downloader, "_wait_before_download") as wait_mock:
                result = downloader._download_song(song, root)

        self.assertEqual(result.status, DownloadStatus.ALREADY_EXISTS)
        self.assertEqual(result.file_name, str(existing))
        wait_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
