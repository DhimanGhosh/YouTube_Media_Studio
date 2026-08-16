"""Central policy gate for evidence-backed music metadata decisions.

This module deliberately does not search the network.  Callers collect local,
Wikipedia, and catalog observations, then submit them here before mutating a
media file.  The language model is an adjudicator, not a source of facts: its
answer is accepted only when it describes one compatible evidence identity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from youtube_audio_video_downloader.services.albums.album_names import normalize_album_name
from youtube_audio_video_downloader.services.ai.metadata_agent import adjudicate_metadata


_FIELDS = ("title", "album", "artists", "year", "language")
_SOURCE_NAMES = ("wikipedia", "catalog", "serpapi")


@dataclass(frozen=True, slots=True)
class MetadataVerificationDecision:
    """A mutation-safe metadata decision made from already gathered evidence."""

    action: str
    metadata: dict[str, str]
    album_art: str
    confidence: float
    reason: str
    sources: tuple[str, ...]
    rejected_sources: tuple[str, ...] = ()


def verify_metadata(
    local: Mapping[str, object],
    wikipedia: Mapping[str, object],
    catalog: Mapping[str, object],
    *,
    serpapi: Mapping[str, object] | None = None,
    model: str,
    catalog_duration_matches: bool | None = None,
) -> MetadataVerificationDecision:
    """Adjudicate evidence and return ``apply`` only for one coherent identity.

    A configured model is always called, including when every external source
    was rejected.  This keeps reasoning and review explanations centralized,
    while deterministic validation remains the final authority.
    """

    cleaned_local = _clean(local)
    accepted: dict[str, dict[str, str]] = {}
    rejected: list[str] = []
    for name, source in (
        ("wikipedia", wikipedia),
        ("catalog", catalog),
        ("serpapi", serpapi or {}),
    ):
        candidate = _clean(source)
        conflict = _local_conflict(cleaned_local, candidate)
        if conflict:
            rejected.append(f"{name}: {conflict}")
        elif candidate:
            accepted[name] = candidate

    selected_model = str(model or "").strip()
    if not selected_model:
        return _deterministic_fallback(
            cleaned_local,
            accepted,
            catalog_duration_matches=catalog_duration_matches,
            rejected_sources=rejected,
            trigger="No agentic model selected",
        )

    print(f"[AI-START] Metadata identity verification | model={selected_model}")

    # Always invoke the adjudicator when configured.  Rejected observations are
    # intentionally withheld so the model cannot revive a deterministically
    # incompatible title or artist identity.
    agent = adjudicate_metadata(
        cleaned_local,
        accepted.get("wikipedia", {}),
        accepted.get("catalog", {}),
        serpapi=accepted.get("serpapi", {}),
        model=selected_model,
        catalog_duration_matches=(
            catalog_duration_matches if "catalog" in accepted else None
        ),
    )
    if agent.action != "apply":
        if agent.reason.casefold().startswith("agent unavailable:"):
            return _deterministic_fallback(
                cleaned_local,
                accepted,
                catalog_duration_matches=catalog_duration_matches,
                rejected_sources=rejected,
                trigger=agent.reason,
            )
        return _review(
            agent.reason,
            confidence=agent.confidence,
            sources=agent.sources,
            rejected_sources=rejected,
        )

    proposed = _clean(agent.metadata)
    proposed_artists = _artists(proposed.get("artists", ""))
    required_artists = _artists(cleaned_local.get("artists", ""))
    if required_artists and not (
        proposed_artists and _artists_cover(required_artists, proposed_artists)
    ):
        return _review(
            "Agent decision does not preserve the full known artist set",
            confidence=min(agent.confidence, 0.6),
            sources=agent.sources,
            rejected_sources=rejected,
        )
    groups = _compatible_groups(accepted)
    chosen_group = next(
        (group for group in groups if _proposal_belongs_to_group(proposed, group)),
        None,
    )
    if chosen_group is None:
        return _review(
            "Agent decision combines fields from incompatible evidence identities",
            confidence=min(agent.confidence, 0.6),
            sources=agent.sources,
            rejected_sources=rejected,
        )

    selected_sources = tuple(
        name for name in agent.sources if name in chosen_group
    ) or tuple(chosen_group)
    artwork = _artwork_for_group(chosen_group, selected_sources)
    result = MetadataVerificationDecision(
        action="apply",
        metadata={field: proposed.get(field, "") for field in _FIELDS},
        album_art=artwork,
        confidence=agent.confidence,
        reason=agent.reason,
        sources=selected_sources,
        rejected_sources=tuple(rejected),
    )
    print(
        f"[AI-VERIFIED] confidence={result.confidence:.0%} | "
        f"sources={', '.join(result.sources) or 'external evidence'}"
    )
    return result


def _deterministic_fallback(
    local: Mapping[str, str],
    accepted: Mapping[str, dict[str, str]],
    *,
    catalog_duration_matches: bool | None,
    rejected_sources: list[str],
    trigger: str,
) -> MetadataVerificationDecision:
    """Select one already-fetched coherent identity without model inference."""

    groups = _compatible_groups(accepted)
    eligible = [
        group
        for group in groups
        if any(source.get("title") and source.get("album") for source in group.values())
    ]
    chosen = next((group for group in eligible if len(group) > 1), None)
    selection_reason = "Wikipedia and catalog agree on one identity"
    if chosen is None and catalog_duration_matches:
        chosen = next((group for group in eligible if "catalog" in group), None)
        selection_reason = "Catalog identity matches the local recording duration"
    if chosen is None and len(eligible) == 1:
        chosen = eligible[0]
        selection_reason = "One exact external identity passed deterministic checks"
    if chosen is None:
        return _review(
            f"{trigger}; deterministic internet evidence is absent or ambiguous",
            rejected_sources=rejected_sources,
        )

    metadata: dict[str, str] = {}
    for source_name in _SOURCE_NAMES:
        source = chosen.get(source_name, {})
        for field in _FIELDS:
            if source.get(field) and not metadata.get(field):
                metadata[field] = source[field]
    required_artists = _artists(local.get("artists", ""))
    selected_artists = _artists(metadata.get("artists", ""))
    if required_artists and not (
        selected_artists and _artists_cover(required_artists, selected_artists)
    ):
        return _review(
            f"{trigger}; deterministic identity does not preserve the full artist set",
            rejected_sources=rejected_sources,
        )
    sources = tuple(chosen)
    confidence = 0.92 if len(chosen) > 1 else 0.88 if catalog_duration_matches else 0.85
    result = MetadataVerificationDecision(
        "apply",
        {field: metadata.get(field, "") for field in _FIELDS},
        _artwork_for_group(chosen, sources),
        confidence,
        f"{selection_reason}; AI provider chain unavailable",
        sources,
        tuple(rejected_sources),
    )
    print(
        f"[AI-STATIC-FALLBACK] {selection_reason} | "
        f"sources={', '.join(sources)}"
    )
    return result


def verify_metadata_evidence(
    local: Mapping[str, object],
    wikipedia: Mapping[str, object],
    catalog: Mapping[str, object],
    *,
    serpapi: Mapping[str, object] | None = None,
    model: str,
    catalog_duration_matches: bool | None = None,
) -> MetadataVerificationDecision:
    """Descriptive alias retained for service-layer call sites."""

    return verify_metadata(
        local,
        wikipedia,
        catalog,
        serpapi=serpapi,
        model=model,
        catalog_duration_matches=catalog_duration_matches,
    )


def _review(
    reason: str,
    *,
    confidence: float = 0.0,
    sources: tuple[str, ...] = (),
    rejected_sources: list[str] | tuple[str, ...] = (),
) -> MetadataVerificationDecision:
    result = MetadataVerificationDecision(
        "review",
        {},
        "",
        float(confidence),
        str(reason or "Metadata review required"),
        tuple(sources),
        tuple(rejected_sources),
    )
    print(f"[AI-REVIEW] {result.reason} | no changes applied")
    return result


def _clean(source: Mapping[str, object]) -> dict[str, str]:
    values = {
        field: (
            normalize_album_name(source.get(field))
            if field == "album"
            else " ".join(str(source.get(field) or "").strip().split())
        )
        for field in _FIELDS
    }
    artwork = str(source.get("album_art") or "").strip()
    if artwork:
        values["album_art"] = artwork
    return {key: value for key, value in values.items() if value}


def _local_conflict(local: Mapping[str, str], candidate: Mapping[str, str]) -> str:
    local_title = _title_key(local.get("title", ""))
    candidate_title = _title_key(candidate.get("title", ""))
    if local_title and candidate_title and local_title != candidate_title:
        return "title conflicts with the exact normalized local title"

    required_artists = _artists(local.get("artists", ""))
    candidate_artists = _artists(candidate.get("artists", ""))
    if required_artists and candidate_artists and not _artists_cover(
        required_artists, candidate_artists
    ):
        return "candidate does not contain the full known artist set"

    # Album Enricher is intentionally non-destructive for an album identity
    # that is already populated.  Storefront search APIs frequently return a
    # later playlist, compilation, or editorial collection containing the same
    # recording (for example, "Love on Repeat") rather than its soundtrack.
    # Such a collection is useful evidence that the recording exists, but it
    # is never authority to replace the user's existing album tag.
    local_album = _field_key("album", local.get("album", ""))
    candidate_album = _field_key("album", candidate.get("album", ""))
    if local_album and candidate_album and local_album != candidate_album:
        return "album conflicts with the protected existing album"
    return ""


def _compatible_groups(
    sources: Mapping[str, dict[str, str]],
) -> list[dict[str, dict[str, str]]]:
    """Group sources only when their populated identity fields do not disagree."""

    groups: list[dict[str, dict[str, str]]] = []
    for name in _SOURCE_NAMES:
        source = sources.get(name)
        if not source:
            continue
        matching = next(
            (group for group in groups if _source_fits_group(source, group)),
            None,
        )
        if matching is None:
            groups.append({name: source})
        else:
            matching[name] = source
    return groups


def _source_fits_group(
    source: Mapping[str, str], group: Mapping[str, Mapping[str, str]]
) -> bool:
    for other in group.values():
        for field in ("title", "album", "year", "language"):
            left = _field_key(field, source.get(field, ""))
            right = _field_key(field, other.get(field, ""))
            if left and right and left != right:
                return False
        left_artists = _artists(source.get("artists", ""))
        right_artists = _artists(other.get("artists", ""))
        if left_artists and right_artists and not (
            _artists_cover(left_artists, right_artists)
            or _artists_cover(right_artists, left_artists)
        ):
            return False
    return True


def _proposal_belongs_to_group(
    proposal: Mapping[str, str], group: Mapping[str, Mapping[str, str]]
) -> bool:
    if not proposal:
        return False
    for field, proposed in proposal.items():
        if field not in _FIELDS or not proposed:
            continue
        available = {
            _field_key(field, source.get(field, ""))
            for source in group.values()
            if source.get(field)
        }
        if not available or _field_key(field, proposed) not in available:
            return False
    # An apply decision needs an externally evidenced track identity, rather
    # than only empty release fields or a local fallback.
    return bool(proposal.get("title") and proposal.get("album"))


def _artwork_for_group(
    group: Mapping[str, Mapping[str, str]], selected_sources: tuple[str, ...]
) -> str:
    for name in (*selected_sources, *tuple(group)):
        source = group.get(name, {})
        artwork = str(source.get("album_art") or "").strip()
        if artwork:
            return artwork
    return ""


def _artists(value: object) -> set[str]:
    return {
        _key(part)
        for part in re.split(
            r"\s*(?:,|;|&|/|\band\b|\bfeat(?:uring)?\.?\b)\s*",
            str(value or ""),
            flags=re.I,
        )
        if _key(part)
    }


def _artists_cover(required: set[str], available: set[str]) -> bool:
    return all(
        any(name == candidate or name in candidate or candidate in name for candidate in available)
        for name in required
    )


def _field_key(field: str, value: object) -> str:
    if field == "album":
        return _key(normalize_album_name(value))
    if field == "title":
        return _title_key(value)
    return _key(value)


def _title_key(value: object) -> str:
    """Normalize narrow catalog aliases without collapsing real song versions."""

    text = re.sub(
        r"\s*[\[(]\s*original\s*[\])]\s*$", " ", str(value or ""), flags=re.I
    )
    tokens = _key(text).split()
    # Bengali/Indian catalogues use both "O My Love" and "Oh My Love" for the
    # same Amanush recording.  This vocalization alias is safe only as a whole
    # token; words such as "On" remain distinct.
    return " ".join("o" if token == "oh" else token for token in tokens)


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
