"""Artist-name formatting utilities.

These helpers normalize copied artist strings into the comma-separated format used by
this project for filenames and ID3 metadata.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_SUPPORTED_TEXT_SEPARATORS = (" · ", " and ", " & ")

_KNOWN_ARTIST_ALIASES = {
    "abhijeet": "Abhijeet Bhattacharya",
    "abhijeet bhattacharya": "Abhijeet Bhattacharya",
    "arijit": "Arijit Singh",
    "arijit singh": "Arijit Singh",
    "ar rahman": "AR Rahman",
    "kk": "KK",
}
_KNOWN_COMPACT_ALIASES = {
    "arrahman": "AR Rahman",
    "kk": "KK",
}


def format_artist_names(input_string: str) -> str:
    """Normalize a free-form artist string into comma-separated display names.

    The formatter is intentionally conservative and follows the utility behavior
    requested for this project:

    - ``" · "`` becomes ``, ``
    - ``" and "`` becomes ``, ``
    - ``" & "`` becomes ``, ``
    - comma-separated artist names remain comma-separated
    - dotted initials such as ``K.K.`` or ``A. R. Rahman`` lose punctuation
      and adjacent initials are joined
    - known shortened credits are expanded to their canonical full name
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
    return ", ".join(dict.fromkeys(part for part in formatted_parts if part))


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
    """Format one artist name with punctuation-free initials and known aliases."""

    cleaned_artist = " ".join(str(artist or "").strip().split())
    if not cleaned_artist:
        return ""

    compact_key = re.sub(r"[^a-z0-9]+", "", cleaned_artist.casefold())
    if compact_key in _KNOWN_COMPACT_ALIASES:
        return _KNOWN_COMPACT_ALIASES[compact_key]

    if re.match(r"^(?:[A-Za-z]\.\s*){2,}", cleaned_artist):
        cleaned_artist = re.sub(r"(?<=\b[A-Za-z])\.", "", cleaned_artist)
    tokens = cleaned_artist.split()
    initial_count = 0
    while initial_count < len(tokens) and len(tokens[initial_count]) == 1:
        initial_count += 1
    if initial_count >= 2:
        tokens[:initial_count] = ["".join(tokens[:initial_count]).upper()]
    cleaned_artist = " ".join(tokens)

    alias_key = re.sub(r"[^a-z0-9]+", " ", cleaned_artist.casefold()).strip()
    if alias_key in _KNOWN_ARTIST_ALIASES:
        return _KNOWN_ARTIST_ALIASES[alias_key]

    if "." in cleaned_artist:
        return cleaned_artist

    # Preserve short all-uppercase stage names/acronyms such as KK.
    if cleaned_artist.isupper() and len(cleaned_artist.replace(" ", "")) <= 3:
        return cleaned_artist

    return " ".join(
        word if word.isupper() and len(word) <= 4 else word.title()
        for word in cleaned_artist.split()
    )
