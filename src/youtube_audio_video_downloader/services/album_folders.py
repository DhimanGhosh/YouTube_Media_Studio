"""Repair album directory names that contain storefront release qualifiers."""

from __future__ import annotations

import re
from pathlib import Path

from mutagen import File as MutagenFile, MutagenError

from youtube_audio_video_downloader.core.file_access import (
    FileInUseSkippedError,
    retry_file_operation,
)
from youtube_audio_video_downloader.core.file_utils import safe_filename
from youtube_audio_video_downloader.services.album_names import (
    canonical_album_name,
    normalize_album_name,
    split_album_folder_name,
)
from youtube_audio_video_downloader.services.media_metadata import read_media_metadata


_AUDIO_EXTENSIONS = frozenset(
    {".aac", ".aif", ".aiff", ".ape", ".flac", ".m4a", ".m4b", ".mp3",
     ".oga", ".ogg", ".opus", ".wav", ".wma", ".wv"}
)


def normalize_album_folders(
    root: str | Path, *, media_paths: list[Path] | tuple[Path, ...] | None = None
) -> tuple[Path, ...]:
    """Rename or merge qualified album folders below ``root`` without overwriting."""

    base = Path(root)
    if not base.is_dir():
        return ()
    repaired: list[Path] = []
    eligible = (
        {path.resolve() for path in media_paths}
        if media_paths is not None
        else None
    )
    folder_source = (
        {
            path.parent
            for path in eligible or ()
            if path.parent != base and path.is_relative_to(base)
        }
        if eligible is not None
        else {path for path in base.rglob("*") if path.is_dir() and not path.is_symlink()}
    )
    folders = sorted(
        folder_source,
        key=lambda path: (len(path.parts), str(path).casefold()),
        reverse=True,
    )
    for folder in folders:
        if not folder.exists():
            continue
        album_identity = _folder_album_and_year(folder, eligible)
        if album_identity is not None:
            album, year = album_identity
            simple_name = safe_filename(
                f"{album} ({year})",
                fallback=normalize_album_name(folder.name),
                invalid_char_replacement="",
            )
        else:
            simple_name = normalize_album_name(folder.name)
        if not simple_name or simple_name == folder.name:
            continue
        target = folder.with_name(simple_name)
        if target.exists() and not target.is_dir():
            print(
                f"[FOLDER-NORMALIZE-SKIPPED] {folder}: target is an existing file ({target})"
            )
            continue
        try:
            if target.is_dir():
                _merge_directory(folder, target)
                if folder.exists():
                    print(
                        f"[FOLDER-NORMALIZE-SKIPPED] {folder}: some items could not be moved"
                    )
                    continue
                action = "merged"
            else:
                retry_file_operation(
                    folder,
                    "renaming the album folder",
                    lambda source=folder, destination=target: source.rename(destination),
                )
                action = "renamed"
        except (FileInUseSkippedError, OSError) as exc:
            print(f"[FOLDER-NORMALIZE-SKIPPED] {folder}: {exc}")
            continue
        repaired.append(target)
        print(f"[FOLDER-NORMALIZED] {action.title()} {folder.name} -> {target.name}")
    return tuple(repaired)


def resolve_album_folder_successor(path: str | Path) -> Path:
    """Resolve one stale saved album path to its uniquely renamed canonical folder."""

    requested = Path(path).expanduser()
    if requested.is_dir() or not requested.parent.is_dir():
        return requested
    wanted_album, _wanted_year = split_album_folder_name(requested.name)
    if not wanted_album:
        return requested
    matches = []
    for candidate in requested.parent.iterdir():
        if not candidate.is_dir() or candidate.is_symlink():
            continue
        album, year = split_album_folder_name(candidate.name)
        if album.casefold() == wanted_album.casefold() and year:
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else requested


