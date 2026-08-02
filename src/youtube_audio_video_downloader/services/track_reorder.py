"""Reorder media track-number tags without changing filenames or media streams."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, TRCK

from youtube_audio_video_downloader.core.file_access import (
    FileInUseSkippedError,
    retry_file_operation,
)


SUPPORTED_TRACK_EXTENSIONS = frozenset(
    {".aif", ".aiff", ".ape", ".flac", ".m4a", ".m4b", ".mp3", ".mp4",
     ".oga", ".ogg", ".opus", ".wav", ".wma", ".wv"}
)


@dataclass(frozen=True, slots=True)
class TrackFile:
    path: Path
    track_number: int | None


def list_track_files(folder: Path) -> list[TrackFile]:
    """Return supported files ordered by their current number, then filename."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise NotADirectoryError(f"Album folder does not exist: {folder}")
    tracks = [
        TrackFile(path, read_track_number(path))
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_TRACK_EXTENSIONS
    ]
    return sorted(tracks, key=lambda item: (
        item.track_number is None,
        item.track_number if item.track_number is not None else 0,
        item.path.name.casefold(),
    ))


def read_track_number(path: Path) -> int | None:
    audio = MutagenFile(path, easy=True)
    if audio is None:
        raise ValueError(f"Unsupported or unreadable media file: {path.name}")
    value = _track_value(audio)
    match = re.match(r"\s*(\d+)", value or "")
    return int(match.group(1)) if match else None


def reorder_track_numbers(
    paths: list[Path], *, retries: int = 3, normalize_total: bool = False
) -> int:
    """Set sequential numbers while preserving all other tags and media streams."""
    if not paths:
        raise ValueError("No songs were found in the selected folder.")
    resolved = [path.expanduser().resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError("The reorder list contains the same file more than once.")
    parent = resolved[0].parent
    if any(path.parent != parent for path in resolved):
        raise ValueError("All songs must be from the same folder.")

    # Validate every item before changing the first one.
    loaded: list[tuple[Path, object, str | None]] = []
    for path in resolved:
        if not path.is_file():
            raise FileNotFoundError(f"Song no longer exists: {path}")
        if path.suffix.lower() not in SUPPORTED_TRACK_EXTENSIONS:
            raise ValueError(f"Unsupported media type: {path.name}")
        audio = MutagenFile(path, easy=True)
        if audio is None:
            raise ValueError(f"Unsupported or unreadable media file: {path.name}")
        loaded.append((path, audio, _track_value(audio)))

    updated = 0
    attempts = max(1, int(retries))
    for number, (path, audio, old_value) in enumerate(loaded, start=1):
        total_match = re.match(r"\s*\d+\s*/\s*(\d+)\s*$", old_value or "")
        if normalize_total:
            new_value = f"{number}/{len(loaded)}"
        else:
            new_value = f"{number}/{total_match.group(1)}" if total_match else str(number)
        for attempt in range(1, attempts + 1):
            try:
                _set_track_value(audio, new_value)
                retry_file_operation(
                    path, "updating its track number", audio.save
                )
                updated += 1
                print(f"[REORDERED] {path.name}: track number {number}")
                break
            except FileInUseSkippedError as exc:
                print(f"[SKIPPED] {exc}")
                break
            except Exception as exc:  # noqa: BLE001
                if attempt >= attempts:
                    raise OSError(f"Could not update {path.name}: {exc}") from exc
                print(
                    f"[RETRY] {path.name}: track update failed ({exc}); "
                    f"attempt {attempt + 1}/{attempts}"
                )
                time.sleep(min(2.0 ** (attempt - 1), 5.0))
    return updated


def _track_value(audio: object) -> str | None:
    """Read the native track field without normalizing any unrelated tag."""
    class_name = type(audio).__name__
    tags = getattr(audio, "tags", None)
    if class_name in {"WAVE", "AIFF"}:
        frames = tags.getall("TRCK") if isinstance(tags, ID3) else []
        return str(frames[0]) if frames else None
    if class_name == "ASF":
        values = tags.get("WM/TrackNumber", []) if tags is not None else []
        return str(values[0]) if values else None
    values = audio.get("tracknumber", [])  # type: ignore[attr-defined]
    return str(values[0]) if values else None


def _set_track_value(audio: object, value: str) -> None:
    """Write the one format-specific field used for album track order."""
    class_name = type(audio).__name__
    if class_name in {"WAVE", "AIFF"}:
        tags = getattr(audio, "tags", None)
        if tags is None:
            audio.add_tags()  # type: ignore[attr-defined]
            tags = audio.tags  # type: ignore[attr-defined]
        tags.delall("TRCK")
        tags.add(TRCK(encoding=3, text=value))
        return
    if class_name == "ASF":
        audio.tags["WM/TrackNumber"] = [value]  # type: ignore[attr-defined]
        return
    audio["tracknumber"] = [value]  # type: ignore[index]
