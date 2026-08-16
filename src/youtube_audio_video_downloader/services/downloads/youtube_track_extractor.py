"""Extract timestamped tracks and singer credits from YouTube descriptions."""

from __future__ import annotations

import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from typing import Any

from youtube_audio_video_downloader.services.ai.ai_provider import chat_json
from youtube_audio_video_downloader.services.albums.album_art_finder import (
    find_catalog_song_metadata,
)
from youtube_audio_video_downloader.services.albums.wikipedia_tracks import find_wikipedia_track_artists
from youtube_audio_video_downloader.utils.artist_name_formatter import (
    format_artist_names,
)
from youtube_audio_video_downloader.utils.track_timestamp_parser import parse_tracks_text
from youtube_audio_video_downloader.utils.time_utils import (
    format_seconds_as_timestamp,
    parse_timestamp_to_seconds,
)


_TIME = r"\d{1,2}:\d{2}(?::\d{2})?"
_TIME_FIRST = re.compile(rf"^\s*(?P<time>{_TIME})\s*(?:[-–—→►•|:)]+)\s*(?P<title>.+)$")
_TIME_LAST = re.compile(rf"^\s*(?P<title>.+?)\s*(?:[-–—→►•|]+)\s*(?P<time>{_TIME})\s*$")
_SONG_CREDIT = re.compile(
    r"^\s*(?:\d+\s*[.)]?\s*)?song\s*[:\-–—]\s*(.+?)\s*$", re.I
)
_SINGER_CREDIT = re.compile(
    r"^\s*(?:singers?|vocals)\s*[:\-–—]\s*(.+?)\s*$", re.I
)
_TRAILING_PARENS = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")
_VERSION_WORDS = {
    "version", "reprise", "remix", "mix", "theme", "title", "track",
    "dialogue", "male", "female", "instrumental", "acoustic", "unplugged",
    "lyrics", "lyrical", "radio", "edit", "film", "original",
}

# Accept punctuation, tabs, or plain whitespace between a timestamp and title.
# YouTube descriptions frequently contain invisible/typographic characters that
# are not represented by the older punctuation-only expressions above.
_ROBUST_TIME_FIRST = re.compile(
    rf"^\s*(?P<time>{_TIME})\s*(?:[-\u2013\u2014\u2192\u23e9\u25ba\u2022|:)]+\s*|\s+)(?P<title>\S.+)$"
)
_ROBUST_TIME_LAST = re.compile(
    rf"^\s*(?P<title>\S.*?)\s*(?:[-\u2013\u2014\u2192\u23e9\u25ba\u2022|]+\s*|\s+)(?P<time>{_TIME})\s*$"
)

_AGENT_TRACK_FIELDS = {
    "start": {"type": "string"},
    "original_title": {"type": "string"},
    "title": {"type": "string"},
    "artists": {"type": "string"},
    "album": {"type": "string"},
    "release_year": {"type": "string"},
}
_AGENT_TRACK_LIST_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": _AGENT_TRACK_FIELDS,
        "required": list(_AGENT_TRACK_FIELDS),
        "additionalProperties": False,
    },
}
_AGENT_TRACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tracks": _AGENT_TRACK_LIST_SCHEMA,
        "reason": {"type": "string"},
    },
    "required": ["tracks", "reason"],
    "additionalProperties": False,
}
_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "accepted": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "tracks": _AGENT_TRACK_LIST_SCHEMA,
    },
    "required": ["accepted", "issues", "tracks"],
    "additionalProperties": False,
}


def _clean_title(title: str) -> str:
    title = re.sub(r"^[\s\u266b\u266c\U0001f3b5\U0001f3b6]+", "", title)
    # YouTube tracklists commonly include the ordinal in the title, for
    # example ``00:00 - 1 - Song``. Accept punctuation-based ordinals and
    # spaced dash/colon variants, while leaving ``50-Cent`` untouched.
    title = re.sub(
        r"^\s*#?\d{1,3}(?:\s*[.)]\s*|\s+[-\u2013\u2014:]\s*|\s*[-\u2013\u2014:]\s+)",
        "",
        title,
    )
    return re.sub(
        r"^\s*(?:\d+\s*[.)]\s*|[\u2022♫►\u2665\u2661\u2764]+\s*)",
        "",
        title,
    ).strip()


