"""Capture the annotated desktop screenshots used by the user guide.

Run from the repository root with::

    uv run python tools/capture_user_guide.py

The script uses an isolated settings/data directory, disables phone access, and
never reads the operator's library paths or saved provider credentials.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

os.environ.setdefault("QT_SCALE_FACTOR", "1")
os.environ["YMS_DISABLE_REMOTE_ACCESS"] = "1"

from PyQt6.QtCore import QPoint, QRect, QSettings, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QFrame,
    QScrollArea,
    QTabWidget,
    QWidget,
)

from youtube_audio_video_downloader.gui.application.main_window import MainWindow
from youtube_audio_video_downloader.gui.components.widgets import TimestampImportDialog
from youtube_audio_video_downloader.gui.media.media_player import ArtistRepairDialog
from youtube_audio_video_downloader.services.media.artist_canonicalizer import (
    ArtistRenameSuggestion,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "media" / "user-guide"
WORKSPACES = OUTPUT / "workspaces"
LIBRARY = OUTPUT / "media-library"
DIALOGS = OUTPUT / "dialogs"
CALLOUT = QColor("#39d9ff")


def _settle(app: QApplication, milliseconds: int = 240) -> None:
    deadline = time.monotonic() + milliseconds / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _visible_rect(target: QWidget, root: QWidget) -> QRect | None:
    if not target.isVisibleTo(root):
        return None
    top_left = target.mapTo(root, QPoint(0, 0))
    rect = QRect(top_left, target.size()).intersected(root.rect())
    ancestor = target.parentWidget()
    while ancestor is not None and ancestor is not root:
        ancestor_top_left = ancestor.mapTo(root, QPoint(0, 0))
        rect = rect.intersected(QRect(ancestor_top_left, ancestor.size()))
        ancestor = ancestor.parentWidget()
    if rect.width() < 32 or rect.height() < 24:
        return None
    return rect.adjusted(2, 2, -3, -3)


def _card_regions(page: QWidget, root: QWidget) -> list[QRect]:
    cards: list[QRect] = []
    for frame in page.findChildren(QFrame):
        if frame.objectName() not in {"glassCard", "heroCard", "libraryRecommendationCard"}:
            continue
        rect = _visible_rect(frame, root)
        if rect is not None:
            cards.append(rect)
    return sorted(cards, key=lambda rect: (rect.top(), rect.left(), -rect.width()))


def _widget_regions(widgets: Iterable[QWidget], root: QWidget) -> list[QRect]:
    rects = [rect for widget in widgets if (rect := _visible_rect(widget, root))]
    return sorted(rects, key=lambda rect: (rect.top(), rect.left()))


def _save_annotated(root: QWidget, path: Path, regions: Iterable[QRect]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = root.grab()
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(CALLOUT, 3))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    region_list = list(regions)
    for rect in region_list:
        painter.drawRoundedRect(rect, 9, 9)

    font = QFont("Segoe UI", 9, QFont.Weight.Bold)
    painter.setFont(font)
    for number, rect in enumerate(region_list, 1):
        # Sit immediately outside the left border so the badge never covers a
        # heading or control label inside the highlighted region.
        if rect.left() >= 30:
            badge = QRect(rect.left() - 22, rect.top() + 5, 20, 20)
        elif rect.width() < 200 and rect.right() + 23 < root.width():
            badge = QRect(rect.right() + 3, rect.top() + 5, 20, 20)
        else:
            badge = QRect(rect.left() + 5, max(1, rect.top() - 10), 20, 20)
        painter.setPen(QPen(QColor("#06101e"), 1))
        painter.setBrush(CALLOUT)
        painter.drawEllipse(badge)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(number))
    painter.end()
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not save screenshot: {path}")
    print(f"{path.relative_to(ROOT)}: {len(region_list)} callouts")


def _show_page(window: MainWindow, app: QApplication, index: int) -> QScrollArea | QWidget:
    window._set_page(index)
    button = window._nav_buttons[index]
    parent = button.parentWidget()
    while parent is not None and not isinstance(parent, QScrollArea):
        parent = parent.parentWidget()
    if isinstance(parent, QScrollArea):
        parent.ensureWidgetVisible(button, 8, 8)
    page = window.pages.currentWidget()
    if isinstance(page, QScrollArea):
        page.verticalScrollBar().setValue(0)
    _settle(app)
    return page


def _capture_workspace(
    window: MainWindow,
    app: QApplication,
    index: int,
    filename: str,
) -> None:
    page = _show_page(window, app, index)
    _save_annotated(window, WORKSPACES / filename, _card_regions(page, window))


def _capture_settings(window: MainWindow, app: QApplication) -> None:
    page = _show_page(window, app, 12)
    captures = (
        ("global-settings-processing.png", ("batch_network",)),
        ("global-settings-audio-playback.png", ("audio_metadata", "video_playback")),
        ("global-settings-ai.png", ("ai_providers",)),
        (
            "global-settings-behavior-storage.png",
            ("behavior_privacy", "storage_appearance"),
        ),
    )
    for filename, expanded in captures:
        for key, section in window.settings_sections.items():
            section.set_expanded(key in expanded)
        if isinstance(page, QScrollArea):
            page.verticalScrollBar().setValue(0)
        _settle(app)
        _save_annotated(window, WORKSPACES / filename, _card_regions(page, window))


def _capture_utilities(window: MainWindow, app: QApplication) -> None:
    page = _show_page(window, app, 10)
    tabs = page.findChild(QTabWidget)
    if tabs is None:
        raise RuntimeError("Utilities tabs were not found")
    for index, filename in enumerate(("utilities-artist.png", "utilities-timestamps.png")):
        tabs.setCurrentIndex(index)
        _settle(app)
        _save_annotated(window, WORKSPACES / filename, _card_regions(page, window))


def _library_regions(window: MainWindow) -> list[QRect]:
    library = window.media_library
    widgets = [
        library.folder_controls,
        library.search_controls,
        library.recommendation_toggle,
    ]
    recommendation = library.findChild(QWidget, "libraryRecommendationCard")
    if recommendation is not None and recommendation.isVisible():
        widgets.append(recommendation)
    widgets.extend(
        [library.artist_track_splitter, library.album_stack, library.player_card]
    )
    if library.playlist_drawer.isVisible():
        widgets.append(library.playlist_drawer)
    if library.queue_drawer.isVisible():
        widgets.append(library.queue_drawer)
    return _widget_regions(widgets, window)


def _capture_library(window: MainWindow, app: QApplication) -> None:
    _show_page(window, app, 13)
    library = window.media_library
    _save_annotated(window, LIBRARY / "overview.png", _library_regions(window))

    library.recommendation_toggle.setChecked(True)
    _settle(app)
    _save_annotated(window, LIBRARY / "smart-curator.png", _library_regions(window))
    library.recommendation_toggle.setChecked(False)

    library.playlist_toggle_button.click()
    _settle(app)
    _save_annotated(window, LIBRARY / "playlists.png", _library_regions(window))
    library.playlist_toggle_button.click()

    library.queue_toggle_button.click()
    _settle(app)
    _save_annotated(window, LIBRARY / "now-playing-queue.png", _library_regions(window))
    library.queue_toggle_button.click()
    _settle(app)


def _capture_dialogs(window: MainWindow, app: QApplication) -> None:
    timestamp = TimestampImportDialog(window)
    timestamp.show()
    _settle(app)
    timestamp_regions = _widget_regions(
        [timestamp.input_text, timestamp.preview_output, timestamp.findChild(QDialogButtonBox)],
        timestamp,
    )
    _save_annotated(timestamp, DIALOGS / "timestamp-import.png", timestamp_regions)
    timestamp.close()

    suggestions = [
        ArtistRenameSuggestion("K.K.", "KK", 18),
        ArtistRenameSuggestion("A. R. Rahman", "AR Rahman", 12),
        ArtistRenameSuggestion("Arijit", "Arijit Singh", 7),
        ArtistRenameSuggestion(
            "Vishal, Shekhar", "Vishal Dadlani, Shekhar Ravjiani", 5
        ),
    ]
    repair = ArtistRepairDialog(
        suggestions,
        window,
        artist_values=("K.K.", "A. R. Rahman", "Arijit", "Vishal, Shekhar"),
    )
    repair.show()
    _settle(app)
    repair_regions = _widget_regions(
        [
            repair.table,
            repair.add_replacement_button,
            repair.findChild(QDialogButtonBox),
        ],
        repair,
    )
    _save_annotated(repair, DIALOGS / "artist-name-review.png", repair_regions)
    repair.close()
    _settle(app)


def main() -> int:
    for directory in (WORKSPACES, LIBRARY, DIALOGS):
        directory.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(8, 12, 26))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(240, 244, 255))
    palette.setColor(QPalette.ColorRole.Base, QColor(10, 16, 32))
    palette.setColor(QPalette.ColorRole.Text, QColor(240, 244, 255))
    palette.setColor(QPalette.ColorRole.Button, QColor(20, 27, 49))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(240, 244, 255))
    app.setPalette(palette)

    with tempfile.TemporaryDirectory(prefix="yms-user-guide-") as temporary:
        temporary_path = Path(temporary)
        settings = QSettings(
            str(temporary_path / "settings.ini"), QSettings.Format.IniFormat
        )
        settings.setValue("remote/access_enabled", False)
        settings.setValue("workspace/persist_enabled", False)
        settings.setValue("defaults/ai_enabled", True)
        settings.setValue("defaults/ai_provider", "ollama")
        settings.setValue("defaults/agentic_model", "qwen2.5:7b")
        settings.sync()

        window = MainWindow(settings=settings, data_directory=temporary_path)
        # Keep the screenshot explanatory without exposing the operator's Windows
        # profile path. The real isolated directory remains in use for the process.
        window.settings_data_directory.set_text(r"C:\Media\YouTubeMediaStudioData")
        window.resize(1440, 900)
        window.move(36, 36)
        window.show()
        _settle(app, 500)

        for index, filename in (
            (0, "dashboard.png"),
            (1, "search-song.png"),
            (2, "audio-downloader.png"),
            (3, "video-downloader.png"),
            (4, "album-splitter.png"),
            (5, "jukebox-splitter.png"),
            (6, "track-reorder.png"),
            (7, "edit-file.png"),
            (8, "edit-album.png"),
            (9, "album-consolidator.png"),
            (11, "live-logs.png"),
        ):
            _capture_workspace(window, app, index, filename)
        _capture_utilities(window, app)
        _capture_settings(window, app)
        _capture_library(window, app)
        _capture_dialogs(window, app)

        window.close()
        _settle(app)

    print(f"Captured user-guide screenshots under {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
