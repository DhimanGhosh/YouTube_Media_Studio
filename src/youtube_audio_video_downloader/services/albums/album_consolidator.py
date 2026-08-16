"""Move tagged media into album-named folders under one destination."""

from __future__ import annotations

import json
import errno
import hashlib
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from mutagen import File as MutagenFile, MutagenError

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.core.file_utils import safe_filename
from youtube_audio_video_downloader.core.file_access import (
    FileInUseSkippedError,
    retry_file_operation,
)
from youtube_audio_video_downloader.loaders.json_loader import parse_file_name_metadata
from youtube_audio_video_downloader.services.media.media_metadata import (
    EditableMediaMetadata,
    read_media_metadata,
    replace_media_metadata,
)
from youtube_audio_video_downloader.services.albums.album_names import (
    canonical_album_name,
    normalize_album_name,
    split_album_folder_name,
)
from youtube_audio_video_downloader.services.albums.album_folders import (
    normalize_album_folders,
    resolve_album_folder_successor,
)
from youtube_audio_video_downloader.services.albums.track_reorder import (
    SUPPORTED_TRACK_EXTENSIONS,
    reorder_track_numbers,
)
from youtube_audio_video_downloader.services.albums.wikipedia_tracks import find_wikipedia_tracks


SUPPORTED_AUDIO_EXTENSIONS = frozenset(
    {
        ".aac", ".aif", ".aiff", ".ape", ".flac", ".m4a", ".m4b", ".mp3",
        ".oga", ".ogg", ".opus", ".wav", ".wma", ".wv",
    }
)
SUPPORTED_VIDEO_EXTENSIONS = frozenset(
    {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".ts", ".webm"}
)
SUPPORTED_MEDIA_EXTENSIONS = SUPPORTED_AUDIO_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS


@dataclass(frozen=True, slots=True)
class ConsolidationReport:
    """Result of one recursive album consolidation scan."""

    scanned: int
    moved: tuple[Path, ...]
    skipped: tuple[str, ...]
    deleted: tuple[Path, ...] = ()
    tagged: int = 0
    reordered: int = 0
    repaired_folders: tuple[Path, ...] = ()


