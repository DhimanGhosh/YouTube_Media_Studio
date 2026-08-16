"""Full-album YouTube audio splitter based on manual track timings or silence gaps."""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from youtube_audio_video_downloader.utils.artist_name_formatter import (
    format_artist_names,
)

from youtube_audio_video_downloader.core.exceptions import UserCancelledError
from youtube_audio_video_downloader.metadata.id3_tagger import MetadataTagger
from youtube_audio_video_downloader.domain.models import DownloadResult, DownloadStatus, ParsedSongMetadata, Song
from youtube_audio_video_downloader.config.settings import DownloadSettings
from youtube_audio_video_downloader.core.file_utils import ensure_directory, safe_filename
from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.core.file_access import retry_file_operation
from youtube_audio_video_downloader.services.albums.album_folders import (
    find_existing_album_track,
)

_URL_PREFIXES = ("http://", "https://")
_URL_FIELDS = ("ytb_link", "video_url", "youtube_url", "url")
_TRACK_NAME_FIELDS = ("track_names", "songs", "titles")
_UNKNOWN_ARTIST = "Unknown"


@dataclass(frozen=True, slots=True)
class AlbumTrackSpec:
    """One manually defined track from the album/jukebox JSON."""

    number: int
    title: str
    start_seconds: float
    stop_seconds: float | None
    artists: list[str]
    download: bool = True
    album: str | None = None
    album_art: str = ""
    release_year: str = ""


@dataclass(frozen=True, slots=True)
class AlbumSongSpec:
    """One standalone YouTube song link that belongs to an album.

    ``start_seconds`` and ``end_seconds`` optionally trim the downloaded
    YouTube audio before the final MP3 is saved. This lets a track entry point
    to a longer YouTube video while still exporting only the actual song range.
    """

    number: int
    source_number: int
    title: str
    ytb_link: str
    artists: list[str]
    download: bool = True
    album_art: str = ""
    release_year: str = ""
    start_seconds: float = 0.0
    end_seconds: float | None = None

    @property
    def is_partial_range(self) -> bool:
        """Return True when the track should be trimmed before saving."""

        return self.start_seconds > 0 or self.end_seconds is not None


@dataclass(frozen=True, slots=True)
class AlbumSplitJob:
    """One full-album video that should be downloaded and split into MP3 tracks."""

    json_key: str
    ytb_link: str
    album: str | None = None
    artists: list[str] | None = None
    track_names: list[str] | None = None
    tracks: list[AlbumTrackSpec] | None = None
    song_tracks: list[AlbumSongSpec] | None = None
    album_art: str = ""
    release_year: str = ""
    track_numbering: bool = True
    download: bool = True


@dataclass(frozen=True, slots=True)
class SilenceSegment:
    """A detected mute/silence region in seconds."""

    start: float
    end: float
    duration: float


@dataclass(frozen=True, slots=True)
class TrackSegment:
    """A final audio range to export as one MP3 track."""

    number: int
    start: float
    end: float
    title: str
    artists: list[str] | None = None
    download: bool = True
    source_number: int | None = None
    album: str | None = None
    album_art: str = ""
    release_year: str = ""

    @property
    def duration(self) -> float:
        """Return track duration in seconds."""

        return max(0.0, self.end - self.start)


