"""Public package interface for YouTube Media Studio."""

from youtube_audio_video_downloader.api import (
    SUPPORTED_OPERATIONS,
    CancellationToken,
    MediaStudio,
    Operation,
    OperationSummary,
    run_operation,
)
from youtube_audio_video_downloader.version import application_version


__version__ = application_version()

__all__ = [
    "CancellationToken",
    "MediaStudio",
    "Operation",
    "OperationSummary",
    "SUPPORTED_OPERATIONS",
    "__version__",
    "run_operation",
]
