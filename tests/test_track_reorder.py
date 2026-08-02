"""Tests for track-number-only album reordering."""

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from mutagen.id3 import TIT2, TRCK
from mutagen.wave import WAVE

from youtube_audio_video_downloader.services.track_reorder import reorder_track_numbers
from youtube_audio_video_downloader.core.file_access import file_in_use_handler


class _FakeAudio(dict):
    def __init__(self, track: str, title: str) -> None:
        super().__init__(tracknumber=[track], title=[title], artist=["Artist"])
        self.saved = False

    def save(self) -> None:
        self.saved = True


class TrackReorderTest(unittest.TestCase):

    def test_locked_track_is_skipped_and_remaining_track_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            folder = Path(temporary_directory)
            locked_path = folder / "Locked.mp3"
            available_path = folder / "Available.mp3"
            locked_path.touch()
            available_path.touch()
            locked = _FakeAudio("1", "Locked")
            available = _FakeAudio("2", "Available")
            locked.save = lambda: (_ for _ in ()).throw(PermissionError("in use"))

            with (
                patch(
                    "youtube_audio_video_downloader.services.track_reorder.MutagenFile",
                    side_effect=[locked, available],
                ),
                file_in_use_handler(lambda _path, _action: None),
            ):
                updated = reorder_track_numbers([locked_path, available_path])

        self.assertEqual(updated, 1)
        self.assertTrue(available.saved)
    def test_only_track_number_changes_and_existing_total_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            folder = Path(temporary_directory)
            first_path = folder / "First.mp3"
            second_path = folder / "Second.mp3"
            first_path.touch()
            second_path.touch()
            first = _FakeAudio("7/12", "First")
            second = _FakeAudio("2/12", "Second")

            with patch(
                "youtube_audio_video_downloader.services.track_reorder.MutagenFile",
                side_effect=[first, second],
            ):
                reorder_track_numbers([first_path, second_path])

        self.assertEqual(first["tracknumber"], ["1/12"])
        self.assertEqual(second["tracknumber"], ["2/12"])
        self.assertEqual(first["title"], ["First"])
        self.assertEqual(first["artist"], ["Artist"])
        self.assertTrue(first.saved)
        self.assertTrue(second.saved)

    def test_all_files_must_be_in_one_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            one = root / "one" / "First.mp3"
            two = root / "two" / "Second.mp3"
            one.parent.mkdir()
            two.parent.mkdir()
            one.touch()
            two.touch()
            with self.assertRaisesRegex(ValueError, "same folder"):
                reorder_track_numbers([one, two])

    def test_wav_audio_frames_and_other_id3_tags_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "Song.wav"
            frames = b"\x01\x02" * 200
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(frames)
            tagged = WAVE(path)
            tagged.add_tags()
            tagged.tags.add(TIT2(encoding=3, text="Untouched title"))
            tagged.tags.add(TRCK(encoding=3, text="9/9"))
            tagged.save()

            reorder_track_numbers([path])

            result = WAVE(path)
            self.assertEqual(str(result.tags.getall("TIT2")[0]), "Untouched title")
            self.assertEqual(str(result.tags.getall("TRCK")[0]), "1/9")
            with wave.open(str(path), "rb") as source:
                self.assertEqual(source.readframes(source.getnframes()), frames)


if __name__ == "__main__":
    unittest.main()
