"""YouTube video downloader with selectable resolution and optional MP3 support."""

from __future__ import annotations

import json
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from youtube_audio_video_downloader.services.downloads.audio_downloader import YouTubeAudioDownloader
from youtube_audio_video_downloader.services.downloads.download_range import (
    build_download_range_options,
)
from youtube_audio_video_downloader.core.exceptions import UserCancelledError
from youtube_audio_video_downloader.loaders.json_loader import load_videos
from youtube_audio_video_downloader.domain.models import (
    AudioQuality,
    DownloadResult,
    DownloadStatus,
    MediaSelection,
    MediaSelectionKind,
    ParsedSongMetadata,
    Song,
    VideoJob,
    VideoQuality,
)
from youtube_audio_video_downloader.config.settings import DownloadSettings
from youtube_audio_video_downloader.core.file_utils import ensure_directory, safe_filename
from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.core.file_access import retry_file_operation


_RESOLUTION_HEIGHTS: dict[str, int] = {
    "8k": 4320,
    "4320p": 4320,
    "uhd8k": 4320,
    "4k": 2160,
    "2160p": 2160,
    "uhd": 2160,
    "2k": 1440,
    "1440p": 1440,
    "qhd": 1440,
    "fhd": 1080,
    "fullhd": 1080,
    "full_hd": 1080,
    "1080p": 1080,
    "hd": 720,
    "720p": 720,
    "sd": 480,
    "480p": 480,
    "360p": 360,
    "240p": 240,
    "144p": 144,
}

_BEST_ALIASES = {"", "best", "highest", "max", "maximum", "auto"}
_ASK_ALIASES = {"ask", "prompt", "interactive", "choose", "manual"}
_MP3_ALIASES = {"mp3", "audio", "audio_only", "audio-only", "bestaudio", "best_audio", "music"}
_ALLOWED_MP3_MODES = {"ask", "audio-only", "both"}


@dataclass(frozen=True, slots=True)
class _VideoDownloadPlan:
    """Prepared, prompt-safe video/audio action ready for worker execution."""

    video: VideoJob
    info: dict[str, Any]
    file_name: str
    selection: MediaSelection