def find_existing_album_track(
    root: str | Path,
    *,
    title: str,
    album: str,
    year: str = "",
) -> Path | None:
    """Find a canonical existing track before an album job downloads it again."""

    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        return None
    wanted_title = _identity_key(title)
    wanted_album, wanted_year = split_album_folder_name(
        canonical_album_name(album, year)
    )
    folders = [base]
    folders.extend(
        folder
        for folder in base.iterdir()
        if folder.is_dir()
        and split_album_folder_name(folder.name)[0].casefold()
        == wanted_album.casefold()
    )
    for folder in folders:
        for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file() or path.suffix.lower() not in _AUDIO_EXTENSIONS:
                continue
            try:
                metadata = read_media_metadata(path)
            except (MutagenError, OSError, RuntimeError, TypeError, ValueError):
                continue
            if _identity_key(metadata.title) != wanted_title:
                continue
            existing_album, existing_year = split_album_folder_name(
                canonical_album_name(metadata.album, metadata.year)
            )
            if existing_album.casefold() != wanted_album.casefold():
                continue
            if wanted_year and existing_year and wanted_year != existing_year:
                continue
            return path
    return None


def consolidate_audio_in_place(
    root: str | Path,
    *,
    media_paths: list[Path] | tuple[Path, ...] | None = None,
    retries: int = 3,
) -> tuple[Path, ...]:
    """Move tagged audio into canonical album folders and remove true duplicates.

    When ``media_paths`` is supplied, consolidation is restricted to the albums
    represented by those files. This keeps an automatic post-download pass from
    reorganizing unrelated parts of a user's library.
    """

    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        return ()
    all_candidates = sorted(
        (
            path for path in base.rglob("*")
            if path.is_file() and path.suffix.lower() in _AUDIO_EXTENSIONS
        ),
        key=lambda path: str(path).casefold(),
    )
    metadata_cache: dict[Path, object] = {}
    if media_paths is None:
        candidates = all_candidates
    else:
        seeds = {
            path.expanduser().resolve()
            for path in media_paths
            if path.expanduser().resolve().is_file()
            and path.expanduser().resolve().is_relative_to(base)
        }
        wanted_albums: set[str] = set()
        for path in seeds:
            try:
                metadata = read_media_metadata(path)
            except (MutagenError, OSError, RuntimeError, TypeError, ValueError):
                continue
            metadata_cache[path] = metadata
            album = canonical_album_name(metadata.album, metadata.year)
            if album:
                wanted_albums.add(_identity_key(album))
        candidates = []
        for path in all_candidates:
            if path in seeds:
                candidates.append(path)
                continue
            try:
                metadata = read_media_metadata(path)
            except (MutagenError, OSError, RuntimeError, TypeError, ValueError):
                continue
            metadata_cache[path] = metadata
            album = canonical_album_name(metadata.album, metadata.year)
            if _identity_key(album) in wanted_albums:
                candidates.append(path)
    final_paths: list[Path] = []
    identities: dict[tuple[str, str, tuple[str, ...]], list[Path]] = {}
    for path in candidates:
        if not path.exists():
            continue
        try:
            metadata = metadata_cache.get(path) or read_media_metadata(path)
        except (MutagenError, OSError, RuntimeError, TypeError, ValueError):
            continue
        album = canonical_album_name(metadata.album, metadata.year)
        title = str(metadata.title or "").strip()
        artists = str(metadata.artists or "").strip()
        if not album or not title or not artists:
            continue
        artist_key = tuple(sorted(
            _identity_key(part)
            for part in re.split(r"\s*(?:,|;|/|\s+&\s+)\s*", artists)
            if _identity_key(part)
        ))
        identity = (_identity_key(album), _identity_key(title), artist_key)
        duplicates = identities.setdefault(identity, [])
        existing = next(
            (
                candidate for candidate in duplicates
                if candidate.exists() and _durations_match(candidate, path)
            ),
            None,
        )
        if existing is not None:
            if _quality_score(path) > _quality_score(existing):
                retry_file_operation(
                    existing,
                    "removing the lower-quality consolidated duplicate",
                    existing.unlink,
                )
                duplicates.remove(existing)
                print(f"[AUTO-CONSOLIDATE-REPLACED] {existing.name}")
            else:
                retry_file_operation(
                    path,
                    "removing the consolidated duplicate",
                    path.unlink,
                )
                print(
                    f"[AUTO-CONSOLIDATE-DUPLICATE] Removed {path.name}; "
                    f"kept {existing.name}"
                )
                continue
        target_folder = base / safe_filename(album, fallback="Unknown Album")
        stem = safe_filename(f"{title} - {album} - {artists}", fallback=path.stem)
        target = target_folder / f"{stem}{path.suffix.lower()}"
        if path.parent == target_folder and target.exists():
            # A deliberately preserved alternate version may already have a
            # numbered name. Leave it stable when duration evidence is not
            # sufficient to call it a duplicate.
            target = path
        if path != target:
            target_folder.mkdir(parents=True, exist_ok=True)
            if target.exists() and target != existing:
                target = _numbered_path(target)
            original_parent = path.parent
            retry_file_operation(
                path,
                "auto-consolidating the downloaded song",
                lambda source=path, destination=target: source.rename(destination),
            )
            print(f"[AUTO-CONSOLIDATED] {path.name} -> {target.parent.name}/{target.name}")
            path = target
            _remove_empty_parents(original_parent, base, retries)
        duplicates.append(path)
        final_paths.append(path)
    return tuple(dict.fromkeys(final_paths))


