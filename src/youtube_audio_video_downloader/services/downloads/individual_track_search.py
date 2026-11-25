"""Find close-duration YouTube audio results for Wikipedia or catalog album tracks."""

from __future__ import annotations

import re
from typing import Any, Callable

from youtube_audio_video_downloader.services.albums.album_art_finder import (
    find_catalog_song_metadata,
)
from youtube_audio_video_downloader.services.albums.album_names import normalize_album_name
from youtube_audio_video_downloader.services.albums.wikipedia_tracks import find_wikipedia_tracks
from youtube_audio_video_downloader.services.albums.catalog_tracks import find_catalog_album_tracks
from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names
from youtube_audio_video_downloader.utils.time_utils import format_seconds_as_timestamp


_VARIANT_TERMS = {
    "reprise", "reprised", "encore", "film", "arabic", "version", "remix", "mix",
    "male", "female", "duet", "dance",
}


def _recording_key(value: str) -> str:
    """Normalize catalog remix credits without making the DJ name part of the song."""
    return _key(re.sub(r"\bremixed\s+by\s+[^)]+", "remix", value, flags=re.IGNORECASE))


def _key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _is_lofi(value: str) -> bool:
    return bool(
        re.search(r"\blo[-‐‑‒–—\s]?fi\b", value, re.IGNORECASE)
    )