class YouTubeAlbumSplitter:
    """Download a full-album YouTube video and split it into MP3 tracks.

    The splitter now supports two production flows:

    1. **Manual timings from JSON**: each track has a title, ``start`` time,
       optional ``stop`` time and artists. This is the most accurate approach
       for album videos where you already know the timestamps.
    2. **Individual song links from JSON**: each track has its own ``ytb_link``
       and is downloaded directly as a full MP3, then tagged as part of the
       album. Optional ``start`` and ``end``/``stop`` values can trim a longer
       source video down to only the song section. This is useful when no
       full-album/jukebox video exists.
    3. **Silence detection fallback**: when no structured ``tracks`` list is
       supplied, FFmpeg ``silencedetect`` is used to find mute/silent gaps.

    Album JSON entries can also include ``download: false`` to keep an album
    as a bookmark/place-holder without downloading or validating its track
    timings during the current run.

    The input URL can include ``list=...`` or radio parameters. The downloader
    uses ``noplaylist=True`` so it processes only the full-album video URL you
    provided, not the surrounding playlist/radio queue.
    """

    def __init__(
        self,
        settings: DownloadSettings | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        self.settings = settings or DownloadSettings()
        self.cancellation_token = cancellation_token or CancellationToken()
        self.metadata_tagger = MetadataTagger()

    def cancel(self) -> None:
        """Request cooperative cancellation for album splitting work."""

        self.cancellation_token.cancel()

    def split_from_input(
        self,
        input_value: str | Path,
        *,
        output_dir: Path | None = None,
        album_name: str | None = None,
        artists: str | list[str] | None = None,
        silence_threshold_db: float = -35.0,
        min_silence_duration: float = 1.5,
        min_track_duration: float = 45.0,
        trim_silence_padding: float = 0.25,
        keep_temp: bool = False,
        overwrite: bool = False,
        write_report: bool = False,
    ) -> list[DownloadResult]:
        """Split one URL or every job from a JSON file into separate MP3 tracks."""

        self.cancellation_token.reset()
        jobs, base_dir = self._load_jobs(input_value, album_name=album_name, artists=artists)
        if not jobs:
            raise ValueError("No valid album split jobs found.")

        target_root = ensure_directory(output_dir.resolve() if output_dir else base_dir / "album_tracks")
        results = self._execute_album_work_items(
            jobs,
            target_root,
            silence_threshold_db=silence_threshold_db,
            min_silence_duration=min_silence_duration,
            min_track_duration=min_track_duration,
            trim_silence_padding=trim_silence_padding,
            keep_temp=keep_temp,
            overwrite=overwrite,
        )

        if write_report:
            self._write_results(target_root / "album_split_results.json", results)

        return results

    def _execute_album_work_items(
        self,
        jobs: list[AlbumSplitJob],
        target_root: Path,
        *,
        silence_threshold_db: float,
        min_silence_duration: float,
        min_track_duration: float,
        trim_silence_padding: float,
        keep_temp: bool,
        overwrite: bool,
    ) -> list[DownloadResult]:
        """Run all enabled album network work through one bounded worker pool.

        The global pool prevents nested executors and makes the default command
        parallel across the complete JSON file:

        * every standalone per-track YouTube link is one work item;
        * every full-album source YouTube link is one work item;
        * album/track entries with ``download: false`` are recorded immediately.

        Each network work item applies its own randomized delay before starting.
        Local FFmpeg splitting begins only after its full source is available.
        """

        ordered_chunks: list[list[DownloadResult] | None] = []
        work_items: list[tuple[int, Any]] = []
        provisional_album_dirs: set[Path] = set()

        for job in jobs:
            self.cancellation_token.raise_if_cancelled()

            if not job.download:
                result = DownloadResult(
                    song=job.json_key,
                    status=DownloadStatus.SKIPPED,
                    file_name=safe_filename(job.album or job.json_key, fallback="Album"),
                    reason=(
                        "download=false at album level; skipped complete album without "
                        "downloading source audio"
                    ),
                )
                ordered_chunks.append([result])
                print(self._format_result(result))
                continue

            if job.song_tracks:
                album_name = safe_filename(job.album or job.json_key, fallback=job.json_key)
                # Do not create the provisional (often yearless) folder until a
                # track has actually passed the existing-library preflight.
                album_dir = target_root / album_name
                provisional_album_dirs.add(album_dir)
                downloadable_total = sum(1 for track in job.song_tracks if track.download)

                for track in job.song_tracks:
                    if not track.download:
                        skip_reason = (
                            "download=false; not downloaded; track_numbering=disabled"
                            if not job.track_numbering
                            else (
                                "download=false; not downloaded; "
                                f"next downloadable track keeps saved track number {track.number:02d}"
                            )
                        )
                        result = DownloadResult(
                            song=f"{job.json_key} / Source Track {track.source_number:02d}",
                            status=DownloadStatus.SKIPPED,
                            file_name=None,
                            reason=skip_reason,
                        )
                        ordered_chunks.append([result])
                        print(self._format_result(result))
                        continue

                    slot = len(ordered_chunks)
                    ordered_chunks.append(None)
                    work_items.append(
                        (
                            slot,
                            partial(
                                self._run_individual_album_track_job,
                                job=job,
                                track=track,
                                album_dir=album_dir,
                                album_name=album_name,
                                downloadable_total=downloadable_total,
                                overwrite=overwrite,
                            ),
                        )
                    )
                continue

            slot = len(ordered_chunks)
            ordered_chunks.append(None)
            work_items.append(
                (
                    slot,
                    partial(
                        self._run_single_album_job,
                        job,
                        target_root,
                        silence_threshold_db=silence_threshold_db,
                        min_silence_duration=min_silence_duration,
                        min_track_duration=min_track_duration,
                        trim_silence_padding=trim_silence_padding,
                        keep_temp=keep_temp,
                        overwrite=overwrite,
                    ),
                )
            )

        if not work_items:
            self._remove_empty_album_job_directories(provisional_album_dirs)
            return [result for chunk in ordered_chunks if chunk for result in chunk]

        worker_count = max(1, min(self.settings.max_workers, len(work_items)))
        print(
            f"[PARALLEL] Starting {len(work_items)} album download work item(s) "
            f"with {worker_count} worker(s); each uses a random "
            f"{self.settings.min_delay_seconds}-{self.settings.max_delay_seconds}s delay"
        )

        executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="yt-album")
        futures = {executor.submit(work): slot for slot, work in work_items}
        try:
            for future in as_completed(futures):
                slot = futures[future]
                ordered_chunks[slot] = future.result()
        except (KeyboardInterrupt, UserCancelledError) as exc:
            self._cancel_futures_and_wait(executor, list(futures))
            raise UserCancelledError("Album download cancelled by user") from exc
        else:
            executor.shutdown(wait=True, cancel_futures=False)

        self._remove_empty_album_job_directories(provisional_album_dirs)
        return [result for chunk in ordered_chunks if chunk for result in chunk]

    @staticmethod
    def _remove_empty_album_job_directories(paths: set[Path]) -> None:
        """Remove only empty directories belonging to this splitter run."""

        for path in sorted(paths, key=lambda value: str(value).casefold(), reverse=True):
            try:
                if path.is_dir() and not any(path.iterdir()):
                    retry_file_operation(
                        path,
                        "removing an unused album download folder",
                        path.rmdir,
                    )
                    print(f"[CLEANUP] Removed unused empty album folder: {path}")
            except OSError as exc:
                print(f"[CLEANUP-WARNING] Could not remove empty album folder {path}: {exc}")

    def _run_individual_album_track_job(
        self,
        *,
        job: AlbumSplitJob,
        track: AlbumSongSpec,
        album_dir: Path,
        album_name: str,
        downloadable_total: int,
        overwrite: bool,
    ) -> list[DownloadResult]:
        """Run one standalone album track and return a report-compatible result list."""

        try:
            result = self._download_individual_album_track(
                job,
                track,
                album_dir,
                album_name,
                downloadable_total,
                overwrite,
            )
        except UserCancelledError:
            raise
        except Exception as exc:
            result = DownloadResult(
                song=f"{job.json_key} / Track {track.number:02d}",
                status=DownloadStatus.FAILED,
                file_name=None,
                reason=str(exc),
            )

        print(self._format_result(result))
        return [result]

    def _run_single_album_job(
        self,
        job: AlbumSplitJob,
        target_root: Path,
        *,
        silence_threshold_db: float,
        min_silence_duration: float,
        min_track_duration: float,
        trim_silence_padding: float,
        keep_temp: bool,
        overwrite: bool,
    ) -> list[DownloadResult]:
        """Run one album job and convert expected skip/failure cases to results."""

        try:
            if not job.download:
                result = DownloadResult(
                    song=job.json_key,
                    status=DownloadStatus.SKIPPED,
                    file_name=safe_filename(job.album or job.json_key, fallback="Album"),
                    reason="download=false at album level; skipped complete album without downloading source audio",
                )
                print(self._format_result(result))
                return [result]

            return self._split_job(
                job,
                target_root,
                silence_threshold_db=silence_threshold_db,
                min_silence_duration=min_silence_duration,
                min_track_duration=min_track_duration,
                trim_silence_padding=trim_silence_padding,
                keep_temp=keep_temp,
                overwrite=overwrite,
            )
        except UserCancelledError:
            raise
        except Exception as exc:  # Keep later JSON entries running even if one album fails.
            result = DownloadResult(
                song=job.json_key,
                status=DownloadStatus.FAILED,
                file_name=safe_filename(job.album or job.json_key, fallback=job.json_key),
                reason=str(exc),
            )
            print(self._format_result(result))
            return [result]

    def _download_individual_album_track(
        self,
        job: AlbumSplitJob,
        track: AlbumSongSpec,
        album_dir: Path,
        album_name: str,
        downloadable_total: int,
        overwrite: bool,
    ) -> DownloadResult:
        """Download one standalone album track as MP3 and write album metadata.

        If the track JSON contains ``start`` and/or ``end``/``stop``, the
        source YouTube audio is first downloaded into a temporary file and then
        trimmed with FFmpeg before tagging. If no range is supplied, the older
        direct best-audio-to-MP3 path is preserved for speed.
        """

        self.cancellation_token.raise_if_cancelled()
        artist_names = track.artists or job.artists or [_UNKNOWN_ARTIST]
        artist_text = ", ".join(artist_names) or _UNKNOWN_ARTIST
        structured_stem = safe_filename(
            f"{track.title} - {album_name} - {artist_text}",
            fallback=f"Track {track.number:02d}",
        )
        final_mp3_path = album_dir / f"{structured_stem}.mp3"
        track_label = f"Track {track.number:02d}" if job.track_numbering else track.title

        parsed_metadata = ParsedSongMetadata(
            title=track.title,
            album=album_name,
            artists=artist_names,
        )
        track_number = track.number if job.track_numbering else None
        track_total = downloadable_total if job.track_numbering else None
        song = Song(
            json_key=f"{job.json_key} / {track_label}",
            ytb_link=track.ytb_link,
            file_name=final_mp3_path.stem,
            parsed_metadata=parsed_metadata,
            album_art=track.album_art or job.album_art,
            release_year=track.release_year or job.release_year,
            track_number=track_number,
            track_total=track_total,
        )

        if not overwrite:
            existing_track = find_existing_album_track(
                album_dir.parent,
                title=track.title,
                album=album_name,
                year=track.release_year or job.release_year,
            )
            if existing_track is not None:
                return DownloadResult(
                    song=song.json_key,
                    status=DownloadStatus.ALREADY_EXISTS,
                    file_name=str(existing_track),
                    reason=(
                        "matching enriched track already exists in the album library; "
                        "download skipped"
                    ),
                )

        if final_mp3_path.exists() and not overwrite:
            try:
                self.metadata_tagger.tag_mp3(final_mp3_path, song)
            except Exception as exc:
                print(f"[TAG-WARNING] {song.json_key}: failed to refresh metadata: {exc}")
            return DownloadResult(
                song=song.json_key,
                status=DownloadStatus.ALREADY_EXISTS,
                file_name=str(final_mp3_path),
                reason="MP3 already exists; metadata refreshed; use --overwrite to recreate it",
            )

        if final_mp3_path.exists() and overwrite:
            retry_file_operation(
                final_mp3_path, "removing it before redownload", final_mp3_path.unlink
            )
        ensure_directory(album_dir)
        self._remove_existing_individual_track_files(album_dir, structured_stem)

        for attempt in range(1, self.settings.max_retries + 1):
            self._wait_before_download(song.json_key)
            try:
                if track.is_partial_range:
                    self._download_and_trim_individual_album_track(
                        track=track,
                        final_mp3_path=final_mp3_path,
                    )
                else:
                    import yt_dlp

                    with yt_dlp.YoutubeDL(
                        self._build_individual_track_yt_dlp_options(album_dir, structured_stem)
                    ) as ydl:
                        ydl.download([track.ytb_link])

                if not final_mp3_path.exists():
                    raise FileNotFoundError(f"Expected MP3 was not created: {final_mp3_path}")

                self.metadata_tagger.tag_mp3(final_mp3_path, song)
                track_reason = (
                    f"track_number={track.number:02d}/{downloadable_total:02d}"
                    if job.track_numbering
                    else "track_numbering=disabled"
                )
                range_reason = self._format_individual_song_range_reason(track)
                return DownloadResult(
                    song=song.json_key,
                    status=DownloadStatus.DOWNLOADED,
                    file_name=str(final_mp3_path),
                    reason=(
                        f"standalone_song_link, source_track={track.source_number:02d}, "
                        f"{track_reason}{range_reason}"
                    ),
                )
            except UserCancelledError:
                raise
            except Exception as exc:
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
            file_name=str(final_mp3_path),
            reason="Max retries exceeded",
        )

    def _download_and_trim_individual_album_track(
        self,
        *,
        track: AlbumSongSpec,
        final_mp3_path: Path,
    ) -> None:
        """Download a standalone song source and export only the requested range."""

        self._require_external_binary("ffmpeg")
        self._require_external_binary("ffprobe")
        temp_context = tempfile.TemporaryDirectory(
            prefix="album_track_source_",
            dir=str(final_mp3_path.parent),
        )
        temp_dir = Path(temp_context.name)
        try:
            source_audio_path = self._download_individual_track_source_audio(track, temp_dir)
            source_duration = self._read_audio_duration_seconds(source_audio_path)
            start_seconds = max(0.0, track.start_seconds)
            end_seconds = track.end_seconds if track.end_seconds is not None else source_duration
            end_seconds = min(max(0.0, end_seconds), source_duration)
            if end_seconds <= start_seconds:
                raise ValueError(
                    f"Invalid trim range for {track.title!r}: end "
                    f"{self._format_timestamp(end_seconds)} must be after start "
                    f"{self._format_timestamp(start_seconds)}"
                )

            command = [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-ss",
                self._format_seconds(start_seconds),
                "-t",
                self._format_seconds(end_seconds - start_seconds),
                "-i",
                str(source_audio_path),
                "-vn",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                f"{self.settings.preferred_mp3_quality}k",
                "-ar",
                self.settings.audio_sample_rate,
                str(final_mp3_path),
            ]
            self._run_command(command)
        finally:
            temp_context.cleanup()

    def _download_individual_track_source_audio(
        self,
        track: AlbumSongSpec,
        temp_dir: Path,
    ) -> Path:
        """Download the unconverted source audio for a standalone track."""

        import yt_dlp
        from ..downloads.download_progress import accelerated_download_options

        output_template = str(temp_dir / "source.%(ext)s")
        options: dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "noplaylist": True,
            "continuedl": True,
            "ignoreerrors": False,
            "quiet": False,
            "no_warnings": False,
            "retries": self.settings.max_retries,
            "fragment_retries": self.settings.max_retries,
            **accelerated_download_options(
                track.title,
                self.settings.segment_connections,
                self.cancellation_token,
            ),
        }

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(track.ytb_link, download=True)
            if not isinstance(info, dict):
                raise ValueError("yt-dlp did not return metadata for the song video")
            prepared_path = Path(ydl.prepare_filename(info))

        if prepared_path.exists():
            return prepared_path

        candidates = [path for path in temp_dir.glob("source.*") if path.is_file()]
        if not candidates:
            raise FileNotFoundError("Downloaded source audio file was not found")

        candidates.sort(key=lambda path: path.stat().st_size, reverse=True)
        return candidates[0]

    def _format_individual_song_range_reason(self, track: AlbumSongSpec) -> str:
        """Return a compact report suffix for optional per-song trim ranges."""

        if not track.is_partial_range:
            return ""
        if track.end_seconds is None:
            return f", start={self._format_timestamp(track.start_seconds)}, end=source_duration"
        return (
            f", start={self._format_timestamp(track.start_seconds)}, "
            f"end={self._format_timestamp(track.end_seconds)}"
        )

    def _build_individual_track_yt_dlp_options(self, album_dir: Path, file_name: str) -> dict[str, Any]:
        """Build yt-dlp options for standalone album-track MP3 downloads."""

        from ..downloads.download_progress import accelerated_download_options

        return {
            "format": "bestaudio/best",
            "outtmpl": str(album_dir / f"{file_name}.%(ext)s"),
            "noplaylist": True,
            "continuedl": True,
            "ignoreerrors": False,
            "quiet": False,
            "no_warnings": False,
            "retries": self.settings.max_retries,
            "fragment_retries": self.settings.max_retries,
            **accelerated_download_options(
                file_name,
                self.settings.segment_connections,
                self.cancellation_token,
            ),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": self.settings.preferred_mp3_quality,
                }
            ],
            "postprocessor_args": ["-ar", self.settings.audio_sample_rate],
        }

    @staticmethod
    def _remove_existing_individual_track_files(album_dir: Path, file_name: str) -> None:
        """Remove stale partial/source files for a standalone album-track output stem."""

        patterns = (
            f"{file_name}.webm",
            f"{file_name}.m4a",
            f"{file_name}.opus",
            f"{file_name}.mp4",
            f"{file_name}.part",
            f"{file_name}.*.part",
            f"{file_name}.temp.*",
        )
        for pattern in patterns:
            for path in album_dir.glob(pattern):
                if path.is_file():
                    retry_file_operation(
                        path, "removing it before redownload", path.unlink
                    )

    def _wait_before_download(self, label: str) -> None:
        """Wait a random configured delay before any album network download."""

        delay = random.randint(self.settings.min_delay_seconds, self.settings.max_delay_seconds)
        print(f"[WAIT] {label}: waiting {delay}s before download")
        self.cancellation_token.wait(delay)

    def _get_retry_wait_seconds(self, error_text: str, attempt: int) -> int:
        """Return retry wait seconds for standalone song downloads."""

        lowered = error_text.lower()
        is_rate_limited = "429" in lowered or "too many requests" in lowered or "bot" in lowered
        if is_rate_limited:
            return self.settings.rate_limit_wait_seconds * attempt
        return self.settings.retry_wait_seconds * attempt

    def _cancel_futures_and_wait(self, executor: ThreadPoolExecutor, futures: list) -> None:
        """Cancel queued standalone song work and wait for active workers to exit."""

        self.cancel()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)

    def _split_job(
        self,
        job: AlbumSplitJob,
        target_root: Path,
        *,
        silence_threshold_db: float,
        min_silence_duration: float,
        min_track_duration: float,
        trim_silence_padding: float,
        keep_temp: bool,
        overwrite: bool,
    ) -> list[DownloadResult]:
        """Download, split and tag one full-album video."""

        self._require_external_binary("ffmpeg")
        self._require_external_binary("ffprobe")

        album_dir_name = safe_filename(job.album or job.json_key, fallback=job.json_key)
        album_dir = ensure_directory(target_root / album_dir_name)
        temp_context = tempfile.TemporaryDirectory(prefix="album_split_", dir=str(target_root))
        temp_dir = Path(temp_context.name)

        try:
            self._wait_before_download(job.json_key)
            print(f"[ALBUM] {job.json_key}: downloading best source audio")
            source_audio_path, info = self._download_source_audio(job, temp_dir)
            album_name = self._resolve_album_name(job, info)
            album_artist_names = self._resolve_artists(job, info)
            duration = self._read_audio_duration_seconds(source_audio_path)

            if job.tracks:
                print(
                    f"[ALBUM] {job.json_key}: using {len(job.tracks)} manual track timing(s) "
                    "from JSON"
                )
                segments = self._build_track_segments_from_manual_tracks(
                    tracks=job.tracks,
                    duration=duration,
                    fallback_artists=album_artist_names,
                )
            else:
                print(
                    f"[ALBUM] {job.json_key}: detecting silence "
                    f"noise={silence_threshold_db}dB duration={min_silence_duration}s"
                )
                silences = self._detect_silences(
                    source_audio_path,
                    silence_threshold_db=silence_threshold_db,
                    min_silence_duration=min_silence_duration,
                )
                segments = self._build_track_segments_from_silence(
                    album_name=album_name,
                    duration=duration,
                    silences=silences,
                    track_names=job.track_names or [],
                    min_track_duration=min_track_duration,
                    trim_silence_padding=trim_silence_padding,
                    fallback_artists=album_artist_names,
                )
                print(
                    f"[ALBUM] {job.json_key}: found {len(silences)} silence section(s), "
                    f"exporting {len(segments)} MP3 track(s)"
                )

            if not segments:
                raise ValueError(
                    "No valid track segments were created. If you are using manual tracks, "
                    "check start/stop values. If you are using silence detection, try a less "
                    "strict threshold, for example --silence-threshold-db -30."
                )

            results = self._export_segments(
                job=job,
                source_audio_path=source_audio_path,
                album_dir=album_dir,
                album_name=album_name,
                segments=segments,
                overwrite=overwrite,
            )

            if keep_temp:
                kept_dir = album_dir / "_source_audio"
                if kept_dir.exists():
                    shutil.rmtree(kept_dir)
                shutil.move(str(temp_dir), str(kept_dir))
                print(f"[ALBUM] {job.json_key}: kept temporary source files at {kept_dir}")

            return results
        finally:
            if not keep_temp:
                temp_context.cleanup()

    def _download_source_audio(
        self,
        job: AlbumSplitJob,
        temp_dir: Path,
    ) -> tuple[Path, dict[str, Any]]:
        """Download the best available source audio with yt-dlp."""

        import yt_dlp
        from ..downloads.download_progress import accelerated_download_options

        output_template = str(temp_dir / "source.%(ext)s")
        options: dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "noplaylist": True,
            "continuedl": True,
            "ignoreerrors": False,
            "quiet": False,
            "no_warnings": False,
            "retries": self.settings.max_retries,
            "fragment_retries": self.settings.max_retries,
            **accelerated_download_options(
                job.json_key,
                self.settings.segment_connections,
                self.cancellation_token,
            ),
        }

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(job.ytb_link, download=True)
            if not isinstance(info, dict):
                raise ValueError("yt-dlp did not return metadata for the album video")
            prepared_path = Path(ydl.prepare_filename(info))

        if prepared_path.exists():
            return prepared_path, info

        candidates = [path for path in temp_dir.glob("source.*") if path.is_file()]
        if not candidates:
            raise FileNotFoundError("Downloaded source audio file was not found")

        candidates.sort(key=lambda path: path.stat().st_size, reverse=True)
        return candidates[0], info

    def _detect_silences(
        self,
        source_audio_path: Path,
        *,
        silence_threshold_db: float,
        min_silence_duration: float,
    ) -> list[SilenceSegment]:
        """Return silence segments detected by FFmpeg's silencedetect filter."""

        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(source_audio_path),
            "-af",
            f"silencedetect=noise={silence_threshold_db}dB:d={min_silence_duration}",
            "-f",
            "null",
            "-",
        ]
        completed = self._run_command(command)
        output = f"{completed.stdout}\n{completed.stderr}"

        pending_starts: list[float] = []
        silences: list[SilenceSegment] = []
        start_pattern = re.compile(r"silence_start:\s*([0-9.]+)")
        end_pattern = re.compile(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)")

        for line in output.splitlines():
            start_match = start_pattern.search(line)
            if start_match:
                pending_starts.append(float(start_match.group(1)))
                continue

            end_match = end_pattern.search(line)
            if end_match:
                end_value = float(end_match.group(1))
                duration_value = float(end_match.group(2))
                start_value = pending_starts.pop(0) if pending_starts else end_value - duration_value
                if duration_value >= min_silence_duration:
                    silences.append(
                        SilenceSegment(
                            start=max(0.0, start_value),
                            end=max(0.0, end_value),
                            duration=max(0.0, duration_value),
                        )
                    )

        return silences

    def _build_track_segments_from_manual_tracks(
        self,
        *,
        tracks: list[AlbumTrackSpec],
        duration: float,
        fallback_artists: list[str],
    ) -> list[TrackSegment]:
        """Create track segments from explicit JSON start/stop values.

        Tracks marked with ``download: false`` are kept in the timeline so they
        can still be used as boundaries for neighbouring tracks, but they are
        not exported. Downloadable tracks receive compact sequential track
        numbers, so skipped tracks do not leave gaps in the saved MP3 metadata.
        """

        if not tracks:
            return []

        sorted_tracks = sorted(tracks, key=lambda item: (item.start_seconds, item.number))
        segments: list[TrackSegment] = []
        next_download_number = 1

        for index, track in enumerate(sorted_tracks):
            start = max(0.0, track.start_seconds)
            if track.stop_seconds is not None:
                end = track.stop_seconds
            elif index + 1 < len(sorted_tracks):
                # The project convention is inclusive stop time. If stop is omitted,
                # use one second before the next track starts, as requested.
                # This intentionally considers skipped tracks too, because they are
                # still real timeline boundaries in the source album video.
                end = sorted_tracks[index + 1].start_seconds - 1.0
            else:
                end = duration

            end = min(max(0.0, end), duration)
            if end <= start:
                raise ValueError(
                    f"Invalid timing for source track {track.number:02d} ({track.title!r}): "
                    f"stop/end {self._format_timestamp(end)} must be after start "
                    f"{self._format_timestamp(start)}"
                )

            track_artists = track.artists or fallback_artists or [_UNKNOWN_ARTIST]
            if track.download:
                segment_number = next_download_number
                next_download_number += 1
            else:
                # Do not consume a saved-track number. The next downloadable track
                # will receive this same number.
                segment_number = next_download_number

            segments.append(
                TrackSegment(
                    number=segment_number,
                    start=start,
                    end=end,
                    title=track.title,
                    artists=track_artists,
                    download=track.download,
                    source_number=track.number,
                    album=track.album,
                    album_art=track.album_art,
                    release_year=track.release_year,
                )
            )

        return segments

    def _build_track_segments_from_silence(
        self,
        *,
        album_name: str,
        duration: float,
        silences: list[SilenceSegment],
        track_names: list[str],
        min_track_duration: float,
        trim_silence_padding: float,
        fallback_artists: list[str],
    ) -> list[TrackSegment]:
        """Create track export ranges from silence cut points."""

        cut_points: list[float] = []
        previous_boundary = 0.0

        for silence in silences:
            cut_point = (silence.start + silence.end) / 2
            if cut_point - previous_boundary < min_track_duration:
                continue
            if duration - cut_point < min_track_duration:
                continue
            cut_points.append(cut_point)
            previous_boundary = cut_point

        boundaries = [0.0, *cut_points, duration]
        segments: list[TrackSegment] = []

        for index in range(len(boundaries) - 1):
            start = boundaries[index]
            end = boundaries[index + 1]

            if index > 0:
                start += max(0.0, trim_silence_padding)
            if index < len(boundaries) - 2:
                end -= max(0.0, trim_silence_padding)

            if end - start < min_track_duration:
                continue

            title = self._track_title(album_name, track_names, index + 1)
            segments.append(
                TrackSegment(
                    number=index + 1,
                    start=start,
                    end=end,
                    title=title,
                    artists=fallback_artists or [_UNKNOWN_ARTIST],
                )
            )

        # If no usable cut point was found, still export the full audio as one track.
        if not segments and duration >= min_track_duration:
            segments.append(
                TrackSegment(
                    number=1,
                    start=0.0,
                    end=duration,
                    title=self._track_title(album_name, track_names, 1),
                    artists=fallback_artists or [_UNKNOWN_ARTIST],
                )
            )

        return segments

    def _export_segments(
        self,
        *,
        job: AlbumSplitJob,
        source_audio_path: Path,
        album_dir: Path,
        album_name: str,
        segments: list[TrackSegment],
        overwrite: bool,
    ) -> list[DownloadResult]:
        """Export track segments as high-quality MP3 files and tag them."""

        results: list[DownloadResult] = []
        downloadable_total = sum(1 for segment in segments if segment.download)

        for segment in segments:
            source_number = segment.source_number or segment.number

            if not segment.download:
                skip_reason = (
                    "download=false; not exported; track_numbering=disabled"
                    if not job.track_numbering
                    else (
                        "download=false; not exported; "
                        f"next downloadable track keeps saved track number {segment.number:02d}"
                    )
                )
                result = DownloadResult(
                    song=f"{job.json_key} / Source Track {source_number:02d}",
                    status=DownloadStatus.SKIPPED,
                    file_name=None,
                    reason=skip_reason,
                )
                results.append(result)
                print(self._format_result(result))
                continue

            artist_names = segment.artists or [_UNKNOWN_ARTIST]
            artist_text = ", ".join(artist_names) or _UNKNOWN_ARTIST
            structured_stem = safe_filename(
                f"{segment.title} - {album_name} - {artist_text}",
                fallback=f"Track {segment.number:02d}",
            )
            output_path = album_dir / f"{structured_stem}.mp3"
            track_label = (
                f"Track {segment.number:02d}" if job.track_numbering else segment.title
            )

            if output_path.exists() and not overwrite:
                result = DownloadResult(
                    song=f"{job.json_key} / {track_label}",
                    status=DownloadStatus.ALREADY_EXISTS,
                    file_name=str(output_path),
                    reason="MP3 already exists; use --overwrite to recreate it",
                )
                results.append(result)
                print(self._format_result(result))
                continue

            if output_path.exists() and overwrite:
                retry_file_operation(
                    output_path, "removing it before recreating the track", output_path.unlink
                )

            command = [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-ss",
                self._format_seconds(segment.start),
                "-t",
                self._format_seconds(segment.duration),
                "-i",
                str(source_audio_path),
                "-vn",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                f"{self.settings.preferred_mp3_quality}k",
                "-ar",
                self.settings.audio_sample_rate,
                str(output_path),
            ]
            self._run_command(command)

            parsed_metadata = ParsedSongMetadata(
                title=segment.title,
                album=album_name,
                artists=artist_names,
            )
            track_number = segment.number if job.track_numbering else None
            track_total = downloadable_total if job.track_numbering else None
            song = Song(
                json_key=f"{job.json_key} / {track_label}",
                ytb_link=job.ytb_link,
                file_name=output_path.stem,
                parsed_metadata=parsed_metadata,
                album_art=job.album_art,
                release_year=job.release_year,
                track_number=track_number,
                track_total=track_total,
            )
            self.metadata_tagger.tag_mp3(output_path, song)

            track_reason = (
                f"track_number={segment.number:02d}/{downloadable_total:02d}"
                if job.track_numbering
                else "track_numbering=disabled"
            )
            result = DownloadResult(
                song=song.json_key,
                status=DownloadStatus.DOWNLOADED,
                file_name=str(output_path),
                reason=(
                    f"{track_reason}, "
                    f"source_track={source_number:02d}, "
                    f"start={self._format_timestamp(segment.start)}, "
                    f"stop={self._format_timestamp(segment.end)}, "
                    f"duration={self._format_seconds(segment.duration)}s"
                ),
            )
            results.append(result)
            print(self._format_result(result))

        return results

    def _read_audio_duration_seconds(self, source_audio_path: Path) -> float:
        """Return source audio duration using ffprobe."""

        completed = self._run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source_audio_path),
            ]
        )
        duration_text = completed.stdout.strip().splitlines()[-1]
        try:
            return float(duration_text)
        except ValueError as exc:
            raise ValueError(f"Unable to read source audio duration: {duration_text!r}") from exc

    @staticmethod
    def _load_jobs(
        input_value: str | Path,
        *,
        album_name: str | None,
        artists: str | list[str] | None,
    ) -> tuple[list[AlbumSplitJob], Path]:
        """Load jobs from a JSON/JSONC file or create one job from a direct URL."""

        input_text = str(input_value).strip()
        if input_text.lower().startswith(_URL_PREFIXES):
            job = AlbumSplitJob(
                json_key=album_name or "YouTube Album",
                ytb_link=input_text,
                album=album_name,
                artists=YouTubeAlbumSplitter._parse_artists(artists),
            )
            return [job], Path.cwd()

        json_path = Path(input_text).expanduser().resolve()
        if not json_path.exists():
            raise FileNotFoundError(f"Album JSON file not found: {json_path}")

        raw_data = YouTubeAlbumSplitter._read_json_or_jsonc(json_path)

        if not isinstance(raw_data, dict):
            raise ValueError("Album splitter input JSON must be an object/dictionary.")

        jobs: list[AlbumSplitJob] = []
        errors: list[str] = []
        for json_key, metadata in raw_data.items():
            if not isinstance(metadata, dict):
                errors.append(f"{json_key!r}: metadata must be a JSON object")
                continue

            key_text = str(json_key).strip()
            track_numbering = YouTubeAlbumSplitter._parse_album_bool_flag(
                metadata.get("track_numbering", True),
                field_name="track_numbering",
                album_key=key_text,
                errors=errors,
            )
            if track_numbering is None:
                continue

            album_download = YouTubeAlbumSplitter._parse_album_bool_flag(
                metadata.get("download", True),
                field_name="download",
                album_key=key_text,
                errors=errors,
            )
            if album_download is None:
                continue

            job_artists = YouTubeAlbumSplitter._parse_artists(
                metadata.get("artists") or metadata.get("artist") or artists
            )
            job_album = (
                str(metadata.get("album") or "").strip()
                or str(metadata.get("album_name") or "").strip()
                or str(metadata.get("title") or "").strip()
                or album_name
                or key_text
            )

            ytb_link = ""
            for field in _URL_FIELDS:
                ytb_link = str(metadata.get(field) or "").strip()
                if ytb_link:
                    break

            track_specs: list[AlbumTrackSpec] = []
            song_tracks: list[AlbumSongSpec] = []
            track_names: list[str] = []

            if album_download:
                if YouTubeAlbumSplitter._has_individual_song_links(metadata):
                    song_tracks = YouTubeAlbumSplitter._parse_individual_song_specs(
                        metadata,
                        key_text,
                        errors,
                    )
                else:
                    if not ytb_link:
                        errors.append(f"{key_text!r}: missing ytb_link/video_url/youtube_url/url")
                        continue
                    track_specs = YouTubeAlbumSplitter._parse_track_specs(metadata, key_text, errors)
                    track_names = YouTubeAlbumSplitter._parse_track_names(metadata)
            else:
                # A disabled album is intentionally retained in JSON as a bookmark.
                # Do not validate source URLs, track timings or per-track values because
                # nothing is downloaded during this run.
                track_specs = []
                song_tracks = []
                track_names = []

            jobs.append(
                AlbumSplitJob(
                    json_key=key_text,
                    ytb_link=ytb_link,
                    album=job_album,
                    artists=job_artists,
                    track_names=track_names,
                    tracks=track_specs,
                    song_tracks=song_tracks,
                    album_art=str(metadata.get("album_art") or "").strip(),
                    release_year=str(metadata.get("release_year") or "").strip(),
                    track_numbering=track_numbering,
                    download=album_download,
                )
            )

        if errors:
            formatted = "\n".join(f"- {error}" for error in errors)
            raise ValueError(f"Invalid album split metadata found:\n{formatted}")

        return jobs, json_path.parent

    @staticmethod
    def _read_json_or_jsonc(json_path: Path) -> Any:
        """Read JSON while tolerating // and /* */ comments outside strings."""

        text = json_path.read_text(encoding="utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            cleaned = YouTubeAlbumSplitter._strip_json_comments(text)
            return json.loads(cleaned)

    @staticmethod
    def _strip_json_comments(text: str) -> str:
        """Remove JavaScript-style comments without touching URLs inside strings."""

        result: list[str] = []
        index = 0
        in_string = False
        escaped = False
        length = len(text)

        while index < length:
            char = text[index]
            next_char = text[index + 1] if index + 1 < length else ""

            if in_string:
                result.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                index += 1
                continue

            if char == '"':
                in_string = True
                result.append(char)
                index += 1
                continue

            if char == "/" and next_char == "/":
                index += 2
                while index < length and text[index] not in "\r\n":
                    index += 1
                continue

            if char == "/" and next_char == "*":
                index += 2
                while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                    if text[index] in "\r\n":
                        result.append(text[index])
                    index += 1
                index += 2
                continue

            result.append(char)
            index += 1

        return "".join(result)

    @staticmethod
    def _has_individual_song_links(metadata: dict[str, Any]) -> bool:
        """Return True when the album tracks contain per-song YouTube URLs."""

        raw_tracks = metadata.get("tracks")
        if not isinstance(raw_tracks, list):
            return False

        for item in raw_tracks:
            title, payload = YouTubeAlbumSplitter._extract_track_payload(item)
            if title and isinstance(payload, dict):
                for field in _URL_FIELDS:
                    if str(payload.get(field) or "").strip():
                        return True
        return False

    @staticmethod
    def _extract_track_payload(item: Any) -> tuple[str, Any]:
        """Extract a track title/payload pair from either supported JSON shape."""

        if not isinstance(item, dict):
            return "", None
        if "title" in item or "name" in item or "ytb_link" in item or "url" in item:
            title = str(item.get("title") or item.get("name") or "").strip()
            return title, item
        if len(item) == 1:
            title, payload = next(iter(item.items()))
            return str(title).strip(), payload
        return "", None

    @staticmethod
    def _parse_individual_song_specs(
        metadata: dict[str, Any],
        album_key: str,
        errors: list[str],
    ) -> list[AlbumSongSpec]:
        """Parse tracks where each album song has its own YouTube URL."""

        raw_tracks = metadata.get("tracks")
        if raw_tracks is None:
            errors.append(f"{album_key!r}: individual-song album mode requires tracks")
            return []
        if not isinstance(raw_tracks, list):
            errors.append(f"{album_key!r}: tracks must be a list")
            return []

        parsed: list[AlbumSongSpec] = []
        next_download_number = 1

        for source_index, item in enumerate(raw_tracks, start=1):
            title, payload = YouTubeAlbumSplitter._extract_track_payload(item)
            if not title:
                errors.append(
                    f"{album_key!r}: track #{source_index} must be either "
                    "{\"Track title\": {...}} or {\"title\": ..., \"ytb_link\": ...}"
                )
                continue
            if not isinstance(payload, dict):
                errors.append(f"{album_key!r}: track {title!r} metadata must be an object")
                continue

            download = YouTubeAlbumSplitter._parse_download_flag(
                payload.get("download", True),
                album_key=album_key,
                track_title=title,
                errors=errors,
            )
            if download is None:
                continue

            ytb_link = ""
            for field in _URL_FIELDS:
                ytb_link = str(payload.get(field) or "").strip()
                if ytb_link:
                    break

            if download and not ytb_link:
                errors.append(f"{album_key!r}: track {title!r} is missing ytb_link/url")
                continue

            start_seconds = 0.0
            end_seconds = None
            if download:
                if "start" in payload and str(payload.get("start") or "").strip():
                    parsed_start = YouTubeAlbumSplitter._parse_time_value(
                        payload.get("start"),
                        field_name="start",
                        album_key=album_key,
                        track_title=title,
                        errors=errors,
                    )
                    if parsed_start is None:
                        continue
                    start_seconds = parsed_start

                end_value = None
                end_field_name = "end"
                if "end" in payload and str(payload.get("end") or "").strip():
                    end_value = payload.get("end")
                    end_field_name = "end"
                elif "stop" in payload and str(payload.get("stop") or "").strip():
                    end_value = payload.get("stop")
                    end_field_name = "stop"

                if end_value is not None:
                    parsed_end = YouTubeAlbumSplitter._parse_time_value(
                        end_value,
                        field_name=end_field_name,
                        album_key=album_key,
                        track_title=title,
                        errors=errors,
                    )
                    if parsed_end is None:
                        continue
                    end_seconds = parsed_end

                if end_seconds is not None and end_seconds <= start_seconds:
                    errors.append(
                        f"{album_key!r}: track {title!r} {end_field_name} must be after start "
                        f"({payload.get('start', '00:00')!r} -> {end_value!r})"
                    )
                    continue

            artist_value = payload.get("artists", payload.get("artist"))
            track_artists = YouTubeAlbumSplitter._parse_artists(artist_value) or [_UNKNOWN_ARTIST]

            if download:
                saved_track_number = next_download_number
                next_download_number += 1
            else:
                saved_track_number = next_download_number

            parsed.append(
                AlbumSongSpec(
                    number=saved_track_number,
                    source_number=source_index,
                    title=title,
                    ytb_link=ytb_link,
                    artists=track_artists,
                    download=download,
                    album_art=str(payload.get("album_art") or "").strip(),
                    release_year=str(payload.get("release_year") or "").strip(),
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                )
            )

        return parsed

    @staticmethod
    def _parse_track_specs(
        metadata: dict[str, Any],
        album_key: str,
        errors: list[str],
    ) -> list[AlbumTrackSpec]:
        """Parse the structured ``tracks`` list with explicit start/stop values."""

        raw_tracks = metadata.get("tracks")
        if raw_tracks is None:
            return []

        if not isinstance(raw_tracks, list):
            errors.append(f"{album_key!r}: tracks must be a list")
            return []

        parsed: list[AlbumTrackSpec] = []
        for index, item in enumerate(raw_tracks, start=1):
            title = ""
            payload: Any = None

            if isinstance(item, dict):
                if "title" in item or "name" in item or "start" in item:
                    title = str(item.get("title") or item.get("name") or f"Track {index:02d}").strip()
                    payload = item
                elif len(item) == 1:
                    title, payload = next(iter(item.items()))
                    title = str(title).strip()
                else:
                    errors.append(
                        f"{album_key!r}: track #{index} must be either "
                        "{\"Track title\": {...}} or {\"title\": ..., \"start\": ...}"
                    )
                    continue
            else:
                # A plain string track is still accepted for old silence-detection mode.
                # It cannot create a manual segment because it has no start time.
                continue

            if not title:
                errors.append(f"{album_key!r}: track #{index} has an empty title")
                continue
            if not isinstance(payload, dict):
                errors.append(f"{album_key!r}: track {title!r} metadata must be an object")
                continue

            if "start" not in payload:
                errors.append(f"{album_key!r}: track {title!r} is missing required start")
                continue

            start_seconds = YouTubeAlbumSplitter._parse_time_value(
                payload.get("start"),
                field_name="start",
                album_key=album_key,
                track_title=title,
                errors=errors,
            )
            stop_seconds = None
            if "stop" in payload and str(payload.get("stop") or "").strip():
                stop_seconds = YouTubeAlbumSplitter._parse_time_value(
                    payload.get("stop"),
                    field_name="stop",
                    album_key=album_key,
                    track_title=title,
                    errors=errors,
                )

            if start_seconds is None:
                continue
            if stop_seconds is not None and stop_seconds <= start_seconds:
                errors.append(
                    f"{album_key!r}: track {title!r} stop must be after start "
                    f"({payload.get('start')!r} -> {payload.get('stop')!r})"
                )
                continue

            artist_value = payload.get("artists", payload.get("artist"))
            track_artists = YouTubeAlbumSplitter._parse_artists(artist_value) or [_UNKNOWN_ARTIST]
            download = YouTubeAlbumSplitter._parse_download_flag(
                payload.get("download", True),
                album_key=album_key,
                track_title=title,
                errors=errors,
            )
            if download is None:
                continue

            parsed.append(
                AlbumTrackSpec(
                    number=index,
                    title=title,
                    start_seconds=start_seconds,
                    stop_seconds=stop_seconds,
                    artists=track_artists,
                    download=download,
                )
            )

        if parsed:
            previous_start = -1.0
            for track in sorted(parsed, key=lambda item: item.number):
                if track.start_seconds < previous_start:
                    errors.append(
                        f"{album_key!r}: track {track.title!r} starts before the previous track. "
                        "Keep tracks in timeline order."
                    )
                    break
                previous_start = track.start_seconds

        return parsed

    @staticmethod
    def _parse_download_flag(
        value: Any,
        *,
        album_key: str,
        track_title: str,
        errors: list[str],
    ) -> bool | None:
        """Parse optional track-level download true/false flag.

        Missing values default to True. Strings are accepted because the user
        JSON examples use "true"/"false".
        """

        if isinstance(value, bool):
            return value
        if value is None:
            return True

        text = str(value).strip().lower()
        if text in {"true", "yes", "y", "1", "download"}:
            return True
        if text in {"false", "no", "n", "0", "skip"}:
            return False

        errors.append(
            f"{album_key!r}: track {track_title!r} download must be true or false. "
            f"Received {value!r}"
        )
        return None

    @staticmethod
    def _parse_album_bool_flag(
        value: Any,
        *,
        field_name: str,
        album_key: str,
        errors: list[str],
    ) -> bool | None:
        """Parse album-level true/false feature flags.

        Missing values default to True. Strings are accepted because the JSON
        examples use "true"/"false".
        """

        if isinstance(value, bool):
            return value
        if value is None:
            return True

        text = str(value).strip().lower()
        if text in {"true", "yes", "y", "1", "enable", "enabled", "on"}:
            return True
        if text in {"false", "no", "n", "0", "disable", "disabled", "off"}:
            return False

        errors.append(
            f"{album_key!r}: {field_name} must be true or false. "
            f"Received {value!r}"
        )
        return None


    @staticmethod
    def _parse_time_value(
        value: Any,
        *,
        field_name: str,
        album_key: str,
        track_title: str,
        errors: list[str],
    ) -> float | None:
        """Parse seconds, MM:SS or HH:MM:SS into seconds."""

        if isinstance(value, int | float):
            seconds = float(value)
            if seconds < 0:
                errors.append(f"{album_key!r}: track {track_title!r} {field_name} cannot be negative")
                return None
            return seconds

        text = str(value or "").strip()
        if not text:
            errors.append(f"{album_key!r}: track {track_title!r} {field_name} cannot be empty")
            return None

        try:
            parts = [float(part) for part in text.split(":")]
        except ValueError:
            errors.append(
                f"{album_key!r}: track {track_title!r} {field_name} must be seconds, MM:SS, "
                f"or HH:MM:SS. Received {text!r}"
            )
            return None

        if len(parts) == 1:
            seconds = parts[0]
        elif len(parts) == 2:
            minutes, seconds_part = parts
            seconds = minutes * 60 + seconds_part
        elif len(parts) == 3:
            hours, minutes, seconds_part = parts
            seconds = hours * 3600 + minutes * 60 + seconds_part
        else:
            errors.append(
                f"{album_key!r}: track {track_title!r} {field_name} must be seconds, MM:SS, "
                f"or HH:MM:SS. Received {text!r}"
            )
            return None

        if seconds < 0:
            errors.append(f"{album_key!r}: track {track_title!r} {field_name} cannot be negative")
            return None
        return seconds

    @staticmethod
    def _parse_artists(value: Any) -> list[str]:
        """Parse artists using the shared comma-separated display format."""

        if isinstance(value, list):
            text = ", ".join(str(item).strip() for item in value if str(item).strip())
        else:
            text = str(value or "").strip()
        formatted = format_artist_names(text)
        return [item.strip() for item in formatted.split(",") if item.strip()]

    @staticmethod
    def _parse_track_names(metadata: dict[str, Any]) -> list[str]:
        """Parse optional manual track names for silence-detection mode."""

        raw_value: Any = None
        for field in _TRACK_NAME_FIELDS:
            if field in metadata:
                raw_value = metadata[field]
                break

        if raw_value is None:
            raw_tracks = metadata.get("tracks")
            if isinstance(raw_tracks, list) and all(isinstance(item, str) for item in raw_tracks):
                raw_value = raw_tracks
            else:
                return []

        if isinstance(raw_value, list):
            parsed: list[str] = []
            for item in raw_value:
                if isinstance(item, dict):
                    name = str(item.get("title") or item.get("name") or "").strip()
                else:
                    name = str(item or "").strip()
                if name:
                    parsed.append(name)
            return parsed

        text = str(raw_value).strip()
        if not text:
            return []
        return [line.strip() for line in text.splitlines() if line.strip()]

    @staticmethod
    def _resolve_album_name(job: AlbumSplitJob, info: dict[str, Any]) -> str:
        """Resolve album name from JSON/CLI first and YouTube title second."""

        return safe_filename(
            job.album or str(info.get("title") or "").strip() or job.json_key,
            fallback=job.json_key,
        )

    @staticmethod
    def _resolve_artists(job: AlbumSplitJob, info: dict[str, Any]) -> list[str]:
        """Resolve artist list from JSON first and YouTube uploader/channel second."""

        if job.artists:
            return job.artists

        uploader = str(info.get("uploader") or info.get("channel") or _UNKNOWN_ARTIST).strip()
        return [uploader or _UNKNOWN_ARTIST]

    @staticmethod
    def _track_title(album_name: str, track_names: list[str], track_number: int) -> str:
        """Return manual track title or a numbered fallback title."""

        if 0 <= track_number - 1 < len(track_names):
            return track_names[track_number - 1]
        return f"{album_name} Track {track_number:02d}"

    @staticmethod
    def _format_seconds(value: float) -> str:
        """Format seconds in a compact FFmpeg-friendly form."""

        return f"{max(0.0, value):.3f}"

    @staticmethod
    def _format_timestamp(value: float) -> str:
        """Format seconds as HH:MM:SS for human-readable reports."""

        total_seconds = int(max(0.0, round(value)))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _require_external_binary(binary_name: str) -> None:
        """Fail early with a readable message when FFmpeg tools are missing."""

        if shutil.which(binary_name) is None:
            raise FileNotFoundError(
                f"{binary_name} was not found in PATH. Install FFmpeg and restart the terminal."
            )

    @staticmethod
    def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a subprocess and convert expected failures into readable exceptions."""

        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
            )
        except KeyboardInterrupt as exc:
            raise UserCancelledError("Operation cancelled by user") from exc

        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            detail = stderr or stdout or f"exit code {completed.returncode}"
            raise RuntimeError(f"Command failed: {' '.join(command[:3])} ... {detail}")

        return completed

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
                "track": result.song,
                "status": result.status.value,
                "file_name": result.file_name,
                "reason": result.reason,
            }
            for result in results
        ]
        with result_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=4, ensure_ascii=False)
