from __future__ import annotations

import gc
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

from youtube_audio_video_downloader.gui.main_window import MainWindow  # noqa: E402
from youtube_audio_video_downloader.gui.theme import APP_STYLE  # noqa: E402
from youtube_audio_video_downloader.config.settings import machine_parallel_workers  # noqa: E402
from youtube_audio_video_downloader.version import application_version  # noqa: E402
from youtube_audio_video_downloader.services.album_editor import (  # noqa: E402
    AlbumFolderMetadata,
)


class MainWindowGlobalAiUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.environment_patch = patch.dict(
            os.environ, {"NVIDIA_API_KEY": "", "SERPAPI_API_KEY": ""}
        )
        self.environment_patch.start()
        self.temporary_directory = tempfile.TemporaryDirectory()
        settings = QSettings(
            str(Path(self.temporary_directory.name) / "settings.ini"),
            QSettings.Format.IniFormat,
        )
        settings.setValue("defaults/agentic_model", "global-agent:test")
        self.settings_patch = patch(
            "youtube_audio_video_downloader.gui.main_window.QSettings",
            return_value=settings,
        )
        self.models_patch = patch(
            "youtube_audio_video_downloader.gui.main_window.available_ollama_models",
            return_value=["global-agent:test"],
        )
        self.settings_patch.start()
        self.models_patch.start()
        self.data_directory = Path(self.temporary_directory.name).resolve()
        self.window = MainWindow(data_directory=self.data_directory)

    def tearDown(self) -> None:
        self.window._workspace_autosave.stop()
        self.window.media_library.shutdown()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        gc.collect()
        self.models_patch.stop()
        self.settings_patch.stop()
        self.environment_patch.stop()
        self.temporary_directory.cleanup()

    def test_stop_is_enabled_only_when_a_worker_exists(self) -> None:
        self.assertFalse(self.window.cancel_button.isEnabled())

        self.window._active_worker = object()
        self.window._sync_stop_button()
        self.assertTrue(self.window.cancel_button.isEnabled())

        self.window._active_worker = None
        self.window._parallel_jobs = {object(): ("search_song", object())}
        self.window._set_idle_state("Completed")
        self.assertTrue(self.window.cancel_button.isEnabled())
        self.assertEqual(self.window.activity_progress.minimum(), 0)
        self.assertEqual(self.window.activity_progress.maximum(), 0)

        self.window._parallel_jobs.clear()
        self.window._sync_stop_button()
        self.assertFalse(self.window.cancel_button.isEnabled())
        self.assertIn("QPushButton#dangerButton:disabled", APP_STYLE)

    def test_search_uses_global_model_without_a_local_model_field(self) -> None:
        self.assertFalse(hasattr(self.window, "song_search_model"))
        self.window.song_search_text.setText("find a song")

        with patch.object(self.window, "_start_operation") as start:
            self.window._start_song_search()

        operation, params = start.call_args.args
        self.assertEqual(operation, "search_song")
        self.assertEqual(params["model"], "global-agent:test")

        self.window._append_log("[AI-PROVIDER] NVIDIA NIM | model=z-ai/glm-5.2")
        self.assertIn("NVIDIA NIM", self.window.ai_status_badge.text())
        self.window._append_log("[AI-STATIC-FALLBACK] Library ranking | offline")
        self.assertIn("STATIC FALLBACK", self.window.ai_status_badge.text())

    def test_dashboard_describes_the_ai_assisted_workflow(self) -> None:
        labels = {label.text() for label in self.window.findChildren(QLabel)}
        self.assertIn("AI-assisted media workflows", labels)
        self.assertNotIn("Liquid-glass batch processing", labels)
        self.assertEqual(
            self.window.settings_nvidia_api_key.echoMode(),
            QLineEdit.EchoMode.Password,
        )
        self.assertEqual(
            self.window.settings_serpapi_api_key.echoMode(),
            QLineEdit.EchoMode.Password,
        )
        self.assertEqual(self.window.settings_ai_provider.currentData(), "ollama")
        self.assertFalse(self.window.settings_ai_model.isEnabled())
        self.assertEqual(
            {
                self.window.settings_ai_provider.itemData(index)
                for index in range(self.window.settings_ai_provider.count())
            },
            {
                "ollama", "nvidia", "openai", "anthropic", "google", "groq",
                "huggingface", "openrouter", "opencode", "custom",
            },
        )
        self.assertEqual(
            self.window.version_label.text(), f"Version {application_version()}"
        )
        self.assertIn(application_version(), self.window.windowTitle())

    def test_every_task_workspace_has_an_independent_ai_switch(self) -> None:
        expected = {
            "search_song",
            "audio",
            "video",
            "album",
            "jukebox",
            "track_reorder",
            "edit_media",
            "album_consolidator",
            "utilities",
        }
        self.assertEqual(set(self.window._tool_ai_checks), expected)
        for operation in expected:
            checkbox = self.window._tool_ai_checks[operation]
            self.assertTrue(checkbox.text().startswith("Use AI for"))
            self.assertFalse(checkbox.isHidden())

    def test_album_enricher_inherits_album_consolidator_ai_switch(self) -> None:
        checkbox = self.window._tool_ai_checks["album_consolidator"]
        checkbox.setChecked(False)

        self.assertFalse(self.window._ai_enabled_for("album_consolidator"))
        self.assertFalse(self.window._ai_enabled_for("album_metadata_enricher"))

    def test_jukebox_ai_switch_persists_and_controls_inline_extraction(self) -> None:
        checkbox = self.window._tool_ai_checks["jukebox"]
        checkbox.setChecked(False)

        self.assertFalse(self.window._ai_enabled_for("jukebox"))
        self.assertFalse(
            self.window.settings.value("ai/tools/jukebox", True, type=bool)
        )

    def test_global_settings_displays_application_data_directory(self) -> None:
        self.assertEqual(
            Path(self.window.settings_data_directory.text()),
            self.data_directory,
        )
        self.assertEqual(
            Path(self.window._metadata_tracker_file),
            self.data_directory / "album_enrichment_tracker.json",
        )

    def test_global_settings_are_grouped_into_persistent_collapsible_sections(self) -> None:
        self.assertEqual(
            set(self.window.settings_sections),
            {
                "batch_network",
                "audio_metadata",
                "ai_providers",
                "behavior_privacy",
                "storage_appearance",
            },
        )
        self.assertFalse(self.window.settings_sections["batch_network"].body.isHidden())
        self.assertTrue(self.window.settings_sections["audio_metadata"].body.isHidden())

        self.window.settings_sections["audio_metadata"].set_expanded(True)
        self.assertTrue(
            self.window.settings.value(
                "ui/settings_sections/audio_metadata", False, type=bool
            )
        )

    def test_serpapi_key_is_saved_and_applied_without_operation_parameters(self) -> None:
        self.window.settings_serpapi_api_key.setText("serpapi-secret")

        with (
            patch.object(self.window, "_save_data_directory", return_value=False),
            patch.object(QMessageBox, "information"),
        ):
            self.window._save_defaults()

        self.assertEqual(
            self.window.settings.value("defaults/serpapi_api_key"),
            "serpapi-secret",
        )
        self.assertEqual(os.environ.get("SERPAPI_API_KEY"), "serpapi-secret")

    def test_saved_blank_provider_keys_override_launch_environment(self) -> None:
        self.window.settings.setValue("defaults/nvidia_api_key", "")
        self.window.settings.setValue("defaults/serpapi_api_key", "")
        os.environ["NVIDIA_API_KEY"] = "nvapi-from-launch-environment"
        os.environ["SERPAPI_API_KEY"] = "serpapi-from-launch-environment"

        self.window._configure_ai_from_settings()

        self.assertNotIn("NVIDIA_API_KEY", os.environ)
        self.assertNotIn("SERPAPI_API_KEY", os.environ)
        self.assertEqual(
            self.window._saved_secret("defaults/nvidia_api_key", "NVIDIA_API_KEY"),
            "",
        )

    def test_cleared_nvidia_key_remains_cleared_after_save_and_restart(self) -> None:
        self.window.settings_ai_provider.setCurrentIndex(
            self.window.settings_ai_provider.findData("nvidia")
        )
        self.window.settings_nvidia_api_key.setText("")
        os.environ["NVIDIA_API_KEY"] = "nvapi-old-value"

        with (
            patch.object(self.window, "_save_data_directory", return_value=False),
            patch.object(QMessageBox, "information"),
        ):
            self.window._save_defaults()

        self.assertEqual(self.window.settings.value("defaults/nvidia_api_key"), "")
        self.assertNotIn("NVIDIA_API_KEY", os.environ)

    def test_provider_specific_credentials_are_isolated_and_applied(self) -> None:
        self.window.settings_ai_provider.setCurrentIndex(
            self.window.settings_ai_provider.findData("openai")
        )
        self.window.settings_ai_api_key.setText("openai-secret")
        self.window.settings_ai_model.setText("gpt-test")
        self.window.settings_ai_provider.setCurrentIndex(
            self.window.settings_ai_provider.findData("huggingface")
        )
        self.assertNotEqual(self.window.settings_ai_api_key.text(), "openai-secret")
        self.window.settings_ai_api_key.setText("hf-secret")
        self.window.settings_ai_model.setText("org/model:fastest")

        with (
            patch.object(self.window, "_save_data_directory", return_value=False),
            patch.object(QMessageBox, "information"),
        ):
            self.window._save_defaults()

        self.assertEqual(self.window.settings.value("defaults/ai_provider"), "huggingface")
        self.assertEqual(
            self.window.settings.value("defaults/ai_providers/openai/api_key"),
            "openai-secret",
        )
        self.assertEqual(
            self.window.settings.value("defaults/ai_providers/huggingface/api_key"),
            "hf-secret",
        )
        self.assertEqual(
            os.environ.get("YOUTUBE_MEDIA_STUDIO_AI_PROVIDER"), "huggingface"
        )
        self.assertEqual(os.environ.get("YOUTUBE_MEDIA_STUDIO_AI_API_KEY"), "hf-secret")

        os.environ["NVIDIA_API_KEY"] = "nvapi-restored-by-launch-environment"
        self.window._configure_ai_from_settings()
        self.assertNotIn("NVIDIA_API_KEY", os.environ)

    def test_active_ai_identity_refreshes_saved_groq_settings(self) -> None:
        self.window.settings.setValue("defaults/ai_provider", "groq")
        self.window.settings.setValue(
            "defaults/ai_providers/groq/api_key", "groq-secret"
        )
        self.window.settings.setValue(
            "defaults/ai_providers/groq/model", "openai/gpt-oss-120b"
        )

        self.assertEqual(
            self.window._active_ai_identity(),
            ("Groq", "openai/gpt-oss-120b"),
        )

    def test_track_reorder_clear_resets_folder_list_and_saved_value(self) -> None:
        self.window.track_reorder_folder.set_text("C:/Music/Album")
        self.window.track_reorder_list.addItem("01 Song.mp3")
        self.window.settings.setValue("workspace/track_reorder_folder", "C:/Music/Album")

        self.window._clear_track_reorder()

        self.assertEqual(self.window.track_reorder_folder.text(), "")
        self.assertEqual(self.window.track_reorder_list.count(), 0)
        self.assertIsNone(self.window.settings.value("workspace/track_reorder_folder"))

    def test_restore_silently_discards_missing_track_reorder_folder(self) -> None:
        missing = self.data_directory / "album_tracks" / "Removed Album (2026)"
        self.window.settings.setValue(
            "workspace/track_reorder_folder", str(missing)
        )
        self.window.track_reorder_folder.set_text("")
        self.window._track_folder_load_timer.stop()

        with patch.object(self.window, "_append_log") as append_log:
            self.window._restore_workspace_state()
            self.window._track_folder_load_timer.stop()

        self.assertEqual(self.window.track_reorder_folder.text(), "")
        self.assertIsNone(
            self.window.settings.value("workspace/track_reorder_folder")
        )
        self.assertFalse(
            any(
                "Could not load album folder" in str(call.args[0])
                for call in append_log.call_args_list
            )
        )

    def test_title_bar_displays_the_application_logo(self) -> None:
        logo = self.window.title_bar.findChild(QLabel, "appLogo")
        self.assertIsNotNone(logo)
        self.assertIsNotNone(logo.pixmap())
        self.assertFalse(logo.pixmap().isNull())

    def test_blank_click_clears_highlights_across_the_application(self) -> None:
        library = self.window.media_library
        library.folder_list.addItem("C:/Music")
        library.folder_list.setCurrentRow(0)
        library.queue_list.addItem("Selected queue song")
        library.queue_list.setCurrentRow(0)
        self.window.track_reorder_list.addItem("Selected reorder song")
        self.window.track_reorder_list.setCurrentRow(0)
        self.window._set_page(13)
        self.window.show()
        self.app.processEvents()

        QTest.mouseClick(library.now_playing, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertFalse(library.folder_list.selectedItems())
        self.assertFalse(library.queue_list.selectedItems())
        self.assertFalse(self.window.track_reorder_list.selectedItems())

    def test_clicking_an_item_keeps_that_item_and_clears_other_views(self) -> None:
        library = self.window.media_library
        library.folder_list.addItem("C:/Music")
        library.folder_list.setCurrentRow(0)
        library.queue_list.addItem("Queue song")
        self.window._set_page(13)
        library.queue_toggle_button.setChecked(True)
        self.window.show()
        self.app.processEvents()

        queue_rect = library.queue_list.visualItemRect(library.queue_list.item(0))
        QTest.mouseClick(
            library.queue_list.viewport(),
            Qt.MouseButton.LeftButton,
            pos=queue_rect.center(),
        )
        self.app.processEvents()

        self.assertEqual(len(library.queue_list.selectedItems()), 1)
        self.assertFalse(library.folder_list.selectedItems())

    def test_clicking_empty_queue_space_clears_its_selection(self) -> None:
        library = self.window.media_library
        library.queue_list.addItem("Queue song")
        library.queue_list.setCurrentRow(0)
        self.window._set_page(13)
        library.queue_toggle_button.setChecked(True)
        self.window.show()
        self.app.processEvents()

        QTest.mouseClick(
            library.queue_list.viewport(),
            Qt.MouseButton.LeftButton,
            pos=library.queue_list.viewport().rect().bottomRight(),
        )
        self.app.processEvents()

        self.assertFalse(library.queue_list.selectedItems())

    def test_library_song_handoff_opens_edit_file_and_loads_metadata(self) -> None:
        selected = self.data_directory / "selected.mp3"
        selected.write_bytes(b"test media")
        with patch.object(self.window, "_load_edit_file") as load:
            self.window._edit_library_file(str(selected))

        self.assertEqual(self.window.pages.currentIndex(), 7)
        self.assertEqual(self.window.edit_file_input.text(), str(selected))
        load.assert_called_once_with(str(selected))

    def test_library_album_handoffs_preserve_move_destination(self) -> None:
        album = self.data_directory / "Album (2024)"
        album.mkdir()
        self.window.album_consolidator_destination.set_text("D:/Do not change")

        self.window._open_library_album_enricher(str(album))

        self.assertEqual(self.window.pages.currentIndex(), 9)
        self.assertEqual(self.window.album_consolidator_source.text(), str(album))
        self.assertEqual(
            self.window.album_consolidator_destination.text(), "D:/Do not change"
        )

        self.window._open_library_track_reorder(str(album))
        self.assertEqual(self.window.pages.currentIndex(), 6)
        self.assertEqual(self.window.track_reorder_folder.text(), str(album))

    def test_library_album_handoff_opens_bulk_album_editor(self) -> None:
        album = self.data_directory / "Album (2024)"
        album.mkdir()
        with patch.object(self.window, "_load_edit_album_folder") as load:
            self.window._edit_library_album(str(album))

        self.assertEqual(self.window.pages.currentIndex(), 8)
        self.assertEqual(self.window.edit_album_folder.text(), str(album))
        load.assert_called_once_with(str(album))

    def test_album_editor_confirms_and_starts_one_bulk_operation(self) -> None:
        album = self.data_directory / "Album"
        album.mkdir()
        summary = AlbumFolderMetadata(
            album,
            (album / "one.mp3", album / "two.flac"),
            "Old Album",
            "2000",
            "Old Artist",
        )
        self.window.edit_album_folder.set_text(str(album))
        self.window.edit_album_name.setText("New Album")
        self.window.edit_album_year.setText("2026")
        self.window.edit_album_artist.setText("New Artist, Guest Artist")
        with (
            patch(
                "youtube_audio_video_downloader.gui.main_window.inspect_album_folder",
                return_value=summary,
            ),
            patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch.object(self.window, "_start_operation") as start,
        ):
            self.window._start_edit_album()

        start.assert_called_once()
        operation, params = start.call_args.args
        self.assertEqual(operation, "edit_album")
        self.assertEqual(
            params["metadata"],
            {
                "album": "New Album",
                "year": "2026",
                "artists": "New Artist, Guest Artist",
            },
        )
        self.assertFalse(params["ai_enabled"])

    def test_album_enricher_name_is_used_in_the_workspace(self) -> None:
        labels = {
            widget.text()
            for widget in self.window.findChildren(QLabel)
        }
        buttons = {
            button.text()
            for button in self.window.findChildren(QPushButton)
        }
        self.assertIn("1. Album enricher", labels)
        self.assertIn("Run album enricher", buttons)

    def test_move_enrichment_is_default_on_and_can_be_disabled(self) -> None:
        self.assertTrue(self.window.album_move_perform_enrichment.isChecked())
        self.assertTrue(self.window.album_move_enrich_all_destination.isEnabled())

        self.window.album_move_perform_enrichment.setChecked(False)
        params = self.window._album_consolidator_params()

        self.assertFalse(params["perform_enrichment"])
        self.assertFalse(self.window.album_move_enrich_all_destination.isEnabled())

        self.window._save_workspace_state()
        self.assertFalse(
            self.window.settings.value(
                "workspace/album_move_perform_enrichment", type=bool
            )
        )

    def test_reset_restores_move_enrichment_default(self) -> None:
        reset_data = self.data_directory / "portable" / "YouTubeMediaStudioData"
        self.window.album_move_perform_enrichment.setChecked(False)
        self.window.settings_ai_provider.setCurrentIndex(
            self.window.settings_ai_provider.findData("nvidia")
        )
        self.window.settings_nvidia_api_key.setText("nvapi-secret")
        self.window.settings_serpapi_api_key.setText("serpapi-secret")
        self.window.settings_nvidia_model.setText("hosted:model")
        self.window.settings_agentic_model.setCurrentText("local:model")
        self.window.settings_workers.setValue(1)
        self.window.audio_input.add_entry("Song", {"ytb_link": "https://example.test"})
        self.window.audio_output.set_text("C:/Music")
        self.window.track_reorder_folder.set_text("C:/Music/Album")
        self.window.edit_file_input.set_text("C:/Music/song.mp3")
        self.window.album_consolidator_source.set_text("C:/Music/Source")
        self.window.media_library.folder_list.addItem("C:/Music")

        with (
            patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch.object(QMessageBox, "information"),
            patch(
                "youtube_audio_video_downloader.gui.main_window.default_data_directory",
                return_value=reset_data,
            ),
            patch(
                "youtube_audio_video_downloader.gui.main_window.save_data_directory_choice"
            ),
        ):
            self.window._reset_app()

        self.assertEqual(self.window.settings_nvidia_api_key.text(), "")
        self.assertEqual(self.window.settings_serpapi_api_key.text(), "")
        self.assertEqual(self.window.settings_nvidia_model.text(), "")
        self.assertEqual(self.window.settings_ai_provider.currentData(), "ollama")
        self.assertEqual(self.window.settings_agentic_model.currentText(), "")
        self.assertEqual(self.window.settings_workers.value(), machine_parallel_workers())
        self.assertEqual(self.window.settings_data_directory.text(), str(reset_data))
        self.assertEqual(self.window.audio_input.data(), {})
        self.assertEqual(self.window.audio_output.text(), "")
        self.assertEqual(self.window.track_reorder_folder.text(), "")
        self.assertEqual(self.window.edit_file_input.text(), "")
        self.assertEqual(self.window.album_consolidator_source.text(), "")
        self.assertTrue(self.window.album_move_perform_enrichment.isChecked())
        self.assertEqual(self.window.media_library.folder_list.count(), 0)
        self.assertIn("STATIC FALLBACK", self.window.ai_status_badge.text())

if __name__ == "__main__":
    unittest.main()
