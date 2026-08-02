"""Timestamp parsing and formatting utilities."""

from __future__ import annotations

import re

_TIMESTAMP_PATTERN = re.compile(r"^\d+(?::\d{1,2}){0,2}$")


def looks_like_timestamp(value: str) -> bool:
    """Return True when text looks like seconds, MM:SS, or HH:MM:SS."""

    return bool(_TIMESTAMP_PATTERN.match(str(value or "").strip()))


def parse_timestamp_to_seconds(value: str | int | float) -> int:
    """Parse seconds, MM:SS, or HH:MM:SS into total seconds."""

    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError("Timestamp cannot be negative")
        return int(value)

    text = str(value or "").strip()
    if not text:
        raise ValueError("Timestamp cannot be empty")

    parts = text.split(":")
    if len(parts) > 3 or not all(part.strip().isdigit() for part in parts):
        raise ValueError(f"Invalid timestamp format: {value!r}")

    numbers = [int(part) for part in parts]
    if len(numbers) == 1:
        hours, minutes, seconds = 0, 0, numbers[0]
    elif len(numbers) == 2:
        hours, minutes, seconds = 0, numbers[0], numbers[1]
    else:
        hours, minutes, seconds = numbers

    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"Invalid timestamp value: {value!r}")

    return hours * 3600 + minutes * 60 + seconds


def format_seconds_as_timestamp(total_seconds: int) -> str:
    """Format seconds as HH:MM:SS."""

    if total_seconds < 0:
        total_seconds = 0

    hours, remainder = divmod(int(total_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def normalize_timestamp(value: str | int | float) -> str:
    """Normalize seconds/MM:SS/HH:MM:SS into HH:MM:SS."""

    return format_seconds_as_timestamp(parse_timestamp_to_seconds(value))
