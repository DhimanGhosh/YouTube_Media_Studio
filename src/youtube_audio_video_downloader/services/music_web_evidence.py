"""Bounded public-search evidence for semantic local-library classification."""

from __future__ import annotations

import json
import re

from agno.tools.duckduckgo import DuckDuckGoTools


def find_music_web_evidence(
    title: str,
    artists: str,
    filters: tuple[str, ...],
    *,
    timeout: float = 6,
    max_results: int = 3,
) -> str:
    """Return compact search-result excerpts without exposing local paths or credentials."""

    clean_title = title.strip()
    constraints = " ".join(value.strip() for value in filters if value.strip())
    queries = (
        f'"{clean_title}" {artists.strip()} {constraints} music'.strip(),
        f'"{clean_title}" {constraints} language'.strip(),
    )
    try:
        tool = DuckDuckGoTools(
            fixed_max_results=max_results,
            timeout=max(1, round(timeout)),
            backend="bing",
        )
    except Exception:
        return ""
    title_key = _text_key(clean_title)
    for query in queries:
        try:
            raw_evidence = tool.duckduckgo_search(query, max_results=max_results)
            rows = json.loads(raw_evidence)
        except Exception:
            continue
        excerpts = _identity_matched_excerpts(rows, title_key)
        if excerpts:
            # Keep the complete multi-song verifier prompt inside the local 16K
            # context budget; exact-song identity and useful semantic phrases occur
            # near the beginning of each bounded search excerpt.
            return " | ".join(excerpts)[:900]
    return ""


def _identity_matched_excerpts(rows: object, title_key: str) -> list[str]:
    """Discard unrelated search hits before their words can corroborate a constraint."""

    if not isinstance(rows, list) or not title_key:
        return []
    excerpts: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        body = str(row.get("body") or "").strip()
        text = " — ".join(value for value in (title, body) if value)
        if title_key in _text_key(text):
            excerpts.append(text[:600])
    return excerpts


def _text_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))
