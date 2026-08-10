"""Core threaded audio downloader implementation."""

from __future__ import annotations

import json
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from youtube_audio_video_downloader.loaders.json_loader import load_existing_mp3_tag_jobs, load_songs
from youtube_audio_video_downloader.metadata.id3_tagger import MetadataTagger
from youtube_audio_video_downloader.domain.models import DownloadResult, DownloadStatus, ExistingMp3TagJob, Song
from youtube_audio_video_downloader.config.settings import DownloadSettings
from youtube_audio_video_downloader.core.file_utils import ensure_directory, safe_filename
from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.core.file_access import retry_file_operation
from youtube_audio_video_downloader.services.album_folders import (
    find_existing_album_track,
)
from youtube_audio_video_downloader.core.exceptions import UserCancelledError
from youtube_audio_video_downloader.services.download_range import (
    build_download_range_options,
)


class YouTubeAudioDownloader:
    """Download YouTube audio entries from a JSON file into a songs directory."""

    def __init__(
        self,
        settings: DownloadSettings | None = None,
        cancellation_token: CancellationToken | None = None,
        start_timestamp: str = "00:00",
        end_timestamp: str = "",
    ) -> None:
        self.settings = settings or DownloadSettings()
        self.cancellation_token = cancellation_token or CancellationToken()
        self.metadata_tagger = MetadataTagger()
        self.download_range_options = build_download_range_options(
            start_timestamp, end_timestamp
        )

    def cancel(self) -> None:
        """Request cooperative cancellation for running audio workers."""

        self.cancellation_token.cancel()

    def download_from_json(
        self, json_path: Path, output_dir: Path | None = None
    ) -> list[DownloadResult]:
        """Read songs from JSON and download them into the requested folder."""

        json_path = json_path.resolve()
        output_dir = ensure_directory(output_dir.resolve() if output_dir else json_path.parent / "songs")
        songs = load_songs(json_path)

        if not songs:
            raise ValueError("No valid songs found in the input JSON file.")

        self.cancellation_token.reset()
        results: list[DownloadResult] = []
        worker_count = max(1, min(self.settings.max_workers, len(songs)))
        print(
            f"[PARALLEL] Starting {len(songs)} audio job(s) with {worker_count} worker(s); "
            f"each download uses a random "
            f"{self.settings.min_delay_seconds}-{self.settings.max_delay_seconds}s delay"
        )
        executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="yt-audio")
        futures = [executor.submit(self._download_song, song, output_dir) for song in songs]

        try:
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print(self._format_result(result))
        except (KeyboardInterrupt, UserCancelledError) as exc:
            self._cancel_futures_and_wait(executor, futures)
            raise UserCancelledError("Audio download cancelled by user") from exc
        else:
            executor.shutdown(wait=True, cancel_futures=False)

        self._write_results(output_dir / "download_results.json", results)
        return results


    def download_song_to_directory(self, song: Song, output_dir: Path) -> DownloadResult:
        """Download one Song into an explicit output directory.

        This is a public wrapper around the same best-audio-to-MP3 logic used by
        ``yt-audio-downloader``. It is used by the video command when the user
        selects the optional MP3 output from the video quality prompt.
        """

        output_dir = ensure_directory(output_dir)
        return self._download_song(song, output_dir)

    def tag_existing_mp3_files_from_json(
        self, json_path: Path, results_dir: Path | None = None
    ) -> list[DownloadResult]:
        """Read an existing-MP3 metadata JSON and apply ID3 tags to each file.

        This mode does not download anything. It only updates metadata on files that
        already exist at each entry's ``mp3_file_path``.
        """

        json_path = json_path.resolve()
        jobs = load_existing_mp3_tag_jobs(json_path)

        if not jobs:
            raise ValueError("No valid MP3 metadata jobs found in the input JSON file.")

        self.cancellation_token.reset()
        results: list[DownloadResult] = []
        worker_count = max(1, min(self.settings.max_workers, len(jobs)))
        print(
            f"[PARALLEL] Starting {len(jobs)} MP3 tagging job(s) "
            f"with {worker_count} worker(s)"
        )
        executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="yt-tag")
        futures = [executor.submit(self._tag_existing_job, job) for job in jobs]

        try:
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print(self._format_result(result))
        except (KeyboardInterrupt, UserCancelledError) as exc:
            self._cancel_futures_and_wait(executor, futures)
            raise UserCancelledError("MP3 tagging cancelled by user") from exc
        else:
            executor.shutdown(wait=True, cancel_futures=False)

        report_dir = ensure_directory(results_dir.resolve()) if results_dir else json_path.parent
        self._write_results(report_dir / "tag_existing_results.json", results)
        return results

    def _tag_existing_job(self, job: ExistingMp3TagJob) -> DownloadResult:
        """Rename one existing MP3 to the structured name and apply metadata."""

        self.cancellation_token.raise_if_cancelled()
        target_mp3_path = self._build_structured_mp3_path(job)
        mp3_path_to_tag = job.mp3_file_path

        if not mp3_path_to_tag.exists():
            if target_mp3_path.exists():
                mp3_path_to_tag = target_mp3_path
            else:
                return DownloadResult(
                    song=job.json_key,
                    status=DownloadStatus.FAILED,
                    file_name=str(job.mp3_file_path),
                    reason="MP3 file not found",
                )

        try:
            if mp3_path_to_tag != target_mp3_path:
                if target_mp3_path.exists():
                    return DownloadResult(
                        song=job.json_key,
                        status=DownloadStatus.FAILED,
                        file_name=str(mp3_path_to_tag),
                        reason=(
                            "Cannot rename because target file already exists: "
                            f"{target_mp3_path}"
                        ),
                    )

                retry_file_operation(
                    mp3_path_to_tag,
                    "renaming the downloaded audio",
                    lambda: mp3_path_to_tag.rename(target_mp3_path),
                )
                mp3_path_to_tag = target_mp3_path
                print(
                    f"[RENAME] {job.json_key}: "
                    f"{job.mp3_file_path.name} -> {target_mp3_path.name}"
                )

            song = Song(
                json_key=job.json_key,
                ytb_link="",
                file_name=target_mp3_path.stem,
                parsed_metadata=job.parsed_metadata,
                album_art=job.album_art,
                release_year=job.release_year,
            )
            self.metadata_tagger.tag_mp3(mp3_path_to_tag, song)
            return DownloadResult(
                song=job.json_key,
                status=DownloadStatus.TAGGED,
                file_name=str(mp3_path_to_tag),
            )
        except Exception as exc:  # Rename/mutagen failures should be reported per file.
            return DownloadResult(
                song=job.json_key,
                status=DownloadStatus.FAILED,
                file_name=str(mp3_path_to_tag),
                reason=str(exc),
            )

    @staticmethod
    def _build_structured_mp3_path(job: ExistingMp3TagJob) -> Path:
        """Return the desired '<title> - <album> - <artists>.mp3' path."""

        metadata = job.parsed_metadata
        artist_text = ", ".join(metadata.artists)
        structured_file_name = safe_filename(
            f"{metadata.title} - {metadata.album} - {artist_text}",
            fallback=job.json_key,
        )
        return job.mp3_file_path.with_name(f"{structured_file_name}.mp3")

    def _download_song(self, song: Song, output_dir: Path) -> DownloadResult:
        """Download one song with retries, conservative delays and ID3 tagging."""

        self.cancellation_token.raise_if_cancelled()
        file_name = safe_filename(song.file_name, fallback=song.json_key)
        final_mp3_path = output_dir / f"{file_name}.mp3"

        if not song.ytb_link:
            return DownloadResult(
                song=song.json_key,
                status=DownloadStatus.SKIPPED,
                file_name=file_name,
                reason="Missing ytb_link",
            )

        existing_track = find_existing_album_track(
            output_dir,
            title=song.parsed_metadata.title,
            album=song.parsed_metadata.album,
            year=song.release_year,
        )
        if existing_track is not None and self.settings.skip_existing:
            return DownloadResult(
                song=song.json_key,
                status=DownloadStatus.ALREADY_EXISTS,
                file_name=str(existing_track),
                reason=(
                    "matching enriched track already exists in the album library; "
                    "download skipped"
                ),
            )

        if final_mp3_path.exists():
            if self.settings.skip_existing:
                self._tag_existing_mp3(final_mp3_path, song)
                return DownloadResult(
                    song=song.json_key,
                    status=DownloadStatus.ALREADY_EXISTS,
                    file_name=final_mp3_path.name,
                    reason="MP3 already exists; metadata refreshed",
                )

            self._remove_existing_download_files(output_dir, file_name, song.json_key)


        for attempt in range(1, self.settings.max_retries + 1):
            self._wait_before_download(song.json_key)

            try:
                import yt_dlp

                with yt_dlp.YoutubeDL(self._build_yt_dlp_options(output_dir, file_name)) as ydl:
                    ydl.download([song.ytb_link])

                self.metadata_tagger.tag_mp3(final_mp3_path, song)

                return DownloadResult(
                    song=song.json_key,
                    status=DownloadStatus.DOWNLOADED,
                    file_name=final_mp3_path.name,
                )

            except UserCancelledError:
                raise
            except Exception as exc:  # yt-dlp/mutagen/urllib can raise varied exception types.
                error_text = str(exc)
                wait_seconds = self._get_retry_wait_seconds(error_text, attempt)
                print(
                    f"[RETRY] {song.json_key}: attempt {attempt}/{self.settings.max_retries} "
                    f"failed. Waiting {wait_seconds}s. Error: {error_text}"
                )
                self.cancellation_token.wait(wait_seconds)

        return DownloadResult(
            song=song.json_key,
            status=DownloadStatus.FAILED,
            file_name=file_name,
            reason="Max retries exceeded",
        )

    @staticmethod
    def _remove_existing_download_files(output_dir: Path, file_name: str, song_title: str) -> None:
        """Delete existing output/temp files before an explicit re-download."""

        patterns = (
            f"{file_name}.mp3",
            f"{file_name}.webm",
            f"{file_name}.m4a",
            f"{file_name}.part",
            f"{file_name}.*.part",
            f"{file_name}.temp.*",
        )

        removed = 0
        for pattern in patterns:
            for path in output_dir.glob(pattern):
                if path.is_file():
                    retry_file_operation(
                        path, "removing it before redownload", path.unlink
                    )
                    removed += 1

        if removed:
            print(f"[OVERWRITE] {song_title}: removed {removed} existing file(s) before download")

    def _tag_existing_mp3(self, mp3_path: Path, song: Song) -> None:
        """Refresh metadata for an existing MP3 without downloading again."""

        try:
            self.metadata_tagger.tag_mp3(mp3_path, song)
        except Exception as exc:  # Existing downloads should not fail the whole run because of tags.
            print(f"[TAG-WARNING] {song.json_key}: failed to refresh metadata: {exc}")

    def _build_yt_dlp_options(self, output_dir: Path, file_name: str) -> dict[str, Any]:
        """Build yt-dlp options for best available audio converted to MP3."""

        return {
            "format": "bestaudio/best",
            "outtmpl": str(output_dir / f"{file_name}.%(ext)s"),
            "noplaylist": True,
            "continuedl": True,
            "ignoreerrors": False,
            "quiet": False,
            "no_warnings": False,
            "retries": self.settings.max_retries,
            "fragment_retries": self.settings.max_retries,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": self.settings.preferred_mp3_quality,
                }
            ],
            "postprocessor_args": ["-ar", self.settings.audio_sample_rate],
            **self.download_range_options,
        }

    def _wait_before_download(self, song_title: str) -> None:
        """Add a conservative random delay before each download attempt."""

        delay = random.randint(self.settings.min_delay_seconds, self.settings.max_delay_seconds)
        print(f"[WAIT] {song_title}: waiting {delay}s before download")
        self.cancellation_token.wait(delay)

    def _get_retry_wait_seconds(self, error_text: str, attempt: int) -> int:
        """Return a longer wait for temporary rate-limit/bot-check style failures."""

        lowered = error_text.lower()
        is_rate_limited = "429" in lowered or "too many requests" in lowered or "bot" in lowered

        if is_rate_limited:
            return self.settings.rate_limit_wait_seconds * attempt

        return self.settings.retry_wait_seconds * attempt


    def _cancel_futures_and_wait(self, executor: ThreadPoolExecutor, futures: list) -> None:
        """Cancel queued work and wait for running workers to leave cleanly."""

        self.cancel()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)

    @staticmethod
    def _format_result(result: DownloadResult) -> str:
        reason = f" | reason={result.reason}" if result.reason else ""
        file_name = f" | file={result.file_name}" if result.file_name else ""
        return f"[{result.status.upper()}] {result.song}{file_name}{reason}"

    @staticmethod
    def _write_results(result_path: Path, results: list[DownloadResult]) -> None:
        """Write a machine-readable result report."""

        payload = [
            {
                "song": result.song,
                "status": result.status.value,
                "file_name": result.file_name,
                "reason": result.reason,
            }
            for result in results
        ]

        with result_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=4, ensure_ascii=False)
