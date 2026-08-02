"""Central settings for safe, conservative download behavior.

The defaults enable bounded parallel downloading for batch workflows while
staggering every network download with an independent randomized delay. CLI
options can still override these values for a particular run.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field


def machine_parallel_workers() -> int:
    """Return the processors this app can use on the current operating system."""

    if sys.platform.startswith("linux") and hasattr(os, "sched_getaffinity"):
        try:
            return max(1, len(os.sched_getaffinity(0)))
        except OSError:
            pass
    if sys.platform == "win32":
        try:
            return max(1, int(os.environ.get("NUMBER_OF_PROCESSORS", "0")))
        except ValueError:
            pass
    return max(1, int(os.cpu_count() or 1))


MAX_PARALLEL_WORKERS = machine_parallel_workers()


@dataclass(frozen=True, slots=True)
class DownloadSettings:
    """Runtime settings for the downloader."""

    max_workers: int = field(default_factory=machine_parallel_workers)
    min_delay_seconds: int = 10
    max_delay_seconds: int = 25
    max_retries: int = 3
    rate_limit_wait_seconds: int = 180
    retry_wait_seconds: int = 60
    preferred_mp3_quality: str = "320"
    audio_sample_rate: str = "44100"
    skip_existing: bool = True
    default_video_resolution: str = "best"
    video_merge_output_format: str = "mp4"
