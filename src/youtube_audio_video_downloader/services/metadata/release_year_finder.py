"""Look up an album's release year from Wikipedia."""

from __future__ import annotations

import re

from youtube_audio_video_downloader.services.albums.wikipedia_tracks import _api
from youtube_audio_video_downloader.services.albums.album_names import normalize_album_name


_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_RELEASED_FIELD = re.compile(
    r"^\s*\|\s*(?:released|release_date)\s*=\s*(.+(?:\n(?!\s*\|).*)*)",
    re.IGNORECASE | re.MULTILINE,
)


def _wikipedia_api(params: dict[str, str], timeout: float) -> dict:
    return _api(params, timeout=timeout)


def extract_release_year(wikitext: str) -> str:
    """Extract the first year specifically from an infobox release field."""
    match = _RELEASED_FIELD.search(str(wikitext or ""))
    if not match:
        return ""
    year = _YEAR.search(match.group(1))
    return year.group(1) if year else ""


def _title_matches(requested: str, candidate: str) -> bool:
    requested_tokens = re.findall(r"[a-z0-9]+", requested.lower())
    candidate_tokens = re.findall(r"[a-z0-9]+", candidate.lower())
    size = len(requested_tokens)
    return bool(size) and any(
        candidate_tokens[index:index + size] == requested_tokens
        for index in range(len(candidate_tokens) - size + 1)
    )


def find_album_release_year(
    album_name: str,
    timeout: float = 12.0,
    *,
    exclude_year: str = "",
) -> dict[str, str]:
    """Return a release year, optionally skipping the currently displayed year."""
    name = normalize_album_name(album_name)
    if not name:
        raise ValueError("Enter an album name before searching for its release year.")
    results: list[dict] = []
    seen_titles: set[str] = set()
    for query in (f'{name} soundtrack album', f'"{name}" film'):
        search = _wikipedia_api(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": "8",
                "format": "json",
                "utf8": "1",
            },
            timeout,
        )
        for item in search.get("query", {}).get("search", []):
            title = str(item.get("title") or "").casefold()
            if title and title not in seen_titles:
                seen_titles.add(title)
                results.append(item)
        if any(_title_matches(name, str(item.get("title") or "")) for item in results):
            break
    if not results:
        raise LookupError(f'No Wikipedia album page was found for "{name}".')
    relevant = [item for item in results if _title_matches(name, str(item.get("title") or ""))]
    if not relevant:
        raise LookupError(f'No Wikipedia page title matched "{name}" exactly enough.')
    ranked = sorted(
        relevant,
        key=lambda item: (
            "soundtrack" not in str(item.get("title", "")).lower(),
        ),
    )
    for result in ranked:
        title = str(result.get("title") or "").strip()
        page = _wikipedia_api(
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "titles": title,
                "format": "json",
                "formatversion": "2",
            },
            timeout,
        )
        pages = page.get("query", {}).get("pages", [])
        if not pages:
            continue
        revisions = pages[0].get("revisions", [])
        if not revisions:
            continue
        wikitext = str(revisions[0].get("slots", {}).get("main", {}).get("content") or "")
        year = extract_release_year(wikitext)
        if year and year != exclude_year:
            return {"year": year, "page_title": title}
    qualifier = "a different release year" if exclude_year else "a release year"
    raise LookupError(f'Wikipedia did not provide {qualifier} for "{name}".')
