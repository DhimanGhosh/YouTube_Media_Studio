"""Parallel, evidence-backed metadata enrichment for a local media folder."""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from mutagen import File as MutagenFile, MutagenError

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.config.settings import MAX_PARALLEL_WORKERS
from youtube_audio_video_downloader.core.file_access import (
    FileInUseSkippedError,
    retry_file_operation,
)
from youtube_audio_video_downloader.core.file_utils import safe_filename
from youtube_audio_video_downloader.loaders.json_loader import parse_file_name_metadata
from youtube_audio_video_downloader.services.albums.album_art_finder import (
    find_album_art,
    find_catalog_song_metadata,
    find_song_art,
)
from youtube_audio_video_downloader.services.albums.album_names import (
    canonical_album_name,
    normalize_album_name,
    split_album_folder_name,
)
from youtube_audio_video_downloader.services.albums.album_folders import (
    consolidate_audio_in_place,
    normalize_album_folders,
)
from youtube_audio_video_downloader.services.metadata.metadata_tracker import (
    MetadataCompletionTracker,
    verification_policy_key,
)
from youtube_audio_video_downloader.services.metadata.metadata_verifier import (
    verify_metadata_evidence,
)
from youtube_audio_video_downloader.services.albums.album_consolidator import (
    SUPPORTED_AUDIO_EXTENSIONS,
    album_contains_artist,
)

from youtube_audio_video_downloader.services.media.media_metadata import (
    EditableMediaMetadata,
    read_media_metadata,
    replace_media_metadata,
)
from youtube_audio_video_downloader.services.metadata.release_year_finder import find_album_release_year
from youtube_audio_video_downloader.services.metadata.serpapi_metadata import (
    find_serpapi_song_metadata,
    serpapi_is_configured,
)
from youtube_audio_video_downloader.services.albums.track_reorder import reorder_track_numbers
from youtube_audio_video_downloader.services.albums.wikipedia_tracks import (
    find_wikipedia_song_metadata,
    find_wikipedia_tracks,
)
from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names


_RENAME_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class MetadataEnrichmentReport:
    scanned: int
    updated: tuple[Path, ...]
    skipped: tuple[str, ...]
    failed: tuple[str, ...]
    repaired_folders: tuple[Path, ...] = ()
    completed: tuple[Path, ...] = ()
    tracked: int = 0


def enrich_folder_metadata(
    source_folder: str | Path,
    *,
    additional_folders: tuple[str | Path, ...] = (),
    workers: int = 5,
    retries: int = 3,
    allow_empty: bool = False,
    tracker_path: str | Path | None = None,
    agentic_model: str = "",
    ai_enabled: bool | None = None,
    force_recheck: bool = False,
    cancellation_token: CancellationToken | None = None,
) -> MetadataEnrichmentReport:
    """Search Wikipedia and artwork providers for incomplete local media tags."""

    token = cancellation_token or CancellationToken()
    requested_roots = (source_folder, *additional_folders)
    roots: list[Path] = []
    for value in requested_roots:
        text = str(value or "").strip()
        if not text:
            continue
        candidate = Path(text).expanduser().resolve()
        if not candidate.is_dir():
            if value == source_folder:
                raise NotADirectoryError(f"Source folder does not exist: {candidate}")
            continue
        if any(candidate == root or candidate.is_relative_to(root) for root in roots):
            continue
        roots = [root for root in roots if not root.is_relative_to(candidate)]
        roots.append(candidate)
    if not roots:
        if allow_empty:
            return MetadataEnrichmentReport(0, (), (), ())
        raise NotADirectoryError("Select an existing source or destination folder")
    repaired_folders: list[Path] = []
    all_candidates = sorted(
        {
            path for root in roots for path in root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
        },
        key=lambda path: str(path).casefold(),
    )
    if not all_candidates:
        if allow_empty:
            return MetadataEnrichmentReport(0, (), (), ())
        raise ValueError("No supported audio files were found in the source folder or its subfolders")
    tracker = MetadataCompletionTracker(tracker_path)
    verification_policy = verification_policy_key(
        agentic_model, internet_only=ai_enabled is False
    )
    tracked_paths = [] if force_recheck else [
        path for path in all_candidates if tracker.is_complete(path, verification_policy)
    ]
    conflicting_year_paths = _conflicting_album_year_paths(all_candidates)
    verified_album_years = (
        _verified_conflicting_album_years(all_candidates)
        if conflicting_year_paths else {}
    )
    if conflicting_year_paths:
        tracked_paths = [
            path for path in tracked_paths if path not in conflicting_year_paths
        ]
        print(
            "[TRACKER] Rechecking "
            f"{len(conflicting_year_paths)} file(s) with conflicting sibling album years"
        )
    tracked_set = set(tracked_paths)
    candidates = [path for path in all_candidates if path not in tracked_set]
    tracked = len(all_candidates) - len(candidates)
    if tracked:
        print(f"[TRACKER] Skipped {tracked} unchanged, fully enriched file(s)")
    report = _enrich_candidates(
        candidates,
        workers,
        retries,
        token,
        album_years=(
            _album_year_consensus(all_candidates)
            if candidates and len(all_candidates) > 1
            else {}
        ),
        verified_album_years=verified_album_years,
        agentic_model=agentic_model,
        ai_enabled=ai_enabled,
        force_recheck=force_recheck,
    )
    for root in roots:
        token.raise_if_cancelled()
        root_completed = [path for path in report.completed if path.is_relative_to(root)]
        # Re-home each tagged file independently before any whole-folder rename.
        # This prevents one stray/mixed track from being carried into the album
        # represented by the other files in its old folder.
        nested_completed = [path for path in root_completed if path.parent != root]
        if nested_completed:
            consolidate_audio_in_place(
                root, media_paths=nested_completed, retries=retries
            )
        repaired_folders.extend(normalize_album_folders(root, media_paths=root_completed))
    updated = tuple(_current_path(path, roots) for path in report.updated)
    completed = tuple(_current_path(path, roots) for path in report.completed)
    current_tracked = tuple(_current_path(path, roots) for path in tracked_paths)
    if completed:
        numbering_scope = completed + current_tracked
        valid_numbering_folders = _rectify_track_numbering(numbering_scope, retries)
        completed = tuple(
            path
            for path in numbering_scope
            if path.parent.resolve() in valid_numbering_folders
        )
    else:
        # Preserve verified tracker paths for pre-move authorization without
        # reopening their media tags on a true fast-path run.
        completed = current_tracked
    tracker.mark_complete(completed, verification_policy)
    return MetadataEnrichmentReport(
        report.scanned,
        updated,
        report.skipped,
        report.failed,
        tuple(dict.fromkeys(repaired_folders)),
        completed,
        tracked,
    )


