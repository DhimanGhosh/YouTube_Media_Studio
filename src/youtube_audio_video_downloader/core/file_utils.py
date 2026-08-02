"""Utility helpers for file names and filesystem operations."""

from __future__ import annotations

import re
from pathlib import Path

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def safe_filename(
    value: str,
    fallback: str = "untitled",
    *,
    invalid_char_replacement: str = "_",
) -> str:
    """Return a Windows-safe file name without changing normal readable text.

    ``invalid_char_replacement`` controls how Windows-forbidden filename
    characters are handled. Most legacy downloaders keep the previous
    underscore behavior, while jukebox track filenames use an empty replacement
    so metadata such as ``Raaz: The Mystery Continues`` becomes
    ``Raaz The Mystery Continues`` in the file name without changing the ID3
    album tag.
    """

    cleaned = re.sub(r'[<>:"/\\|?*]', invalid_char_replacement, value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(". ")

    if not cleaned:
        cleaned = fallback

    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}_file"

    return cleaned


def ensure_directory(path: Path) -> Path:
    """Create a directory if it does not already exist and return it."""

    path.mkdir(parents=True, exist_ok=True)
    return path
