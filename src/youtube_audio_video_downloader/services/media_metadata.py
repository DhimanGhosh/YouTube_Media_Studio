"""Read and atomically replace editable metadata on local audio files."""

from __future__ import annotations

import base64
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from mutagen import File as MutagenFile
from mutagen.flac import Picture
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TRCK,
)
from mutagen.mp4 import MP4Cover

from youtube_audio_video_downloader.core.file_access import retry_file_operation
from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names


@dataclass(slots=True)
class EditableMediaMetadata:
    """The common song fields exposed by the Edit File workspace."""

    title: str = ""
    album: str = ""
    artists: str = ""
    album_artist: str = ""
    year: str = ""
    track_number: str = ""
    track_total: str = ""
    artwork_present: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_media_metadata(path: str | Path) -> EditableMediaMetadata:
    """Return common editable tags from a Mutagen-supported media file."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Existing media file does not exist: {source}")
    media = MutagenFile(source)
    if media is None:
        raise ValueError(f"Unsupported or unreadable media file: {source.suffix or source.name}")
    tags = media.tags
    result = EditableMediaMetadata()
    if isinstance(tags, ID3):
        result.title = _id3_text(tags, "TIT2")
        result.album = _id3_text(tags, "TALB")
        result.artists = ", ".join(_id3_values(tags, "TPE1"))
        result.album_artist = ", ".join(_id3_values(tags, "TPE2"))
        result.year = _id3_text(tags, "TDRC")
        result.track_number, result.track_total = _split_number(_id3_text(tags, "TRCK"))
        result.artwork_present = bool(tags.getall("APIC"))
        return result

    if _is_mp4(media):
        result.title = _first(tags, "\xa9nam")
        result.album = _first(tags, "\xa9alb")
        result.artists = ", ".join(_values(tags, "\xa9ART"))
        result.album_artist = ", ".join(_values(tags, "aART"))
        result.year = _first(tags, "\xa9day")
        result.track_number, result.track_total = _mp4_pair(tags, "trkn")
        result.artwork_present = bool(tags and tags.get("covr"))
        return result

    result.title = _first(tags, "title")
    result.album = _first(tags, "album")
    result.artists = ", ".join(_values(tags, "artist"))
    result.album_artist = ", ".join(_values(tags, "albumartist"))
    result.year = _first(tags, "date") or _first(tags, "year")
    result.track_number, result.track_total = _split_number(_first(tags, "tracknumber"))
    pictures = getattr(media, "pictures", None)
    result.artwork_present = bool(pictures) or bool(tags and tags.get("metadata_block_picture"))
    return result


def replace_media_metadata(
    path: str | Path,
    metadata: dict[str, Any] | EditableMediaMetadata,
    *,
    artwork_path: str | Path | None = None,
    remove_artwork: bool = False,
) -> Path:
    """Write tags through a validated temporary copy, then replace the source."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Existing media file does not exist: {source}")
    temporary = source.with_name(f".{source.stem}.metadata-{uuid.uuid4().hex}{source.suffix}")
    try:
        shutil.copy2(source, temporary)
        write_media_metadata(
            temporary,
            metadata,
            artwork_path=artwork_path,
            remove_artwork=remove_artwork,
        )
        shutil.copystat(source, temporary)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("Metadata editing did not produce a valid media file")
        retry_file_operation(
            source,
            "updating its metadata",
            lambda: os.replace(temporary, source),
        )
    finally:
        if temporary.exists():
            temporary.unlink()
    return source


def write_media_metadata(
    path: str | Path,
    metadata: dict[str, Any] | EditableMediaMetadata,
    *,
    artwork_path: str | Path | None = None,
    remove_artwork: bool = False,
) -> Path:
    """Replace common tags on an already staged media file."""

    target = Path(path).expanduser().resolve()
    values = metadata.as_dict() if isinstance(metadata, EditableMediaMetadata) else dict(metadata)
    media = MutagenFile(target)
    if media is None:
        raise ValueError(f"Unsupported or unreadable media file: {target.suffix or target.name}")
    if media.tags is None:
        media.add_tags()
    tags = media.tags
    if isinstance(tags, ID3):
        _write_id3(tags, values)
    elif _is_mp4(media):
        _write_mp4(tags, values)
    else:
        _write_mapping_tags(tags, values)
    _write_artwork(media, target, artwork_path, remove_artwork)
    retry_file_operation(target, "writing its metadata", media.save)
    return target


def _write_id3(tags: ID3, values: dict[str, Any]) -> None:
    frames = {
        "title": ("TIT2", TIT2), "album": ("TALB", TALB), "year": ("TDRC", TDRC),
    }
    for key, (frame_id, frame_type) in frames.items():
        if key not in values:
            continue
        tags.delall(frame_id)
        text = _clean(values.get(key))
        if text:
            tags.add(frame_type(encoding=3, text=text))
    if "artists" in values:
        tags.delall("TPE1")
        artists = _artists(values.get("artists"))
        if artists:
            tags.add(TPE1(encoding=3, text=artists))
    if "album_artist" in values:
        tags.delall("TPE2")
        album_artists = _artists(values.get("album_artist"))
        if album_artists:
            tags.add(TPE2(encoding=3, text=album_artists))
    if "track_number" in values or "track_total" in values:
        tags.delall("TRCK")
        number = _number_pair(values, "track")
        if number:
            tags.add(TRCK(encoding=3, text=number))


