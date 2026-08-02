"""Evidence-bounded local AI adjudication for music metadata."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

from youtube_audio_video_downloader.services.album_names import normalize_album_name
from youtube_audio_video_downloader.services.ai_provider import chat_json


@dataclass(frozen=True, slots=True)
class MetadataAgentDecision:
    """A validated decision; review decisions must never mutate media."""

    action: str
    metadata: dict[str, str]
    confidence: float
    reason: str
    sources: tuple[str, ...]


_FIELDS = ("title", "album", "artists", "year", "language")
_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["apply", "review"]},
        "title": {"type": "string"},
        "album": {"type": "string"},
        "artists": {"type": "string"},
        "year": {"type": "string"},
        "language": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action", *_FIELDS, "confidence", "reason", "sources"],
    "additionalProperties": False,
}


def adjudicate_metadata(
    local: Mapping[str, object],
    wikipedia: Mapping[str, object],
    catalog: Mapping[str, object],
    *,
    serpapi: Mapping[str, object] | None = None,
    model: str,
    catalog_duration_matches: bool | None = None,
    timeout: float = 90.0,
) -> MetadataAgentDecision:
    """Ask the configured AI chain to select facts, then independently validate them."""

    selected_model = str(model or "").strip()
    evidence = {
        "local": _clean_source(local),
        "wikipedia": _clean_source(wikipedia),
        "catalog": _clean_source(catalog),
        "serpapi": _clean_source(serpapi or {}),
    }
    if not selected_model:
        return MetadataAgentDecision("review", {}, 0.0, "No agentic model selected", ())
    messages = [
            {
                "role": "system",
                "content": (
                    "You adjudicate music metadata using only the supplied evidence. Never invent "
                    "a title, album, artist, year, or language. Distinguish original releases from "
                    "digital reissues and same-title films by artist, duration, language, and album "
                    "membership. A populated local album is user-owned and immutable: never replace "
                    "it with a playlist, compilation, editorial collection, reissue, or any other "
                    "album returned by a provider. External evidence may fill a missing album, but "
                    "a conflict with an existing album must be returned for review. "
                    "Choose review whenever the external identities or complete artist credits are "
                    "ambiguous. Return apply only at confidence >= 0.85."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "evidence": evidence,
                        "catalog_duration_matches_local_recording": catalog_duration_matches,
                        "required_policy": (
                            "Every non-empty output field must appear in at least one evidence source."
                        ),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
    try:
        proposed = chat_json(
            messages,
            _SCHEMA,
            model=selected_model,
            timeout=timeout,
            temperature=0,
        ).data
    except Exception as exc:
        return MetadataAgentDecision(
            "review", {}, 0.0, f"Agent unavailable: {exc}", ()
        )
    return _validate_decision(proposed, evidence, catalog_duration_matches)


def evidence_conflicts(
    local: Mapping[str, object],
    wikipedia: Mapping[str, object],
    catalog: Mapping[str, object],
    serpapi: Mapping[str, object] | None = None,
) -> bool:
    """Return whether sources disagree on a populated identity field."""

    sources = (
        _clean_source(local),
        _clean_source(wikipedia),
        _clean_source(catalog),
        _clean_source(serpapi or {}),
    )
    for field in ("album", "year", "language"):
        values = {_key(field, source.get(field, "")) for source in sources}
        values.discard("")
        if len(values) > 1:
            return True
    return False


def _validate_decision(
    proposed: object,
    evidence: dict[str, dict[str, str]],
    catalog_duration_matches: bool | None,
) -> MetadataAgentDecision:
    if not isinstance(proposed, dict):
        return MetadataAgentDecision("review", {}, 0.0, "Invalid agent response", ())
    metadata = {field: str(proposed.get(field) or "").strip() for field in _FIELDS}
    ignored_optional_fields: list[str] = []
    for field, value in metadata.items():
        if not value:
            continue
        allowed = {
            _key(field, source.get(field, ""))
            for source in evidence.values()
            if source.get(field)
        }
        if _key(field, value) not in allowed:
            if field == "language":
                metadata[field] = ""
                ignored_optional_fields.append(field)
                continue
            return MetadataAgentDecision(
                "review", {}, 0.0, f"Agent invented unsupported {field}: {value}", ()
            )
    try:
        confidence = min(1.0, max(0.0, float(proposed.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    action = str(proposed.get("action") or "review").strip().lower()
    reason = str(proposed.get("reason") or "No explanation supplied").strip()
    if ignored_optional_fields:
        reason += "; ignored unsupported optional language"
    sources = tuple(
        source for source in proposed.get("sources", [])
        if str(source) in evidence and evidence[str(source)]
    ) if isinstance(proposed.get("sources"), list) else ()
    wiki_album = _key("album", evidence["wikipedia"].get("album", ""))
    catalog_album = _key("album", evidence["catalog"].get("album", ""))
    if wiki_album and catalog_album and wiki_album != catalog_album:
        if catalog_duration_matches is not True:
            action = "review"
            confidence = min(confidence, 0.6)
            reason = "Conflicting album candidates without a matching recording duration"
    if action != "apply" or confidence < 0.85 or not sources:
        return MetadataAgentDecision("review", metadata, confidence, reason, sources)
    return MetadataAgentDecision("apply", metadata, confidence, reason, sources)


def _clean_source(source: Mapping[str, object]) -> dict[str, str]:
    return {
        field: (
            normalize_album_name(source.get(field))
            if field == "album" else str(source.get(field) or "").strip()
        )
        for field in _FIELDS
        if str(source.get(field) or "").strip()
    }


def _key(field: str, value: object) -> str:
    text = normalize_album_name(value) if field == "album" else str(value or "")
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
