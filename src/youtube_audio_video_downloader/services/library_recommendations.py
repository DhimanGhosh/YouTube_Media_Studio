"""Agentic recommendations grounded in the indexed local media library."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from youtube_audio_video_downloader.services.agno_provider import run_structured_agent
from youtube_audio_video_downloader.services.album_art_finder import find_catalog_song_metadata
from youtube_audio_video_downloader.services.media_library import LibraryItem, split_artists
from youtube_audio_video_downloader.services.music_web_evidence import (
    find_music_web_evidence,
)


MAX_LIBRARY_CANDIDATES = 750
MAX_SEMANTIC_CANDIDATES = 120
MAX_EVIDENCE_LOOKUPS = 20
MAX_RECOMMENDATIONS = 20
MAX_MATCHED_FILTERS = 12
MAX_TASTE_PLAYLISTS = 20
MAX_TASTE_TRACKS = 80
MAX_TASTE_TRACKS_PER_PLAYLIST = 12

_QUERY_SCAFFOLD_TOKENS = {
    "a", "all", "an", "and", "any", "by", "find", "for", "from", "get",
    "give", "in", "library", "matching", "me", "mood", "music", "my", "of",
    "or", "please", "play", "recommend", "recommendation", "recommendations",
    "result", "results", "return", "show", "song", "songs", "some", "suggestion",
    "suggestions", "the", "track", "tracks", "with",
}
_TIME_PREFERENCE_TOKENS = {
    "latest": {"latest", "newest", "recent"},
    "newer": {"new", "newer", "recent"},
    "older": {"old", "older"},
    "oldest": {"earliest", "oldest"},
}


@dataclass(frozen=True, slots=True)
class LibraryRecommendation:
    item: LibraryItem
    reason: str
    exists_locally: bool


@dataclass(frozen=True, slots=True)
class _QueryPlan:
    artists: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    semantic_filters: tuple[str, ...] = ()
    time_preference: str = "any"
    use_web_evidence: bool = False


class _PlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artists: list[str] = Field(max_length=8)
    languages: list[str] = Field(max_length=8)
    genres: list[str] = Field(max_length=8)
    moods: list[str] = Field(max_length=8)
    activities: list[str] = Field(max_length=8)
    energy_or_tempo: list[str] = Field(max_length=8)
    other_constraints: list[str] = Field(max_length=8)
    time_preference: Literal["any", "older", "oldest", "newer", "latest"]
    use_web_evidence: bool


class _SemanticMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    matches: bool
    confidence: float = Field(ge=0, le=1)
    matched_filters: list[str] = Field(max_length=MAX_MATCHED_FILTERS)
    evidence_support: list["_FilterEvidence"] = Field(
        default_factory=list, max_length=MAX_MATCHED_FILTERS
    )


class _FilterEvidence(BaseModel):
    """One exact evidence phrase supporting one requested semantic filter."""

    model_config = ConfigDict(extra="forbid")

    filter: str
    phrase: str


class _SemanticOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[_SemanticMatch] = Field(max_length=MAX_SEMANTIC_CANDIDATES)


class _EvidenceJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    supports: bool
    confidence: float = Field(ge=0, le=1)


class _EvidenceJudgmentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judgments: list[_EvidenceJudgment] = Field(max_length=MAX_SEMANTIC_CANDIDATES)


class _CuratorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[int] = Field(max_length=MAX_RECOMMENDATIONS)


def recommend_library_tracks(
    request_text: str,
    items: Iterable[LibraryItem],
    *,
    model: str,
    limit: int = 8,
    timeout: float = 90,
    language_continuation: bool = False,
    playlists: Mapping[str, Iterable[str]] | None = None,
) -> list[LibraryRecommendation]:
    """Plan, filter, verify, and rank a natural-language local-library request."""

    request_text = request_text.strip()
    if not request_text:
        raise ValueError("Describe an artist, genre, mood, or listening occasion.")
    selected_model = model.strip()
    if not selected_model:
        raise ValueError("Configure an agentic model in Global Settings first.")
    bounded_limit = max(1, min(int(limit), MAX_RECOMMENDATIONS))
    all_items = list(items)
    if not all_items:
        raise ValueError("The indexed library has no tracks to recommend.")
    taste_profile = _playlist_taste_profile(all_items, playlists or {})

    literal_artists = _requested_artists(request_text, all_items)
    plan = _plan_request(
        request_text, all_items, literal_artists=literal_artists,
        taste_profile=taste_profile, model=selected_model, timeout=timeout,
    )
    plan = _recover_omitted_constraints(request_text, plan)
    if language_continuation:
        plan = _QueryPlan(
            languages=plan.languages,
            use_web_evidence=bool(plan.languages),
        )
        if not plan.languages:
            return []
        request_text = "Songs in requested language(s): " + ", ".join(plan.languages)
    print(
        "[AI-AGENT] Library query planner | "
        f"artists={list(plan.artists)} languages={list(plan.languages)} "
        f"semantic={list(plan.semantic_filters)} time={plan.time_preference}"
    )

    artist_matches, unresolved_artist = _resolve_plan_artists(plan.artists, all_items)
    if unresolved_artist:
        print("[AI-AGENT] Local catalog filter | requested artist is not indexed")
        return []
    candidates = _bounded_candidates(
        all_items, request_text, artist_intent=artist_matches
    )
    candidates = _apply_time_preference(candidates, plan.time_preference)
    candidates = _rank_by_playlist_taste(candidates, taste_profile)
    if not candidates:
        return []
    print(f"[AI-AGENT] Local catalog filter | candidates={len(candidates)}")

    semantic_filters = (*plan.languages, *plan.semantic_filters)
    reasons: dict[int, tuple[str, ...]] = {}
    # Every constrained candidate must have the same opportunity for independent
    # evidence. Sending additional, unevidenced rows to the verifier made valid
    # results depend on which agent happened to approve which subset.
    semantic_candidates = candidates[:MAX_EVIDENCE_LOOKUPS]
    evidence = (
        _collect_catalog_evidence(semantic_candidates, semantic_filters)
        if plan.use_web_evidence or semantic_filters else {}
    )
    print(
        "[AI-AGENT] Evidence scout | "
        f"internet_matches={len(evidence)} requested={plan.use_web_evidence}"
    )
    plan = _promote_evidence_languages(plan, evidence)
    semantic_filters = (*plan.languages, *plan.semantic_filters)
    verified = _verify_semantics(
        request_text, semantic_candidates, semantic_filters, evidence,
        languages=plan.languages,
        semantic_filters=plan.semantic_filters,
        model=selected_model, timeout=timeout,
    )
    if verified is not None:
        candidates, reasons = verified
    elif semantic_filters:
        candidates, reasons = _filter_by_independent_evidence(
            candidates,
            evidence,
            languages=plan.languages,
            semantic_filters=plan.semantic_filters,
        )
    print(f"[AI-AGENT] Semantic verifier | matches={len(candidates)}")
    if not candidates:
        return []

    ranked = _curate_candidates(
        request_text, candidates, reasons, plan, taste_profile=taste_profile,
        model=selected_model,
        limit=bounded_limit, timeout=timeout,
    )
    print(f"[AI-AGENT] Smart Library Curator | recommendations={len(ranked)}")
    return [
        LibraryRecommendation(
            item,
            _recommendation_reason(
                item,
                reasons.get(id(item), ()),
                plan,
                artist_matches,
                playlist_affinity=_playlist_affinity(item, taste_profile) > 0,
            ),
            Path(item.path).is_file(),
        )
        for item in ranked
    ]


def _plan_request(
    request_text: str,
    items: list[LibraryItem],
    *,
    literal_artists: tuple[str, ...],
    taste_profile: list[dict[str, object]],
    model: str,
    timeout: float,
) -> _QueryPlan:
    artists = sorted(
        {artist for item in items for artist in split_artists(item.artists) if artist},
        key=str.casefold,
    )
    years = [item.year for item in items if item.year]
    context = {
        "request": request_text,
        "literal_local_artist_matches": list(literal_artists),
        "available_artists": artists[:500],
        "library_year_range": [min(years), max(years)] if years else [],
        "playlist_taste_profile": taste_profile,
    }
    try:
        payload = run_structured_agent(
            name="Library query planner",
            role="Turn a listening request into independent local-library filters.",
            instructions=(
                "Convert the request into independent filters without choosing songs. Copy "
                "artist names from available_artists when they match. Put requested spoken "
                "languages in languages, preserving the user's language name rather than a "
                "code. Classify every explicitly requested musical descriptor into genres, "
                "moods, activities, energy_or_tempo, or other_constraints; never infer traits "
                "the user did not request. Map "
                "relative release-age intent to "
                "time_preference; never invent numeric year cutoffs. Set use_web_evidence when "
                "language or musical traits cannot be proven from title/artist/album/year. "
                "The five descriptor arrays together MUST retain every explicit non-artist, "
                "non-language, non-time constraint: how the music should sound or feel, its "
                "genre, tempo, energy, mood, occasion, or an activity it should suit. Do not "
                "silently drop such a descriptor. Empty arrays mean that dimension was not "
                "requested. Playlist names and their tracks are preference hints for vague "
                "requests, not hard filters and not proof of a track's language or traits. Never "
                "turn descriptive words into an artist merely because the request ends in "
                "'songs'."
            ),
            input_data=context,
            output_schema=_PlanOutput,
            requested_model=model,
            timeout=timeout,
            temperature=0,
            max_tokens=700,
        )
    except Exception as exc:
        print(f"[AI-STATIC-FALLBACK] Library query planner | {exc}")
        return _QueryPlan(artists=literal_artists)
    return _QueryPlan(
        artists=_clean_strings(payload.artists) or literal_artists,
        languages=_clean_strings(payload.languages),
        semantic_filters=_clean_strings(
            [
                *payload.genres,
                *payload.moods,
                *payload.activities,
                *payload.energy_or_tempo,
                *payload.other_constraints,
            ]
        ),
        time_preference=payload.time_preference,
        use_web_evidence=payload.use_web_evidence,
    )


def _recover_omitted_constraints(request_text: str, plan: _QueryPlan) -> _QueryPlan:
    """Retain meaningful request terms even when the planning model drops them."""

    represented = {
        token
        for value in (*plan.artists, *plan.languages, *plan.semantic_filters)
        for token in _text_key(value).split()
    }
    represented.update(_TIME_PREFERENCE_TOKENS.get(plan.time_preference, ()))
    recovery_budget = max(
        0,
        MAX_MATCHED_FILTERS - len(plan.languages) - len(plan.semantic_filters),
    )
    recovered = tuple(
        dict.fromkeys(
            token
            for token in _text_key(request_text).split()
            if len(token) > 2
            and not token.isdigit()
            and token not in represented
            and token not in _QUERY_SCAFFOLD_TOKENS
        )
    )[:recovery_budget]
    if not recovered:
        return plan
    return _QueryPlan(
        artists=plan.artists,
        languages=plan.languages,
        semantic_filters=(*plan.semantic_filters, *recovered),
        time_preference=plan.time_preference,
        use_web_evidence=True,
    )


def _promote_evidence_languages(
    plan: _QueryPlan, evidence: dict[int, dict[str, str]]
) -> _QueryPlan:
    """Move recovered filters into the strict language lane when catalogs identify them."""

    catalog_languages = tuple(
        language
        for facts in evidence.values()
        if (language := _text_key(facts.get("language", "")))
    )
    if not catalog_languages:
        return plan
    promoted: list[str] = []
    remaining: list[str] = []
    for value in plan.semantic_filters:
        key = _text_key(value)
        target = promoted if any(
            _values_overlap(key, language) for language in catalog_languages
        ) else remaining
        target.append(value)
    if not promoted:
        return plan
    return _QueryPlan(
        artists=plan.artists,
        languages=tuple(dict.fromkeys((*plan.languages, *promoted))),
        semantic_filters=tuple(remaining),
        time_preference=plan.time_preference,
        use_web_evidence=plan.use_web_evidence,
    )


def _verify_semantics(
    request_text: str,
    candidates: list[LibraryItem],
    filters: tuple[str, ...],
    evidence: dict[int, dict[str, str]],
    *,
    languages: tuple[str, ...],
    semantic_filters: tuple[str, ...],
    model: str,
    timeout: float,
) -> tuple[list[LibraryItem], dict[int, tuple[str, ...]]] | None:
    catalog = [
        _catalog_row(index, item, evidence.get(index))
        for index, item in enumerate(candidates)
    ]
    try:
        payload = run_structured_agent(
            name="Library semantic verifier",
            role="Verify language, style, mood, activity, tempo, and energy constraints.",
            instructions=(
                "Treat the original request as authoritative and independently identify every "
                "explicit musical constraint in it. Evaluate every catalog item against all of "
                "those constraints, including any descriptor the planning agent omitted from "
                "filters. Use indexed metadata and supplied internet catalog evidence. Do not "
                "substitute unsupported general musical knowledge. matches may be true only when the item satisfies the "
                "whole original request. Be conservative when identity is ambiguous; never "
                "invent IDs. Language claims must be supported by the supplied catalog or web "
                "evidence; never infer language from a singer's nationality, other songs, or "
                "general popularity. Judge activity, energy, and tempo from the music itself—not video "
                "choreography, actors dancing, search-result SEO, or the presence of a word in "
                "a title. matched_filters must list every provided filter plus every explicit "
                "request descriptor that the item meets. For every non-language filter, add an "
                "evidence_support entry containing the filter and the shortest exact phrase "
                "copied from that item's supplied internet evidence which supports it. A close "
                "musical synonym such as 'melancholic' may support 'sad', but the phrase must "
                "appear verbatim in the supplied evidence. Do not cite titles, artist names, "
                "album names, or unsupported knowledge as semantic evidence. Return rows only "
                "for items where matches is true; omit rejected items so the bounded structured "
                "response remains concise."
            ),
            input_data={"request": request_text, "filters": filters, "catalog": catalog},
            output_schema=_SemanticOutput,
            requested_model=model,
            timeout=timeout,
            temperature=0,
            max_tokens=4000,
        )
    except Exception as exc:
        print(f"[AI-STATIC-FALLBACK] Library semantic verifier | {exc}")
        return None

    approved_synonyms = _adjudicate_semantic_evidence(
        payload.matches,
        evidence,
        semantic_filters,
        model=model,
        timeout=timeout,
    )

    selected: list[LibraryItem] = []
    reasons: dict[int, tuple[str, ...]] = {}
    seen: set[int] = set()
    for row in payload.matches:
        candidate_id = row.id
        confidence = row.confidence
        if (
            candidate_id in seen
            or not 0 <= candidate_id < len(candidates)
            or not row.matches
            or confidence < 0.45
        ):
            continue
        matched = _clean_strings(row.matched_filters)
        if not _covers_filters(matched, filters):
            continue
        if languages and not _evidence_supports_languages(
            evidence.get(candidate_id, {}), languages
        ):
            continue
        if semantic_filters and not _agent_evidence_supports_semantic_filters(
            evidence.get(candidate_id, {}), semantic_filters,
            approved_synonyms.get(candidate_id, set()),
        ):
            continue
        seen.add(candidate_id)
        item = candidates[candidate_id]
        selected.append(item)
        reasons[id(item)] = filters
    return selected, reasons


def _filter_by_independent_evidence(
    candidates: list[LibraryItem],
    evidence: dict[int, dict[str, str]],
    *,
    languages: tuple[str, ...],
    semantic_filters: tuple[str, ...],
) -> tuple[list[LibraryItem], dict[int, tuple[str, ...]]]:
    """Fail closed on constrained requests when the semantic agent is unavailable."""

    requested = (*languages, *semantic_filters)
    selected: list[LibraryItem] = []
    reasons: dict[int, tuple[str, ...]] = {}
    for candidate_id, item in enumerate(candidates):
        facts = evidence.get(candidate_id, {})
        if not _evidence_supports_languages(facts, languages):
            continue
        if not _evidence_supports_semantic_filters(facts, semantic_filters):
            continue
        selected.append(item)
        reasons[id(item)] = requested
    return selected, reasons


def _curate_candidates(
    request_text: str,
    candidates: list[LibraryItem],
    reasons: dict[int, tuple[str, ...]],
    plan: _QueryPlan,
    *,
    taste_profile: list[dict[str, object]],
    model: str,
    limit: int,
    timeout: float,
) -> list[LibraryItem]:
    catalog = [
        {**_catalog_row(index, item), "verified_filters": list(reasons.get(id(item), ()))}
        for index, item in enumerate(candidates)
    ]
    try:
        payload = run_structured_agent(
            name="Smart Library Curator",
            role="Produce the final diverse, ranked local-library selection.",
            instructions=(
                "Rank only supplied IDs. Preserve all verified filters, follow the requested "
                "release-time preference, and treat playlist names plus their member tracks "
                "as the user's positive taste examples. Prefer candidates related by artist, "
                "album, era, or the playlist's stated theme while still keeping the result "
                "diverse. A playlist is a preference hint, never independent factual proof. "
                "Prefer strong metadata identity and provide a "
                f"diverse useful set of at most {limit} IDs. Never invent IDs."
            ),
            input_data={
                "request": request_text,
                "time_preference": plan.time_preference,
                "playlist_taste_profile": taste_profile,
                "catalog": catalog,
            },
            output_schema=_CuratorOutput,
            requested_model=model,
            timeout=timeout,
            temperature=0.15,
            max_tokens=500,
        )
    except Exception as exc:
        print(f"[AI-STATIC-FALLBACK] Smart Library Curator | {exc}")
        return candidates[:limit]
    selected: list[LibraryItem] = []
    seen: set[int] = set()
    for candidate_id in payload.ids:
        if (
            isinstance(candidate_id, bool) or not isinstance(candidate_id, int)
            or candidate_id in seen or not 0 <= candidate_id < len(candidates)
        ):
            continue
        seen.add(candidate_id)
        selected.append(candidates[candidate_id])
        if len(selected) >= limit:
            break
    # Ranking may reorder or diversify a verified set, but it must never erase it.
    # Some local models return an empty structured ID list even after the preceding
    # evidence gates have produced valid candidates.
    return selected or candidates[:limit]


def _playlist_taste_profile(
    items: Iterable[LibraryItem],
    playlists: Mapping[str, Iterable[str]],
) -> list[dict[str, object]]:
    """Build bounded, path-free positive taste examples from saved playlists."""

    by_path = {item.path.casefold(): item for item in items}
    profile: list[dict[str, object]] = []
    remaining_tracks = MAX_TASTE_TRACKS
    for raw_name, paths in sorted(
        playlists.items(), key=lambda pair: str(pair[0]).casefold()
    )[:MAX_TASTE_PLAYLISTS]:
        name = str(raw_name).strip()
        if not name or remaining_tracks <= 0:
            continue
        tracks: list[dict[str, object]] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for raw_path in paths:
            item = by_path.get(str(raw_path).casefold())
            if item is None:
                continue
            identity = _song_identity(item)
            if identity in seen:
                continue
            seen.add(identity)
            tracks.append(
                {
                    "title": item.title,
                    "artists": item.artists,
                    "album": item.album,
                    "year": item.year,
                }
            )
            remaining_tracks -= 1
            if (
                remaining_tracks <= 0
                or len(tracks) >= MAX_TASTE_TRACKS_PER_PLAYLIST
            ):
                break
        if tracks:
            profile.append({"name": name, "tracks": tracks})
    return profile


def _playlist_affinity(
    item: LibraryItem, taste_profile: list[dict[str, object]]
) -> int:
    """Score metadata similarity to positive playlist examples without guessing traits."""

    item_artists = {_text_key(value) for value in split_artists(item.artists)}
    item_album = _text_key(item.album)
    item_identity = _song_identity(item)
    score = 0
    for playlist in taste_profile:
        name_tokens = {
            token for token in _text_key(str(playlist.get("name", ""))).split()
            if len(token) > 2 and token not in _QUERY_SCAFFOLD_TOKENS
        }
        metadata_tokens = set(
            _text_key(f"{item.title} {item.artists} {item.album}").split()
        )
        score += len(name_tokens & metadata_tokens)
        for raw_track in playlist.get("tracks", []):
            if not isinstance(raw_track, dict):
                continue
            taste_artists = {
                _text_key(value)
                for value in split_artists(str(raw_track.get("artists", "")))
            }
            if item_artists & taste_artists:
                score += 5
            taste_album = _text_key(str(raw_track.get("album", "")))
            if item_album and taste_album and item_album == taste_album:
                score += 3
            taste_identity = (
                _text_key(str(raw_track.get("title", ""))),
                tuple(sorted(taste_artists)),
            )
            if item_identity == taste_identity:
                # Playlist members are examples; slightly prefer a related discovery.
                score -= 1
    return score


def _rank_by_playlist_taste(
    candidates: list[LibraryItem], taste_profile: list[dict[str, object]]
) -> list[LibraryItem]:
    if not taste_profile:
        return candidates
    original_order = {id(item): index for index, item in enumerate(candidates)}
    return sorted(
        candidates,
        key=lambda item: (-_playlist_affinity(item, taste_profile), original_order[id(item)]),
    )


def playlist_taste_search_query(
    request_text: str,
    items: Iterable[LibraryItem],
    playlists: Mapping[str, Iterable[str]],
) -> str:
    """Add a concise playlist-derived taste profile to external discovery requests."""

    profile = _playlist_taste_profile(items, playlists)
    examples: list[str] = []
    for playlist in profile[:4]:
        tracks = playlist.get("tracks", [])
        labels = [
            f"{track.get('title', '')} by {track.get('artists', '')}"
            for track in tracks[:3]
            if isinstance(track, dict)
        ]
        if labels:
            examples.append(f"{playlist['name']}: " + "; ".join(labels))
    request = request_text.strip()
    if not examples:
        return request
    return (
        f"{request}. Suggest similar songs based on my saved playlists: "
        + " | ".join(examples)
    )


def _collect_catalog_evidence(
    candidates: list[LibraryItem], filters: tuple[str, ...]
) -> dict[int, dict[str, str]]:
    """Fetch bounded public catalog facts in parallel for semantic verification."""

    selected = list(enumerate(candidates[:MAX_EVIDENCE_LOOKUPS]))

    def lookup(row: tuple[int, LibraryItem]) -> tuple[int, dict[str, str]]:
        index, item = row
        try:
            result = find_catalog_song_metadata(item.title, item.artists, timeout=6)
        except Exception:
            result = {}
        useful = {
            key: str(result.get(key) or "").strip()
            for key in ("title", "artists", "album", "year", "language", "genre")
            if str(result.get(key) or "").strip()
        }
        web_evidence = find_music_web_evidence(
            item.title,
            item.artists,
            filters,
            timeout=6,
        )
        if web_evidence:
            useful["web_search_excerpts"] = web_evidence
        return index, useful

    if not selected or not filters:
        return {}
    with ThreadPoolExecutor(max_workers=min(4, len(selected))) as pool:
        return {index: result for index, result in pool.map(lookup, selected) if result}


def _catalog_row(
    index: int, item: LibraryItem, evidence: dict[str, str] | None = None
) -> dict[str, object]:
    row: dict[str, object] = {
        "id": index, "title": item.title, "artists": item.artists,
        "album": item.album, "year": item.year, "type": item.media_type,
    }
    if evidence:
        row["internet_catalog_evidence"] = evidence
    return row


def _bounded_candidates(
    items: Iterable[LibraryItem],
    request_text: str,
    *,
    artist_intent: tuple[str, ...] = (),
) -> list[LibraryItem]:
    """Keep requests bounded while favoring literal artist/title/album matches."""

    unique: dict[tuple[str, tuple[str, ...]], LibraryItem] = {}
    for item in items:
        unique.setdefault(_song_identity(item), item)
    if artist_intent:
        unique = {
            identity: item for identity, item in unique.items()
            if _item_matches_artists(item, artist_intent)
        }
    tokens = {token for token in _text_key(request_text).split() if len(token) > 2}

    def rank(item: LibraryItem) -> tuple[int, str, str]:
        metadata = _text_key(f"{item.title} {item.artists} {item.album}")
        matches = sum(_phrase_in(token, metadata) for token in tokens)
        return (-matches, item.artists.casefold(), item.title.casefold())

    return sorted(unique.values(), key=rank)[:MAX_LIBRARY_CANDIDATES]


def _song_identity(item: LibraryItem) -> tuple[str, tuple[str, ...]]:
    """Identify the same recording across duplicate indexed file paths."""

    artists = tuple(sorted(
        {_text_key(artist) for artist in split_artists(item.artists) if _text_key(artist)}
    ))
    return _text_key(item.title), artists


def _apply_time_preference(items: list[LibraryItem], preference: str) -> list[LibraryItem]:
    """Prioritize relative age without discarding semantic matches prematurely."""

    if preference == "any":
        return items
    if preference in {"oldest", "older"}:
        return sorted(
            items,
            key=lambda item: (
                item.year is None,
                item.year if item.year is not None else 9999,
                item.title.casefold(),
            ),
        )
    return sorted(
        items,
        key=lambda item: (
            item.year is None,
            -(item.year or 0),
            item.title.casefold(),
        ),
    )


def _requested_artists(request_text: str, items: Iterable[LibraryItem]) -> tuple[str, ...]:
    """Resolve literal request phrases against indexed artists without guessing."""

    request_key = _text_key(request_text)
    request_tokens = set(request_key.split())
    artists = sorted(
        {artist.strip() for item in items for artist in split_artists(item.artists)
         if artist.strip()},
        key=str.casefold,
    )
    exact = [artist for artist in artists if _phrase_in(_text_key(artist), request_key)]
    if exact:
        return tuple(exact)
    token_owners: dict[str, set[str]] = {}
    for artist in artists:
        for token in _text_key(artist).split():
            if len(token) >= 3:
                token_owners.setdefault(token, set()).add(artist)
    resolved = {
        next(iter(token_owners[token])) for token in request_tokens
        if token in token_owners and len(token_owners[token]) == 1
    }
    return tuple(sorted(resolved, key=str.casefold))


def _resolve_plan_artists(
    requested: tuple[str, ...], items: Iterable[LibraryItem]
) -> tuple[tuple[str, ...], bool]:
    if not requested:
        return (), False
    available = sorted(
        {artist for item in items for artist in split_artists(item.artists) if artist},
        key=str.casefold,
    )
    resolved: set[str] = set()
    for query in requested:
        key = _text_key(query)
        matches = [
            artist for artist in available
            if _phrase_in(key, _text_key(artist)) or _phrase_in(_text_key(artist), key)
        ]
        if not matches:
            return (), True
        resolved.update(matches)
    return tuple(sorted(resolved, key=str.casefold)), False


def _item_matches_artists(item: LibraryItem, requested: tuple[str, ...]) -> bool:
    item_artists = {_text_key(value) for value in split_artists(item.artists)}
    return any(_text_key(artist) in item_artists for artist in requested)


def _recommendation_reason(
    item: LibraryItem,
    matched_filters: tuple[str, ...],
    plan: _QueryPlan,
    artists: tuple[str, ...],
    *,
    playlist_affinity: bool = False,
) -> str:
    parts: list[str] = []
    if artists:
        parts.append("artist")
    parts.extend(matched_filters)
    if playlist_affinity:
        parts.append("saved playlist taste")
    if plan.time_preference != "any" and item.year:
        parts.append(f"{plan.time_preference} release ({item.year})")
    if not parts:
        return "Selected from indexed library metadata"
    return "Matches " + ", ".join(dict.fromkeys(parts))


def _covers_filters(matched: tuple[str, ...], requested: tuple[str, ...]) -> bool:
    matched_keys = {_text_key(value) for value in matched}
    return all(
        any(key == candidate or key in candidate or candidate in key for candidate in matched_keys)
        for key in (_text_key(value) for value in requested) if key
    )


def _evidence_supports_languages(
    evidence: dict[str, str], requested: tuple[str, ...]
) -> bool:
    """Require independent evidence for every requested language."""

    requested_keys = tuple(key for value in requested if (key := _text_key(value)))
    if not requested_keys:
        return True
    explicit_language = _text_key(evidence.get("language", ""))
    if explicit_language:
        return all(_values_overlap(value, explicit_language) for value in requested_keys)
    corroboration = _evidence_corroboration_text(evidence)
    return bool(corroboration) and all(
        value in corroboration for value in requested_keys
    )


def _evidence_supports_semantic_filters(
    evidence: dict[str, str], requested: tuple[str, ...]
) -> bool:
    """Require supplied public evidence for requested mood, style, or tempo traits."""

    corroboration = _evidence_corroboration_text(evidence)
    evidence_words = set(corroboration.split())
    return bool(evidence_words) and all(
        set(_text_key(value).split()).issubset(evidence_words)
        for value in requested
        if _text_key(value)
    )


def _agent_evidence_supports_semantic_filters(
    evidence: dict[str, str],
    requested: tuple[str, ...],
    approved_synonyms: set[str],
) -> bool:
    """Accept literal traits or agent-mapped synonyms quoted from exact-song evidence."""

    return all(
        _evidence_supports_semantic_filter(evidence, value)
        or key in approved_synonyms
        for value in requested
        if (key := _text_key(value))
    )


def _evidence_supports_semantic_filter(
    evidence: dict[str, str], requested: str
) -> bool:
    corroboration = _evidence_corroboration_text(evidence)
    words = set(corroboration.split())
    key = _text_key(requested)
    return bool(words and key and set(key.split()).issubset(words))


def _adjudicate_semantic_evidence(
    matches: list[_SemanticMatch],
    evidence: dict[int, dict[str, str]],
    requested: tuple[str, ...],
    *,
    model: str,
    timeout: float,
) -> dict[int, set[str]]:
    """Independently judge whether cited phrases actually entail requested traits."""

    claims, claim_targets = _extract_semantic_claims(matches, evidence, requested)
    if not claims:
        return {}
    try:
        payload = run_structured_agent(
            name="Library evidence entailment verifier",
            role="Judge whether short quoted music-evidence phrases entail requested traits.",
            instructions=(
                "Judge each phrase by its literal ordinary musical meaning only. supports may "
                "be true only when the phrase itself clearly describes or entails the requested "
                "mood, style, activity, energy, or tempo. Reject generic identity phrases such "
                "as language, artist, song, soundtrack, release, or album; reject titles and "
                "ambiguous promotional wording. Do not use outside knowledge. Return every ID."
            ),
            input_data={"claims": claims},
            output_schema=_EvidenceJudgmentOutput,
            requested_model=model,
            timeout=timeout,
            temperature=0,
            max_tokens=1000,
        )
    except Exception as exc:
        print(f"[AI-STATIC-FALLBACK] Library evidence entailment verifier | {exc}")
        return {}
    approved: dict[int, set[str]] = {}
    for judgment in payload.judgments:
        target = claim_targets.get(judgment.id)
        if target is None or not judgment.supports or judgment.confidence < 0.7:
            continue
        candidate_id, key = target
        approved.setdefault(candidate_id, set()).add(key)
    return approved


def _extract_semantic_claims(
    matches: list[_SemanticMatch],
    evidence: dict[int, dict[str, str]],
    requested: tuple[str, ...],
) -> tuple[list[dict[str, object]], dict[int, tuple[int, str]]]:
    """Build compact cited-phrase claims for independent entailment review."""

    claims: list[dict[str, object]] = []
    claim_targets: dict[int, tuple[int, str]] = {}
    for row in matches:
        candidate_id = getattr(row, "id", -1)
        if not isinstance(candidate_id, int) or candidate_id not in evidence:
            continue
        facts = evidence[candidate_id]
        corroboration = _evidence_corroboration_text(facts)
        support = getattr(row, "evidence_support", ())
        if not isinstance(support, (list, tuple)):
            continue
        for value in requested:
            key = _text_key(value)
            if not key or _evidence_supports_semantic_filter(facts, value):
                continue
            for cited in support:
                cited_filter = _text_key(getattr(cited, "filter", ""))
                phrase = _text_key(getattr(cited, "phrase", ""))
                if (
                    not _values_overlap(key, cited_filter)
                    or not _phrase_in(phrase, corroboration)
                ):
                    continue
                claim_id = len(claims)
                claims.append({"id": claim_id, "filter": value, "phrase": phrase})
                claim_targets[claim_id] = (candidate_id, key)
                break
    return claims, claim_targets


def _evidence_corroboration_text(evidence: dict[str, str]) -> str:
    return _text_key(
        " ".join(
            str(evidence.get(key, ""))
            for key in ("language", "genre", "web_search_excerpts")
        )
    )


def _values_overlap(first: str, second: str) -> bool:
    return first == second or first in second or second in first


def _clean_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(
        text for item in value if (text := str(item or "").strip())
    ))


def _text_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _phrase_in(phrase: str, text: str) -> bool:
    return bool(phrase and re.search(rf"(?:^| ){re.escape(phrase)}(?: |$)", text))
