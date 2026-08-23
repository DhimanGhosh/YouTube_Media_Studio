"""Existing enriched library files should prevent redundant downloads."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from youtube_audio_video_downloader.config.settings import DownloadSettings
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

    def test_transient_tag_failure_is_retried_without_redownloading(self) -> None:
        downloader = YouTubeAudioDownloader(
            DownloadSettings(
                min_delay_seconds=0,
                max_delay_seconds=0,
                max_retries=3,
                retry_wait_seconds=0,
                skip_existing=False,
            )
        )
        song = Song(
            json_key="Song",
            ytb_link="https://youtu.be/example",
            file_name="Song - Album - Artist",
            parsed_metadata=ParsedSongMetadata("Song", "Album", ["Artist"]),
        )
        downloader.metadata_tagger.tag_mp3 = Mock(
            side_effect=[OSError("temporary tag failure"), None]
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final_path = root / "Song - Album - Artist.mp3"

            def create_download(*_args, **_kwargs):
                final_path.write_bytes(b"mp3")
                return {"_filename": str(final_path)}

            with patch(
                "youtube_audio_video_downloader.services.downloads.audio_downloader."
                "download_with_fallback",
                side_effect=create_download,
            ) as download:
                result = downloader._download_song(song, root)

        self.assertEqual(result.status, DownloadStatus.DOWNLOADED)
        download.assert_called_once()
        self.assertEqual(downloader.metadata_tagger.tag_mp3.call_count, 2)


if __name__ == "__main__":
    unittest.main()
