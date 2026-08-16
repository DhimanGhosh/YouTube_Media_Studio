from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.services.albums.album_folders import (
    normalize_album_folders,
    resolve_album_folder_successor,
)
from youtube_audio_video_downloader.services.albums.album_names import (
    canonical_album_name,
    normalize_album_name,
)
from youtube_audio_video_downloader.services.media.media_metadata import EditableMediaMetadata


class AlbumNameTest(unittest.TestCase):
    def test_removes_storefront_soundtrack_and_ep_suffixes(self) -> None:
        self.assertEqual(
            normalize_album_name(
                "Byabodhan (Original Motion Picture Soundtrack) - EP"
            ),
            "Byabodhan",
        )
        self.assertEqual(
            normalize_album_name("Byabodhan (Original Motion Picture Soundtrack)"),
            "Byabodhan",
        )
        self.assertEqual(normalize_album_name("Byabodhan - EP"), "Byabodhan")
        self.assertEqual(
            normalize_album_name(
                "I Love You (Original Motion Picture Soundtrack) [Original]"
            ),
            "I Love You",
        )

    def test_preserves_plain_album_name(self) -> None:
        self.assertEqual(normalize_album_name("Byabodhan"), "Byabodhan")

    def test_canonical_album_tag_contains_only_name_and_release_year(self) -> None:
        self.assertEqual(
            canonical_album_name("Byabodhan (2025 film)", "2025-01-01"),
            "Byabodhan (2025)",
        )

    def test_resolves_a_uniquely_renamed_saved_album_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "Bajrangi Bhaijaan (2015)"
            canonical.mkdir()

            resolved = resolve_album_folder_successor(root / "Bajrangi Bhaijaan")

            self.assertEqual(resolved, canonical)

    def test_removes_wikipedia_film_disambiguation(self) -> None:
        self.assertEqual(normalize_album_name("Byabodhan (2025 film)"), "Byabodhan")

    def test_preserves_language_from_wikipedia_film_disambiguation(self) -> None:
        self.assertEqual(
            canonical_album_name("Highway (2014 Bengali film)", "2014"),
            "Highway (Bengali) (2014)",
        )
        self.assertEqual(
            canonical_album_name("Highway (2014 Hindi film)", "2014"),
            "Highway (Hindi) (2014)",
        )

    @patch("youtube_audio_video_downloader.services.albums.album_folders.read_media_metadata")
    def test_renames_and_merges_matching_album_year_folders_without_deleting(
        self, read_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            album="Byabodhan", year="2025"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "Byabodhan"
            second = root / "Byabodhan (2025 film)"
            first.mkdir()
            second.mkdir()
            (first / "Song.mp3").write_bytes(b"first")
            (second / "Song.mp3").write_bytes(b"second")

            repaired = normalize_album_folders(root)

            target = root / "Byabodhan (2025)"
            self.assertIn(target, repaired)
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertEqual(
                {
                    (target / "Song.mp3").read_bytes(),
                    (target / "Song (2).mp3").read_bytes(),
                },
                {b"first", b"second"},
            )

    def test_merges_qualified_folder_without_overwriting_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clean = root / "Byabodhan"
            qualified = root / "Byabodhan (Original Motion Picture Soundtrack) - EP"
            clean.mkdir()
            qualified.mkdir()
            (clean / "Song.mp3").write_bytes(b"existing")
            (qualified / "Song.mp3").write_bytes(b"incoming")
            (qualified / "Another.mp3").write_bytes(b"another")

            repaired = normalize_album_folders(root)

            self.assertEqual(repaired, (clean,))
            self.assertFalse(qualified.exists())
            self.assertEqual((clean / "Song.mp3").read_bytes(), b"existing")
            self.assertEqual((clean / "Song (2).mp3").read_bytes(), b"incoming")
            self.assertEqual((clean / "Another.mp3").read_bytes(), b"another")


if __name__ == "__main__":
    unittest.main()
