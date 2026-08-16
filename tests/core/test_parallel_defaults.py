from __future__ import annotations

import unittest
from inspect import signature
from unittest.mock import patch

from youtube_audio_video_downloader.cli.album_splitter_cli import build_parser as album_parser
from youtube_audio_video_downloader.cli.audio_downloader_cli import build_parser as audio_parser
from youtube_audio_video_downloader.cli.jukebox_splitter_cli import build_parser as jukebox_parser
from youtube_audio_video_downloader.cli.video_downloader_cli import build_parser as video_parser
from youtube_audio_video_downloader.config.settings import (
    DownloadSettings,
    machine_parallel_workers,
)
from youtube_audio_video_downloader.services.albums.album_splitter import YouTubeAlbumSplitter
from youtube_audio_video_downloader.services.albums.jukebox_splitter import (
    YouTubeJukeboxSplitter,
)
from youtube_audio_video_downloader.services.downloads.audio_downloader import YouTubeAudioDownloader
from youtube_audio_video_downloader.services.downloads.video_downloader import YouTubeVideoDownloader


class ParallelDefaultsTest(unittest.TestCase):
    def test_shared_defaults(self) -> None:
        settings = DownloadSettings()
        self.assertEqual(settings.max_workers, machine_parallel_workers())
        self.assertEqual(settings.min_delay_seconds, 10)
        self.assertEqual(settings.max_delay_seconds, 25)
        self.assertEqual(settings.segment_connections, 8)

    def test_all_download_clis_use_shared_defaults(self) -> None:
        parsed_args = (
            audio_parser().parse_args(["songs.json"]),
            video_parser().parse_args(["videos.json"]),
            album_parser().parse_args(["albums.json"]),
            jukebox_parser().parse_args(["jukeboxes.json"]),
        )

        for args in parsed_args:
            self.assertEqual(args.workers, machine_parallel_workers())
            self.assertEqual(args.min_delay, 10)
            self.assertEqual(args.max_delay, 25)
            self.assertFalse(args.write_report)
            self.assertEqual(args.connections, 8)

    def test_max_workers_alias(self) -> None:
        self.assertEqual(
            audio_parser().parse_args(["songs.json", "--max-workers", "7"]).workers,
            7,
        )
        self.assertEqual(
            video_parser().parse_args(["videos.json", "--max-workers", "7"]).workers,
            7,
        )

    def test_all_service_report_defaults_are_disabled(self) -> None:
        methods = (
            YouTubeAudioDownloader.download_from_json,
            YouTubeAudioDownloader.tag_existing_mp3_files_from_json,
            YouTubeVideoDownloader.download_from_json,
            YouTubeAlbumSplitter.split_from_input,
            YouTubeJukeboxSplitter.split_from_json,
        )

        for method in methods:
            self.assertIs(signature(method).parameters["write_report"].default, False)

    def test_linux_worker_default_respects_process_affinity(self) -> None:
        with (
            patch("youtube_audio_video_downloader.config.settings.sys.platform", "linux"),
            patch(
                "youtube_audio_video_downloader.config.settings.os.sched_getaffinity",
                return_value={0, 1, 2},
                create=True,
            ),
        ):
            self.assertEqual(machine_parallel_workers(), 3)

    def test_windows_worker_default_uses_available_processor_count(self) -> None:
        with (
            patch("youtube_audio_video_downloader.config.settings.sys.platform", "win32"),
            patch.dict("os.environ", {"NUMBER_OF_PROCESSORS": "12"}),
        ):
            self.assertEqual(machine_parallel_workers(), 12)


if __name__ == "__main__":
    unittest.main()
