"""Find a square album-cover image URL using Google Images."""

from __future__ import annotations

import json
import re
import threading
from difflib import SequenceMatcher
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from youtube_audio_video_downloader.config.app_identity import http_user_agent
from youtube_audio_video_downloader.services.albums.album_names import normalize_album_name
from youtube_audio_video_downloader.utils.artist_name_formatter import (
    format_artist_names,
)


_IMAGE_RESULT = re.compile(
    r'\[\s*("(?:https?:)?\\?/\\?/[^"\n]+?")\s*,\s*(\d+)\s*,\s*(\d+)\s*\]'
)
_BLOCKED_HOSTS = ("gstatic.com", "google.com/images", "googleusercontent.com")
_YOUTUBE_SEARCH_LOCK = threading.Lock()


class AlbumArtNotFoundError(LookupError):
    """Raised when Google does not return a usable square cover."""


def find_catalog_song_metadata(
    song_title: str,
    artists: str = "",
    timeout: float = 12.0,
    *,
    exclude_album: str = "",
    exclude_artists: str = "",
) -> dict[str, str]:
    """Return a high-confidence song match, optionally skipping current metadata."""

    title = str(song_title or "").strip()
    artist_hint = str(artists or "").strip()
    if not title:
        return {}
    terms = " ".join(part for part in (title, artist_hint) if part)
    request = Request(
        "https://itunes.apple.com/search?country=IN&media=music&entity=song&limit=50&term="
        + quote_plus(terms),
        headers={"User-Agent": http_user_agent()},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - Apple catalog
            payload = json.load(response)
    except OSError:
        return {}
    wanted_title = _catalog_track_key(title)
    wanted_artists = _artist_names(artist_hint)
    matches: list[tuple[tuple[bool, int, float], dict[str, str], dict[str, object]]] = []
    for result in payload.get("results", []) if isinstance(payload, dict) else []:
        if not isinstance(result, dict):
            continue
        result_title = str(result.get("trackName") or "").strip()
        result_artist = str(result.get("artistName") or "").strip()
        album = normalize_album_name(result.get("collectionName"))
        candidate_title = _catalog_track_key(result_title)
        candidate_artists = _artist_names(result_artist)
        similarity = SequenceMatcher(None, wanted_title, candidate_title).ratio()
        exact_title = candidate_title == wanted_title
        artist_hits = _artist_match_count(wanted_artists, candidate_artists)
        artist_matches = _artist_identity_matches(
            wanted_artists, candidate_artists
        )
        threshold = 0.82 if wanted_artists and artist_matches else 0.93
        if not album or similarity < threshold:
            continue
        artwork = str(result.get("artworkUrl100") or "").strip()
        if artwork:
            artwork = artwork.replace("/100x100bb.", "/1200x1200bb.")
        matches.append(
            (
                (exact_title, artist_hits, similarity),
                {
                    "title": result_title,
                    "album": album,
                    "artists": format_artist_names(result_artist),
                    "year": str(result.get("releaseDate") or "")[:4],
                    "album_art": artwork,
                    "source": "Apple Music catalog",
                    "duration_seconds": str(
                        round(float(result.get("trackTimeMillis") or 0) / 1000, 3)
                    ),
                    "genre": str(result.get("primaryGenreName") or "").strip(),
                    "language": _catalog_language(result.get("primaryGenreName")),
                },
                result,
            )
        )
    if not matches:
        return {}
    matches.sort(key=lambda item: item[0], reverse=True)
    excluded_album_key = _catalog_key(exclude_album)
    excluded_artist_names = _artist_names(exclude_artists)
    selected = next(
        (
            item
            for item in matches
            if not (
                excluded_album_key
                and _catalog_key(item[1]["album"]) == excluded_album_key
            )
            and not (
                excluded_artist_names
                and _artist_names(item[1]["artists"]) == excluded_artist_names
            )
        ),
        None,
    )
    if selected is None:
        return {}
    _best_score, best, track_result = selected
    if wanted_artists and not _artist_identity_matches(
        wanted_artists, _artist_names(best["artists"])
    ):
        return {}
    # A song's releaseDate is frequently its original single/recording date,
    # not the release date of the collection shown in collectionName.  Using
    # that track date split one album into several year folders.  Resolve the
    # parent collection and use its identity, date, and cover as one unit.
    collection_id = track_result.get("collectionId")
    if collection_id:
        collection = _lookup_catalog_collection(collection_id, timeout)
        collection_name = normalize_album_name(collection.get("collectionName"))
        if collection_name:
            best["album"] = collection_name
        collection_year = str(collection.get("releaseDate") or "")[:4]
        if collection_year:
            best["year"] = collection_year
        collection_art = str(collection.get("artworkUrl100") or "").strip()
        if collection_art:
            best["album_art"] = collection_art.replace(
                "/100x100bb.", "/1200x1200bb."
            )
        collection_language = _catalog_language(collection.get("primaryGenreName"))
        if collection_language:
            best["language"] = collection_language
        collection_genre = str(collection.get("primaryGenreName") or "").strip()
        if collection_genre:
            best["genre"] = collection_genre
    return best


def _catalog_language(value: object) -> str:
    """Translate Apple's regional genre into a soundtrack language identity."""

    genre = str(value or "").strip().casefold()
    if genre == "bengali":
        return "Bengali"
    if genre in {"bollywood", "hindi"}:
        return "Hindi"
    return ""


def _lookup_catalog_collection(collection_id: object, timeout: float) -> dict[str, object]:
    """Return the parent album record for a matched Apple catalog track."""

    request = Request(
        f"https://itunes.apple.com/lookup?country=IN&id={quote_plus(str(collection_id))}",
        headers={"User-Agent": http_user_agent()},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - Apple catalog
            payload = json.load(response)
    except (OSError, ValueError):
        return {}
    for result in payload.get("results", []) if isinstance(payload, dict) else []:
        if isinstance(result, dict) and result.get("wrapperType") == "collection":
            return result
    return {}


def _catalog_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _catalog_track_key(value: str) -> str:
    """Normalize non-version catalog labels without merging remixes into originals."""

    text = re.sub(r"\s*[\[(]\s*original\s*[\])]\s*", " ", str(value or ""), flags=re.I)
    return _catalog_key(text)


def _artist_names(value: str) -> set[str]:
    return {
        _catalog_key(part)
        for part in re.split(
            r"\s*(?:,|&|\band\b|\bfeat(?:uring)?\.?\b)\s*",
            str(value or ""),
            flags=re.I,
        )
        if _catalog_key(part)
    }


def _artist_match_count(wanted: set[str], available: set[str]) -> int:
    return sum(
        any(name == candidate or name in candidate or candidate in name for candidate in available)
        for name in wanted
    )


def _artist_identity_matches(wanted: set[str], available: set[str]) -> bool:
    """Require every multi-artist hint while tolerating a single partial credit."""

    if not wanted:
        return True
    hits = _artist_match_count(wanted, available)
    return hits > 0 if len(wanted) == 1 else hits == len(wanted)


def extract_square_image_urls(page: str) -> list[str]:
    """Extract original, square image URLs from a Google Images result page."""
    urls: list[str] = []
    seen: set[str] = set()
    for encoded_url, width, height in _IMAGE_RESULT.findall(page):
        if width != height:
            continue
        try:
            url = json.loads(encoded_url).replace("\\/", "/")
        except (json.JSONDecodeError, AttributeError):
            continue
        if url.startswith("//"):
            url = "https:" + url
        lowered = url.lower()
        if not lowered.startswith(("http://", "https://")):
            continue
        if any(host in lowered for host in _BLOCKED_HOSTS):
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def find_album_art(
    album_name: str,
    timeout: float = 12.0,
    release_year: str = "",
    *,
    exclude_url: str = "",
) -> str:
    """Return a square cover, optionally skipping the currently displayed URL."""
    name = normalize_album_name(album_name)
    if not name:
        raise ValueError("Enter an album name before searching for its cover.")
    try:
        return _find_catalog_album_art(
            name,
            timeout,
            release_year,
            exclude_url=exclude_url,
        )
    except (AlbumArtNotFoundError, OSError):
        pass
    from youtube_audio_video_downloader.services.metadata.serpapi_metadata import (
        find_serpapi_album_art,
    )

    serpapi_art = find_serpapi_album_art(
        name,
        release_year,
        timeout,
        exclude_url=exclude_url,
    )
    if serpapi_art:
        return serpapi_art
    query = quote_plus(f"{name} {release_year.strip()} album art".replace("  ", " "))
    request = Request(
        f"https://www.google.com/search?tbm=isch&hl=en&safe=active&q={query}",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed Google host
            page = response.read().decode("utf-8", errors="replace")
        urls = extract_square_image_urls(page)
    except OSError:
        urls = []
    for url in urls:
        if url != exclude_url:
            return url
    raise AlbumArtNotFoundError(
        f'No different square album art was found for "{name}". '
        "You can still paste a URL manually."
    )


def find_song_art(song_title: str, artists: str = "", timeout: float = 12.0) -> str:
    """Return artwork from an exact catalog match before looser image search."""
    title = str(song_title or "").strip()
    artist_text = str(artists or "").strip()
    if not title:
        raise ValueError("Enter a song title before searching for its artwork.")
    try:
        return _find_catalog_song_art(title, artist_text, timeout)
    except (AlbumArtNotFoundError, OSError):
        pass
    terms = " ".join(part for part in (title, artist_text, "cover art") if part)
    request = Request(
        f"https://www.google.com/search?tbm=isch&hl=en&safe=active&q={quote_plus(terms)}",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed Google host
            urls = extract_square_image_urls(
                response.read().decode("utf-8", errors="replace")
            )
    except OSError:
        urls = []
    if urls:
        return urls[0]
    return _find_exact_youtube_thumbnail(title, artist_text)


def _find_exact_youtube_thumbnail(song_title: str, artists: str) -> str:
    """Use artwork from an exact YouTube result when image catalogs have no entry."""

    import yt_dlp

    wanted_title = _catalog_key(song_title)
    artist_parts = [
        _catalog_key(part)
        for part in re.split(r"\s*(?:,|&|\band\b)\s*", artists, flags=re.I)
        if _catalog_key(part)
    ]
    query = " ".join(part for part in (song_title, artists, "official audio") if part)
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": True,
    }
    with _YOUTUBE_SEARCH_LOCK:
        with yt_dlp.YoutubeDL(options) as downloader:
            payload = downloader.extract_info(f"ytsearch8:{query}", download=False)
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    matches: list[tuple[tuple[int, int], str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        result_title = str(entry.get("title") or "")
        context = _catalog_key(
            " ".join(
                (result_title, str(entry.get("channel") or entry.get("uploader") or ""))
            )
        )
        if not wanted_title or wanted_title not in _catalog_key(result_title):
            continue
        artist_hits = sum(part in context for part in artist_parts)
        if artist_parts and (
            artist_hits == 0
            if len(artist_parts) == 1
            else artist_hits != len(artist_parts)
        ):
            continue
        video_id = str(entry.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            continue
        matches.append(
            (
                (int(bool(entry.get("channel_is_verified"))), artist_hits),
                f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            )
        )
    if not matches:
        raise AlbumArtNotFoundError(
            f'No exact cover artwork was found for "{song_title}".'
        )
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def _find_catalog_song_art(song_title: str, artists: str, timeout: float) -> str:
    """Find artwork for an exact song match in Apple's key-free catalog."""
    terms = " ".join(part for part in (song_title, artists) if part)
    request = Request(
        "https://itunes.apple.com/search?entity=song&limit=20&term=" + quote_plus(terms),
        headers={"User-Agent": http_user_agent()},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed Apple host
        payload = json.load(response)
    results = payload.get("results", []) if isinstance(payload, dict) else []
    normalized_title = _catalog_track_key(song_title)
    normalized_artists = _artist_names(artists)
    for result in results:
        if not isinstance(result, dict):
            continue
        track = _catalog_track_key(str(result.get("trackName") or ""))
        result_artists = _artist_names(str(result.get("artistName") or ""))
        artwork = str(result.get("artworkUrl100") or "").strip()
        artist_matches = _artist_identity_matches(
            normalized_artists, result_artists
        )
        if artwork and track == normalized_title and artist_matches:
            return artwork.replace("/100x100bb.", "/1200x1200bb.")
    raise AlbumArtNotFoundError(
        f'No square song artwork was found for "{song_title}".'
    )


def _find_catalog_album_art(
    album_name: str,
    timeout: float,
    release_year: str = "",
    *,
    exclude_url: str = "",
) -> str:
    """Use Apple's key-free catalog when Google serves an anti-bot page."""
    album_name = normalize_album_name(album_name)
    request = Request(
        "https://itunes.apple.com/search?entity=album&limit=10&term="
        + quote_plus(album_name),
        headers={"User-Agent": http_user_agent()},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed Apple host
        payload = json.load(response)
    results = payload.get("results", []) if isinstance(payload, dict) else []
    normalized_name = re.sub(r"[^a-z0-9]+", " ", album_name.lower()).strip()
    for result in results:
        if not isinstance(result, dict):
            continue
        collection = re.sub(
            r"[^a-z0-9]+", " ", str(result.get("collectionName") or "").lower()
        ).strip()
        artwork = str(result.get("artworkUrl100") or "").strip()
        result_year = str(result.get("releaseDate") or "")[:4]
        if release_year and result_year:
            try:
                # Soundtracks are sometimes released shortly before the film.
                if abs(int(result_year) - int(release_year)) > 1:
                    continue
            except ValueError:
                continue
        candidate = artwork.replace("/100x100bb.", "/1200x1200bb.")
        if (
            candidate
            and candidate != exclude_url
            and (
                collection == normalized_name
                or collection.startswith(normalized_name + " ")
            )
        ):
            return candidate
    if results and not release_year:
        for result in results:
            if not isinstance(result, dict):
                continue
            artwork = str(result.get("artworkUrl100") or "").strip()
            candidate = artwork.replace("/100x100bb.", "/1200x1200bb.")
            if candidate and candidate != exclude_url:
                return candidate
    raise AlbumArtNotFoundError(
        f'No square album art was found for "{album_name}". You can still paste a URL manually.'
    )
