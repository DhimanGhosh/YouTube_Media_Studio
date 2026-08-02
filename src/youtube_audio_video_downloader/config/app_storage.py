"""Resolve and migrate the application's user-controlled persistent data directory."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PyQt6.QtCore import QSettings


DATA_DIRECTORY_ENV = "YOUTUBE_MEDIA_STUDIO_DATA_DIR"
DATA_DIRECTORY_NAME = "YouTubeMediaStudioData"
LOCATION_FILE_NAME = "YouTubeMediaStudio.storage.json"
SETTINGS_FILE_NAME = "settings.ini"
MIGRATION_MARKER_NAME = ".legacy-data-imported"


def application_directory() -> Path:
    """Return the stable folder containing the executable, or the source-run cwd."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def location_file(base_directory: str | Path | None = None) -> Path:
    return Path(base_directory or application_directory()) / LOCATION_FILE_NAME


def default_data_directory(base_directory: str | Path | None = None) -> Path:
    # Installed/frozen applications must not keep settings beside the binary:
    # release cleanup and upgrades are allowed to replace that directory.
    if base_directory is None and getattr(sys, "frozen", False):
        return platform_data_directory()
    return Path(base_directory or application_directory()) / DATA_DIRECTORY_NAME


def platform_data_directory() -> Path:
    """Return a writable per-user fallback for protected installation folders."""

    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return root / "DhimanTools" / "YouTube Media Studio"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "DhimanTools"
            / "YouTube Media Studio"
        )
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "DhimanTools" / "YouTube Media Studio"


def resolve_data_directory(
    *,
    base_directory: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> Path:
    """Resolve CLI/env, saved choice, then a durable platform default."""

    environment = os.environ if environ is None else environ
    override = environment.get(DATA_DIRECTORY_ENV, "").strip()
    if override:
        chosen = Path(os.path.expandvars(os.path.expanduser(override)))
    else:
        pointers = [location_file(base_directory)]
        if base_directory is None:
            pointers.append(platform_data_directory() / LOCATION_FILE_NAME)
        chosen = next(
            (value for pointer in pointers if (value := _read_location_file(pointer))),
            default_data_directory(base_directory),
        )
        # Older Windows/Linux builds sometimes wrote a location pointer back to
        # their own disposable release folder.  Treat only that exact historical
        # default as legacy; genuine custom locations remain authoritative.
        if base_directory is None and getattr(sys, "frozen", False):
            legacy_default = application_directory() / DATA_DIRECTORY_NAME
            if chosen.resolve() == legacy_default.resolve():
                chosen = platform_data_directory()
    chosen = chosen.resolve()
    try:
        chosen.mkdir(parents=True, exist_ok=True)
    except OSError:
        if override:
            raise
        chosen = platform_data_directory().resolve()
        chosen.mkdir(parents=True, exist_ok=True)
    if base_directory is None and getattr(sys, "frozen", False):
        legacy_default = (application_directory() / DATA_DIRECTORY_NAME).resolve()
        if chosen == platform_data_directory().resolve() and legacy_default != chosen:
            merge_newer_application_data(legacy_default, chosen)
    return chosen


def settings_file(data_directory: str | Path) -> Path:
    return Path(data_directory) / SETTINGS_FILE_NAME


def save_data_directory_choice(
    data_directory: str | Path,
    *,
    base_directory: str | Path | None = None,
) -> Path:
    """Persist the selected location beside the executable using an atomic replace."""

    chosen = Path(data_directory).expanduser().resolve()
    chosen.mkdir(parents=True, exist_ok=True)
    pointer = location_file(base_directory)
    try:
        _write_location_file(pointer, chosen)
    except OSError:
        if base_directory is not None:
            raise
        _write_location_file(platform_data_directory() / LOCATION_FILE_NAME, chosen)
    return chosen


def copy_application_data(source: str | Path, destination: str | Path) -> list[Path]:
    """Copy persistent files to a new location without overwriting existing data."""

    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if source_path == destination_path:
        return []
    if destination_path.is_relative_to(source_path):
        raise OSError("The new data folder cannot be inside the current data folder.")
    destination_path.mkdir(parents=True, exist_ok=True)
    if not source_path.exists():
        return []

    copied: list[Path] = []
    for item in source_path.iterdir():
        if item.name == MIGRATION_MARKER_NAME:
            continue
        target = destination_path / item.name
        if target.exists():
            continue
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
        copied.append(target)
    return copied


def merge_newer_application_data(
    source: str | Path, destination: str | Path
) -> list[Path]:
    """Import missing or newer legacy files while retaining newer user data."""

    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if source_path == destination_path or not source_path.is_dir():
        return []
    destination_path.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for item in source_path.rglob("*"):
        relative = item.relative_to(source_path)
        if MIGRATION_MARKER_NAME in relative.parts:
            continue
        target = destination_path / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists() and target.stat().st_mtime_ns >= item.stat().st_mtime_ns:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied.append(target)
    return copied


def migrate_legacy_data(data_directory: str | Path, settings: "QSettings") -> None:
    """Import the previous native Qt settings and AppData tracker once."""

    from PyQt6.QtCore import QSettings

    data_path = Path(data_directory)
    marker = data_path / MIGRATION_MARKER_NAME
    tracker_target = data_path / "album_enrichment_tracker.json"
    renamed_tracker = data_path / "song_enrichment_tracker.json"
    if not tracker_target.exists() and renamed_tracker.is_file():
        shutil.copy2(renamed_tracker, tracker_target)
    if marker.exists():
        return

    if not settings.allKeys():
        legacy = QSettings("DhimanTools", "YouTubeMediaStudio")
        for key in legacy.allKeys():
            settings.setValue(key, legacy.value(key))
        settings.sync()

    if not tracker_target.exists():
        for candidate in legacy_tracker_candidates():
            if candidate.is_file():
                shutil.copy2(candidate, tracker_target)
                break

    marker.write_text(
        "Legacy Qt settings and enrichment data were checked for import.\n",
        encoding="utf-8",
    )


def legacy_tracker_candidates() -> list[Path]:
    """Return historical tracker locations without requiring a QApplication."""

    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        candidates.append(
            Path(appdata)
            / "DhimanTools"
            / "YouTube Media Studio"
            / "song_enrichment_tracker.json"
        )
    candidates.extend(
        [
            Path.home()
            / "Library"
            / "Application Support"
            / "DhimanTools"
            / "YouTube Media Studio"
            / "song_enrichment_tracker.json",
            Path(
                os.environ.get(
                    "XDG_CONFIG_HOME",
                    Path.home() / ".config",
                )
            )
            / "DhimanTools"
            / "YouTube Media Studio"
            / "song_enrichment_tracker.json",
            Path.home() / ".youtube_media_studio" / "song_enrichment_tracker.json",
        ]
    )
    return candidates


def _read_location_file(pointer: Path) -> Path | None:
    if not pointer.is_file():
        return None
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        value = str(payload.get("data_directory", "")).strip()
    except (OSError, ValueError, AttributeError):
        return None
    if not value:
        return None
    selected = Path(os.path.expandvars(os.path.expanduser(value)))
    if not selected.is_absolute():
        selected = pointer.parent / selected
    return selected


def _write_location_file(pointer: Path, chosen: Path) -> None:
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = pointer.with_suffix(pointer.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"data_directory": str(chosen)}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(pointer)