def _key(title: str) -> str:
    """Return a comparison key without discarding non-Latin writing systems."""

    normalized = unicodedata.normalize("NFKC", str(title or "")).casefold()
    normalized = re.sub(r"\btitle\s+(?:track|song)\b", "", normalized)
    characters = (
        character
        if unicodedata.category(character)[0] in {"L", "M", "N"}
        else " "
        for character in normalized
    )
    return " ".join("".join(characters).split())


def _uses_only_latin_letters(text: str) -> bool:
    """Return whether every letter is Latin; punctuation and numbers are neutral."""

    letters = [character for character in str(text or "") if character.isalpha()]
    return not letters or all(
        "LATIN" in unicodedata.name(character, "") for character in letters
    )


def _tracks_require_romanization(tracks: list[dict]) -> bool:
    for track in tracks:
        if not isinstance(track, dict) or not track:
            continue
        title = str(next(iter(track)))
        if not _uses_only_latin_letters(title):
            return True
    return False


def _split_inline_artists(title: str) -> tuple[str, str]:
    """Split ``Song Title (Singer Names)`` but retain version descriptors."""
    match = _TRAILING_PARENS.match(title)
    if not match:
        return title, ""
    possible_artists = match.group(2).strip()
    if not any(character.isalpha() for character in possible_artists):
        return title, ""
    words = set(re.findall(r"[a-z]+", possible_artists.lower()))
    if words & _VERSION_WORDS:
        return title, ""
    return match.group(1).strip(), possible_artists


def match_wikipedia_artist(title: str, artists_by_title: dict[str, str]) -> str:
    """Match minor spelling/spacing variants against a Wikipedia track title."""
    title_key = _key(title)
    best_artist = ""
    best_ratio = 0.0
    for candidate_key, artist in artists_by_title.items():
        normalized_candidate = _key(candidate_key)
        if title_key == normalized_candidate:
            return artist
        ratio = SequenceMatcher(None, title_key, normalized_candidate).ratio()
        if ratio > best_ratio:
            best_ratio, best_artist = ratio, artist
    # High threshold prevents a base track and its reprise/version from colliding.
    return best_artist if best_ratio >= 0.88 else ""


