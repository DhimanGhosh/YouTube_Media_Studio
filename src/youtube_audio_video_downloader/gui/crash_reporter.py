"""Persistent diagnostics for Python, Qt, threads, and native fatal signals."""

from __future__ import annotations

import faulthandler
import os
import platform
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import TextIO

from PyQt6.QtCore import (
    QMessageLogContext,
    QTimer,
    QtMsgType,
    qInstallMessageHandler,
    qVersion,
)


class CrashReporter:
    """Write a timestamped session trace that survives native process failure."""

    def __init__(self, directory: str | Path | None = None) -> None:
        root = Path(directory) if directory else Path.cwd() / "crash_reports"
        root.mkdir(parents=True, exist_ok=True)
        self._rotate(root)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = root / f"crash-report-{stamp}-pid{os.getpid()}.log"
        self._file: TextIO = self.path.open("w", encoding="utf-8", buffering=1)
        self._lock = threading.RLock()
        self._previous_qt_handler = None
        self._previous_excepthook = sys.excepthook
        self._previous_threading_hook = threading.excepthook
        self._previous_unraisable_hook = sys.unraisablehook
        self._installed = False
        self._heartbeat_timer: QTimer | None = None
        self._watchdog_stop = threading.Event()
        self._last_heartbeat = time.monotonic()

    def install(self) -> None:
        if self._installed:
            return
        self._installed = True
        self._write_header()
        faulthandler.enable(file=self._file, all_threads=True)
        self._previous_qt_handler = qInstallMessageHandler(self._qt_message)
        sys.excepthook = self._exception_hook
        threading.excepthook = self._thread_exception_hook
        sys.unraisablehook = self._unraisable_hook
        print(f"[DIAGNOSTICS] Session/crash report: {self.path}", flush=True)

    def log(self, component: str, message: str) -> None:
        self._write(f"[{component}] {message}")

    def start_hang_watchdog(self, threshold_seconds: float = 8.0) -> None:
        """Dump every Python thread if the Qt event loop stops responding."""

        if self._heartbeat_timer is not None:
            return
        self._last_heartbeat = time.monotonic()
        timer = QTimer()
        timer.setInterval(500)
        timer.timeout.connect(self._heartbeat)
        timer.start()
        self._heartbeat_timer = timer
        self._watchdog_stop.clear()

        def watch() -> None:
            reported = False
            while not self._watchdog_stop.wait(2.0):
                lag = time.monotonic() - self._last_heartbeat
                if lag >= threshold_seconds and not reported:
                    self._write(
                        f"[HANG-WATCHDOG] GUI heartbeat missing for {lag:.1f}s; "
                        "dumping all Python thread stacks"
                    )
                    with self._lock:
                        faulthandler.dump_traceback(file=self._file, all_threads=True)
                        self._file.flush()
                    reported = True
                elif lag < 2.0:
                    reported = False

        thread = threading.Thread(
            target=watch,
            name="gui-hang-watchdog",
            daemon=True,
        )
        thread.start()
        self.log("WATCHDOG", f"GUI hang threshold={threshold_seconds:.1f}s")

    def exception(self, component: str, exc: BaseException) -> None:
        self._write(f"[{component}] {type(exc).__name__}: {exc}")
        with self._lock:
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=self._file)
            self._file.flush()

    def finalize(self, exit_code: int | None = None) -> None:
        if not self._installed:
            return
        self.log("SHUTDOWN", f"Clean Python shutdown; exit_code={exit_code}")
        self._watchdog_stop.set()
        if self._heartbeat_timer is not None:
            self._heartbeat_timer.stop()
            self._heartbeat_timer = None
        qInstallMessageHandler(self._previous_qt_handler)
        sys.excepthook = self._previous_excepthook
        threading.excepthook = self._previous_threading_hook
        sys.unraisablehook = self._previous_unraisable_hook
        if faulthandler.is_enabled():
            faulthandler.disable()
        self._installed = False
        self._file.close()

    def _heartbeat(self) -> None:
        self._last_heartbeat = time.monotonic()

    def _write_header(self) -> None:
        self._file.write("YouTube Media Studio diagnostic report\n")
        self._file.write("=" * 72 + "\n")
        self._file.write(f"Started: {datetime.now().astimezone().isoformat()}\n")
        self._file.write(f"PID: {os.getpid()}\n")
        self._file.write(f"Python: {sys.version}\n")
        self._file.write(f"Qt: {qVersion()}\n")
        self._file.write(f"Platform: {platform.platform()}\n")
        self._file.write(f"Executable: {sys.executable}\n")
        self._file.write(f"Command: {sys.argv!r}\n")
        self._file.write(f"Working directory: {Path.cwd()}\n")
        self._file.write(
            "A missing clean-shutdown footer means the process was terminated "
            "or failed below Python.\n"
        )
        self._file.write("=" * 72 + "\n")
        self._file.flush()

    def _write(self, message: str) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        thread = threading.current_thread()
        with self._lock:
            self._file.write(
                f"{timestamp} [{thread.name}:{thread.ident}] {message.rstrip()}\n"
            )
            self._file.flush()

    def _qt_message(
        self, message_type: QtMsgType, context: QMessageLogContext, message: str
    ) -> None:
        location = ""
        if context.file:
            location = f" {context.file}:{context.line}"
        category = context.category or "qt"
        self._write(f"[QT/{message_type.name}/{category}]{location} {message}")
        try:
            sys.__stderr__.write(f"{message}\n")
            sys.__stderr__.flush()
        except (AttributeError, OSError):
            pass

    def _exception_hook(self, exc_type, exc, tb) -> None:
        self._write(f"[UNCAUGHT] {exc_type.__name__}: {exc}")
        with self._lock:
            traceback.print_exception(exc_type, exc, tb, file=self._file)
            self._file.flush()
        self._previous_excepthook(exc_type, exc, tb)

    def _thread_exception_hook(self, args: threading.ExceptHookArgs) -> None:
        self._write(
            f"[THREAD-UNCAUGHT/{args.thread.name}] "
            f"{args.exc_type.__name__}: {args.exc_value}"
        )
        with self._lock:
            traceback.print_exception(
                args.exc_type, args.exc_value, args.exc_traceback, file=self._file
            )
            self._file.flush()
        self._previous_threading_hook(args)

    def _unraisable_hook(self, args) -> None:
        self._write(f"[UNRAISABLE] {args.exc_type.__name__}: {args.exc_value}")
        with self._lock:
            traceback.print_exception(
                args.exc_type, args.exc_value, args.exc_traceback, file=self._file
            )
            self._file.flush()
        self._previous_unraisable_hook(args)

    @staticmethod
    def _rotate(root: Path, keep: int = 12) -> None:
        reports = sorted(
            root.glob("crash-report-*.log"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for old in reports[keep - 1 :]:
            try:
                old.unlink()
            except OSError:
                pass


class DisabledCrashReporter:
    """No-op reporter used when the user has disabled persistent diagnostics."""

    path: Path | None = None

    def install(self) -> None:
        return

    def log(self, component: str, message: str) -> None:
        return

    def start_hang_watchdog(self, threshold_seconds: float = 8.0) -> None:
        return

    def exception(self, component: str, exc: BaseException) -> None:
        return

    def finalize(self, exit_code: int | None = None) -> None:
        return


_reporter: CrashReporter | DisabledCrashReporter | None = None


def install_crash_reporter(
    directory: str | Path | None = None, *, enabled: bool = True
) -> CrashReporter | DisabledCrashReporter:
    global _reporter
    if _reporter is None:
        _reporter = CrashReporter(directory) if enabled else DisabledCrashReporter()
        _reporter.install()
    return _reporter


def crash_reporter() -> CrashReporter | DisabledCrashReporter | None:
    return _reporter


def log_diagnostic(component: str, message: str) -> None:
    reporter = _reporter
    if reporter is not None:
        reporter.log(component, message)