def enrich_media_files(
    media_paths: Sequence[str | Path],
    *,
    workers: int = 5,
    retries: int = 3,
    tracker_path: str | Path | None = None,
    agentic_model: str = "",
    ai_enabled: bool | None = None,
    force_recheck: bool = False,
    cancellation_token: CancellationToken | None = None,
) -> MetadataEnrichmentReport:
    """Enrich only the supplied audio files, ignoring unsupported or absent paths."""

    token = cancellation_token or CancellationToken()
    candidates = sorted(
        {
            Path(value).expanduser().resolve()
            for value in media_paths
            if Path(value).expanduser().is_file()
            and Path(value).suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
        },
        key=lambda path: str(path).casefold(),
    )
    if not candidates:
        return MetadataEnrichmentReport(0, (), (), ())
    tracker = MetadataCompletionTracker(tracker_path)
    verification_policy = verification_policy_key(
        agentic_model, internet_only=ai_enabled is False
    )
    tracked_paths = [] if force_recheck else [
        path for path in candidates if tracker.is_complete(path, verification_policy)
    ]
    conflicting_year_paths = _conflicting_album_year_paths(candidates)
    verified_album_years = (
        _verified_conflicting_album_years(candidates)
        if conflicting_year_paths else {}
    )
    if conflicting_year_paths:
        tracked_paths = [
            path for path in tracked_paths if path not in conflicting_year_paths
        ]
        print(
            "[TRACKER] Rechecking "
            f"{len(conflicting_year_paths)} file(s) with conflicting sibling album years"
        )
    tracked_set = set(tracked_paths)
    pending = [path for path in candidates if path not in tracked_set]
    tracked = len(candidates) - len(pending)
    if tracked:
        print(f"[TRACKER] Skipped {tracked} unchanged, fully enriched file(s)")
    report = _enrich_candidates(
        pending,
        workers,
        retries,
        token,
        album_years=(
            _album_year_consensus(candidates)
            if pending and len(candidates) > 1
            else {}
        ),
        verified_album_years=verified_album_years,
        agentic_model=agentic_model,
        ai_enabled=ai_enabled,
        force_recheck=force_recheck,
    )
    if report.completed:
        numbering_scope = report.completed + tuple(tracked_paths)
        valid_numbering_folders = _rectify_track_numbering(numbering_scope, retries)
        completed = tuple(
            path
            for path in numbering_scope
            if path.parent.resolve() in valid_numbering_folders
        )
    else:
        completed = tuple(tracked_paths)
    tracker.mark_complete(completed, verification_policy)
    return MetadataEnrichmentReport(
        report.scanned,
        report.updated,
        report.skipped,
        report.failed,
        report.repaired_folders,
        completed,
        tracked,
    )


def _rectify_track_numbering(paths: tuple[Path, ...], retries: int) -> set[Path]:
    """Normalize implausible numbering within each touched album group."""

    parents = {path.parent.resolve() for path in paths if path.is_file()}
    valid_parents: set[Path] = set()
    for parent in parents:
        album_groups: dict[str, list[tuple[Path, EditableMediaMetadata]]] = {}
        readable = True
        for path in sorted(parent.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
                continue
            try:
                metadata = read_media_metadata(path)
            except Exception as exc:
                print(f"[TRACK-NUMBER-SKIPPED] {path.name}: {exc}")
                readable = False
                continue
            album = normalize_album_name(_known(metadata.album))
            if album:
                album_groups.setdefault(album.casefold(), []).append((path, metadata))
        parent_valid = readable
        for items in album_groups.values():
            numbered = sorted(
                items,
                key=lambda item: (
                    _positive_number(item[1].track_number) is None,
                    _positive_number(item[1].track_number) or 0,
                    item[0].name.casefold(),
                ),
            )
            current = [_positive_number(metadata.track_number) for _, metadata in numbered]
            totals = [_positive_number(metadata.track_total) for _, metadata in numbered]
            expected = list(range(1, len(numbered) + 1))
            has_numbering = any(number is not None for number in current)
            invalid_total = any(total is not None and total != len(numbered) for total in totals)
            if not has_numbering:
                continue
            if current == expected and not invalid_total:
                continue
            try:
                updated = reorder_track_numbers(
                    [path for path, _ in numbered],
                    retries=retries,
                    normalize_total=True,
                )
            except (OSError, ValueError) as exc:
                print(f"[TRACK-NUMBER-SKIPPED] {parent.name}: {exc}")
                parent_valid = False
                continue
            if updated != len(numbered):
                parent_valid = False
                print(
                    f"[TRACK-NUMBER-SKIPPED] {parent.name}: not every track could be updated"
                )
            else:
                print(
                    f"[TRACK-NUMBER-FIXED] {parent.name}: normalized {updated} track(s)"
                )
        if parent_valid:
            valid_parents.add(parent)
    return valid_parents


def _positive_number(value: object) -> int | None:
    match = re.match(r"\s*(\d+)", str(value or ""))
    number = int(match.group(1)) if match else 0
    return number if number > 0 else None


def _enrich_candidates(
    candidates: list[Path],
    workers: int,
    retries: int,
    token: CancellationToken,
    *,
    album_years: dict[str, str] | None = None,
    verified_album_years: dict[str, str] | None = None,
    agentic_model: str = "",
    ai_enabled: bool | None = None,
    force_recheck: bool = False,
) -> MetadataEnrichmentReport:
    """Run the common parallel enrichment pipeline for concrete files."""

    token._album_years = dict(album_years or {})  # type: ignore[attr-defined]
    token._verified_album_years = dict(verified_album_years or {})  # type: ignore[attr-defined]
    token._agentic_model = str(agentic_model or "").strip()  # type: ignore[attr-defined]
    token._ai_enabled = ai_enabled  # type: ignore[attr-defined]
    token._force_recheck = bool(force_recheck)  # type: ignore[attr-defined]
    token._wikipedia_album_tracks = {}  # type: ignore[attr-defined]
    token._wikipedia_album_tracks_lock = threading.Lock()  # type: ignore[attr-defined]
    worker_count = max(1, min(int(workers), MAX_PARALLEL_WORKERS, len(candidates)))
    updated: list[Path] = []
    completed_paths: list[Path] = []
    language_by_path: dict[Path, str] = {}
    skipped: list[str] = []
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="metadata-enrich") as pool:
        futures = {
            pool.submit(_enrich_one_file_with_retries, path, token, retries): path
            for path in candidates
        }
        for future in as_completed(futures):
            path = futures[future]
            token.raise_if_cancelled()
            try:
                result = future.result()
                status, detail, result_path = result[:3]
                is_complete = bool(result[3]) if len(result) > 3 else False
                evidence_language = str(result[4] or "") if len(result) > 4 else ""
            except FileInUseSkippedError as exc:
                message = f"{path.name}: {exc}"
                skipped.append(message)
                print(f"[ENRICH-SKIPPED] {message}")
                continue
            except Exception as exc:  # One lookup failure must not stop other files.
                message = f"{path.name}: {exc}"
                failed.append(message)
                print(f"[ENRICH-FAILED] {message}")
                continue
            if is_complete:
                completed_paths.append(result_path)
                if evidence_language:
                    language_by_path[result_path] = evidence_language
            if status == "updated":
                updated.append(result_path)
                print(f"[ENRICHED] {result_path.name}: {detail}")
            else:
                message = f"{path.name}: {detail}"
                skipped.append(message)
                print(f"[ENRICH-SKIPPED] {message}")
    language_paths = _apply_mixed_language_qualifiers(language_by_path, retries)
    if language_paths:
        completed_paths = [language_paths.get(path, path) for path in completed_paths]
        updated = [language_paths.get(path, path) for path in updated]
        updated.extend(
            current for original, current in language_paths.items()
            if original not in updated and current not in updated
        )
    return MetadataEnrichmentReport(
        scanned=len(candidates),
        updated=tuple(sorted(set(updated), key=lambda path: str(path).casefold())),
        skipped=tuple(sorted(skipped, key=str.casefold)),
        failed=tuple(sorted(failed, key=str.casefold)),
        completed=tuple(sorted(set(completed_paths), key=lambda path: str(path).casefold())),
    )


