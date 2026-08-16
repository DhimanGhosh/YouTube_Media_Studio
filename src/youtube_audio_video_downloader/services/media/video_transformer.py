"""Permanent, safely published crop and aspect transforms for local videos."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.core.file_access import retry_file_operation


VIDEO_ASPECT_OPTIONS: tuple[str, ...] = (
    "Default", "16:9", "4:3", "1:1", "16:10", "2.21:1", "2.35:1", "2.39:1", "5:4",
)
VIDEO_CROP_OPTIONS: tuple[str, ...] = (
    "Default", "16:10", "16:9", "4:3", "1.85:1", "2.21:1", "2.35:1", "2.39:1",
    "5:3", "5:4", "1:1",
)
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"})

_RATIO_EXPRESSIONS = {
    "16:9": "16/9",
    "4:3": "4/3",
    "1:1": "1",
    "16:10": "16/10",
    "1.85:1": "1.85",
    "2.21:1": "2.21",
    "2.35:1": "2.35",
    "2.39:1": "2.39",
    "5:3": "5/3",
    "5:4": "5/4",
}


def permanently_transform_video(
    source_path: str | Path,
    *,
    crop_ratio: str = "Default",
    aspect_ratio: str = "Default",
    cancellation_token: CancellationToken | None = None,
) -> Path:
    """Re-encode one video with the selected centered crop and output aspect."""

    token = cancellation_token or CancellationToken()
    token.raise_if_cancelled()
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Video file does not exist: {source}")
    if source.suffix.casefold() not in VIDEO_EXTENSIONS:
        raise ValueError("Permanent crop/aspect editing requires a supported video file")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg was not found in PATH. Install FFmpeg and restart the app.")

    video_filter = build_video_transform_filter(crop_ratio, aspect_ratio)
    temporary = source.with_name(
        f".{source.stem}.display-{uuid.uuid4().hex}{source.suffix}"
    )
    command = _ffmpeg_command(source, temporary, video_filter)
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
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
                f"FFmpeg could not apply the permanent video display edit: "
                f"{detail or 'unknown error'}"
            )
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not create a valid transformed video file")
        shutil.copystat(source, temporary)
        retry_file_operation(
            source,
            "replacing it with the permanently cropped video",
            lambda: os.replace(temporary, source),
        )
    finally:
        if process is not None and process.poll() is None:
            process.kill()
        if temporary.exists():
            temporary.unlink()

    print(
        f"[SAVED] Permanent video display edit applied: {source} "
        f"(crop={crop_ratio}, aspect={aspect_ratio})"
    )
    return source


def build_video_transform_filter(crop_ratio: str, aspect_ratio: str) -> str:
    """Build an FFmpeg filter that bakes the selected player modes into pixels."""

    crop = str(crop_ratio or "Default").strip()
    aspect = str(aspect_ratio or "Default").strip()
    if crop not in VIDEO_CROP_OPTIONS:
        raise ValueError(f"Unsupported crop ratio: {crop}")
    if aspect not in VIDEO_ASPECT_OPTIONS:
        raise ValueError(f"Unsupported aspect ratio: {aspect}")
    if crop == "Default" and aspect == "Default":
        raise ValueError("Choose a crop ratio, an aspect ratio, or both")

    filters: list[str] = []
    if crop != "Default":
        ratio = _RATIO_EXPRESSIONS[crop]
        filters.append(
            "crop="
            f"w=trunc(if(gt(a\\,{ratio})\\,ih*{ratio}\\,iw)/2)*2:"
            f"h=trunc(if(gt(a\\,{ratio})\\,ih\\,iw/{ratio})/2)*2"
        )
    if aspect != "Default":
        ratio = _RATIO_EXPRESSIONS[aspect]
        filters.extend(
            (
                f"scale=w=trunc(ih*{ratio}/2)*2:h=trunc(ih/2)*2",
                "setsar=1",
            )
        )
    return ",".join(filters)


def _ffmpeg_command(source: Path, destination: Path, video_filter: str) -> list[str]:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(source), "-map", "0", "-map_metadata", "0", "-map_chapters", "0",
        "-c", "copy", "-filter:v:0", video_filter,
    ]
    if source.suffix.casefold() == ".webm":
        command.extend(("-c:v:0", "libvpx-vp9", "-crf", "28", "-b:v", "0"))
    else:
        command.extend(("-c:v:0", "libx264", "-preset", "medium", "-crf", "18"))
    if source.suffix.casefold() in {".mp4", ".m4v", ".mov"}:
        command.extend(("-movflags", "+faststart"))
    command.append(str(destination))
    return command
