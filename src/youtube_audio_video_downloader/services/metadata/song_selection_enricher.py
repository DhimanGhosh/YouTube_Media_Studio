"""Enrich a selected YouTube song before routing it to the audio downloader."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from youtube_audio_video_downloader.services.ai.ai_provider import chat_json
from youtube_audio_video_downloader.services.albums.album_art_finder import (
    find_catalog_song_metadata,
    find_song_art,
)
from youtube_audio_video_downloader.services.albums.album_names import normalize_album_name
from youtube_audio_video_downloader.services.metadata.metadata_verifier import (
    verify_metadata_evidence,
)
from youtube_audio_video_downloader.services.albums.wikipedia_tracks import (
    find_wikipedia_song_metadata,
)
from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names


def title_case_text(value: object, fallback: str = "") -> str:
    """Return trimmed title-case display text."""
    text = " ".join(str(value or "").strip().split())
    return text.title() if text else fallback


def enrich_selected_song(
    url: str,
    *,
    title: str,
    album: str,
    artists: str,
    thumbnail: str = "",
    model: str = "qwen2.5:7b",
    request_text: str = "",
    use_ai: bool = True,
) -> dict[str, str]:
    """Fetch upload metadata and resolve cover art for one selected result."""
    selected_url = str(url or "").strip()
    if not selected_url.lower().startswith(("http://", "https://")):
        raise ValueError("The selected YouTube result has no valid URL.")

    import yt_dlp

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        extracted = downloader.extract_info(selected_url, download=False)
    info = extracted if isinstance(extracted, dict) else {}

    resolved = _resolve_music_metadata(
        info,
        requested_title=title,
        requested_album=album,
        requested_artists=artists,
        model=model,
        request_text=request_text,
        use_ai=use_ai,
    )
    normalized_title = _lookup_song_title(resolved["title"])
    normalized_album = normalize_album_name(resolved["album"])
    normalized_artists = resolved["artists"]
    upload_year = _youtube_upload_year(info)
    youtube_thumbnail = _best_thumbnail(info) or str(thumbnail or "").strip()

    evidence = _find_external_metadata(
        raw_context=" ".join(
            part for part in (
                str(info.get("title") or ""), request_text, normalized_title,
                normalized_album, normalized_artists,
            ) if part
        ),
        title=normalized_title,
        album=normalized_album,
        artists=normalized_artists,
        model=model if use_ai else "",
    )
    if evidence:
        normalized_title = _lookup_song_title(evidence.get("title") or normalized_title)
        normalized_album = normalize_album_name(evidence.get("album") or normalized_album)

    album_art = str(evidence.get("album_art") or "").strip()
    if not album_art:
        try:
            album_art = find_song_art(normalized_title, normalized_artists)
        except Exception:  # Cover lookup is best-effort; YouTube remains the fallback.
            album_art = youtube_thumbnail

    return {
        "url": selected_url,
        "title": normalized_title,
        "album": normalized_album,
        "artists": normalized_artists,
        "release_year": str(evidence.get("year") or upload_year),
        "album_art": album_art,
    }


def _find_external_metadata(
    *, raw_context: str, title: str, artists: str, album: str = "", model: str = ""
) -> dict[str, str]:
    """Return one agent-verified external identity without cross-source mixing."""

    try:
        wiki = find_wikipedia_song_metadata(raw_context, title, artists)
    except (LookupError, OSError, TimeoutError, ValueError):
        wiki = {}
    catalog_title = str(wiki.get("title") or title).strip()
    try:
        catalog = find_catalog_song_metadata(catalog_title, artists)
    except (LookupError, OSError, TimeoutError, ValueError):
        catalog = {}
    decision = verify_metadata_evidence(
        {"title": title, "album": album, "artists": artists},
        wiki,
        catalog,
        model=model,
    )
    if decision.action != "apply":
        return {}
    evidence = dict(decision.metadata)
    if decision.album_art:
        evidence["album_art"] = decision.album_art
    return {key: str(value or "").strip() for key, value in evidence.items()}


def _lookup_song_title(value: object) -> str:
    text = " ".join(str(value or "").strip().split())
    text = re.sub(r"\s*[\[(]\s*original\s*[\])]\s*$", "", text, flags=re.I)
    text = re.sub(
        r"\s+-\s+(?:lo-?fi|slowed(?:\s*&\s*reverb)?|remix|reprise|acoustic)\s*$",
        "", text, flags=re.I,
    )
    return text.strip()


def _resolve_music_metadata(
    info: dict[str, Any],
    *,
    requested_title: str,
    requested_album: str,
    requested_artists: str,
    model: str,
    request_text: str,
    use_ai: bool = True,
) -> dict[str, str]:
    """Resolve clean music fields from structured data or a promotional title."""
    structured_track = str(info.get("track") or info.get("alt_title") or "").strip()
    if structured_track:
        title = title_case_text(structured_track, "Song")
        album = title_case_text(info.get("album") or requested_album or title, title)
        return {
            "title": title,
            "album": album,
            "artists": _resolve_artists(info, requested_artists),
        }

    raw_title = str(info.get("title") or requested_title or "").strip()
    fallback_title = title_case_text(_clean_video_title(raw_title), "Song")
    fallback_artists = _resolve_artists(info, requested_artists)
    raw_album = str(info.get("album") or requested_album or "").strip()
    fallback_album = (
        fallback_title
        if not raw_album or raw_album.lower() == "unknown"
        else title_case_text(raw_album, fallback_title)
    )
    if use_ai:
        try:
            llm = _resolve_with_ollama(
                model=model,
                request_text=request_text,
                raw_title=raw_title,
                description=str(info.get("description") or "")[:5000],
                channel=str(info.get("channel") or info.get("uploader") or ""),
                requested_title=requested_title,
                requested_album=requested_album,
                requested_artists=requested_artists,
            )
        except Exception:
            llm = {}
    else:
        llm = {}

    llm_title = _clean_video_title(str(llm.get("title") or ""))
    title = title_case_text(llm_title, fallback_title)
    album_text = str(llm.get("album") or "").strip()
    album = title_case_text(album_text, fallback_album)
    if album.lower() == "unknown":
        album = fallback_album
    llm_artists = format_artist_names(str(llm.get("artists") or ""))
    # The model may mistake actors named in promotional titles for singers.
    # Prefer user intent and explicit YouTube/description artist evidence.
    artists = fallback_artists if fallback_artists != "Unknown" else (llm_artists or "Unknown")
    return {"title": title, "album": album, "artists": artists}


def _resolve_with_ollama(**evidence: str) -> dict[str, str]:
    """Ask the NVIDIA/Ollama provider chain to separate music metadata fields."""
    model = str(evidence.pop("model", "") or "qwen2.5:7b")
    messages = [
            {
                "role": "system",
                "content": (
                    "Resolve metadata for one selected song video. Return title, album, and "
                    "artists only. Remove promotional phrases such as full audio song, official "
                    "video, lyrics, HD, and label/channel names from title. Artists must contain "
                    "performing singers only, comma separated; exclude actors, dancers, movie "
                    "stars, music labels, composers, and lyricists unless they perform the song. "
                    "Use an explicitly stated album/movie when supported. If this is a standalone "
                    "single with no album evidence, use the clean song title as album. Do not copy "
                    "the raw YouTube title into a field without separating it."
                ),
            },
            {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)},
        ]
    schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "album": {"type": "string"},
                "artists": {"type": "string"},
            },
            "required": ["title", "album", "artists"],
            "additionalProperties": False,
    }
    parsed = chat_json(
        messages,
        schema,
        model=model,
        timeout=90,
        temperature=0,
    ).data
    return {
        str(key): str(value)
        for key, value in parsed.items()
        if key in {"title", "album", "artists"}
    }


def _clean_video_title(raw_title: str) -> str:
    """Remove common YouTube marketing text from a song title fallback."""
    first_segment = re.split(r"\s*[|｜]\s*", str(raw_title or ""), maxsplit=1)[0]
    cleaned = re.sub(
        r"\b(?:full\s+)?(?:official\s+)?(?:audio|video|lyric(?:s|al)?)(?:\s+song)?\b",
        "",
        first_segment,
        flags=re.I,
    )
    cleaned = re.sub(r"\b(?:hd|4k|hq)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—|'\"[]()")
    return cleaned


def _youtube_upload_year(info: dict[str, Any]) -> str:
    """Return the year in which YouTube says the selected video was posted."""
    upload_date = str(info.get("upload_date") or "").strip()
    if len(upload_date) >= 4 and upload_date[:4].isdigit():
        return upload_date[:4]
    timestamp = info.get("timestamp")
    try:
        return str(datetime.fromtimestamp(float(timestamp), tz=timezone.utc).year)
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _resolve_artists(info: dict[str, Any], requested_artists: str) -> str:
    """Use query artists only when the selected video metadata supports them."""
    requested = format_artist_names(requested_artists)
    if requested.lower() == "unknown":
        requested = ""
    raw_media_artists = info.get("artists")
    if isinstance(raw_media_artists, list):
        media_artists = format_artist_names(", ".join(map(str, raw_media_artists)))
    else:
        media_artists = format_artist_names(
            str(info.get("artist") or info.get("creator") or "")
        )
    description_artists = ""
    description = str(info.get("description") or "")
    credit_match = re.search(
        r"^\s*(?:singers?|vocals?|performers?|artists?)\s*[:\-–—]\s*(.+?)\s*$",
        description,
        flags=re.I | re.M,
    )
    if credit_match:
        description_artists = format_artist_names(credit_match.group(1).rstrip("."))
    if not media_artists:
        media_artists = description_artists
    channel = format_artist_names(
        str(info.get("uploader") or info.get("channel") or "")
    )
    evidence = " ".join((media_artists, channel)).lower()
    if requested and all(
        part.strip().lower() in evidence
        for part in requested.split(",")
        if part.strip()
    ):
        return requested
    if channel and channel.lower() in media_artists.lower():
        return channel
    if requested:
        return requested
    if media_artists:
        return media_artists
    generic_channel = re.search(
        r"\b(?:t[- ]?series|vevo|records?|music|entertainment|official|label|network)\b",
        channel,
        flags=re.I,
    )
    return channel if channel and not generic_channel else "Unknown"


def _best_thumbnail(info: dict[str, Any]) -> str:
    """Return the largest thumbnail exposed by yt-dlp."""
    thumbnails = info.get("thumbnails")
    if isinstance(thumbnails, list):
        valid = [item for item in thumbnails if isinstance(item, dict) and item.get("url")]
        if valid:
            best = max(
                valid,
                key=lambda item: (
                    int(item.get("width") or 0) * int(item.get("height") or 0),
                    int(item.get("preference") or 0),
                ),
            )
            return str(best.get("url") or "").strip()
    return str(info.get("thumbnail") or "").strip()
