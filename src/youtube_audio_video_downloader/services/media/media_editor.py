"""Unified local media editing: tags, trimming, and content redownload."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.core.file_utils import safe_filename
from youtube_audio_video_downloader.core.file_access import retry_file_operation
from youtube_audio_video_downloader.services.media.audio_trimmer import trim_audio
from youtube_audio_video_downloader.services.media.media_metadata import (
    replace_media_metadata,
    write_media_metadata,
)
from youtube_audio_video_downloader.services.downloads.media_redownloader import AUDIO_EXTENSIONS, redownload_media


def edit_media_file(
    source_path: str | Path,
    action: str,
    metadata: dict[str, Any],
    *,
    start_timestamp: str = "00:00",
    end_timestamp: str = "",
    overwrite_source: bool = False,
    output_path: str | Path | None = None,
    youtube_url: str = "",
    media_mode: str = "auto",
    artwork_path: str | Path | None = None,
    remove_artwork: bool = False,
    crop_ratio: str = "Default",
    aspect_ratio: str = "Default",
    cancellation_token: CancellationToken | None = None,
) -> list[Path]:
    """Apply one Edit File action and replace its resulting metadata."""

    token = cancellation_token or CancellationToken()
    token.raise_if_cancelled()
    source = Path(source_path).expanduser().resolve()
    source_stat = source.stat() if source.is_file() else None
    selected_action = str(action or "metadata").strip().lower()
    if selected_action == "metadata":
        result = replace_media_metadata(
            source_path,
            metadata,
            artwork_path=artwork_path,
            remove_artwork=remove_artwork,
        )
        print(f"[SAVED] Metadata updated in: {result}")
        return [_rename_from_metadata(result, metadata)]
    if selected_action == "trim":
        result = trim_audio(
            source_path,
            start_timestamp,
            end_timestamp,
            overwrite_source=overwrite_source,
            output_path=output_path,
            cancellation_token=token,
        )
        token.raise_if_cancelled()
        write_media_metadata(
            result,
            metadata,
            artwork_path=artwork_path,
            remove_artwork=remove_artwork,
        )
        _restore_file_stat(result, source_stat)
        return [_rename_from_metadata(result, metadata)]
    if selected_action == "redownload":
        results = redownload_media(
            source_path,
            youtube_url,
            media_mode=media_mode,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            overwrite_source=overwrite_source,
            output_path=output_path,
            cancellation_token=token,
        )
        for result in results:
            token.raise_if_cancelled()
            write_media_metadata(
                result,
                metadata,
                artwork_path=artwork_path,
                remove_artwork=remove_artwork,
            )
            _restore_file_stat(result, source_stat)
        return [_rename_from_metadata(result, metadata) for result in results]
    if selected_action == "video_display":
        raise ValueError(
            "Video crop/aspect is a Media Library playback profile and cannot "
            "modify the media file"
        )
    raise ValueError(
        "Edit action must be Metadata only, Trim audio, Redownload media, "
        "or a supported non-destructive action"
    )


def _restore_file_stat(path: Path, source_stat: os.stat_result | None) -> None:
    if source_stat is None:
        return
    os.chmod(path, source_stat.st_mode)
    os.utime(path, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))


def _rename_from_metadata(path: Path, metadata: dict[str, Any]) -> Path:
    """Rename edited audio using the project's Title - Album - Artists rule."""

    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        return path
    title = str(metadata.get("title") or "").strip()
    album = str(metadata.get("album") or "").strip()
    raw_artists = metadata.get("artists")
    if isinstance(raw_artists, (list, tuple)):
        artists = ", ".join(str(value).strip() for value in raw_artists if str(value).strip())
    else:
        artists = ", ".join(
            value.strip() for value in str(raw_artists or "").split(",") if value.strip()
        )
    if not title or not album or not artists:
        print("[FILENAME-WARNING] Title, album, and artists are required to rename edited audio")
        return path
    stem = safe_filename(f"{title} - {album} - {artists}", fallback=path.stem)
    desired = path.with_name(f"{stem}{path.suffix}")
    if desired == path:
        return path
    destination = _available_rename_path(desired)
    retry_file_operation(
        path,
        "renaming the edited file",
        lambda: path.rename(destination),
    )
    print(f"[RENAMED] Edited media renamed to: {destination}")
    return destination


def _available_rename_path(desired: Path) -> Path:
    if not desired.exists():
        return desired
    counter = 2
    while True:
        candidate = desired.with_name(f"{desired.stem} ({counter}){desired.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1