def consolidate_albums(
    source_folder: str | Path,
    destination_folder: str | Path,
    *,
    retries: int = 3,
    verified_audio_paths: Iterable[str | Path] | None = None,
    cancellation_token: CancellationToken | None = None,
) -> ConsolidationReport:
    """Move readable media to ``destination/<album tag>/<original name>``.

    When ``verified_audio_paths`` is supplied, audio not approved by the
    metadata verifier remains in the source tree for manual review. Videos are
    unaffected because the song verifier does not establish video identity.
    """

    token = cancellation_token or CancellationToken()
    requested_source = Path(source_folder).expanduser().resolve()
    source = resolve_album_folder_successor(requested_source).resolve()
    destination = Path(destination_folder).expanduser().resolve()
    verified_audio = (
        None
        if verified_audio_paths is None
        else {
            Path(value).expanduser().resolve()
            for value in verified_audio_paths
        }
    )
    if not source.is_dir():
        raise NotADirectoryError(f"Source folder does not exist: {source}")
    if source == destination:
        raise ValueError("Source and destination folders must be different")
    if destination.is_relative_to(source):
        raise ValueError("Destination folder cannot be inside the source folder")

    token.raise_if_cancelled()
    repaired_folders = normalize_album_folders(destination)
    candidates = sorted(
        (
            path for path in source.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in SUPPORTED_MEDIA_EXTENSIONS
        ),
        key=lambda path: str(path).casefold(),
    )
    if not candidates:
        raise ValueError("No supported audio or video files were found in the source folder")

    planned: list[tuple[Path, Path, str]] = []
    moved: list[Path] = []
    deleted: list[Path] = []
    skipped: list[str] = []
    tagged = 0
    touched_albums: dict[str, tuple[str, Path]] = {}
    destination_titles: dict[str, dict[str, Path]] = {}
    for media_path in candidates:
        token.raise_if_cancelled()
        media_path = _follow_renamed_source_file(media_path, source)
        if not media_path.is_file():
            message = (
                f"{media_path.name}: source changed during consolidation and "
                "the renamed file could not be located"
            )
            skipped.append(message)
            print(f"[SKIPPED] {message}")
            continue
        if (
            verified_audio is not None
            and media_path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
            and media_path.resolve() not in verified_audio
        ):
            message = (
                f"{media_path.name}: agentic metadata verification is incomplete; "
                "left in source for review"
            )
            skipped.append(message)
            print(f"[AGENT-REVIEW] {message}")
            continue
        if "unknown" in media_path.name.casefold():
            message = f"{media_path.name}: filename contains Unknown"
            skipped.append(message)
            print(f"[SKIPPED] {message}")
            continue
        try:
            title, album, artists, was_tagged, album_is_embedded = (
                _read_album_or_tag_from_filename(media_path)
            )
            tagged += int(was_tagged)
        except (OSError, RuntimeError, ValueError) as exc:
            message = f"{media_path.name}: metadata could not be read ({exc})"
            skipped.append(message)
            print(f"[SKIPPED] {message}")
            continue
        if not album or album.casefold() == "unknown":
            reason = "empty" if not album else "Unknown"
            message = f"{media_path.name}: Album metadata is {reason}"
            skipped.append(message)
            print(f"[SKIPPED] {message}")
            continue
        if album_contains_artist(album, artists):
            if album_is_embedded:
                try:
                    replace_media_metadata(media_path, {"album": ""})
                except (OSError, RuntimeError, ValueError) as exc:
                    message = (
                        f"{media_path.name}: invalid Album metadata contains a credited "
                        f"artist name but could not be removed ({exc})"
                    )
                    skipped.append(message)
                    print(f"[SKIPPED] {message}")
                    continue
                print(f"[UNTAGGED] Removed invalid Album metadata: {media_path.name}")
            message = (
                f"{media_path.name}: Album metadata contains a credited artist name"
                + (" and was removed" if album_is_embedded else "")
            )
            skipped.append(message)
            print(f"[SKIPPED] {message}")
            continue
        album_folder = safe_filename(album, fallback="Unknown Album")
        target = destination / album_folder / media_path.name
        folder_key = str(target.parent).casefold()
        if folder_key not in destination_titles:
            destination_titles[folder_key] = _read_destination_titles(target.parent)
        title_index = destination_titles[folder_key]
        normalized_title = _title_key(title)
        existing_title_path = title_index.get(normalized_title) if normalized_title else None
        if media_path.resolve() == target.resolve():
            moved.append(media_path)
            touched_albums[album.casefold()] = (album, media_path.parent)
            print(f"[ALREADY-ORGANIZED] {media_path.name} is already in {album}")
            continue
        if existing_title_path is not None:
            try:
                retry_file_operation(
                    media_path,
                    "deleting the duplicate",
                    lambda: _unlink_with_retry(media_path),
                )
            except OSError as exc:
                message = (
                    f"{media_path.name}: title already exists as {existing_title_path.name}, "
                    f"but the source could not be deleted ({exc})"
                )
                skipped.append(message)
                print(f"[SKIPPED] {message}")
                continue
            deleted.append(media_path)
            print(
                f"[DELETED-DUPLICATE] {media_path.name}: title already exists as "
                f"{existing_title_path.name} in {target.parent}"
            )
            continue
        if target.exists():
            if _files_identical(media_path, target):
                try:
                    retry_file_operation(
                        media_path, "deleting the duplicate", lambda: _unlink_with_retry(media_path)
                    )
                except OSError as exc:
                    message = (
                        f"{media_path.name}: identical destination exists but the source "
                        f"could not be removed ({exc})"
                    )
                    skipped.append(message)
                    print(f"[SKIPPED] {message}")
                    continue
                moved.append(target)
                touched_albums[album.casefold()] = (album, target.parent)
                print(f"[MOVED] Removed identical source duplicate: {media_path.name}")
                continue
            message = f"{media_path.name}: destination already exists at {target}"
            skipped.append(message)
            print(f"[SKIPPED] {message}")
            continue
        planned.append((media_path, target, album))

    for media_path, target, album in planned:
        token.raise_if_cancelled()
        media_path = _follow_renamed_source_file(media_path, source)
        if not media_path.is_file():
            message = (
                f"{media_path.name}: source changed before moving and the "
                "renamed file could not be located"
            )
            skipped.append(message)
            print(f"[SKIPPED] {message}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            retry_file_operation(
                media_path,
                "moving it into the album folder",
                lambda: _transactional_move(media_path, target),
            )
        except FileInUseSkippedError as exc:
            message = str(exc)
            skipped.append(message)
            print(f"[SKIPPED] {message}")
            continue
        except OSError as exc:
            raise OSError(f"Could not move {media_path} to {target}: {exc}") from exc
        moved.append(target)
        touched_albums[album.casefold()] = (album, target.parent)
        print(f"[MOVED] {media_path.name} -> {album}/{target.name}")

    reordered = 0
    for album, album_folder in touched_albums.values():
        token.raise_if_cancelled()
        try:
            reordered += _reorder_album_from_wikipedia(
                album_folder, album, retries=retries
            )
        except (LookupError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
            print(f"[REORDER-SKIPPED] {album}: {exc}")

    return ConsolidationReport(
        scanned=len(candidates),
        moved=tuple(moved),
        skipped=tuple(skipped),
        deleted=tuple(deleted),
        tagged=tagged,
        reordered=reordered,
        repaired_folders=repaired_folders,
    )


def _follow_renamed_source_file(path: Path, scan_root: Path) -> Path:
    """Follow a source album folder when enrichment adds its release year."""

    if path.is_file():
        return path
    successor = resolve_album_folder_successor(scan_root)
    if successor == scan_root or not successor.is_dir():
        return path
    try:
        relative = path.relative_to(scan_root)
    except ValueError:
        relative = Path(path.name)
    candidate = successor / relative
    if candidate.is_file():
        return candidate
    matches = list(successor.rglob(path.name))
    return matches[0] if len(matches) == 1 else path


def _read_album_or_tag_from_filename(path: Path) -> tuple[str, str, str, bool, bool]:
    metadata_error: Exception | None = None
    metadata: EditableMediaMetadata | None = None
    try:
        metadata = read_media_metadata(path)
        album = metadata.album.strip()
        if album:
            normalized_album = canonical_album_name(album, metadata.year)
            was_normalized = normalized_album != album
            if was_normalized:
                replace_media_metadata(path, {"album": normalized_album})
                print(
                    f"[NORMALIZED] Removed soundtrack/EP suffix from Album metadata: "
                    f"{path.name}"
                )
            return (
                _metadata_or_structured_title(path, metadata.title),
                normalized_album,
                metadata.artists.strip(),
                was_normalized,
                True,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        metadata_error = exc
    album = _probe_album(path)
    if album:
        album = normalize_album_name(album)
        return (
            _metadata_or_structured_title(path, metadata.title) if metadata is not None else "",
            album,
            metadata.artists.strip() if metadata is not None else "",
            False,
            True,
        )
    if metadata is not None and _core_song_metadata_is_empty(metadata):
        try:
            parsed = parse_file_name_metadata(path.stem)
        except ValueError:
            pass
        else:
            parsed_artists = ", ".join(parsed.artists)
            if album_contains_artist(parsed.album, parsed_artists):
                return parsed.title, parsed.album, parsed_artists, False, False
            replace_media_metadata(
                path,
                {
                    "title": parsed.title,
                    "album": parsed.album,
                    "artists": parsed.artists,
                },
            )
            print(f"[TAGGED] Added metadata from filename: {path.name}")
            return parsed.title, parsed.album, parsed_artists, True, True
    if metadata_error is not None:
        raise ValueError(str(metadata_error)) from metadata_error
    return "", "", "", False, False


def _read_destination_titles(folder: Path) -> dict[str, Path]:
    """Index readable embedded titles already present in one album folder."""

    if not folder.is_dir():
        return {}
    found: dict[str, Path] = {}
    for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
            continue
        try:
            title = _metadata_or_structured_title(path, read_media_metadata(path).title)
        except (OSError, RuntimeError, ValueError):
            continue
        title_key = _title_key(title)
        if title_key:
            found.setdefault(title_key, path)
    return found


def _metadata_or_structured_title(path: Path, metadata_title: object) -> str:
    title = str(metadata_title or "").strip()
    if title:
        return title
    try:
        return parse_file_name_metadata(path.stem).title
    except ValueError:
        return ""


def album_contains_artist(album: object, artists: object) -> bool:
    """Return whether an album tag suspiciously contains a full credited artist."""

    album_key = _title_key(album)
    if not album_key:
        return False
    artist_text = str(artists or "")
    credits = re.split(r"\s*(?:,|&|\band\b|\bfeat\.?\b|\bfeaturing\b)\s*", artist_text, flags=re.I)
    for credit in credits:
        credit_key = _title_key(credit)
        if credit_key and credit_key != "unknown" and len(credit_key) >= 3:
            if credit_key in album_key:
                if _is_year_qualified_artist_collection(album, credit_key):
                    continue
                return True
    return False


def _is_year_qualified_artist_collection(album: object, artist_key: str) -> bool:
    """Recognize legitimate titles such as ``Hits of Kumar Sanu (1995)``."""

    album_name, album_year = split_album_folder_name(album)
    if not album_year:
        return False
    name_key = _title_key(album_name)
    collection_prefix = (
        r"(?:hits?|best|essential|collection|classics?|golden|songs?)\s+"
        r"(?:of|by)\s+"
    )
    return bool(
        re.fullmatch(collection_prefix + re.escape(artist_key), name_key, flags=re.I)
    )


def _core_song_metadata_is_empty(metadata: EditableMediaMetadata) -> bool:
    return not any((metadata.title.strip(), metadata.album.strip(), metadata.artists.strip()))


def _probe_album(path: Path) -> str:
    """Use FFprobe for containers that Mutagen cannot inspect, such as MKV/WebM."""

    if shutil.which("ffprobe") is None:
        return ""
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format_tags=album:stream_tags=album", "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ""
    tag_groups = [payload.get("format", {}).get("tags", {})]
    tag_groups.extend(stream.get("tags", {}) for stream in payload.get("streams", []))
    for tags in tag_groups:
        if not isinstance(tags, dict):
            continue
        for key, value in tags.items():
            if str(key).casefold() == "album" and str(value).strip():
                return str(value).strip()
    return ""


def _transactional_move(source: Path, target: Path) -> None:
    """Move without leaving a visible destination copy when source deletion fails."""

    last_error: OSError | None = None
    for attempt in range(6):
        try:
            source.rename(target)
            return
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                last_error = exc
                break
            last_error = exc
            if attempt < 5:
                time.sleep(0.2)
    if last_error is None or last_error.errno != errno.EXDEV:
        raise last_error or OSError(f"Could not move {source}")

    temporary = target.with_name(f".{target.name}.consolidating-{uuid.uuid4().hex}")
    try:
        shutil.copy2(source, temporary)
        if not _files_identical(source, temporary):
            raise OSError(f"Cross-volume copy verification failed for {source.name}")
        _unlink_with_retry(source)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _unlink_with_retry(path: Path) -> None:
    last_error: OSError | None = None
    for attempt in range(6):
        try:
            path.unlink()
            return
        except OSError as exc:
            last_error = exc
            if attempt < 5:
                time.sleep(0.2)
    raise last_error or OSError(f"Could not remove {path}")


def _files_identical(first: Path, second: Path) -> bool:
    try:
        if first.stat().st_size != second.stat().st_size:
            return False
        return _sha256(first) == _sha256(second)
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reorder_album_from_wikipedia(
    folder: Path, album: str, *, retries: int = 3
) -> int:
    """Apply contiguous numbering to the Wikipedia-matched subset in an album folder."""

    album_base, album_year = split_album_folder_name(album)
    candidates = sorted(
        (
            path for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_TRACK_EXTENSIONS
        ),
        key=lambda path: path.name.casefold(),
    )
    local_tracks: list[tuple[Path, EditableMediaMetadata]] = []
    release_year = ""
    for path in candidates:
        try:
            metadata = read_media_metadata(path)
        except (OSError, RuntimeError, ValueError):
            continue
        if not metadata.title.strip():
            continue
        metadata_album, _metadata_album_year = split_album_folder_name(metadata.album)
        if metadata_album.casefold() != album_base.casefold():
            continue
        local_tracks.append((path, metadata))
        release_year = release_year or metadata.year.strip()
    if not local_tracks:
        return 0
    wikipedia_tracks = find_wikipedia_tracks(album_base, release_year or album_year)
    if not wikipedia_tracks:
        print(f"[REORDER-SKIPPED] {album}: no verified Wikipedia track table found")
        return 0
    matched = sorted(
        (
            (track_index, path)
            for path, metadata in local_tracks
            if (
                track_index := _wikipedia_track_index(
                    metadata.title, path, wikipedia_tracks
                )
            ) is not None
        ),
        key=lambda item: (item[0], item[1].name.casefold()),
    )
    paths = [path for _, path in matched]
    if not paths:
        print(f"[REORDER-SKIPPED] {album}: no local titles matched Wikipedia exactly")
        return 0
    matched_set = set(paths)
    unmatched = sorted(
        (path for path, _metadata in local_tracks if path not in matched_set),
        key=lambda path: path.name.casefold(),
    )
    paths.extend(unmatched)
    updated = reorder_track_numbers(paths, retries=retries, normalize_total=True)
    if updated != len(paths):
        raise OSError(
            f"Wikipedia ordering updated only {updated} of {len(paths)} local track(s)"
        )
    print(f"[REORDERED-SUMMARY] {album}: assigned contiguous numbers to {updated} track(s)")
    return updated


def _title_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _wikipedia_track_index(
    title: str, path: Path, tracks: list[dict[str, object]]
) -> int | None:
    """Resolve canonical aliases and duration-confirmed typos for ordering."""

    wanted = _reorder_title_key(title)
    exact = [
        index for index, track in enumerate(tracks)
        if _reorder_title_key(track.get("title")) == wanted
    ]
    if len(exact) == 1:
        return exact[0]
    ranked = sorted(
        [
            (
                SequenceMatcher(
                    None, wanted, _reorder_title_key(track.get("title"))
                ).ratio(),
                index,
                track,
            )
            for index, track in enumerate(tracks)
            if _reorder_title_key(track.get("title"))
        ],
        key=lambda item: item[0],
    )
    if not ranked:
        return None
    best_score, best_index, best = ranked[-1]
    runner_up = ranked[-2][0] if len(ranked) > 1 else 0.0
    if best_score < 0.84 or best_score - runner_up < 0.08:
        return None
    try:
        expected_duration = float(best.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        return None
    actual_duration = _track_duration(path)
    tolerance = max(10.0, max(actual_duration, expected_duration) * 0.05)
    if actual_duration <= 0 or expected_duration <= 0:
        return None
    return best_index if abs(actual_duration - expected_duration) <= tolerance else None


def _reorder_title_key(value: object) -> str:
    key = _title_key(value)
    key = re.sub(r"^i am\b", "i m", key)
    key = re.sub(r"\b(male|female) version$", r"\1", key)
    return " ".join(key.split())


def _track_duration(path: Path) -> float:
    try:
        media = MutagenFile(path)
        return float(getattr(getattr(media, "info", None), "length", 0.0) or 0.0)
    except (MutagenError, OSError, TypeError, ValueError):
        return 0.0
