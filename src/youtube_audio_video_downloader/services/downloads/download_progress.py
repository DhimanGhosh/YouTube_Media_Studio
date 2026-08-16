"""Shared parallel-fragment settings and structured download telemetry."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


DOWNLOAD_EVENT_PREFIX = "[DOWNLOAD-EVENT] "


class DownloadProgressReporter:
    """Emit throttled machine-readable yt-dlp progress without noisy terminal bars."""

    def __init__(self, label: str, connections: int, cancellation_token=None) -> None:
        self.label = label
        self.connections = max(1, min(32, int(connections)))
        self.cancellation_token = cancellation_token
        self._lock = threading.Lock()
        self._last_emit = 0.0
        self._last_percent = -1
        self._last_fragment = -1

    def __call__(self, status: dict[str, Any]) -> None:
        if self.cancellation_token is not None:
            self.cancellation_token.raise_if_cancelled()
        state = str(status.get("status") or "downloading")
        downloaded = int(status.get("downloaded_bytes") or 0)
        total = int(status.get("total_bytes") or status.get("total_bytes_estimate") or 0)
        percent = min(100.0, downloaded * 100.0 / total) if total else 0.0
        fragment = int(status.get("fragment_index") or 0)
        fragment_count = int(status.get("fragment_count") or 0)
        now = time.monotonic()
        percent_bucket = int(percent)
        with self._lock:
            important = state == "finished" or fragment != self._last_fragment
            if not important and percent_bucket == self._last_percent and now - self._last_emit < 0.75:
                return
            self._last_emit = now
            self._last_percent = percent_bucket
            self._last_fragment = fragment
        info = status.get("info_dict") or {}
        protocol = str(info.get("protocol") or "")
        fragmented = fragment_count > 1 or "m3u8" in protocol or "dash" in protocol
        used_connections = min(self.connections, fragment_count) if fragment_count > 1 else 1
        payload = {
            "label": self.label,
            "file": Path(str(status.get("filename") or "")).name,
            "status": state,
            "percent": 100.0 if state == "finished" else percent,
            "downloaded": downloaded,
            "total": total,
            "speed": float(status.get("speed") or 0),
            "eta": int(status.get("eta") or 0),
            "connections_configured": self.connections,
            "connections_used": used_connections,
            "fragmented": fragmented,
            "fragment": fragment,
            "fragment_count": fragment_count,
        }
        print(DOWNLOAD_EVENT_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def accelerated_download_options(
    label: str,
    connections: int,
    cancellation_token=None,
) -> dict[str, Any]:
    """Return yt-dlp options for resumable parallel fragment downloading."""

    count = max(1, min(32, int(connections)))
    return {
        "concurrent_fragment_downloads": count,
        "noprogress": True,
        "progress_hooks": [DownloadProgressReporter(label, count, cancellation_token)],
    }


def parse_download_event(line: str) -> dict[str, Any] | None:
    if not line.startswith(DOWNLOAD_EVENT_PREFIX):
        return None
    try:
        payload = json.loads(line[len(DOWNLOAD_EVENT_PREFIX) :])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def format_download_event(payload: dict[str, Any]) -> str:
    """Return a concise user-facing Live Logs line."""

    def size(value: float) -> str:
        amount = float(value or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if amount < 1024 or unit == "GB":
                return f"{amount:.1f} {unit}"
            amount /= 1024
        return f"{amount:.1f} GB"

    pct = float(payload.get("percent") or 0)
    downloaded = size(float(payload.get("downloaded") or 0))
    total_value = float(payload.get("total") or 0)
    total = size(total_value) if total_value else "unknown"
    speed_value = float(payload.get("speed") or 0)
    speed = f"{size(speed_value)}/s" if speed_value else "calculating"
    eta_value = int(payload.get("eta") or 0)
    eta = f"{eta_value // 60:02d}:{eta_value % 60:02d}" if eta_value else "--:--"
    used = int(payload.get("connections_used") or 1)
    configured = int(payload.get("connections_configured") or used)
    fragment = int(payload.get("fragment") or 0)
    count = int(payload.get("fragment_count") or 0)
    segment = f" | segment {fragment}/{count}" if count else ""
    return (
        f"[DOWNLOAD] {payload.get('label') or payload.get('file') or 'Media'} | "
        f"{pct:.1f}% | {downloaded} / {total} | {speed} | ETA {eta} | "
        f"connections {used}/{configured}{segment}"
    )
