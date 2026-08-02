"""Paths for GUI assets in source checkouts and frozen applications."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_root() -> Path:
    """Return the PyInstaller extraction root or the source project root."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[3]


def asset_path(name: str) -> Path:
    """Return an asset path without depending on the current directory."""

    return resource_root() / "assets" / name


def application_icon_path() -> Path:
    """Return the cross-platform transparent application icon."""

    return asset_path("youtube_media_studio.png")