def _enrich_one_file_with_retries(
    path: Path,
    token: CancellationToken,
    retries: int,
) -> tuple[str, str, Path, bool]:
    """Retry one failed file without replaying already successful files."""

    attempts = max(1, int(retries))
    for attempt in range(1, attempts + 1):
        token.raise_if_cancelled()
        try:
            return _enrich_one_file(path, token)
        except FileInUseSkippedError:
            raise
        except Exception as exc:
            if attempt >= attempts:
                raise
            print(
                f"[RETRY] {path.name}: enrichment failed ({exc}); "
                f"attempt {attempt + 1}/{attempts}"
            )
            token.wait(min(2.0 ** (attempt - 1), 5.0))
    raise RuntimeError("Metadata enrichment retry loop ended unexpectedly")


def _current_path(path: Path, roots: list[Path]) -> Path:
    """Locate an enriched file after its containing album folder was renamed."""

    if path.exists():
        return path
    owning_root = next((root for root in roots if path.is_relative_to(root)), None)
    if owning_root is None:
        return path
    matches = list(owning_root.rglob(path.name))
    return matches[0] if len(matches) == 1 else path


def _enrich_one_file(
    path: Path,
    token: CancellationToken,
) -> tuple[str, str, Path, bool]:
    token.raise_if_cancelled()
    print(f"[ENRICH-START] Searching metadata for: {path.name}")
    metadata = read_media_metadata(path)
    force_recheck = bool(getattr(token, "_force_recheck", False))
    tagged_existing_album = _known(metadata.album)
    parent_album, parent_year = split_album_folder_name(path.parent.name)
    # A year-qualified album folder is an explicit user/library boundary.  It
    # also lets a fixed build recover files that an older build wrongly retagged
    # to a storefront compilation while leaving them inside the intended folder.
    folder_album_scope = canonical_album_name(parent_album, parent_year) if parent_year else ""
    original_existing_album = folder_album_scope or tagged_existing_album
    normalized_existing_album, _existing_album_year = split_album_folder_name(
        normalize_album_name(original_existing_album)
    )
    original_existing_artists = _known(metadata.artists)
    normalized_existing_artists = _normalize_enrichment_artists(original_existing_artists)
    artists_need_separator_cleanup = (
        original_existing_artists != normalized_existing_artists
    )
    raw_file_title, raw_file_album, raw_file_artists = _filename_hints(path.stem)
    file_title = _known(raw_file_title)
    file_album = _known(raw_file_album)
    file_artists = _known(raw_file_artists)
    structured_name = _parse_structured_name(path.stem)
    structured_core_is_valid = bool(
        structured_name
        and file_title
        and file_album
        and file_artists
        and not album_contains_artist(file_album, file_artists)
    )
    raw_existing_album = normalized_existing_album
    title_hint = _known(metadata.title) or file_title
    lookup_title = _lookup_song_title(title_hint)
    artists_hint = normalized_existing_artists or file_artists
    existing_album_is_invalid = album_contains_artist(raw_existing_album, artists_hint)
    existing_album = "" if existing_album_is_invalid else raw_existing_album
    existing_album_key = _track_key(existing_album)
    title_key = _track_key(lookup_title)
    existing_album_looks_like_track_variant = bool(
        existing_album_key
        and (
            existing_album_key == title_key
            or "lofi" in existing_album_key.split()
            or "slowed" in existing_album_key.split()
            or "reverb" in existing_album_key.split()
        )
    )
    protected_existing_album = bool(
        existing_album
        and (
            folder_album_scope
            or not existing_album_looks_like_track_variant
        )
    )
    protected_album_conflict = False
    album_hint, filename_album_year = split_album_folder_name(
        existing_album or file_album
    )
    year_hint = parent_year or _known(metadata.year)
    sibling_year = getattr(token, "_album_years", {}).get(_track_key(album_hint), "")
    verified_sibling_year = getattr(token, "_verified_album_years", {}).get(
        _track_key(album_hint), ""
    )
    wiki_match: dict[str, str] = {}
    album_verified = False
    album_table_title = ""

    # When artists are missing but the album is known, its verified track table
    # is the strongest and cheapest source. Share one request between sibling
    # workers so broad per-song searches cannot throttle the album lookup.
    if album_hint and (not normalized_existing_artists or force_recheck):
        tracks = _shared_wikipedia_tracks(token, album_hint, year_hint or filename_album_year)
        exact = _album_track_match(tracks, lookup_title, path)
        if exact:
            album_table_title = str(exact.get("title") or "").strip()
            print(
                f"[WIKIPEDIA-ALBUM-MATCH] {path.name}: "
                f"{album_table_title} | {str(exact.get('artists') or '').strip()}"
            )
            wiki_match = {
                "title": album_table_title,
                "album": album_hint,
                "artists": str(exact.get("artists") or "").strip(),
                "year": year_hint or filename_album_year,
            }
            album_verified = True

    if not album_verified:
        try:
            lookup_context = " - ".join(
                part for part in (file_title, file_album, file_artists) if part
            )
            wiki_match = find_wikipedia_song_metadata(
                lookup_context or path.stem, lookup_title, artists_hint
            )
        except (LookupError, OSError, TimeoutError, ValueError):
            wiki_match = {}
        album_verified = bool(wiki_match.get("album"))

    if not album_verified:
        title_track_album = _title_track_album_hint(lookup_title)
        if title_track_album:
            try:
                title_track_release = find_album_release_year(title_track_album)
            except (LookupError, OSError, TimeoutError, ValueError):
                title_track_release = {}
            if title_track_release.get("year"):
                # "Title Track" is an explicit album relationship, but still
                # require an externally verified album page/year before tagging.
                wiki_match = {
                    "title": lookup_title,
                    "album": title_track_album,
                    "artists": artists_hint,
                    "year": str(title_track_release["year"]),
                    "page_title": str(title_track_release.get("page_title") or ""),
                }
                album_verified = True

    if not album_verified and album_hint:
        # A bad embedded year must not prevent an otherwise exact track-row match.
        tracks = _shared_wikipedia_tracks(token, album_hint)
        exact = _album_track_match(tracks, lookup_title, path)
        if exact:
            album_table_title = str(exact.get("title") or "").strip()
            print(
                f"[WIKIPEDIA-ALBUM-MATCH] {path.name}: "
                f"{album_table_title} | {str(exact.get('artists') or '').strip()}"
            )
            wiki_match = {
                "title": album_table_title,
                "album": album_hint,
                "artists": str(exact.get("artists") or "").strip(),
            }
            album_verified = True

    candidate_album, _candidate_year = split_album_folder_name(
        normalize_album_name(_known(wiki_match.get("album")))
    )
    if candidate_album:
        wiki_match = {**wiki_match, "album": candidate_album}
    candidate_artists = artists_hint or _known(wiki_match.get("artists"))
    if candidate_album and album_contains_artist(candidate_album, candidate_artists):
        wiki_match = {**wiki_match, "album": ""}
        album_verified = False

    catalog_title = _known(wiki_match.get("title")) or lookup_title
    catalog_match = find_catalog_song_metadata(catalog_title, artists_hint)
    agent_wiki_match = dict(wiki_match)
    # A duration-confirmed row from the protected album table is authoritative
    # for missing singer credits. Catalog APIs often include the composer or
    # album artist and must not displace that explicit Wikipedia Singer(s) cell.
    agent_catalog_match = (
        {} if album_table_title and (not normalized_existing_artists or force_recheck)
        else dict(catalog_match)
    )
    corrected_catalog_title = _known(catalog_match.get("title"))
    if (
        not wiki_match.get("language")
        and corrected_catalog_title
        and _track_key(corrected_catalog_title) != _track_key(catalog_title)
    ):
        try:
            supplemental_wiki = find_wikipedia_song_metadata(
                corrected_catalog_title, corrected_catalog_title, artists_hint
            )
        except (LookupError, OSError, TimeoutError, ValueError):
            supplemental_wiki = {}
        if supplemental_wiki.get("language"):
            # The catalog remains authoritative for album identity here; this
            # second lookup only supplies language after correcting a misspelled
            # local title such as Patakha Gudi -> Patakha Guddi.
            wiki_match = {
                **wiki_match,
                "language": str(supplemental_wiki["language"]),
            }
    catalog_album, _catalog_year = split_album_folder_name(
        normalize_album_name(_known(catalog_match.get("album")))
    )
    if album_contains_artist(
        catalog_album, artists_hint or _known(catalog_match.get("artists"))
    ):
        catalog_album = ""
    if candidate_album and catalog_album and (
        _track_key(candidate_album) != _track_key(catalog_album)
    ):
        if _catalog_recording_matches_file(path, catalog_match):
            # Same-title songs can belong to different films. An exact artist
            # catalog result whose duration matches the local recording is
            # stronger than an ambiguous singer-discography title row.
            wiki_language = _known(wiki_match.get("language"))
            wiki_match = {"language": wiki_language} if wiki_language else {}
            candidate_album = ""
            catalog_match = {**catalog_match, "album": catalog_album}
            album_verified = True
        else:
            # Do not attach cover art from a catalog result that conflicts with
            # a film/album row unless it also matches the recording duration.
            catalog_match = {}
            catalog_album = ""
    elif catalog_album:
        catalog_match = {**catalog_match, "album": catalog_album}
        album_verified = True

    serpapi_match: dict[str, str] = {}
    # Paid Google searches are a fallback, not the first request for every file.
    # Use them when the built-in sources failed to identify one coherent album.
    if (
        serpapi_is_configured()
        and not protected_existing_album
        and not catalog_album
    ):
        serpapi_match = find_serpapi_song_metadata(lookup_title, artists_hint)
        serpapi_album, _serpapi_year = split_album_folder_name(
            normalize_album_name(_known(serpapi_match.get("album")))
        )
        if album_contains_artist(
            serpapi_album, artists_hint or _known(serpapi_match.get("artists"))
        ):
            serpapi_match = {}
        elif serpapi_album:
            serpapi_match = {**serpapi_match, "album": serpapi_album}
            album_verified = True
            agent_catalog_album, _agent_catalog_year = split_album_folder_name(
                normalize_album_name(_known(agent_catalog_match.get("album")))
            )
            if (
                candidate_album
                and _track_key(candidate_album) == _track_key(serpapi_album)
                and agent_catalog_album
                and _track_key(agent_catalog_album) != _track_key(serpapi_album)
            ):
                # Two independent sources agree while the storefront points to
                # another recording/version. Do not let that rejected collection
                # force the final verifier back into review.
                agent_catalog_match = {}

    evidence = {**catalog_match, **serpapi_match, **wiki_match}
    # An Apple collection record describes the album as a whole. Wikipedia
    # discography/list pages often describe the individual track's release
    # year, which must not fragment one collection into multiple album years.
    # Keep Wikipedia's year for a dedicated film/album page (important when a
    # soundtrack precedes a film), otherwise use the catalog collection year.
    wiki_page = _known(wiki_match.get("page_title"))
    wiki_is_track_list = bool(
        wiki_page
        and (
            re.search(r"(?:discography|list of songs recorded by)", wiki_page, re.I)
            or _track_key(wiki_page)
            in {_track_key(artists_hint), _track_key(file_artists)}
        )
    )
    if catalog_match.get("year") and (not wiki_match.get("year") or wiki_is_track_list):
        evidence["year"] = _original_release_year(
            wiki_match, catalog_match, candidate_album, catalog_album
        ) or catalog_match["year"]
    if catalog_match.get("album_art"):
        evidence["album_art"] = catalog_match["album_art"]

    agentic_model = str(getattr(token, "_agentic_model", "") or "").strip()
    local_evidence = {
        # An album-table title reached here only after exact alias matching or
        # unique fuzzy + duration verification, so it is safe as the canonical
        # local identity presented to the final evidence gate.
        "title": album_table_title or _known(metadata.title),
        "album": existing_album if protected_existing_album else "",
        "artists": (
            "" if album_table_title and force_recheck
            else normalized_existing_artists
        ),
        "year": _known(metadata.year),
    }
    internet_only = getattr(token, "_ai_enabled", None) is False
    if agentic_model or internet_only:
        decision = verify_metadata_evidence(
            local_evidence,
            agent_wiki_match,
            agent_catalog_match,
            serpapi=serpapi_match,
            model=agentic_model,
            catalog_duration_matches=(
                _catalog_recording_matches_file(path, agent_catalog_match)
                if agent_catalog_match else None
            ),
        )
        if decision.action != "apply" and not protected_existing_album:
            rejected = (
                f"; rejected {', '.join(decision.rejected_sources)}"
                if decision.rejected_sources else ""
            )
            print(
                f"[METADATA-REVIEW] {path.name}: {decision.reason} "
                f"(confidence {decision.confidence:.0%}){rejected}"
            )
            return (
                "skipped", f"metadata review required: {decision.reason}", path, False,
                _known(decision.metadata.get("language")),
            )
        if decision.action == "apply":
            # Replace the rule-merged dictionary rather than updating it. Keeping
            # leftovers could combine fields from incompatible internet identities.
            evidence = {
                key: value for key, value in decision.metadata.items() if value
            }
            if decision.album_art:
                evidence["album_art"] = decision.album_art
            album_verified = bool(evidence.get("album"))
            mode = "AI" if agentic_model else "INTERNET"
            print(
                f"[{mode}-VERIFIED] {path.name}: {decision.reason} "
                f"(confidence {decision.confidence:.0%})"
            )
        else:
            protected_album_conflict = True
            # Ambiguous evidence must not leak into a protected local identity.
            evidence = {}
            print(
                f"[ALBUM-PRESERVED] {path.name}: existing album "
                f"'{original_existing_album}' is authoritative; conflicting "
                "external collection metadata will not replace it"
            )

    # Defense in depth: even a provider result or model response that survives
    # the evidence gate cannot replace a populated, valid album.  Also reject
    # artwork obtained from a different catalog collection; it will be looked
    # up again using the protected album below.
    if protected_existing_album:
        evidence_album, _ = split_album_folder_name(
            normalize_album_name(_known(evidence.get("album")))
        )
        catalog_art_conflicts = bool(
            evidence.get("album_art")
            and catalog_album
            and _track_key(catalog_album) != _track_key(existing_album)
        )
        if (
            evidence_album
            and _track_key(evidence_album) != _track_key(existing_album)
        ) or catalog_art_conflicts:
            protected_album_conflict = True
            evidence.pop("album_art", None)
        evidence["album"] = existing_album
        if year_hint and not force_recheck:
            evidence["year"] = year_hint

    resolved_album = (
        _known(evidence.get("album"))
        or existing_album
        or (file_album if structured_core_is_valid else "")
    )
    resolved_year = (
        (_known(evidence.get("year")) or year_hint or filename_album_year)
        if protected_existing_album and force_recheck
        else (year_hint or filename_album_year)
        if protected_existing_album
        else (
            verified_sibling_year
            or _known(evidence.get("year"))
            or sibling_year
            or year_hint
            or filename_album_year
        )
    )
    evidence_language = _known(evidence.get("language"))
    if (
        evidence_language
        and not _album_language(resolved_album)
        and _has_language_collision(path, resolved_album, resolved_year)
    ):
        resolved_album = _qualify_album_language(resolved_album, evidence_language)
    if resolved_album and not resolved_year:
        try:
            year_result = find_album_release_year(resolved_album)
        except (LookupError, OSError, TimeoutError, ValueError):
            year_result = {}
        if year_result.get("year"):
            resolved_year = str(year_result["year"])
            album_verified = True

    canonical_resolved_album = (
        canonical_album_name(existing_album, resolved_year)
        if protected_existing_album
        else canonical_album_name(resolved_album, resolved_year)
    )

    updates: dict[str, object] = {}
    verified_title = _lookup_song_title(_known(evidence.get("title")))
    normalized_existing_title = _normalize_display_title(metadata.title)
    if verified_title and _track_key(metadata.title) != _track_key(verified_title):
        updates["title"] = verified_title
    elif normalized_existing_title and _known(metadata.title) != normalized_existing_title:
        updates["title"] = normalized_existing_title
    elif not _known(metadata.title) and structured_core_is_valid:
        updates["title"] = file_title
    if canonical_resolved_album and tagged_existing_album != canonical_resolved_album:
        updates["album"] = canonical_resolved_album
    elif existing_album_is_invalid:
        # Empty values intentionally delete the corresponding embedded tag.
        updates["album"] = ""
    if artists_need_separator_cleanup:
        updates["artists"] = normalized_existing_artists
    elif (
        force_recheck
        and album_table_title
        and _known(evidence.get("artists"))
        and _track_key(normalized_existing_artists)
        != _track_key(evidence.get("artists"))
    ):
        updates["artists"] = _normalize_enrichment_artists(evidence["artists"])
    elif not _known(metadata.artists) and (
        evidence.get("artists") or structured_core_is_valid or artists_hint
    ):
        updates["artists"] = artists_hint or evidence["artists"]
    if resolved_year and year_hint != resolved_year:
        updates["year"] = resolved_year

    # An existing picture is not proof that it belongs to the resolved album.
    # Exact catalog artwork is therefore allowed to repair a populated APIC tag.
    artwork_url = _known(evidence.get("album_art"))
    album_language_changed = bool(
        canonical_resolved_album
        and _album_language(canonical_resolved_album)
        and _album_language(tagged_existing_album)
        != _album_language(canonical_resolved_album)
    )
    if protected_existing_album and protected_album_conflict and not artwork_url:
        try:
            artwork_url = find_album_art(existing_album, release_year=resolved_year)
        except (LookupError, OSError, TimeoutError, ValueError):
            try:
                artwork_url = find_song_art(
                    verified_title or lookup_title, artists_hint
                )
            except (LookupError, OSError, TimeoutError, ValueError):
                artwork_url = ""
    if (
        not artwork_url
        and resolved_album
        and album_verified
        and (not metadata.artwork_present or album_language_changed)
    ):
        if not artwork_url:
            try:
                artwork_url = find_album_art(resolved_album, release_year=resolved_year)
            except (LookupError, OSError, TimeoutError, ValueError):
                try:
                    artwork_url = find_song_art(verified_title or lookup_title, artists_hint)
                except (LookupError, OSError, TimeoutError, ValueError):
                    artwork_url = ""
    final_title = _known(updates.get("title", metadata.title))
    final_album = _known(updates.get("album", metadata.album))
    final_artists = _known(updates.get("artists", metadata.artists))
    if not updates and not artwork_url:
        renamed = _rename_enriched_audio(path, final_title, final_album, final_artists)
        complete = _is_complete(metadata)
        if renamed != path:
            return "updated", "renamed", renamed, complete, evidence_language
        detail = (
            "metadata is already complete"
            if complete
            else "no exact internet metadata match was found"
        )
        return "skipped", detail, path, complete, evidence_language
    token.raise_if_cancelled()
    replace_media_metadata(path, updates, artwork_path=artwork_url or None)
    changed = [
        "Album metadata field removed" if key == "album" and value == "" else key
        for key, value in updates.items()
    ]
    if artwork_url:
        changed.append("artwork")
    renamed = _rename_enriched_audio(path, final_title, final_album, final_artists)
    if renamed != path:
        changed.append("renamed")
    complete = bool(
        final_title
        and final_album
        and final_artists
        and (resolved_year or year_hint)
        and (metadata.artwork_present or artwork_url)
    )
    return "updated", ", ".join(changed), renamed, complete, evidence_language


