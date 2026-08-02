"""Cooperative cancellation helpers for CLI services.

The project uses worker threads for parallel downloads. Raising ``KeyboardInterrupt``
on the main thread does not automatically stop worker threads, especially when a
worker is inside a long randomized delay. This module provides a tiny shared
cancellation token so the main thread can ask workers to stop and then wait for
them cleanly before the Python process exits.
"""

from __future__ import annotations

import threading

from youtube_audio_video_downloader.core.exceptions import UserCancelledError


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation for all code paths sharing this token."""

        self._event.set()

    def reset(self) -> None:
        """Clear a previous cancellation request before a new run."""

        self._event.clear()

    def is_cancelled(self) -> bool:
        """Return whether cancellation has been requested."""

        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise a user-facing cancellation error when cancellation is active."""

        if self.is_cancelled():
            raise UserCancelledError("Operation cancelled by user")

    def wait(self, seconds: float, *, reason: str = "Operation cancelled by user") -> None:
        """Sleep for ``seconds`` but wake immediately when cancelled.

        ``threading.Event.wait`` is used instead of ``time.sleep`` so a worker
        that is currently waiting before a download or retry can return quickly
        after Ctrl+C, allowing the executor to shut down without Python printing
        shutdown-time tracebacks.
        """

        self.raise_if_cancelled()
        if seconds <= 0:
            return
        if self._event.wait(seconds):
            raise UserCancelledError(reason)
