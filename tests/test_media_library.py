"""Tests for local media-library discovery and faceted filtering."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.services.media_library import (
    LibraryItem, filter_library, scan_library, split_artists,
)
from youtube_audio_video_downloader.services.media_metadata import EditableMediaMetadata


def item(title: str, artist: str, album: str, year: int | None, media_type: str = "audio") -> LibraryItem:
    return LibraryItem(
        path=f"C:/{title}.mp3", title=title, album=album, artists=artist,
        year=year, duration_ms=1000, media_type=media_type, modified_ns=1,
    )


class MediaLibraryTest(unittest.TestCase):
    def test_query_searches_all_metadata_fields(self) -> None:
        items = [item("Blue Sky", "One Artist", "Colors", 1998), item("Other", "Second", "Later", 2021)]
        self.assertEqual([value.title for value in filter_library(items, query="colors 1998")], ["Blue Sky"])

    def test_multiple_artists_and_year_range_form_one_queue(self) -> None:
        items = [
            item("One", "Alpha", "First", 1995),
            item("Two", "Beta & Guest", "Second", 2002),
            item("Three", "Gamma", "Third", 2015),
        ]
        result = filter_library(items, artists=["Alpha", "Beta"], year_from=1990, year_to=2005)
        self.assertEqual([value.title for value in result], ["One", "Two"])

    def test_artist_splitting_supports_common_separators(self) -> None:
        self.assertEqual(split_artists("One, Two & Three / Four"), ["One", "Two", "Three", "Four"])

    def test_scan_ignores_non_media_and_uses_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            song = root / "track.mp3"
            song.write_bytes(b"placeholder")
            (root / "notes.txt").write_text("no", encoding="utf-8")
            metadata = EditableMediaMetadata(title="Title", album="Album", artists="Artist", year="Released 2001")
            with patch("youtube_audio_video_downloader.services.media_library.read_media_metadata", return_value=metadata), patch("youtube_audio_video_downloader.services.media_library.MutagenFile", return_value=None):
                result = scan_library([root])
        self.assertEqual(len(result), 1)
        self.assertEqual(
            (result[0].title, result[0].album, result[0].year),
            ("Title", "Album (2001)", 2001),
        )

    def test_scan_does_not_append_an_existing_album_year_twice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "track.mp3").write_bytes(b"placeholder")
            metadata = EditableMediaMetadata(
                title="Title", album="Album (2001)", artists="Artist", year="2001"
            )
            with patch(
                "youtube_audio_video_downloader.services.media_library.read_media_metadata",
                return_value=metadata,
            ), patch(
                "youtube_audio_video_downloader.services.media_library.MutagenFile",
                return_value=None,
            ):
                result = scan_library([root])

        self.assertEqual(result[0].album, "Album (2001)")


if __name__ == "__main__":
    unittest.main()
