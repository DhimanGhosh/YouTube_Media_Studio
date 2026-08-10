"""Completion behavior shared by all downloader batch editors."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtWidgets import QPushButton  # noqa: E402

from youtube_audio_video_downloader.gui.widgets import JsonBatchEditor  # noqa: E402


class BatchEditorCompletionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_successful_entry_collapses_for_every_downloader(self) -> None:
        for kind in ("audio", "video", "album", "jukebox"):
            with self.subTest(kind=kind):
                editor = JsonBatchEditor(kind)
                entry = editor.entries[0]
                entry["fields"]["__name__"].setText("Example")

                editor.disable_completed(("Example",))

                self.assertFalse(entry["section"].toggle.isChecked())
                self.assertEqual(entry["section"].status_label.text(), "COMPLETED")
                self.assertFalse(entry["fields"]["download"].isChecked())

    def test_nested_tracks_collapse_individually_then_collapse_parent(self) -> None:
        editor = JsonBatchEditor("album")
        editor.load_data({
            "Album": {
                "ytb_link": "https://youtu.be/example",
                "tracks": [
                    {"First": {"start": "00:00", "end": "01:00", "artists": "Artist"}},
                    {"Second": {"start": "01:01", "end": "02:00", "artists": "Artist"}},
                ],
            }
        })
        entry = editor.entries[0]
        tracks = entry["fields"]["__tracks__"]

        editor.disable_completed(("Album / First",))

        self.assertFalse(tracks[0]["fields"]["download"].isChecked())
        self.assertFalse(tracks[0]["section"].toggle.isChecked())
        self.assertTrue(tracks[1]["section"].toggle.isChecked())
        self.assertTrue(entry["section"].toggle.isChecked())

        editor.disable_completed(("Album / Second",))

        self.assertFalse(tracks[1]["fields"]["download"].isChecked())
        self.assertFalse(entry["fields"]["download"].isChecked())
        self.assertFalse(entry["section"].toggle.isChecked())

    def test_completed_parent_disables_and_collapses_every_nested_track(self) -> None:
        editor = JsonBatchEditor("album")
        editor.load_data({
            "Album": {
                "tracks": [
                    {"First": {"ytb_link": "https://youtu.be/first"}},
                    {"Second": {"ytb_link": "https://youtu.be/second"}},
                ],
            }
        })
        entry = editor.entries[0]

        editor.disable_completed(("Album",))

        self.assertFalse(entry["fields"]["download"].isChecked())
        self.assertFalse(entry["section"].toggle.isChecked())
        for track in entry["fields"]["__tracks__"]:
            self.assertFalse(track["fields"]["download"].isChecked())
            self.assertFalse(track["section"].toggle.isChecked())
            self.assertEqual(track["section"].status_label.text(), "COMPLETED")

    def test_restored_completed_status_repairs_enabled_expanded_album(self) -> None:
        editor = JsonBatchEditor("album")
        editor.load_data({
            "Previously completed": {
                "download": "true",
                "tracks": [{"Song": {"download": "true"}}],
            }
        })
        entry = editor.entries[0]

        editor.set_statuses({"Previously completed": "Completed"})

        self.assertFalse(entry["fields"]["download"].isChecked())
        self.assertFalse(entry["section"].toggle.isChecked())
        self.assertFalse(
            entry["fields"]["__tracks__"][0]["fields"]["download"].isChecked()
        )

    def test_failed_entry_stays_expanded(self) -> None:
        editor = JsonBatchEditor("audio")
        entry = editor.entries[0]
        entry["fields"]["__name__"].setText("Example")

        editor.disable_completed(("Example",), ("Example",))

        self.assertTrue(entry["section"].toggle.isChecked())
        self.assertEqual(entry["section"].status_label.text(), "NEEDS ATTENTION")

    def test_audio_file_name_is_generated_instead_of_entered(self) -> None:
        editor = JsonBatchEditor("audio")
        entry = editor.entries[0]
        fields = entry["fields"]
        fields["__name__"].setText("Example")
        fields["title"].setText("Song Title")
        fields["album"].setText("Album Name")
        fields["artists"].setText("Artist One, Artist Two")

        payload = editor.data()["Song Title"]

        self.assertNotIn("file_name", payload)
        self.assertEqual(payload["title"], "Song Title")
        self.assertEqual(payload["album"], "Album Name")
        self.assertEqual(payload["artists"], "Artist One, Artist Two")

    def test_audio_uses_title_without_a_visible_name_field(self) -> None:
        editor = JsonBatchEditor("audio")
        entry = editor.entries[0]
        fields = entry["fields"]
        fields["title"].setText("Only Visible Identity")

        self.assertEqual(entry["form"].indexOf(fields["__name__"]), -1)
        self.assertIn("Only Visible Identity", entry["section"].toggle.text())
        self.assertIn("Only Visible Identity", editor.data())

    def test_audio_mode_shows_only_its_relevant_path_field(self) -> None:
        editor = JsonBatchEditor("audio")
        fields = editor.entries[0]["fields"]
        self.assertFalse(fields["mp3_file_path"].isVisibleTo(editor))

        editor.set_audio_mode("tag-existing")

        self.assertTrue(fields["mp3_file_path"].isVisibleTo(editor))
        self.assertFalse(fields["ytb_link"].isVisibleTo(editor))
        self.assertFalse(fields["start_timestamp"].isVisibleTo(editor))
        self.assertFalse(fields["end_timestamp"].isVisibleTo(editor))

    def test_audio_and_video_entries_serialize_their_own_timestamp_ranges(self) -> None:
        audio = JsonBatchEditor("audio")
        audio_fields = audio.entries[0]["fields"]
        audio_fields["title"].setText("Audio clip")
        audio_fields["start_timestamp"].setText("00:10")
        audio_fields["end_timestamp"].setText("00:45")

        video = JsonBatchEditor("video")
        video_fields = video.entries[0]["fields"]
        video_fields["__name__"].setText("Video clip")
        video_fields["start_timestamp"].setText("01:00")
        video_fields["end_timestamp"].setText("01:30")

        self.assertEqual(audio.data()["Audio clip"]["start_timestamp"], "00:10")
        self.assertEqual(audio.data()["Audio clip"]["end_timestamp"], "00:45")
        self.assertEqual(video.data()["Video clip"]["start_timestamp"], "01:00")
        self.assertEqual(video.data()["Video clip"]["end_timestamp"], "01:30")

    def test_populated_entry_replaces_launch_placeholder(self) -> None:
        editor = JsonBatchEditor("jukebox")
        placeholder = editor.entries[0]

        added = editor.add_entry(
            "Compilation",
            {"ytb_link": "https://youtu.be/abcdefghijk"},
        )

        self.assertEqual(editor.entries, [added])
        self.assertNotIn(placeholder, editor.entries)

    def test_populated_entry_keeps_existing_nonblank_entries(self) -> None:
        editor = JsonBatchEditor("jukebox")
        first = editor.add_entry("First", {"ytb_link": "https://youtu.be/abcdefghijk"})
        second = editor.add_entry("Second", {"ytb_link": "https://youtu.be/lmnopqrstuv"})

        self.assertEqual(editor.entries, [first, second])

    def test_auto_extract_starts_after_jukebox_record_is_constructed(self) -> None:
        editor = JsonBatchEditor("jukebox")
        with patch.object(editor, "_extract_entry_tracks") as extract:
            added = editor.add_entry(
                "Compilation",
                {"ytb_link": "https://youtu.be/abcdefghijk"},
                auto_extract=True,
            )
            self.app.processEvents()

        extract.assert_called_once_with(added)

    def test_extracted_track_auto_enrichment_starts_year_and_cover(self) -> None:
        editor = JsonBatchEditor("jukebox")
        entry = editor.add_entry("Compilation")
        fields = entry["fields"]
        track = editor._add_track(
            fields["__tracks_layout__"],
            fields["__tracks__"],
            "Song",
            {"album": "Movie Album", "artists": "Singer"},
        )

        with (
            patch.object(editor, "_find_release_year") as find_year,
            patch.object(editor, "_find_album_art") as find_cover,
        ):
            editor._auto_enrich_track(track)

        find_year.assert_called_once()
        find_cover.assert_called_once()

    def test_jukebox_track_has_manual_album_and_artist_enrichment_buttons(self) -> None:
        editor = JsonBatchEditor("jukebox")
        entry = editor.add_entry("Compilation")
        fields = entry["fields"]
        track = editor._add_track(
            fields["__tracks_layout__"],
            fields["__tracks__"],
            "Song",
        )

        self.assertEqual(
            track["fields"]["__find_album_button__"].text(),
            "Find album",
        )
        self.assertEqual(
            track["fields"]["__find_artists_button__"].text(),
            "Find artists",
        )

    def test_missing_album_or_artists_starts_track_metadata_enrichment(self) -> None:
        editor = JsonBatchEditor("jukebox")
        entry = editor.add_entry("Compilation")
        fields = entry["fields"]
        track = editor._add_track(
            fields["__tracks_layout__"],
            fields["__tracks__"],
            "Song",
            {"artists": "Unknown"},
        )

        with patch.object(editor, "_find_track_metadata") as enrich:
            editor._auto_enrich_track(track)

        enrich.assert_called_once_with(track, target="all")

    def test_jukebox_artist_field_uses_shared_formatter_when_added_and_saved(self) -> None:
        editor = JsonBatchEditor("jukebox")
        entry = editor.add_entry("Compilation")
        fields = entry["fields"]
        track = editor._add_track(
            fields["__tracks_layout__"],
            fields["__tracks__"],
            "Song",
            {
                "album": "Movie",
                "artists": "Javed Ali & Sonu Nigam and Shreya Ghoshal",
            },
        )

        self.assertEqual(
            track["fields"]["artists"].text(),
            "Javed Ali, Sonu Nigam, Shreya Ghoshal",
        )
        track["fields"]["artists"].setText("Singer One & Singer Two")

        payload = editor.data()["Compilation"]["tracks"][0]["Song"]

        self.assertEqual(payload["artists"], "Singer One, Singer Two")

    def test_album_auto_fill_reports_no_new_data_when_result_changes_nothing(self) -> None:
        editor = JsonBatchEditor("album")
        entry = editor.add_entry("Bhoot Bangla")
        logs: list[str] = []
        editor.log_requested.connect(logs.append)

        editor._apply_album_auto_fill_result(
            "Bhoot Bangla",
            {"errors": ["year: not found", "cover: not found"]},
            entry["fields"],
            entry["section"],
            QPushButton(),
        )

        self.assertEqual(entry["section"].status_label.text(), "NO NEW DATA")
        self.assertTrue(
            any("No usable metadata found" in line for line in logs),
            logs,
        )
        self.assertFalse(entry["fields"]["release_year"].text())
        self.assertFalse(entry["fields"]["album_art"].text())
        self.assertFalse(entry["fields"]["ytb_link"].text())

    def test_album_auto_fill_logs_visible_field_updates(self) -> None:
        editor = JsonBatchEditor("album")
        entry = editor.add_entry("Album")
        logs: list[str] = []
        editor.log_requested.connect(logs.append)

        editor._apply_album_auto_fill_result(
            "Album",
            {"year": "1999", "album_art": "https://example.test/cover.jpg"},
            entry["fields"],
            entry["section"],
            QPushButton(),
        )

        self.assertEqual(entry["section"].status_label.text(), "METADATA FOUND")
        self.assertEqual(entry["fields"]["release_year"].text(), "1999")
        self.assertEqual(
            entry["fields"]["album_art"].text(),
            "https://example.test/cover.jpg",
        )
        self.assertTrue(any("release year, album art" in line for line in logs), logs)


if __name__ == "__main__":
    unittest.main()