def description_to_timestamp_text(description: str) -> str:
    """Create parser-ready ``time - title by singers`` lines from a description."""
    timestamped: list[tuple[str, str, str]] = []
    credits: list[tuple[str, str]] = []
    current_song = ""
    for raw_line in str(description or "").splitlines():
        line = re.sub(r"[\u200b\u200c\u200d\u2060\ufeff]", "", raw_line).strip()
        match = (
            _ROBUST_TIME_FIRST.match(line)
            or _ROBUST_TIME_LAST.match(line)
            or _TIME_FIRST.match(line)
            or _TIME_LAST.match(line)
        )
        if match:
            title = _clean_title(match.group("title"))
            if title and not title.lower().startswith(("http://", "https://")):
                title, inline_artists = _split_inline_artists(title)
                timestamped.append((match.group("time"), title, inline_artists))
                # Many official-label descriptions put the timestamp heading
                # directly above ``Singer(s) - ...`` without a ``Song:`` row.
                current_song = title
        song_match = _SONG_CREDIT.match(line)
        if song_match:
            current_song = _clean_title(song_match.group(1))
            continue
        singer_match = _SINGER_CREDIT.match(line)
        if singer_match and current_song:
            singers = singer_match.group(1).strip().rstrip(".")
            credits.append((current_song, singers))
            current_song = ""

    if not timestamped:
        raise LookupError("No timestamped track list was found in the YouTube description.")

    lines: list[str] = []
    for timestamp, title, inline_artists in timestamped:
        title_key = _key(title)
        full_title = f"{title} ({inline_artists})" if inline_artists else title
        full_title_key = _key(full_title)
        best_singers = ""
        best_ratio = 0.0
        for credit_title, singers in credits:
            credit_key = _key(credit_title)
            ratio = max(
                SequenceMatcher(None, title_key, credit_key).ratio(),
                SequenceMatcher(None, full_title_key, credit_key).ratio(),
            )
            if title_key == credit_key or full_title_key == credit_key:
                ratio = 1.0
            if ratio > best_ratio:
                best_ratio, best_singers = ratio, singers
        # A high threshold still tolerates spelling variants, but avoids assigning
        # a singer from a different similarly named version of the same song.
        credited_singers = best_singers if best_ratio >= 0.82 else ""
        final_title = title
        if inline_artists and credited_singers:
            inline_key = _key(inline_artists)
            singers_key = _key(credited_singers)
            inline_is_singer = (
                inline_key in singers_key
                or singers_key in inline_key
                or SequenceMatcher(None, inline_key, singers_key).ratio() >= 0.72
            )
            if not inline_is_singer:
                final_title = full_title
        singers = credited_singers or inline_artists
        suffix = f" by {singers}" if singers else ""
        lines.append(f"{timestamp} - {final_title}{suffix}")
    return "\n".join(lines)


def extract_tracks_from_youtube(
    url: str,
    album_name: str = "",
    release_year: str = "",
    *,
    model: str = "",
    use_ai: bool = True,
    mixed_albums: bool = False,
) -> tuple[str, list[dict]]:
    """Fetch, interpret, validate, and parse a YouTube track list.

    When AI is enabled, one evidence-bounded agent extracts the list and a
    second agent reviews it. Deterministic timestamp/duration validation is the
    final gate. Chapters and the local description parser remain available when
    AI is disabled or unavailable.
    """
    if not url.strip().lower().startswith(("http://", "https://")):
        raise ValueError("Find or enter a valid YouTube link before extracting tracks.")
    import yt_dlp

    url = _single_video_url(url)
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url.strip(), download=False)
    if not isinstance(info, dict):
        raise LookupError("YouTube returned no metadata for the selected video.")
    description = str(info.get("description") or "")
    video_title = str(info.get("title") or album_name or "YouTube jukebox")
    duration = int(info.get("duration") or 0)
    chapters = info.get("chapters") if isinstance(info.get("chapters"), list) else []

    deterministic_error = ""
    try:
        timestamp_text = description_to_timestamp_text(description)
        deterministic_tracks = _parse_and_validate_tracks(timestamp_text, duration)
    except (LookupError, TypeError, ValueError) as exc:
        deterministic_error = str(exc)
        try:
            timestamp_text = _chapters_to_timestamp_text(chapters)
            deterministic_tracks = _parse_and_validate_tracks(timestamp_text, duration)
        except (LookupError, TypeError, ValueError) as chapter_exc:
            deterministic_tracks = []
            deterministic_error = f"{deterministic_error}; chapters: {chapter_exc}"

    selected_model = model.strip()
    ai_error = ""
    if use_ai and selected_model:
        try:
            timestamp_text, tracks = _agentic_track_extraction(
                video_title=video_title,
                description=description,
                chapters=chapters,
                duration=duration,
                model=selected_model,
            )
            print(
                f"[AI-VERIFIED] Jukebox track extraction | "
                f"tracks={len(tracks)} | model={selected_model}"
            )
        except Exception as exc:
            print(f"[AI-FALLBACK] Jukebox extraction used deterministic evidence | {exc}")
            ai_error = str(exc)
            requires_romanization = _tracks_require_romanization(deterministic_tracks)
            tracks = deterministic_tracks
            if requires_romanization:
                deterministic_error = (
                    "Source timestamps were extracted without AI romanization because "
                    "the selected model was unavailable."
                )
    else:
        tracks = deterministic_tracks

    if not tracks:
        reason = deterministic_error or "No usable timestamp evidence was returned."
        if ai_error:
            reason = f"AI romanization failed: {ai_error}; deterministic evidence: {reason}"
        raise LookupError(
            "No validated timestamped track list could be extracted. "
            f"{reason}"
        )
    if mixed_albums:
        _enrich_mixed_track_catalog_metadata(tracks, video_title)
    elif album_name.strip():
        try:
            wikipedia_artists = find_wikipedia_track_artists(album_name, release_year)
        except Exception:
            wikipedia_artists = {}
        for track in tracks:
            if not isinstance(track, dict) or not track:
                continue
            title, values = next(iter(track.items()))
            wiki_artist = match_wikipedia_artist(str(title), wikipedia_artists)
            if wiki_artist and isinstance(values, dict):
                values["artists"] = wiki_artist
    _normalize_track_artist_fields(tracks)
    return timestamp_text, tracks


