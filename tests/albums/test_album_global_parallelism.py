from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import MethodType
from unittest.mock import patch

from youtube_audio_video_downloader.config.settings import DownloadSettings
from youtube_audio_video_downloader.domain.models import DownloadResult, DownloadStatus
from youtube_audio_video_downloader.services.albums.album_splitter import (
    AlbumSongSpec,
    AlbumSplitJob,
    YouTubeAlbumSplitter,
)


class AlbumGlobalParallelismTest(unittest.TestCase):
    def test_only_later_albums_wait_once_and_tracks_share_the_start_gate(self) -> None:
        for album_count in (1, 2):
            with self.subTest(albums=album_count):
                splitter = YouTubeAlbumSplitter(DownloadSettings(
                    max_workers=6, min_delay_seconds=7, max_delay_seconds=7,
                ))
                def fake_track(self, job, track, *_args):
                    self._wait_before_download(track.title)
                    return DownloadResult(song=track.title, status=DownloadStatus.DOWNLOADED,
                                          file_name=f"{track.title}.mp3")
                splitter._download_individual_album_track = MethodType(fake_track, splitter)
                jobs = [AlbumSplitJob(json_key=f"Album {album}", ytb_link="", song_tracks=[
                    AlbumSongSpec(index, index, f"{album}-{index}", "https://youtu.be/abcdefghijk", ["Singer"])
                    for index in range(1, 4)
                ]) for album in range(album_count)]
                with tempfile.TemporaryDirectory() as directory, patch.object(
                    splitter.cancellation_token, "wait",
                ) as wait, patch(
                    "youtube_audio_video_downloader.services.albums.album_splitter.random.randint",
                    return_value=7,
                ) as random_delay:
                    results = splitter._execute_album_work_items(
                        jobs, Path(directory), silence_threshold_db=-35, min_silence_duration=1.5,
                        min_track_duration=45, trim_silence_padding=0.25, keep_temp=False, overwrite=False,
                    )
                self.assertEqual(len(results), album_count * 3)
                self.assertTrue(all(result.status == DownloadStatus.DOWNLOADED for result in results))
                self.assertEqual(wait.call_count, album_count - 1)
                self.assertEqual(random_delay.call_count, album_count - 1)
                if album_count > 1:
                    wait.assert_called_once_with(7)

    def test_all_existing_tracks_remove_unused_provisional_album_folder(self) -> None:
        splitter = YouTubeAlbumSplitter(
            DownloadSettings(max_workers=1, min_delay_seconds=0, max_delay_seconds=0)
        )

        def fake_track(
            self: YouTubeAlbumSplitter,
            job: AlbumSplitJob,
            track: AlbumSongSpec,
            album_dir: Path,
            album_name: str,
            downloadable_total: int,
            overwrite: bool,
        ) -> DownloadResult:
            del self, job, album_dir, album_name, downloadable_total, overwrite
            return DownloadResult(
                song=track.title,
                status=DownloadStatus.ALREADY_EXISTS,
                file_name="Kismat Konnection (2008)/existing.mp3",
            )

        splitter._download_individual_album_track = MethodType(fake_track, splitter)
        job = AlbumSplitJob(
            json_key="Kismat Konnection",
            ytb_link="",
            album="Kismat Konnection",
            song_tracks=[AlbumSongSpec(1, 1, "Existing", "url", ["Artist"])],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provisional = root / "Kismat Konnection"
            provisional.mkdir()
            splitter._execute_album_work_items(
                [job],
                root,
                silence_threshold_db=-35.0,
                min_silence_duration=1.5,
                min_track_duration=45.0,
                trim_silence_padding=0.25,
                keep_temp=False,
                overwrite=False,
            )
            self.assertFalse(provisional.exists())

    def test_tracks_from_multiple_albums_share_one_worker_pool(self) -> None:
        splitter = YouTubeAlbumSplitter(
            DownloadSettings(max_workers=3, min_delay_seconds=0, max_delay_seconds=0)
        )
        lock = threading.Lock()
        active = 0
        max_active = 0

        def run_fake_work() -> None:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.08)
            with lock:
                active -= 1

        def fake_track(
            self: YouTubeAlbumSplitter,
            job: AlbumSplitJob,
            track: AlbumSongSpec,
            album_dir: Path,
            album_name: str,
            downloadable_total: int,
            overwrite: bool,
        ) -> DownloadResult:
            del self, job, album_dir, album_name, downloadable_total, overwrite
            run_fake_work()
            return DownloadResult(
                song=track.title,
                status=DownloadStatus.DOWNLOADED,
                file_name=f"{track.title}.mp3",
            )

        def fake_full_album(
            self: YouTubeAlbumSplitter,
            job: AlbumSplitJob,
            target_root: Path,
            **kwargs: object,
        ) -> list[DownloadResult]:
            del self, target_root, kwargs
            run_fake_work()
            return [
                DownloadResult(
                    song=job.json_key,
                    status=DownloadStatus.DOWNLOADED,
                    file_name=f"{job.json_key}.mp3",
                )
            ]

        splitter._download_individual_album_track = MethodType(fake_track, splitter)
        splitter._run_single_album_job = MethodType(fake_full_album, splitter)

        jobs = [
            AlbumSplitJob(
                json_key="Album A",
                ytb_link="",
                album="Album A",
                song_tracks=[
                    AlbumSongSpec(1, 1, "A1", "url-a1", ["Artist"]),
                    AlbumSongSpec(2, 2, "A2", "url-a2", ["Artist"]),
                ],
            ),
            AlbumSplitJob(
                json_key="Album B",
                ytb_link="",
                album="Album B",
                song_tracks=[
                    AlbumSongSpec(1, 1, "B1", "url-b1", ["Artist"]),
                    AlbumSongSpec(2, 2, "B2", "url-b2", ["Artist"]),
                ],
            ),
            AlbumSplitJob(
                json_key="Full Album C",
                ytb_link="url-full-c",
                album="Full Album C",
            ),
            AlbumSplitJob(
                json_key="Disabled Album",
                ytb_link="url-disabled",
                album="Disabled Album",
                download=False,
            ),
        ]

        with tempfile.TemporaryDirectory() as directory:
            results = splitter._execute_album_work_items(
                jobs,
                Path(directory),
                silence_threshold_db=-35.0,
                min_silence_duration=1.5,
                min_track_duration=45.0,
                trim_silence_padding=0.25,
                keep_temp=False,
                overwrite=False,
            )

        self.assertEqual(len(results), 6)
        self.assertEqual(max_active, 3)
        self.assertEqual(
            sum(result.status == DownloadStatus.SKIPPED for result in results),
            1,
        )


if __name__ == "__main__":
    unittest.main()
