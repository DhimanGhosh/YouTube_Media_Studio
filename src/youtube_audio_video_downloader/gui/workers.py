"""Background worker objects for responsive GUI operations."""

from __future__ import annotations

import contextlib
import io
import re
import threading
import traceback
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.core.exceptions import UserCancelledError
from youtube_audio_video_downloader.core.file_access import (
    FileInUseSkippedError,
    file_in_use_handler,
)
from youtube_audio_video_downloader.gui.operations import OperationSummary, execute_operation


_OPERATION_CONTEXTS: dict[str, tuple[str, str]] = {
    "album_metadata_enricher": ("Album Consolidator", "Album enricher"),
    "album_consolidator": ("Album Consolidator", "Move into album folders"),
}

_SAFE_OPERATION_RETRIES = frozenset(
    {
        "album_metadata_enricher",
        "track_reorder",
        "audio_trimmer",
        "redownload",
        "edit_media",
        "edit_album",
        "duplicate_links",
        "format_artists",
        "parse_tracks",
        "search_song",
        "enrich_song",
    }
)


def operation_display_name(operation: str) -> str:
    """Return a user-facing workflow/subsection name for logs and status text."""

    main, subsection = _OPERATION_CONTEXTS.get(
        operation,
        (str(operation).replace("_", " ").title(), ""),
    )
    return f"{main} · {subsection}" if subsection else main


def running_operation_text(operation: str, detail: str = "") -> str:
    """Format the activity bar as main workspace, running subsection, action."""

    main, subsection = _OPERATION_CONTEXTS.get(
        operation,
        (str(operation).replace("_", " ").title(), ""),
    )
    prefix = f"{main} · Running {subsection}" if subsection else f"Running {main}"
    action = str(detail or "").strip()
    return f"{prefix} · {action}" if action else prefix


def estimate_eta_seconds(
    current: int,
    total: int,
    elapsed_seconds: float,
    historical_seconds_per_item: float | None = None,
) -> float | None:
    """Blend a learned operation rate with the current run's observed rate."""

    current = max(0, int(current))
    total = max(0, int(total))
    if total <= 0:
        return None
    if current >= total:
        return 0.0
    learned = (
        float(historical_seconds_per_item)
        if historical_seconds_per_item is not None
        and historical_seconds_per_item > 0
        else None
    )
    live = float(elapsed_seconds) / current if current > 0 and elapsed_seconds > 0 else None
    if learned is None and live is None:
        return None
    if learned is None:
        rate = live
    elif live is None:
        rate = learned
    else:
        # Trust history early, then progressively favor this run's real pace.
        live_weight = min(0.8, current / max(5.0, total * 0.35))
        rate = learned * (1.0 - live_weight) + live * live_weight
    return max(0.0, (total - current) * float(rate or 0.0))


