"""Find duplicate YouTube links in project JSON files."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from youtube_audio_video_downloader.utils.json_utils import load_json_object

DEFAULT_LINK_FIELDS = ("ytb_link", "video_url", "youtube_url", "url")


def normalize_url(url: str) -> str:
    """Normalize URLs for duplicate comparison."""

    return str(url or "").strip()


def find_duplicate_youtube_links(
    json_file: Path,
    *,
    link_fields: Iterable[str] = DEFAULT_LINK_FIELDS,
) -> list[dict[str, Any]]:
    """Find top-level JSON entries that point to the same YouTube URL.

    The utility is useful before batch downloads because duplicate links usually
    mean one of two things:
      1. the same song/video is intentionally listed more than once, or
      2. a metadata copy/paste mistake will download/tag the wrong media.
    """

    data = load_json_object(Path(json_file), allow_comments=True)
    return find_duplicate_youtube_links_in_data(data, link_fields=link_fields)


def find_duplicate_youtube_links_in_data(
    data: dict[str, Any],
    *,
    link_fields: Iterable[str] = DEFAULT_LINK_FIELDS,
) -> list[dict[str, Any]]:
    """Find duplicate links in an already-loaded project object."""

    fields = tuple(link_fields)
    link_to_entries: dict[str, list[str]] = defaultdict(list)

    for entry_name, entry_data in data.items():
        if not isinstance(entry_data, dict):
            continue

        link = _extract_link(entry_data, fields)
        if not link:
            continue

        link_to_entries[link].append(str(entry_name))

    return [
        {
            "ytb_link": link,
            "count": len(entry_names),
            "entries": entry_names,
            "songs": entry_names,  # backward-compatible key for older scripts
        }
        for link, entry_names in link_to_entries.items()
        if len(entry_names) >= 2
    ]


def _extract_link(entry_data: dict[str, Any], link_fields: tuple[str, ...]) -> str:
    """Return the first non-empty link from supported field names."""

    for field_name in link_fields:
        link = normalize_url(str(entry_data.get(field_name) or ""))
        if link:
            return link
    return ""
