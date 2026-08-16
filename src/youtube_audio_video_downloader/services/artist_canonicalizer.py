"""Find, review, and apply consistent artist identities across a media library."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from youtube_audio_video_downloader.services.media_metadata import (
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


def suggest_artist_renames(values: Iterable[str]) -> list[ArtistRenameSuggestion]:
    """Suggest aliases plus an unambiguous longer name present in the library."""

    per_track = [split_artist_credits(value) for value in values]
    counts = Counter(part for parts in per_track for part in set(parts))
    formatted = {source: format_artist_names(source) for source in counts}
    candidates = set(formatted.values())
    for source, normalized in tuple(formatted.items()):
        source_tokens = normalized.casefold().split()
        if not source_tokens:
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
    return sorted(
        (
            ArtistRenameSuggestion(source, replacement, counts[source])
            for source, replacement in formatted.items()
            if source != replacement
        ),
        key=lambda suggestion: suggestion.detected.casefold(),
    )


def apply_artist_replacements(value: str, replacements: Mapping[str, str]) -> str:
    """Apply reviewed replacements to one credit and remove resulting duplicates."""

    lookup = {source.casefold(): target.strip() for source, target in replacements.items()}
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
