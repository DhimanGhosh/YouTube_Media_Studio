"""Jukebox YouTube audio splitter based on manual song timings.

This service is intentionally separate from the album splitter because a
jukebox/compilation video usually does not represent one real album. The JSON
key becomes the output folder name, and each listed song is cut into a separate
high-quality MP3 track.
"""

from __future__ import annotations

import random
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from youtube_audio_video_downloader.services.album_splitter import (
    _UNKNOWN_ARTIST,
    _URL_FIELDS,
    _URL_PREFIXES,
    AlbumTrackSpec,
    TrackSegment,
    YouTubeAlbumSplitter,
)
from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.core.file_access import retry_file_operation
from youtube_audio_video_downloader.core.exceptions import UserCancelledError
from youtube_audio_video_downloader.metadata.id3_tagger import MetadataTagger
from youtube_audio_video_downloader.domain.models import DownloadResult, DownloadStatus, ParsedSongMetadata, Song
from youtube_audio_video_downloader.config.settings import DownloadSettings
from youtube_audio_video_downloader.core.file_utils import ensure_directory, safe_filename
from youtube_audio_video_downloader.utils.artist_name_formatter import (
    format_artist_names,
)


@dataclass(frozen=True, slots=True)
class JukeboxSplitJob:
    """One jukebox/compilation YouTube video to split into MP3 songs."""

    json_key: str
    ytb_link: str
    tracks: list[AlbumTrackSpec]
    album_art: str = ""
    release_year: str = ""
    track_numbering: bool = True
    download: bool = True