def format_eta(seconds: float | None) -> str:
    """Return a compact ETA suitable for the progress bar."""

    if seconds is None:
        return "Estimating…"
    rounded = max(0, int(round(seconds)))
    hours, remainder = divmod(rounded, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class SignalTextStream(io.TextIOBase):
    """Thread-safe line-buffered stream that forwards text to a Qt signal."""

    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self._callback = callback
        self._buffers: dict[int, str] = {}
        self._lock = threading.Lock()

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if not text:
            return 0
        with self._lock:
            thread_id = threading.get_ident()
            buffer = self._buffers.get(thread_id, "") + str(text).replace("\r", "")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if line:
                    self._callback(line)
            self._buffers[thread_id] = buffer
        return len(text)

    def flush(self) -> None:
        with self._lock:
            for thread_id, buffer in tuple(self._buffers.items()):
                if buffer:
                    self._callback(buffer)
                self._buffers.pop(thread_id, None)


class OperationWorker(QObject):
    """Run one service operation away from the GUI thread."""

    log = pyqtSignal(str)
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str, str)
    cancelled = pyqtSignal()
    progress = pyqtSignal(int, int, str)
    phase_changed = pyqtSignal(str, int)
    file_in_use = pyqtSignal(str, str)
    item_finished = pyqtSignal(str, bool)

    def __init__(self, operation: str, params: dict) -> None:
        super().__init__()
        self.operation = operation
        self.params = params
        self.cancellation_token = CancellationToken()
        self._progress_current = 0
        self._progress_total = 0
        self._file_prompt_event: threading.Event | None = None

    @pyqtSlot()
    def run(self) -> None:
        self._progress_total = _estimate_operation_total(self.operation, self.params)
        self.progress.emit(0, self._progress_total, "Preparing operation")
        self.log.emit(
            f"[PROGRESS] 0/{self._progress_total or '?'} | Preparing operation"
        )
        stream = SignalTextStream(self._forward_log)
        try:
            self.log.emit(f"[START] {operation_display_name(self.operation)}")
            with (
                contextlib.redirect_stdout(stream),
                contextlib.redirect_stderr(stream),
                file_in_use_handler(self._wait_for_file_release),
            ):
                summary = self._execute_with_retries()
            stream.flush()
            if self.cancellation_token.is_cancelled():
                self.cancelled.emit()
                return
            final_total = self._progress_total or max(self._progress_current, 1)
            self.progress.emit(final_total, final_total, "Finalizing results")
            self.log.emit(
                f"[PROGRESS] {final_total}/{final_total} | Finalizing results"
            )
            self.finished.emit(summary.as_dict())
        except UserCancelledError:
            stream.flush()
            self.cancelled.emit()
        except FileInUseSkippedError as exc:
            stream.flush()
            message = str(exc)
            self.log.emit(f"[SKIPPED] {message}")
            self.finished.emit(
                OperationSummary(
                    operation=self.operation,
                    total=1,
                    skipped=1,
                    failed_items=(message,),
                ).as_dict()
            )
        except Exception as exc:  # noqa: BLE001 - surfaced cleanly to the GUI
            stream.flush()
            self.failed.emit(str(exc), traceback.format_exc())

    def _execute_with_retries(self) -> OperationSummary:
        """Retry safe operation-level transient failures using the global policy."""

        configured = max(1, int(self.params.get("retries", 1) or 1))
        attempts = configured if self.operation in _SAFE_OPERATION_RETRIES else 1
        for attempt in range(1, attempts + 1):
            self.cancellation_token.raise_if_cancelled()
            try:
                return execute_operation(
                    self.operation, self.params, self.cancellation_token
                )
            except (UserCancelledError, FileInUseSkippedError):
                raise
            except Exception as exc:
                if attempt >= attempts:
                    raise
                next_attempt = attempt + 1
                self.log.emit(
                    f"[RETRY] {operation_display_name(self.operation)} failed: {exc} | "
                    f"attempt {next_attempt}/{attempts}"
                )
                self.cancellation_token.wait(min(2.0 ** (attempt - 1), 5.0))
        raise RuntimeError("Retry loop ended unexpectedly")

    @pyqtSlot()
    def cancel(self) -> None:
        self.cancellation_token.cancel()
        self.log.emit("[CANCEL] Cancellation requested. Running workers are shutting down safely...")
        self.acknowledge_file_in_use()

    def _wait_for_file_release(self, path: Path, action: str) -> None:
        event = threading.Event()
        self._file_prompt_event = event
        self.log.emit(
            f"[FILE-IN-USE] {path} | action={action} | waiting for user confirmation"
        )
        self.file_in_use.emit(str(path), action)
        event.wait()
        self._file_prompt_event = None
        self.cancellation_token.raise_if_cancelled()
        self.log.emit(f"[RETRY] Retrying {action}: {path}")

    def acknowledge_file_in_use(self) -> None:
        event = self._file_prompt_event
        if event is not None:
            event.set()

    def _forward_log(self, line: str) -> None:
        self.log.emit(line)
        phase_total, phase_label = _progress_phase_from_log(line)
        if phase_total:
            # This is a new unit of work with a different throughput. Appending
            # it to the completed file count makes both the total and ETA jump.
            self._progress_current = 0
            self._progress_total = phase_total
            unit = "album(s)" if "album" in phase_label.casefold() else "item(s)"
            phase_detail = f"Starting {phase_label}: {phase_total} {unit}"
            self.phase_changed.emit(phase_label, phase_total)
            self.progress.emit(0, phase_total, phase_detail)
            self.log.emit(
                f"[PROGRESS] 0/{self._progress_total} | {phase_detail}"
            )
            return
        item_result = _item_result_from_log(self.operation, line)
        if item_result is not None:
            self.item_finished.emit(*item_result)
        completed, detail = _progress_from_log(self.operation, line)
        if completed:
            self._progress_current += 1
            if self._progress_total:
                self._progress_current = min(self._progress_current, self._progress_total)
        if completed or detail:
            self.progress.emit(self._progress_current, self._progress_total, detail)
        if completed:
            self.log.emit(
                f"[PROGRESS] {self._progress_current}/{self._progress_total or '?'} | {detail}"
            )


