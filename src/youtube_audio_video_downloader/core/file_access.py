"""Shared retry/skip handling for files locked by another application."""

from __future__ import annotations

import errno
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, TypeVar


T = TypeVar("T")
FileInUseHandler = Callable[[Path, str], None]

_handler_lock = threading.Lock()
_prompt_lock = threading.Lock()
_file_in_use_handler: FileInUseHandler | None = None


class FileInUseSkippedError(OSError):
    """Raised after the user-approved retry still finds a file locked."""

    def __init__(self, path: str | Path, action: str, cause: OSError) -> None:
        self.path = Path(path)
        self.action = action
        self.cause = cause
        super().__init__(
            f"Skipped {self.path.name}: it is still being used by another application"
        )


@contextmanager
def file_in_use_handler(handler: FileInUseHandler):
    """Install the GUI's blocking prompt for one active operation."""

    global _file_in_use_handler
    with _handler_lock:
        previous = _file_in_use_handler
        _file_in_use_handler = handler
    try:
        yield
    finally:
        with _handler_lock:
            _file_in_use_handler = previous


def retry_file_operation(path: str | Path, action: str, operation: Callable[[], T]) -> T:
    """Run once, prompt on a sharing violation, retry once, then mark it skipped."""

    try:
        return operation()
    except OSError as exc:
        if not _is_file_in_use_error(exc):
            raise
        with _prompt_lock:
            with _handler_lock:
                handler = _file_in_use_handler
            if handler is not None:
                handler(Path(path), action)
            try:
                return operation()
            except OSError as retry_exc:
                if _is_file_in_use_error(retry_exc):
                    raise FileInUseSkippedError(path, action, retry_exc) from retry_exc
                raise


def _is_file_in_use_error(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {32, 33} or (
        getattr(exc, "errno", None) in {errno.EACCES, errno.EBUSY}
    )
