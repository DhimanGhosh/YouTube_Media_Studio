"""Tests for the unified Edit File metadata service."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mutagen.id3 import APIC, COMM, ID3, TALB, TCON, TIT2, TPE1, TPE2, TPOS, TXXX

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.gui.operations import execute_operation
from youtube_audio_video_downloader.services.media_editor import edit_media_file
from youtube_audio_video_downloader.services.media_metadata import (
    read_media_metadata,
    replace_media_metadata,
)


@unittest.skipUnless(shutil.which("ffmpeg"), "needs FFmpeg")
class MediaMetadataTest(unittest.TestCase):
    def test_reads_and_atomically_replaces_common_mp3_tags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "song.mp3"
            self._make_audio(source)
            tags = ID3(source)
            tags.add(TIT2(encoding=3, text="Old title"))
            tags.add(TALB(encoding=3, text="Old album"))
            tags.add(TPE1(encoding=3, text=["Artist One", "Artist Two"]))
            tags.add(TPE2(encoding=3, text="Existing album artist"))
            tags.add(TPOS(encoding=3, text="1/2"))
            tags.add(TCON(encoding=3, text="Existing genre"))
            tags.add(COMM(encoding=3, lang="eng", desc="", text="Existing comment"))
            tags.add(TXXX(encoding=3, desc="Keep custom", text="still here"))
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=b"\xff\xd8\xff\xd9"))
            tags.save(source)

            loaded = read_media_metadata(source)
            self.assertEqual(loaded.title, "Old title")
            self.assertEqual(loaded.album, "Old album")
            self.assertEqual(loaded.artists, "Artist One, Artist Two")
            self.assertTrue(loaded.artwork_present)

            replace_media_metadata(
                source,
                {
                    "title": "New title", "album": "New album", "artists": "New Artist",
                    "year": "2026", "track_number": "2", "track_total": "9",
                },
                remove_artwork=True,
            )

            updated = read_media_metadata(source)
            self.assertEqual(updated.title, "New title")
            self.assertEqual(updated.track_number, "2")
            self.assertEqual(updated.track_total, "9")
            self.assertFalse(updated.artwork_present)
            resulting_tags = ID3(source)
            self.assertEqual(resulting_tags.getall("TXXX:Keep custom")[0].text, ["still here"])
            self.assertEqual(resulting_tags["TPE2"].text, ["Existing album artist"])
            self.assertEqual(resulting_tags["TPOS"].text, ["1/2"])
            self.assertEqual(resulting_tags["TCON"].text, ["Existing genre"])
            self.assertEqual(resulting_tags.getall("COMM")[0].text, ["Existing comment"])

    @patch("youtube_audio_video_downloader.gui.operations.edit_media_file")
    def test_unified_gui_operation_routes_metadata_only_edit(self, edit_mock) -> None:
        edit_mock.return_value = [Path("song.mp3")]
        summary = execute_operation(
            "edit_media",
            {"action": "metadata", "input_path": "song.mp3", "metadata": {"title": "New"}},
            CancellationToken(),
        )
        self.assertEqual(summary.operation, "edit_media")
        self.assertEqual(summary.tagged, 1)
        self.assertEqual(summary.downloaded, 0)
        self.assertEqual(summary.completed_items, ("song.mp3",))

    @unittest.skipUnless(shutil.which("ffprobe"), "needs FFprobe")
    def test_trim_and_metadata_are_applied_as_one_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp3"
            output = Path(directory) / "edited.mp3"
            self._make_audio(source)
            results = edit_media_file(
                source,
                "trim",
                {"title": "Trimmed title", "artists": "Edited Artist"},
                start_timestamp="00:00",
                end_timestamp="00:00.5",
                output_path=output,
            )
            self.assertEqual(results, [output])
            self.assertTrue(source.is_file())
            self.assertEqual(read_media_metadata(output).title, "Trimmed title")

    def test_metadata_edit_renames_audio_from_updated_tags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "old name.mp3"
            self._make_audio(source)
            results = edit_media_file(
                source,
                "metadata",
                {
                    "title": "New Title", "album": "New Album",
                    "artists": "Artist One, Artist Two", "year": "2026",
                },
            )
            expected = Path(directory) / "New Title - New Album - Artist One, Artist Two.mp3"
            self.assertEqual(results, [expected])
            self.assertTrue(expected.is_file())
            self.assertFalse(source.exists())

    @patch("youtube_audio_video_downloader.services.media_metadata.urlopen")
    def test_artwork_accepts_an_https_image_url(self, urlopen_mock) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit: int) -> bytes:
                return b"\x89PNG\r\n\x1a\nminimal-test-image"

        urlopen_mock.return_value = Response()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "song.mp3"
            self._make_audio(source)
            replace_media_metadata(
                source,
                {"title": "Song", "album": "Album", "artists": "Artist"},
                artwork_path="https://example.com/cover.png",
            )
            self.assertTrue(read_media_metadata(source).artwork_present)
            self.assertEqual(urlopen_mock.call_count, 1)

    @staticmethod
    def _make_audio(path: Path) -> None:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=1", "-codec:a", "libmp3lame", str(path),
            ],
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
