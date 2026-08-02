"""Natural-language song intent understanding and YouTube result discovery."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.request import Request, urlopen

from youtube_audio_video_downloader.services.ai_provider import chat_json
from youtube_audio_video_downloader.services.album_names import normalize_album_name


@dataclass(frozen=True, slots=True)
class SongSearchIntent:
    """Normalized meaning of a plain-language media search."""

    title: str = ""
    artists: str = ""
    album: str = ""
    movie: str = ""
    release_year: str = ""
    workflow: str = "audio"
    search_query: str = ""
    explanation: str = ""
    engine: str = "local"

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "artists": {"type": "string"},
        "album": {"type": "string"},
        "movie": {"type": "string"},
        "release_year": {"type": "string"},
        "workflow": {
            "type": "string",
            "enum": ["audio", "video", "album", "jukebox"],
        },
        "search_query": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": [
        "title", "artists", "album", "movie", "release_year",
        "workflow", "search_query", "explanation",
    ],
    "additionalProperties": False,
}


def understand_song_request(
    request_text: str,
    *,
    model: str = "qwen2.5:7b",
    use_ai: bool = True,
) -> SongSearchIntent:
    """Understand a request with NVIDIA/Ollama, otherwise use a local parser."""
    text = request_text.strip()
    if not text:
        raise ValueError("Enter a song, artist, album, movie, or jukebox to search for.")
    if not use_ai:
        fallback = _local_intent(text)
        return SongSearchIntent(
            **{
                **fallback.as_dict(),
                "explanation": "Parsed locally with AI disabled for this search.",
                "engine": "internet + local rules",
            }
        )
    selected_model = model.strip() or "qwen2.5:7b"
    try:
        payload = _call_ollama_intent(text, selected_model)
        provider = str(payload.pop("__provider__", "AI"))
        response_model = str(payload.pop("__model__", selected_model))
        try:
            review = chat_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "Independently review a proposed music-search intent against the "
                            "original request. Correct only errors supported by the request. Keep "
                            "title, artists, album, and movie disjoint; never invent metadata. "
                            "Choose jukebox only for a timestamped multi-song compilation, album "
                            "for one album, video for an explicit video request, otherwise audio."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"request": text, "proposed_intent": payload},
                            ensure_ascii=False,
                        ),
                    },
                ],
                _INTENT_SCHEMA,
                model=selected_model,
                timeout=90,
                temperature=0,
            )
            payload = review.data
            provider = f"{provider} + independent reviewer"
        except Exception as review_exc:
            print(f"[AI-REVIEW-FALLBACK] Search intent kept extractor result | {review_exc}")
        print(
            f"[AI-VERIFIED] Search intent extracted and reviewed | "
            f"{provider} | model={response_model}"
        )
        return _intent_from_payload(
            payload,
            engine=f"{provider} · {response_model}",
            request_text=text,
        )
    except Exception as exc:
        print(f"[AI-FALLBACK] Search intent used local parser | reason={exc}")
        fallback = _local_intent(text)
        return SongSearchIntent(
            **{
                **fallback.as_dict(),
                "explanation": f"{fallback.explanation} AI providers unavailable: {exc}",
                "engine": "local fallback",
            }
        )


def available_ollama_models(timeout: float = 2.0) -> list[str]:
    """List installed local Ollama completion models, excluding cloud and embeddings."""
    request = Request("http://127.0.0.1:11434/api/tags")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - local Ollama API
        payload = json.loads(response.read().decode("utf-8"))
    models: list[str] = []
    for item in payload.get("models", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        capabilities = item.get("capabilities", [])
        is_completion = not capabilities or "completion" in capabilities
        if name and is_completion and not name.endswith(":cloud") and not item.get("remote_host"):
            models.append(name)
    return models


def _call_ollama_intent(text: str, model: str) -> dict[str, Any]:
    messages = [
            {
                "role": "system",
                "content": (
                    "Extract music-search intent. Do not invent metadata the user did not imply. "
                    "Keep title, artists, album, and movie disjoint: text identified as an artist "
                    "must not also remain appended to the song title. For example, in a query "
                    "ending with a recognizable artist name, put that name only in artists. "
                    "Use audio for a song/audio request, video for a music-video request, album "
                    "for a full album that may need silence-based splitting, and jukebox for a "
                    "timestamped multi-song video. Make search_query concise and YouTube-friendly."
                ),
            },
            {"role": "user", "content": text},
        ]
    response = chat_json(
        messages,
        _INTENT_SCHEMA,
        model=model,
        timeout=90,
        temperature=0,
    )
    return {**response.data, "__provider__": response.provider, "__model__": response.model}


def _intent_from_payload(
    payload: dict[str, Any], *, engine: str, request_text: str = ""
) -> SongSearchIntent:
    workflow = str(payload.get("workflow") or "audio").lower()
    if workflow not in {"audio", "video", "album", "jukebox"}:
        workflow = "audio"
    values = {
        key: str(payload.get(key) or "").strip()
        for key in ("title", "artists", "album", "movie", "release_year", "search_query", "explanation")
    }
    values["title"] = _remove_artist_suffix(values["title"], values["artists"])
    values["album"] = normalize_album_name(values["album"])
    values["movie"] = normalize_album_name(values["movie"])
    request_lower = request_text.lower()
    explicit_video = bool(re.search(r"\b(?:music\s+video|video)\b", request_lower))
    if workflow == "video" and request_text and not explicit_video:
        workflow = "audio"
        values["search_query"] = re.sub(
            r"\b(?:music\s+video|video)\b", "official audio", values["search_query"], flags=re.I
        ).strip()
        values["explanation"] = (
            "Single-song audio request; no music video was explicitly requested."
        )
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", request_text)
    if year_match:
        values["release_year"] = year_match.group(1)
        for key in ("album", "movie"):
            values[key] = re.sub(
                rf"\s*[\[(]?\b{re.escape(year_match.group(1))}\b[\])]?(?:\s*)$",
                "",
                values[key],
            ).strip()
    if re.search(r"\b(?:movie|film)\b", request_lower):
        values["movie"] = values["movie"] or values["album"]
        values["album"] = ""
    elif re.search(r"\balbum\b", request_lower):
        values["album"] = values["album"] or values["movie"]
        values["movie"] = ""
    if not values["search_query"]:
        values["search_query"] = " ".join(
            part for part in (values["title"], values["artists"], values["album"], values["movie"]) if part
        )
    if workflow == "audio" and not re.search(
        r"\b(?:audio|lyrics?|official)\b", values["search_query"], re.I
    ):
        values["search_query"] += " official audio"
    return SongSearchIntent(**values, workflow=workflow, engine=engine)


def _remove_artist_suffix(title: str, artists: str) -> str:
    """Remove artist tokens that the intent model duplicated at the end of a title."""
    title_parts = title.split()
    first_artist = re.split(r"\s*(?:,|&|\band\b|\bfeat\.?\b)\s*", artists, maxsplit=1, flags=re.I)[0]
    artist_parts = first_artist.split()
    if not title_parts or not artist_parts:
        return title
    normalized_title = [re.sub(r"[^a-z0-9]+", "", part.lower()) for part in title_parts]
    normalized_artist = [re.sub(r"[^a-z0-9]+", "", part.lower()) for part in artist_parts]
    for length in range(min(len(title_parts), len(artist_parts)), 0, -1):
        if normalized_title[-length:] == normalized_artist[:length]:
            cleaned = " ".join(title_parts[:-length]).strip()
            return cleaned or title
    return title


def _local_intent(text: str) -> SongSearchIntent:
    """Conservative offline fallback; it never claims inferred metadata as fact."""
    lowered = text.lower()
    workflow = "audio"
    if "jukebox" in lowered or "all songs" in lowered:
        workflow = "jukebox"
    elif "full album" in lowered or re.search(r"\balbum\b", lowered):
        workflow = "album"
    elif "music video" in lowered or re.search(r"\bvideo\b", lowered):
        workflow = "video"
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    artist_match = re.search(r"\bby\s+(.+?)(?=\s+(?:from|in|album|movie|film)\b|$)", text, re.I)
    album_match = re.search(r"\b(?:from|in)\s+(?:the\s+)?(?:album|movie|film)\s+(.+)$", text, re.I)
    cleaned = re.sub(
        r"^(?:find|search(?:\s+for)?|download|play|show me)\s+", "", text, flags=re.I
    ).strip()
    title = re.split(r"\s+by\s+|\s+from\s+(?:the\s+)?(?:album|movie|film)\s+", cleaned, maxsplit=1, flags=re.I)[0]
    title = re.sub(r"\b(?:song|audio|music video|video)\b\s*$", "", title, flags=re.I).strip(" -,'\"")
    collection = normalize_album_name(
        album_match.group(1).strip(" -,'\"") if album_match else ""
    )
    if year_match and collection:
        collection = re.sub(rf"\s*\b{re.escape(year_match.group(1))}\b\s*$", "", collection).strip()
    is_movie = bool(re.search(r"\b(?:movie|film)\b", text, re.I))
    query = cleaned
    if workflow in {"album", "jukebox"} and "jukebox" not in lowered:
        query += " full album audio jukebox"
    elif workflow == "audio" and not re.search(r"\b(?:audio|lyrics?|official)\b", lowered):
        query += " official audio"
    return SongSearchIntent(
        title=title,
        artists=artist_match.group(1).strip(" -,'\"") if artist_match else "",
        album="" if is_movie else collection,
        movie=collection if is_movie else "",
        release_year=year_match.group(1) if year_match else "",
        workflow=workflow,
        search_query=query,
        explanation="Parsed locally because the Ollama model was unavailable.",
        engine="local",
    )


def search_youtube_for_intent(intent: SongSearchIntent, *, limit: int = 8) -> list[dict[str, Any]]:
    """Return ranked, preview-ready YouTube results for a normalized intent."""
    import yt_dlp

    limit = max(1, min(int(limit), 12))
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        response = downloader.extract_info(f"ytsearch{limit}:{intent.search_query}", download=False)
    entries = response.get("entries", []) if isinstance(response, dict) else []
    results: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        video_id = str(entry.get("id") or "").strip()
        url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")) and video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"
        if not url:
            continue
        duration = int(entry.get("duration") or 0)
        results.append({
            "id": video_id,
            "title": str(entry.get("title") or "Untitled").strip(),
            "url": url,
            "channel": str(entry.get("channel") or entry.get("uploader") or "").strip(),
            "duration": duration,
            "views": int(entry.get("view_count") or 0),
            "thumbnail": str(entry.get("thumbnail") or (f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else "")),
        })
    return results


def search_song(
    request_text: str,
    *,
    model: str = "qwen2.5:7b",
    limit: int = 8,
    use_ai: bool = True,
) -> dict[str, Any]:
    """High-level operation consumed by the desktop worker."""
    intent = understand_song_request(request_text, model=model, use_ai=use_ai)
    intent_data = intent.as_dict()
    collection = intent.album or intent.movie
    warnings: list[str] = []
    if collection:
        if not intent_data["release_year"]:
            try:
                from youtube_audio_video_downloader.services.release_year_finder import (
                    find_album_release_year,
                )

                intent_data["release_year"] = find_album_release_year(collection)["year"]
            except Exception as exc:  # Metadata enrichment is best-effort.
                warnings.append(f"release year: {exc}")
        try:
            from youtube_audio_video_downloader.services.album_art_finder import find_album_art

            intent_data["album_art"] = find_album_art(
                collection, release_year=intent_data["release_year"]
            )
        except Exception as exc:  # Metadata enrichment is best-effort.
            intent_data["album_art"] = ""
            warnings.append(f"album art: {exc}")
    else:
        intent_data["album_art"] = ""
    intent_data["metadata_warnings"] = "; ".join(warnings)
    return {
        "intent": intent_data,
        "results": search_youtube_for_intent(intent, limit=limit),
    }


def routed_result_title(
    selected_youtube_title: str, interpreted_title: str, workflow: str
) -> str:
    """Choose a route name without replacing a selected video with broad intent text."""

    selected = str(selected_youtube_title or "").strip()
    interpreted = str(interpreted_title or "").strip()
    if str(workflow or "").casefold() in {"video", "jukebox"}:
        return selected or interpreted or "Untitled"
    return interpreted or selected or "Untitled"
