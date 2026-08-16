"""Shared timestamp-range validation for audio and video downloads."""

from __future__ import annotations

import math
from typing import Any

from youtube_audio_video_downloader.services.media.audio_trimmer import parse_timestamp


def build_download_range_options(
    start_timestamp: str = "00:00", end_timestamp: str = ""
) -> dict[str, Any]:
    """Return yt-dlp range options, or no options for a full-source download."""

    start_text = str(start_timestamp or "").strip() or "00:00"
    end_text = str(end_timestamp or "").strip()
    start = parse_timestamp(start_text, label="Start timestamp")
    end = (
        parse_timestamp(end_text, label="End timestamp")
        if end_text
        else math.inf
    )
    if end <= start:
        raise ValueError("Start timestamp must be earlier than end timestamp")
    if start == 0 and math.isinf(end):
        return {}

    from yt_dlp.utils import download_range_func

    return {
        "download_ranges": download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
    }
