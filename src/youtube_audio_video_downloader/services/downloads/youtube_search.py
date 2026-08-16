"""Small yt-dlp-backed searches used by the desktop form helpers."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from youtube_audio_video_downloader.services.downloads.youtube_track_extractor import (
    _chapters_to_timestamp_text,
    description_to_timestamp_text,
)
from youtube_audio_video_downloader.services.albums.wikipedia_tracks import find_wikipedia_tracks
from youtube_audio_video_downloader.services.albums.album_names import normalize_album_name
from youtube_audio_video_downloader.utils.track_timestamp_parser import parse_tracks_text


def album_jukebox_query(album_name: str, release_year: str = "") -> str:
    """Build the YouTube query used for a full-album/jukebox source."""
    name = normalize_album_name(album_name)
    if not name:
        raise ValueError("Enter the album or jukebox name before searching YouTube.")
    year = release_year.strip()
    return f"{name}{f' {year}' if year else ''} full album audio jukebox"


def find_album_jukebox_video(
    album_name: str,
    release_year: str = "",
    *,
    exclude_url: str = "",
) -> dict[str, str]:
    """Return the best validated result, optionally skipping the current URL."""
    import yt_dlp

    query = album_jukebox_query(album_name, release_year)
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": True,
    }
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with yt_dlp.YoutubeDL(options) as downloader:
        for search_query in album_jukebox_queries(album_name, release_year):
            result = downloader.extract_info(f"ytsearch20:{search_query}", download=False)
            for entry in result.get("entries", []) if isinstance(result, dict) else []:
                if not isinstance(entry, dict):
                    continue
                identity = str(entry.get("id") or entry.get("url") or "")
                if identity and identity in seen_ids:
                    continue
                seen_ids.add(identity)
                entries.append(entry)
    candidates = rank_jukebox_candidates(
        [entry for entry in entries if isinstance(entry, dict)],
        album_name=album_name.strip(),
    )
    if not candidates:
        raise LookupError(f'No YouTube result was found for "{query}".')
    try:
        expected_tracks = find_wikipedia_tracks(album_name, release_year)
    except Exception:
        expected_tracks = []
    with yt_dlp.YoutubeDL(
        {"quiet": True, "no_warnings": True, "skip_download": True}
    ) as downloader:
        for candidate in candidates:
            video_id = str(candidate.get("id") or "").strip()
            url = str(candidate.get("webpage_url") or candidate.get("url") or "").strip()
            if not url.lower().startswith(("http://", "https://")) and video_id:
                url = f"https://www.youtube.com/watch?v={video_id}"
            if not url:
                continue
            if _youtube_video_identity(url) == _youtube_video_identity(exclude_url):
                continue
            try:
                detail = downloader.extract_info(url, download=False)
                description = str(detail.get("description") or "")
                searchable_metadata = " ".join(
                    (str(detail.get("title") or ""), description[:1500])
                )
                if _has_unsupported_language(searchable_metadata):
                    continue
                try:
                    timestamp_text = description_to_timestamp_text(description)
                except LookupError:
                    chapters = (
                        detail.get("chapters")
                        if isinstance(detail.get("chapters"), list)
                        else []
                    )
                    timestamp_text = _chapters_to_timestamp_text(chapters)
                if (
                    expected_tracks
                    and not _trusted_official_jukebox(candidate)
                    and not _description_matches_album_tracks(
                        timestamp_text, expected_tracks
                    )
                ):
                    continue
            except Exception:
                continue
            return {
                "title": str(candidate.get("title") or "").strip(),
                "url": url,
                "channel": str(candidate.get("channel") or candidate.get("uploader") or "").strip(),
                "views": str(int(candidate.get("view_count") or 0)),
            }
    raise LookupError(
        f'No different popular YouTube jukebox with an extractable timestamp list '
        f'was found for "{query}".'
    )


def album_jukebox_queries(album_name: str, release_year: str = "") -> list[str]:
    """Return ordered YouTube queries for official full-album audio sources."""

    name = normalize_album_name(album_name)
    year = release_year.strip()
    year_base = f"{name} {year}" if year else ""
    queries = [
        f"{name} full album audio jukebox",
        f"{name} official audio jukebox",
        f"{name} full album songs official",
        f"{name} audio jukebox official label",
        f"{name} audio jukebox Sony Music India",
    ]
    if year_base:
        queries.extend(
            [
                f"{year_base} full album audio jukebox",
                f"{year_base} official audio jukebox",
                f"{year_base} full album songs official",
            ]
        )
    deduped: list[str] = []
    for query in queries:
        if query not in deduped:
            deduped.append(query)
    return deduped


def _youtube_video_identity(url: str) -> str:
    """Normalize common YouTube URL shapes for current-result exclusion."""
    text = str(url or "").strip()
    match = re.search(
        r"(?:[?&]v=|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})",
        text,
    )
    return match.group(1) if match else text


def _has_unsupported_language(text: str) -> bool:
    """Reject videos explicitly labeled outside Hindi, Bengali, or English."""
    unsupported = (
        "odia", "oriya", "punjabi", "telugu", "tamil", "malayalam",
        "kannada", "marathi", "gujarati", "assamese", "bhojpuri",
    )
    lowered = text.lower()
    return any(re.search(rf"\b{language}\b", lowered) for language in unsupported)


def rank_jukebox_candidates(
    entries: list[dict[str, Any]], *, album_name: str
) -> list[dict[str, Any]]:
    """Require album-title relevance, then rank verified jukeboxes and views."""
    candidates = []
    album_key = " ".join(re.findall(r"[a-z0-9]+", album_name.lower()))
    for entry in entries:
        title = str(entry.get("title") or "").lower()
        title_key = " ".join(re.findall(r"[a-z0-9]+", title))
        duration = float(entry.get("duration") or 0)
        if not album_key or album_key not in title_key:
            continue
        if not any(term in title for term in ("jukebox", "full album", "all songs")):
            continue
        if "video jukebox" in title or _is_variant_collection(title):
            continue
        if duration and duration < 600:
            continue
        candidates.append(entry)
    return sorted(
        candidates,
        key=lambda entry: (
            _album_focus_score(entry, album_name),
            _official_jukebox_score(entry),
            bool(entry.get("channel_is_verified")),
            int(entry.get("view_count") or 0),
        ),
        reverse=True,
    )


def _album_focus_score(entry: dict[str, Any], album_name: str) -> int:
    title_key = _title_key(str(entry.get("title") or ""))
    album_key = _title_key(album_name)
    if not title_key or not album_key:
        return 0
    score = 0
    if title_key == album_key or title_key.startswith(album_key + " audio jukebox"):
        score += 10
    elif title_key.startswith(album_key):
        score += 7
    elif album_key in title_key:
        score += 2
    if title_key.startswith(("best of ", "mix ", "top ", "hits of ")):
        score -= 6
    return score


def _official_jukebox_score(entry: dict[str, Any]) -> int:
    title = str(entry.get("title") or "").casefold()
    channel = str(entry.get("channel") or entry.get("uploader") or "").casefold()
    score = 0
    if entry.get("channel_is_verified"):
        score += 5
    if "audio jukebox" in title:
        score += 4
    if "official" in title:
        score += 2
    if any(label in channel for label in ("sony music", "vevo", "t-series", "saregama")):
        score += 3
    if any(noise in title for noise in ("movie all songs", "hit song", "hindi best song")):
        score -= 2
    return score


def _trusted_official_jukebox(entry: dict[str, Any]) -> bool:
    """Accept a verified label audio jukebox even when Wikipedia rows are noisy."""

    title = str(entry.get("title") or "").casefold()
    return bool(entry.get("channel_is_verified")) and "audio jukebox" in title


_VARIANT_WORDS = {
    "remix", "remixes", "reprise", "version", "versions", "mix", "lofi", "mashup",
    "instrumental", "acoustic", "unplugged", "jhankar", "sad",
}


def _is_variant_collection(title: str) -> bool:
    key = " ".join(re.findall(r"[a-z0-9]+", title.lower()))
    return bool(set(key.split()) & {"remix", "remixes", "lofi", "mashup"})


def _description_matches_album_tracks(
    timestamp_text: str,
    expected_tracks: list[dict[str, object]],
) -> bool:
    """Reject timestamp lists dominated by remixes or unrelated album tracks."""
    parsed = parse_tracks_text(timestamp_text, title_case=False).get("tracks", [])
    candidate_titles = [
        str(next(iter(track)))
        for track in parsed
        if isinstance(track, dict) and track
    ]
    expected_titles = [str(track.get("title") or "") for track in expected_tracks]
    expected_core = [title for title in expected_titles if not _is_variant_title(title)]
    if not candidate_titles:
        return False

    variant_count = sum(_is_variant_title(title) for title in candidate_titles)
    if len(expected_core) >= 3 and variant_count / len(candidate_titles) >= 0.5:
        return False

    candidate_core = [title for title in candidate_titles if not _is_variant_title(title)]
    matched = sum(
        any(_titles_match(candidate, expected) for candidate in candidate_core)
        for expected in expected_core
    )
    required = min(3, len(expected_core))
    return matched >= required


def _is_variant_title(title: str) -> bool:
    return bool(set(_title_key(title).split()) & _VARIANT_WORDS)


def _title_key(title: str) -> str:
    key = " ".join(re.findall(r"[a-z0-9]+", title.lower()))
    noise = {"song", "full", "audio", "official", "lyrical", "lyrics", "track"}
    return " ".join(word for word in key.split() if word not in noise)


def _titles_match(left: str, right: str) -> bool:
    left_key, right_key = _title_key(left), _title_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key or left_key in right_key or right_key in left_key:
        return True
    return SequenceMatcher(None, left_key, right_key).ratio() >= 0.82