def _single_video_url(url: str) -> str:
    """Drop playlist/radio parameters so yt-dlp inspects only the selected video."""

    match = re.search(
        r"(?:[?&]v=|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})",
        str(url or ""),
    )
    return f"https://www.youtube.com/watch?v={match.group(1)}" if match else url.strip()


def _normalize_track_artist_fields(tracks: list[dict]) -> None:
    """Apply the shared comma-separated artist format to every extracted track."""
    for track in tracks:
        if not isinstance(track, dict) or not track:
            continue
        _title, values = next(iter(track.items()))
        if not isinstance(values, dict):
            continue
        artists = format_artist_names(str(values.get("artists") or ""))
        values["artists"] = artists or "Unknown"


def _enrich_mixed_track_catalog_metadata(
    tracks: list[dict], video_title: str = ""
) -> None:
    """Fill missing per-song album facts from a strict, key-free catalog match."""

    common_artist = _common_artist_from_video_title(video_title)
    candidates: list[tuple[dict[str, Any], str, str]] = []
    for track in tracks[:100]:
        if not isinstance(track, dict) or not track:
            continue
        title, values = next(iter(track.items()))
        if not isinstance(values, dict):
            continue
        artists = str(values.get("artists") or "").strip()
        album = str(values.get("album") or "").strip()
        if artists.casefold() in {"", "unknown"}:
            artists = common_artist
        if album.casefold() not in {"", "unknown"}:
            continue
        candidates.append((values, str(title), artists))

    def lookup(item: tuple[dict[str, Any], str, str]) -> tuple[dict[str, Any], dict[str, str]]:
        values, title, artists = item
        return values, find_catalog_song_metadata(title, artists, timeout=8)

    with ThreadPoolExecutor(max_workers=min(4, len(candidates) or 1)) as executor:
        for values, evidence in executor.map(lookup, candidates):
            if not evidence:
                continue
            values["album"] = evidence.get("album") or "Unknown"
            if str(values.get("artists") or "").casefold() in {"", "unknown"}:
                values["artists"] = evidence.get("artists") or "Unknown"
            values["release_year"] = evidence.get("year") or ""
            values["album_art"] = evidence.get("album_art") or ""
    for track in tracks:
        if isinstance(track, dict) and track:
            _title, values = next(iter(track.items()))
            if isinstance(values, dict):
                values.setdefault("album", "Unknown")
                values.setdefault("release_year", "")
                values.setdefault("album_art", "")


def _common_artist_from_video_title(video_title: str) -> str:
    """Return only an artist explicitly identified by a narrow title phrase."""

    match = re.search(
        r"\b(?:hits|songs)\s+of\s+([^|([\]{}]{2,60})",
        str(video_title or ""),
        flags=re.IGNORECASE,
    )
    return " ".join(match.group(1).split()).strip(" -") if match else ""


