"""JSON utility helpers used by maintenance commands.

The main downloader loaders stay strict where required. These helpers are more
forgiving because they are used by user-facing utility commands where copied JSON
snippets may contain ``//`` or ``/* ... */`` comments.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def strip_json_comments(text: str) -> str:
    """Remove JavaScript-style comments from JSON-like text.

    The implementation respects quoted strings, so URLs such as
    ``https://youtube.com/...`` are not damaged by the ``//`` sequence.
    """

    result: list[str] = []
    index = 0
    in_string = False
    escape = False

    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if in_string:
            result.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue

        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(text) and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2
            continue

        result.append(char)
        index += 1

    return "".join(result)


def load_json_object(path: Path, *, allow_comments: bool = True) -> dict[str, Any]:
    """Load a JSON file and ensure the root value is an object/dictionary."""

    if not path.exists():
        raise FileNotFoundError(f"JSON file not found: {path}")

    text = path.read_text(encoding="utf-8")
    if allow_comments:
        text = strip_json_comments(text)

    data: Any = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Input JSON must be an object/dictionary.")

    return data