def find_individual_album_tracks(
    album_name: str,
    release_year: str = "",
    is_cancelled: Callable[[], bool] | None = None,
) -> list[dict]:
    """Return album-editor tracks with individual YouTube links."""
    import yt_dlp
    from yt_dlp.utils import DownloadError

    try:
        album_tracks = find_wikipedia_tracks(album_name, release_year, preferred_languages_only=True)
    except (OSError, ValueError):
        album_tracks = []
    if not album_tracks:
        album_tracks = find_catalog_album_tracks(album_name, release_year)
    if not album_tracks:
        raise LookupError(
            f'No usable track list was found on Wikipedia or the Apple Music catalog for "{album_name}". '
            "You can add tracks manually or paste a full-album YouTube source."
        )
    results: list[dict] = []
    options: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": "in_playlist", "noplaylist": True, "ignore_no_formats_error": True,
    }
    album_key = _key(normalize_album_name(album_name))
    with yt_dlp.YoutubeDL(options) as downloader:
        for track in album_tracks:
            if is_cancelled is not None and is_cancelled():
                return []
            title = str(track["title"])
            artists = str(track["artists"])
            if _is_lofi(title):
                results.append({title: {
                    "ytb_link": "",
                    "start": "",
                    "end": "",
                    "artists": artists,
                    "download": "false",
                    "match_status": "Lofi track intentionally not searched",
                }})
                continue
            expected = int(track.get("duration_seconds") or 0)
            candidates = []
            topic_candidates = []
            seen_ids = set()
            title_key = _recording_key(title)
            variant_words = set(title_key.split()) & _VARIANT_TERMS
            wanted_tokens = set(title_key.split()) - _VARIANT_TERMS - {"ft", "feat", "featuring"}
            if not wanted_tokens:
                wanted_tokens = set(title_key.split())
            search_title = re.sub(r"\s*\((?:male|female)\)", "", title, flags=re.IGNORECASE)
            search_title = re.sub(r"\bremixed\s+by\s+[^)]+", "remix", search_title, flags=re.IGNORECASE)
            search_error = None
            for query in (
                f"{album_name} {title} {artists} full song audio lyrical",
                f"{search_title} {album_name} full audio",
            ):
                if is_cancelled is not None and is_cancelled():
                    return []
                try:
                    search = downloader.extract_info(f"ytsearch8:{query}", download=False)
                except DownloadError as exc:
                    search_error = exc
                    continue
                entries = (search.get("entries") or []) if isinstance(search, dict) else []
                credit_lookups = 0
                if variant_words & {"male", "female"}:
                    entries = sorted(
                        (entry for entry in entries if isinstance(entry, dict)),
                        key=lambda entry: (
                            not bool(entry.get("channel_is_verified")),
                            abs(float(entry.get("duration") or 0) - expected),
                        ),
                    )
                for entry in entries:
                    if not isinstance(entry, dict) or not entry.get("id"):
                        continue
                    if entry["id"] in seen_ids:
                        continue
                    seen_ids.add(entry["id"])
                    candidate_title = str(entry.get("title") or "")
                    candidate_tokens = set(_recording_key(candidate_title).split())
                    overlap = len(wanted_tokens & candidate_tokens) / max(1, len(wanted_tokens))
                    # Search can translate a song title (Zakhmi -> Wounded) while
                    # the watch page still supplies its original recording title.
                    if overlap < 0.65 and (
                        entry.get("channel_is_verified")
                        and f" {album_key} " in f" {_key(candidate_title)} "
                        and {"official", "audio"} <= candidate_tokens
                        and any(
                            f" {artist} " in f" {_key(candidate_title)} "
                            for artist in _artist_keys(artists)
                        )
                    ):
                        if is_cancelled is not None and is_cancelled():
                            return []
                        try:
                            original = downloader.extract_info(
                                _video_url(entry), download=False, process=False,
                            )
                        except DownloadError:
                            original = None
                        if isinstance(original, dict) and original.get("title"):
                            entry = {**entry, **original}
                            candidate_title = str(entry["title"])
                            candidate_tokens = set(_recording_key(candidate_title).split())
                            overlap = len(wanted_tokens & candidate_tokens) / max(1, len(wanted_tokens))
                    if overlap < 0.65:
                        continue
                    candidate_variants = candidate_tokens & _VARIANT_TERMS
                    needs_credits = not _variant_compatible(variant_words, candidate_variants)
                    if needs_credits and not _variant_compatible(
                        variant_words, candidate_variants, credited=True,
                    ):
                        continue
                    if needs_credits and (not artists or expected <= 0):
                        continue
                    if _is_lofi(candidate_title) or candidate_tokens & {
                        "cover", "reaction", "status", "shorts", "teaser", "8d", "slowed",
                    }:
                        continue
                    duration = int(float(entry.get("duration") or 0))
                    if duration <= 0:
                        continue
                    if needs_credits and (
                        not _duration_matches(duration, expected) or credit_lookups >= 3
                    ):
                        continue
                    if (needs_credits or str(entry.get("channel") or "").endswith(" - Topic")) and artists:
                        if is_cancelled is not None and is_cancelled():
                            return []
                        try:
                            if needs_credits:
                                credit_lookups += 1
                            metadata = downloader.extract_info(
                                _video_url(entry), download=False, process=False,
                            )
                        except DownloadError as exc:
                            search_error = exc
                            continue
                        if needs_credits and entry.get("channel_is_verified") and isinstance(metadata, dict):
                            metadata = _with_description_credits(metadata)
                        if _matches_topic_release(metadata, title_key, album_key, artists):
                            actual_duration = int(float(metadata.get("duration") or duration))
                            if not needs_credits or _duration_matches(actual_duration, expected):
                                topic_candidates.append((actual_duration, {**entry, **metadata}))
                                break
                        continue
                    candidates.append((duration, entry))
                # Label-distributed tracks with matching album and credits can
                # establish the recording even when Wikipedia lists a bad duration.
                chosen = _closest_match(topic_candidates, expected, check_duration=False)
                if chosen is not None:
                    break
            if chosen is None and not candidates and search_error is not None:
                raise search_error
            if chosen is None and candidates:
                if is_cancelled is not None and is_cancelled():
                    return []
                catalog_duration = _catalog_duration(title, artists, album_key, release_year)
                if catalog_duration and not _duration_matches(catalog_duration, expected):
                    credited_candidates = [
                        (duration, entry) for duration, entry in candidates
                        if _has_recording_credits(entry["title"], album_key, artists)
                    ]
                    chosen = _closest_match(credited_candidates, catalog_duration)
                else:
                    chosen = _closest_match(candidates, catalog_duration or expected)
            if chosen is None:
                closest = _closest_match(candidates, expected, check_duration=False)
                results.append({title: {
                    "ytb_link": "",
                    "start": "",
                    "end": "",
                    "artists": artists,
                    "download": "false",
                    "match_status": "No safe close-duration YouTube match",
                    "length_notice": _length_notice(
                        expected, closest, matched=False, source_name=str(track.get("source") or "Wikipedia")
                    ),
                }})
                continue
            results.append({title: {
                "ytb_link": _video_url(chosen),
                "start": "",
                "end": "",
                "artists": artists,
                "download": "true",
                "match_status": "Matched",
                "length_notice": _length_notice(
                    expected, chosen, matched=True, source_name=str(track.get("source") or "Wikipedia")
                ),
            }})
    if not any(values["ytb_link"] or values.get("length_notice")
               for track in results for values in track.values()):
        raise LookupError(f'No close-duration YouTube tracks were found for "{album_name}".')
    return results


def _video_url(entry: dict) -> str:
    url = str(entry.get("webpage_url") or entry.get("url") or "")
    if url.startswith(("http://", "https://")):
        return url
    return f"https://www.youtube.com/watch?v={entry['id']}"


def _catalog_duration(title: str, artists: str, album_key: str, release_year: str) -> int:
    if not artists:
        return 0
    try:
        catalog = find_catalog_song_metadata(title, artists, timeout=5)
        if (
            _key(catalog.get("title", "")) == _key(title)
            and _key(catalog.get("album", "")) == album_key
            and (not release_year or catalog.get("year") == release_year)
        ):
            return round(float(catalog.get("duration_seconds") or 0))
    except (ValueError, TypeError, OverflowError):
        # A malformed catalog response should not discard YouTube search results.
        pass
    return 0