def _apply_mixed_language_qualifiers(
    language_by_path: dict[Path, str], _retries: int
) -> dict[Path, Path]:
    """Qualify every track when one album/year contains multiple languages."""

    records: list[tuple[Path, EditableMediaMetadata, str, tuple[str, str]]] = []
    languages: dict[tuple[str, str], set[str]] = {}
    for path, language in language_by_path.items():
        try:
            metadata = read_media_metadata(path)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        album, album_year = split_album_folder_name(metadata.album)
        year_match = re.search(
            r"\b((?:19|20)\d{2})\b", _known(metadata.year) or album_year
        )
        key = (_track_key(_album_without_language(album)), year_match.group(1) if year_match else "")
        normalized_language = language.title()
        if not key[0] or not key[1] or not normalized_language:
            continue
        languages.setdefault(key, set()).add(normalized_language)
        records.append((path, metadata, normalized_language, key))
    mixed = {key for key, values in languages.items() if len(values) > 1}
    changed_paths: dict[Path, Path] = {}
    for path, metadata, language, key in records:
        if key not in mixed:
            continue
        album_base = _album_without_language(metadata.album)
        qualified = canonical_album_name(
            _qualify_album_language(album_base, language), key[1]
        )
        if metadata.album == qualified:
            continue
        retry_file_operation(
            path,
            "writing mixed-language album identity",
            lambda p=path, album=qualified: replace_media_metadata(
                p, {"album": album}
            ),
        )
        renamed = _rename_enriched_audio(
            path, _known(metadata.title), qualified, _known(metadata.artists)
        )
        changed_paths[path] = renamed
        print(f"[LANGUAGE-SPLIT] {renamed.name}: {qualified}")
    return changed_paths


