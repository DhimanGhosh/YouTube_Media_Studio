"""Project-specific exceptions used by the command-line entry points."""

from __future__ import annotations


class UserCancelledError(Exception):
    """Raised when the user intentionally cancels an interactive operation."""
