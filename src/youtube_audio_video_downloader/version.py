"""Resolve the application version consistently in source and packaged builds."""

from __future__ import annotations

import sys
import tomllib
from functools import lru_cache
from importlib import metadata
from pathlib import Path

from youtube_audio_video_downloader.config.app_identity import PACKAGE_DISTRIBUTION


@lru_cache(maxsize=1)
def application_version() -> str:
    """Return installed package metadata, with build/source file fallbacks."""

    try:
        value = metadata.version(PACKAGE_DISTRIBUTION).strip()
    except metadata.PackageNotFoundError:
        value = ""
    if value:
        return value

    executable_version = Path(sys.executable).resolve().parent / "version.txt"
    try:
        value = executable_version.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value

    source_project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        with source_project.open("rb") as handle:
            value = str(tomllib.load(handle)["project"]["version"]).strip()
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError):
        value = ""
    return value or "unknown"
