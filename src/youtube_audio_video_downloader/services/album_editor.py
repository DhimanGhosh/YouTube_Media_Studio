"""Read and update album-level metadata across a complete media folder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.services.media_library import MEDIA_EXTENSIONS
from youtube_audio_video_downloader.services.media_metadata import (
    read_media_metadata,
    replace_media_metadata,
)


@dataclass(frozen=True, slots=True)
class AlbumFolderMetadata:
    folder: Path
    files: tuple[Path, ...]
    album: str
    year: str
    album_artist: str
    mixed_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AlbumEditResult:
    updated: tuple[Path, ...]
    failed: tuple[tuple[Path, str], ...]


def inspect_album_folder(folder: str | Path) -> AlbumFolderMetadata:
    """Summarize shared editable tags across supported files in an album folder."""

    root = _album_folder(folder)
    files = _media_files(root)
    if not files:
        raise ValueError("The selected folder contains no supported media files.")
    values: dict[str, set[str]] = {"album": set(), "year": set(), "album_artist": set()}
    readable: list[Path] = []
    for path in files:
        try:
            metadata = read_media_metadata(path)
        except (OSError, RuntimeError, ValueError):
            continue
        readable.append(path)
        for field in values:
            text = str(getattr(metadata, field) or "").strip()
            if text:
                values[field].add(text)
    if not readable:
        raise ValueError("No readable metadata was found in the selected album folder.")
    mixed = tuple(field for field, found in values.items() if len(found) > 1)
    return AlbumFolderMetadata(
        folder=root,
        files=tuple(files),
        album=_single_value(values["album"]),
        year=_single_value(values["year"]),
        album_artist=_single_value(values["album_artist"]),
        mixed_fields=mixed,
    )


def edit_album_folder(
    folder: str | Path,
    metadata: dict[str, Any],
    *,
    cancellation_token: CancellationToken | None = None,
) -> AlbumEditResult:
    """Apply only album, year, and album-artist tags to every supported media file."""

    root = _album_folder(folder)
    files = _media_files(root)
    if not files:
        raise ValueError("The selected folder contains no supported media files.")
    values = {
        "album": str(metadata.get("album") or "").strip(),
        "year": str(metadata.get("year") or "").strip(),
        "album_artist": str(metadata.get("album_artist") or "").strip(),
    }
    if not values["album"]:
        raise ValueError("Album name is required.")
    if values["year"] and not re.fullmatch(r"\d{4}", values["year"]):
        raise ValueError("Release year must be a four-digit year or blank.")
    token = cancellation_token or CancellationToken()
    updated: list[Path] = []
    failed: list[tuple[Path, str]] = []
    for path in files:
        token.raise_if_cancelled()
        try:
            replace_media_metadata(path, values)
        except (OSError, RuntimeError, ValueError) as exc:
            failed.append((path, str(exc)))
            print(f"[ALBUM-EDIT-FAILED] {path.name} | {exc}")
            continue
        updated.append(path)
        print(f"[ALBUM-EDITED] {path.name}")
    return AlbumEditResult(tuple(updated), tuple(failed))


def _album_folder(folder: str | Path) -> Path:
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Album folder does not exist: {root}")
    return root


def _media_files(root: Path) -> list[Path]:
    return sorted(
        (
            path for path in root.rglob("*")
            if path.is_file() and path.suffix.casefold() in MEDIA_EXTENSIONS
        ),
        key=lambda path: str(path).casefold(),
    )


def _single_value(values: set[str]) -> str:
    return next(iter(values)) if len(values) == 1 else ""
