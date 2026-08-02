"""Headless interaction tests for the media-library player workspace."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import (  # noqa: E402
    QBuffer, QByteArray, QIODevice, QPoint, QSettings, QSize, Qt,
)
from PyQt6.QtGui import QColor, QIcon, QPixmap  # noqa: E402
from PyQt6.QtMultimedia import QAudioBuffer, QAudioFormat, QMediaPlayer  # noqa: E402
from PyQt6.QtTest import QSignalSpy, QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMenu  # noqa: E402

from youtube_audio_video_downloader.gui.media_player import MediaLibraryPage  # noqa: E402
from youtube_audio_video_downloader.gui.widgets import (  # noqa: E402
    BlankClickSelectionFilter,
)
from youtube_audio_video_downloader.services.library_recommendations import (  # noqa: E402
    LibraryRecommendation,
)
from youtube_audio_video_downloader.services.media_library import LibraryItem  # noqa: E402


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


class MediaPlayerPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.environment_patch = patch.dict(os.environ, {"NVIDIA_API_KEY": ""})
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
        self.page.table.sortItems(5, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.page.table.item(0, 0).text(), "Short")
        self.page.table.sortItems(3, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.page.table.item(0, 0).text(), "Long")

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

    def test_player_controls_have_visible_icons_and_mode_labels(self) -> None:
        self.assertTrue(self.page.play_button.text() == "")
        self.assertFalse(self.page.play_button.icon().isNull())
        self.assertEqual(self.page.library_refresh_button.text(), "Refresh")
        self.assertEqual(self.page.shuffle_button.text(), "Shuffle off")
        self.assertEqual(self.page.repeat_button.text(), "Repeat off")

        self.page._state_changed(QMediaPlayer.PlaybackState.PlayingState)
        self.assertFalse(self.page.play_button.icon().isNull())
        self.assertEqual(self.page.play_button.text(), "")

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
            "youtube_audio_video_downloader.gui.media_player.random.shuffle",
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
            "youtube_audio_video_downloader.gui.media_player.random.shuffle",
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
        self.page.settings.setValue("defaults/agentic_model", "library-agent:test")
        expected = [LibraryRecommendation(item, "Artist matches the request", True)]
        with patch(
            "youtube_audio_video_downloader.gui.media_player.recommend_library_tracks",
            return_value=expected,
        ) as recommendation_mock:
            self.page.recommendation_request.setText("calm Test Artist songs")
            self.page.request_ai_recommendations()
            QTest.qWait(100)

        recommendation_mock.assert_called_once()
        self.assertEqual(
            recommendation_mock.call_args.kwargs["model"], "library-agent:test"
        )
        self.assertTrue(self.page.recommendations.isVisibleTo(self.page))
        self.assertIn("[LOCAL] Calm Song", self.page.recommendations.item(0).text())
        self.assertTrue(self.page.recommendation_button.isEnabled())
        self.assertEqual(self.page.recommendation_button.text(), "Get AI suggestions")

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

    def test_no_local_ai_match_automatically_opens_youtube_search(self) -> None:
        self.page.recommendation_request.setText("Atif songs")
        self.page._last_recommendation_request = "Atif songs"
        online_spy = QSignalSpy(self.page.request_search_song)
        self.page._recommendations_finished([], "")
        QTest.qWait(10)
        self.assertEqual(len(online_spy), 1)
        self.assertEqual(online_spy[0][0], "Atif songs")

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
            "youtube_audio_video_downloader.gui.media_player.artwork_bytes",
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
            "youtube_audio_video_downloader.gui.media_player.artwork_bytes",
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
