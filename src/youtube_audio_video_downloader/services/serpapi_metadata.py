"""Optional SerpApi-backed Google evidence for missing song metadata."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from youtube_audio_video_downloader.config.app_identity import http_user_agent
from youtube_audio_video_downloader.services.album_names import normalize_album_name
from youtube_audio_video_downloader.utils.artist_name_formatter import (
    format_artist_names,
)


SERPAPI_API_KEY_ENV = "SERPAPI_API_KEY"
SERPAPI_SEARCH_URL = "https://serpapi.com/search.json"

_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_RELEASE_YEAR = re.compile(
    r"\b(?:release(?:d|\s+date|\s+year)?|published)[^|;\n]{0,60}?"
    r"((?:19|20)\d{2})\b",
    re.I,
)
_EXPLICIT_ALBUM = re.compile(
    r"\b(?:film|movie|album)\s*(?:name\s*)?(?:is\s+|[-:–—]\s*)"
    r"[\"']?([^|;\n.]{2,100}?)[\"']?(?=\s*(?:[|;\n.]|$))",
    re.I,
)
_PART_OF_ALBUM = re.compile(
    r"\b(?:part of|from)\s+(?:the\s+)?(?:album|film|movie)\s+"
    r"[\"']?([^|;\n.]{2,100}?)[\"']?(?=\s*(?:[|;\n.]|$))",
    re.I,
)


def configure_serpapi_environment(api_key: str = "") -> None:
    """Expose the saved key only to in-process SerpApi requests."""

    key = str(api_key or "").strip()
    if key:
        os.environ[SERPAPI_API_KEY_ENV] = key
    else:
        os.environ.pop(SERPAPI_API_KEY_ENV, None)


def serpapi_is_configured() -> bool:
    return bool(os.environ.get(SERPAPI_API_KEY_ENV, "").strip())


def find_serpapi_song_metadata(
    song_title: str,
    artists: str = "",
    timeout: float = 15.0,
) -> dict[str, str]:
    """Return explicit Google evidence for one exact title/artist identity.

    Search snippets are accepted only when they contain the requested title and
    artist identity and explicitly associate the song with an album, film, or
    movie. This service never guesses an album from an unrelated result title.
    """

    title = _display(song_title)
    artist_text = format_artist_names(artists)
    api_key = os.environ.get(SERPAPI_API_KEY_ENV, "").strip()
    if not title or not api_key:
        return {}

    query = " ".join(
        part
        for part in (
            f'"{title}"',
            f'"{artist_text}"' if artist_text else "",
            "song album film movie release year",
        )
        if part
    )
    params = urlencode(
        {
            "engine": "google",
            "q": query,
            "api_key": api_key,
            "gl": "in",
            "hl": "en",
            "num": "10",
            "safe": "active",
        }
    )
    request = Request(
        f"{SERPAPI_SEARCH_URL}?{params}",
        headers={"User-Agent": http_user_agent()},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed SerpApi host
            payload = json.load(response)
    except (HTTPError, URLError, OSError, ValueError, TypeError) as exc:
        # Never print the exception or request URL: either may contain the API key.
        print(f"[SERPAPI-UNAVAILABLE] Google metadata request failed ({type(exc).__name__})")
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("error"):
        print("[SERPAPI-UNAVAILABLE] Google metadata request was rejected")
        return {}

    match = extract_serpapi_song_metadata(payload, title, artist_text)
    if match:
        print(
            f"[SERPAPI-MATCH] {title}: album={match['album']}"
            + (f" | year={match['year']}" if match.get("year") else "")
            + f" | evidence={match.get('evidence_count', '1')} result(s)"
        )
    else:
        print(f"[SERPAPI-NO-MATCH] {title}: no exact album/movie evidence")
    return match


def find_serpapi_album_art(
    album_name: str,
    release_year: str = "",
    timeout: float = 15.0,
    *,
    exclude_url: str = "",
) -> str:
    """Return the first square original from authenticated Google Images."""

    album = normalize_album_name(album_name)
    api_key = os.environ.get(SERPAPI_API_KEY_ENV, "").strip()
    if not album or not api_key:
        return ""
    query = " ".join(part for part in (album, str(release_year).strip(), "album art") if part)
    params = urlencode(
        {
            "engine": "google_images",
            "q": query,
            "api_key": api_key,
            "gl": "in",
            "hl": "en",
            "safe": "active",
            "imgar": "s",
        }
    )
    request = Request(
        f"{SERPAPI_SEARCH_URL}?{params}",
        headers={"User-Agent": http_user_agent()},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed SerpApi host
            payload = json.load(response)
    except (HTTPError, URLError, OSError, ValueError, TypeError) as exc:
        print(f"[SERPAPI-UNAVAILABLE] Google Images request failed ({type(exc).__name__})")
        return ""
    if not isinstance(payload, dict) or payload.get("error"):
        print("[SERPAPI-UNAVAILABLE] Google Images request was rejected")
        return ""
    results = payload.get("images_results")
    if not isinstance(results, list):
        return ""
    for result in results:
        if not isinstance(result, Mapping) or result.get("unsafe") is True:
            continue
        original = str(result.get("original") or "").strip()
        if not original.startswith(("https://", "http://")) or original == exclude_url:
            continue
        try:
            width = int(result.get("original_width") or 0)
            height = int(result.get("original_height") or 0)
        except (TypeError, ValueError):
            continue
        if width > 0 and width == height:
            print(f"[SERPAPI-ART] {album}: authenticated Google Images match")
            return original
    print(f"[SERPAPI-NO-ART] {album}: no safe square original image")
    return ""


def extract_serpapi_song_metadata(
    payload: Mapping[str, object], song_title: str, artists: str = ""
) -> dict[str, str]:
    """Extract a conservative identity from an already-fetched SerpApi response."""

    title = _display(song_title)
    artist_text = format_artist_names(artists)
    title_key = _key(title)
    artist_keys = tuple(_key(part) for part in _artist_parts(artist_text))
    evidence: list[tuple[str, str, bool]] = []

    knowledge = payload.get("knowledge_graph")
    if isinstance(knowledge, Mapping):
        context = _mapping_text(knowledge)
        album = _explicit_mapping_album(knowledge) or _album_from_text(
            context, title, artist_text
        )
        if album and _identity_matches(context, title_key, artist_keys):
            evidence.append((album, _year_from_mapping(knowledge, context), True))

    for result in _result_mappings(payload):
        context = _mapping_text(result)
        if not _identity_matches(context, title_key, artist_keys):
            continue
        album = _explicit_mapping_album(result) or _album_from_text(
            context, title, artist_text
        )
        if album:
            evidence.append((album, _year_from_mapping(result, context), False))

    if not evidence:
        return {}

    album_votes = Counter(_key(album) for album, _year, _structured in evidence)
    selected_key, selected_count = album_votes.most_common(1)[0]
    selected = [item for item in evidence if _key(item[0]) == selected_key]
    # One result is sufficient only when SerpApi supplied a structured album/film
    # field. Free-text snippets require independent agreement from two results.
    if selected_count < 2 and not any(structured for _album, _year, structured in selected):
        return {}

    album = normalize_album_name(
        next(
            (value for value, _year, _structured in selected if not value.isupper()),
            selected[0][0],
        )
    )
    years = Counter(year for _album, year, _structured in selected if year)
    year = years.most_common(1)[0][0] if years else ""
    return {
        "title": title,
        "album": album,
        "artists": artist_text,
        "year": year,
        "source": "SerpApi Google Search",
        "evidence_count": str(selected_count),
    }


def _result_mappings(payload: Mapping[str, object]) -> Iterable[Mapping[str, object]]:
    for key in ("organic_results", "video_results", "inline_videos"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, Mapping):
                yield value


def _mapping_text(value: Mapping[str, object]) -> str:
    parts: list[str] = []
    for key in (
        "title", "type", "artist", "artists", "album", "film", "movie",
        "description", "snippet", "date", "release_date", "source",
    ):
        item = value.get(key)
        if isinstance(item, (str, int, float)) and str(item).strip():
            parts.append(str(item).strip())
    return " | ".join(parts)


def _explicit_mapping_album(value: Mapping[str, object]) -> str:
    for key in ("album", "film", "movie"):
        album = _clean_album(value.get(key))
        if album:
            return album
    return ""


def _album_from_text(text: str, title: str, artists: str) -> str:
    # Official music results commonly use "Song - FILM | Singer" titles.
    first_segment = text.split(" | ", 1)[0]
    parts = [part.strip(" -–—|\t") for part in re.split(r"\s+[-–—]\s+", first_segment)]
    if len(parts) >= 2 and _key(parts[0]) == _key(title):
        for candidate in parts[1:]:
            cleaned = _clean_album(candidate)
            if cleaned and _key(cleaned) not in {_key(title), _key(artists)}:
                return cleaned

    for pattern in (_EXPLICIT_ALBUM, _PART_OF_ALBUM):
        match = pattern.search(text)
        if match:
            album = _clean_album(match.group(1))
            if album:
                return album

    return ""


def _clean_album(value: object) -> str:
    text = _display(value).strip(" \"'|:;,-–—")
    text = re.sub(r"\s+(?:released|release date|release year)\b.*$", "", text, flags=re.I)
    if not text or text.casefold() in {"song", "music", "video"}:
        return ""
    return normalize_album_name(text)


def _year_from_mapping(value: Mapping[str, object], context: str) -> str:
    for key in ("release_year", "release_date", "year"):
        match = _YEAR.search(str(value.get(key) or ""))
        if match:
            return match.group(1)
    match = _RELEASE_YEAR.search(context)
    return match.group(1) if match else ""


def _identity_matches(context: str, title_key: str, artist_keys: tuple[str, ...]) -> bool:
    context_key = _key(context)
    if not title_key or title_key not in context_key:
        return False
    if not artist_keys:
        return True
    return all(part in context_key for part in artist_keys)


def _artist_parts(value: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"\s*(?:,|&|\band\b|\bfeat(?:uring)?\.?\b)\s*", value, flags=re.I)
        if part.strip()
    ]


def _display(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