def _chapters_to_timestamp_text(chapters: list[Any]) -> str:
    lines: list[str] = []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        title = _clean_title(str(chapter.get("title") or ""))
        try:
            start = int(float(chapter.get("start_time") or 0))
        except (TypeError, ValueError):
            continue
        if title:
            lines.append(f"{format_seconds_as_timestamp(start)} - {title}")
    if not lines:
        raise LookupError("No usable YouTube chapters were found.")
    return "\n".join(lines)


def _parse_and_validate_tracks(timestamp_text: str, duration: int) -> list[dict]:
    payload = parse_tracks_text(timestamp_text, end_field="end", title_case=True)
    tracks = payload.get("tracks", [])
    if not tracks:
        raise LookupError("The timestamp parser could not create tracks from the evidence.")
    previous = -1
    names: set[str] = set()
    validated: list[dict] = []
    skipped_out_of_range: list[str] = []
    for index, track in enumerate(tracks, start=1):
        if not isinstance(track, dict) or not track:
            raise ValueError(f"Track #{index} is malformed.")
        title, values = next(iter(track.items()))
        if not isinstance(values, dict):
            raise ValueError(f"Track {title!r} has malformed metadata.")
        start = parse_timestamp_to_seconds(str(values.get("start") or ""))
        if start <= previous:
            raise ValueError("Track timestamps must be strictly increasing.")
        if duration > 0 and start >= duration:
            skipped_out_of_range.append(str(title))
            continue
        key = _key(str(title))
        if not key or key in names:
            raise ValueError(f"Track title {title!r} is empty or duplicated.")
        names.add(key)
        previous = start
        validated.append(track)
    if not validated:
        raise ValueError("Every extracted track starts after the video ends.")
    if skipped_out_of_range:
        print(
            "[TIMESTAMP-WARNING] Skipped out-of-range track starts after the "
            "video duration: " + ", ".join(skipped_out_of_range[:5])
        )
    return validated


def _agentic_track_extraction(
    *,
    video_title: str,
    description: str,
    chapters: list[Any],
    duration: int,
    model: str,
) -> tuple[str, list[dict]]:
    evidence = {
        "video_title": video_title,
        "duration_seconds": duration,
        "chapters": chapters[:200],
        "description": description[:40_000],
    }
    extraction = chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are the extraction agent for a music jukebox. Extract every real song "
                    "timestamp from the supplied YouTube evidence. Ignore promotional links and "
                    "credit timestamps that are not track starts. Use only evidence: never invent "
                    "titles, artists, albums, or years. For original_title, copy the exact source "
                    "title from the evidence. For title, romanize original_title phonetically into "
                    "Latin script so a reader can pronounce it; never translate its meaning. For "
                    "example, Bengali 'বেঁচে থাকার গান' becomes 'Benche Thakar Gaan', not an English "
                    "meaning. Keep an already-Latin title unchanged. Apply the same phonetic "
                    "romanization to non-Latin artist and album names. Prefer plain ASCII Latin "
                    "letters when natural. A jukebox may contain songs from different "
                    "albums, so preserve a per-track album only when the description/title states "
                    "it; otherwise use Unknown. Normalize start as M:SS or H:MM:SS."
                ),
            },
            {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)},
        ],
        _AGENT_TRACK_SCHEMA,
        model=model,
        timeout=90,
        temperature=0,
        max_tokens=8192,
    ).data
    proposed = _clean_agent_tracks(extraction.get("tracks"), duration)
    if not proposed:
        raise LookupError("The extraction agent returned no validated tracks.")
    search_evidence = _collect_track_search_evidence(proposed)

    review = chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are an independent validation agent. Compare the proposed jukebox tracks "
                    "against the supplied YouTube evidence. Remove advertisements and non-song "
                    "timestamps, restore omitted visible songs, and correct only facts explicitly "
                    "present in evidence. Never guess an album or artist. Require original_title to "
                    "match the source evidence and title to be its phonetic Latin-script "
                    "romanization, not a translation of its meaning. Romanize non-Latin artist and "
                    "album names the same way and keep already-Latin text unchanged. Return accepted true only "
                    "when timestamps are ordered, unique, inside duration, and evidence-supported."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "evidence": evidence,
                        "proposed_tracks": proposed,
                        "independent_youtube_search_evidence": search_evidence,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        _REVIEW_SCHEMA,
        model=model,
        timeout=90,
        temperature=0,
        max_tokens=8192,
    ).data
    reviewed = _clean_agent_tracks(review.get("tracks"), duration)
    if not bool(review.get("accepted")) or not reviewed:
        issues = "; ".join(str(value) for value in review.get("issues", [])[:5])
        raise ValueError(f"Validation agent rejected extraction: {issues or 'unspecified'}")

    timestamp_text = "\n".join(
        f"{item['start']} - {item['title']}"
        + (f" by {item['artists']}" if item["artists"] != "Unknown" else "")
        for item in reviewed
    )
    tracks = _parse_and_validate_tracks(timestamp_text, duration)
    for parsed, evidence_track in zip(tracks, reviewed, strict=True):
        _title, values = next(iter(parsed.items()))
        values["album"] = evidence_track["album"]
        values["release_year"] = evidence_track["release_year"]
    return timestamp_text, tracks


