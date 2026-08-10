"""Data models used by the downloader, tagger and video downloader."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class DownloadStatus(StrEnum):
    """Supported operation status values."""

    DOWNLOADED = "downloaded"
    TAGGED = "tagged"
    SKIPPED = "skipped"
    FAILED = "failed"
    ALREADY_EXISTS = "already_exists"
    LISTED = "listed"


class MediaSelectionKind(StrEnum):
    """What the video command should download for one JSON entry."""

    VIDEO = "video"
    AUDIO = "audio"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class ParsedSongMetadata:
    """Metadata parsed from either JSON fields or a structured file name.

    Expected file name format:
        <title> - <album> - <comma separated artists>
    """

    title: str
    album: str
    artists: list[str]


@dataclass(frozen=True, slots=True)
class Song:
    """Normalized song metadata loaded from the downloader JSON file."""

    json_key: str
    ytb_link: str
    file_name: str
    parsed_metadata: ParsedSongMetadata
    album_art: str = ""
    release_year: str = ""
    track_number: int | None = None
    track_total: int | None = None
    start_timestamp: str = "00:00"
    end_timestamp: str = ""


@dataclass(frozen=True, slots=True)
class ExistingMp3TagJob:
    """Normalized metadata job for an already downloaded MP3 file."""

    json_key: str
    mp3_file_path: Path
    parsed_metadata: ParsedSongMetadata
    album_art: str = ""
    release_year: str = ""

    @property
    def as_song(self) -> Song:
        """Return a Song-compatible object for the shared MetadataTagger."""

        return Song(
            json_key=self.json_key,
            ytb_link="",
            file_name=self.mp3_file_path.stem,
            parsed_metadata=self.parsed_metadata,
            album_art=self.album_art,
            release_year=self.release_year,
        )


@dataclass(frozen=True, slots=True)
class VideoJob:
    """Normalized video download job loaded from a JSON file.

    ``file_name`` is optional for video jobs. When it is not supplied in JSON,
    the downloader extracts the official YouTube title from yt-dlp metadata and
    uses that as the output file name.
    """

    json_key: str
    ytb_link: str
    file_name: str | None = None
    resolution: str | None = None
    start_timestamp: str = "00:00"
    end_timestamp: str = ""


@dataclass(frozen=True, slots=True)
class VideoQuality:
    """One downloadable video quality option."""

    label: str
    height: int
    width: int | None
    fps: float | None
    video_format_id: str
    audio_format_id: str | None
    video_ext: str
    audio_ext: str | None
    estimated_size_bytes: int | None
    note: str = ""

    @property
    def format_selector(self) -> str:
        """Return the exact yt-dlp format selector for this quality."""

        if self.audio_format_id:
            return f"{self.video_format_id}+{self.audio_format_id}/{self.video_format_id}"
        return self.video_format_id


@dataclass(frozen=True, slots=True)
class AudioQuality:
    """Best audio option shown by the video command as an MP3 choice."""

    label: str
    format_id: str
    source_ext: str
    estimated_size_bytes: int | None
    abr: float | None = None


@dataclass(frozen=True, slots=True)
class MediaSelection:
    """Final media choice for one video JSON entry."""

    kind: MediaSelectionKind
    video_quality: VideoQuality | None = None
    audio_quality: AudioQuality | None = None


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """Result of one download/tagging attempt."""

    song: str
    status: DownloadStatus
    file_name: str | None = None
    reason: str | None = None
