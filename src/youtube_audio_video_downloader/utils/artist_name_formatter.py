"""Artist-name formatting utilities.

These helpers normalize copied artist strings into the comma-separated format used by
this project for filenames and ID3 metadata.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_SUPPORTED_TEXT_SEPARATORS = (" · ", " and ", " & ")


def format_artist_names(input_string: str) -> str:
    """Normalize a free-form artist string into comma-separated display names.

    The formatter is intentionally conservative and follows the utility behavior
    requested for this project:

    - ``" · "`` becomes ``, ``
    - ``" and "`` becomes ``, ``
    - ``" & "`` becomes ``, ``
    - comma-separated artist names remain comma-separated
    - dotted initials such as ``K.K.`` or ``A. R. Rahman`` are preserved
    - normal names are converted to title case

    Examples:
        ``"Kumar Sanu, Udit Narayan and Alka Yagnik"`` becomes
        ``"Kumar Sanu, Udit Narayan, Alka Yagnik"``.

        ``"Sonu Nigam & Sunidhi Chauhan"`` becomes
        ``"Sonu Nigam, Sunidhi Chauhan"``.
    """

    raw_text = str(input_string or "").strip()
    if not raw_text:
        return ""

    processed_text = re.sub(
        r"\s*(?:&|\band\b|Â·|·)\s*",
        ", ",
        raw_text,
        flags=re.IGNORECASE,
    )
    parts = [part.strip() for part in processed_text.split(",") if part.strip()]
    formatted_parts = [_format_single_artist(part) for part in parts]
    return ", ".join(formatted_parts)


def format_artists_names(input_string: str) -> str:
    """Backward-compatible alias for the older helper name."""

    return format_artist_names(input_string)


def _replace_text_separators(value: str, separators: Iterable[str]) -> str:
    """Replace supported artist delimiters with a comma delimiter."""

    processed_value = value
    for separator in separators:
        processed_value = processed_value.replace(separator, ", ")
    return processed_value


def _format_single_artist(artist: str) -> str:
    """Format one artist name while preserving common initials/acronyms."""

    cleaned_artist = " ".join(str(artist or "").strip().split())
    if not cleaned_artist:
        return ""

    # Match the user's requested behavior: if a part contains a period, treat it
    # as an initial-bearing name and preserve the casing exactly as provided.
    # Examples: K.K., A. R. Rahman, A. R. Rehman.
    if "." in cleaned_artist:
        return cleaned_artist

    # Preserve short all-uppercase stage names/acronyms such as KK.
    if cleaned_artist.isupper() and len(cleaned_artist.replace(" ", "")) <= 3:
        return cleaned_artist

    return cleaned_artist.title()
