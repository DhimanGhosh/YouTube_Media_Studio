"""Load and validate JSON input files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from youtube_audio_video_downloader.domain.models import ExistingMp3TagJob, ParsedSongMetadata, Song, VideoJob
from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names


FILE_NAME_DELIMITER = " - "


def parse_file_name_metadata(file_name: str) -> ParsedSongMetadata:
    """Parse title, album and artists from a structured file name.

    Supported format:
        <title> - <album> - <comma separated artists>

    The delimiter is space + hyphen + space: ``" - "``. Normal hyphens inside
    title/album/artist text are safe as long as they are not written as ``" - "``.
    """

    parts = [part.strip() for part in file_name.split(FILE_NAME_DELIMITER, maxsplit=2)]

    if len(parts) != 3 or not all(parts):
        raise ValueError(
            "Invalid structured file name format. Expected: "
            "'<title> - <album> - <comma separated artists>'. "
            f"Received: {file_name!r}"
        )

    title, album, artists_text = parts
    artists = parse_artists(artists_text)

    if not artists:
        raise ValueError(f"No artists found in file name: {file_name!r}")

    return ParsedSongMetadata(title=title, album=album, artists=artists)


def parse_artists(artists_value: Any) -> list[str]:
    """Parse artists from either a comma-separated string or a JSON list."""

    if isinstance(artists_value, list):
        artists_value = ", ".join(
            str(artist).strip() for artist in artists_value if str(artist).strip()
        )

    artists_text = format_artist_names(str(artists_value or "").strip())
    return [artist.strip() for artist in artists_text.split(",") if artist.strip()]


def load_songs(json_path: Path) -> list[Song]:
    """Load songs from a download JSON file.

    Expected JSON shape:

    {
      "Song Key": {
        "ytb_link": "https://www.youtube.com/watch?v=...",
        "title": "Song Title",
        "album": "Album",
        "artists": "Artist 1, Artist 2",
        "album_art": "https://...",
        "release_year": "2000"
      }
    }

    A legacy structured ``file_name`` is also accepted. When it is omitted,
    the output name is generated from ``title``, ``album``, and ``artists``.
    """

    raw_data = _load_json_object(json_path)
    songs: list[Song] = []
    validation_errors: list[str] = []

    for json_key, metadata in raw_data.items():
        if not isinstance(metadata, dict):
            validation_errors.append(f"{json_key!r}: metadata must be a JSON object")
            continue

        json_key_text = str(json_key).strip()
        file_name = str(metadata.get("file_name") or "").strip()

        try:
            if file_name:
                parsed_metadata = parse_file_name_metadata(file_name)
            else:
                title = str(metadata.get("title") or "").strip()
                album = str(metadata.get("album") or "").strip()
                artists = parse_artists(metadata.get("artists"))
                missing = [
                    label
                    for label, value in (
                        ("title", title),
                        ("album", album),
                        ("artists", artists),
                    )
                    if not value
                ]
                if missing:
                    raise ValueError(
                        "missing metadata needed for the automatic file name: "
                        + ", ".join(missing)
                    )
                parsed_metadata = ParsedSongMetadata(
                    title=title,
                    album=album,
                    artists=artists,
                )
                file_name = f"{title} - {album} - {', '.join(artists)}"
        except ValueError as exc:
            validation_errors.append(f"{json_key_text!r}: {exc}")
            continue

        songs.append(
            Song(
                json_key=json_key_text,
                ytb_link=str(metadata.get("ytb_link") or "").strip(),
                file_name=file_name,
                parsed_metadata=parsed_metadata,
                album_art=str(metadata.get("album_art") or "").strip(),
                release_year=str(metadata.get("release_year") or "").strip(),
                start_timestamp=str(metadata.get("start_timestamp") or "00:00").strip(),
                end_timestamp=str(metadata.get("end_timestamp") or "").strip(),
            )
        )

    _raise_if_validation_errors(validation_errors)
    return songs


def load_existing_mp3_tag_jobs(json_path: Path) -> list[ExistingMp3TagJob]:
    """Load metadata tagging jobs for already downloaded MP3 files.

    Expected JSON shape:

    {
      "Song Key": {
        "mp3_file_path": "D:/Songs/Song Title.mp3",
        "title": "Song Title",
        "album": "Album",
        "artists": "Artist 1, Artist 2",
        "album_art": "https://...",
        "release_year": "2000"
      }
    }

    ``title``, ``album`` and ``artists`` may be supplied explicitly in JSON.
    When any of them is missing, the missing values are parsed from the MP3 file
    name using ``<title> - <album> - <artists>``.

    This is intentionally more permissive than the download JSON loader because
    tag-existing mode must support legacy/title-only files such as
    ``Aa Ab Laut Chalen.mp3`` and then rename them to the structured file name.
    """

    raw_data = _load_json_object(json_path)
    jobs: list[ExistingMp3TagJob] = []
    validation_errors: list[str] = []

    for json_key, metadata in raw_data.items():
        if not isinstance(metadata, dict):
            validation_errors.append(f"{json_key!r}: metadata must be a JSON object")
            continue

        json_key_text = str(json_key).strip()
        mp3_file_path_text = str(metadata.get("mp3_file_path") or "").strip()

        if not mp3_file_path_text:
            validation_errors.append(f"{json_key_text!r}: missing mp3_file_path")
            continue

        mp3_file_path = Path(mp3_file_path_text).expanduser()
        if mp3_file_path.suffix.lower() != ".mp3":
            validation_errors.append(f"{json_key_text!r}: mp3_file_path must point to a .mp3 file")
            continue

        try:
            parsed_metadata = _build_metadata_for_existing_mp3(metadata, mp3_file_path.stem)
        except ValueError as exc:
            validation_errors.append(f"{json_key_text!r}: {exc}")
            continue

        jobs.append(
            ExistingMp3TagJob(
                json_key=json_key_text,
                mp3_file_path=mp3_file_path,
                parsed_metadata=parsed_metadata,
                album_art=str(metadata.get("album_art") or "").strip(),
                release_year=str(metadata.get("release_year") or "").strip(),
            )
        )

    _raise_if_validation_errors(validation_errors)
    return jobs


def _build_metadata_for_existing_mp3(
    metadata: dict[str, Any],
    file_name_stem: str,
) -> ParsedSongMetadata:
    """Build metadata for tag-existing mode.

    Explicit JSON fields win. The structured file name is used only as a fallback
    for missing fields, so title-only files can still be renamed and tagged when
    JSON contains title, album and artists.
    """

    explicit_title = str(metadata.get("title") or "").strip()
    explicit_album = str(metadata.get("album") or "").strip()
    explicit_artists = parse_artists(metadata.get("artists"))

    if explicit_title and explicit_album and explicit_artists:
        return ParsedSongMetadata(
            title=explicit_title,
            album=explicit_album,
            artists=explicit_artists,
        )

    try:
        parsed_from_name = parse_file_name_metadata(file_name_stem)
    except ValueError as exc:
        raise ValueError(
            "title, album and artists must be provided explicitly when "
            "mp3_file_path is not already named as "
            "'<title> - <album> - <comma separated artists>.mp3'. "
            f"Filename parse error: {exc}"
        ) from exc

    title = explicit_title or parsed_from_name.title
    album = explicit_album or parsed_from_name.album
    artists = explicit_artists or parsed_from_name.artists

    if not title or not album or not artists:
        raise ValueError(
            "title, album and artists must be provided explicitly or parsable from mp3_file_path"
        )

    return ParsedSongMetadata(title=title, album=album, artists=artists)


def _load_json_object(json_path: Path) -> dict[str, Any]:
    """Read a JSON file and ensure the root value is an object/dictionary."""

    if not json_path.exists():
        raise FileNotFoundError(f"JSON file not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as file:
        raw_data: Any = json.load(file)

    if not isinstance(raw_data, dict):
        raise ValueError("Input JSON must be an object/dictionary of songs.")

    return raw_data


def _raise_if_validation_errors(validation_errors: list[str]) -> None:
    """Raise one readable validation exception for all bad JSON entries."""

    if validation_errors:
        formatted_errors = "\n".join(f"- {error}" for error in validation_errors)
        raise ValueError(f"Invalid song metadata found:\n{formatted_errors}")


def load_videos(json_path: Path) -> list[VideoJob]:
    """Load video download jobs from a JSON file.

    Expected JSON shape:

    {
      "Video Key": {
        "ytb_link": "https://www.youtube.com/watch?v=...",
        "file_name": "Readable output file name",
        "resolution": "1080p"
      }
    }

    The URL can be supplied as ``ytb_link``, ``video_url``, ``youtube_url`` or
    ``url``. ``file_name``/``title`` is optional for videos; when missing, the
    downloader extracts the official YouTube title from yt-dlp metadata and uses
    that as the output name. ``resolution`` is optional; when missing the
    CLI/settings fallback is used. Supported resolution values are normalized
    later by the video downloader, so JSON may use values like ``4K``,
    ``2160p``, ``FHD``, ``1080p``, ``HD``, ``720p``, ``best``, ``ask`` or ``mp3``.
    """

    raw_data = _load_json_object(json_path)
    videos: list[VideoJob] = []
    validation_errors: list[str] = []

    for json_key, metadata in raw_data.items():
        if not isinstance(metadata, dict):
            validation_errors.append(f"{json_key!r}: metadata must be a JSON object")
            continue

        json_key_text = str(json_key).strip()
        ytb_link = (
            str(metadata.get("ytb_link") or "").strip()
            or str(metadata.get("video_url") or "").strip()
            or str(metadata.get("youtube_url") or "").strip()
            or str(metadata.get("url") or "").strip()
        )
        file_name = (
            str(metadata.get("file_name") or "").strip()
            or str(metadata.get("title") or "").strip()
            or None
        )
        resolution = (
            str(metadata.get("resolution") or "").strip()
            or str(metadata.get("quality") or "").strip()
            or None
        )

        if not ytb_link:
            validation_errors.append(f"{json_key_text!r}: missing ytb_link/video_url/youtube_url/url")
            continue

        # file_name/title is intentionally optional for videos. If it is not
        # supplied, the video downloader extracts the official YouTube title
        # from yt-dlp metadata and uses that as the safe output name.

        videos.append(
            VideoJob(
                json_key=json_key_text,
                ytb_link=ytb_link,
                file_name=file_name,
                resolution=resolution,
                start_timestamp=str(metadata.get("start_timestamp") or "00:00").strip(),
                end_timestamp=str(metadata.get("end_timestamp") or "").strip(),
            )
        )

    _raise_if_validation_errors(validation_errors)
    return videos
