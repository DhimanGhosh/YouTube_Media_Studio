"""MP3 metadata tagging utilities."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, TPE2, TRCK, error as ID3Error
from mutagen.mp3 import MP3

from youtube_audio_video_downloader.domain.models import ParsedSongMetadata, Song
from youtube_audio_video_downloader.core.file_access import retry_file_operation
from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names


class MetadataTagger:
    """Apply ID3 tags and optional album art to downloaded MP3 files."""

    def tag_mp3(self, mp3_path: Path, song: Song) -> None:
        """Write title, album, artists, release year and album art into an MP3 file."""

        if not mp3_path.exists():
            raise FileNotFoundError(f"MP3 file not found for tagging: {mp3_path}")

        metadata = song.parsed_metadata
        audio = MP3(mp3_path, ID3=ID3)

        if audio.tags is None:
            try:
                audio.add_tags()
            except ID3Error:
                # Tags may have been created by another process between read/add.
                pass

        tags = audio.tags or ID3()
        artist_text = format_artist_names(", ".join(metadata.artists))
        artists = [part.strip() for part in artist_text.split(",") if part.strip()]

        tags.delall("TIT2")
        tags.delall("TALB")
        tags.delall("TPE1")
        tags.delall("TPE2")
        tags.delall("TDRC")
        tags.delall("TRCK")
        tags.delall("APIC")

        tags.add(TIT2(encoding=3, text=metadata.title))
        tags.add(TALB(encoding=3, text=metadata.album))
        tags.add(TPE1(encoding=3, text=artists))
        tags.add(TPE2(encoding=3, text=artist_text))

        if song.release_year:
            tags.add(TDRC(encoding=3, text=song.release_year))

        track_text = self._format_track_number(song.track_number, song.track_total)
        if track_text:
            tags.add(TRCK(encoding=3, text=track_text))

        album_art = self._download_album_art(song.album_art)
        if album_art is not None:
            image_bytes, mime_type = album_art
            tags.add(
                APIC(
                    encoding=3,
                    mime=mime_type,
                    type=3,
                    desc="Cover",
                    data=image_bytes,
                )
            )

        retry_file_operation(
            mp3_path,
            "writing its song metadata",
            lambda: tags.save(mp3_path, v2_version=3),
        )

    @staticmethod
    def _format_track_number(track_number: int | None, track_total: int | None) -> str:
        """Return an ID3 TRCK-compatible track number string."""

        if track_number is None or track_number <= 0:
            return ""
        if track_total is not None and track_total >= track_number:
            return f"{track_number}/{track_total}"
        return str(track_number)

    @staticmethod
    def _download_album_art(album_art_url: str) -> tuple[bytes, str] | None:
        """Download album art bytes from the JSON URL.

        Tagging continues without artwork if the image URL is empty or fails.
        """

        if not album_art_url:
            return None

        request = Request(
            album_art_url,
            headers={"User-Agent": "Mozilla/5.0"},
        )

        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310 - user-provided metadata URL.
                image_bytes = response.read()
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
        except (OSError, URLError, TimeoutError):
            return None

        guessed_type = content_type or mimetypes.guess_type(album_art_url)[0] or "image/jpeg"
        if guessed_type not in {"image/jpeg", "image/png", "image/webp"}:
            guessed_type = "image/jpeg"

        return image_bytes, guessed_type


def metadata_as_dict(metadata: ParsedSongMetadata) -> dict[str, object]:
    """Return parsed metadata as a JSON-serializable dictionary."""

    return {
        "title": metadata.title,
        "album": metadata.album,
        "artists": metadata.artists,
    }
