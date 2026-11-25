"""Read an exact album's ordered tracks from the public Apple music catalog."""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode
from urllib.request import Request

from youtube_audio_video_downloader.config.app_identity import http_user_agent
from youtube_audio_video_downloader.core.network import urlopen
from youtube_audio_video_downloader.services.albums.album_art_finder import _catalog_key
from youtube_audio_video_downloader.services.albums.album_names import normalize_album_name
from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names
from youtube_audio_video_downloader.utils.music_text import clean_song_title, music_words


def _request(endpoint: str, **params: object) -> list[dict]:
    request = Request(
        f"https://itunes.apple.com/{endpoint}?" + urlencode({"country": "IN", **params}),
        headers={"User-Agent": http_user_agent()},
    )
    with urlopen(request, timeout=12) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        return []
    return [row for row in payload.get("results", []) if isinstance(row, dict)]


def find_catalog_album_tracks(album_name: str, release_year: str = "") -> list[dict]:
    """Require an unambiguous album identity, year, and complete ordered track list."""
    return find_catalog_album_release(album_name, release_year).get("tracks", [])


def find_catalog_album_release(
    album_name: str, release_year: str = "", *, expected_titles: tuple[str, ...] = (),
) -> dict:
    """Resolve a release and its artwork together, using track evidence for namesakes."""
    name = normalize_album_name(album_name)
    if not name:
        return {}
    albums = _request("search", entity="album", media="music", term=name, limit=50)
    matches = {
        row["collectionId"]: row for row in albums
        if row.get("collectionId")
        and _catalog_key(normalize_album_name(row.get("collectionName"))) == _catalog_key(name)
        and (not release_year or str(row.get("releaseDate") or "")[:4] == release_year)
    }
    if len(matches) > 1 and not expected_titles:
        # A namesake Single/EP is not the full album requested here. Keep
        # ambiguity protection when more than one full release remains.
        full_releases = {key: row for key, row in matches.items()
                         if not re.search(r"\s-\s(?:single|ep)\s*$", str(row.get("collectionName") or ""), re.I)}
        if len(full_releases) == 1:
            matches = full_releases
    if not matches or (not expected_titles and len(matches) != 1) or len(matches) > 5:
        return {}
    expected = {music_words(clean_song_title(title, name)) for title in expected_titles if title.strip()}
    releases = []
    for collection_id, album in matches.items():
        tracks = _collection_tracks(collection_id, album, name)
        if not tracks:
            continue
        available = {music_words(track["title"]) for track in tracks}
        score = len(expected & available)
        if expected and score < max(min(2, len(expected)), (len(expected) + 1) // 2):
            continue
        releases.append((score, {
            "collection_id": collection_id, "album": name,
            "year": str(album.get("releaseDate") or "")[:4], "tracks": tracks,
            "album_art": str(album.get("artworkUrl100") or "").replace("/100x100bb.", "/1200x1200bb."),
        }))
    releases.sort(key=lambda release: release[0], reverse=True)
    if not releases or (len(releases) > 1 and releases[0][0] == releases[1][0]):
        return {}
    return releases[0][1]


def _collection_tracks(collection_id: object, album: dict, name: str) -> list[dict]:
    if str(album.get("primaryGenreName") or "").casefold() in {
        "tamil", "telugu", "kannada", "malayalam", "marathi", "punjabi", "gujarati",
    }:
        return []
    rows = _request("lookup", id=collection_id, entity="song", limit=200)
    tracks = [
        row for row in rows
        if row.get("wrapperType") == "track" and row.get("kind") == "song"
        and row.get("collectionId") == collection_id
    ]
    if not tracks or len(tracks) != int(album.get("trackCount") or 0):
        return []
    tracks.sort(key=lambda row: (int(row.get("discNumber") or 1), int(row.get("trackNumber") or 0)))
    result = []
    for row in tracks:
        title = clean_song_title(str(row.get("trackName") or ""), name)
        artists = format_artist_names(str(row.get("artistName") or ""))
        duration = round(float(row.get("trackTimeMillis") or 0) / 1000)
        if not title or not artists or duration <= 0:
            return []
        result.append({
            "title": title, "artists": artists, "duration_seconds": duration,
            "source": "Apple Music catalog",
        })
    return result