def _write_mp4(tags: Any, values: dict[str, Any]) -> None:
    mapping = {
        "title": "\xa9nam", "album": "\xa9alb", "year": "\xa9day",
    }
    for field, atom in mapping.items():
        if field not in values:
            continue
        text = _clean(values.get(field))
        if text:
            tags[atom] = [text]
        else:
            tags.pop(atom, None)
    if "artists" in values:
        artists = _artists(values.get("artists"))
        if artists:
            tags["\xa9ART"] = artists
        else:
            tags.pop("\xa9ART", None)
    if "album_artist" in values:
        album_artists = _artists(values.get("album_artist"))
        if album_artists:
            tags["aART"] = album_artists
        else:
            tags.pop("aART", None)
    if "track_number" in values or "track_total" in values:
        number = _integer(values.get("track_number"))
        total = _integer(values.get("track_total"))
        if number or total:
            tags["trkn"] = [(number, total)]
        else:
            tags.pop("trkn", None)


def _write_mapping_tags(tags: Any, values: dict[str, Any]) -> None:
    mapping = {
        "title": "title", "album": "album", "year": "date",
    }
    for field, key in mapping.items():
        if field not in values:
            continue
        text = _clean(values.get(field))
        if text:
            tags[key] = [text]
        else:
            tags.pop(key, None)
    if "artists" in values:
        artists = _artists(values.get("artists"))
        if artists:
            tags["artist"] = artists
        else:
            tags.pop("artist", None)
    if "album_artist" in values:
        album_artists = _artists(values.get("album_artist"))
        if album_artists:
            tags["albumartist"] = album_artists
        else:
            tags.pop("albumartist", None)
    if "track_number" in values or "track_total" in values:
        number = _number_pair(values, "track")
        if number:
            tags["tracknumber"] = [number]
        else:
            tags.pop("tracknumber", None)


def _write_artwork(media: Any, target: Path, artwork_path: str | Path | None, remove: bool) -> None:
    replacement = str(artwork_path or "").strip()
    if not replacement and not remove:
        return
    data = b""
    mime = "image/jpeg"
    if replacement:
        data = _load_artwork_bytes(replacement)
        kind = _image_kind(data)
        if kind not in {"jpeg", "png"}:
            raise ValueError("Artwork must be a JPEG or PNG image")
        mime = "image/png" if kind == "png" else "image/jpeg"
    tags = media.tags
    if isinstance(tags, ID3):
        tags.delall("APIC")
        if data:
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        return
    if _is_mp4(media):
        tags.pop("covr", None)
        if data:
            image_format = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
            tags["covr"] = [MP4Cover(data, imageformat=image_format)]
        return
    if hasattr(media, "clear_pictures") and hasattr(media, "add_picture"):
        media.clear_pictures()
        if data:
            picture = Picture()
            picture.type = 3
            picture.mime = mime
            picture.desc = "Cover"
            picture.data = data
            media.add_picture(picture)
        return
    tags.pop("metadata_block_picture", None)
    if data:
        picture = Picture()
        picture.type, picture.mime, picture.desc, picture.data = 3, mime, "Cover", data
        tags["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]


def _id3_values(tags: ID3, key: str) -> list[str]:
    frame = tags.get(key)
    return [str(value) for value in getattr(frame, "text", []) if str(value).strip()]


def _id3_text(tags: ID3, key: str) -> str:
    return " / ".join(_id3_values(tags, key))


def _values(tags: Any, key: str) -> list[str]:
    if not tags:
        return []
    value = tags.get(key, [])
    if not isinstance(value, (list, tuple)):
        value = [value]
    return [str(item) for item in value if str(item).strip()]


def _first(tags: Any, key: str) -> str:
    values = _values(tags, key)
    return values[0] if values else ""


def _split_number(value: str) -> tuple[str, str]:
    current, separator, total = str(value or "").partition("/")
    return current.strip(), total.strip() if separator else ""


def _mp4_pair(tags: Any, key: str) -> tuple[str, str]:
    try:
        number, total = tags[key][0]
        return str(number or ""), str(total or "")
    except (KeyError, IndexError, TypeError, ValueError):
        return "", ""


def _number_pair(values: dict[str, Any], prefix: str) -> str:
    number = _clean(values.get(f"{prefix}_number"))
    total = _clean(values.get(f"{prefix}_total"))
    return f"{number}/{total}" if total else number


def _artists(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(item).strip() for item in value if str(item).strip())
    formatted = format_artist_names(str(value or ""))
    return [item.strip() for item in formatted.split(",") if item.strip()]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int:
    text = _clean(value)
    if not text:
        return 0
    try:
        number = int(text)
    except ValueError as exc:
        raise ValueError(f"Track and disc values must be whole numbers: {text!r}") from exc
    if number < 0:
        raise ValueError("Track and disc values cannot be negative")
    return number


def _is_mp4(media: Any) -> bool:
    return media.__class__.__name__ in {"MP4", "M4A"}


def _image_kind(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return ""


def _load_artwork_bytes(source: str) -> bytes:
    parsed = urlparse(source)
    if parsed.scheme.lower() in {"http", "https"}:
        request = Request(source, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310 - user-selected URL.
                data = response.read(15 * 1024 * 1024 + 1)
        except (OSError, URLError, TimeoutError) as exc:
            raise ValueError(f"Could not download artwork: {exc}") from exc
        if len(data) > 15 * 1024 * 1024:
            raise ValueError("Artwork image is larger than 15 MB")
        return data
    image = Path(source).expanduser().resolve()
    if not image.is_file():
        raise FileNotFoundError(f"Artwork image does not exist: {image}")
    if image.stat().st_size > 15 * 1024 * 1024:
        raise ValueError("Artwork image is larger than 15 MB")
    return image.read_bytes()
