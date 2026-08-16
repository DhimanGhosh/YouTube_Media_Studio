"""Headless interaction tests for the media-library player workspace."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import (  # noqa: E402
    QBuffer, QByteArray, QEvent, QIODevice, QPoint, QPointF, QRect, QRectF, QSettings,
    QSize, Qt, QTimer,
)
from PyQt6.QtGui import QColor, QIcon, QPixmap, QWheelEvent  # noqa: E402
from PyQt6.QtMultimedia import QAudioBuffer, QAudioFormat, QMediaPlayer  # noqa: E402
from PyQt6.QtTest import QSignalSpy, QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QListWidget, QMenu, QStackedWidget  # noqa: E402

from youtube_audio_video_downloader.gui.media.media_player import (  # noqa: E402
    ArtistRepairDialog,
    EMBEDDED_VIDEO_MAX_HEIGHT,
    EMBEDDED_VIDEO_MIN_HEIGHT,
    FULLSCREEN_VIDEO_MAX_HEIGHT,
    MediaLibraryPage,
    VIDEO_TABLE_ROW_HEIGHT,
    VIDEO_THUMBNAIL_SIZE,
)
from youtube_audio_video_downloader.gui.components.widgets import (  # noqa: E402
    BlankClickSelectionFilter,
)
from youtube_audio_video_downloader.services.ai.library_recommendations import (  # noqa: E402
    LibraryRecommendation,
)
from youtube_audio_video_downloader.services.media.artist_canonicalizer import (  # noqa: E402
    ArtistRenameSuggestion,
)
from youtube_audio_video_downloader.services.media.media_library import LibraryItem  # noqa: E402
from youtube_audio_video_downloader.services.media.media_playlists import (  # noqa: E402
    decode_playlists,
)
from youtube_audio_video_downloader.services.media.remote_media import (  # noqa: E402
    RemoteMediaServer,
    media_id,
)


def media(name: str, year: int, duration: int) -> LibraryItem:
    return LibraryItem(
        path=f"C:/{name}.mp3",
        title=name,
        album="Test Album",
        artists="Test Artist",
        year=year,
        duration_ms=duration,
        media_type="audio",
        modified_ns=1,
    )


def wait_until(predicate: Callable[[], bool], timeout_ms: int = 2_000) -> bool:
    """Process Qt events until a background UI condition is satisfied or times out."""

    deadline = time.monotonic() + timeout_ms / 1_000
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        QTest.qWait(10)
    return True


class MediaPlayerPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.environment_patch = patch.dict(
            os.environ,
            {"NVIDIA_API_KEY": "", "YMS_DISABLE_REMOTE_ACCESS": "1"},
        )
        self.environment_patch.start()
        self.temporary = tempfile.TemporaryDirectory()
        settings = QSettings(
            str(Path(self.temporary.name) / "player.ini"),
            QSettings.Format.IniFormat,
        )
        self.page = MediaLibraryPage(settings)
        self.page.items = [
            media("Short", 2005, 60_000),
            media("Long", 1999, 600_000),
        ]
        self.page.apply_filters()

    def tearDown(self) -> None:
        self.page.shutdown()
        self.page.deleteLater()
        self.app.processEvents()
        self.temporary.cleanup()
        self.environment_patch.stop()

    def test_every_column_is_sortable_and_user_resizable_after_initial_sizing(self) -> None:
        header = self.page.table.horizontalHeader()
        for column in range(self.page.table.columnCount()):
            self.assertEqual(
                header.sectionResizeMode(column),
                header.ResizeMode.Interactive,
            )
            self.assertLess(self.page.table.columnWidth(column), 500)
            self.page.table.sortItems(column, Qt.SortOrder.AscendingOrder)
            self.page.table.sortItems(column, Qt.SortOrder.DescendingOrder)
        self.page.table.sortItems(6, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.page.table.item(0, 0).text(), "Short")
        self.page.table.sortItems(4, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.page.table.item(0, 0).text(), "Long")

    def test_playlist_and_queue_drawers_are_horizontally_resizable(self) -> None:
        self.assertIs(self.page.drawer_splitter.widget(0), self.page.playlist_drawer)
        self.assertIs(self.page.drawer_splitter.widget(2), self.page.queue_drawer)
        self.assertEqual(self.page.playlist_drawer.maximumWidth(), 16_777_215)
        self.assertEqual(self.page.queue_drawer.maximumWidth(), 16_777_215)

        self.page.resize(2400, 900)
        self.page.show()
        self.page.playlist_toggle_button.setChecked(True)
        self.page.queue_toggle_button.setChecked(True)
        QTest.qWait(20)
        self.page.drawer_splitter.setSizes([280, 760, 360])
        self.page._save_drawer_widths(0, 0)

        sizes = self.page.drawer_splitter.sizes()
        self.assertGreater(sizes[0], 180)
        self.assertGreater(sizes[2], 180)
        self.assertEqual(
            self.page.settings.value("library/playlist_drawer_width", type=int),
            sizes[0],
        )
        self.assertEqual(
            self.page.settings.value("library/queue_drawer_width", type=int),
            sizes[2],
        )

    def test_artist_and_track_sections_are_horizontally_resizable(self) -> None:
        self.assertIs(
            self.page.artist_track_splitter.widget(0).findChild(QListWidget),
            self.page.facets,
        )
        self.assertIs(
            self.page.artist_track_splitter.widget(1).findChild(QStackedWidget),
            self.page.media_view_stack,
        )
        self.page.artist_track_splitter.setSizes([310, 890])
        self.page.artist_track_splitter.splitterMoved.emit(310, 1)
        self.assertEqual(
            self.page.settings.value("library/artist_pane_width", type=int),
            self.page.artist_track_splitter.sizes()[0],
        )

    def test_browser_and_embedded_player_are_vertically_resizable(self) -> None:
        splitter = self.page.browser_player_splitter
        self.assertIs(splitter.widget(0), self.page.library_splitter)
        self.assertIs(splitter.widget(1), self.page._player_host)
        self.assertFalse(splitter.childrenCollapsible())

        self.page.resize(1400, 1000)
        self.page.show()
        QTest.qWait(20)
        splitter.setSizes([560, 360])
        splitter.splitterMoved.emit(560, 1)

        self.assertGreater(splitter.sizes()[1], 150)
        self.assertEqual(
            self.page.settings.value("library/player_panel_height", type=int),
            splitter.sizes()[1],
        )
        self.assertGreater(EMBEDDED_VIDEO_MAX_HEIGHT, 520)

    def test_folder_and_search_controls_share_one_thirty_seventy_row(self) -> None:
        self.page.resize(1400, 900)
        self.page.show()
        QTest.qWait(20)

        self.assertEqual(
            self.page.folder_controls.mapTo(self.page, QPoint(0, 0)).y(),
            self.page.search_controls.mapTo(self.page, QPoint(0, 0)).y(),
        )
        combined = self.page.folder_controls.width() + self.page.search_controls.width()
        self.assertAlmostEqual(
            self.page.folder_controls.width() / combined,
            0.3,
            delta=0.08,
        )

    def test_artist_repair_review_allows_editing_the_proposed_name(self) -> None:
        dialog = ArtistRepairDialog(
            [ArtistRenameSuggestion("K.K.", "KK", 12)],
            self.page,
            artist_values=("K.K.", "Vishal, Shekhar", "Vishal & Shekhar"),
        )
        try:
            dialog.show()
            QTest.qWait(20)
            replacement_editor = dialog._replacement_editors[0]
            replacement_editor.setText("Kumar Sanu")
            self.assertEqual(dialog.replacements(), {"K.K.": "Kumar Sanu"})
            self.assertFalse(
                bool(dialog.table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable)
            )
            self.assertTrue(
                dialog.table.cellWidget(0, 1).rect().contains(
                    replacement_editor.geometry()
                )
            )

            dialog.add_replacement_button.click()
            source_editor = dialog._source_editors[1]
            source_editor.setText("Vishal, Shekhar")
            dialog._replacement_editors[1].setText(
                "Vishal Dadlani, Shekhar Ravjiani"
            )
            self.assertEqual(dialog.table.item(1, 2).text(), "2")
            self.assertEqual(
                dialog.replacements(),
                {
                    "K.K.": "Kumar Sanu",
                    "Vishal, Shekhar": "Vishal Dadlani, Shekhar Ravjiani",
                },
            )
        finally:
            dialog.deleteLater()

    def test_track_columns_resize_for_every_new_result_set(self) -> None:
        short = media("Compact", 2005, 60_000)
        long_artist = "An exceptionally long artist name " * 8
        long = LibraryItem(
            "C:/Wide.mp3",
            "Wide",
            "Test Album",
            long_artist,
            2005,
            60_000,
            "audio",
            1,
        )

        self.page._populate_table(
            self.page.table,
            [short],
            include_album_and_type=True,
        )
        compact_width = self.page.table.columnWidth(2)
        self.page._populate_table(
            self.page.table,
            [long],
            include_album_and_type=True,
        )
        expanded_width = self.page.table.columnWidth(2)
        self.page._populate_table(
            self.page.table,
            [short],
            include_album_and_type=True,
        )

        self.assertGreater(expanded_width, compact_width)
        self.assertLess(self.page.table.columnWidth(2), expanded_width)

        self.page._populate_table(
            self.page.album_tracks,
            [short],
            include_album_and_type=False,
        )
        album_compact_width = self.page.album_tracks.columnWidth(1)
        self.page._populate_table(
            self.page.album_tracks,
            [long],
            include_album_and_type=False,
        )
        self.assertGreater(
            self.page.album_tracks.columnWidth(1),
            album_compact_width,
        )

        video_index = self.page.media_type_filter.findData("video")
        self.page.media_type_filter.blockSignals(True)
        self.page.media_type_filter.setCurrentIndex(video_index)
        self.page.media_type_filter.blockSignals(False)
        long_video = LibraryItem(
            "C:/Long clip.mp4",
            "A previously very long video title " * 7,
            "Test Album",
            "Test Artist",
            2024,
            60_000,
            "video",
            1,
        )
        short_video = LibraryItem(
            "C:/Long clip.mp4",
            "Short title",
            "Test Album",
            "Test Artist",
            2024,
            60_000,
            "video",
            2,
        )
        self.page._populate_table(
            self.page.table, [long_video], include_album_and_type=True
        )
        long_video_width = self.page.table.columnWidth(0)
        self.page._populate_table(
            self.page.table, [short_video], include_album_and_type=True
        )
        self.assertLess(self.page.table.columnWidth(0), long_video_width)
        self.assertLess(self.page.table.columnWidth(0), 340)

    def test_library_table_displays_every_match_beyond_previous_250_row_cap(self) -> None:
        matches = [media(f"Song {index:04d}", 2000, 60_000) for index in range(876)]

        self.page._apply_search_results(matches)

        self.assertEqual(self.page.table.rowCount(), 876)
        self.assertEqual(self.page.match_status.text(), "876 match(es)")
        displayed_paths = {
            str(
                self.page.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            )
            for row in range(self.page.table.rowCount())
        }
        self.assertEqual(displayed_paths, {item.path for item in matches})

    def test_media_type_selector_separates_music_and_video_browsing(self) -> None:
        video = LibraryItem(
            "C:/Movie.mp4", "Movie", "Test Movie", "Test Artist",
            2024, 60_000, "video", 1,
        )
        self.page.items = [*self.page.items, video]

        self.page.media_type_filter.setCurrentIndex(
            self.page.media_type_filter.findData("video")
        )
        self.assertTrue(
            wait_until(
                lambda: self.page.filtered == [video]
                and self.page.media_results_label.text() == "Videos"
            )
        )
        self.assertTrue(self.page.library_splitter.widget(1).isHidden())

        self.page.media_type_filter.setCurrentIndex(
            self.page.media_type_filter.findData("audio")
        )
        self.assertTrue(
            wait_until(
                lambda: len(self.page.filtered) == 2
                and all(item.media_type == "audio" for item in self.page.filtered)
            )
        )
        self.assertEqual(self.page.media_results_label.text(), "Music")
        self.assertFalse(self.page.library_splitter.widget(1).isHidden())

    def test_table_refresh_preserves_selected_song_by_path_after_sorting(self) -> None:
        short_row = next(
            row
            for row in range(self.page.table.rowCount())
            if self.page.table.item(row, 0).text() == "Short"
        )
        self.page.table.selectRow(short_row)

        # Simulate a background scan returning the same songs in a different
        # source order before the visible title sort is reapplied.
        self.page._populate_table(
            self.page.table,
            list(reversed(self.page.filtered)),
            include_album_and_type=True,
        )

        selected = self.page.table.selectionModel().selectedRows()
        self.assertEqual(len(selected), 1)
        self.assertEqual(
            self.page.table.item(selected[0].row(), 0).text(), "Short"
        )

    def test_track_tables_support_ctrl_additive_and_shift_range_selection(self) -> None:
        self.page.items = [
            media(f"Song {index}", 2000 + index, 60_000)
            for index in range(4)
        ]
        self.page.apply_filters()
        self.page.resize(1200, 800)
        self.page.show()
        self.app.processEvents()
        selection_filter = BlankClickSelectionFilter(self.page)
        self.app.installEventFilter(selection_filter)

        try:
            def click_row(table, row: int, modifier: Qt.KeyboardModifier) -> None:
                item = table.item(row, 0)
                QTest.mouseClick(
                    table.viewport(),
                    Qt.MouseButton.LeftButton,
                    modifier,
                    table.visualItemRect(item).center(),
                )

            click_row(self.page.table, 0, Qt.KeyboardModifier.NoModifier)
            click_row(self.page.table, 2, Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(
                len(self.page.table.selectionModel().selectedRows()),
                3,
            )
            click_row(self.page.table, 3, Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(len(self.page.table.selectionModel().selectedRows()), 4)

            self.page.open_album(self.page.albums.item(0))
            self.app.processEvents()
            click_row(self.page.album_tracks, 0, Qt.KeyboardModifier.NoModifier)
            click_row(self.page.album_tracks, 2, Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(
                len(self.page.album_tracks.selectionModel().selectedRows()),
                3,
            )
            click_row(
                self.page.album_tracks,
                3,
                Qt.KeyboardModifier.ControlModifier,
            )
            self.assertEqual(
                len(self.page.album_tracks.selectionModel().selectedRows()),
                4,
            )
        finally:
            self.app.removeEventFilter(selection_filter)

    def test_unchanged_periodic_scan_does_not_rebuild_the_browser(self) -> None:
        self.page.apply_filters = Mock()
        self.page._scan_finished(list(self.page.items))
        self.page.apply_filters.assert_not_called()

    def test_refresh_requested_during_scan_is_queued(self) -> None:
        self.page._scanner_thread = Mock()

        self.page.refresh_library()

        self.assertTrue(self.page._scan_refresh_pending)
        self.assertEqual(self.page.library_refresh_button.text(), "Refresh queued")
        with patch.object(QTimer, "singleShot") as single_shot:
            self.page._scanner_thread_finished()
        single_shot.assert_called_once_with(0, self.page.refresh_library)
        self.assertIsNone(self.page._scanner_thread)
        self.assertTrue(self.page.library_refresh_button.isEnabled())

    def test_refresh_without_folders_clears_stale_library_rows(self) -> None:
        self.assertTrue(self.page.items)

        self.page.refresh_library()

        self.assertEqual(self.page.items, [])
        self.assertEqual(self.page.table.rowCount(), 0)

    def test_scan_removes_deleted_tracks_from_the_queue(self) -> None:
        original_items = list(self.page.items)
        self.page.queue = list(original_items)
        self.page._queue_source = list(original_items)
        self.page.queue_index = 1

        self.page._scan_finished([original_items[0]])

        self.assertEqual(self.page.queue, [original_items[0]])
        self.assertEqual(self.page._queue_source, [original_items[0]])
        self.assertEqual(self.page.queue_index, 0)

    def test_table_refresh_preserves_vertical_scroll_position(self) -> None:
        many_items = [media(f"Song {index:03d}", 2000, 1000) for index in range(80)]
        self.page.table.resize(700, 140)
        self.page._populate_table(
            self.page.table, many_items, include_album_and_type=True
        )
        self.app.processEvents()
        scrollbar = self.page.table.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        wanted = min(25, scrollbar.maximum())
        scrollbar.setValue(wanted)

        self.page._populate_table(
            self.page.table,
            list(reversed(many_items)),
            include_album_and_type=True,
        )

        self.assertEqual(scrollbar.value(), wanted)

    def test_album_refresh_preserves_vertical_scroll_position(self) -> None:
        albums = [
            LibraryItem(
                f"C:/album-{index}.mp3", f"Song {index}", f"Album {index}",
                "Artist", 2000 + index % 20, 1000, "audio", 1,
            )
            for index in range(40)
        ]
        self.page.filtered = albums
        self.page._artwork_cache = {item.path: QIcon() for item in albums}
        self.page.resize(900, 800)
        self.page.show()
        self.page.albums.resize(600, 170)
        self.page._render_albums()
        self.app.processEvents()
        scrollbar = self.page.albums.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        wanted = min(20, scrollbar.maximum())
        scrollbar.setValue(wanted)

        self.page._render_albums()
        self.app.processEvents()

        self.assertEqual(scrollbar.value(), wanted)

    def test_album_wheel_scrolls_exactly_one_visual_row_per_notch(self) -> None:
        albums = [
            LibraryItem(
                f"C:/album-{index}.mp3", f"Song {index}", f"Album {index}",
                "Artist", 2000, 1000, "audio", 1,
            )
            for index in range(40)
        ]
        self.page.filtered = albums
        self.page._artwork_cache = {item.path: QIcon() for item in albums}
        self.page.resize(900, 700)
        self.page.show()
        self.page.albums.setFixedHeight(170)
        self.page._render_albums()
        self.app.processEvents()
        scrollbar = self.page.albums.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), self.page.albums.gridSize().height())

        event = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate, False,
        )
        self.page.albums.wheelEvent(event)

        self.assertEqual(scrollbar.value(), self.page.albums.gridSize().height())

    def test_search_row_gives_remaining_width_to_search_field(self) -> None:
        self.page.resize(1600, 900)
        self.page.show()
        self.app.processEvents()

        fixed_controls = (
            self.page.clear_search_button,
            self.page.year_from,
            self.page.year_to,
            self.page.online_search_button,
        )
        self.assertTrue(all(
            self.page.search.width() > control.width()
            for control in fixed_controls
        ))
        self.assertGreaterEqual(
            self.page.online_search_button.width(),
            self.page.online_search_button.sizeHint().width(),
        )

    def test_year_filters_use_hints_and_accept_only_numeric_input(self) -> None:
        for spin, hint in (
            (self.page.year_from, "From year"),
            (self.page.year_to, "To year"),
        ):
            self.assertEqual(spin.value(), 0)
            self.assertEqual(spin.lineEdit().text(), "")
            self.assertEqual(spin.lineEdit().placeholderText(), hint)

            spin.setFocus()
            QTest.keyClicks(spin.lineEdit(), "2024letters!@#")

            self.assertEqual(spin.value(), 2024)
            self.assertEqual(spin.lineEdit().text(), "2024")

    def test_to_year_cannot_be_lower_than_from_year(self) -> None:
        self.page.year_from.setValue(2005)
        self.assertEqual(self.page.year_to.value(), 0)

        self.page.year_to.setValue(1999)
        self.assertEqual(self.page.year_to.value(), 2005)

        self.page.year_to.setValue(2010)
        self.page.year_from.setValue(2020)
        self.assertEqual(self.page.year_to.value(), 2020)

    def test_clear_follows_year_fields_and_removes_their_filters(self) -> None:
        layout = self.page.search_controls.layout()
        self.assertGreater(
            layout.indexOf(self.page.clear_search_button),
            layout.indexOf(self.page.year_to),
        )
        self.assertLess(
            layout.indexOf(self.page.clear_search_button),
            layout.indexOf(self.page.online_search_button),
        )
        self.page.search.setText("filtered title")
        self.page.media_type_filter.setCurrentIndex(
            self.page.media_type_filter.findData("video")
        )
        self.page.year_from.setValue(2010)
        self.page.year_to.setValue(2020)

        self.page.clear_search_button.click()

        self.assertEqual(self.page.search.text(), "")
        self.assertEqual(self.page.year_from.value(), 0)
        self.assertEqual(self.page.year_to.value(), 0)
        self.assertEqual(self.page.media_type_filter.currentData(), "video")
        self.assertEqual(self.page.year_from.lineEdit().text(), "")
        self.assertEqual(self.page.year_to.lineEdit().text(), "")

    def test_album_opens_without_playing_and_exposes_track_actions(self) -> None:
        self.page._load_current = Mock()
        self.page.open_album(self.page.albums.item(0))
        self.assertEqual(self.page.album_stack.currentIndex(), 1)
        self.assertEqual(self.page.album_tracks.rowCount(), 2)
        self.assertEqual(self.page.queue, [])

        self.page.album_tracks.selectRow(0)
        self.page.play_album_tracks("play", selected_only=True)
        self.assertEqual(len(self.page.queue), 1)
        self.page._load_current.assert_called_once()

    def test_album_queue_selected_appends_without_interrupting_current_track(self) -> None:
        current = media("Already Playing", 2024, 180_000)
        self.page.queue = [current]
        self.page._queue_source = [current]
        self.page.queue_index = 0
        self.page._sync_queue_drawer()
        self.page._load_current = Mock()
        self.page.open_album(self.page.albums.item(0))
        self.page.album_tracks.selectRow(0)

        self.page.play_album_tracks("queue", selected_only=True)

        self.assertEqual(len(self.page.queue), 2)
        self.assertEqual(self.page.queue[0], current)
        self.assertEqual(self.page.queue_index, 0)
        self.assertEqual(self.page.queue_status.text(), "Track 1 of 2")
        self.assertEqual(self.page.queue_list.count(), 2)
        self.page._load_current.assert_not_called()
        self.assertGreaterEqual(
            len(
                [
                    button
                    for button in self.page.findChildren(type(self.page.queue_toggle_button))
                    if button.text() == "Queue selected"
                ]
            ),
            2,
        )

    def test_queue_drawer_reorders_tracks_and_preserves_current_identity(self) -> None:
        third = media("Third", 2010, 180_000)
        self.page.queue = [*self.page.items, third]
        self.page._queue_source = list(self.page.queue)
        self.page.queue_index = 1
        current_path = self.page.queue[1].path
        self.page._shuffle_enabled = True
        self.page._sync_queue_drawer()

        moved = self.page.queue_list.takeItem(0)
        self.page.queue_list.insertItem(2, moved)
        self.page._queue_reordered()

        self.assertEqual(
            [item.title for item in self.page.queue], ["Long", "Third", "Short"]
        )
        self.assertEqual(self.page.queue[self.page.queue_index].path, current_path)
        self.assertFalse(self.page._shuffle_enabled)
        self.assertEqual(self.page._queue_source, self.page.queue)
        self.assertEqual(
            self.page.queue_list.dragDropMode(),
            self.page.queue_list.DragDropMode.InternalMove,
        )
        for row in range(self.page.queue_list.count()):
            entry = self.page.queue_list.item(row)
            foreground = entry.data(Qt.ItemDataRole.ForegroundRole)
            if row == self.page.queue_index:
                self.assertEqual(foreground, QColor("#9db0ff"))
            else:
                self.assertIsNone(foreground)
        self.page.queue_toggle_button.setChecked(True)
        self.assertFalse(self.page.queue_drawer.isHidden())

    def test_queue_rebuild_cancels_drag_overlay_before_delete_and_enqueue(self) -> None:
        third = media("Third", 2010, 180_000)
        replacement = media("Replacement", 2011, 180_000)
        self.page.queue = [*self.page.items, third]
        self.page._queue_source = list(self.page.queue)
        self.page.queue_index = 0
        self.page._sync_queue_drawer()
        self.page.queue_list.setCurrentRow(2)

        moving = self.page.queue_list.currentItem()
        snapshot = QPixmap(120, 40)
        snapshot.fill(QColor("#334d8f"))
        self.page.queue_list._animate_drop(
            moving,
            snapshot,
            QRect(0, 0, 120, 40),
            QRect(0, 40, 120, 40),
        )
        overlay = self.page.queue_list._drop_overlay
        self.assertIsNotNone(overlay)
        self.assertFalse(overlay.isHidden())

        self.page._remove_selected_queue_entry()
        self.page._append_to_queue([replacement])
        self.app.processEvents()

        self.assertTrue(overlay.isHidden())
        self.assertIsNone(self.page.queue_list._drop_overlay)
        self.assertEqual(
            [item.title for item in self.page.queue],
            ["Short", "Long", "Replacement"],
        )
        self.assertEqual(self.page.queue_list.count(), 3)
        self.assertEqual(
            [
                self.page.queue_list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(self.page.queue_list.count())
            ],
            [item.path for item in self.page.queue],
        )

    def test_album_context_actions_emit_the_exact_physical_folder(self) -> None:
        album_folder = Path(self.temporary.name) / "Test Album (2024)"
        album_folder.mkdir()
        menu = QMenu()
        enrich_spy = QSignalSpy(self.page.request_album_enricher)
        reorder_spy = QSignalSpy(self.page.request_track_reorder)
        self.page._add_album_folder_actions(
            menu,
            "Consolidate / Album enricher",
            [album_folder],
            self.page.request_album_enricher,
        )
        self.page._add_album_folder_actions(
            menu,
            "Track reorder",
            [album_folder],
            self.page.request_track_reorder,
        )

        menu.actions()[0].trigger()
        menu.actions()[1].trigger()

        self.assertEqual(enrich_spy[0][0], str(album_folder))
        self.assertEqual(reorder_spy[0][0], str(album_folder))
        self.assertEqual(
            self.page.table.contextMenuPolicy(),
            Qt.ContextMenuPolicy.CustomContextMenu,
        )
        self.assertEqual(
            self.page.album_tracks.contextMenuPolicy(),
            Qt.ContextMenuPolicy.CustomContextMenu,
        )

    def test_playlists_persist_paths_and_apply_duplicate_choice_to_full_album(self) -> None:
        name = self.page.create_playlist("Bengali favourites")
        self.assertEqual(name, "Bengali favourites")
        self.assertEqual(
            self.page.add_items_to_playlist(
                list(self.page.items), name, duplicate_policy="skip"
            ),
            2,
        )

        extra = media("New album track", 2010, 120_000)
        self.assertEqual(
            self.page.add_items_to_playlist(
                [self.page.items[0], extra], name, duplicate_policy="skip"
            ),
            1,
        )
        self.assertEqual(len(self.page.playlists[name]), 3)
        self.assertIn("skipped 1 duplicate", self.page.playlist_track_status.text())

        self.assertEqual(
            self.page.add_items_to_playlist(
                [self.page.items[0]], name, duplicate_policy="add"
            ),
            1,
        )
        self.assertEqual(len(self.page.playlists[name]), 4)
        self.assertTrue(self.page.settings.value("library/playlists"))
        self.assertEqual(
            self.page.playlist_tracks.contextMenuPolicy(),
            Qt.ContextMenuPolicy.CustomContextMenu,
        )
        self.assertEqual(
            self.page.facets.contextMenuPolicy(),
            Qt.ContextMenuPolicy.CustomContextMenu,
        )

    def test_playlist_tracks_drag_reorder_persists_exact_order(self) -> None:
        name = self.page.create_playlist("Road trip")
        duplicate_path = self.page.items[0].path
        original = [
            duplicate_path,
            self.page.items[1].path,
            duplicate_path,
        ]
        self.page.playlists[name] = list(original)
        self.page._save_playlists()
        self.page._render_playlist_tracks()

        moved = self.page.playlist_tracks.takeItem(1)
        self.page.playlist_tracks.insertItem(0, moved)
        self.page._playlist_tracks_reordered()

        expected = [original[1], original[0], original[2]]
        self.assertEqual(self.page.playlists[name], expected)
        saved = decode_playlists(self.page.settings.value("library/playlists", ""))
        self.assertEqual(saved[name], expected)
        self.assertEqual(
            self.page.playlist_tracks.dragDropMode(),
            self.page.playlist_tracks.DragDropMode.InternalMove,
        )
        self.assertEqual(
            self.page.playlist_tracks.selectionMode(),
            self.page.playlist_tracks.SelectionMode.ExtendedSelection,
        )

    def test_phone_playlist_actions_mutate_and_persist_desktop_state(self) -> None:
        third = media("Third", 2012, 180_000)
        self.page.items.append(third)
        self.page._remote_path_by_id = {
            media_id(item.path): item.path for item in self.page.items
        }

        self.page._handle_remote_action(
            {"type": "create_playlist", "name": "Phone edits"}
        )
        self.page._handle_remote_action(
            {
                "type": "add_to_playlist",
                "playlist": "Phone edits",
                "media_ids": [media_id(item.path) for item in self.page.items],
            }
        )
        self.page._handle_remote_action(
            {
                "type": "reorder_playlist",
                "playlist": "Phone edits",
                "visible_positions": [2, 0],
            }
        )
        self.page._handle_remote_action(
            {
                "type": "remove_playlist_positions",
                "playlist": "Phone edits",
                "positions": [1],
            }
        )

        expected = [third.path, self.page.items[0].path]
        self.assertEqual(self.page.playlists["Phone edits"], expected)
        saved = decode_playlists(self.page.settings.value("library/playlists", ""))
        self.assertEqual(saved["Phone edits"], expected)

    def test_desktop_playlist_edits_publish_path_hiding_phone_revision(self) -> None:
        server = RemoteMediaServer(lambda _action: None, port=0, html="")
        self.page._remote_server = server
        name = self.page.create_playlist("Live sync")
        self.page.playlists[name] = [item.path for item in self.page.items]
        self.page._save_playlists()

        self.page._publish_remote_state()
        first = server.state()
        self.assertEqual(first["revision"], 1)
        self.assertEqual(
            [track["title"] for track in first["playlists"][0]["tracks"]],
            ["Short", "Long"],
        )
        self.assertNotIn("C:/", str(first))

        self.page.playlists[name].reverse()
        self.page._save_playlists()
        self.page._publish_remote_state()
        second = server.state()
        self.assertEqual(second["revision"], 2)
        self.assertEqual(
            [track["title"] for track in second["playlists"][0]["tracks"]],
            ["Long", "Short"],
        )
        self.page._remote_server = None

    def test_phone_access_switch_stops_restarts_and_persists(self) -> None:
        running = Mock()
        running.urls = ["http://192.168.1.10:8765"]
        running.pin = "123456"
        running.port = 8765
        self.page._remote_server = running
        self.page._set_phone_access_switch(True, "On")
        self.page.phone_access_details_button.setVisible(True)
        self.page.phone_access_switch.setEnabled(True)

        self.page.phone_access_switch.setChecked(False)

        running.stop.assert_called_once_with()
        self.assertIsNone(self.page._remote_server)
        self.assertFalse(
            self.page.settings.value(
                "library/remote_access_enabled",
                True,
                type=bool,
            )
        )
        self.assertEqual(self.page.phone_access_switch.text(), "Phone access · Off")
        self.assertTrue(self.page.phone_access_details_button.isHidden())

        restarted = Mock()
        restarted.urls = ["http://192.168.1.10:8765"]
        restarted.pin = "654321"
        restarted.port = 8765
        with (
            patch.dict(os.environ, {"YMS_DISABLE_REMOTE_ACCESS": ""}),
            patch(
                "youtube_audio_video_downloader.gui.media.media_player.RemoteMediaServer",
                return_value=restarted,
            ) as server_type,
        ):
            self.page.phone_access_switch.setChecked(True)

        server_type.assert_called_once()
        restarted.start.assert_called_once_with()
        restarted.update_state.assert_called_once()
        self.assertIs(self.page._remote_server, restarted)
        self.assertTrue(
            self.page.settings.value(
                "library/remote_access_enabled",
                False,
                type=bool,
            )
        )
        self.assertEqual(self.page.phone_access_switch.text(), "Phone access · On")
        self.assertFalse(self.page.phone_access_details_button.isHidden())

    def test_saved_phone_access_off_prevents_server_startup(self) -> None:
        settings = QSettings(
            str(Path(self.temporary.name) / "remote-off.ini"),
            QSettings.Format.IniFormat,
        )
        settings.setValue("library/remote_access_enabled", False)
        with (
            patch.dict(os.environ, {"YMS_DISABLE_REMOTE_ACCESS": ""}),
            patch(
                "youtube_audio_video_downloader.gui.media.media_player.RemoteMediaServer"
            ) as server_type,
        ):
            page = MediaLibraryPage(settings)
        try:
            server_type.assert_not_called()
            self.assertFalse(page.phone_access_switch.isChecked())
            self.assertEqual(page.phone_access_switch.text(), "Phone access · Off")
        finally:
            page.shutdown()
            page.deleteLater()

    def test_phone_curator_request_uses_desktop_ai_configuration(self) -> None:
        self.page.request_ai_recommendations = Mock()

        self.page._handle_remote_action(
            {
                "type": "curate",
                "query": "relaxing Bengali songs from the 2000s",
                "limit": 12,
            }
        )

        self.assertTrue(self.page.recommendation_ai_enabled.isChecked())
        self.assertEqual(
            self.page.recommendation_request.text(),
            "relaxing Bengali songs from the 2000s",
        )
        self.assertEqual(self.page.recommendation_limit.value(), 12)
        self.page.request_ai_recommendations.assert_called_once_with()

    def test_playlist_filter_searches_metadata_and_reorders_visible_matches(self) -> None:
        items = [
            LibraryItem(
                "C:/Bengali 2005.mp3", "First", "Blue Album", "Artist One",
                2005, 60_000, "audio", 1,
            ),
            LibraryItem(
                "C:/Hindi 2010.mp3", "Second", "Red Album", "Artist Two",
                2010, 60_000, "audio", 1,
            ),
            LibraryItem(
                "C:/Bengali 2015.mp3", "Third", "Green Album", "Artist One",
                2015, 60_000, "audio", 1,
            ),
        ]
        self.page.items = items
        name = self.page.create_playlist("Mixed")
        self.page.playlists[name] = [item.path for item in items]
        self.page._render_playlist_tracks()

        for query, expected_paths in (
            ("Artist One", [items[0].path, items[2].path]),
            ("Red Album", [items[1].path]),
            ("2015", [items[2].path]),
            ("Hindi 2010", [items[1].path]),
        ):
            self.page.playlist_filter.setText(query)
            self.assertEqual(
                [
                    self.page.playlist_tracks.item(row).data(Qt.ItemDataRole.UserRole)
                    for row in range(self.page.playlist_tracks.count())
                ],
                expected_paths,
            )

        self.page.playlist_filter.setText("Artist One")
        moved = self.page.playlist_tracks.takeItem(1)
        self.page.playlist_tracks.insertItem(0, moved)
        self.page._playlist_tracks_reordered()

        self.assertEqual(
            self.page.playlists[name],
            [items[2].path, items[1].path, items[0].path],
        )
        self.page.playlist_filter.clear()
        self.assertEqual(self.page.playlist_tracks.count(), 3)

    def test_playlist_filter_can_search_and_remove_across_all_playlists(self) -> None:
        first, second = self.page.items
        self.page.playlists = {
            "Favourites": [first.path, second.path],
            "Road trip": [first.path],
        }
        self.page._active_playlist = "Favourites"
        self.page._render_playlists()

        self.page.playlist_filter.setText(first.artists)
        self.page.playlist_filter_all.setChecked(True)

        self.assertEqual(self.page.playlist_tracks.count(), 3)
        self.assertFalse(self.page.playlist_tracks.dragEnabled())
        self.assertIn("All playlists", self.page.playlist_track_status.text())
        self.page.playlist_tracks.item(2).setSelected(True)
        self.page.remove_selected_playlist_tracks()
        self.assertEqual(self.page.playlists["Favourites"], [first.path, second.path])
        self.assertEqual(self.page.playlists["Road trip"], [])

    def test_player_controls_have_visible_icons_and_mode_labels(self) -> None:
        self.assertTrue(self.page.play_button.text() == "")
        self.assertFalse(self.page.play_button.icon().isNull())
        self.assertEqual(self.page.seek_backward_button.text(), "")
        self.assertEqual(self.page.seek_forward_button.text(), "")
        self.assertFalse(self.page.seek_backward_button.icon().isNull())
        self.assertFalse(self.page.seek_forward_button.icon().isNull())
        self.assertNotEqual(
            self.page.seek_backward_button.icon().cacheKey(),
            self.page.previous_button.icon().cacheKey(),
        )
        self.assertNotEqual(
            self.page.seek_forward_button.icon().cacheKey(),
            self.page.next_button.icon().cacheKey(),
        )
        self.assertEqual(self.page.library_refresh_button.text(), "Refresh")
        self.assertEqual(self.page.shuffle_button.text(), "Shuffle off")
        self.assertEqual(self.page.repeat_button.text(), "Repeat off")
        layout = self.page.player_controls.layout()
        ordered_controls = (
            self.page.shuffle_button,
            self.page.seek_backward_button,
            self.page.previous_button,
            self.page.play_button,
            self.page.next_button,
            self.page.seek_forward_button,
            self.page.stop_button,
            self.page.repeat_button,
            self.page.aspect_button,
            self.page.crop_button,
            self.page.fullscreen_button,
        )
        self.assertEqual(
            [layout.indexOf(control) for control in ordered_controls],
            list(range(len(ordered_controls))),
        )

        self.page._state_changed(QMediaPlayer.PlaybackState.PlayingState)
        self.assertFalse(self.page.play_button.icon().isNull())
        self.assertEqual(self.page.play_button.text(), "")

    def test_audio_transport_seeks_and_hides_video_only_controls(self) -> None:
        player = Mock()
        player.position.return_value = 30_000
        player.duration.return_value = 90_000
        self.page.player = player
        self.page.set_video_seek_seconds(7)

        QTest.mouseClick(self.page.seek_backward_button, Qt.MouseButton.LeftButton)
        player.setPosition.assert_called_once_with(23_000)
        player.setPosition.reset_mock()
        QTest.mouseClick(self.page.seek_forward_button, Qt.MouseButton.LeftButton)
        player.setPosition.assert_called_once_with(37_000)

        self.assertTrue(self.page.aspect_button.isHidden())
        self.assertTrue(self.page.crop_button.isHidden())
        self.assertTrue(self.page.fullscreen_button.isHidden())

    def test_repeat_modes_control_end_of_queue(self) -> None:
        self.page.queue = list(self.page.items)
        self.page._queue_source = list(self.page.items)
        self.page.queue_index = len(self.page.queue) - 1
        self.page._load_current = Mock()

        self.page._repeat_mode = "off"
        self.page._advance_after_end()
        self.page._load_current.assert_not_called()

        self.page.cycle_repeat_mode()
        self.assertEqual(self.page._repeat_mode, "all")
        self.page._advance_after_end()
        self.assertEqual(self.page.queue_index, 0)
        self.page._load_current.assert_called_once()

        self.page.cycle_repeat_mode()
        self.assertEqual(self.page._repeat_mode, "one")
        player = Mock()
        self.page.player = player
        self.page._advance_after_end()
        player.setPosition.assert_called_once_with(0)
        player.play.assert_called_once_with()

    def test_shuffle_keeps_current_song_and_off_restores_queue_order(self) -> None:
        third = media("Third", 2010, 180_000)
        original = [*self.page.items, third]
        self.page.queue = list(original)
        self.page._queue_source = list(original)
        self.page.queue_index = 1
        current_path = self.page.queue[self.page.queue_index].path

        with patch(
            "youtube_audio_video_downloader.gui.media.media_player.random.shuffle",
            side_effect=lambda values: values.reverse(),
        ):
            self.page.set_shuffle_enabled(True)

        self.assertEqual(self.page.queue[0].path, current_path)
        self.assertEqual(self.page.queue_index, 0)
        self.page.set_shuffle_enabled(False)
        self.assertEqual(self.page.queue, original)
        self.assertEqual(self.page.queue[self.page.queue_index].path, current_path)

    def test_shuffle_all_builds_one_stable_duplicate_free_navigation_queue(self) -> None:
        songs = [media(f"Song {index}", 2000 + index, 1000) for index in range(4)]
        self.page.filtered = [songs[0], songs[1], songs[2], songs[1], songs[3]]
        self.page._load_current = Mock()
        with patch(
            "youtube_audio_video_downloader.gui.media.media_player.random.shuffle",
            side_effect=lambda values: values.reverse(),
        ) as shuffle_mock:
            self.page.shuffle_all_matches()

        self.assertTrue(self.page._shuffle_enabled)
        self.assertEqual(self.page._queue_source, songs)
        self.assertEqual(self.page.queue, list(reversed(songs)))
        self.assertEqual(len({item.path for item in self.page.queue}), 4)
        shuffle_mock.assert_called_once()
        stable_order = list(self.page.queue)

        player = Mock()
        player.position.return_value = 1000
        self.page.player = player
        self.page.next()
        self.assertEqual(self.page.queue_index, 1)
        self.page.previous()
        self.assertEqual(self.page.queue_index, 0)
        self.assertEqual(self.page.queue, stable_order)
        shuffle_mock.assert_called_once()

    def test_previous_uses_five_second_restart_threshold(self) -> None:
        self.page.queue = list(self.page.items)
        self.page.queue_index = 1
        self.page._repeat_mode = "off"
        self.page._load_current = Mock()
        player = Mock()
        self.page.player = player

        player.position.return_value = 5000
        self.page.previous()
        self.assertEqual(self.page.queue_index, 1)
        player.setPosition.assert_called_once_with(0)
        self.page._load_current.assert_not_called()

        player.reset_mock()
        player.position.return_value = 4999
        self.page.previous()
        self.assertEqual(self.page.queue_index, 0)
        self.page._load_current.assert_called_once()

    def test_queue_boundaries_follow_repeat_off_one_and_all(self) -> None:
        self.page.queue = list(self.page.items)
        self.page._queue_source = list(self.page.items)
        self.page._load_current = Mock()
        player = Mock()
        player.position.return_value = 1000
        self.page.player = player

        self.page.queue_index = len(self.page.queue) - 1
        self.page._repeat_mode = "off"
        self.page.next()
        player.stop.assert_called_once()
        self.page._load_current.assert_not_called()

        player.reset_mock()
        self.page._repeat_mode = "one"
        current_index = self.page.queue_index
        self.page.next()
        self.assertEqual(self.page.queue_index, current_index)
        player.setPosition.assert_called_once_with(0)
        player.play.assert_called_once()

        player.reset_mock()
        self.page._repeat_mode = "all"
        self.page.next()
        self.assertEqual(self.page.queue_index, 0)
        self.page._load_current.assert_called_once()
        self.page._load_current.reset_mock()
        self.page.previous()
        self.assertEqual(self.page.queue_index, len(self.page.queue) - 1)
        self.page._load_current.assert_called_once()

    def test_shuffle_off_uses_original_selected_song_order(self) -> None:
        songs = [media(f"Selected {index}", 2000, 1000) for index in range(3)]
        self.page._shuffle_enabled = False
        self.page._load_current = Mock()
        self.page._replace_queue(songs)
        self.assertEqual(self.page.queue, songs)
        self.page.next()
        self.page.next()
        self.assertEqual(self.page.queue_index, 2)
        self.page.previous()
        self.assertEqual(self.page.queue_index, 1)
        self.assertEqual(self.page.queue, songs)

    def test_background_search_populates_suggestions_and_no_match_message(self) -> None:
        self.page.set_suggestion_limit(1)
        self.page.search.setText("Short")
        QTest.qWait(350)
        self.assertEqual([item.title for item in self.page.filtered], ["Short"])
        self.assertEqual(len(self.page.suggestion_model.stringList()), 1)

        self.page.search.setText("Definitely missing")
        QTest.qWait(350)
        self.assertEqual(self.page.filtered, [])
        self.assertEqual(self.page.suggestion_model.stringList(), ["No search found"])
        online_spy = QSignalSpy(self.page.request_search_song)
        self.page.search_or_download()
        self.assertEqual(len(online_spy), 1)
        self.assertEqual(online_spy[0][0], "Definitely missing")

    def test_ai_recommendations_use_global_model_and_render_grounded_local_result(self) -> None:
        local_path = Path(self.temporary.name) / "recommended.mp3"
        local_path.write_bytes(b"audio")
        item = LibraryItem(
            str(local_path), "Calm Song", "Evening", "Test Artist",
            2022, 1000, "audio", 1,
        )
        self.page.items = [item]
        self.page.playlists = {"Calm favourites": [item.path]}
        self.page.settings.setValue("defaults/agentic_model", "library-agent:test")
        self.page.ai_identity_resolver = Mock(
            return_value=("Groq", "openai/gpt-oss-120b")
        )
        expected = [LibraryRecommendation(item, "Artist matches the request", True)]
        with patch(
            "youtube_audio_video_downloader.gui.media.media_player.recommend_library_tracks",
            return_value=expected,
        ) as recommendation_mock:
            self.page.recommendation_request.setText("calm Test Artist songs")
            self.page.request_ai_recommendations()
            self.assertTrue(
                wait_until(
                    lambda: self.page.recommendations.isVisibleTo(self.page)
                    and self.page.recommendation_button.isEnabled()
                ),
                "recommendations did not render and finish worker cleanup",
            )

        recommendation_mock.assert_called_once()
        self.assertEqual(
            recommendation_mock.call_args.kwargs["model"], "openai/gpt-oss-120b"
        )
        self.page.ai_identity_resolver.assert_called_once_with()
        self.assertEqual(recommendation_mock.call_args.kwargs["limit"], 10)
        self.assertEqual(
            recommendation_mock.call_args.kwargs["playlists"],
            {"Calm favourites": [item.path]},
        )
        self.assertTrue(self.page.recommendations.isVisibleTo(self.page))
        self.assertIn("[LOCAL] Calm Song", self.page.recommendations.item(0).text())
        self.assertTrue(self.page.recommendation_button.isEnabled())
        self.assertEqual(self.page.recommendation_button.text(), "Find in my library")

    def test_prompt_result_count_overrides_visible_recommendation_limit(self) -> None:
        self.page.recommendation_limit.setValue(5)
        self.page.recommendation_request.setText("slow bengali songs, return 12 results")
        with patch(
            "youtube_audio_video_downloader.gui.media.media_player.recommend_library_tracks",
            return_value=[],
        ) as recommendation_mock:
            self.page.request_ai_recommendations()
            QTest.qWait(100)

        self.assertEqual(recommendation_mock.call_args.kwargs["limit"], 12)

    def test_prompt_result_counts_cover_noun_pattern_clamping_and_years(self) -> None:
        cases = (
            ("find 3 tracks", 3),
            ("40 songs", 20),
            ("0 results", 1),
            ("songs from 1999", 5),
        )
        self.page.recommendation_limit.setValue(5)
        for request, expected in cases:
            with self.subTest(request=request), patch(
                "youtube_audio_video_downloader.gui.media.media_player.recommend_library_tracks",
                return_value=[],
            ) as recommendation_mock:
                self.page.recommendation_request.setText(request)
                self.page.request_ai_recommendations()
                QTest.qWait(100)
                self.assertEqual(
                    recommendation_mock.call_args.kwargs["limit"], expected
                )

    def test_mix_appends_verified_continuation_without_duplicates(self) -> None:
        exact = self.page.items[0]
        continuation = self.page.items[1]
        self.page._replace_queue([exact])
        self.page._last_recommendations = [
            LibraryRecommendation(exact, "Matches Bengali, slow", True)
        ]
        self.page._finish_recommendation_mix(
            [
                LibraryRecommendation(exact, "Matches Bengali", True),
                LibraryRecommendation(continuation, "Matches Bengali", True),
            ],
            "",
        )

        self.assertEqual(self.page.queue, [exact, continuation])
        self.assertIn("related track(s) queued", self.page.recommendation_status.text())

    def test_mix_handles_missing_exact_files_and_empty_continuation(self) -> None:
        missing = self.page.items[0]
        self.page._last_recommendations = [
            LibraryRecommendation(missing, "Matches Bengali", False)
        ]
        self.page.start_recommendation_mix()
        self.assertEqual(
            self.page.recommendation_status.text(), "Matching local files are missing"
        )

        self.page.queue = [self.page.items[1]]
        self.page._queue_source = [self.page.items[1]]
        self.page.queue_index = 0
        self.page._finish_recommendation_mix([], "")
        self.assertEqual(self.page.queue, [self.page.items[1]])
        self.assertIn("Mix playing 1 track(s)", self.page.recommendation_status.text())

    def test_ai_suggestions_can_be_cleared_and_routed_to_youtube(self) -> None:
        self.page.recommendation_request.setText("Atif songs")
        self.page.recommendations.addItem("old result")
        self.page.recommendations.setVisible(True)
        online_spy = QSignalSpy(self.page.request_search_song)

        self.page.search_recommendation_online()
        self.assertEqual(len(online_spy), 1)
        self.assertEqual(online_spy[0][0], "Atif songs")

        self.page.clear_ai_recommendations()
        self.assertEqual(self.page.recommendation_request.text(), "")
        self.assertEqual(self.page.recommendations.count(), 0)
        self.assertFalse(self.page.recommendations.isVisible())
        self.assertEqual(self.page.recommendation_status.text(), "AI idle")

    def test_no_local_ai_match_stays_in_library_until_online_search_is_requested(self) -> None:
        self.page.recommendation_request.setText("Atif songs")
        self.page._last_recommendation_request = "Atif songs"
        online_spy = QSignalSpy(self.page.request_search_song)
        self.page._recommendations_finished([], "")
        QTest.qWait(10)
        self.assertEqual(len(online_spy), 0)
        self.assertEqual(self.page.recommendation_status.text(), "No matching local tracks")
        self.assertIn("Search YouTube too", self.page.recommendations.item(0).text())

    def test_clicking_seekbar_animates_to_clicked_position_without_blocking(self) -> None:
        slider = self.page.position
        slider.setRange(0, 1000)
        slider.resize(400, 26)
        slider.show()
        spy = QSignalSpy(slider.seekRequested)
        QTest.mouseClick(
            slider,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(200, 13),
        )
        self.assertTrue(spy)
        self.assertTrue(slider.is_seek_animating())
        self.assertLess(slider.value(), 500)
        QTest.qWait(350)
        self.assertGreater(slider.value(), 450)
        self.assertLess(slider.value(), 550)

    def test_clicking_volume_bar_animates_to_the_exact_clicked_level(self) -> None:
        slider = self.page.volume
        slider.setValue(0)
        slider.resize(216, 26)
        slider.show()

        QTest.mouseClick(
            slider,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(158, 13),
        )

        self.assertTrue(slider.is_seek_animating())
        self.assertLess(slider.value(), 70)
        QTest.qWait(350)
        self.assertGreater(slider.value(), 68)
        self.assertLess(slider.value(), 76)
        self.assertEqual(self.page.volume_percent.text(), f"{slider.value()}%")

        self.page._ensure_fullscreen_window()
        self.assertTrue(hasattr(self.page.fullscreen_volume, "is_seek_animating"))
        self.assertEqual(
            self.page.fullscreen_volume_percent.text(),
            f"{slider.value()}%",
        )
        self.page.fullscreen_volume.setValue(0)
        QTest.mouseClick(
            self.page.fullscreen_volume,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(140, 13),
        )
        self.assertTrue(self.page.fullscreen_volume.is_seek_animating())
        QTest.qWait(350)
        self.assertEqual(
            self.page.fullscreen_volume_percent.text(),
            f"{self.page.fullscreen_volume.value()}%",
        )
        self.assertEqual(
            self.page.volume_percent.text(),
            self.page.fullscreen_volume_percent.text(),
        )

    def test_video_player_enters_full_screen_with_controls_and_restores(self) -> None:
        video_path = Path(self.temporary.name) / "movie.mp4"
        video_path.write_bytes(b"video")
        video = LibraryItem(
            str(video_path), "Movie", "Test Movie", "Test Artist",
            2024, 60_000, "video", 1,
        )
        self.page.setStyleSheet(
            "QPushButton#playerControlButton { border-radius: 14px; }"
        )
        self.page.items = [video]
        self.page._replace_queue([video])
        self.page.resize(1200, 800)
        self.page.show()
        self.app.processEvents()

        self.assertTrue(self.page.video.isVisibleTo(self.page))
        self.assertTrue(self.page.fullscreen_button.isEnabled())
        self.assertGreaterEqual(
            self.page.video_viewport.minimumHeight(), EMBEDDED_VIDEO_MIN_HEIGHT
        )
        self.assertFalse(
            self.page.video_viewport.geometry().intersects(
                self.page.player_controls.geometry()
            )
        )

        QTest.mouseDClick(self.page.video, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertTrue(self.page._player_fullscreen)
        fullscreen = self.page._fullscreen_window
        overlay = self.page._fullscreen_controls_overlay
        self.assertIsNotNone(fullscreen)
        self.assertIsNotNone(overlay)
        self.assertTrue(fullscreen.isFullScreen())
        self.assertTrue(
            bool(fullscreen.windowFlags() & Qt.WindowType.FramelessWindowHint)
        )
        self.assertEqual(
            self.page.video_viewport.maximumHeight(), FULLSCREEN_VIDEO_MAX_HEIGHT
        )
        self.assertEqual(self.page.fullscreen_button.text(), "Exit full screen")
        self.assertIs(self.page.video_viewport.window(), fullscreen)
        self.assertIs(overlay.window(), fullscreen)
        self.assertIs(self.page.position.window(), self.page.window())
        self.assertIs(self.page.volume.window(), self.page.window())
        self.assertFalse(self.page.library_splitter.isHidden())
        self.assertEqual(self.page.fullscreen_backward_button.text(), "")
        self.assertEqual(self.page.fullscreen_forward_button.text(), "")
        self.assertFalse(self.page.fullscreen_backward_button.icon().isNull())
        self.assertFalse(self.page.fullscreen_forward_button.icon().isNull())
        self.assertIn(
            "QPushButton#playerControlButton { border-radius: 14px; }",
            fullscreen.styleSheet(),
        )

        QTest.qWait(220)
        self.assertTrue(overlay.isVisible())
        self.page._animate_fullscreen_controls(False)
        QTest.qWait(220)
        self.assertTrue(overlay.isHidden())
        self.assertEqual(
            self.page.video_viewport.geometry(), fullscreen.rect()
        )
        QTest.mouseMove(self.page.video_viewport, QPoint(12, 12))
        QTest.qWait(220)
        self.assertTrue(overlay.isVisible())
        self.assertIsNone(self.page.fullscreen_aspect_button.menu())
        QTest.mouseClick(
            self.page.fullscreen_aspect_button, Qt.MouseButton.LeftButton
        )
        self.app.processEvents()
        self.assertEqual(self.page.fullscreen_aspect_button.text(), "Aspect: 16:9")

        QTest.keyClick(self.page.video, Qt.Key.Key_C)
        self.app.processEvents()
        self.assertEqual(self.page.crop_button.text(), "Crop: 16:10")
        self.assertTrue(self.page._player_fullscreen)

        QTest.keyClick(self.page.video, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertFalse(self.page._player_fullscreen)

        QTest.mouseDClick(
            self.page.video.viewport(), Qt.MouseButton.LeftButton
        )
        self.app.processEvents()
        self.assertTrue(self.page._player_fullscreen)
        self.page.exit_video_fullscreen()

        QTest.mouseClick(
            self.page.fullscreen_button, Qt.MouseButton.LeftButton
        )
        self.app.processEvents()
        self.assertTrue(self.page._player_fullscreen)

        QTest.mouseClick(
            self.page.fullscreen_exit_button, Qt.MouseButton.LeftButton
        )
        self.app.processEvents()

        self.assertFalse(self.page._player_fullscreen)
        self.assertFalse(self.page.window().isFullScreen())
        self.assertIs(self.page.video_viewport.window(), self.page.window())
        self.assertIs(self.page.player_card.parent(), self.page._player_host)
        self.assertFalse(self.page.library_splitter.isHidden())
        self.assertEqual(
            self.page.video_viewport.maximumHeight(), EMBEDDED_VIDEO_MAX_HEIGHT
        )
        self.assertEqual(self.page.fullscreen_button.text(), "Full screen")
        self.page.clear_playback_queue()
        QTest.qWait(50)

    def test_video_aspect_and_crop_cycle_with_buttons_shortcuts_and_osd(self) -> None:
        video_path = Path(self.temporary.name) / "wide-movie.mp4"
        video_path.write_bytes(b"video")
        video = LibraryItem(
            str(video_path), "Wide Movie", "Test Movie", "Test Artist",
            2024, 60_000, "video", 1,
        )
        self.assertFalse(self.page.aspect_button.isEnabled())
        self.assertFalse(self.page.crop_button.isEnabled())
        self.page._replace_queue([video])
        self.page.resize(1200, 800)
        self.page.show()
        self.app.processEvents()
        self.page.video_viewport.resize(800, 450)
        self.page.video_viewport.set_source_size(QSize(1920, 800))

        self.assertTrue(self.page.video_viewport.autoFillBackground())
        self.assertEqual(
            self.page.video_viewport.palette().window().color(), QColor("black")
        )
        self.assertTrue(self.page.aspect_button.isEnabled())
        self.assertTrue(self.page.crop_button.isEnabled())
        self.assertEqual(self.page.aspect_button.text(), "Aspect: Default")
        self.assertEqual(self.page.crop_button.text(), "Crop: Default")
        self.assertLess(self.page.video_viewport.clip.height(), 450)

        self.assertEqual(
            [action.text() for action in self.page.aspect_menu.actions()[:3]],
            ["Default", "16:9", "4:3"],
        )
        self.assertIs(self.page.aspect_button.menu(), self.page.aspect_menu)
        self.assertIs(self.page.crop_button.menu(), self.page.crop_menu)
        self.assertTrue(self.page.aspect_menu.actions()[0].isChecked())
        self.assertTrue(self.page.crop_menu.actions()[0].isChecked())
        self.page.aspect_menu.actions()[1].trigger()
        self.app.processEvents()
        self.assertEqual(self.page.aspect_button.text(), "Aspect: 16:9")
        self.assertEqual(self.page.video_viewport.message.text(), "Aspect ratio: 16:9")
        self.assertTrue(self.page.video_viewport.message.isVisible())
        self.assertAlmostEqual(
            self.page.video_viewport.clip.width()
            / self.page.video_viewport.clip.height(),
            16 / 9,
            places=2,
        )

        self.page.crop_menu.actions()[1].trigger()
        self.app.processEvents()
        self.assertEqual(self.page.crop_button.text(), "Crop: 16:10")

        QTest.keyClick(self.page.video, Qt.Key.Key_A)
        self.app.processEvents()
        self.assertEqual(self.page.aspect_button.text(), "Aspect: 4:3")

        QTest.keyClick(self.page.video, Qt.Key.Key_C)
        self.app.processEvents()
        self.assertEqual(self.page.crop_button.text(), "Crop: 16:9")
        self.assertEqual(self.page.video_viewport.message.text(), "Crop: 16:9")
        self.assertGreater(
            self.page.video_viewport.video_item.size().width(),
            self.page.video_viewport.clip.width(),
        )
        self.assertEqual(
            self.page.video_viewport.scene.sceneRect(),
            QRectF(0, 0, self.page.video.width(), self.page.video.height()),
        )
        self.assertTrue(
            self.page.video_viewport.rect().contains(
                self.page.video_viewport.message.geometry()
            )
        )

        QTest.qWait(1750)
        self.assertFalse(self.page.video_viewport.message.isVisible())

    def test_video_display_modes_reset_unless_memory_is_enabled(self) -> None:
        first_path = Path(self.temporary.name) / "first-display.mp4"
        second_path = Path(self.temporary.name) / "second-display.mp4"
        first_path.write_bytes(b"video")
        second_path.write_bytes(b"video")
        first = LibraryItem(
            str(first_path), "First", "Film", "Artist", 2024, 60_000, "video", 1
        )
        second = LibraryItem(
            str(second_path), "Second", "Film", "Artist", 2024, 60_000, "video", 1
        )

        self.page._replace_queue([first])
        self.page.cycle_video_aspect()
        self.page.cycle_video_crop()
        self.assertEqual(self.page.aspect_button.text(), "Aspect: 16:9")
        self.assertEqual(self.page.crop_button.text(), "Crop: 16:10")

        self.page._repeat_mode = "all"
        self.page._advance_after_end()
        self.assertEqual(self.page.aspect_button.text(), "Aspect: 16:9")
        self.assertEqual(self.page.crop_button.text(), "Crop: 16:10")

        self.page.stop()
        self.assertEqual(self.page.crop_button.text(), "Crop: 16:10")
        self.page.play()
        self.assertEqual(self.page.aspect_button.text(), "Aspect: Default")
        self.assertEqual(self.page.crop_button.text(), "Crop: Default")

        self.page.cycle_video_aspect()
        self.page.cycle_video_crop()

        self.page._replace_queue([second])
        self.assertEqual(self.page.aspect_button.text(), "Aspect: Default")
        self.assertEqual(self.page.crop_button.text(), "Crop: Default")

        self.page.set_remember_video_display_modes(True)
        self.page.cycle_video_aspect()
        self.page.cycle_video_crop()
        self.page._replace_queue([first])
        self.assertEqual(self.page.aspect_button.text(), "Aspect: 16:9")
        self.assertEqual(self.page.crop_button.text(), "Crop: 16:10")
        self.assertEqual(
            self.page.settings.value("library/video_aspect_mode"), "16:9"
        )
        self.assertEqual(
            self.page.settings.value("library/video_crop_mode"), "16:10"
        )

        self.page.set_remember_video_display_modes(False)
        self.assertFalse(
            self.page.settings.contains("library/video_aspect_mode")
        )
        self.assertFalse(
            self.page.settings.contains("library/video_crop_mode")
        )
        self.page._replace_queue([second])
        self.assertEqual(self.page.aspect_button.text(), "Aspect: Default")
        self.assertEqual(self.page.crop_button.text(), "Crop: Default")

    def test_video_keyboard_playback_and_timeline_shortcuts(self) -> None:
        video_path = Path(self.temporary.name) / "keyboard-movie.mp4"
        video_path.write_bytes(b"video")
        video = LibraryItem(
            str(video_path), "Keyboard Movie", "Film", "Artist",
            2024, 100_000, "video", 1,
        )
        player = Mock()
        player.position.return_value = 30_000
        player.duration.return_value = 100_000
        player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        self.page.player = player
        self.page._replace_queue([video])
        self.page.resize(1200, 800)
        self.page.show()
        self.app.processEvents()
        self.page.set_video_seek_seconds(7)
        player.reset_mock()

        QTest.keyClick(self.page.video, Qt.Key.Key_Right)
        player.setPosition.assert_called_with(37_000)
        QTest.keyClick(
            self.page.video,
            Qt.Key.Key_Left,
            Qt.KeyboardModifier.ShiftModifier,
        )
        player.setPosition.assert_called_with(16_000)

        QTest.keyClick(self.page.video, Qt.Key.Key_Home)
        player.setPosition.assert_called_with(0)
        QTest.keyClick(self.page.video, Qt.Key.Key_3)
        player.setPosition.assert_called_with(30_000)
        QTest.keyClick(self.page.video, Qt.Key.Key_0)
        player.setPosition.assert_called_with(0)

        QTest.keyClick(self.page.video, Qt.Key.Key_Space)
        player.pause.assert_called_once()
        self.page.audio_output.setMuted(False)
        QTest.keyClick(self.page.video, Qt.Key.Key_M)
        self.assertTrue(self.page.audio_output.isMuted())
        QTest.keyClick(self.page.video, Qt.Key.Key_S)
        player.stop.assert_called()

        with patch.object(self.page, "next") as next_track, patch.object(
            self.page, "previous"
        ) as previous_track:
            QTest.keyClick(self.page.video, Qt.Key.Key_N)
            QTest.keyClick(self.page.video, Qt.Key.Key_P)
        next_track.assert_called_once()
        previous_track.assert_called_once()

        QTest.keyClick(self.page.video, Qt.Key.Key_F)
        self.app.processEvents()
        self.assertTrue(self.page._player_fullscreen)
        QTest.keyClick(self.page.video, Qt.Key.Key_F)
        self.app.processEvents()
        self.assertFalse(self.page._player_fullscreen)

        self.page.search.setFocus()
        self.app.processEvents()
        self.assertFalse(self.page.aspect_shortcut.isEnabled())
        QTest.keyClicks(self.page.search, "ac")
        self.assertEqual(self.page.search.text(), "ac")
        self.page.video.setFocus()
        self.app.processEvents()
        self.assertTrue(self.page.aspect_shortcut.isEnabled())

    def test_media_library_suppresses_passive_hover_tooltips(self) -> None:
        tooltip_event = QEvent(QEvent.Type.ToolTip)
        self.assertTrue(self.page.eventFilter(self.page.video, tooltip_event))
        self.assertTrue(
            self.page.eventFilter(self.page.fullscreen_button, tooltip_event)
        )

    def test_video_mode_loads_tall_preview_rows_and_thumbnail_grid(self) -> None:
        first_path = Path(self.temporary.name) / "first.mp4"
        second_path = Path(self.temporary.name) / "second.mp4"
        first_path.write_bytes(b"video")
        second_path.write_bytes(b"video")
        videos = [
            LibraryItem(
                str(first_path), "First Video", "Film", "Artist",
                2024, 60_000, "video", 1,
            ),
            LibraryItem(
                str(second_path), "Second Video", "Film", "Artist",
                2024, 60_000, "video", 1,
            ),
        ]
        source = QPixmap(320, 180)
        source.fill(QColor("orange"))
        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        source.save(buffer, "PNG")
        buffer.close()
        self.page.items = videos
        self.page.media_type_filter.setCurrentIndex(
            self.page.media_type_filter.findData("video")
        )

        with patch(
            "youtube_audio_video_downloader.gui.media.media_player.video_thumbnail_bytes",
            return_value=bytes(payload),
        ):
            self.page.apply_filters()
            self.assertTrue(
                wait_until(lambda: self.page._video_thumbnail_thread is None)
            )

        self.assertEqual(self.page.table.rowCount(), 2)
        self.assertEqual(self.page.table.iconSize(), VIDEO_THUMBNAIL_SIZE)
        self.assertEqual(self.page.table.rowHeight(0), VIDEO_TABLE_ROW_HEIGHT)
        self.assertEqual(self.page.table.horizontalHeaderItem(0).text(), "Title")
        self.assertEqual(self.page.table.horizontalHeaderItem(1).text(), "Artwork")
        self.assertEqual(self.page.table.item(0, 0).text(), "First Video")
        self.assertTrue(self.page.table.item(0, 0).icon().isNull())
        self.assertFalse(self.page.table.item(0, 1).icon().isNull())
        self.assertAlmostEqual(
            self.page.table.columnWidth(1) / self.page.table.rowHeight(0),
            16 / 9,
            places=1,
        )
        self.page.table.selectRow(0)

        QTest.mouseClick(
            self.page.video_view_toggle, Qt.MouseButton.LeftButton
        )
        self.assertIs(
            self.page.media_view_stack.currentWidget(), self.page.video_grid
        )
        self.assertEqual(self.page.video_grid.count(), 2)
        self.assertEqual(self.page.video_grid.item(0).text(), "First Video")
        self.assertFalse(self.page.video_grid.item(0).icon().isNull())
        self.assertTrue(self.page.video_grid.item(0).isSelected())

        self.page.video_grid.clearSelection()
        self.page.video_grid.item(1).setSelected(True)
        self.page.play_selected()
        self.assertEqual([item.path for item in self.page.queue], [str(second_path)])

        QTest.mouseClick(
            self.page.video_view_toggle, Qt.MouseButton.LeftButton
        )
        selected_rows = self.page.table.selectionModel().selectedRows()
        self.assertEqual(len(selected_rows), 1)
        self.assertEqual(
            self.page.table.item(selected_rows[0].row(), 0).data(
                Qt.ItemDataRole.UserRole
            ),
            str(second_path),
        )

    def test_cached_album_art_is_bounded_to_thumbnail_dimensions(self) -> None:
        source = QPixmap(1200, 1200)
        source.fill(QColor("red"))
        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        source.save(buffer, "PNG")
        buffer.close()
        self.page._artwork_cache.clear()
        with patch(
            "youtube_audio_video_downloader.gui.media.media_player.artwork_bytes",
            return_value=bytes(payload),
        ):
            self.page._render_albums()
            self.page._load_next_album_art(self.page._album_art_generation)
        icon = next(iter(self.page._artwork_cache.values()))
        size = icon.actualSize(QSize(1000, 1000))
        self.assertLessEqual(size.width(), 108)
        self.assertLessEqual(size.height(), 108)

    def test_now_playing_art_is_blank_then_shows_embedded_cover(self) -> None:
        self.assertTrue(self.page.now_playing_art.pixmap().isNull())
        source = QPixmap(600, 600)
        source.fill(QColor("blue"))
        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        source.save(buffer, "PNG")
        buffer.close()
        path = Path(self.temporary.name) / "covered.mp3"
        path.write_bytes(b"audio")
        template = media("Covered", 2020, 1000)
        covered = LibraryItem(
            str(path), template.title, template.album, template.artists,
            template.year, template.duration_ms, template.media_type,
            template.modified_ns,
        )
        self.page.player = Mock()
        self.page.queue = [covered]
        self.page.queue_index = 0

        with patch(
            "youtube_audio_video_downloader.gui.media.media_player.artwork_bytes",
            return_value=bytes(payload),
        ):
            self.page._load_current()

        artwork = self.page.now_playing_art.pixmap()
        self.assertFalse(artwork.isNull())
        self.assertLessEqual(artwork.width(), 104)
        self.assertLessEqual(artwork.height(), 104)
        self.page._set_now_playing_art(None)
        self.assertTrue(self.page.now_playing_art.pixmap().isNull())

    def test_audio_playback_detaches_native_video_renderer(self) -> None:
        path = Path(self.temporary.name) / "audio.mp3"
        path.write_bytes(b"test")
        template = media("Audio", 2000, 1000)
        audio = LibraryItem(
            str(path), template.title, template.album, template.artists,
            template.year, template.duration_ms, template.media_type,
            template.modified_ns,
        )
        player = Mock()
        self.page.player = player
        self.page.queue = [audio]
        self.page.queue_index = 0
        self.page._load_current()
        player.setVideoOutput.assert_called_once_with(None)

    def test_decoded_pcm_is_analyzed_on_the_spectrum_worker_thread(self) -> None:
        audio_format = QAudioFormat()
        audio_format.setSampleRate(48_000)
        audio_format.setChannelCount(2)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        time_axis = np.arange(4096, dtype=np.float32) / 48_000
        signal = np.sin(2 * np.pi * 440 * time_axis) * 0.7
        pcm = (np.column_stack((signal, signal)) * 32767).astype(np.int16).tobytes()
        spy = QSignalSpy(self.page.spectrum_ready)

        self.page.audio_buffer_output.audioBufferReceived.emit(
            QAudioBuffer(pcm, audio_format)
        )
        QTest.qWait(100)

        self.assertEqual(self.page._spectrum_worker.thread(), self.page._spectrum_thread)
        self.assertGreaterEqual(len(spy), 1)
        self.assertGreater(max(spy[-1][0]), 0.7)

    def test_duplicate_media_key_commands_are_debounced(self) -> None:
        player = Mock()
        player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        self.page.player = player
        self.page._dispatch_media_command("toggle", "test")
        self.page._dispatch_media_command("toggle", "test-duplicate")
        QTest.qWait(20)
        player.pause.assert_called_once()

    def test_artist_facets_only_show_artists_with_current_search_matches(self) -> None:
        self.page.items = [
            LibraryItem(
                "C:/chal.mp3", "Chal Waha Jate Hai", "Chal Waha Jate Hai",
                "Arijit Singh", 2021, 1000, "audio", 1,
            ),
            LibraryItem(
                "C:/other.mp3", "Other Song", "Other Album",
                "Aarvan", 2020, 1000, "audio", 1,
            ),
        ]
        self.page.apply_filters()
        aarvan = self.page.facets.findItems(
            "Aarvan", Qt.MatchFlag.MatchExactly
        )[0]
        aarvan.setSelected(True)
        self.page.search.blockSignals(True)
        self.page.search.setText("Chal Waha Jate Hai")
        self.page.search.blockSignals(False)
        self.page.apply_filters()

        visible_artists = [
            self.page.facets.item(row).text()
            for row in range(self.page.facets.count())
        ]
        self.assertEqual(visible_artists, ["Arijit Singh"])
        self.assertEqual([item.title for item in self.page.filtered], ["Chal Waha Jate Hai"])

    def test_all_albums_button_clears_the_artist_filter(self) -> None:
        self.page.items = [
            LibraryItem(
                "C:/atif.mp3", "Atif Song", "Atif Album",
                "Atif Aslam", 2020, 1000, "audio", 1,
            ),
            LibraryItem(
                "C:/other.mp3", "Other Song", "Other Album",
                "Other Artist", 2021, 1000, "audio", 1,
            ),
        ]
        self.page.apply_filters()
        self.assertTrue(self.page.all_albums_button.isHidden())
        atif = self.page.facets.findItems(
            "Atif Aslam", Qt.MatchFlag.MatchExactly
        )[0]
        atif.setSelected(True)
        self.page.apply_filters()

        self.assertFalse(self.page.all_albums_button.isHidden())
        self.assertFalse(self.page.all_tracks_button.isHidden())
        self.assertEqual([item.album for item in self.page.filtered], ["Atif Album"])
        self.assertIn("Atif Aslam", self.page.album_browser_context.text())
        self.assertIn("1 album", self.page.album_browser_context.text())

        self.page.open_album(self.page.albums.item(0))
        self.assertEqual(self.page.album_detail_title.text(), "Tracks from: Atif Album")
        self.assertIn("Artists: Atif Aslam", self.page.album_detail_context.text())
        self.assertIn("0 selected", self.page.album_detail_context.text())
        self.assertEqual(self.page.album_back_button.text(), "‹ Albums by Atif Aslam")
        self.page.album_tracks.selectRow(0)
        self.assertIn("1 selected", self.page.album_detail_context.text())

        self.page._show_all_albums()

        self.assertFalse(self.page.facets.selectedItems())
        self.assertTrue(self.page.all_albums_button.isHidden())
        self.assertIn("all artists", self.page.album_browser_context.text())
        self.assertEqual(
            {item.album for item in self.page.filtered},
            {"Atif Album", "Other Album"},
        )

    def test_play_selection_does_not_clear_the_active_artist_filter(self) -> None:
        self.page.items = [
            LibraryItem(
                "C:/atif.mp3", "Atif Song", "Atif Album",
                "Atif Aslam", 2020, 1000, "audio", 1,
            ),
            LibraryItem(
                "C:/other.mp3", "Other Song", "Other Album",
                "Other Artist", 2021, 1000, "audio", 1,
            ),
        ]
        self.page.apply_filters()
        atif = self.page.facets.findItems(
            "Atif Aslam", Qt.MatchFlag.MatchExactly
        )[0]
        atif.setSelected(True)
        self.page.apply_filters()
        self.page.resize(1200, 800)
        self.page.show()
        self.app.processEvents()
        selection_filter = BlankClickSelectionFilter(self.page)
        self.app.installEventFilter(selection_filter)

        try:
            row = self.page.table.item(0, 0)
            QTest.mouseClick(
                self.page.table.viewport(),
                Qt.MouseButton.LeftButton,
                pos=self.page.table.visualItemRect(row).center(),
            )
            self.app.processEvents()
        finally:
            self.app.removeEventFilter(selection_filter)

        self.assertEqual(
            [item.text() for item in self.page.facets.selectedItems()],
            ["Atif Aslam"],
        )
        self.assertEqual([item.album for item in self.page.filtered], ["Atif Album"])
        self.assertEqual(self.page.albums.count(), 1)

    def test_search_closes_album_detail_when_album_no_longer_matches(self) -> None:
        self.page.open_album(self.page.albums.item(0))
        self.assertEqual(self.page.album_stack.currentIndex(), 1)
        self.page.search.blockSignals(True)
        self.page.search.setText("missing")
        self.page.search.blockSignals(False)
        self.page.apply_filters()
        self.assertEqual(self.page.album_stack.currentIndex(), 0)
        self.assertEqual(self.page.album_tracks.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