def _artist_keys(artists: str) -> set[str]:
    return {_key(artist) for artist in format_artist_names(artists).split(",") if artist.strip()}


def _has_recording_credits(video_title: str, album_key: str, artists: str) -> bool:
    title = f" {_key(video_title)} "
    credits = _artist_keys(artists)
    return (
        bool(credits)
        and f" {album_key} " in title
        and all(f" {credit} " in title for credit in credits)
    )


def _matches_topic_release(
    metadata: dict | None, title_key: str, album_key: str, artists: str,
) -> bool:
    if not metadata:
        return False
    if _key(normalize_album_name(metadata.get("album"))) != album_key:
        return False
    wanted = title_key.split()
    available = _recording_key(str(metadata.get("track") or "")).split()
    variants = set(wanted) & _VARIANT_TERMS
    if not _variant_compatible(variants, set(available) & _VARIANT_TERMS, credited=True):
        return False
    # The catalog sometimes expands a short Wikipedia title, such as
    # "Kabhi Aayine (Remix)" to "Kabhi Aayine Pe Likha Tujhe (Remix)".
    wanted_title = [word for word in wanted if word not in _VARIANT_TERMS]
    available_title = [word for word in available if word not in _VARIANT_TERMS]
    if wanted_title != available_title and (
        len(wanted_title) < 2 or wanted_title != available_title[:len(wanted_title)]
    ):
        return False
    expected_credits = _artist_keys(artists)
    available_credits = _artist_keys(", ".join(metadata.get("artists") or []))
    return bool(expected_credits) and expected_credits <= available_credits


def _with_description_credits(metadata: dict) -> dict:
    """Read labeled release credits on verified labels' lyrical uploads."""
    if metadata.get("track") and metadata.get("album") and metadata.get("artists"):
        return metadata
    description = str(metadata.get("description") or "")
    fields = {}
    for key, labels in (("album", "movie|album|film"), ("artists", "singer|singers|vocals")):
        match = re.search(rf"^\s*(?:{labels})\s*:\s*([^\r\n]+)", description, re.IGNORECASE | re.MULTILINE)
        if match:
            fields[key] = match.group(1).strip()
    if len(fields) != 2:
        return metadata
    return {
        **metadata,
        "track": metadata.get("track") or metadata.get("title"),
        "album": fields["album"],
        "artists": [fields["artists"]],
    }


def _closest_match(
    candidates: list[tuple[int, dict]], expected: int, *, check_duration: bool = True,
) -> dict | None:
    eligible = [
        (duration, entry) for duration, entry in candidates
        if not check_duration or _duration_matches(duration, expected)
    ]
    closest = min(eligible, key=lambda item: (
        abs(item[0] - expected) if expected else 0,
        not bool(item[1].get("channel_is_verified")),
        -int(item[1].get("view_count") or 0),
    ), default=None)
    return closest[1] if closest else None


def _duration_matches(duration: int, expected: int) -> bool:
    return not expected or abs(duration - expected) <= max(25, int(expected * 0.12))


def _length_notice(
    expected: int, entry: dict | None, *, matched: bool, source_name: str = "Wikipedia",
) -> str:
    duration = int(float(entry.get("duration") or 0)) if entry else 0
    if not duration or _duration_matches(duration, expected):
        return ""
    wiki_time = format_seconds_as_timestamp(expected).removeprefix("00:")
    video_time = format_seconds_as_timestamp(duration).removeprefix("00:")
    source = "Matched YouTube audio" if matched else "Closest YouTube candidate"
    return f"{source_name} lists {wiki_time}. {source} runs {video_time}."


def _variant_compatible(expected: set[str], candidate: set[str], *, credited: bool = False) -> bool:
    genders = {"male", "female"}
    if expected & genders:
        expected = expected - {"version"}
        candidate = candidate - {"version"}
        if candidate & genders and expected & genders != candidate & genders:
            return False
        if expected & genders != candidate & genders:
            # Catalog gender labels are often absent (or called "Duet") on YouTube.
            # Only album, singer, title and duration evidence can establish the version.
            return credited and _variant_compatible(
                expected - genders, candidate - {"duet"}, credited=credited,
            )
        return _variant_compatible(expected - genders, candidate - genders, credited=credited)
    if not expected:
        return not candidate
    if expected & {"remix", "mix"}:
        return bool(candidate & {"remix", "mix"})
    if "reprise" in expected:
        return bool(candidate & {"reprise", "reprised"})
    version_family = {"reprise", "reprised", "film", "version"}
    return bool(expected & candidate) or bool(
        expected & version_family and candidate & version_family
    )
