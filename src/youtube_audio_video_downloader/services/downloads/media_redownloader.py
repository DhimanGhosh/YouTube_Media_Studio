"""Replace the media essence of a local file while retaining its metadata."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mutagen import File as MutagenFile

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.core.file_access import retry_file_operation
from youtube_audio_video_downloader.services.media.audio_trimmer import parse_timestamp


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".aiff"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"}


def redownload_media(
    source_path: str | Path,
    youtube_url: str,
    *,
    media_mode: str = "auto",
    start_timestamp: str = "00:00",
    end_timestamp: str = "",
    overwrite_source: bool = False,
    output_path: str | Path | None = None,
    cancellation_token: CancellationToken | None = None,
) -> list[Path]:
    """Download fresh media and publish metadata-preserving replacement(s).

    ``auto`` follows the source file category. ``both`` emits a source-shaped
    primary result plus an MP3 or MP4 companion.
    """

    token = cancellation_token or CancellationToken()
    token.raise_if_cancelled()
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Existing media file does not exist: {source}")
    url = str(youtube_url or "").strip()
    parsed_url = urlparse(url)
    hostname = (parsed_url.hostname or "").lower()
    if parsed_url.scheme not in {"http", "https"} or not (
        hostname == "youtu.be" or hostname == "youtube.com" or hostname.endswith(".youtube.com")
    ):
        raise ValueError("Enter a valid YouTube video link")
    source_kind = _media_kind(source)
    mode = str(media_mode or "auto").strip().lower()
    if mode not in {"auto", "audio", "video", "both"}:
        raise ValueError("Content type must be Automatic, Audio, Video, or Both")
    requested_kinds = (
        [source_kind] if mode == "auto" else (["audio", "video"] if mode == "both" else [mode])
    )

    start = parse_timestamp(start_timestamp or "00:00", label="Start timestamp")
    end = (
        parse_timestamp(end_timestamp, label="End timestamp")
        if str(end_timestamp or "").strip()
        else None
    )
    if end is not None and start >= end:
        raise ValueError("Start timestamp must be earlier than end timestamp")

    _require_binary("ffmpeg")
    results: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="yt_redownload_") as directory:
        work_dir = Path(directory)
        downloaded = _download_source(url, work_dir, token)
        staged: list[tuple[Path, Path, str]] = []
        try:
            for kind in requested_kinds:
                token.raise_if_cancelled()
                destination = _destination_for(
                    source, kind, source_kind, requested_kinds, overwrite_source, output_path
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(
                    f".{destination.stem}.redownload-{uuid.uuid4().hex}{destination.suffix}"
                )
                staged.append((temporary, destination, kind))
                _render_media(downloaded, source, temporary, kind, start, end, token)
                _restore_tag_metadata(source, temporary)
                shutil.copystat(source, temporary)

            # Publish companions/copies first and the original-file replacement last.
            for temporary, destination, kind in sorted(
                staged, key=lambda item: item[1] == source
            ):
                retry_file_operation(
                    destination,
                    "publishing the redownloaded media",
                    lambda temporary=temporary, destination=destination: os.replace(
                        temporary, destination
                    ),
                )
                print(f"[SAVED] Redownloaded {kind} saved to: {destination}")
            results = [destination for _, destination, _ in staged]
        finally:
            for temporary, _, _ in staged:
                if temporary.exists():
                    temporary.unlink()
    return results


def _media_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    raise ValueError(f"Unsupported media file type: {suffix or '(no extension)'}")


def _destination_for(
    source: Path,
    kind: str,
    source_kind: str,
    requested_kinds: list[str],
    overwrite_source: bool,
    output_path: str | Path | None,
) -> Path:
    primary = kind == source_kind
    if primary and overwrite_source:
        return source
    if primary and output_path and str(output_path).strip():
        destination = Path(output_path).expanduser().resolve()
        if destination == source:
            raise ValueError("Choose Replace existing file to overwrite the source")
        if destination.suffix.lower() != source.suffix.lower():
            raise ValueError("The primary copy must use the same extension as the source file")
        if destination.exists():
            raise FileExistsError(f"Output file already exists: {destination}")
        return destination

    if primary:
        return _available_path(source.with_name(f"{source.stem}_redownloaded{source.suffix}"))
    extension = ".mp3" if kind == "audio" else ".mp4"
    label = "audio" if kind == "audio" else "video"
    candidate = source.with_name(f"{source.stem}_redownloaded_{label}{extension}")
    return _available_path(candidate)


def _available_path(candidate: Path) -> Path:
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        alternate = candidate.with_name(f"{candidate.stem}_{counter}{candidate.suffix}")
        if not alternate.exists():
            return alternate
        counter += 1


def _download_source(url: str, work_dir: Path, token: CancellationToken) -> Path:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is required to redownload media") from exc

    def progress_hook(status: dict[str, Any]) -> None:
        token.raise_if_cancelled()
        if status.get("status") == "downloading":
            percent = str(status.get("_percent_str", "")).strip()
            if percent:
                print(f"[DOWNLOAD] {percent}")

    options = {
        "format": "bestvideo*+bestaudio/best",
        "outtmpl": str(work_dir / "download.%(ext)s"),
        "merge_output_format": "mkv",
        "noplaylist": True,
        "overwrites": True,
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": False,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.download([url])
    candidates = [
        path
        for path in work_dir.glob("download.*")
        if path.is_file() and path.suffix not in {".part", ".ytdl"}
    ]
    if not candidates:
        raise RuntimeError("yt-dlp did not create a media file")
    return max(candidates, key=lambda path: path.stat().st_size)


def _render_media(
    downloaded: Path,
    metadata_source: Path,
    destination: Path,
    kind: str,
    start: float,
    end: float | None,
    token: CancellationToken,
) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if start:
        command.extend(["-ss", f"{start:.6f}"])
    command.extend(["-i", str(downloaded), "-i", str(metadata_source)])
    if end is not None:
        command.extend(["-t", f"{end - start:.6f}"])
    if kind == "audio":
        command.extend(["-map", "0:a:0", "-vn"])
        command.extend(_audio_codec_args(destination.suffix.lower()))
    else:
        command.extend(["-map", "0:v:0", "-map", "0:a:0?"])
        command.extend(_video_codec_args(destination.suffix.lower()))
    command.extend(["-map_metadata", "1", "-map_chapters", "1", str(destination)])
    _run_cancellable(command, token)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("FFmpeg did not create a valid replacement file")


def _audio_codec_args(suffix: str) -> list[str]:
    codecs = {
        ".mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
        ".m4a": ["-c:a", "aac", "-b:a", "256k"],
        ".aac": ["-c:a", "aac", "-b:a", "256k"],
        ".flac": ["-c:a", "flac"],
        ".ogg": ["-c:a", "libvorbis", "-q:a", "8"],
        ".opus": ["-c:a", "libopus", "-b:a", "192k"],
        ".wav": ["-c:a", "pcm_s16le"],
        ".aiff": ["-c:a", "pcm_s16be"],
    }
    if suffix not in codecs:
        raise ValueError(f"Redownload is not supported for audio output type {suffix}")
    return codecs[suffix]


def _video_codec_args(suffix: str) -> list[str]:
    if suffix == ".webm":
        return ["-c:v", "libvpx-vp9", "-crf", "24", "-b:v", "0", "-c:a", "libopus", "-b:a", "192k"]
    if suffix == ".avi":
        return ["-c:v", "mpeg4", "-q:v", "2", "-c:a", "libmp3lame", "-b:a", "192k"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k"]


def _run_cancellable(command: list[str], token: CancellationToken) -> None:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        while process.poll() is None:
            if token.is_cancelled():
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                token.raise_if_cancelled()
            time.sleep(0.05)
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            detail = stderr.strip().splitlines()[-1] if stderr.strip() else stdout.strip()
            raise RuntimeError(
                f"FFmpeg could not create the replacement: {detail or 'unknown error'}"
            )
    finally:
        if process.poll() is None:
            process.kill()


def _restore_tag_metadata(source: Path, destination: Path) -> None:
    """Copy Mutagen-supported tags/artwork without making unsupported formats fail."""
    try:
        source_media = MutagenFile(source)
        output_media = MutagenFile(destination)
        if source_media is None or output_media is None or source_media.tags is None:
            return
        if output_media.tags is None:
            output_media.add_tags()
        else:
            output_media.tags.clear()
        for key, value in source_media.tags.items():
            output_media.tags[key] = deepcopy(value)
        pictures = getattr(source_media, "pictures", None)
        if pictures is not None and hasattr(output_media, "clear_pictures"):
            output_media.clear_pictures()
            for picture in pictures:
                output_media.add_picture(deepcopy(picture))
        output_media.save()
    except Exception as exc:  # Container metadata from FFmpeg is still preserved.
        print(f"[METADATA-WARNING] Some format-specific tags could not be restored: {exc}")


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"{name} was not found in PATH. Install FFmpeg and restart the app.")
