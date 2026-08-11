"""Local media-library discovery, metadata indexing, and filtering."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from mutagen import File as MutagenFile, MutagenError

from youtube_audio_video_downloader.services.album_names import canonical_album_name
from youtube_audio_video_downloader.services.media_metadata import read_media_metadata


AUDIO_EXTENSIONS = frozenset({".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"})
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


@dataclass(frozen=True, slots=True)
class LibraryItem:
    path: str
    title: str
    album: str
    artists: str
    year: int | None
    duration_ms: int
    media_type: str
    modified_ns: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def scan_library(
    folders: Iterable[str | Path],
    *,
    cancelled: Callable[[], bool] | None = None,
) -> list[LibraryItem]:
    """Recursively index supported media in the selected folders."""

    paths: dict[str, Path] = {}
    for folder in folders:
        if cancelled and cancelled():
            return []
        root = Path(folder).expanduser()
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if cancelled and cancelled():
                return []
            if path.is_file() and path.suffix.casefold() in MEDIA_EXTENSIONS:
                resolved = path.resolve()
                paths[str(resolved).casefold()] = resolved
    items = []
    for path in paths.values():
        if cancelled and cancelled():
            return []
        items.append(_read_item(path))
    return sorted(items, key=lambda item: (item.artists.casefold(), item.album.casefold(), item.title.casefold()))


def filter_library(
    items: Iterable[LibraryItem],
    *,
    query: str = "",
    artists: Iterable[str] = (),
    albums: Iterable[str] = (),
    year_from: int | None = None,
    year_to: int | None = None,
    media_type: str = "all",
) -> list[LibraryItem]:
    """Filter items by free text and any combination of collection facets."""

    needles = [part.casefold() for part in query.split() if part.strip()]
    wanted_artists = {value.casefold() for value in artists if value.strip()}
    wanted_albums = {value.casefold() for value in albums if value.strip()}
    result: list[LibraryItem] = []
    for item in items:
        haystack = f"{item.title} {item.album} {item.artists} {item.year or ''} {Path(item.path).name}".casefold()
        item_artists = {part.casefold() for part in split_artists(item.artists)}
        if needles and not all(needle in haystack for needle in needles):
            continue
        if wanted_artists and item_artists.isdisjoint(wanted_artists):
            continue
        if wanted_albums and item.album.casefold() not in wanted_albums:
            continue
        if year_from is not None and (item.year is None or item.year < year_from):
            continue
        if year_to is not None and (item.year is None or item.year > year_to):
            continue
        if media_type != "all" and item.media_type != media_type:
            continue
        result.append(item)
    return result


def split_artists(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s*(?:,|;|/|\s+&\s+)\s*", value) if part.strip()]


def artwork_bytes(path: str | Path) -> bytes:
    """Return embedded front-cover bytes when Mutagen exposes them."""

    try:
        media = MutagenFile(Path(path), easy=False)
        if media is None:
            return b""
        tags = media.tags
        if tags is not None and hasattr(tags, "getall"):
            pictures = tags.getall("APIC")
            if pictures:
                return bytes(pictures[0].data)
        covers = tags.get("covr", []) if tags is not None else []
        if covers:
            return bytes(covers[0])
        pictures = getattr(media, "pictures", [])
        if pictures:
            return bytes(pictures[0].data)
    except (MutagenError, OSError, TypeError, ValueError):
        pass
    return b""


def video_thumbnail_bytes(path: str | Path, duration_ms: int = 0) -> bytes:
    """Return embedded artwork or a small representative video frame."""

    embedded = artwork_bytes(path)
    if embedded:
        return embedded
    ffmpeg = shutil.which("ffmpeg")
    source = Path(path)
    if not ffmpeg or not source.is_file():
        return b""
    seek_seconds = (
        min(30.0, max(0.1, duration_ms / 10_000))
        if duration_ms > 0
        else 1.0
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{seek_seconds:.3f}",
        "-i",
        str(source),
        "-map",
        "0:V:0",
        "-frames:v",
        "1",
        "-vf",
        "scale=320:180:force_original_aspect_ratio=decrease",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return b""
    return completed.stdout if completed.returncode == 0 else b""


def _read_item(path: Path) -> LibraryItem:
    title, album, artists, year = path.stem, "Unknown Album", "Unknown Artist", None
    try:
        metadata = read_media_metadata(path)
        title = metadata.title.strip() or title
        album = metadata.album.strip() or album
        artists = metadata.artists.strip() or artists
        match = re.search(r"(?:19|20)\d{2}", metadata.year)
        year = int(match.group()) if match else None
        if album != "Unknown Album":
            album = canonical_album_name(album, year)
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    duration_ms = 0
    try:
        media = MutagenFile(path)
        duration_ms = max(0, round(float(getattr(media.info, "length", 0.0)) * 1000)) if media else 0
    except (MutagenError, OSError, TypeError, ValueError):
        pass
    return LibraryItem(
        path=str(path), title=title, album=album, artists=artists, year=year,
        duration_ms=duration_ms,
        media_type="audio" if path.suffix.casefold() in AUDIO_EXTENSIONS else "video",
        modified_ns=path.stat().st_mtime_ns,
    )
