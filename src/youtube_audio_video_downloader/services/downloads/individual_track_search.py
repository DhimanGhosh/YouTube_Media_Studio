"""Find close-duration YouTube audio results for Wikipedia album tracks."""

from __future__ import annotations

import re
from typing import Any, Callable

from youtube_audio_video_downloader.services.albums.wikipedia_tracks import find_wikipedia_tracks


_VARIANT_TERMS = {
    "reprise", "reprised", "encore", "film", "arabic", "version", "remix", "mix",
    "male", "female",
}


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

    wikipedia_tracks = find_wikipedia_tracks(album_name, release_year)
    if not wikipedia_tracks:
        raise LookupError(f'Wikipedia has no usable soundtrack table for "{album_name}".')
    results: list[dict] = []
    options: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": "in_playlist", "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        for track in wikipedia_tracks:
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
            query = f"{album_name} {title} {artists} full song audio lyrical"
            search = downloader.extract_info(f"ytsearch8:{query}", download=False)
            entries = search.get("entries", []) if isinstance(search, dict) else []
            candidates = []
            title_key = _key(title)
            wanted_tokens = set(title_key.split()) - {"ft", "feat", "featuring"}
            variant_words = wanted_tokens & _VARIANT_TERMS
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                candidate_key = _key(str(entry.get("title") or ""))
                candidate_tokens = set(candidate_key.split())
                overlap = len(wanted_tokens & candidate_tokens) / max(1, len(wanted_tokens))
                if overlap < 0.65:
                    continue
                # Never substitute a base song for a named remix/reprise/version,
                # or a variant for the canonical base song. Matching singer names
                # are not enough to prove that the recording is the right version.
                if not _variant_compatible(variant_words, candidate_tokens & _VARIANT_TERMS):
                    continue
                duration = int(float(entry.get("duration") or 0))
                if duration <= 0:
                    continue
                difference = abs(duration - expected) if expected else 0
                tolerance = max(25, int(expected * 0.12)) if expected else 10_000
                if expected and difference > tolerance:
                    continue
                candidate_title = str(entry.get("title") or "").lower()
                if _is_lofi(candidate_title) or any(
                    word in candidate_title for word in ("cover", "reaction", "status", "shorts")
                ):
                    continue
                candidates.append((
                    difference,
                    not bool(entry.get("channel_is_verified")),
                    -int(entry.get("view_count") or 0),
                    entry,
                ))
            if not candidates:
                results.append({title: {
                    "ytb_link": "",
                    "start": "",
                    "end": "",
                    "artists": artists,
                    "download": "false",
                    "match_status": "No safe close-duration YouTube match",
                }})
                continue
            chosen = sorted(candidates, key=lambda item: item[:3])[0][3]
            video_id = str(chosen.get("id") or "")
            url = str(chosen.get("webpage_url") or chosen.get("url") or "")
            if not url.startswith(("http://", "https://")):
                url = f"https://www.youtube.com/watch?v={video_id}"
            results.append({title: {
                "ytb_link": url,
                "start": "",
                "end": "",
                "artists": artists,
                "download": "true",
                "match_status": "Matched",
            }})
    if not any(next(iter(track.values())).get("ytb_link") for track in results):
        raise LookupError(f'No close-duration YouTube tracks were found for "{album_name}".')
    return results


def _variant_compatible(expected: set[str], candidate: set[str]) -> bool:
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
