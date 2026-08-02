"""Desktop application entry point."""

from __future__ import annotations

import ctypes
import signal
import sys
from ctypes import wintypes

from PyQt6.QtCore import QSettings, QTimer, Qt
from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import QApplication

from youtube_audio_video_downloader.gui.main_window import MainWindow
from youtube_audio_video_downloader.gui.crash_reporter import install_crash_reporter
from youtube_audio_video_downloader.gui.resources import application_icon_path
from youtube_audio_video_downloader.config.app_storage import (
    migrate_legacy_data,
    resolve_data_directory,
    settings_file,
)
from youtube_audio_video_downloader.config.app_identity import (
    APP_DISPLAY_NAME,
    DESKTOP_FILE_ID,
    ORGANIZATION_NAME,
    WINDOWS_APP_USER_MODEL_ID,
)
from youtube_audio_video_downloader.version import application_version


def _enable_windows_backdrop(window: MainWindow) -> None:
    """Request the modern Windows backdrop when available; fail silently elsewhere."""

    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        dwmapi = ctypes.windll.dwmapi
        # DWMWA_SYSTEMBACKDROP_TYPE = 38, DWMSBT_TRANSIENTWINDOW = 3.
        backdrop = ctypes.c_int(3)
        dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(38),
            ctypes.byref(backdrop),
            ctypes.sizeof(backdrop),
        )
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, rounded corners = 2.
        corners = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(33),
            ctypes.byref(corners),
            ctypes.sizeof(corners),
        )
    except Exception:
        return


def _set_windows_app_identity() -> None:
    """Give Windows a stable taskbar identity for grouping and icon selection."""

    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_USER_MODEL_ID
        )
    except Exception:
        return


def main() -> int:
    """Launch the PyQt6 desktop application."""

    data_directory = resolve_data_directory()
    settings = QSettings(
        str(settings_file(data_directory)),
        QSettings.Format.IniFormat,
    )
    migrate_legacy_data(data_directory, settings)
    crash_reports_enabled = settings.value(
        "privacy/crash_reports_enabled", False, type=bool
    )
    reporter = install_crash_reporter(
        data_directory / "crash_reports", enabled=crash_reports_enabled
    )
    reporter.log("APP", "Initializing QApplication")
    _set_windows_app_identity()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationVersion(application_version())
    app.setOrganizationName(ORGANIZATION_NAME)
    if sys.platform.startswith("linux"):
        app.setDesktopFileName(DESKTOP_FILE_ID)
    application_icon = QIcon(str(application_icon_path()))
    if not application_icon.isNull():
        app.setWindowIcon(application_icon)
    app.setStyle("Fusion")
    reporter.start_hang_watchdog()

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(8, 12, 26))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(240, 244, 255))
    palette.setColor(QPalette.ColorRole.Base, QColor(10, 16, 32))
    palette.setColor(QPalette.ColorRole.Text, QColor(240, 244, 255))
    palette.setColor(QPalette.ColorRole.Button, QColor(20, 27, 49))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(240, 244, 255))
    app.setPalette(palette)

    window = MainWindow(settings=settings, data_directory=data_directory)
    if not application_icon.isNull():
        window.setWindowIcon(application_icon)
    window.show()
    _enable_windows_backdrop(window)
    reporter.log("APP", f"Main window shown; winId={int(window.winId())}")
    app.aboutToQuit.connect(lambda: reporter.log("APP", "QApplication aboutToQuit"))

    interrupt_requested = False

    def request_interrupt_shutdown(*_args) -> None:
        nonlocal interrupt_requested
        if interrupt_requested:
            return
        interrupt_requested = True
        # Python delivers SIGINT on the GUI thread, so initiating the normal
        # close path directly is safe and avoids losing a queued callback when
        # the interrupt arrived from inside another Qt-to-Python callback.
        window.request_graceful_shutdown()

    previous_excepthook = sys.excepthook

    def gui_excepthook(exception_type, exception, traceback) -> None:
        if issubclass(exception_type, KeyboardInterrupt):
            request_interrupt_shutdown()
            return
        previous_excepthook(exception_type, exception, traceback)

    sys.excepthook = gui_excepthook
    console_signals = [signal.SIGINT]
    if hasattr(signal, "SIGBREAK"):
        console_signals.append(signal.SIGBREAK)
    previous_signal_handlers = {
        console_signal: signal.getsignal(console_signal)
        for console_signal in console_signals
    }
    for console_signal in console_signals:
        signal.signal(console_signal, request_interrupt_shutdown)

    # Let Python regularly service console signals even while Qt owns the main loop.
    signal_timer = QTimer(app)
    signal_timer.setInterval(200)
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start()
    exit_code: int | None = None
    try:
        exit_code = app.exec()
        return exit_code
    except KeyboardInterrupt:
        request_interrupt_shutdown()
        exit_code = app.exec() if window.isVisible() else 0
        return exit_code
    finally:
        signal_timer.stop()
        for console_signal, previous_handler in previous_signal_handlers.items():
            signal.signal(console_signal, previous_handler)
        sys.excepthook = previous_excepthook
        reporter.finalize(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