class YouTubeJukeboxSplitter(YouTubeAlbumSplitter):
    """Download one jukebox video and export manually defined MP3 song ranges.

    Supported JSON shape::

        {
          "Feel Good Hindi Songs": {
            "ytb_link": "https://www.youtube.com/watch?v=...",
            "track_numbering": "false",
            "download": "true",
            "tracks": [
              {"Song Name": {"start": "00:00:00", "end": "00:05:18", "album": "Album", "artists": "Artist", "album_art": "...", "release_year": "2020"}}
            ]
          }
        }

    ``end`` is the preferred field name for jukeboxes. ``stop`` is also accepted
    for consistency with the album splitter. If ``end``/``stop`` is missing or
    blank, the splitter uses the next listed track start minus one second. For
    the final track, a blank end means the source audio duration.
    """

    def __init__(
        self,
        settings: DownloadSettings | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        super().__init__(settings=settings, cancellation_token=cancellation_token)
        self.metadata_tagger = MetadataTagger()

    def split_from_json(
        self,
        json_file: str | Path,
        *,
        output_dir: Path | None = None,
        keep_temp: bool = False,
        overwrite: bool = False,
        write_report: bool = True,
    ) -> list[DownloadResult]:
        """Split every jukebox job from a JSON/JSONC file."""

        self.cancellation_token.reset()
        jobs, base_dir = self._load_jukebox_jobs(json_file)
        if not jobs:
            raise ValueError("No valid jukebox split jobs found.")

        target_root = ensure_directory(output_dir.resolve() if output_dir else base_dir / "jukebox_tracks")
        results = self._execute_jukebox_jobs(
            jobs,
            target_root,
            keep_temp=keep_temp,
            overwrite=overwrite,
        )

        if write_report:
            self._write_results(target_root / "jukebox_split_results.json", results)

        return results

    def _execute_jukebox_jobs(
        self,
        jobs: list[JukeboxSplitJob],
        target_root: Path,
        *,
        keep_temp: bool,
        overwrite: bool,
    ) -> list[DownloadResult]:
        """Execute enabled jukebox jobs through one bounded worker pool."""

        ordered_results: list[list[DownloadResult] | None] = [None] * len(jobs)
        active_jobs: list[tuple[int, JukeboxSplitJob]] = []

        for index, job in enumerate(jobs):
            self.cancellation_token.raise_if_cancelled()
            if job.download:
                active_jobs.append((index, job))
                continue

            result = DownloadResult(
                song=job.json_key,
                status=DownloadStatus.SKIPPED,
                file_name=safe_filename(job.json_key, fallback="Jukebox"),
                reason=(
                    "download=false at jukebox level; skipped complete jukebox without "
                    "downloading source audio"
                ),
            )
            ordered_results[index] = [result]
            print(self._format_result(result))

        if not active_jobs:
            return [
                result
                for job_results in ordered_results
                if job_results
                for result in job_results
            ]

        worker_count = max(1, min(self.settings.max_workers, len(active_jobs)))
        print(
            f"[PARALLEL] Starting {len(active_jobs)} enabled jukebox job(s) "
            f"with {worker_count} worker(s); each uses a random "
            f"{self.settings.min_delay_seconds}-{self.settings.max_delay_seconds}s delay"
        )

        executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="yt-jukebox")
        futures = {
            executor.submit(
                self._run_single_jukebox_job,
                job,
                target_root,
                keep_temp=keep_temp,
                overwrite=overwrite,
            ): index
            for index, job in active_jobs
        }
        try:
            for future in as_completed(futures):
                index = futures[future]
                ordered_results[index] = future.result()
        except (KeyboardInterrupt, UserCancelledError) as exc:
            self._cancel_futures_and_wait(executor, list(futures))
            raise UserCancelledError("Jukebox extraction cancelled by user") from exc
        else:
            executor.shutdown(wait=True, cancel_futures=False)

        return [result for job_results in ordered_results if job_results for result in job_results]


    def _cancel_futures_and_wait(self, executor: ThreadPoolExecutor, futures: list) -> None:
        """Cancel queued jukebox jobs and wait for running jobs to exit."""

        self.cancel()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)

    def _run_single_jukebox_job(
        self,
        job: JukeboxSplitJob,
        target_root: Path,
        *,
        keep_temp: bool,
        overwrite: bool,
    ) -> list[DownloadResult]:
        """Run one jukebox job and convert per-jukebox failures to result rows."""

        try:
            self.cancellation_token.raise_if_cancelled()
            if not job.download:
                result = DownloadResult(
                    song=job.json_key,
                    status=DownloadStatus.SKIPPED,
                    file_name=safe_filename(job.json_key, fallback="Jukebox"),
                    reason="download=false at jukebox level; skipped complete jukebox without downloading source audio",
                )
                print(self._format_result(result))
                return [result]

            return self._split_jukebox_job(
                job,
                target_root,
                keep_temp=keep_temp,
                overwrite=overwrite,
            )
        except UserCancelledError:
            raise
        except Exception as exc:  # Keep later JSON entries running even if one jukebox fails.
            result = DownloadResult(
                song=job.json_key,
                status=DownloadStatus.FAILED,
                file_name=safe_filename(job.json_key, fallback="Jukebox"),
                reason=str(exc),
            )
            print(self._format_result(result))
            return [result]

    def _split_jukebox_job(
        self,
        job: JukeboxSplitJob,
        target_root: Path,
        *,
        keep_temp: bool,
        overwrite: bool,
    ) -> list[DownloadResult]:
        """Download, split and tag one jukebox video."""

        self._require_external_binary("ffmpeg")
        self._require_external_binary("ffprobe")

        jukebox_dir_name = safe_filename(job.json_key, fallback="Jukebox")
        jukebox_dir = ensure_directory(target_root / jukebox_dir_name)
        temp_context = tempfile.TemporaryDirectory(prefix="jukebox_split_", dir=str(target_root))
        temp_dir = Path(temp_context.name)

        try:
            self._wait_before_download(job.json_key)
            print(f"[JUKEBOX] {job.json_key}: downloading best source audio")
            source_audio_path, info = self._download_source_audio(job, temp_dir)  # type: ignore[arg-type]
            duration = self._read_audio_duration_seconds(source_audio_path)
            album_art = job.album_art or self._thumbnail_from_info(info)

            print(
                f"[JUKEBOX] {job.json_key}: using {len(job.tracks)} manual song timing(s) "
                "from JSON"
            )
            segments = self._build_track_segments_from_manual_tracks(
                tracks=job.tracks,
                duration=duration,
                fallback_artists=[_UNKNOWN_ARTIST],
            )
            if not segments:
                raise ValueError("No valid jukebox track segments were created. Check start/end values.")

            results = self._export_jukebox_segments(
                job=job,
                source_audio_path=source_audio_path,
                jukebox_dir=jukebox_dir,
                segments=segments,
                album_art=album_art,
                overwrite=overwrite,
            )

            if keep_temp:
                kept_dir = jukebox_dir / "_source_audio"
                if kept_dir.exists():
                    shutil.rmtree(kept_dir)
                shutil.move(str(temp_dir), str(kept_dir))
                print(f"[JUKEBOX] {job.json_key}: kept temporary source files at {kept_dir}")

            return results
        finally:
            if not keep_temp:
                temp_context.cleanup()

    def _export_jukebox_segments(
        self,
        *,
        job: JukeboxSplitJob,
        source_audio_path: Path,
        jukebox_dir: Path,
        segments: list[TrackSegment],
        album_art: str,
        overwrite: bool,
    ) -> list[DownloadResult]:
        """Export jukebox song segments as MP3 files and tag them."""

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

            track_album = self._normalize_jukebox_metadata_text(segment.album, fallback=_UNKNOWN_ARTIST)
            artist_names = self._normalize_jukebox_artists(segment.artists)
            artist_text = ", ".join(artist_names) or _UNKNOWN_ARTIST
            structured_stem = safe_filename(
                f"{segment.title} - {track_album} - {artist_text}",
                fallback=f"Track {segment.number:02d}",
                invalid_char_replacement="",
            )
            output_path = jukebox_dir / f"{structured_stem}.mp3"
            track_label = f"Track {segment.number:02d}" if job.track_numbering else segment.title

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
                album=track_album,
                artists=artist_names,
            )
            track_number = segment.number if job.track_numbering else None
            track_total = downloadable_total if job.track_numbering else None
            track_album_art = (segment.album_art or "").strip() or album_art
            track_release_year = (segment.release_year or "").strip() or job.release_year
            song = Song(
                json_key=f"{job.json_key} / {track_label}",
                ytb_link=job.ytb_link,
                file_name=output_path.stem,
                parsed_metadata=parsed_metadata,
                album_art=track_album_art,
                release_year=track_release_year,
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
                    f"end={self._format_timestamp(segment.end)}, "
                    f"duration={self._format_seconds(segment.duration)}s"
                ),
            )
            results.append(result)
            print(self._format_result(result))

        return results

    @staticmethod
    def _load_jukebox_jobs(json_file: str | Path) -> tuple[list[JukeboxSplitJob], Path]:
        """Load jukebox jobs from a JSON/JSONC file."""

        input_text = str(json_file).strip()
        if input_text.lower().startswith(_URL_PREFIXES):
            raise ValueError("yt-jukebox-splitter needs a JSON file because song start/end timings are required.")

        json_path = Path(input_text).expanduser().resolve()
        if not json_path.exists():
            raise FileNotFoundError(f"Jukebox JSON file not found: {json_path}")

        raw_data = YouTubeAlbumSplitter._read_json_or_jsonc(json_path)
        if not isinstance(raw_data, dict):
            raise ValueError("Jukebox splitter input JSON must be an object/dictionary.")

        jobs: list[JukeboxSplitJob] = []
        errors: list[str] = []

        for json_key, metadata in raw_data.items():
            if not isinstance(metadata, dict):
                errors.append(f"{json_key!r}: metadata must be a JSON object")
                continue

            key_text = str(json_key).strip()
            ytb_link = ""
            for field in _URL_FIELDS:
                ytb_link = str(metadata.get(field) or "").strip()
                if ytb_link:
                    break
            if not ytb_link:
                errors.append(f"{key_text!r}: missing ytb_link/video_url/youtube_url/url")
                continue

            track_numbering = YouTubeAlbumSplitter._parse_album_bool_flag(
                metadata.get("track_numbering", True),
                field_name="track_numbering",
                album_key=key_text,
                errors=errors,
            )
            if track_numbering is None:
                continue

            jukebox_download = YouTubeAlbumSplitter._parse_album_bool_flag(
                metadata.get("download", True),
                field_name="download",
                album_key=key_text,
                errors=errors,
            )
            if jukebox_download is None:
                continue

            if jukebox_download:
                tracks = YouTubeJukeboxSplitter._parse_jukebox_track_specs(metadata, key_text, errors)
                if not tracks:
                    errors.append(f"{key_text!r}: at least one track with start/end timing is required")
                    continue
            else:
                # A disabled jukebox is intentionally retained in the JSON as a bookmark.
                # Do not validate track timings because the source is not downloaded or split.
                tracks = []

            jobs.append(
                JukeboxSplitJob(
                    json_key=key_text,
                    ytb_link=ytb_link,
                    tracks=tracks,
                    album_art=str(metadata.get("album_art") or "").strip(),
                    release_year=str(metadata.get("release_year") or "").strip(),
                    track_numbering=track_numbering,
                    download=jukebox_download,
                )
            )

        if errors:
            formatted = "\n".join(f"- {error}" for error in errors)
            raise ValueError(f"Invalid jukebox split metadata found:\n{formatted}")

        return jobs, json_path.parent

    @staticmethod
    def _parse_jukebox_track_specs(
        metadata: dict[str, Any],
        jukebox_key: str,
        errors: list[str],
    ) -> list[AlbumTrackSpec]:
        """Parse jukebox tracks with start and optional end/stop values."""

        raw_tracks = metadata.get("tracks")
        if raw_tracks is None:
            return []
        if not isinstance(raw_tracks, list):
            errors.append(f"{jukebox_key!r}: tracks must be a list")
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
                        f"{jukebox_key!r}: track #{index} must be either "
                        '{"Track title": {...}} or {"title": ..., "start": ...}'
                    )
                    continue
            else:
                errors.append(f"{jukebox_key!r}: track #{index} must be a JSON object")
                continue

            if not title:
                errors.append(f"{jukebox_key!r}: track #{index} has an empty title")
                continue
            if not isinstance(payload, dict):
                errors.append(f"{jukebox_key!r}: track {title!r} metadata must be an object")
                continue
            if "start" not in payload:
                errors.append(f"{jukebox_key!r}: track {title!r} is missing required start")
                continue

            start_seconds = YouTubeAlbumSplitter._parse_time_value(
                payload.get("start"),
                field_name="start",
                album_key=jukebox_key,
                track_title=title,
                errors=errors,
            )

            stop_value = None
            if "end" in payload:
                stop_value = payload.get("end")
            elif "stop" in payload:
                stop_value = payload.get("stop")

            stop_seconds = None
            if str(stop_value or "").strip():
                stop_seconds = YouTubeAlbumSplitter._parse_time_value(
                    stop_value,
                    field_name="end",
                    album_key=jukebox_key,
                    track_title=title,
                    errors=errors,
                )

            if start_seconds is None:
                continue
            if stop_seconds is not None and stop_seconds <= start_seconds:
                errors.append(
                    f"{jukebox_key!r}: track {title!r} end must be after start "
                    f"({payload.get('start')!r} -> {stop_value!r})"
                )
                continue

            track_album = YouTubeJukeboxSplitter._normalize_jukebox_metadata_text(
                payload.get("album"),
                fallback=_UNKNOWN_ARTIST,
            )
            track_album_art = str(payload.get("album_art") or "").strip()
            track_release_year = str(payload.get("release_year") or "").strip()
            artist_value = payload.get("artists", payload.get("artist"))
            track_artists = YouTubeJukeboxSplitter._normalize_jukebox_artists(
                YouTubeAlbumSplitter._parse_artists(artist_value)
            )
            download = YouTubeAlbumSplitter._parse_download_flag(
                payload.get("download", True),
                album_key=jukebox_key,
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
                    album=track_album,
                    album_art=track_album_art,
                    release_year=track_release_year,
                )
            )

        if parsed:
            previous_start = -1.0
            for track in sorted(parsed, key=lambda item: item.number):
                if track.start_seconds < previous_start:
                    errors.append(
                        f"{jukebox_key!r}: track {track.title!r} starts before the previous track. "
                        "Keep tracks in timeline order."
                    )
                    break
                previous_start = track.start_seconds

        return parsed

    def _wait_before_download(self, jukebox_name: str) -> None:
        """Add a conservative random delay before each full jukebox source download."""

        delay = random.randint(self.settings.min_delay_seconds, self.settings.max_delay_seconds)
        print(f"[WAIT] {jukebox_name}: waiting {delay}s before source audio download")
        self.cancellation_token.wait(delay)

    @staticmethod
    def _normalize_jukebox_metadata_text(value: Any, *, fallback: str = _UNKNOWN_ARTIST) -> str:
        """Return a clean jukebox metadata field with an Unknown fallback.

        Jukebox videos are compilations, so album and artists may differ per
        song. Blank/missing values are intentionally written as ``Unknown`` so
        filenames and ID3 metadata always remain structured as
        ``<track> - <album> - <artists>.mp3``.
        """

        text = str(value or "").strip()
        return text or fallback

    @staticmethod
    def _normalize_jukebox_artists(value: Any) -> list[str]:
        """Return one or more artist names with Unknown fallback."""

        if isinstance(value, list):
            raw_text = ", ".join(
                str(item).strip() for item in value if str(item).strip()
            )
        else:
            raw_text = str(value or "").strip()
        formatted = format_artist_names(raw_text)
        artists = [item.strip() for item in formatted.split(",") if item.strip()]
        return artists or [_UNKNOWN_ARTIST]

    @staticmethod
    def _thumbnail_from_info(info: dict[str, Any]) -> str:
        """Return the best available YouTube thumbnail URL from yt-dlp metadata."""

        thumbnails = info.get("thumbnails")
        if isinstance(thumbnails, list):
            valid = [item for item in thumbnails if isinstance(item, dict) and item.get("url")]
            if valid:
                valid.sort(key=lambda item: (item.get("width") or 0) * (item.get("height") or 0))
                return str(valid[-1].get("url") or "").strip()

        return str(info.get("thumbnail") or "").strip()
