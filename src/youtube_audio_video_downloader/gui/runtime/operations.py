"""Qt-independent execution layer used by the desktop worker thread."""

from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from youtube_audio_video_downloader.config.settings import (
    DownloadSettings,
    machine_parallel_workers,
)
from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.domain.models import DownloadResult
from youtube_audio_video_downloader.gui.runtime.ai_usage import operation_ai_usage
from youtube_audio_video_downloader.services.albums.album_splitter import YouTubeAlbumSplitter
from youtube_audio_video_downloader.services.albums.album_consolidator import consolidate_albums
from youtube_audio_video_downloader.services.albums.album_metadata_enricher import (
    enrich_folder_metadata,
    enrich_media_files,
)
from youtube_audio_video_downloader.services.albums.album_folders import (
    consolidate_audio_in_place,
    normalize_album_folders,
    resolve_album_folder_successor,
)
from youtube_audio_video_downloader.services.albums.album_editor import edit_album_folder
from youtube_audio_video_downloader.services.albums.album_names import split_album_folder_name
from youtube_audio_video_downloader.services.albums.album_consolidator import (
    _reorder_album_from_wikipedia,
)
from youtube_audio_video_downloader.services.downloads.audio_downloader import YouTubeAudioDownloader
from youtube_audio_video_downloader.services.media.audio_trimmer import trim_audio
from youtube_audio_video_downloader.services.albums.jukebox_splitter import YouTubeJukeboxSplitter
from youtube_audio_video_downloader.services.downloads.media_redownloader import redownload_media
from youtube_audio_video_downloader.services.media.media_editor import edit_media_file
from youtube_audio_video_downloader.services.media.media_metadata import read_media_metadata
from youtube_audio_video_downloader.services.metadata.metadata_tracker import (
    MetadataCompletionTracker,
    verification_policy_key,
)
from youtube_audio_video_downloader.services.ai.operation_agent import preflight_operation
from youtube_audio_video_downloader.services.downloads.song_search import search_song
from youtube_audio_video_downloader.services.metadata.song_selection_enricher import enrich_selected_song
from youtube_audio_video_downloader.services.albums.track_reorder import reorder_track_numbers
from youtube_audio_video_downloader.services.downloads.video_downloader import YouTubeVideoDownloader
from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names
from youtube_audio_video_downloader.utils.duplicate_links import find_duplicate_youtube_links
from youtube_audio_video_downloader.utils.track_timestamp_parser import parse_tracks_to_json


SUPPORTED_OPERATIONS = (
    "audio",
    "video",
    "album",
    "jukebox",
    "track_reorder",
    "audio_trimmer",
    "redownload",
    "edit_media",
    "edit_album",
    "album_consolidator",
    "album_metadata_enricher",
    "duplicate_links",
    "format_artists",
    "parse_tracks",
    "search_song",
    "enrich_song",
)


@dataclass(frozen=True, slots=True)
class OperationSummary:
    """Serializable summary returned to the UI after one operation."""

    operation: str
    total: int = 0
    downloaded: int = 0
    moved: int = 0
    deleted: int = 0
    reordered: int = 0
    tagged: int = 0
    skipped: int = 0
    tracked: int = 0
    listed: int = 0
    failed: int = 0
    output_text: str = ""
    output_path: str = ""
    completed_items: tuple[str, ...] = ()
    failed_items: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def execute_operation(
    operation: str,
    params: dict[str, Any],
    cancellation_token: CancellationToken,
) -> OperationSummary:
    """Execute one GUI operation through the existing service layer."""

    handlers = {
        "audio": _run_audio,
        "video": _run_video,
        "album": _run_album,
        "jukebox": _run_jukebox,
        "track_reorder": _run_track_reorder,
        "audio_trimmer": _run_audio_trimmer,
        "redownload": _run_redownload,
        "edit_media": _run_edit_media,
        "edit_album": _run_edit_album,
        "album_consolidator": _run_album_consolidator,
        "album_metadata_enricher": _run_album_metadata_enricher,
        "duplicate_links": _run_duplicate_links,
        "format_artists": _run_format_artists,
        "parse_tracks": _run_parse_tracks,
        "search_song": _run_search_song,
        "enrich_song": _run_enrich_song,
    }
    handler = handlers.get(operation)
    if handler is None:
        raise ValueError(f"Unsupported operation: {operation}")
    print(operation_ai_usage(operation, params).log_text)
    cancellation_token.raise_if_cancelled()
    # Advisory only: this layer audits intent and ambiguity but never rewrites
    # parameters. Metadata mutation remains guarded by metadata_verifier.
    if bool(params.get("ai_enabled", True)):
        preflight_operation(operation, params)
    else:
        print("[AI-DISABLED] Skipping model preflight; using internet/deterministic processing")
    cancellation_token.raise_if_cancelled()
    return handler(params, cancellation_token)