def _collect_track_search_evidence(
    tracks: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    """Search unresolved tracks and give result titles to the review agent."""

    unresolved = [
        track
        for track in tracks[:100]
        if track.get("album", "").casefold() in {"", "unknown"}
    ]

    def search(track: dict[str, str]) -> tuple[str, list[dict[str, str]]]:
        import yt_dlp

        query = " ".join(
            value
            for value in (
                track.get("title", ""),
                "" if track.get("artists", "").casefold() == "unknown" else track.get("artists", ""),
                "movie album official audio",
            )
            if value
        )
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                response = downloader.extract_info(f"ytsearch4:{query}", download=False)
        except Exception:
            return track.get("title", ""), []
        entries = response.get("entries", []) if isinstance(response, dict) else []
        matches = [
            {
                "title": str(entry.get("title") or ""),
                "channel": str(entry.get("channel") or entry.get("uploader") or ""),
            }
            for entry in entries
            if isinstance(entry, dict) and entry.get("title")
        ]
        return track.get("title", ""), matches

    with ThreadPoolExecutor(max_workers=min(4, len(unresolved) or 1)) as executor:
        return {
            title: matches
            for title, matches in executor.map(search, unresolved)
            if title and matches
        }


def _clean_agent_tracks(value: object, duration: int) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    previous = -1
    names: set[str] = set()
    for raw in value[:250]:
        if not isinstance(raw, dict):
            continue
        title = " ".join(str(raw.get("title") or "").split()).strip()
        original_title = " ".join(
            str(raw.get("original_title") or raw.get("title") or "").split()
        ).strip()
        try:
            seconds = parse_timestamp_to_seconds(str(raw.get("start") or ""))
        except ValueError:
            continue
        key = _key(title)
        original_key = _key(original_title)
        if (
            not title
            or not key
            or not original_key
            or not _uses_only_latin_letters(title)
            or key in names
            or seconds <= previous
        ):
            continue
        if duration > 0 and seconds >= duration:
            continue
        names.add(key)
        previous = seconds
        year = " ".join(str(raw.get("release_year") or "").split())
        if year and not re.fullmatch(r"(?:19|20)\d{2}", year):
            year = ""
        result.append(
            {
                "start": format_seconds_as_timestamp(seconds),
                "original_title": original_title,
                "title": title,
                "artists": format_artist_names(
                    " ".join(str(raw.get("artists") or "").split())
                )
                or "Unknown",
                "album": " ".join(str(raw.get("album") or "").split()) or "Unknown",
                "release_year": year,
            }
        )
    return result