def _estimate_operation_total(operation: str, params: dict) -> int:
    """Estimate concrete work items before execution without modifying any files."""

    if operation in {"album_consolidator", "album_metadata_enricher"}:
        root_values = [params.get("source_folder", "")]
        if operation == "album_metadata_enricher":
            root_values.append(params.get("destination_folder", ""))
        roots: list[Path] = []
        for value in root_values:
            text = str(value or "").strip()
            if not text:
                continue
            candidate = Path(text).expanduser().resolve()
            if not candidate.is_dir():
                continue
            if any(candidate == root or candidate.is_relative_to(root) for root in roots):
                continue
            roots = [root for root in roots if not root.is_relative_to(candidate)]
            roots.append(candidate)
        if not roots:
            return 0
        from youtube_audio_video_downloader.services.album_consolidator import (
            SUPPORTED_AUDIO_EXTENSIONS,
            SUPPORTED_MEDIA_EXTENSIONS,
        )
        from youtube_audio_video_downloader.services.metadata_tracker import (
            MetadataCompletionTracker,
        )

        tracker_path = params.get("tracker_path")
        tracker = MetadataCompletionTracker(tracker_path) if tracker_path else None

        def untracked_audio_count(paths) -> int:
            audio_paths = [
                path for path in paths
                if path.is_file()
                and not path.is_symlink()
                and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
            ]
            if tracker is None:
                return len(audio_paths)
            return sum(not tracker.is_complete(path) for path in audio_paths)

        supported = (
            SUPPORTED_AUDIO_EXTENSIONS
            if operation == "album_metadata_enricher"
            else SUPPORTED_MEDIA_EXTENSIONS
        )

        primary_total = sum(
            1 for root in roots for path in root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in supported
        )
        if operation == "album_metadata_enricher":
            return untracked_audio_count(
                path for root in roots for path in root.rglob("*")
            )
        source_root = roots[0]
        moved_audio_estimate = untracked_audio_count(source_root.rglob("*"))
        destination_audio = 0
        if bool(params.get("enrich_all_destination", False)):
            destination_text = str(params.get("destination_folder", "") or "").strip()
            destination = Path(destination_text).expanduser().resolve() if destination_text else None
            if destination is not None and destination.is_dir():
                destination_audio = untracked_audio_count(destination.rglob("*"))
        return primary_total + moved_audio_estimate + destination_audio
    if operation == "track_reorder":
        paths = params.get("paths", [])
        return len(paths) if isinstance(paths, list) else 0
    input_data = params.get("input_data")
    if isinstance(input_data, dict):
        def enabled(values: object) -> bool:
            if not isinstance(values, dict):
                return True
            raw_flag = values.get("download", "true")
            flag = str("true" if raw_flag is None else raw_flag).strip().casefold()
            return flag not in {"false", "0", "no", "off"}

        if operation in {"album", "jukebox"}:
            total = 0
            for values in input_data.values():
                if not enabled(values):
                    # Disabled parent jobs emit one terminal SKIPPED result.
                    total += 1
                    continue
                tracks = values.get("tracks", []) if isinstance(values, dict) else []
                total += len(tracks) if isinstance(tracks, list) and tracks else 1
            return total
        # Audio and Video remove disabled rows before invoking their services,
        # so those rows must not inflate progress or ETA.
        return sum(1 for values in input_data.values() if enabled(values))
    return 1


def _progress_from_log(operation: str, line: str) -> tuple[bool, str]:
    """Translate service log records into one progress event and readable status."""

    text = str(line or "").strip()
    match = re.match(r"^\[([^]]+)]\s*(.*)$", text)
    if not match:
        return False, text[:140]
    event, detail = match.group(1).upper(), match.group(2).strip()
    terminal_by_operation = {
        "album_consolidator": {
            "MOVED", "SKIPPED", "DELETED-DUPLICATE",
            "ENRICHED", "ENRICH-SKIPPED", "ENRICH-FAILED",
        },
        "album_metadata_enricher": {
            "ENRICHED", "ENRICH-SKIPPED", "ENRICH-FAILED",
            "REORDERED-SUMMARY", "REORDER-SKIPPED",
        },
        "track_reorder": {"REORDERED", "SKIPPED"},
    }
    generic_terminal = {
        "DOWNLOADED", "TAGGED", "SKIPPED", "FAILED", "ALREADY_EXISTS", "LISTED"
    }
    completed = event in terminal_by_operation.get(operation, generic_terminal)
    verbs = {
        "MOVED": "Moved", "SKIPPED": "Skipped", "DELETED-DUPLICATE": "Deleted duplicate",
        "ENRICHED": "Updated metadata", "ENRICH-SKIPPED": "Skipped metadata",
        "ENRICH-FAILED": "Metadata lookup failed", "TAGGED": "Tagged",
        "DOWNLOADED": "Downloaded", "FAILED": "Failed", "WAIT": "Waiting",
        "UNTAGGED": "Removing invalid album", "REORDERED": "Reordered tracks",
        "REORDERED-SUMMARY": "Completed album ordering",
        "REORDER-SKIPPED": "Skipped album ordering",
    }
    readable = verbs.get(event, event.replace("-", " ").title())
    return completed, f"{readable}: {detail}"[:180] if detail else readable


def _progress_phase_from_log(line: str) -> tuple[int, str]:
    """Return the fixed size and label announced for a later work phase."""

    match = re.match(
        r"^\[PROGRESS-PHASE]\s*(.*?)\s*\|\s*total=(\d+)\s*$",
        str(line or "").strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return 0, ""
    total = max(0, int(match.group(2)))
    label = match.group(1).strip() or "Next phase"
    return total, label


def _item_result_from_log(operation: str, line: str) -> tuple[str, bool] | None:
    """Extract a completed batch-editor item as soon as its result is printed."""

    if operation not in {"audio", "video", "album", "jukebox"}:
        return None
    match = re.match(r"^\[([^]]+)]\s*(.*)$", str(line or "").strip())
    if not match:
        return None
    event = match.group(1).upper()
    successful = {"DOWNLOADED", "TAGGED", "ALREADY_EXISTS"}
    unsuccessful = {"FAILED", "SKIPPED"}
    if event not in successful | unsuccessful:
        return None
    item = match.group(2).split(" | ", 1)[0].strip()
    return (item, event in successful) if item else None
