from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.gui.operations import OperationSummary
from youtube_audio_video_downloader.gui.main_window import MainWindow
from youtube_audio_video_downloader.gui.workers import (
    OperationWorker,
    SignalTextStream,
    _estimate_operation_total,
    _item_result_from_log,
    _progress_from_log,
    _progress_phase_from_log,
    estimate_eta_seconds,
    format_eta,
    operation_display_name,
    running_operation_text,
)


class GuiWorkerProgressTest(unittest.TestCase):

    def test_signal_stream_keeps_partial_prints_separate_per_thread(self) -> None:
        lines: list[str] = []
        stream = SignalTextStream(lines.append)
        stream.write("[ENRICH-SKIPPED] first.mp3")
        thread = threading.Thread(
            target=lambda: stream.write("[ENRICH-SKIPPED] second.mp3\n")
        )
        thread.start()
        thread.join()
        stream.write("\n")

        self.assertCountEqual(
            lines,
            ["[ENRICH-SKIPPED] first.mp3", "[ENRICH-SKIPPED] second.mp3"],
        )

    def test_later_phase_resets_total_and_album_result_advances_progress(self) -> None:
        worker = OperationWorker("album_metadata_enricher", {})
        worker._progress_total = 701
        worker._progress_current = 701
        progress: list[tuple[int, int, str]] = []
        phases: list[tuple[str, int]] = []
        worker.progress.connect(lambda current, total, text: progress.append((current, total, text)))
        worker.phase_changed.connect(lambda label, total: phases.append((label, total)))

        worker._forward_log(
            "[PROGRESS-PHASE] Wikipedia album ordering | total=120"
        )
        worker._forward_log(
            "[REORDER-SKIPPED] Album: no verified Wikipedia track table found"
        )

        self.assertEqual(progress[0][:2], (0, 120))
        self.assertEqual(progress[1][:2], (1, 120))
        self.assertEqual(phases, [("Wikipedia album ordering", 120)])
        self.assertEqual(
            _progress_phase_from_log(
                "[PROGRESS-PHASE] Wikipedia album ordering | total=120"
            )[0],
            120,
        )

    @patch("youtube_audio_video_downloader.gui.workers.execute_operation")
    def test_safe_operation_uses_configured_global_attempts(self, execute_mock) -> None:
        execute_mock.side_effect = [
            OSError("temporary one"),
            OSError("temporary two"),
            OperationSummary(operation="search_song", total=1),
        ]
        worker = OperationWorker("search_song", {"retries": 3})
        worker.cancellation_token.wait = lambda _seconds: None

        result = worker._execute_with_retries()

        self.assertEqual(result.operation, "search_song")
        self.assertEqual(execute_mock.call_count, 3)

    @patch("youtube_audio_video_downloader.gui.workers.execute_operation")
    def test_mutating_batch_is_not_replayed_as_a_whole(self, execute_mock) -> None:
        execute_mock.side_effect = OSError("partial move failed")
        worker = OperationWorker("album_consolidator", {"retries": 5})

        with self.assertRaises(OSError):
            worker._execute_with_retries()

        execute_mock.assert_called_once()

    def test_eta_blends_history_with_current_progress(self) -> None:
        eta = estimate_eta_seconds(
            current=2,
            total=10,
            elapsed_seconds=6.0,
            historical_seconds_per_item=4.0,
        )
        self.assertIsNotNone(eta)
        self.assertGreater(eta, 24.0)
        self.assertLess(eta, 32.0)
        self.assertEqual(format_eta(eta), "00:29")

    def test_eta_uses_history_before_the_first_item_finishes(self) -> None:
        self.assertEqual(
            format_eta(estimate_eta_seconds(0, 10, 0.5, 2.5)),
            "00:25",
        )
        self.assertEqual(format_eta(estimate_eta_seconds(0, 10, 0.5)), "Estimating…")

    def test_album_activity_uses_workspace_and_subsection_names(self) -> None:
        self.assertEqual(
            running_operation_text(
                "album_metadata_enricher", "Updated metadata: Song.mp3"
            ),
            "Album Consolidator · Running Album enricher · Updated metadata: Song.mp3",
        )
        self.assertEqual(
            running_operation_text("album_consolidator", "Moved: Song.mp3"),
            "Album Consolidator · Running Move into album folders · Moved: Song.mp3",
        )
        self.assertEqual(
            operation_display_name("album_metadata_enricher"),
            "Album Consolidator · Album enricher",
        )

    def test_completed_file_progress_is_mirrored_to_live_log_signal(self) -> None:
        worker = OperationWorker("album_consolidator", {})
        worker._progress_total = 4
        logs: list[str] = []
        progress: list[tuple[int, int, str]] = []
        worker.log.connect(logs.append)
        worker.progress.connect(lambda current, total, text: progress.append((current, total, text)))

        worker._forward_log("[MOVED] song.mp3 -> Album/song.mp3")

        self.assertEqual(progress[0][:2], (1, 4))
        self.assertEqual(logs[0], "[MOVED] song.mp3 -> Album/song.mp3")
        self.assertIn("[PROGRESS] 1/4", logs[1])
    def test_consolidator_total_includes_move_and_post_move_audio_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "nested").mkdir()
            (source / "one.mp3").write_bytes(b"")
            (source / "nested" / "two.mkv").write_bytes(b"")
            (source / "notes.txt").write_text("ignore", encoding="utf-8")

            self.assertEqual(
                _estimate_operation_total(
                    "album_consolidator", {"source_folder": str(source)}
                ),
                3,
            )

    def test_download_totals_match_enabled_rows_and_parent_level_skips(self) -> None:
        audio_data = {
            "Enabled": {"download": True},
            "Disabled": {"download": "false"},
        }
        self.assertEqual(
            _estimate_operation_total("audio", {"input_data": audio_data}), 1
        )

        album_data = {
            "Disabled album": {
                "download": False,
                "tracks": [{"One": {}}, {"Two": {}}, {"Three": {}}],
            },
            "Enabled album": {
                "download": True,
                "tracks": [{"Four": {}}, {"Five": {}}],
            },
        }
        self.assertEqual(
            _estimate_operation_total("album", {"input_data": album_data}), 3
        )
        self.assertEqual(
            _estimate_operation_total("jukebox", {"input_data": album_data}), 3
        )

    def test_download_eta_history_is_separated_by_timing_and_output_settings(self) -> None:
        base = {
            "workers": 4,
            "min_delay": 10,
            "max_delay": 25,
            "preferred_mp3_quality": "320",
            "audio_sample_rate": "44100",
            "mode": "download",
        }
        original = MainWindow._eta_profile_key("audio", base)
        changed_delay = MainWindow._eta_profile_key(
            "audio", {**base, "max_delay": 60}
        )
        changed_mode = MainWindow._eta_profile_key(
            "audio", {**base, "mode": "tag-existing"}
        )

        self.assertNotEqual(original, changed_delay)
        self.assertNotEqual(original, changed_mode)

    def test_enrichment_eta_history_is_separated_by_phase(self) -> None:
        params = {"workers": 8, "source_folder": "C:/Music"}

        enrichment = MainWindow._eta_profile_key(
            "album_metadata_enricher", params, "album_enrichment"
        )
        ordering = MainWindow._eta_profile_key(
            "album_metadata_enricher", params, "wikipedia_album_ordering"
        )

        self.assertNotEqual(enrichment, ordering)
        self.assertIn("phase=album_enrichment", enrichment)
        self.assertIn("phase=wikipedia_album_ordering", ordering)

    def test_consolidator_advances_only_on_terminal_file_events(self) -> None:
        self.assertEqual(
            _progress_from_log("album_consolidator", "[TAGGED] Added metadata: one.mp3")[0],
            False,
        )
        completed, detail = _progress_from_log(
            "album_consolidator", "[MOVED] one.mp3 -> Album/one.mp3"
        )
        self.assertTrue(completed)
        self.assertIn("Moved", detail)
        enriched, enrich_detail = _progress_from_log(
            "album_consolidator", "[ENRICHED] one.mp3: year, artwork"
        )
        self.assertTrue(enriched)
        self.assertIn("Updated metadata", enrich_detail)

    def test_metadata_enrichment_failure_advances_one_file(self) -> None:
        completed, detail = _progress_from_log(
            "album_metadata_enricher", "[ENRICH-FAILED] one.mp3: timeout"
        )
        self.assertTrue(completed)
        self.assertIn("Metadata lookup failed", detail)

    def test_download_result_identifies_live_editor_item(self) -> None:
        self.assertEqual(
            _item_result_from_log(
                "audio", "[DOWNLOADED] Paisa Hai Toh | file=Paisa Hai Toh.mp3"
            ),
            ("Paisa Hai Toh", True),
        )
        self.assertEqual(
            _item_result_from_log("album", "[FAILED] Album / Track | reason=locked"),
            ("Album / Track", False),
        )


if __name__ == "__main__":
    unittest.main()
