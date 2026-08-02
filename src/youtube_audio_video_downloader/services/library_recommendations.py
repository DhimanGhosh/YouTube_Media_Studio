"""AI recommendations grounded exclusively in the indexed local media library."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from youtube_audio_video_downloader.services.ai_provider import chat_json
from youtube_audio_video_downloader.services.media_library import LibraryItem, split_artists


MAX_LIBRARY_CANDIDATES = 750
MAX_RECOMMENDATIONS = 12


@dataclass(frozen=True, slots=True)
class LibraryRecommendation:
    item: LibraryItem
    reason: str
    exists_locally: bool


_RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "maxItems": MAX_RECOMMENDATIONS,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["recommendations"],
    "additionalProperties": False,
}


def recommend_library_tracks(
    request_text: str,
    items: Iterable[LibraryItem],
    *,
    model: str,
    limit: int = 8,
    timeout: float = 90,
) -> list[LibraryRecommendation]:
    """Ask the AI provider chain to select tracks, rejecting non-indexed responses."""

    request_text = request_text.strip()
    if not request_text:
        raise ValueError("Describe an artist, genre, mood, or listening occasion.")
    selected_model = model.strip()
    if not selected_model:
        raise ValueError("Configure an agentic model in Global Settings first.")
    bounded_limit = max(1, min(int(limit), MAX_RECOMMENDATIONS))
    all_items = list(items)
    artist_intent = _requested_artists(request_text, all_items)
    candidates = _bounded_candidates(all_items, request_text, artist_intent=artist_intent)
    if not candidates:
        if artist_intent:
            return []
        raise ValueError("The indexed library has no tracks to recommend.")

    catalog = [
        {
            "id": index,
            "title": item.title,
            "artists": item.artists,
            "album": item.album,
            "year": item.year,
            "type": item.media_type,
        }
        for index, item in enumerate(candidates)
    ]
    messages = [
            {
                "role": "system",
                "content": (
                    "You are a local media-library DJ. Select only IDs from the supplied "
                    "catalog. Use only its title, artist, album, year, and media type; do not "
                    "claim web knowledge or invent tracks, genres, facts, or IDs. Interpret "
                    "artist, genre, and mood requests conservatively. Return diverse, relevant "
                    f"choices, at most {bounded_limit}. Give one short reason grounded in the "
                    "supplied metadata for each choice. If evidence is insufficient, return fewer."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"request": request_text, "catalog": catalog},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]
    try:
        payload = chat_json(
            messages,
            _RECOMMENDATION_SCHEMA,
            model=selected_model,
            timeout=timeout,
            temperature=0.2,
            max_tokens=1200,
        ).data
    except Exception as exc:
        print(f"[AI-STATIC-FALLBACK] Library ranking | {exc}")
        payload = {
            "recommendations": [
                {"id": index, "reason": "Deterministic metadata ranking"}
                for index in range(min(bounded_limit, len(candidates)))
            ]
        }
    rows = payload.get("recommendations", []) if isinstance(payload, dict) else []

    recommendations: list[LibraryRecommendation] = []
    seen: set[int] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or isinstance(row.get("id"), bool):
            continue
        candidate_id = row.get("id")
        if not isinstance(candidate_id, int) or candidate_id in seen:
            continue
        if not 0 <= candidate_id < len(candidates):
            continue
        seen.add(candidate_id)
        item = candidates[candidate_id]
        if artist_intent and not _item_matches_artists(item, artist_intent):
            continue
        reason = _grounded_reason(item, request_text, artist_intent)
        recommendations.append(
            LibraryRecommendation(item, reason, Path(item.path).is_file())
        )
        if len(recommendations) >= bounded_limit:
            break
    return recommendations


def _bounded_candidates(
    items: Iterable[LibraryItem],
    request_text: str,
    *,
    artist_intent: tuple[str, ...] = (),
) -> list[LibraryItem]:
    """Keep requests bounded while favoring literal artist/title/album matches."""

    unique = {item.path.casefold(): item for item in items}
    if artist_intent:
        unique = {
            path: item
            for path, item in unique.items()
            if _item_matches_artists(item, artist_intent)
        }
    tokens = {token.casefold() for token in request_text.split() if len(token) > 2}

    def rank(item: LibraryItem) -> tuple[int, str, str]:
        metadata = f"{item.title} {item.artists} {item.album}".casefold()
        matches = sum(token in metadata for token in tokens)
        return (-matches, item.artists.casefold(), item.title.casefold())

    return sorted(unique.values(), key=rank)[:MAX_LIBRARY_CANDIDATES]


def _requested_artists(
    request_text: str, items: Iterable[LibraryItem]
) -> tuple[str, ...]:
    """Resolve explicit artist words against indexed artists without model guessing."""

    request_key = _text_key(request_text)
    request_tokens = set(request_key.split())
    artists = sorted(
        {
            artist.strip()
            for item in items
            for artist in split_artists(item.artists)
            if artist.strip()
        },
        key=str.casefold,
    )
    exact = [artist for artist in artists if _phrase_in(_text_key(artist), request_key)]
    if exact:
        return tuple(exact)

    generic = {"song", "songs", "music", "artist", "singer", "playlist", "play"}
    token_owners: dict[str, set[str]] = {}
    for artist in artists:
        for token in _text_key(artist).split():
            if len(token) >= 3 and token not in generic:
                token_owners.setdefault(token, set()).add(artist)
    resolved = {
        next(iter(token_owners[token]))
        for token in request_tokens
        if token in token_owners and len(token_owners[token]) == 1
    }
    if resolved:
        return tuple(sorted(resolved, key=str.casefold))

    words = request_key.split()
    if words and words[-1] in {"song", "songs"}:
        filler = {"play", "recommend", "suggest", "some", "me", "please"}
        descriptors = {
            "calm", "happy", "sad", "romantic", "party", "workout", "focus",
            "relaxing", "sleep", "mood", "road", "trip", "rainy", "morning",
        }
        hint_words = [word for word in words[:-1] if word not in filler]
        if hint_words and not set(hint_words) & descriptors:
            return (" ".join(hint_words).title(),)
    return ()


def _item_matches_artists(item: LibraryItem, requested: tuple[str, ...]) -> bool:
    item_artists = {_text_key(value) for value in split_artists(item.artists)}
    return any(_text_key(artist) in item_artists for artist in requested)


def _grounded_reason(
    item: LibraryItem, request_text: str, artist_intent: tuple[str, ...]
) -> str:
    """Create display text only from literal indexed metadata, never model prose."""

    if artist_intent:
        matched = [
            artist for artist in artist_intent
            if _item_matches_artists(item, (artist,))
        ]
        return "Artist matches " + ", ".join(matched)
    request_tokens = {
        token for token in _text_key(request_text).split()
        if len(token) > 2 and token not in {"song", "songs", "music", "playlist"}
    }
    metadata = _text_key(f"{item.title} {item.artists} {item.album}")
    matched_tokens = sorted(token for token in request_tokens if token in metadata)
    if matched_tokens:
        return "Metadata matches: " + ", ".join(matched_tokens[:4])
    return "Selected from indexed library metadata"


def _text_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _phrase_in(phrase: str, text: str) -> bool:
    return bool(phrase and re.search(rf"(?:^| ){re.escape(phrase)}(?: |$)", text))
