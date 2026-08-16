"""Find, review, and apply consistent artist identities across a media library."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from youtube_audio_video_downloader.services.media.media_metadata import (
    read_media_metadata,
    replace_media_metadata,
)
from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names


@dataclass(frozen=True, slots=True)
class ArtistRenameSuggestion:
    detected: str
    replacement: str
    tracks: int


@dataclass(frozen=True, slots=True)
class ArtistRepairReport:
    scanned: int
    updated: tuple[Path, ...]
    failed: tuple[str, ...]


def split_artist_credits(value: str) -> list[str]:
    """Split common multi-artist separators while retaining original spellings."""

    text = re.sub(
        r"\s*(?:&|\band\b|Â·|·|;|/)\s*", ",", str(value or ""), flags=re.I
    )
    return [part.strip() for part in text.split(",") if part.strip()]


def _credit_key(value: str) -> tuple[str, ...]:
    return tuple(
        compact
        for part in split_artist_credits(value)
        if (compact := re.sub(r"[^a-z0-9]+", "", part.casefold()))
    )


def _is_unknown_artist(value: str) -> bool:
    return _credit_key(value) in {("unknown",), ("unknownartist",)}


def suggest_artist_renames(values: Iterable[str]) -> list[ArtistRenameSuggestion]:
    """Suggest aliases plus an unambiguous longer name present in the library."""

    per_track = [split_artist_credits(value) for value in values]
    counts = Counter(part for parts in per_track for part in set(parts))
    formatted = {
        source: format_artist_names(source)
        for source in counts
        if not _is_unknown_artist(source)
    }
    candidates = set(formatted.values())
    for source, normalized in tuple(formatted.items()):
        source_tokens = normalized.casefold().split()
        # A bare given name can refer to several people (for example Vishal
        # Dadlani or Vishal Mishra). Only explicit known aliases may expand a
        # single token; library-derived prefix matching requires more context.
        if len(source_tokens) < 2:
            continue
        longer = sorted(
            {
                candidate
                for candidate in candidates
                if len(candidate.split()) > len(source_tokens)
                and candidate.casefold().split()[: len(source_tokens)] == source_tokens
            },
            key=lambda candidate: (-len(candidate.split()), candidate.casefold()),
        )
        if longer:
            longest_size = len(longer[0].split())
            longest = [name for name in longer if len(name.split()) == longest_size]
            if len(longest) == 1:
                formatted[source] = longest[0]
    suggestions = [
            ArtistRenameSuggestion(source, replacement, counts[source])
            for source, replacement in formatted.items()
            if source != replacement
    ]
    full_credits = Counter(", ".join(parts) for parts in per_track if len(parts) > 1)
    for source, tracks in full_credits.items():
        individual = ", ".join(format_artist_names(part) for part in split_artist_credits(source))
        contextual = format_artist_names(source)
        if contextual and contextual != individual:
            suggestions.append(ArtistRenameSuggestion(source, contextual, tracks))
    return sorted(
        suggestions,
        key=lambda suggestion: suggestion.detected.casefold(),
    )


def count_artist_replacement_matches(values: Iterable[str], source: str) -> int:
    """Count tracks affected by a reviewed single-name or whole-credit rule."""

    source_key = _credit_key(source)
    if not source_key:
        return 0
    if len(source_key) > 1:
        return sum(_credit_key(value) == source_key for value in values)
    return sum(source_key[0] in set(_credit_key(value)) for value in values)


def apply_artist_replacements(value: str, replacements: Mapping[str, str]) -> str:
    """Apply reviewed replacements to one credit and remove resulting duplicates."""

    exact_credits = {
        key: target.strip()
        for source, target in replacements.items()
        if len(key := _credit_key(source)) > 1 and target.strip()
    }
    value_key = _credit_key(value)
    if value_key in exact_credits:
        return format_artist_names(exact_credits[value_key])
    lookup = {
        source.casefold(): target.strip()
        for source, target in replacements.items()
        if len(_credit_key(source)) == 1
    }
    resolved: list[str] = []
    seen: set[str] = set()
    for artist in split_artist_credits(value):
        canonical = format_artist_names(lookup.get(artist.casefold(), artist))
        key = canonical.casefold()
        if canonical and key not in seen:
            resolved.append(canonical)
            seen.add(key)
    return ", ".join(resolved)


def repair_artist_metadata(
    paths: Iterable[str | Path],
    replacements: Mapping[str, str],
    *,
    progress: Callable[[int, int, Path], None] | None = None,
) -> ArtistRepairReport:
    """Apply reviewed replacements to artist and album-artist tags."""

    candidates = tuple(dict.fromkeys(Path(path).expanduser().resolve() for path in paths))
    updated: list[Path] = []
    failed: list[str] = []
    total = len(candidates)
    for index, path in enumerate(candidates, start=1):
        if progress:
            progress(index, total, path)
        try:
            metadata = read_media_metadata(path)
            artists = apply_artist_replacements(metadata.artists, replacements)
            album_artist = apply_artist_replacements(metadata.album_artist, replacements)
            changes: dict[str, str] = {}
            if artists and artists != metadata.artists:
                changes["artists"] = artists
            if album_artist and album_artist != metadata.album_artist:
                changes["album_artist"] = album_artist
            if changes:
                replace_media_metadata(path, changes)
                updated.append(path)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            failed.append(f"{path.name}: {exc}")
    return ArtistRepairReport(total, tuple(updated), tuple(failed))