def _run_track_reorder(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    raw_paths = params.get("paths", [])
    if not isinstance(raw_paths, list):
        raise ValueError("Track paths must be a list.")
    paths = [Path(str(value)) for value in raw_paths]
    token.raise_if_cancelled()
    updated = reorder_track_numbers(
        paths, retries=int(params.get("retries", 3) or 3)
    )
    return OperationSummary(
        operation="track_reorder",
        total=len(paths),
        tagged=updated,
        skipped=len(paths) - updated,
        completed_items=(),
    )


def _run_album_consolidator(
    params: dict[str, Any], token: CancellationToken
) -> OperationSummary:
    source = resolve_album_folder_successor(
        str(params.get("source_folder", "") or "")
    )
    workers = int(
        params.get("workers", machine_parallel_workers()) or machine_parallel_workers()
    )
    retries = int(params.get("retries", 3) or 3)
    agentic_model = str(params.get("agentic_model", "") or "").strip()
    perform_enrichment = bool(params.get("perform_enrichment", True))
    pre_move_enrichment = None
    verified_audio_paths = None
    if agentic_model and perform_enrichment:
        print("[AGENT-PRE-MOVE] Verifying audio identity before folder routing")
        pre_move_enrichment = enrich_folder_metadata(
            source,
            workers=workers,
            retries=retries,
            allow_empty=True,
            tracker_path=params.get("tracker_path"),
            cancellation_token=token,
            agentic_model=agentic_model,
        )
        source = resolve_album_folder_successor(source)
        verified_audio_paths = pre_move_enrichment.completed
    report = consolidate_albums(
        source,
        str(params.get("destination_folder", "") or ""),
        retries=retries,
        verified_audio_paths=verified_audio_paths,
        cancellation_token=token,
    )
    destination = Path(str(params.get("destination_folder", "") or "")).expanduser().resolve()
    enrichment_updated: tuple[Path, ...] = ()
    enrichment_skipped: tuple[str, ...] = ()
    enrichment_failed: tuple[str, ...] = ()
    enrichment_repaired_folders: tuple[Path, ...] = ()
    enrichment_tracked = 0
    if perform_enrichment and bool(params.get("enrich_all_destination", False)):
        enrichment = enrich_folder_metadata(
            destination,
            workers=workers,
            retries=retries,
            allow_empty=True,
            tracker_path=params.get("tracker_path"),
            cancellation_token=token,
            agentic_model=agentic_model,
        )
    elif perform_enrichment and pre_move_enrichment is not None:
        enrichment = pre_move_enrichment
    elif perform_enrichment:
        enrichment = enrich_media_files(
            list(report.moved),
            workers=workers,
            retries=retries,
            tracker_path=params.get("tracker_path"),
            cancellation_token=token,
            agentic_model=agentic_model,
        )
    else:
        enrichment = None
        print(
            "[ENRICH-SKIPPED] Album enrichment disabled; "
            "moving by existing metadata and applying track ordering"
        )
    if enrichment is not None:
        enrichment_updated = enrichment.updated
        enrichment_skipped = enrichment.skipped
        enrichment_failed = enrichment.failed
        enrichment_repaired_folders = enrichment.repaired_folders
        enrichment_tracked = enrichment.tracked
    repaired_folders = tuple(
        dict.fromkeys(
            report.repaired_folders
            + enrichment_repaired_folders
            + normalize_album_folders(destination)
        )
    )
    reordered_after_merge = 0
    for album_folder in repaired_folders:
        token.raise_if_cancelled()
        album, _year = split_album_folder_name(album_folder.name)
        if not album or not album_folder.is_dir():
            continue
        try:
            reordered_after_merge += _reorder_album_from_wikipedia(
                album_folder, album, retries=retries
            )
        except (LookupError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
            print(f"[REORDER-SKIPPED] {album_folder.name}: {exc}")
    return OperationSummary(
        operation="album_consolidator",
        total=report.scanned,
        moved=len(report.moved),
        deleted=len(report.deleted),
        tagged=report.tagged + len(enrichment_updated),
        reordered=report.reordered + reordered_after_merge,
        skipped=len(report.skipped) + len(enrichment_skipped),
        tracked=enrichment_tracked,
        failed=len(enrichment_failed),
        output_path=str(destination),
        completed_items=(
            tuple(path.name for path in report.moved)
            + tuple(f"Deleted duplicate: {path.name}" for path in report.deleted)
        ),
        failed_items=report.skipped + enrichment_skipped + enrichment_failed,
    )


def _run_album_metadata_enricher(
    params: dict[str, Any], token: CancellationToken
) -> OperationSummary:
    source = resolve_album_folder_successor(
        str(params.get("source_folder", "") or "")
    )
    report = enrich_folder_metadata(
        source,
        additional_folders=(str(params.get("destination_folder", "") or ""),),
        workers=int(params.get("workers", machine_parallel_workers()) or machine_parallel_workers()),
        retries=int(params.get("retries", 3) or 3),
        tracker_path=params.get("tracker_path"),
        cancellation_token=token,
        agentic_model=str(params.get("agentic_model", "") or ""),
        ai_enabled=bool(params.get("ai_enabled", True)),
        force_recheck=bool(params.get("force_recheck", False)),
    )
    reordered = 0
    failed_ordering_folders: set[Path] = set()
    album_groups: dict[tuple[Path, str], str] = {}
    for path in report.completed:
        try:
            album = read_media_metadata(path).album.strip()
        except (OSError, RuntimeError, ValueError):
            continue
        if album:
            album_base, _album_year = split_album_folder_name(album)
            album_groups.setdefault(
                (path.parent.resolve(), album_base.casefold()), album
            )
    if bool(params.get("wikipedia_track_order", True)):
        if album_groups:
            print(
                "[PROGRESS-PHASE] Wikipedia album ordering "
                f"| total={len(album_groups)}"
            )
        for (album_folder, _album_key), album in album_groups.items():
            token.raise_if_cancelled()
            if not album_folder.is_dir():
                continue
            try:
                reordered += _reorder_album_from_wikipedia(
                    album_folder,
                    album,
                    retries=int(params.get("retries", 3) or 3),
                )
            except (LookupError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
                failed_ordering_folders.add(album_folder)
                print(f"[REORDER-SKIPPED] {album_folder.name}: {exc}")
    tracker_path = params.get("tracker_path")
    if tracker_path:
        MetadataCompletionTracker(tracker_path).mark_complete(
            tuple(
                path
                for path in report.completed
                if path.parent.resolve() not in failed_ordering_folders
            ),
            verification_policy_key(params.get("agentic_model")),
        )
    return OperationSummary(
        operation="album_metadata_enricher",
        total=report.scanned,
        tagged=len(report.updated),
        reordered=reordered,
        skipped=len(report.skipped),
        tracked=report.tracked,
        failed=len(report.failed),
        output_path=str(Path(str(params.get("source_folder", "") or "")).expanduser().resolve()),
        completed_items=tuple(path.name for path in report.updated),
        failed_items=report.failed,
    )


def _run_search_song(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    token.raise_if_cancelled()
    payload = search_song(
        str(params.get("request_text", "") or ""),
        model=str(params.get("model", "qwen2.5:7b") or "qwen2.5:7b"),
        limit=int(params.get("limit", 8) or 8),
        use_ai=bool(params.get("ai_enabled", True)),
    )
    token.raise_if_cancelled()
    return OperationSummary(
        operation="search_song",
        total=len(payload["results"]),
        output_text=json.dumps(payload, ensure_ascii=False),
    )


def _run_enrich_song(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    token.raise_if_cancelled()
    payload = enrich_selected_song(
        str(params.get("url", "") or ""),
        title=str(params.get("title", "") or ""),
        album=str(params.get("album", "") or ""),
        artists=str(params.get("artists", "") or ""),
        thumbnail=str(params.get("thumbnail", "") or ""),
        model=str(params.get("model", "qwen2.5:7b") or "qwen2.5:7b"),
        request_text=str(params.get("request_text", "") or ""),
        use_ai=bool(params.get("ai_enabled", True)),
    )
    token.raise_if_cancelled()
    return OperationSummary(
        operation="enrich_song",
        total=1,
        listed=1,
        output_text=json.dumps(payload, ensure_ascii=False),
    )


def _base_settings(params: dict[str, Any]) -> DownloadSettings:
    workers = int(params.get("workers", machine_parallel_workers()))
    min_delay = int(params.get("min_delay", 10))
    max_delay = int(params.get("max_delay", 25))
    retries = int(params.get("retries", 3))
    retry_wait = int(params.get("retry_wait", 60))
    rate_limit_wait = int(params.get("rate_limit_wait", 180))
    if workers < 1:
        raise ValueError("Workers must be at least 1")
    if min_delay < 0 or max_delay < 0:
        raise ValueError("Delay values cannot be negative")
    if min_delay > max_delay:
        raise ValueError("Minimum delay cannot be greater than maximum delay")
    if retries < 1:
        raise ValueError("Retries must be at least 1")
    if retry_wait < 0 or rate_limit_wait < 1:
        raise ValueError("Retry waits must be non-negative and rate-limit wait must be positive")

    return DownloadSettings(
        max_workers=workers,
        min_delay_seconds=min_delay,
        max_delay_seconds=max_delay,
        max_retries=retries,
        retry_wait_seconds=retry_wait,
        rate_limit_wait_seconds=rate_limit_wait,
        preferred_mp3_quality=str(params.get("preferred_mp3_quality", "320")),
        audio_sample_rate=str(params.get("audio_sample_rate", "44100")),
        skip_existing=not bool(params.get("overwrite", False)),
        default_video_resolution=str(params.get("resolution", "best") or "best"),
        video_merge_output_format=str(params.get("merge_format", "mp4") or "mp4"),
    )


def _required_path(params: dict[str, Any], field: str, label: str) -> Path:
    raw_value = str(params.get(field, "") or "").strip()
    if not raw_value:
        raise ValueError(f"{label} is required")
    path = Path(raw_value).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    return path


def _optional_path(params: dict[str, Any], field: str) -> Path | None:
    raw_value = str(params.get(field, "") or "").strip()
    return Path(raw_value).expanduser() if raw_value else None


@contextmanager
def _input_json(params: dict[str, Any], path_field: str, label: str):
    """Supply services a JSON path from either the visual editor or a legacy path."""
    data = params.get("input_data")
    if data is not None:
        if not isinstance(data, dict) or not data:
            raise ValueError(f"Add at least one {label.lower()} entry")
        if label in {"Audio", "Video"}:
            data = {
                name: values
                for name, values in data.items()
                if not isinstance(values, dict)
                or str(values.get("download", "true")).lower() not in {"false", "0", "no"}
            }
        with tempfile.TemporaryDirectory(prefix="yt_media_studio_") as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            yield path
        return
    yield _required_path(params, path_field, label)


def _run_audio(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    settings = _base_settings(params)
    mode = str(params.get("mode", "download"))
    service = YouTubeAudioDownloader(
        settings=settings,
        cancellation_token=token,
    )
    with _input_json(params, "input_path", "Audio") as json_path:
        if mode == "tag-existing":
            results_dir = Path.cwd() if params.get("input_data") is not None else None
            results = service.tag_existing_mp3_files_from_json(
                json_path,
                results_dir=results_dir,
                write_report=bool(params.get("write_report", False)),
            )
            enrichment_roots = [results_dir or json_path.parent]
        else:
            output_dir = _optional_path(params, "output_dir")
            if params.get("input_data") is not None:
                output_dir = output_dir or Path.cwd() / "songs"
            results = service.download_from_json(
                json_path,
                output_dir=output_dir,
                write_report=bool(params.get("write_report", False)),
            )
            enrichment_roots = [output_dir or json_path.parent / "songs"]
        enrichment = _enrich_download_results(
            results, enrichment_roots, params, token
        )
    return _summarize_results(
        "audio", results, enrichment, output_roots=enrichment_roots
    )


def _run_video(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    settings = _base_settings(params)
    service = YouTubeVideoDownloader(
        settings=settings,
        cancellation_token=token,
        interactive_prompts=False,
    )
    resolution = str(params.get("resolution", "best") or "best")
    if resolution.lower() == "ask":
        raise ValueError("Interactive 'ask' quality is not used in the GUI. Choose a quality or Automatic (best).")

    video_output_dir = _optional_path(params, "output_dir")
    audio_output_dir = _optional_path(params, "audio_output_dir")
    if params.get("input_data") is not None:
        video_output_dir = video_output_dir or Path.cwd() / "videos"
        audio_output_dir = audio_output_dir or Path.cwd() / "songs"
    with _input_json(params, "input_path", "Video") as json_path:
        results = service.download_from_json(
            json_path,
            cli_resolution=resolution,
            output_dir=video_output_dir,
            audio_output_dir=audio_output_dir,
            info_mode=bool(params.get("info_mode", False)),
            mp3_mode=str(params.get("mp3_mode", "audio-only")),
            write_report=bool(params.get("write_report", False)),
        )
        enrichment = _enrich_download_results(
            results,
            [
                video_output_dir or json_path.parent / "videos",
                audio_output_dir or json_path.parent / "songs",
            ],
            params,
            token,
        ) if not bool(params.get("info_mode", False)) else None
    return _summarize_results(
        "video",
        results,
        enrichment,
        output_roots=[
            video_output_dir or json_path.parent / "videos",
            audio_output_dir or json_path.parent / "songs",
        ],
    )


def _run_album(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    settings = _base_settings(params)
    service = YouTubeAlbumSplitter(settings=settings, cancellation_token=token)
    output_dir = _optional_path(params, "output_dir")
    # Visual-editor input is backed by a short-lived internal JSON file. Never
    # allow the service's input-relative default to place finished tracks there.
    if params.get("input_data") is not None and output_dir is None:
        output_dir = Path.cwd() / "album_tracks"
    if params.get("input_data") is not None:
        context = _input_json(params, "input_value", "Album")
    else:
        input_value = str(params.get("input_value", "") or "").strip()
        if input_value.lower().startswith(("http://", "https://")):
            return _run_album_url(params, token, service, input_value)
        context = _input_json(params, "input_value", "Album")
    with context as input_path:
        enrichment_root = output_dir or Path(input_path).resolve().parent / "album_tracks"
        results = service.split_from_input(
            str(input_path), output_dir=output_dir,
            silence_threshold_db=float(params.get("silence_threshold_db", -35.0)),
            min_silence_duration=float(params.get("min_silence_duration", 1.5)),
            min_track_duration=float(params.get("min_track_duration", 45.0)),
            trim_silence_padding=float(params.get("trim_silence_padding", 0.25)),
            keep_temp=bool(params.get("keep_temp", False)), overwrite=bool(params.get("overwrite", False)),
            write_report=bool(params.get("write_report", False)),
        )
        enrichment = _enrich_download_results(
            results, [enrichment_root], params, token
        )
    summary = _summarize_results(
        "album", results, enrichment, output_roots=[enrichment_root]
    )
    completed_albums = _completed_album_entries(params.get("input_data"), results)
    if completed_albums:
        summary = replace(
            summary,
            completed_items=tuple(
                dict.fromkeys((*summary.completed_items, *completed_albums))
            ),
        )
    return summary


def _run_album_url(params, token, service, input_value):
    output_dir = _optional_path(params, "output_dir")
    results = service.split_from_input(
        input_value, output_dir=output_dir,
        album_name=str(params.get("album_name", "") or "").strip() or None,
        artists=str(params.get("artists", "") or "").strip() or None,
        silence_threshold_db=float(params.get("silence_threshold_db", -35.0)),
        min_silence_duration=float(params.get("min_silence_duration", 1.5)),
        min_track_duration=float(params.get("min_track_duration", 45.0)),
        trim_silence_padding=float(params.get("trim_silence_padding", 0.25)),
        keep_temp=bool(params.get("keep_temp", False)), overwrite=bool(params.get("overwrite", False)),
        write_report=bool(params.get("write_report", False)),
    )
    enrichment = _enrich_download_results(
        results, [output_dir or Path.cwd() / "album_tracks"], params, token
    )
    return _summarize_results(
        "album",
        results,
        enrichment,
        output_roots=[output_dir or Path.cwd() / "album_tracks"],
    )


def _run_jukebox(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    settings = _base_settings(params)
    service = YouTubeJukeboxSplitter(settings=settings, cancellation_token=token)
    output_dir = _optional_path(params, "output_dir")
    if params.get("input_data") is not None and output_dir is None:
        output_dir = Path.cwd() / "jukebox_tracks"
    with _input_json(params, "input_path", "Jukebox") as json_path:
        enrichment_root = output_dir or json_path.parent / "jukebox_tracks"
        results = service.split_from_json(
            json_path, output_dir=output_dir,
            keep_temp=bool(params.get("keep_temp", False)), overwrite=bool(params.get("overwrite", False)),
            write_report=bool(params.get("write_report", False)),
        )
        enrichment = _enrich_download_results(
            results, [enrichment_root], params, token
        )
    summary = _summarize_results(
        "jukebox", results, enrichment, output_roots=[enrichment_root]
    )
    completed_jukeboxes = _completed_album_entries(
        params.get("input_data"), results
    )
    if completed_jukeboxes:
        summary = replace(
            summary,
            completed_items=tuple(
                dict.fromkeys((*summary.completed_items, *completed_jukeboxes))
            ),
        )
    return summary


def _run_audio_trimmer(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    output_path = trim_audio(
        str(params.get("input_path", "") or ""),
        str(params.get("start_timestamp", "00:00") or "00:00"),
        str(params.get("end_timestamp", "") or ""),
        overwrite_source=bool(params.get("overwrite_source", False)),
        output_path=str(params.get("output_path", "") or "") or None,
        cancellation_token=token,
    )
    return OperationSummary(
        operation="audio_trimmer",
        total=1,
        downloaded=1,
        output_path=str(output_path),
        completed_items=(output_path.name,),
    )


def _run_redownload(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    output_paths = redownload_media(
        str(params.get("input_path", "") or ""),
        str(params.get("youtube_url", "") or ""),
        media_mode=str(params.get("media_mode", "auto") or "auto"),
        start_timestamp=str(params.get("start_timestamp", "00:00") or "00:00"),
        end_timestamp=str(params.get("end_timestamp", "") or ""),
        overwrite_source=bool(params.get("overwrite_source", False)),
        output_path=str(params.get("output_path", "") or "") or None,
        cancellation_token=token,
    )
    return OperationSummary(
        operation="redownload",
        total=len(output_paths),
        downloaded=len(output_paths),
        output_path=str(output_paths[0]) if output_paths else "",
        completed_items=tuple(path.name for path in output_paths),
    )


def _run_edit_media(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    metadata = params.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Edited metadata must be an object")
    action = str(params.get("action", "metadata") or "metadata")
    if action == "video_display":
        raise ValueError(
            "Video crop/aspect must be saved as a Media Library playback profile"
        )
    output_paths = edit_media_file(
        str(params.get("input_path", "") or ""),
        action,
        metadata,
        start_timestamp=str(params.get("start_timestamp", "00:00") or "00:00"),
        end_timestamp=str(params.get("end_timestamp", "") or ""),
        overwrite_source=bool(params.get("overwrite_source", False)),
        output_path=str(params.get("output_path", "") or "") or None,
        youtube_url=str(params.get("youtube_url", "") or ""),
        media_mode=str(params.get("media_mode", "auto") or "auto"),
        artwork_path=str(params.get("artwork_path", "") or "") or None,
        remove_artwork=bool(params.get("remove_artwork", False)),
        crop_ratio=str(params.get("crop_ratio", "Default") or "Default"),
        aspect_ratio=str(params.get("aspect_ratio", "Default") or "Default"),
        cancellation_token=token,
    )
    return OperationSummary(
        operation="edit_media",
        total=len(output_paths),
        downloaded=len(output_paths) if action not in {"metadata", "video_display"} else 0,
        tagged=len(output_paths),
        output_path=str(output_paths[0]) if output_paths else "",
        completed_items=tuple(path.name for path in output_paths),
    )


def _run_edit_album(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    metadata = params.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Album metadata must be an object")
    result = edit_album_folder(
        str(params.get("folder", "") or ""),
        metadata,
        artwork_path=str(params.get("artwork_path", "") or "") or None,
        remove_artwork=bool(params.get("remove_artwork", False)),
        cancellation_token=token,
    )
    return OperationSummary(
        operation="edit_album",
        total=len(result.updated) + len(result.failed),
        tagged=len(result.updated),
        failed=len(result.failed),
        output_path=str(Path(str(params.get("folder", "") or "")).resolve()),
        completed_items=tuple(path.name for path in result.updated),
        failed_items=tuple(path.name for path, _error in result.failed),
    )


def _run_duplicate_links(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    token.raise_if_cancelled()
    json_path = _required_path(params, "input_path", "JSON file")
    duplicates = find_duplicate_youtube_links(json_path)
    token.raise_if_cancelled()

    output_path = _optional_path(params, "output_path")
    output_text = json.dumps(duplicates, indent=2, ensure_ascii=False)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_text + "\n", encoding="utf-8")
        print(f"[SAVED] Duplicate report saved to: {output_path}")

    if duplicates:
        print(f"[FOUND] {len(duplicates)} duplicate link group(s)")
        for item in duplicates:
            print(f"[DUPLICATE] {item['count']} entries -> {item['ytb_link']}")
    else:
        print("[OK] No duplicate YouTube links found")

    return OperationSummary(
        operation="duplicate_links",
        total=len(duplicates),
        failed=0,
        output_text=output_text,
        output_path=str(output_path or ""),
    )


def _run_format_artists(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    token.raise_if_cancelled()
    raw_text = str(params.get("input_text", "") or "").strip()
    if not raw_text:
        raise ValueError("Artist text is required")
    output_text = format_artist_names(raw_text)
    print(output_text)
    return OperationSummary(operation="format_artists", total=1, output_text=output_text)


def _run_parse_tracks(params: dict[str, Any], token: CancellationToken) -> OperationSummary:
    token.raise_if_cancelled()
    raw_text = str(params.get("input_text", "") or "")
    input_path = str(params.get("input_path", "") or "").strip()
    if not raw_text.strip() and input_path:
        path = Path(input_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Timestamp text file does not exist: {path}")
        raw_text = path.read_text(encoding="utf-8")
    if not raw_text.strip():
        raise ValueError("Timestamp text is required")

    output_text = parse_tracks_to_json(
        raw_text,
        end_field=str(params.get("end_field", "end")),
        title_case=not bool(params.get("keep_case", False)),
        unknown_artists=str(params.get("unknown_artists", "Unknown") or "Unknown"),
    )
    token.raise_if_cancelled()

    output_path = _optional_path(params, "output_path")
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_text + "\n", encoding="utf-8")
        print(f"[SAVED] Track JSON saved to: {output_path}")
    else:
        print(output_text)

    return OperationSummary(
        operation="parse_tracks",
        total=1,
        output_text=output_text,
        output_path=str(output_path or ""),
    )


def _summarize_results(
    operation: str,
    results: list[DownloadResult],
    enrichment: Any | None = None,
    *,
    output_roots: list[Path] | None = None,
) -> OperationSummary:
    counts = {
        "downloaded": 0,
        "tagged": 0,
        "skipped": 0,
        "listed": 0,
        "failed": 0,
    }
    for result in results:
        status = result.status.value
        if status == "downloaded":
            counts["downloaded"] += 1
        elif status == "tagged":
            counts["tagged"] += 1
        elif status in {"skipped", "already_exists"}:
            counts["skipped"] += 1
        elif status == "listed":
            counts["listed"] += 1
        elif status == "failed":
            counts["failed"] += 1

    summary = OperationSummary(
        operation=operation,
        total=len(results),
        downloaded=counts["downloaded"],
        tagged=counts["tagged"],
        skipped=counts["skipped"],
        listed=counts["listed"],
        failed=counts["failed"],
        output_path=_result_output_folder(results, output_roots or []),
        completed_items=tuple(
            result.song
            for result in results
            if result.status.value in {"downloaded", "tagged", "already_exists"}
        ),
        failed_items=tuple(
            result.song for result in results if result.status.value == "failed"
        ),
    )
    if enrichment is None:
        return summary
    return replace(
        summary,
        tagged=summary.tagged + len(enrichment.updated),
        failed=summary.failed + len(enrichment.failed),
    )


def _result_output_folder(
    results: list[DownloadResult], roots: list[Path]
) -> str:
    """Return the most specific existing folder containing a completed output."""

    completed = [
        result
        for result in results
        if result.status.value in {"downloaded", "tagged", "already_exists"}
    ]
    if not completed:
        return ""

    resolved_roots = [root.expanduser().resolve() for root in roots]
    for result in completed:
        raw_name = str(result.file_name or "").strip()
        if not raw_name:
            continue
        reported = Path(raw_name).expanduser()
        candidates = (
            [reported.resolve()]
            if reported.is_absolute()
            else [root / reported for root in resolved_roots]
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.parent)
            if candidate.is_dir():
                return str(candidate)
            if candidate.parent.is_dir():
                return str(candidate.parent)

    return str(resolved_roots[0]) if resolved_roots else ""


def _enrich_download_results(
    results: list[DownloadResult],
    roots: list[Path],
    params: dict[str, Any],
    token: CancellationToken,
):
    """Run the shared Album Enricher immediately on successful audio outputs."""

    if not bool(params.get("auto_enrich_downloads", True)):
        return None
    paths = _resolve_downloaded_audio_paths(results, roots)
    if not paths:
        return None
    print(f"[POST-DOWNLOAD-ENRICH] Processing {len(paths)} audio file(s)")
    report = enrich_media_files(
        paths,
        workers=int(params.get("workers", machine_parallel_workers()) or machine_parallel_workers()),
        retries=int(params.get("retries", 3) or 3),
        tracker_path=params.get("tracker_path"),
        cancellation_token=token,
        agentic_model=str(params.get("agentic_model", "") or ""),
    )
    if bool(params.get("auto_consolidate_downloads", True)):
        relevant_paths = list(dict.fromkeys(
            [path.resolve() for path in report.updated if path.exists()]
            + [path.resolve() for path in paths if path.exists()]
        ))
        for root in dict.fromkeys(
            Path(value).expanduser().resolve() for value in roots if value
        ):
            token.raise_if_cancelled()
            consolidate_audio_in_place(
                root,
                media_paths=relevant_paths,
                retries=int(params.get("retries", 3) or 3),
            )
    return report


def _resolve_downloaded_audio_paths(
    results: list[DownloadResult], roots: list[Path]
) -> list[Path]:
    supported = {
        ".aac", ".aif", ".aiff", ".ape", ".flac", ".m4a", ".m4b",
        ".mp3", ".oga", ".ogg", ".opus", ".wav", ".wma", ".wv",
    }
    resolved_roots = [Path(root).expanduser().resolve() for root in roots if root]
    found: dict[str, Path] = {}
    for result in results:
        if result.status.value not in {"downloaded", "tagged", "already_exists"}:
            continue
        for raw_name in str(result.file_name or "").split(" + "):
            raw_name = raw_name.strip()
            if not raw_name:
                continue
            candidate = Path(raw_name).expanduser()
            candidates = [candidate] if candidate.is_absolute() else [
                root / candidate for root in resolved_roots
            ]
            for path in candidates:
                if path.is_file() and path.suffix.casefold() in supported:
                    resolved = path.resolve()
                    found[str(resolved).casefold()] = resolved
                    break
            else:
                for root in resolved_roots:
                    if not root.is_dir():
                        continue
                    matches = [
                        path for path in root.rglob(candidate.name)
                        if path.is_file() and path.suffix.casefold() in supported
                    ]
                    if len(matches) == 1:
                        resolved = matches[0].resolve()
                        found[str(resolved).casefold()] = resolved
                        break
    return list(found.values())


def _completed_album_entries(
    input_data: object, results: list[DownloadResult]
) -> tuple[str, ...]:
    """Identify fully successful visual-editor albums despite numbered result labels."""

    if not isinstance(input_data, dict):
        return ()
    successful_statuses = {"downloaded", "tagged", "already_exists"}
    completed: list[str] = []
    for raw_name, raw_values in input_data.items():
        name = str(raw_name)
        values = raw_values if isinstance(raw_values, dict) else {}
        parent_flag = str(values.get("download", "true")).strip().casefold()
        if parent_flag in {"false", "0", "no", "off"}:
            continue
        prefix = name + " / "
        album_results = [
            result
            for result in results
            if result.song == name or result.song.startswith(prefix)
        ]
        if not album_results or any(
            result.status.value == "failed" for result in album_results
        ):
            continue
        tracks = values.get("tracks", [])
        enabled_track_count = 0
        if isinstance(tracks, list) and tracks:
            for track in tracks:
                if not isinstance(track, dict) or not track:
                    continue
                track_values = next(iter(track.values()))
                track_values = track_values if isinstance(track_values, dict) else {}
                track_flag = str(
                    track_values.get("download", "true")
                ).strip().casefold()
                if track_flag not in {"false", "0", "no", "off"}:
                    enabled_track_count += 1
        successful_count = sum(
            result.status.value in successful_statuses for result in album_results
        )
        required_successes = enabled_track_count if enabled_track_count else 1
        if successful_count >= required_successes:
            completed.append(name)
    return tuple(completed)
