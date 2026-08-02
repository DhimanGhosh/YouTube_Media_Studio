"""Domain models used across downloader services."""

from youtube_audio_video_downloader.domain.models import (
    AudioQuality,
    DownloadResult,
    DownloadStatus,
    ExistingMp3TagJob,
    MediaSelection,
    MediaSelectionKind,
    ParsedSongMetadata,
    Song,
    VideoJob,
    VideoQuality,
)

__all__ = [
    "AudioQuality",
    "DownloadResult",
    "DownloadStatus",
    "ExistingMp3TagJob",
    "MediaSelection",
    "MediaSelectionKind",
    "ParsedSongMetadata",
    "Song",
    "VideoJob",
    "VideoQuality",
]
