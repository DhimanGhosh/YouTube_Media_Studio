"""Losslessly trim an existing audio file while retaining its metadata streams."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import time
import uuid
from copy import deepcopy
from pathlib import Path

from mutagen import File as MutagenFile

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.core.file_access import retry_file_operation


def parse_timestamp(value: str, *, label: str = "Timestamp") -> float:
    """Parse ``SS``, ``MM:SS``, or ``HH:MM:SS`` into seconds."""

    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} is required")
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"{label} must use SS, MM:SS, or HH:MM:SS")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"{label} contains a non-numeric value: {text!r}") from exc
    if any(not math.isfinite(number) for number in numbers):
        raise ValueError(f"{label} must contain finite numbers")
    if any(number < 0 for number in numbers):
        raise ValueError(f"{label} cannot be negative")
    if len(numbers) > 1 and any(number >= 60 for number in numbers[1:]):
        raise ValueError(f"{label} minutes and seconds must be below 60")
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def format_timestamp(seconds: float) -> str:
    """Format seconds as an editable timestamp without losing milliseconds."""

    milliseconds = max(0, round(float(seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    base = f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"
    return f"{base}.{millis:03d}" if millis else base


def probe_audio_duration(path: str | Path) -> float:
    """Read an audio file's duration with FFprobe."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Audio file does not exist: {source}")
    _require_binary("ffprobe")
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "FFprobe could not read the file"
        raise ValueError(f"Unable to read audio duration: {detail}")
    try:
        duration = float(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise ValueError("FFprobe returned an invalid audio duration") from exc
    if duration <= 0:
        raise ValueError("Audio duration must be greater than zero")
    return duration


def trim_audio(
    source_path: str | Path,
    start_timestamp: str,
    end_timestamp: str,
    *,
    overwrite_source: bool = False,
    output_path: str | Path | None = None,
    cancellation_token: CancellationToken | None = None,
) -> Path:
    """Trim audio with stream copy and atomically publish the resulting file."""

    token = cancellation_token or CancellationToken()
    token.raise_if_cancelled()
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Audio file does not exist: {source}")
    _require_binary("ffmpeg")

    duration = probe_audio_duration(source)
    start = parse_timestamp(start_timestamp, label="Start timestamp")
    end = parse_timestamp(end_timestamp, label="End timestamp")
    tolerance = 0.05
    if start >= end:
        raise ValueError("Start timestamp must be earlier than end timestamp")
    if start > duration + tolerance:
        raise ValueError(f"Start timestamp exceeds track length ({format_timestamp(duration)})")
    if end > duration + tolerance:
        raise ValueError(f"End timestamp exceeds track length ({format_timestamp(duration)})")
    end = min(end, duration)

    if overwrite_source:
        destination = source
    elif output_path and str(output_path).strip():
        destination = Path(output_path).expanduser().resolve()
        if destination == source:
            raise ValueError("Choose Overwrite existing file to replace the source audio")
    else:
        destination = _available_copy_path(source)
    if destination.suffix.lower() != source.suffix.lower():
        raise ValueError("The copy must use the same file extension as the source")
    if destination.exists() and destination != source:
        raise FileExistsError(f"Output file already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    temporary = destination.with_name(
        f".{destination.stem}.trim-{uuid.uuid4().hex}{destination.suffix}"
    )
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-ss",
        f"{start:.6f}",
        "-t",
        f"{end - start:.6f}",
        "-map",
        "0",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-c",
        "copy",
        str(temporary),
    ]
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
            raise RuntimeError(f"FFmpeg could not trim the audio: {detail or 'unknown error'}")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not create a valid trimmed audio file")
        _restore_tag_metadata(source, temporary)
        shutil.copystat(source, temporary)
        retry_file_operation(
            destination,
            "replacing it with the trimmed audio",
            lambda: os.replace(temporary, destination),
        )
    finally:
        if process is not None and process.poll() is None:
            process.kill()
        if temporary.exists():
            temporary.unlink()

    print(f"[SAVED] Trimmed audio saved to: {destination}")
    return destination


def _available_copy_path(source: Path) -> Path:
    candidate = source.with_name(f"{source.stem}_trimmed{source.suffix}")
    counter = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem}_trimmed_{counter}{source.suffix}")
        counter += 1
    return candidate


def _restore_tag_metadata(source: Path, trimmed: Path) -> None:
    """Restore format-specific tags that FFmpeg may otherwise normalize or omit."""

    source_audio = MutagenFile(source)
    if source_audio is None or source_audio.tags is None:
        return
    trimmed_audio = MutagenFile(trimmed)
    if trimmed_audio is None:
        raise RuntimeError("Unable to restore metadata on the trimmed audio file")
    if trimmed_audio.tags is None:
        trimmed_audio.add_tags()
    else:
        trimmed_audio.tags.clear()
    for key, value in source_audio.tags.items():
        trimmed_audio.tags[key] = deepcopy(value)

    source_pictures = getattr(source_audio, "pictures", None)
    if source_pictures is not None and hasattr(trimmed_audio, "clear_pictures"):
        trimmed_audio.clear_pictures()
        for picture in source_pictures:
            trimmed_audio.add_picture(deepcopy(picture))
    trimmed_audio.save()


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"{name} was not found in PATH. Install FFmpeg and restart the app.")
