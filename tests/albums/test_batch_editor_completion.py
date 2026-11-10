"""Completion behavior shared by all downloader batch editors."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtWidgets import QCheckBox  # noqa: E402
from PyQt6.QtWidgets import QPushButton  # noqa: E402

from youtube_audio_video_downloader.gui.components.widgets import (  # noqa: E402
    AlbumMetadataAutoFiller,
    JsonBatchEditor,
)


class BatchEditorCompletionTest(unittest.TestCase):
    def test_failed_album_shows_specific_reason_and_retry_instruction(self):
        editor = JsonBatchEditor("album")
        entry = editor.entries[0]
        entry["fields"]["__name__"].setText("Example")
        editor.disable_completed((), ("Example",), {"Example": "Source video is unavailable"})
        self.assertIn("Source video is unavailable", entry["section"].failure_detail.text())
        self.assertIn("retry", entry["section"].failure_detail.text())
        self.assertFalse(entry["section"].failure_detail.isHidden())
        editor.disable_completed(("Example",))
        self.assertTrue(entry["section"].failure_detail.isHidden())

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

    def test_video_link_row_precedes_file_name_for_auto_fill(self) -> None:
        editor = JsonBatchEditor("video")
        entry = editor.entries[0]
        form = entry["form"]

        link_row, _link_role = form.getWidgetPosition(entry["fields"]["ytb_link"])
        name_row, _name_role = form.getWidgetPosition(entry["fields"]["__name__"])

        self.assertLess(link_row, name_row)
        self.assertEqual(form.labelForField(entry["fields"]["ytb_link"]).text(), "Ytb Link")
        self.assertEqual(form.labelForField(entry["fields"]["__name__"]).text(), "File name")

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

    def test_album_failure_preserves_download_selection(self) -> None:
        editor = JsonBatchEditor("album")
        editor.load_data({"The Body": {
            "download": " off ",
            "tracks": [{"Song": {"download": False}}],
        }})

        editor.disable_completed((), ("The Body / 01. Song",))

        values = editor.data()["The Body"]
        self.assertEqual(values["download"], "false")
        self.assertEqual(values["tracks"][0]["Song"]["download"], "false")
        self.assertEqual(editor.entries[0]["section"].status_label.text(), "NEEDS ATTENTION")

    def test_collapsed_album_drag_reorders_layout_and_saved_data(self) -> None:
        editor = JsonBatchEditor("album")
        editor.load_data({"First": {}, "Second": {}, "Third": {"download": False}})
        for record in editor.entries:
            record["section"].set_expanded(False)
        section = editor.entries[2]["section"]
        self.assertFalse(section.drag_handle.isHidden())
        self.assertEqual(section.drag_handle.cursor().shape(), Qt.CursorShape.OpenHandCursor)
        section.set_expanded(True)
        self.assertTrue(section.drag_handle.isHidden())
        section.set_expanded(False)
        section.set_status("Needs attention")
        editor.show()
        self.app.processEvents()
        order_changes = []
        editor.order_changed.connect(lambda: order_changes.append(list(editor.data())))

        event = Mock()
        event.source.return_value = section.drag_handle
        event.position.return_value = QPointF(0, editor.entries[0]["section"].geometry().top())
        editor.dragEnterEvent(event)
        editor.dropEvent(event)

        self.assertEqual(order_changes, [["Third", "First", "Second"]])
        self.assertEqual(event.acceptProposedAction.call_count, 2)
        self.assertIs(editor.entries_layout.itemAt(0).widget(), section)
        self.assertFalse(section.toggle.isChecked())
        self.assertEqual(section.status_label.text(), "NEEDS ATTENTION")
        payload = json.loads(json.dumps(editor.data()))
        editor.load_data(payload)
        self.assertEqual(list(editor.data()), ["Third", "First", "Second"])
        self.assertEqual(editor.data()["Third"]["download"], "false")

    def test_length_notice_survives_completion_and_clears_when_link_changes(self) -> None:
        editor = JsonBatchEditor("album")
        notice = "Wikipedia lists 03:29. Matched YouTube audio runs 04:15."
        editor.load_data({"Hate Story 2": {"tracks": [{"Pink Lips": {
            "ytb_link": "https://youtu.be/pink",
            "length_notice": notice,
        }}]}})
        track = editor.entries[0]["fields"]["__tracks__"][0]

        editor.disable_completed(("Hate Story 2",))
        badge = track["section"].length_badge
        self.assertEqual(badge.text(), "Wiki length differs")
        self.assertEqual(badge.toolTip(), notice)
        self.assertFalse(badge.isHidden())
        self.assertEqual(editor.data()["Hate Story 2"]["tracks"][0]["Pink Lips"]["length_notice"], notice)

        track["fields"]["ytb_link"].setText("https://youtu.be/replacement")

        self.assertTrue(badge.isHidden())
        self.assertNotIn("length_notice", editor.data()["Hate Story 2"]["tracks"][0]["Pink Lips"])

    def test_collapsed_track_checkbox_syncs_with_expanded_download_field(self) -> None:
        editor = JsonBatchEditor("album")
        editor.load_data({"Album": {"tracks": [{"Track": {"download": False}}]}})
        track = editor.entries[0]["fields"]["__tracks__"][0]
        section = track["section"]
        header_download = next(box for box in section.findChildren(QCheckBox) if box.text() == "Download")
        self.assertTrue(header_download.isHidden())

        section.set_expanded(False)
        self.assertFalse(header_download.isHidden())
        header_download.click()

        self.assertFalse(section.toggle.isChecked())
        self.assertTrue(track["fields"]["download"].isChecked())
        self.assertEqual(editor.data()["Album"]["tracks"][0]["Track"]["download"], "true")

        section.set_expanded(True)
        track["fields"]["download"].setChecked(False)
        self.assertTrue(header_download.isHidden())
        self.assertFalse(header_download.isChecked())

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

    def test_album_accepts_legacy_local_source_alias_as_first_class_source(self) -> None:
        editor = JsonBatchEditor("album")

        entry = editor.add_entry("Local album", {"source_file": "C:/Media/album.mp4"})
        fields = entry["fields"]

        self.assertEqual(fields["ytb_link"].text(), "C:/Media/album.mp4")
        self.assertEqual(fields["__source_mode__"].currentData(), "local")
        self.assertFalse(fields["__extract_button__"].isEnabled())
        self.assertIn("original source file is preserved", fields["__extract_button__"].toolTip())
        self.assertEqual(editor.data()["Local album"]["ytb_link"], "C:/Media/album.mp4")

    def test_jukebox_source_mode_switches_between_browse_and_youtube_search(self) -> None:
        editor = JsonBatchEditor("jukebox")
        entry = editor.add_entry("Compilation", {"ytb_link": "https://youtu.be/abcdefghijk"})
        fields = entry["fields"]
        source_mode = fields["__source_mode__"]

        self.assertEqual(source_mode.currentData(), "youtube")
        self.assertFalse(fields["__source_search__"].isHidden())
        source_mode.setCurrentIndex(source_mode.findData("local"))

        self.assertTrue(fields["__source_search__"].isHidden())
        self.assertFalse(fields["__extract_button__"].isEnabled())

    def test_album_auto_fill_switches_a_local_source_back_to_youtube_mode(self) -> None:
        editor = JsonBatchEditor("album")
        entry = editor.add_entry("Album", {"local_file": "C:/Media/album.mp4"})
        fields = entry["fields"]

        with patch.object(editor, "_extract_youtube_tracks"):
            editor._apply_album_auto_fill_result(
                "Album",
                {"youtube": {"url": "https://youtu.be/abcdefghijk"}},
                fields,
                entry["section"],
                QPushButton(),
            )
            self.app.processEvents()

        self.assertEqual(fields["__source_mode__"].currentData(), "youtube")
        self.assertTrue(fields["__extract_button__"].isEnabled())
        self.assertEqual(fields["ytb_link"].text(), "https://youtu.be/abcdefghijk")

    def test_each_album_has_default_off_individual_track_search_switch(self) -> None:
        editor = JsonBatchEditor("album")
        first = editor.add_entry("First album")
        second = editor.add_entry("Second album")

        self.assertFalse(first["fields"]["__individual_track_search__"].isChecked())
        self.assertFalse(second["fields"]["__individual_track_search__"].isChecked())
        self.assertIn(
            "timestamped full-album",
            first["fields"]["__source_search__"].toolTip(),
        )

    def test_individual_track_results_replace_jukebox_source_and_track_rows(self) -> None:
        editor = JsonBatchEditor("album")
        entry = editor.add_entry(
            "Album",
            {
                "ytb_link": "https://youtu.be/oldjukebox",
                "tracks": [{"Old row": {"start": "00:00"}}],
            },
        )
        fields = entry["fields"]
        fields["__individual_track_search__"].setChecked(True)

        editor._apply_album_auto_fill_result(
            "Album",
            {
                "individual_tracks": [
                    {"First song": {"ytb_link": "https://youtu.be/firsttrack"}},
                    {"Second song": {"ytb_link": "https://youtu.be/secondtrk"}},
                ]
            },
            fields,
            entry["section"],
            QPushButton(),
        )

        self.assertFalse(fields["ytb_link"].text())
        self.assertEqual(
            [track["fields"]["__name__"].text() for track in fields["__tracks__"]],
            ["First song", "Second song"],
        )

    def test_default_auto_fill_does_not_fall_back_to_individual_tracks(self) -> None:
        filler = AlbumMetadataAutoFiller("Album", "2001")

        with (
            patch(
                "youtube_audio_video_downloader.gui.components.widgets.find_album_art",
                return_value="cover",
            ),
            patch(
                "youtube_audio_video_downloader.gui.components.widgets.find_album_jukebox_video",
                side_effect=LookupError("not found"),
            ),
            patch(
                "youtube_audio_video_downloader.gui.components.widgets.find_individual_album_tracks"
            ) as individual_search,
        ):
            filler.run()

        individual_search.assert_not_called()

    def test_enabled_auto_fill_searches_individual_tracks_without_jukebox_lookup(self) -> None:
        filler = AlbumMetadataAutoFiller("Album", "2001", individual_tracks=True)

        with (
            patch(
                "youtube_audio_video_downloader.gui.components.widgets.find_album_art",
                return_value="cover",
            ),
            patch(
                "youtube_audio_video_downloader.gui.components.widgets.find_album_jukebox_video"
            ) as jukebox_search,
            patch(
                "youtube_audio_video_downloader.gui.components.widgets.find_individual_album_tracks",
                return_value=[{"Song": {"ytb_link": "https://youtu.be/abcdefghijk"}}],
            ) as individual_search,
        ):
            filler.run()

        individual_search.assert_called_once()
        jukebox_search.assert_not_called()

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
