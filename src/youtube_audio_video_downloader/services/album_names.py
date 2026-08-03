"""Canonical album-name cleanup shared by searches, tags, folders, and filenames."""

from __future__ import annotations

import re


_RELEASE_SUFFIXES = (
    re.compile(r"\s*-\s*(?:ep|single)\s*$", re.I),
    re.compile(r"\s*[\[(]\s*original\s*[\])]\s*$", re.I),
    re.compile(
        r"\s*\((?:original\s+)?(?:motion\s+picture\s+)?soundtrack\)\s*$",
        re.I,
    ),
    re.compile(
        r"\s*(?:original\s+)?(?:motion\s+picture\s+)?soundtrack\s*$",
        re.I,
    ),
    re.compile(
        r"\s*\(\s*\d{4}\s+(?:film|soundtrack(?:\s+album)?|album)\s*\)\s*$",
        re.I,
    ),
)

_CANONICAL_FOLDER_YEAR = re.compile(r"^(.*?)\s*\(\s*(\d{4})\s*\)\s*$")
_LANGUAGE_FILM_SUFFIX = re.compile(
    r"\s*\(\s*(?:19|20)\d{2}\s+"
    r"(Hindi|Bengali|Tamil|Telugu|Malayalam|Kannada|Marathi|Punjabi)\s+film\s*\)\s*$",
    re.I,
)


def normalize_album_name(value: object) -> str:
    """Remove storefront soundtrack/EP qualifiers while preserving the album title."""

    cleaned = " ".join(str(value or "").strip().split())
    # Wikipedia uses language-qualified disambiguation for same-name films,
    # e.g. Highway (2014 Hindi film) and Highway (2014 Bengali film).  The
    # release year belongs in the canonical trailing year slot, but language is
    # essential album identity and must be retained.
    cleaned = _LANGUAGE_FILM_SUFFIX.sub(
        lambda match: f" ({match.group(1).title()})", cleaned
    )
    previous = None
    while cleaned and cleaned != previous:
        previous = cleaned
        for suffix in _RELEASE_SUFFIXES:
            cleaned = suffix.sub("", cleaned).strip(" -")
    return cleaned


def split_album_folder_name(value: object) -> tuple[str, str]:
    """Return a clean album base and an optional canonical folder year."""

    cleaned = normalize_album_name(value)
    match = _CANONICAL_FOLDER_YEAR.fullmatch(cleaned)
    if match:
        return normalize_album_name(match.group(1)), match.group(2)
    return cleaned, ""


def canonical_album_name(value: object, release_year: object) -> str:
    """Return the canonical embedded/folder value ``Album (YYYY)`` when verified."""

    album, album_year = split_album_folder_name(value)
    year_match = re.search(r"\b((?:19|20)\d{2})\b", str(release_year or ""))
    year = year_match.group(1) if year_match else album_year
    return f"{album} ({year})" if album and year else album
