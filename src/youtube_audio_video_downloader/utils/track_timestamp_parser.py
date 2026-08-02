"""Convert copied timestamp lists into album/jukebox track JSON snippets."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta

from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names
from youtube_audio_video_downloader.utils.time_utils import (
    format_seconds_as_timestamp,
    looks_like_timestamp,
    parse_timestamp_to_seconds,
)

_ARTIST_SEPARATOR_PATTERN = re.compile(r"\s+by\s+", flags=re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ParsedTrackLine:
    """One parsed track line from pasted timestamp text."""

    title: str
    start: str
    artists: str


def parse_tracks_text(
    tracks_text: str,
    *,
    end_field: str = "end",
    title_case: bool = True,
    unknown_artists: str = "Unknown",
) -> dict[str, list[dict[str, dict[str, str]]]]:
    """Parse timestamp lines into album/jukebox-compatible track JSON.

    Supported line formats:

    - ``Title - MM:SS``
    - ``Title - HH:MM:SS``
    - ``MM:SS - Title``
    - ``HH:MM:SS - Title``

    Artist extraction is also supported when a title contains ``by``:

    - ``00:14:36 - Jaan 'Nisaar by Arijit Singh``

    In that case, the output track title becomes ``Jaan 'Nisaar`` and the
    ``artists`` field becomes ``Arijit Singh``. When no artist is supplied, the
    output includes ``artists: Unknown`` by default.

    The end/stop time for every track except the last one is calculated as one
    second before the next parsed track starts. The final track receives a blank
    end/stop value so the splitter can run it until the source audio ends.
    """

    if end_field not in {"end", "stop"}:
        raise ValueError("end_field must be either 'end' or 'stop'")

    parsed_lines = _parse_lines(
        tracks_text,
        title_case=title_case,
        unknown_artists=unknown_artists,
    )
    tracks: list[dict[str, dict[str, str]]] = []

    for index, current_track in enumerate(parsed_lines):
        track_payload = {
            "start": current_track.start,
            end_field: "",
            "artists": current_track.artists,
        }

        if index + 1 < len(parsed_lines):
            next_start_seconds = parse_timestamp_to_seconds(parsed_lines[index + 1].start)
            # End time is one second before the next track starts.
            end_timedelta = timedelta(seconds=next_start_seconds) - timedelta(seconds=1)
            track_payload[end_field] = format_seconds_as_timestamp(
                int(end_timedelta.total_seconds())
            )

        tracks.append({current_track.title: track_payload})

    return {"tracks": tracks}


def parse_tracks_to_json(
    tracks_text: str,
    *,
    end_field: str = "end",
    title_case: bool = True,
    unknown_artists: str = "Unknown",
    indent: int = 2,
) -> str:
    """Return parsed timestamp text as formatted JSON."""

    parsed = parse_tracks_text(
        tracks_text,
        end_field=end_field,
        title_case=title_case,
        unknown_artists=unknown_artists,
    )
    return json.dumps(parsed, indent=indent, ensure_ascii=False)


def _parse_lines(
    tracks_text: str,
    *,
    title_case: bool,
    unknown_artists: str,
) -> list[ParsedTrackLine]:
    """Parse each non-empty line and skip unsupported rows."""

    parsed_lines: list[ParsedTrackLine] = []

    for raw_line in str(tracks_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        title, timestamp = _split_track_line(line)
        if not title or not timestamp:
            continue

        cleaned_title, artists = _extract_title_and_artists(
            title,
            title_case=title_case,
            unknown_artists=unknown_artists,
        )
        parsed_lines.append(
            ParsedTrackLine(
                title=cleaned_title,
                start=format_seconds_as_timestamp(parse_timestamp_to_seconds(timestamp)),
                artists=artists,
            )
        )

    return parsed_lines


def _split_track_line(line: str) -> tuple[str, str]:
    """Support both ``TIMESTAMP - TITLE`` and ``TITLE - TIMESTAMP``."""

    parts = line.split(" - ", maxsplit=1)
    if len(parts) != 2:
        return "", ""

    left = parts[0].strip()
    right = parts[1].strip()

    if looks_like_timestamp(left):
        return right, left

    if looks_like_timestamp(right):
        return left, right

    return "", ""


def _extract_title_and_artists(
    raw_title: str,
    *,
    title_case: bool,
    unknown_artists: str,
) -> tuple[str, str]:
    """Extract ``Track Title by Artist`` into title and normalized artists."""

    title_text = str(raw_title or "").strip()
    separator = _outside_parentheses_artist_separator(title_text)
    if separator is not None:
        title_text, artist_text = (
            title_text[: separator.start()].strip(),
            title_text[separator.end():].strip(),
        )
        artists = format_artist_names(artist_text) or unknown_artists
    else:
        artists = unknown_artists

    if title_case:
        title_text = title_text.title()

    return title_text.strip(), artists


def _outside_parentheses_artist_separator(title: str) -> re.Match[str] | None:
    """Find an artist ``by`` separator that is not part of a version label.

    A title such as ``Piya (Remix by DJ Suketu)`` must remain intact; only
    ``Piya (Remix by DJ Suketu) by Atif Aslam`` contains an artist suffix.
    """
    depth = 0
    depths: list[int] = []
    for character in title:
        depths.append(depth)
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth = max(0, depth - 1)
    matches = list(_ARTIST_SEPARATOR_PATTERN.finditer(title))
    for match in reversed(matches):
        if depths[match.start()] == 0:
            return match
    return None
