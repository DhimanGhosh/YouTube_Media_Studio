"""Supported values for non-destructive video playback display profiles.

The Media Library stores these values in application settings. This module
intentionally contains no media transformation or file-writing operation.
"""

from __future__ import annotations


VIDEO_ASPECT_OPTIONS: tuple[str, ...] = (
    "Default", "16:9", "4:3", "1:1", "16:10", "2.21:1", "2.35:1", "2.39:1", "5:4",
)
VIDEO_CROP_OPTIONS: tuple[str, ...] = (
    "Default", "16:10", "16:9", "4:3", "1.85:1", "2.21:1", "2.35:1", "2.39:1",
    "5:3", "5:4", "1:1",
)
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"})
