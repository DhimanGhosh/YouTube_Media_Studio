"""Find close-duration YouTube audio results for Wikipedia or catalog album tracks."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Callable

from youtube_audio_video_downloader.services.albums.album_art_finder import (
    find_catalog_song_metadata,
)
from youtube_audio_video_downloader.services.albums.album_names import normalize_album_name
from youtube_audio_video_downloader.services.albums.wikipedia_tracks import find_wikipedia_tracks
from youtube_audio_video_downloader.services.albums.catalog_tracks import find_catalog_album_tracks, find_catalog_album_release
from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names
from youtube_audio_video_downloader.utils.time_utils import format_seconds_as_timestamp
from youtube_audio_video_downloader.utils.music_text import clean_song_title, music_words


_VARIANT_TERMS = {
    "reprise", "reprised", "encore", "film", "arabic", "version", "remix", "mix",
    "male", "female", "duet", "dance", "sad",
}


def _recording_key(value: str) -> str:
    """Normalize catalog remix credits without making the DJ name part of the song."""
    return _key(re.sub(r"\bremixed\s+by\s+[^)]+", "remix", clean_song_title(value), flags=re.IGNORECASE))


def _key(value: str) -> str:
    return " ".join(music_words(value))


def _literal_tokens(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", clean_song_title(value).casefold()))


def _is_remix_or_dj_version(title: str) -> bool:
    """Exclude alternate mixes without excluding original songs about a DJ."""
    return bool(re.search(
        r"\b(?:re[ -]?mix(?:ed|es)?|mashups?)\b|\b(?:club|dance|extended|party)\s+mix\b"
        r"|\bdj\b[^()]*\b(?:mix|version|edit)\b"
        r"|[\[(]\s*(?:dance|club|electro|extended)(?:\s+(?:mix|version|edit))?\s*[\])]"
        r"|\((?!\s*(?:feat|ft|featuring)\b)[^)]*\bdj\b[^)]*\)", clean_song_title(title), re.IGNORECASE,
    ))


def _is_lofi(value: str) -> bool:
    return bool(
        re.search(r"\blo[-‐‑‒–—\s]?fi\b", value, re.IGNORECASE)
    )


def _unwanted_upload(title: str) -> bool:
    return _is_remix_or_dj_version(title) or _is_lofi(title) or bool(_literal_tokens(title) & {
        "cover", "reaction", "status", "shorts", "teaser", "8d", "slowed",
    })


def _without_artist_version(title: str, artists: str) -> str:
    """Separate an explicit named-singer version from its core song title."""
    def remove(match: re.Match[str]) -> str:
        annotation = _key(match.group(1)).split()
        for artist in _artist_keys(artists):
            words = artist.split()
            if annotation[:len(words)] == words and set(annotation[len(words):]) <= {"version", "vocal", "solo"}:
                return " "
        return match.group(0)
    return " ".join(re.sub(r"[\[(]([^()\[\]]+)[\])]", remove, title).split())


def _complete_track_evidence(tracks: list[dict], album_name: str, release_year: str) -> list[dict]:
    """Fill missing lengths and singer-version aliases from one verified album release."""
    if all(track.get("duration_seconds") for track in tracks):
        return tracks
    try:
        release = find_catalog_album_release(
            album_name, release_year, expected_titles=tuple(str(track["title"]) for track in tracks),
        )
    except (OSError, ValueError):
        return tracks
    catalog_tracks = release.get("tracks", [])
    result = []
    for track in tracks:
        if track.get("duration_seconds"):
            result.append(track)
            continue
        title = str(track["title"])
        artists = str(track.get("artists") or "")
        credits = _artist_keys(artists)
        wanted_key = _recording_key(title)
        wanted_variants = set(wanted_key.split()) & _VARIANT_TERMS
        matches = []
        for candidate in catalog_tracks:
            candidate_title = str(candidate.get("title") or "")
            if _is_remix_or_dj_version(candidate_title) or not credits or not credits <= _artist_keys(str(candidate.get("artists") or "")):
                continue
            core_title = _without_artist_version(candidate_title, artists)
            candidate_key = _recording_key(core_title)
            variants = set(candidate_key.split()) & _VARIANT_TERMS
            named_artist_alias = core_title != candidate_title
            compatible = _variant_compatible(wanted_variants, variants, credited=True)
            if not compatible and not named_artist_alias and not (not wanted_variants and variants <= {"male", "female", "version"}):
                continue
            if _recording_titles_match(wanted_key, candidate_key) and candidate.get("duration_seconds"):
                matches.append((candidate, core_title))
        if len(matches) == 1:
            candidate, match_title = matches[0]
            track = {**track, "duration_seconds": candidate["duration_seconds"],
                     "_duration_source": "Apple Music catalog", "_match_title": match_title,
                     "_verify_credits": True}
        result.append(track)
    return result


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
    album_tracks = [track for track in album_tracks if not _is_remix_or_dj_version(str(track["title"]))]
    if not album_tracks:
        raise LookupError("No original album tracks remain after excluding remix and DJ versions.")
    album_tracks = _complete_track_evidence(album_tracks, album_name, release_year)
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
            title = clean_song_title(str(track["title"]), album_name)
            artists = str(track["artists"])
            match_title = clean_song_title(str(track.get("_match_title") or title), album_name)
            core_title = _without_artist_version(match_title, artists)
            verify_credits = bool(track.get("_verify_credits")) or core_title != match_title
            match_title = core_title
            duration_source = str(track.get("_duration_source") or track.get("source") or "Wikipedia")
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
            title_key = _recording_key(match_title)
            variant_words = set(title_key.split()) & _VARIANT_TERMS
            wanted_tokens = _literal_tokens(match_title) - _VARIANT_TERMS - {"ft", "feat", "featuring"}
            if not wanted_tokens:
                wanted_tokens = _literal_tokens(match_title)
            search_title = re.sub(r"\s*-\s*(?:male|female)\s*$", "", match_title, flags=re.IGNORECASE)
            search_title = re.sub(r"\bremixed\s+by\s+[^)]+", "remix", search_title, flags=re.IGNORECASE)
            # Search titles often omit version labels. Keep those labels in
            # verification, but let the fallback discover the clean song name.
            search_title = " ".join(re.sub(
                r"\b(?:" + "|".join(sorted(_VARIANT_TERMS)) + r")\b|[()\[\]]",
                " ", search_title, flags=re.IGNORECASE,
            ).split()).strip(" -") or match_title
            search_error = None
            for query in (
                f"{album_name} {match_title} {artists} full song audio lyrical",
                f"{search_title} {album_name} {artists}",
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
                    overlap = len(wanted_tokens & _literal_tokens(candidate_title)) / max(1, len(wanted_tokens))
                    # Search can translate a song title (Zakhmi -> Wounded) while
                    # the watch page still supplies its original recording title.
                    if overlap < 0.65 and (
                        entry.get("channel_is_verified")
                        and f" {album_key} " in f" {_key(candidate_title)} "
                        and set(_key("official audio").split()) <= candidate_tokens
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
                            overlap = len(wanted_tokens & _literal_tokens(candidate_title)) / max(1, len(wanted_tokens))
                    if overlap < 0.65 and not _is_credited_spelling_match(
                        entry, title_key, album_key, artists, expected,
                    ):
                        # Topic uploads often expose the album and singer only on
                        # the watch page. Inspect a bounded shortlist of plausible
                        # titles rather than rejecting those recordings up front.
                        duration = int(float(entry.get("duration") or 0))
                        if (
                            expected > 0 and duration > 0 and _duration_matches(duration, expected)
                            and credit_lookups < 3
                            and (entry.get("channel_is_verified") or str(entry.get("channel") or "").endswith(" - Topic"))
                            and _recording_titles_match(title_key, _recording_key(candidate_title))
                            and not _unwanted_upload(candidate_title)
                            and _variant_compatible(variant_words, candidate_tokens & _VARIANT_TERMS, credited=True)
                        ):
                            if is_cancelled is not None and is_cancelled():
                                return []
                            credit_lookups += 1
                            try:
                                metadata = downloader.extract_info(_video_url(entry), download=False, process=False)
                            except DownloadError as exc:
                                search_error = exc
                                continue
                            if _matches_topic_release(metadata, title_key, album_key, artists, allow_credit_alias=verify_credits,
                                                      alternative_title_key=_recording_key(title) if verify_credits else ""):
                                actual_duration = int(float(metadata.get("duration") or duration))
                                if _duration_matches(actual_duration, expected):
                                    topic_candidates.append((actual_duration, {**entry, **metadata}))
                                    break
                        continue
                    candidate_variants = candidate_tokens & _VARIANT_TERMS
                    needs_credits = verify_credits or not _variant_compatible(variant_words, candidate_variants)
                    missing_version = bool(variant_words - {"male", "female", "version"}) and not candidate_variants
                    can_read_version = missing_version and (
                        entry.get("channel_is_verified") or str(entry.get("channel") or "").endswith(" - Topic")
                    )
                    if needs_credits and not verify_credits and not can_read_version and not _variant_compatible(
                        variant_words, candidate_variants, credited=True,
                    ):
                        continue
                    if needs_credits and (not artists or expected <= 0):
                        continue
                    if _unwanted_upload(candidate_title):
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
                            metadata = _with_description_credits(
                                metadata, album_name if f" {album_key} " in f" {_key(candidate_title)} " else "",
                            )
                        if _matches_topic_release(metadata, title_key, album_key, artists, allow_credit_alias=verify_credits,
                                                  alternative_title_key=_recording_key(title) if verify_credits else ""):
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
                    metadata_checks = 0
                    for duration, entry in candidates:
                        if metadata_checks >= 3:
                            break
                        if not entry.get("channel_is_verified") or f" {album_key} " not in f" {_key(entry['title'])} ":
                            continue
                        if not _duration_matches(duration, catalog_duration):
                            continue
                        if is_cancelled is not None and is_cancelled():
                            return []
                        metadata_checks += 1
                        try:
                            metadata = downloader.extract_info(_video_url(entry), download=False, process=False)
                        except DownloadError as exc:
                            search_error = exc
                            continue
                        metadata = _with_description_credits(metadata, album_name)
                        if _matches_topic_release(metadata, title_key, album_key, artists):
                            credited_candidates.append((int(float(metadata.get("duration") or duration)), {**entry, **metadata}))
                    chosen = _closest_match(credited_candidates, catalog_duration)
                else:
                    chosen = _closest_match(candidates, catalog_duration or expected)
            if chosen is None and search_error is not None:
                raise search_error
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
                        expected, closest, matched=False, source_name=duration_source
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
                    expected, chosen, matched=True, source_name=duration_source
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


def _is_credited_spelling_match(
    entry: dict, title_key: str, album_key: str, artists: str, expected: int,
) -> bool:
    """Allow small transliteration differences only with corroborating release evidence."""
    if not entry.get("channel_is_verified") or not album_key or expected <= 0:
        return False
    duration = int(float(entry.get("duration") or 0))
    if duration <= 0 or not _duration_matches(duration, expected):
        return False
    candidate_key = _recording_key(str(entry.get("title") or ""))
    padded = f" {_key(str(entry.get('title') or ''))} "
    if f" {album_key} " not in padded or not any(
        f" {artist} " in padded for artist in _artist_keys(artists)
    ):
        return False
    wanted = [word for word in title_key.split() if word not in _VARIANT_TERMS]
    available = candidate_key.split()
    if len(wanted) < 3:
        return False
    # Compare short ordered title spans, not the whole title plus cast and credits.
    # Pyar/Pyaar and Uljhanne/Uljhane should not hide an otherwise verified song.
    for size in range(max(3, len(wanted) - 1), len(wanted) + 2):
        for start in range(len(available) - size + 1):
            span = available[start:start + size]
            if len(set(wanted) & set(span)) < 2:
                continue
            if SequenceMatcher(None, " ".join(wanted), " ".join(span)).ratio() >= 0.86:
                return True
    return False


def _catalog_duration(title: str, artists: str, album_key: str, release_year: str) -> int:
    if not artists:
        return 0
    try:
        catalog = find_catalog_song_metadata(title, artists, timeout=5)
        if (
            _recording_titles_match(_recording_key(title), _recording_key(catalog.get("title", "")))
            and _variant_compatible(
                set(_recording_key(title).split()) & _VARIANT_TERMS,
                set(_recording_key(catalog.get("title", "")).split()) & _VARIANT_TERMS,
                credited=True,
            )
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
    *, allow_credit_alias: bool = False, alternative_title_key: str = "",
) -> bool:
    if not metadata:
        return False
    if alternative_title_key and _matches_topic_release(metadata, alternative_title_key, album_key, artists):
        return True
    if _is_remix_or_dj_version(str(metadata.get("track") or "")):
        return False
    if _key(normalize_album_name(metadata.get("album"))) != album_key:
        return False
    wanted = title_key.split()
    track_title = str(metadata.get("track") or "")
    available = _recording_key(_without_artist_version(track_title, artists) if allow_credit_alias else track_title).split()
    if allow_credit_alias and set(available) & _VARIANT_TERMS <= {"male", "female", "version"}:
        available = [word for word in available if word not in {"male", "female", "version"}]
    variants = set(wanted) & _VARIANT_TERMS
    if not _variant_compatible(variants, set(available) & _VARIANT_TERMS, credited=True):
        return False
    if not _recording_titles_match(title_key, " ".join(available)):
        return False
    expected_credits = _artist_keys(artists)
    available_credits = _artist_keys(", ".join(metadata.get("artists") or []))
    return bool(expected_credits) and expected_credits <= available_credits


def _recording_titles_match(expected: str, candidate: str) -> bool:
    """Compare clean recording titles; callers verify release, credits and variants."""
    wanted = [word for word in expected.split() if word not in _VARIANT_TERMS]
    available = [word for word in candidate.split() if word not in _VARIANT_TERMS]
    if not wanted or not available:
        return bool(expected) and expected == candidate
    if wanted == available:
        return True
    shorter, longer = sorted((wanted, available), key=len)
    if len(shorter) >= 2 and shorter == longer[:len(shorter)]:
        return True
    return (
        min(len(wanted), len(available)) >= 3
        and len(set(wanted) & set(available)) >= 2
        and SequenceMatcher(None, " ".join(wanted), " ".join(available)).ratio() >= 0.86
    )


def _with_description_credits(metadata: dict, album_name: str = "") -> dict:
    """Read labeled release credits on verified labels' lyrical uploads."""
    if not isinstance(metadata, dict):
        return {}
    if metadata.get("track") and metadata.get("album") and metadata.get("artists"):
        return metadata
    description = str(metadata.get("description") or "")
    fields = {}
    for key, labels in (("album", "movie|album|film"), ("artists", r"singer(?:\(s\)|s)?|vocals|playback singer"),
                        ("composer", "music director|music composer|composer"),
                        ("track", "song(?: name)?")):
        match = re.search(rf"^\s*(?:{labels})\s*(?::|[-–—])\s*([^\r\n]+)", description, re.IGNORECASE | re.MULTILINE)
        if match:
            fields[key] = match.group(1).strip()
    if not fields.get("artists") or not (fields.get("album") or album_name):
        return metadata
    return {
        **metadata,
        "track": metadata.get("track") or fields.get("track") or metadata.get("title"),
        "album": metadata.get("album") or fields.get("album") or album_name,
        "artists": [fields["artists"], *([fields["composer"]] if fields.get("composer") else [])],
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
    expected = expected - {"version"}
    candidate = candidate - {"version"}
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
