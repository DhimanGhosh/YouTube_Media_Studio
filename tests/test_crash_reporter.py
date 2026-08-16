"""Tests for persistent desktop crash diagnostics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from youtube_audio_video_downloader.gui.runtime.crash_reporter import (
    CrashReporter,
    DisabledCrashReporter,
)


class CrashReporterTest(unittest.TestCase):
    def test_disabled_reporter_creates_no_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "crash_reports"
            reporter = DisabledCrashReporter()
            reporter.install()
            reporter.log("TEST", "must not persist")
            reporter.exception("TEST", RuntimeError("must not persist"))
            reporter.finalize(1)

            self.assertFalse(target.exists())

    def test_report_contains_environment_events_and_clean_footer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            reporter = CrashReporter(temporary)
            reporter.install()
            reporter.log("TEST", "playback event")
            reporter.finalize(0)
            content = reporter.path.read_text(encoding="utf-8")

        self.assertIn("Python:", content)
        self.assertIn("Qt:", content)
        self.assertIn("[TEST] playback event", content)
        self.assertIn("Clean Python shutdown; exit_code=0", content)

    def test_report_records_traceback_details(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            reporter = CrashReporter(temporary)
            reporter.install()
            try:
                raise RuntimeError("diagnostic example")
            except RuntimeError as exc:
                reporter.exception("TEST-ERROR", exc)
            reporter.finalize(1)
            content = Path(reporter.path).read_text(encoding="utf-8")

        self.assertIn("RuntimeError: diagnostic example", content)
        self.assertIn("test_report_records_traceback_details", content)


if __name__ == "__main__":
    unittest.main()