def _rename_enriched_audio(path: Path, title: str, album: str, artists: str) -> Path:
    """Rename in the current subfolder only when all filename fields are known."""

    if not title or not album or not artists:
        return path
    stem = safe_filename(f"{title} - {album} - {artists}", fallback=path.stem)
    desired = path.with_name(f"{stem}{path.suffix.lower()}")
    if desired == path:
        return path
    with _RENAME_LOCK:
        if desired.exists() and _same_enriched_recording(
            path, desired, title=title, album=album, artists=artists
        ):
            if _media_quality_score(path) > _media_quality_score(desired):
                retry_file_operation(
                    desired,
                    "removing the lower-quality duplicate",
                    desired.unlink,
                )
                retry_file_operation(
                    path,
                    "renaming the preferred enriched song",
                    lambda: path.rename(desired),
                )
                print(
                    f"[DUPLICATE-REPLACED] Kept higher-quality copy as: {desired.name}"
                )
            else:
                retry_file_operation(
                    path,
                    "removing the enriched duplicate",
                    path.unlink,
                )
                print(
                    f"[DUPLICATE-REMOVED] {path.name}: canonical copy already "
                    f"exists as {desired.name}"
                )
            return desired
        destination = _available_rename_path(desired)
        retry_file_operation(
            path,
            "renaming the enriched song",
            lambda: path.rename(destination),
        )
    print(f"[RENAMED] Enriched song renamed to: {destination}")
    return destination


