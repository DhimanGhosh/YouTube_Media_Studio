"""Tests for automatic canonical album-folder consolidation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.services.albums.album_folders import (
    consolidate_audio_in_place,
    find_existing_album_track,
)
from youtube_audio_video_downloader.services.media.media_metadata import EditableMediaMetadata


class AlbumFoldersTest(unittest.TestCase):
    def test_existing_track_is_found_in_canonical_year_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            album = root / "Kismat Konnection (2008)"
            album.mkdir()
            existing = album / "Bakhuda Tumhi Ho.mp3"
            existing.write_bytes(b"existing")
            metadata = EditableMediaMetadata(
                title="Bakhuda Tumhi Ho",
                album="Kismat Konnection (2008)",
                artists="Atif Aslam, Alka Yagnik",
                year="2008",
            )
            with patch(
                "youtube_audio_video_downloader.services.albums.album_folders.read_media_metadata",
                return_value=metadata,
            ):
                found = find_existing_album_track(
                    root,
                    title="Bakhuda Tumhi Ho",
                    album="Kismat Konnection",
                    year="2008",
                )

        self.assertEqual(found, existing)

    def test_auto_consolidation_merges_folder_and_keeps_better_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "Kismat Konnection"
            canonical = root / "Kismat Konnection (2008)"
            legacy.mkdir()
            canonical.mkdir()
            name = "Bakhuda Tumhi Ho - Kismat Konnection (2008) - Atif Aslam.mp3"
            downloaded = legacy / name
            existing = canonical / name
            downloaded.write_bytes(b"new higher quality")
            existing.write_bytes(b"old")
            metadata = EditableMediaMetadata(
                title="Bakhuda Tumhi Ho",
                album="Kismat Konnection (2008)",
                artists="Atif Aslam",
                year="2008",
                artwork_present=True,
            )
            with patch(
                "youtube_audio_video_downloader.services.albums.album_folders.read_media_metadata",
                return_value=metadata,
            ), patch(
                "youtube_audio_video_downloader.services.albums.album_folders._durations_match",
                return_value=True,
            ), patch(
                "youtube_audio_video_downloader.services.albums.album_folders._quality_score",
                side_effect=lambda path: (320000, 1, path.stat().st_size),
            ):
                consolidated = consolidate_audio_in_place(root)

            final = canonical / name
            self.assertTrue(final.is_file())
            self.assertEqual(final.read_bytes(), b"new higher quality")
            self.assertFalse(legacy.exists())
            self.assertEqual(consolidated, (final,))

    def test_scoped_auto_consolidation_does_not_move_an_unrelated_album(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target_source = root / "Kismat Konnection" / "target.mp3"
            other_source = root / "Unrelated Download" / "other.mp3"
            target_source.parent.mkdir()
            other_source.parent.mkdir()
            target_source.write_bytes(b"target")
            other_source.write_bytes(b"other")

            def metadata_for(path: Path) -> EditableMediaMetadata:
                if path.name == "target.mp3":
                    return EditableMediaMetadata(
                        title="Bakhuda Tumhi Ho",
                        album="Kismat Konnection (2008)",
                        artists="Atif Aslam",
                        year="2008",
                    )
                return EditableMediaMetadata(
                    title="Other Song",
                    album="Other Album (2020)",
                    artists="Other Artist",
                    year="2020",
                )

            with patch(
                "youtube_audio_video_downloader.services.albums.album_folders.read_media_metadata",
                side_effect=metadata_for,
            ):
                consolidate_audio_in_place(root, media_paths=[target_source])

            self.assertTrue(other_source.is_file())
            self.assertTrue((root / "Kismat Konnection (2008)").is_dir())
            self.assertFalse(target_source.parent.exists())


if __name__ == "__main__":
    unittest.main()
