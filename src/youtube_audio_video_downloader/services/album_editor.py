"""Read and update album-level metadata across a complete media folder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.core.file_access import retry_file_operation
from youtube_audio_video_downloader.core.file_utils import safe_filename
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
    artists: str
    mixed_fields: tuple[str, ...] = ()
    artwork_files: int = 0


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
    values: dict[str, set[str]] = {"album": set(), "year": set(), "artists": set()}
    readable: list[Path] = []
    artwork_files = 0
    for path in files:
        try:
            metadata = read_media_metadata(path)
        except (OSError, RuntimeError, ValueError):
            continue
        readable.append(path)
        if metadata.artwork_present:
            artwork_files += 1
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
        artists=_single_value(values["artists"]),
        mixed_fields=mixed,
        artwork_files=artwork_files,
    )


def edit_album_folder(
    folder: str | Path,
    metadata: dict[str, Any],
    *,
    artwork_path: str | Path | None = None,
    remove_artwork: bool = False,
    cancellation_token: CancellationToken | None = None,
) -> AlbumEditResult:
    """Apply shared album tags while optionally preserving per-track artists."""

    root = _album_folder(folder)
    files = _media_files(root)
    if not files:
        raise ValueError("The selected folder contains no supported media files.")
    shared_values = {
        "album": str(metadata.get("album") or "").strip(),
        "year": str(metadata.get("year") or "").strip(),
    }
    artist_override = str(metadata.get("artists") or "").strip()
    if not shared_values["album"]:
        raise ValueError("Album name is required.")
    if shared_values["year"] and not re.fullmatch(r"\d{4}", shared_values["year"]):
        raise ValueError("Release year must be a four-digit year or blank.")
    selected_artwork = str(artwork_path or "").strip()
    if selected_artwork and remove_artwork:
        raise ValueError("Choose replacement artwork or remove existing artwork, not both.")
    token = cancellation_token or CancellationToken()
    updated: list[Path] = []
    failed: list[tuple[Path, str]] = []
    for path in files:
        token.raise_if_cancelled()
        try:
            current = read_media_metadata(path)
            file_values = dict(shared_values)
            if artist_override:
                file_values["artists"] = artist_override
            replace_media_metadata(
                path,
                file_values,
                artwork_path=selected_artwork or None,
                remove_artwork=remove_artwork,
            )
            rename_values = {
                **file_values,
                "artists": artist_override or current.artists.strip(),
            }
            updated_path = _rename_album_file(path, current.title, rename_values)
        except (OSError, RuntimeError, ValueError) as exc:
            failed.append((path, str(exc)))
            print(f"[ALBUM-EDIT-FAILED] {path.name} | {exc}")
            continue
        updated.append(updated_path)
        print(f"[ALBUM-EDITED] {path.name} -> {updated_path.name}")
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


def _rename_album_file(path: Path, title: str, values: dict[str, str]) -> Path:
    """Rename one album file from its preserved title and new shared identity."""

    clean_title = str(title or "").strip()
    album = values["album"]
    year = values["year"]
    artists = values["artists"]
    if not clean_title:
        raise ValueError(f"Title is required to rename {path.name}.")
    album_for_filename = album
    if year and not re.search(rf"(?:^|\D){re.escape(year)}(?:\D|$)", album):
        album_for_filename = f"{album} ({year})"
    stem = safe_filename(
        f"{clean_title} - {album_for_filename} - {artists}",
        fallback=path.stem,
    )
    desired = path.with_name(f"{stem}{path.suffix}")
    if desired == path:
        return path
    destination = _available_path(desired)
    retry_file_operation(
        path,
        "renaming album media after its metadata update",
        lambda: path.rename(destination),
    )
    return destination


def _available_path(desired: Path) -> Path:
    if not desired.exists():
        return desired
    counter = 2
    while True:
        candidate = desired.with_name(f"{desired.stem} ({counter}){desired.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1