class YouTubeVideoDownloader:
    """Download YouTube videos from JSON with JSON/CLI/default resolution selection.

    The video command now supports three media choices for one JSON entry:

    * video only: normal selected-resolution download
    * audio only: best MP3 using the same audio logic as ``yt-audio-downloader``
    * both: selected-resolution video plus best MP3
    """

    def __init__(
        self,
        settings: DownloadSettings | None = None,
        cancellation_token: CancellationToken | None = None,
        interactive_prompts: bool = True,
    ) -> None:
        self.settings = settings or DownloadSettings()
        self.cancellation_token = cancellation_token or CancellationToken()
        self.interactive_prompts = interactive_prompts
        self.audio_downloader = YouTubeAudioDownloader(
            settings=self.settings,
            cancellation_token=self.cancellation_token,
        )

    def cancel(self) -> None:
        """Request cooperative cancellation for video and optional MP3 workers."""

        self.cancellation_token.cancel()
        self.audio_downloader.cancel()

    def scan_available_qualities(self, url: str) -> dict[str, Any]:
        """Return title and selectable qualities without downloading media."""
        info = self._extract_video_info(url)
        qualities = self._build_quality_options(info)
        audio = self._build_audio_quality(info)
        return {
            "title": str(info.get("title") or "").strip(),
            "qualities": [quality.label for quality in qualities],
            "mp3_available": audio is not None,
        }

    def download_from_json(
        self,
        json_path: Path,
        *,
        cli_resolution: str | None = None,
        output_dir: Path | None = None,
        audio_output_dir: Path | None = None,
        info_mode: bool = False,
        mp3_mode: str = "ask",
        write_report: bool = True,
    ) -> list[DownloadResult]:
        """Read video jobs from JSON and process each selected media option.

        Resolution priority:
            1. per-video ``resolution`` from JSON
            2. ``cli_resolution`` from CLI
            3. ``settings.default_video_resolution``
            4. highest available quality

        ``--info`` mode is a dry run. It prints available video resolutions,
        estimated sizes, the optional MP3 choice, and the selected/would-select
        action without downloading or creating output folders unless a report is
        explicitly requested.

        Real downloads use a two-phase pipeline:
            1. prepare/select every video first, including all interactive prompts
            2. execute prepared downloads in parallel using ``--workers``

        This prevents prompt text from multiple worker threads getting mixed while
        still allowing the actual video/audio downloads to run concurrently.
        """

        self.cancellation_token.reset()
        json_path = json_path.resolve()
        video_dir = output_dir.resolve() if output_dir else json_path.parent / "videos"
        audio_dir = audio_output_dir.resolve() if audio_output_dir else json_path.parent / "songs"
        mp3_mode = self._normalize_mp3_mode(mp3_mode)

        # Dry-run should not create folders unless the caller explicitly asks for a report.
        if not info_mode or write_report:
            video_dir = ensure_directory(video_dir)
            audio_dir = ensure_directory(audio_dir)

        videos = load_videos(json_path)
        if not videos:
            raise ValueError("No valid videos found in the input JSON file.")

        if info_mode:
            results: list[DownloadResult] = []
            for video in videos:
                self.cancellation_token.raise_if_cancelled()
                result = self._process_video(
                    video,
                    video_dir,
                    audio_dir,
                    cli_resolution=cli_resolution,
                    info_mode=True,
                    mp3_mode=mp3_mode,
                )
                results.append(result)
                print(self._format_result(result))

            if write_report:
                self._write_results(video_dir / "video_download_results.json", results)
            return results

        results: list[DownloadResult] = []
        plans: list[_VideoDownloadPlan] = []

        # Phase 1: extract metadata and resolve every interactive choice on the main thread.
        for video in videos:
            self.cancellation_token.raise_if_cancelled()
            prepared = self._prepare_video_plan(
                video,
                cli_resolution=cli_resolution,
                mp3_mode=mp3_mode,
            )
            if isinstance(prepared, DownloadResult):
                results.append(prepared)
                print(self._format_result(prepared))
            else:
                plans.append(prepared)
                print(self._format_plan(prepared))

        # Phase 2: execute the already selected jobs concurrently.
        if plans:
            results.extend(self._execute_plans_parallel(plans, video_dir, audio_dir))

        if write_report:
            self._write_results(video_dir / "video_download_results.json", results)

        return results

    def _prepare_video_plan(
        self,
        video: VideoJob,
        *,
        cli_resolution: str | None,
        mp3_mode: str,
    ) -> _VideoDownloadPlan | DownloadResult:
        """Build a prompt-safe execution plan for one video job.

        This method intentionally does not download anything. It may prompt the
        user when ``ask`` is selected, but all prompts happen before worker
        threads are started.
        """

        try:
            info = self._extract_video_info(video.ytb_link)
            file_name = self._resolve_video_file_name(video, info)
            qualities = self._build_quality_options(info)
            audio_quality = self._build_audio_quality(info)

            if not qualities and audio_quality is None:
                return DownloadResult(
                    song=video.json_key,
                    status=DownloadStatus.FAILED,
                    file_name=file_name,
                    reason="No downloadable video or audio formats found",
                )

            selection = self._choose_media_selection(
                video=video,
                qualities=qualities,
                audio_quality=audio_quality,
                cli_resolution=cli_resolution,
                mp3_mode=mp3_mode,
            )
            return _VideoDownloadPlan(
                video=video,
                info=info,
                file_name=file_name,
                selection=selection,
            )
        except UserCancelledError:
            raise
        except Exception as exc:  # yt-dlp and filesystem errors vary by source/OS.
            fallback_name = safe_filename(video.file_name or video.json_key, fallback=video.json_key)
            return DownloadResult(
                song=video.json_key,
                status=DownloadStatus.FAILED,
                file_name=fallback_name,
                reason=str(exc),
            )

    def _execute_plans_parallel(
        self,
        plans: list[_VideoDownloadPlan],
        video_dir: Path,
        audio_dir: Path,
    ) -> list[DownloadResult]:
        """Execute prepared plans with the configured worker count."""

        worker_count = max(1, min(self.settings.max_workers, len(plans)))
        print(
            f"[PARALLEL] Starting {len(plans)} prepared video/audio job(s) "
            f"with {worker_count} worker(s); each network download uses a random "
            f"{self.settings.min_delay_seconds}-{self.settings.max_delay_seconds}s delay"
        )
        if worker_count == 1:
            results: list[DownloadResult] = []
            for plan in plans:
                result = self._execute_prepared_plan(plan, video_dir, audio_dir)
                results.append(result)
                print(self._format_result(result))
            return results

        ordered_results: list[DownloadResult | None] = [None] * len(plans)
        executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="yt-video")
        futures = {
            executor.submit(self._execute_prepared_plan, plan, video_dir, audio_dir): index
            for index, plan in enumerate(plans)
        }
        try:
            for future in as_completed(futures):
                index = futures[future]
                result = future.result()
                ordered_results[index] = result
                print(self._format_result(result))
        except (KeyboardInterrupt, UserCancelledError) as exc:
            self._cancel_futures_and_wait(executor, list(futures))
            raise UserCancelledError("Download cancelled by user") from exc
        else:
            executor.shutdown(wait=True, cancel_futures=False)

        return [result for result in ordered_results if result is not None]


    def _cancel_futures_and_wait(self, executor: ThreadPoolExecutor, futures: list) -> None:
        """Cancel queued work and wait until running workers leave cleanly."""

        self.cancel()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)

    def _execute_prepared_plan(
        self,
        plan: _VideoDownloadPlan,
        video_dir: Path,
        audio_dir: Path,
    ) -> DownloadResult:
        """Execute one prepared plan and convert runtime errors into a result row."""

        try:
            self.cancellation_token.raise_if_cancelled()
            return self._execute_selection(
                video=plan.video,
                info=plan.info,
                video_dir=video_dir,
                audio_dir=audio_dir,
                file_name=plan.file_name,
                selection=plan.selection,
            )
        except UserCancelledError:
            raise
        except Exception as exc:  # Per-video failures should not stop the whole batch.
            return DownloadResult(
                song=plan.video.json_key,
                status=DownloadStatus.FAILED,
                file_name=plan.file_name,
                reason=str(exc),
            )

    def _process_video(
        self,
        video: VideoJob,
        video_dir: Path,
        audio_dir: Path,
        *,
        cli_resolution: str | None,
        info_mode: bool,
        mp3_mode: str,
    ) -> DownloadResult:
        """Extract metadata, choose media, and optionally download one entry."""

        try:
            info = self._extract_video_info(video.ytb_link)
            file_name = self._resolve_video_file_name(video, info)
            qualities = self._build_quality_options(info)
            audio_quality = self._build_audio_quality(info)

            if not qualities and audio_quality is None:
                return DownloadResult(
                    song=video.json_key,
                    status=DownloadStatus.FAILED,
                    file_name=file_name,
                    reason="No downloadable video or audio formats found",
                )

            requested_resolution = self._requested_resolution(video, cli_resolution)

            if info_mode:
                self._print_quality_table(
                    video,
                    qualities,
                    audio_quality,
                    requested_resolution=requested_resolution,
                )
                reason = self._build_info_reason(
                    video=video,
                    qualities=qualities,
                    audio_quality=audio_quality,
                    requested_resolution=requested_resolution,
                    mp3_mode=mp3_mode,
                )
                return DownloadResult(
                    song=video.json_key,
                    status=DownloadStatus.LISTED,
                    file_name=file_name,
                    reason=reason,
                )

            selection = self._choose_media_selection(
                video=video,
                qualities=qualities,
                audio_quality=audio_quality,
                cli_resolution=cli_resolution,
                mp3_mode=mp3_mode,
            )

            return self._execute_selection(
                video=video,
                info=info,
                video_dir=video_dir,
                audio_dir=audio_dir,
                file_name=file_name,
                selection=selection,
            )
        except UserCancelledError:
            raise
        except Exception as exc:  # yt-dlp and filesystem errors vary by source/OS.
            fallback_name = safe_filename(video.file_name or video.json_key, fallback=video.json_key)
            return DownloadResult(
                song=video.json_key,
                status=DownloadStatus.FAILED,
                file_name=fallback_name,
                reason=str(exc),
            )

    def _execute_selection(
        self,
        *,
        video: VideoJob,
        info: dict[str, Any],
        video_dir: Path,
        audio_dir: Path,
        file_name: str,
        selection: MediaSelection,
    ) -> DownloadResult:
        """Run the selected video/audio/both download operation."""

        downloaded_files: list[str] = []
        action_notes: list[str] = []
        operation_statuses: list[DownloadStatus] = []

        if selection.kind in {MediaSelectionKind.VIDEO, MediaSelectionKind.BOTH}:
            if selection.video_quality is None:
                raise ValueError("Video selection is missing the selected video quality")

            existing_video = self._find_existing_video(video_dir, file_name)
            if existing_video and self.settings.skip_existing:
                action_notes.append(f"video already exists: {existing_video.name}")
                downloaded_files.append(existing_video.name)
                operation_statuses.append(DownloadStatus.ALREADY_EXISTS)
            else:
                if not self.settings.skip_existing:
                    self._remove_existing_video_files(video_dir, file_name, video.json_key)

                self._wait_before_download(video.json_key)
                self._download_selected_quality(video, video_dir, file_name, selection.video_quality)
                final_video_name = f"{file_name}.{self.settings.video_merge_output_format}"
                downloaded_files.append(final_video_name)
                action_notes.append(
                    f"video={selection.video_quality.label}, "
                    f"estimated_size={self._format_size(selection.video_quality.estimated_size_bytes)}"
                )
                operation_statuses.append(DownloadStatus.DOWNLOADED)

        if selection.kind in {MediaSelectionKind.AUDIO, MediaSelectionKind.BOTH}:
            audio_result = self._download_best_mp3_audio(video, info, audio_dir, file_name)
            if audio_result.file_name:
                downloaded_files.append(audio_result.file_name)
            operation_statuses.append(audio_result.status)
            action_notes.append(f"mp3={audio_result.status.value}: {audio_result.reason or 'ok'}")

        if not downloaded_files:
            return DownloadResult(
                song=video.json_key,
                status=DownloadStatus.SKIPPED,
                file_name=file_name,
                reason="Nothing was downloaded",
            )

        successful_statuses = {DownloadStatus.DOWNLOADED, DownloadStatus.ALREADY_EXISTS, DownloadStatus.TAGGED}
        if operation_statuses and all(status == DownloadStatus.FAILED for status in operation_statuses):
            final_status = DownloadStatus.FAILED
        elif operation_statuses and all(status == DownloadStatus.ALREADY_EXISTS for status in operation_statuses):
            final_status = DownloadStatus.ALREADY_EXISTS
        elif any(status in successful_statuses for status in operation_statuses):
            final_status = DownloadStatus.DOWNLOADED
        else:
            final_status = DownloadStatus.SKIPPED

        return DownloadResult(
            song=video.json_key,
            status=final_status,
            file_name=" + ".join(downloaded_files),
            reason="; ".join(action_notes),
        )

    @staticmethod
    def _resolve_video_file_name(video: VideoJob, info: dict[str, Any]) -> str:
        """Return a Windows-safe output name from JSON or YouTube metadata.

        Priority:
            1. JSON ``file_name`` / ``title`` value loaded into ``video.file_name``
            2. YouTube metadata title from yt-dlp
            3. JSON object key as a final fallback
        """

        raw_file_name = str(video.file_name or "").strip()
        if raw_file_name:
            return safe_filename(raw_file_name, fallback=video.json_key)

        youtube_title = str(
            info.get("title")
            or info.get("fulltitle")
            or info.get("alt_title")
            or ""
        ).strip()

        resolved_name = safe_filename(youtube_title, fallback=video.json_key)
        if youtube_title:
            print(f"[TITLE] {video.json_key}: using YouTube title as file name -> {resolved_name}")
        else:
            print(f"[TITLE] {video.json_key}: YouTube title unavailable; using JSON key -> {resolved_name}")
        return resolved_name

    @staticmethod
    def _extract_video_info(url: str) -> dict[str, Any]:
        """Fetch video metadata and format list without downloading the file."""

        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "ignoreerrors": False,
        }

        import yt_dlp

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

        if not isinstance(info, dict):
            raise ValueError("yt-dlp did not return video metadata")
        return info

    def _build_quality_options(self, info: dict[str, Any]) -> list[VideoQuality]:
        """Build one best video candidate per available video height."""

        formats = info.get("formats") or []
        if not isinstance(formats, list):
            return []

        best_audio = self._pick_best_audio_format(formats)
        best_by_height: dict[int, dict[str, Any]] = {}

        for fmt in formats:
            if not isinstance(fmt, dict):
                continue
            if fmt.get("vcodec") in {None, "none"}:
                continue

            height = self._safe_int(fmt.get("height"))
            if height is None or height <= 0:
                continue

            current = best_by_height.get(height)
            if current is None or self._video_score(fmt) > self._video_score(current):
                best_by_height[height] = fmt

        qualities: list[VideoQuality] = []
        for height, video_fmt in sorted(best_by_height.items(), reverse=True):
            has_own_audio = video_fmt.get("acodec") not in {None, "none"}
            audio_fmt = None if has_own_audio else best_audio

            video_size = self._get_format_size(video_fmt)
            audio_size = self._get_format_size(audio_fmt) if audio_fmt else 0
            estimated_size = None
            if video_size is not None:
                estimated_size = video_size + (audio_size or 0)

            qualities.append(
                VideoQuality(
                    label=f"{height}p",
                    height=height,
                    width=self._safe_int(video_fmt.get("width")),
                    fps=self._safe_float(video_fmt.get("fps")),
                    video_format_id=str(video_fmt.get("format_id") or "best"),
                    audio_format_id=(
                        str(audio_fmt.get("format_id"))
                        if isinstance(audio_fmt, dict) and audio_fmt.get("format_id")
                        else None
                    ),
                    video_ext=str(video_fmt.get("ext") or "unknown"),
                    audio_ext=(
                        str(audio_fmt.get("ext"))
                        if isinstance(audio_fmt, dict) and audio_fmt.get("ext")
                        else None
                    ),
                    estimated_size_bytes=estimated_size,
                    note="video+audio" if audio_fmt else "progressive/has audio",
                )
            )

        return qualities

    def _build_audio_quality(self, info: dict[str, Any]) -> AudioQuality | None:
        """Return the best audio-only stream represented as final MP3 output."""

        formats = info.get("formats") or []
        if not isinstance(formats, list):
            return None

        best_audio = self._pick_best_audio_format(formats)
        if not best_audio:
            return None

        return AudioQuality(
            label="MP3",
            format_id=str(best_audio.get("format_id") or "bestaudio"),
            source_ext=str(best_audio.get("ext") or "audio"),
            estimated_size_bytes=self._get_format_size(best_audio),
            abr=self._safe_float(best_audio.get("abr") or best_audio.get("tbr")),
        )

    @staticmethod
    def _pick_best_audio_format(formats: list[Any]) -> dict[str, Any] | None:
        """Return the best audio-only format for merging/conversion."""

        audio_formats = [
            fmt
            for fmt in formats
            if isinstance(fmt, dict)
            and fmt.get("acodec") not in {None, "none"}
            and fmt.get("vcodec") in {None, "none"}
        ]
        if not audio_formats:
            return None

        return max(
            audio_formats,
            key=lambda fmt: (
                YouTubeVideoDownloader._safe_float(fmt.get("abr")) or 0.0,
                YouTubeVideoDownloader._safe_float(fmt.get("tbr")) or 0.0,
                YouTubeVideoDownloader._get_format_size(fmt) or 0,
            ),
        )

    @staticmethod
    def _video_score(fmt: dict[str, Any]) -> tuple[float, float, int]:
        """Score a video format within the same resolution."""

        return (
            YouTubeVideoDownloader._safe_float(fmt.get("fps")) or 0.0,
            YouTubeVideoDownloader._safe_float(fmt.get("tbr")) or 0.0,
            YouTubeVideoDownloader._get_format_size(fmt) or 0,
        )

    @staticmethod
    def _get_format_size(fmt: dict[str, Any] | None) -> int | None:
        """Return exact or approximate format size in bytes when available."""

        if not isinstance(fmt, dict):
            return None
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        try:
            return int(size) if size is not None else None
        except (TypeError, ValueError):
            return None

    def _choose_media_selection(
        self,
        *,
        video: VideoJob,
        qualities: list[VideoQuality],
        audio_quality: AudioQuality | None,
        cli_resolution: str | None,
        mp3_mode: str,
    ) -> MediaSelection:
        """Choose video/audio/both using JSON > CLI > settings > highest precedence."""

        requested_resolution = self._requested_resolution(video, cli_resolution)
        normalized = self._normalize_resolution(requested_resolution)

        should_ask = normalized == "ask"
        if should_ask and not self.interactive_prompts:
            fallback_resolution = cli_resolution or self.settings.default_video_resolution or "best"
            fallback_normalized = self._normalize_resolution(fallback_resolution)
            if fallback_normalized == "ask":
                fallback_resolution = "best"
                fallback_normalized = "best"
            print(
                f"[QUALITY] {video.json_key}: interactive ask replaced by GUI selection "
                f"{fallback_resolution}"
            )
            requested_resolution = fallback_resolution
            normalized = fallback_normalized
            should_ask = False

        if should_ask:
            self._print_quality_table(video, qualities, audio_quality, requested_resolution=requested_resolution)
            return self._prompt_for_media_selection(video, qualities, audio_quality, mp3_mode)

        if normalized == "mp3":
            if audio_quality is None:
                raise ValueError("MP3 was requested, but no audio-only stream is available")
            return self._selection_for_mp3_choice(video, qualities, audio_quality, mp3_mode)

        if not qualities:
            raise ValueError("No downloadable video qualities found")

        if normalized == "best":
            selected = qualities[0]
            print(f"[QUALITY] {video.json_key}: selected highest available {selected.label}")
            return MediaSelection(kind=MediaSelectionKind.VIDEO, video_quality=selected)

        target_height = self._height_for_resolution(normalized)
        selected = self._select_nearest_at_or_below(qualities, target_height)

        if selected.height != target_height:
            print(
                f"[QUALITY] {video.json_key}: requested {requested_resolution}, "
                f"selected nearest available {selected.label}"
            )
        else:
            print(f"[QUALITY] {video.json_key}: selected {selected.label}")

        return MediaSelection(kind=MediaSelectionKind.VIDEO, video_quality=selected)

    def _build_info_reason(
        self,
        *,
        video: VideoJob,
        qualities: list[VideoQuality],
        audio_quality: AudioQuality | None,
        requested_resolution: str,
        mp3_mode: str,
    ) -> str:
        """Return dry-run information without triggering interactive prompts."""

        normalized = self._normalize_resolution(requested_resolution)

        if normalized == "ask":
            return "Info mode; real download would ask for video quality or MP3 choice; no download started"

        if normalized == "mp3":
            if audio_quality is None:
                return "Info mode; MP3 requested but no audio-only stream was found; no download started"
            if mp3_mode == "ask":
                return (
                    "Info mode; MP3 requested; real download would ask whether to download "
                    "MP3 only or both video + MP3; no download started"
                )
            if mp3_mode == "both" and qualities:
                video_quality = qualities[0]
                return (
                    "Info mode; would download both "
                    f"video={video_quality.label} and MP3, "
                    f"mp3_source_size={self._format_size(audio_quality.estimated_size_bytes)}; "
                    "no download started"
                )
            return (
                "Info mode; would download MP3 audio only, "
                f"estimated_source_size={self._format_size(audio_quality.estimated_size_bytes)}; "
                "no download started"
            )

        if not qualities:
            return "Info mode; no video qualities found; no download started"

        if normalized == "best":
            selected = qualities[0]
        else:
            selected = self._select_nearest_at_or_below(
                qualities,
                self._height_for_resolution(normalized),
            )

        return (
            f"Info mode; selected={selected.label}, "
            f"estimated_size={self._format_size(selected.estimated_size_bytes)}, "
            "no download started"
        )

    def _selection_for_mp3_choice(
        self,
        video: VideoJob,
        qualities: list[VideoQuality],
        audio_quality: AudioQuality,
        mp3_mode: str,
    ) -> MediaSelection:
        """Resolve what to download after MP3 was selected/requested."""

        if mp3_mode == "audio-only":
            print(f"[QUALITY] {video.json_key}: selected MP3 audio only")
            return MediaSelection(kind=MediaSelectionKind.AUDIO, audio_quality=audio_quality)

        if mp3_mode == "both":
            if not qualities:
                raise ValueError("Cannot download both because no video quality is available")
            selected_video = qualities[0]
            print(
                f"[QUALITY] {video.json_key}: selected MP3 + highest available video "
                f"{selected_video.label}"
            )
            return MediaSelection(
                kind=MediaSelectionKind.BOTH,
                video_quality=selected_video,
                audio_quality=audio_quality,
            )

        return self._prompt_for_mp3_mode(video, qualities, audio_quality)

    def _prompt_for_media_selection(
        self,
        video: VideoJob,
        qualities: list[VideoQuality],
        audio_quality: AudioQuality | None,
        mp3_mode: str,
    ) -> MediaSelection:
        """Ask the user to select video quality or the MP3 option."""

        while True:
            answer = input(
                f"Choose for '{video.json_key}' by number/resolution, "
                "type mp3 for best MP3 audio, or press Enter for highest video: "
            ).strip()

            if not answer:
                if not qualities:
                    if audio_quality is None:
                        raise ValueError("No video or MP3 option is available")
                    return self._selection_for_mp3_choice(video, qualities, audio_quality, mp3_mode)
                return MediaSelection(kind=MediaSelectionKind.VIDEO, video_quality=qualities[0])

            lowered = answer.lower().strip()
            if lowered in {"c", "cancel", "q", "quit", "exit"}:
                raise UserCancelledError("Download cancelled by user")

            if answer.isdigit():
                index = int(answer) - 1
                if 0 <= index < len(qualities):
                    return MediaSelection(kind=MediaSelectionKind.VIDEO, video_quality=qualities[index])
                if audio_quality is not None and index == len(qualities):
                    return self._selection_for_mp3_choice(video, qualities, audio_quality, mp3_mode)

            try:
                normalized = self._normalize_resolution(answer)
            except ValueError:
                normalized = ""

            if normalized == "mp3" and audio_quality is not None:
                return self._selection_for_mp3_choice(video, qualities, audio_quality, mp3_mode)
            if normalized == "best" and qualities:
                return MediaSelection(kind=MediaSelectionKind.VIDEO, video_quality=qualities[0])
            if normalized and normalized not in {"ask", "mp3"} and qualities:
                return MediaSelection(
                    kind=MediaSelectionKind.VIDEO,
                    video_quality=self._select_nearest_at_or_below(
                        qualities,
                        self._height_for_resolution(normalized),
                    ),
                )

            valid_options = [quality.label for quality in qualities]
            if audio_quality is not None:
                valid_options.append("mp3")
            print(f"Invalid choice. Available: {', '.join(valid_options)}")

    def _prompt_for_mp3_mode(
        self,
        video: VideoJob,
        qualities: list[VideoQuality],
        audio_quality: AudioQuality,
    ) -> MediaSelection:
        """Ask whether MP3 means audio-only or video plus MP3."""

        while True:
            answer = input(
                f"MP3 selected for '{video.json_key}'. Download [1] MP3 only, "
                "[2] both video + MP3, or [c] cancel? Press Enter for MP3 only: "
            ).strip().lower()

            if answer in {"", "1", "audio", "audio-only", "mp3", "only"}:
                print(f"[QUALITY] {video.json_key}: selected MP3 audio only")
                return MediaSelection(kind=MediaSelectionKind.AUDIO, audio_quality=audio_quality)

            if answer in {"2", "both", "video+audio", "video", "all"}:
                if not qualities:
                    print("No video quality is available, so only MP3 audio can be downloaded.")
                    return MediaSelection(kind=MediaSelectionKind.AUDIO, audio_quality=audio_quality)
                selected_video = self._prompt_for_video_quality_for_both(video, qualities)
                print(
                    f"[QUALITY] {video.json_key}: selected {selected_video.label} video + MP3 audio"
                )
                return MediaSelection(
                    kind=MediaSelectionKind.BOTH,
                    video_quality=selected_video,
                    audio_quality=audio_quality,
                )

            if answer in {"c", "cancel", "q", "quit", "exit"}:
                raise UserCancelledError("Download cancelled by user")

            print("Invalid choice. Use 1 for MP3 only, 2 for both, or c to cancel.")

    def _prompt_for_video_quality_for_both(
        self,
        video: VideoJob,
        qualities: list[VideoQuality],
    ) -> VideoQuality:
        """Ask which video quality to use when both video and MP3 were requested."""

        while True:
            answer = input(
                "Choose video quality for the video copy by number/resolution, "
                "or press Enter for highest video: "
            ).strip()

            if not answer:
                return qualities[0]

            lowered = answer.lower().strip()
            if lowered in {"c", "cancel", "q", "quit", "exit"}:
                raise UserCancelledError("Download cancelled by user")

            if answer.isdigit():
                index = int(answer) - 1
                if 0 <= index < len(qualities):
                    return qualities[index]

            try:
                normalized = self._normalize_resolution(answer)
            except ValueError:
                normalized = ""

            if normalized == "best":
                return qualities[0]
            if normalized and normalized not in {"ask", "mp3"}:
                return self._select_nearest_at_or_below(
                    qualities,
                    self._height_for_resolution(normalized),
                )

            valid_options = ", ".join(quality.label for quality in qualities)
            print(f"Invalid video quality. Available: {valid_options}")

    def _download_best_mp3_audio(
        self,
        video: VideoJob,
        info: dict[str, Any],
        audio_dir: Path,
        file_name: str,
    ) -> DownloadResult:
        """Download MP3 through the existing audio downloader implementation."""

        metadata = self._build_audio_metadata(info, file_name)
        artist_text = ", ".join(metadata.artists)
        structured_file_name = safe_filename(
            f"{metadata.title} - {metadata.album} - {artist_text}",
            fallback=file_name,
        )
        song = Song(
            json_key=video.json_key,
            ytb_link=video.ytb_link,
            file_name=structured_file_name,
            parsed_metadata=metadata,
            album_art=str(info.get("thumbnail") or "").strip(),
            release_year=self._extract_release_year(info),
            start_timestamp=video.start_timestamp,
            end_timestamp=video.end_timestamp,
        )
        return self.audio_downloader.download_song_to_directory(song, audio_dir)

    @staticmethod
    def _build_audio_metadata(info: dict[str, Any], file_name: str) -> ParsedSongMetadata:
        """Build conservative MP3 metadata for video-selected audio downloads."""

        title = str(info.get("title") or file_name).strip() or file_name
        artist = str(
            info.get("artist")
            or info.get("uploader")
            or info.get("channel")
            or "YouTube"
        ).strip()
        return ParsedSongMetadata(
            title=title,
            album="YouTube",
            artists=[artist or "YouTube"],
        )

    @staticmethod
    def _extract_release_year(info: dict[str, Any]) -> str:
        """Return a 4-digit upload/release year when yt-dlp exposes one."""

        for key in ("release_year", "release_date", "upload_date"):
            value = str(info.get(key) or "").strip()
            if len(value) >= 4 and value[:4].isdigit():
                return value[:4]
        return ""

    def _requested_resolution(self, video: VideoJob, cli_resolution: str | None) -> str:
        """Return the effective requested resolution before format selection."""

        return (
            video.resolution
            or cli_resolution
            or self.settings.default_video_resolution
            or "best"
        )

    @staticmethod
    def _normalize_resolution(value: str | None) -> str:
        """Normalize user/JSON resolution values into a known key."""

        raw = str(value or "").strip().lower()
        normalized = raw.replace(" ", "").replace("-", "_")
        hyphen_normalized = raw.replace(" ", "").replace("_", "-")

        if normalized in _ASK_ALIASES:
            return "ask"
        if normalized in _BEST_ALIASES:
            return "best"
        if normalized in _RESOLUTION_HEIGHTS:
            return normalized
        if normalized in _MP3_ALIASES or hyphen_normalized in _MP3_ALIASES:
            return "mp3"
        raise ValueError(
            f"Unsupported resolution {value!r}. Use values like 8K, 4K, 2K, FHD, "
            "1080p, HD, 720p, 480p, 360p, best, ask or mp3."
        )

    @staticmethod
    def _normalize_mp3_mode(value: str) -> str:
        """Validate and normalize the MP3 handling mode."""

        normalized = str(value or "ask").strip().lower().replace("_", "-")
        if normalized not in _ALLOWED_MP3_MODES:
            raise ValueError("--mp3-mode must be one of: ask, audio-only, both")
        return normalized

    @staticmethod
    def _height_for_resolution(normalized_resolution: str) -> int:
        """Return pixel height for a normalized resolution key."""

        try:
            return _RESOLUTION_HEIGHTS[normalized_resolution]
        except KeyError as exc:
            raise ValueError(f"Resolution does not map to a height: {normalized_resolution}") from exc

    @staticmethod
    def _select_nearest_at_or_below(
        qualities: list[VideoQuality],
        target_height: int,
    ) -> VideoQuality:
        """Select best quality at or below requested height, falling back to lowest."""

        for quality in qualities:
            if quality.height <= target_height:
                return quality
        return qualities[-1]

    def _download_selected_quality(
        self,
        video: VideoJob,
        output_dir: Path,
        file_name: str,
        selected: VideoQuality,
    ) -> None:
        """Download one selected video quality using exact format IDs with retries."""

        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            try:
                import yt_dlp

                with yt_dlp.YoutubeDL(
                    self._build_video_yt_dlp_options(output_dir, file_name, selected, video)
                ) as ydl:
                    ydl.download([video.ytb_link])
                return
            except UserCancelledError:
                raise
            except Exception as exc:  # yt-dlp can raise several runtime-specific exceptions.
                last_error = exc
                if attempt >= self.settings.max_retries:
                    break
                wait_seconds = self._get_retry_wait_seconds(str(exc), attempt)
                print(
                    f"[RETRY] {video.json_key}: attempt {attempt}/{self.settings.max_retries} "
                    f"failed. Waiting {wait_seconds}s. Error: {exc}"
                )
                self.cancellation_token.wait(wait_seconds)

        raise RuntimeError(f"Video download failed after {self.settings.max_retries} attempt(s): {last_error}")

    def _build_video_yt_dlp_options(
        self,
        output_dir: Path,
        file_name: str,
        selected: VideoQuality,
        video: VideoJob,
    ) -> dict[str, Any]:
        """Return yt-dlp options for one video's independently selected range."""

        return {
            "format": selected.format_selector,
            "outtmpl": str(output_dir / f"{file_name}.%(ext)s"),
            "merge_output_format": self.settings.video_merge_output_format,
            "noplaylist": True,
            "continuedl": True,
            "ignoreerrors": False,
            "quiet": False,
            "no_warnings": False,
            "retries": self.settings.max_retries,
            "fragment_retries": self.settings.max_retries,
            **build_download_range_options(video.start_timestamp, video.end_timestamp),
        }

    @staticmethod
    def _print_quality_table(
        video: VideoJob,
        qualities: list[VideoQuality],
        audio_quality: AudioQuality | None,
        *,
        requested_resolution: str | None,
    ) -> None:
        """Print available video qualities and optional MP3 choice with sizes."""

        print("\nAvailable qualities")
        print(f"Video     : {video.json_key}")
        print(f"Requested : {requested_resolution or 'best'}")
        print("-" * 112)
        print(
            f"{'No.':<4} {'Type':<7} {'Quality':<9} {'FPS/ABR':<9} "
            f"{'Ext':<15} {'Estimated Size':<17} {'Format IDs'}"
        )
        print("-" * 112)

        for index, quality in enumerate(qualities, start=1):
            fps = f"{quality.fps:g}" if quality.fps else "-"
            ext = quality.video_ext
            if quality.audio_ext:
                ext = f"{quality.video_ext}+{quality.audio_ext}"
            size = YouTubeVideoDownloader._format_size(quality.estimated_size_bytes)
            format_ids = quality.video_format_id
            if quality.audio_format_id:
                format_ids = f"{quality.video_format_id}+{quality.audio_format_id}"
            print(
                f"{index:<4} {'Video':<7} {quality.label:<9} {fps:<9} "
                f"{ext:<15} {size:<17} {format_ids}"
            )

        if audio_quality is not None:
            index = len(qualities) + 1
            abr = f"{audio_quality.abr:g}k" if audio_quality.abr else "best"
            size = YouTubeVideoDownloader._format_size(audio_quality.estimated_size_bytes)
            print(
                f"{index:<4} {'Audio':<7} {'MP3':<9} {abr:<9} "
                f"{audio_quality.source_ext + '->mp3':<15} {size:<17} {audio_quality.format_id}"
            )

        print("-" * 112)

    @staticmethod
    def _find_existing_video(output_dir: Path, file_name: str) -> Path | None:
        """Find an existing final video file for a target stem."""

        ignored_suffixes = {".part", ".ytdl", ".temp", ".json"}
        for path in output_dir.glob(f"{file_name}.*"):
            if path.is_file() and path.suffix.lower() not in ignored_suffixes:
                return path
        return None

    @staticmethod
    def _remove_existing_video_files(output_dir: Path, file_name: str, video_title: str) -> None:
        """Delete previous output/temp files before an explicit re-download."""

        removed = 0
        for path in output_dir.glob(f"{file_name}.*"):
            if path.is_file():
                retry_file_operation(
                    path, "removing it before redownload", path.unlink
                )
                removed += 1
        if removed:
            print(f"[OVERWRITE] {video_title}: removed {removed} existing video/temp file(s)")

    def _wait_before_download(self, title: str) -> None:
        """Add a conservative random delay before each download attempt."""

        delay = random.randint(self.settings.min_delay_seconds, self.settings.max_delay_seconds)
        print(f"[WAIT] {title}: waiting {delay}s before download")
        self.cancellation_token.wait(delay)

    def _get_retry_wait_seconds(self, error_text: str, attempt: int) -> int:
        """Return retry wait duration based on the error text."""

        lowered = error_text.lower()
        is_rate_limited = "429" in lowered or "too many requests" in lowered or "bot" in lowered
        if is_rate_limited:
            return self.settings.rate_limit_wait_seconds * attempt
        return self.settings.retry_wait_seconds * attempt

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_size(size_bytes: int | None) -> str:
        """Format bytes as a readable size, or unknown when yt-dlp has no estimate."""

        if size_bytes is None or size_bytes <= 0:
            return "unknown"

        size = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024
        return f"{size_bytes} B"

    @staticmethod
    def _format_plan(plan: _VideoDownloadPlan) -> str:
        """Return a concise queued message for a prepared download plan."""

        selection = plan.selection
        if selection.kind == MediaSelectionKind.AUDIO:
            action = "mp3 audio only"
        elif selection.kind == MediaSelectionKind.BOTH:
            video_label = selection.video_quality.label if selection.video_quality else "video"
            action = f"{video_label} video + MP3 audio"
        else:
            video_label = selection.video_quality.label if selection.video_quality else "video"
            action = f"{video_label} video"

        return f"[QUEUED] {plan.video.json_key} | file={plan.file_name} | action={action}"

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
                "video": result.song,
                "status": result.status.value,
                "file_name": result.file_name,
                "reason": result.reason,
            }
            for result in results
        ]

        with result_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=4, ensure_ascii=False)