def _same_enriched_recording(
    candidate: Path,
    existing: Path,
    *,
    title: str,
    album: str,
    artists: str,
) -> bool:
    """Recognize duplicate recordings after both resolve to one song identity."""

    try:
        metadata = read_media_metadata(existing)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    if _track_key(metadata.title) != _track_key(title):
        return False
    existing_album = canonical_album_name(metadata.album, metadata.year)
    if _track_key(existing_album) != _track_key(album):
        return False
    existing_artists = {
        _track_key(part)
        for part in _normalize_enrichment_artists(metadata.artists).split(",")
        if _track_key(part)
    }
    candidate_artists = {
        _track_key(part)
        for part in _normalize_enrichment_artists(artists).split(",")
        if _track_key(part)
    }
    if not existing_artists or existing_artists != candidate_artists:
        return False
    candidate_duration = _media_duration(candidate)
    existing_duration = _media_duration(existing)
    if candidate_duration <= 0 or existing_duration <= 0:
        return False
    tolerance = max(20.0, max(candidate_duration, existing_duration) * 0.08)
    return abs(candidate_duration - existing_duration) <= tolerance


def _media_duration(path: Path) -> float:
    try:
        media = MutagenFile(path)
        return float(getattr(getattr(media, "info", None), "length", 0.0) or 0.0)
    except (MutagenError, OSError, TypeError, ValueError):
        return 0.0


