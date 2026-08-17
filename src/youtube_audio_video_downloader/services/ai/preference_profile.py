"""Versioned, device-local preference profile derived from user-created playlists."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, TypedDict

from youtube_audio_video_downloader.config.app_storage import resolve_data_directory
from youtube_audio_video_downloader.services.media.media_library import LibraryItem, split_artists


PROFILE_VERSION = 1
MAX_PROFILE_PLAYLISTS = 200
MAX_PROFILE_TRACKS = 2_000


class PreferenceTrack(TypedDict):
    title: str
    artists: list[str]
    album: str
    year: int | None


class PreferencePlaylist(TypedDict):
    name: str
    tracks: list[PreferenceTrack]


class PreferenceProfile(TypedDict):
    version: int
    fingerprint: str
    source: str
    playlists: list[PreferencePlaylist]


def preference_profile_path(data_directory: str | Path | None = None) -> Path:
    root = Path(data_directory) if data_directory is not None else resolve_data_directory()
    return root / "ai" / "preference-profile.json"


def update_preference_profile(
    items: Iterable[LibraryItem],
    playlists: Mapping[str, Iterable[str]],
    *,
    data_directory: str | Path | None = None,
) -> PreferenceProfile:
    """Build and atomically persist the current user's path-free playlist profile."""

    by_path = {_path_key(item.path): item for item in items}
    remaining = MAX_PROFILE_TRACKS
    profile_playlists: list[PreferencePlaylist] = []
    for name in sorted(playlists, key=str.casefold)[:MAX_PROFILE_PLAYLISTS]:
        clean_name = " ".join(str(name).split())[:160]
        if not clean_name or remaining <= 0:
            continue
        tracks: list[PreferenceTrack] = []
        seen: set[str] = set()
        for raw_path in playlists[name]:
            item = by_path.get(_path_key(str(raw_path)))
            if item is None:
                continue
            identity = "|".join(
                [item.title.casefold(), item.artists.casefold(), item.album.casefold()]
            )
            if identity in seen:
                continue
            seen.add(identity)
            tracks.append(
                {
                    "title": item.title,
                    "artists": split_artists(item.artists),
                    "album": item.album,
                    "year": item.year,
                }
            )
            remaining -= 1
            if remaining <= 0:
                break
        if tracks:
            profile_playlists.append({"name": clean_name, "tracks": tracks})
    canonical = json.dumps(profile_playlists, ensure_ascii=False, sort_keys=True)
    profile: PreferenceProfile = {
        "version": PROFILE_VERSION,
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "source": "user-created playlist names and member-track metadata",
        "playlists": profile_playlists,
    }
    target = preference_profile_path(data_directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    if load_preference_profile(data_directory).get("fingerprint") == profile["fingerprint"]:
        return profile
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(target)
    return profile


def load_preference_profile(
    data_directory: str | Path | None = None,
) -> PreferenceProfile:
    target = preference_profile_path(data_directory)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if payload.get("version") != PROFILE_VERSION or not isinstance(
            payload.get("playlists"), list
        ):
            raise ValueError("unsupported preference profile")
        return payload
    except (OSError, ValueError, AttributeError, TypeError):
        return {
            "version": PROFILE_VERSION,
            "fingerprint": "",
            "source": "user-created playlist names and member-track metadata",
            "playlists": [],
        }


def remove_preference_profile(data_directory: str | Path | None = None) -> None:
    preference_profile_path(data_directory).unlink(missing_ok=True)


def _path_key(path: str) -> str:
    try:
        return str(Path(path).expanduser().resolve()).casefold()
    except OSError:
        return str(path).strip().casefold()
