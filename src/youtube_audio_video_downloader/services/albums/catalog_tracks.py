"""Read an exact album's ordered tracks from the public Apple music catalog."""

from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import Request

from youtube_audio_video_downloader.config.app_identity import http_user_agent
from youtube_audio_video_downloader.core.network import urlopen
from youtube_audio_video_downloader.services.albums.album_art_finder import _catalog_key
from youtube_audio_video_downloader.services.albums.album_names import normalize_album_name
from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names


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
    name = normalize_album_name(album_name)
    if not name:
        return []
    albums = _request("search", entity="album", media="music", term=name, limit=50)
    matches = {
        row["collectionId"]: row for row in albums
        if row.get("collectionId")
        and _catalog_key(normalize_album_name(row.get("collectionName"))) == _catalog_key(name)
        and (not release_year or str(row.get("releaseDate") or "")[:4] == release_year)
    }
    if len(matches) != 1:
        return []
    collection_id, album = next(iter(matches.items()))
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
        title = str(row.get("trackName") or "").strip()
        artists = format_artist_names(str(row.get("artistName") or ""))
        duration = round(float(row.get("trackTimeMillis") or 0) / 1000)
        if not title or not artists or duration <= 0:
            return []
        result.append({
            "title": title, "artists": artists, "duration_seconds": duration,
            "source": "Apple Music catalog",
        })
    return result
