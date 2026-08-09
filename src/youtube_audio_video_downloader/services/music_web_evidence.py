"""Bounded public-search evidence for semantic local-library classification."""

from __future__ import annotations

from ddgs import DDGS


def find_music_web_evidence(
    title: str,
    artists: str,
    filters: tuple[str, ...],
    *,
    timeout: float = 6,
    max_results: int = 3,
) -> str:
    """Return compact search-result excerpts without exposing local paths or credentials."""

    identity = " ".join(part for part in (title.strip(), artists.strip()) if part)
    constraints = " ".join(value.strip() for value in filters if value.strip())
    query = f'"{identity}" {constraints} music'.strip()
    try:
        rows = DDGS(timeout=timeout).text(query, max_results=max_results)
    except Exception:
        return ""
    excerpts: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        text = " — ".join(
            value
            for value in (
                str(row.get("title") or "").strip(),
                str(row.get("body") or "").strip(),
            )
            if value
        )
        if text:
            excerpts.append(text[:600])
    return " | ".join(excerpts)[:1800]