def _identity_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _duration(path: Path) -> float:
    try:
        media = MutagenFile(path)
        return float(getattr(getattr(media, "info", None), "length", 0.0) or 0.0)
    except (MutagenError, OSError, TypeError, ValueError):
        return 0.0


def _durations_match(left: Path, right: Path) -> bool:
    left_duration, right_duration = _duration(left), _duration(right)
    if left_duration <= 0 or right_duration <= 0:
        return False
    tolerance = max(20.0, max(left_duration, right_duration) * 0.08)
    return abs(left_duration - right_duration) <= tolerance


def _quality_score(path: Path) -> tuple[int, int, int]:
    bitrate = 0
    try:
        media = MutagenFile(path)
        bitrate = int(getattr(getattr(media, "info", None), "bitrate", 0) or 0)
    except (MutagenError, OSError, TypeError, ValueError):
        pass
    try:
        artwork = int(read_media_metadata(path).artwork_present)
        size = path.stat().st_size
    except (MutagenError, OSError, RuntimeError, TypeError, ValueError):
        artwork, size = 0, 0
    return bitrate, artwork, size


def _remove_empty_parents(path: Path, root: Path, retries: int) -> None:
    current = path
    while current != root and current.is_relative_to(root):
        try:
            if any(current.iterdir()):
                return
            retry_file_operation(
                current,
                "removing the empty pre-consolidation folder",
                current.rmdir,
            )
        except (FileInUseSkippedError, OSError):
            return
        current = current.parent


def _folder_album_and_year(
    folder: Path, eligible: set[Path] | None = None
) -> tuple[str, str] | None:
    """Infer one unambiguous album/year pair from audio files directly in a folder."""

    albums: dict[str, str] = {}
    years: set[str] = set()
    for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in _AUDIO_EXTENSIONS:
            continue
        if eligible is not None and path.resolve() not in eligible:
            continue
        try:
            metadata = read_media_metadata(path)
        except Exception:  # Mutagen format readers expose container-specific errors.
            continue
        album, _ = split_album_folder_name(metadata.album)
        year_match = re.search(r"\b((?:19|20)\d{2})\b", str(metadata.year or ""))
        if album:
            albums.setdefault(album.casefold(), album)
        if year_match:
            years.add(year_match.group(1))
    if len(albums) != 1 or len(years) != 1:
        return None
    return next(iter(albums.values())), next(iter(years))


def _merge_directory(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir(), key=lambda path: path.name.casefold()):
        target = destination / child.name
        if child.is_dir() and not child.is_symlink() and target.is_dir():
            _merge_directory(child, target)
            continue
        if target.exists():
            target = _numbered_path(target)
        try:
            retry_file_operation(
                child,
                "merging the old album folder",
                lambda item=child, new_path=target: item.rename(new_path),
            )
        except (FileInUseSkippedError, OSError) as exc:
            print(f"[FOLDER-NORMALIZE-SKIPPED] {child}: {exc}")
    if not any(source.iterdir()):
        try:
            retry_file_operation(
                source,
                "removing the old album folder",
                source.rmdir,
            )
        except (FileInUseSkippedError, OSError) as exc:
            print(f"[FOLDER-NORMALIZE-SKIPPED] {source}: {exc}")


def _numbered_path(path: Path) -> Path:
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1