def _catalog_recording_matches_file(
    path: Path, catalog_match: dict[str, str]
) -> bool:
    try:
        catalog_duration = float(catalog_match.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        return False
    file_duration = _media_duration(path)
    if file_duration <= 0 or catalog_duration <= 0:
        return False
    tolerance = max(5.0, max(file_duration, catalog_duration) * 0.03)
    return abs(file_duration - catalog_duration) <= tolerance


def _media_quality_score(path: Path) -> tuple[int, int, int]:
    """Prefer bitrate, then embedded artwork, then file size."""

    bitrate = 0
    try:
        media = MutagenFile(path)
        bitrate = int(getattr(getattr(media, "info", None), "bitrate", 0) or 0)
    except (MutagenError, OSError, TypeError, ValueError):
        pass
    try:
        artwork = int(read_media_metadata(path).artwork_present)
    except (OSError, RuntimeError, TypeError, ValueError):
        artwork = 0
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return bitrate, artwork, size


def _available_rename_path(desired: Path) -> Path:
    if not desired.exists():
        return desired
    counter = 2
    while True:
        candidate = desired.with_name(f"{desired.stem} ({counter}){desired.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _is_complete(metadata: EditableMediaMetadata) -> bool:
    return all(
        (
            _known(metadata.title), _known(metadata.album), _known(metadata.artists),
            _known(metadata.year), metadata.artwork_present,
        )
    )


def _known(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() == "unknown" else text


def _normalize_enrichment_artists(value: object) -> str:
    """Canonicalize legacy multi-artist tag separators for tags and filenames."""

    text = _known(value)
    if not text:
        return ""
    text = re.sub(r"\s*[;_]\s*", ", ", text)
    compact_slash_name = bool(re.fullmatch(r"[A-Z0-9]{1,4}/[A-Z0-9]{1,4}", text))
    if not compact_slash_name:
        text = re.sub(r"\s*/\s*", ", ", text)
    return format_artist_names(text)


def _parse_structured_name(stem: str):
    try:
        return parse_file_name_metadata(stem)
    except ValueError:
        return None


def _filename_hints(stem: str) -> tuple[str, str, str]:
    parsed = _parse_structured_name(stem)
    if parsed is not None:
        return parsed.title, normalize_album_name(parsed.album), ", ".join(parsed.artists)
    title, separator, artists = stem.partition(" - ")
    if separator and title.strip() and artists.strip():
        return title.strip(), "", artists.strip()
    return stem.strip(), "", ""


def _track_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _lookup_song_title(value: object) -> str:
    """Remove catalog/version labels that are not part of the composition title."""

    text = _known(value)
    text = re.sub(r"\s*[\[(]\s*original\s*[\])]\s*$", "", text, flags=re.I)
    text = re.sub(
        r"\s+-\s+(?:lo-?fi|slowed(?:\s*&\s*reverb)?|remix|reprise|acoustic)\s*$",
        "",
        text,
        flags=re.I,
    )
    return _normalize_display_title(text)


def _title_track_album_hint(value: object) -> str:
    """Extract the named film/album from an explicit title-track label."""

    text = _known(value)
    marker = re.search(
        r"\s*(?:[-–—:]\s*)?(?:\(\s*)?title\s+(?:track|song)(?:\s*\))?\s*$",
        text,
        flags=re.I,
    )
    if not marker:
        return ""
    return normalize_album_name(text[:marker.start()].strip(" -–—:()"))


def _normalize_display_title(value: object) -> str:
    """Clean malformed quote punctuation without discarding version identity."""

    text = " ".join(_known(value).split())
    text = re.sub(r"[\"'“”‘’]+\s*,\s*(?=\()", " ", text)
    text = re.sub(r"\s+,\s*(?=\()", " ", text)
    text = text.strip(" \"'“”‘’")
    text = re.sub(
        r"\(\s*(male|female)\s+version\s*\)",
        lambda match: f"({match.group(1).title()} Version)",
        text,
        flags=re.I,
    )
    return text.strip()


def _album_year_consensus(paths: list[Path]) -> dict[str, str]:
    """Infer a year only when every dated sibling of an album base agrees."""

    observed: dict[str, set[str]] = {}
    for path in paths:
        try:
            metadata = read_media_metadata(path)
        except (MutagenError, OSError, RuntimeError, TypeError, ValueError):
            continue
        album, album_year = split_album_folder_name(metadata.album)
        key = _track_key(album)
        year_match = re.search(
            r"\b((?:19|20)\d{2})\b", _known(metadata.year) or album_year
        )
        if key and year_match:
            observed.setdefault(key, set()).add(year_match.group(1))
    return {
        album: next(iter(years))
        for album, years in observed.items()
        if len(years) == 1
    }


def _conflicting_album_year_paths(paths: list[Path]) -> set[Path]:
    """Return sibling tracks whose same album identity has multiple years."""

    groups: dict[tuple[Path, str], dict[str, set[Path]]] = {}
    for path in paths:
        parsed = _parse_structured_name(path.stem)
        if parsed is None:
            continue
        album, album_year = split_album_folder_name(
            normalize_album_name(parsed.album)
        )
        album_key = _track_key(album)
        year_match = re.search(r"\b((?:19|20)\d{2})\b", album_year)
        if not album_key or not year_match:
            continue
        group = groups.setdefault((path.parent.resolve(), album_key), {})
        group.setdefault(year_match.group(1), set()).add(path)
    return {
        path
        for years in groups.values()
        if len(years) > 1
        for year_paths in years.values()
        for path in year_paths
    }


def _verified_conflicting_album_years(paths: list[Path]) -> dict[str, str]:
    """Resolve one authoritative year for every conflicted sibling album."""

    observed: dict[str, tuple[str, set[str]]] = {}
    for path in paths:
        parsed = _parse_structured_name(path.stem)
        if parsed is None:
            continue
        album, year = split_album_folder_name(normalize_album_name(parsed.album))
        key = _track_key(album)
        if not key or not year:
            continue
        display_album, years = observed.setdefault(key, (album, set()))
        years.add(year)
    verified: dict[str, str] = {}
    for key, (album, years) in observed.items():
        if len(years) < 2:
            continue
        try:
            result = find_album_release_year(album)
        except (LookupError, OSError, TimeoutError, ValueError):
            continue
        year_match = re.search(r"\b((?:19|20)\d{2})\b", str(result.get("year") or ""))
        if not year_match:
            continue
        verified[key] = year_match.group(1)
        print(
            f"[ALBUM-YEAR] {album}: using verified collection year "
            f"{verified[key]} for all sibling tracks"
        )
    return verified


def _original_release_year(
    wiki_match: dict[str, str],
    catalog_match: dict[str, str],
    wiki_album: str,
    catalog_album: str,
) -> str:
    """Prefer an evidenced original year over a later digital reissue date."""

    wiki_year_match = re.search(r"\b((?:19|20)\d{2})\b", _known(wiki_match.get("year")))
    catalog_year_match = re.search(
        r"\b((?:19|20)\d{2})\b", _known(catalog_match.get("year"))
    )
    if not wiki_year_match or not catalog_year_match:
        return ""
    if _track_key(wiki_album) != _track_key(catalog_album):
        return ""
    wiki_year = int(wiki_year_match.group(1))
    catalog_year = int(catalog_year_match.group(1))
    # A gap of two or more years for the same album/track is characteristic of
    # a catalog remaster or digital reissue. The discography/film year is the
    # original collection identity used for tags and album folders.
    return str(wiki_year) if catalog_year - wiki_year >= 2 else ""


def _album_language(value: object) -> str:
    album, _year = split_album_folder_name(value)
    match = re.search(
        r"\((Hindi|Bengali|Tamil|Telugu|Malayalam|Kannada|Marathi|Punjabi)\)\s*$",
        album,
        flags=re.I,
    )
    return match.group(1).title() if match else ""


def _album_without_language(value: object) -> str:
    album, _year = split_album_folder_name(value)
    return re.sub(
        r"\s*\((?:Hindi|Bengali|Tamil|Telugu|Malayalam|Kannada|Marathi|Punjabi)\)\s*$",
        "",
        album,
        flags=re.I,
    ).strip()


def _qualify_album_language(album: object, language: str) -> str:
    base = _album_without_language(album)
    return f"{base} ({language.title()})" if base else ""


def _has_language_collision(path: Path, album: object, year: object) -> bool:
    """Detect same-name/year sibling albums carrying a language qualifier."""

    library = path.parent.parent
    if not library.is_dir():
        return False
    wanted_base = _track_key(_album_without_language(album))
    wanted_year_match = re.search(r"\b((?:19|20)\d{2})\b", str(year or ""))
    wanted_year = wanted_year_match.group(1) if wanted_year_match else ""
    if not wanted_base or not wanted_year:
        return False
    for sibling in library.iterdir():
        if not sibling.is_dir():
            continue
        sibling_album, sibling_year = split_album_folder_name(sibling.name)
        if sibling_year != wanted_year or not _album_language(sibling_album):
            continue
        if _track_key(_album_without_language(sibling_album)) == wanted_base:
            return True
    return False


def _album_track_match(
    tracks: list[dict[str, object]], title: str, path: Path
) -> dict[str, object] | None:
    """Match a local title to one row from an already verified album table."""

    wanted = _album_title_key(title)
    exact = [
        track for track in tracks
        if _album_title_key(track.get("title")) == wanted
    ]
    if len(exact) == 1:
        return exact[0]

    # Permit a small filename typo only inside the verified album boundary and
    # only when the recording duration independently supports one unique row.
    ranked = sorted(
        [
            (
                SequenceMatcher(
                    None, wanted, _album_title_key(track.get("title"))
                ).ratio(),
                track,
            )
            for track in tracks if _album_title_key(track.get("title"))
        ],
        key=lambda item: item[0],
    )
    if not ranked:
        return None
    best_score, best = ranked[-1]
    runner_up = ranked[-2][0] if len(ranked) > 1 else 0.0
    if best_score < 0.84 or best_score - runner_up < 0.08:
        return None
    try:
        table_duration = float(best.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        return None
    file_duration = _media_duration(path)
    if file_duration <= 0 or table_duration <= 0:
        return None
    tolerance = max(10.0, max(file_duration, table_duration) * 0.05)
    return best if abs(file_duration - table_duration) <= tolerance else None


def _shared_wikipedia_tracks(
    token: CancellationToken, album: str, year: str = ""
) -> list[dict[str, object]]:
    """Fetch one album table once and share it between concurrent file workers."""

    cache = getattr(token, "_wikipedia_album_tracks", None)
    lock = getattr(token, "_wikipedia_album_tracks_lock", None)
    if not isinstance(cache, dict) or lock is None:
        cache = {}
        lock = threading.Lock()
        token._wikipedia_album_tracks = cache  # type: ignore[attr-defined]
        token._wikipedia_album_tracks_lock = lock  # type: ignore[attr-defined]
    key = (_track_key(album), str(year or "").strip())
    with lock:
        if key not in cache:
            try:
                cache[key] = find_wikipedia_tracks(album, str(year or "").strip())
            except (LookupError, OSError, TimeoutError, ValueError):
                cache[key] = []
        return list(cache[key])


def _album_title_key(value: object) -> str:
    """Normalize common filename/Wikipedia wording differences for one album."""

    key = _track_key(value)
    key = re.sub(r"^i am\b", "i m", key)
    key = re.sub(r"\b(male|female) version$", r"\1", key)
    return " ".join(key.split())
