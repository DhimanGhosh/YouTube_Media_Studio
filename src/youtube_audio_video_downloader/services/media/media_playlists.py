"""Persistent path-based playlists for the local Media Library."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterable, Mapping


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlaylistAddResult:
    """Describe one playlist insertion without hiding duplicate decisions."""

    paths: list[str]
    added: int
    duplicates: int


def decode_playlists(value: object) -> dict[str, list[str]]:
    """Decode the QSettings payload while safely ignoring malformed entries."""

    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        LOGGER.warning("Could not decode saved Media Library playlists: %s", exc)
        return {}
    if not isinstance(payload, dict):
        LOGGER.warning(
            "Saved Media Library playlists are not a mapping (got %s)",
            type(payload).__name__,
        )
        return {}
    playlists: dict[str, list[str]] = {}
    for raw_name, raw_paths in payload.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_paths, list):
            continue
        playlists[name] = [
            str(path).strip() for path in raw_paths if str(path).strip()
        ]
    return playlists


def encode_playlists(playlists: Mapping[str, Iterable[str]]) -> str:
    """Return a stable JSON representation suitable for QSettings."""

    payload = {
        str(name): [str(path) for path in paths]
        for name, paths in sorted(playlists.items(), key=lambda pair: pair[0].casefold())
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def add_playlist_paths(
    existing: Iterable[str],
    incoming: Iterable[str],
    *,
    skip_duplicates: bool,
) -> PlaylistAddResult:
    """Add exact file links, optionally omitting paths already in the playlist."""

    paths = [str(path) for path in existing]
    known = {path.casefold() for path in paths}
    added = 0
    duplicates = 0
    for raw_path in incoming:
        path = str(raw_path).strip()
        if not path:
            continue
        duplicate = path.casefold() in known
        if duplicate:
            duplicates += 1
            if skip_duplicates:
                continue
        paths.append(path)
        known.add(path.casefold())
        added += 1
    return PlaylistAddResult(paths=paths, added=added, duplicates=duplicates)
