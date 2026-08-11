"""Persistent local media library and responsive audio/video player."""

from __future__ import annotations

import random
import re
import sys
import time
from pathlib import Path
from ctypes import wintypes
from typing import Callable

from PyQt6.QtCore import (
    QEasingCurve,
    QAbstractNativeEventFilter,
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    QSettings,
    QStringListModel,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtProperty,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
    QWheelEvent,
)
from PyQt6.QtMultimedia import QAudioBuffer, QAudioBufferOutput, QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCompleter,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from youtube_audio_video_downloader.gui.widgets import GlassCard
from youtube_audio_video_downloader.gui.audio_visualizer import SpectrumAnalyzer
from youtube_audio_video_downloader.gui.crash_reporter import log_diagnostic
from youtube_audio_video_downloader.services.media_library import (
    LibraryItem,
    artwork_bytes,
    filter_library,
    scan_library,
    split_artists,
)
from youtube_audio_video_downloader.services.media_playlists import (
    add_playlist_paths,
    decode_playlists,
    encode_playlists,
)
from youtube_audio_video_downloader.services.library_recommendations import (
    LibraryRecommendation,
    playlist_taste_search_query,
    recommend_library_tracks,
)
from youtube_audio_video_downloader.services.ai_provider import configured_primary_identity


class LibraryScanner(QObject):
    finished = pyqtSignal(object)

    def __init__(self, folders: list[str]) -> None:
        super().__init__()
        self.folders = folders

    @pyqtSlot()
    def run(self) -> None:
        thread = QThread.currentThread()
        self.finished.emit(
            scan_library(self.folders, cancelled=thread.isInterruptionRequested)
        )


class LibrarySearchWorker(QObject):
    finished = pyqtSignal(int, object, object, object)

    def __init__(
        self,
        request_id: int,
        items: list[LibraryItem],
        query: str,
        artists: list[str],
        year_from: int | None,
        year_to: int | None,
        media_type: str,
        suggestion_limit: int,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.items = items
        self.query = query
        self.artists = artists
        self.year_from = year_from
        self.year_to = year_to
        self.media_type = media_type
        self.suggestion_limit = suggestion_limit

    @pyqtSlot()
    def run(self) -> None:
        base_matches = filter_library(
            self.items,
            query=self.query,
            year_from=self.year_from,
            year_to=self.year_to,
            media_type=self.media_type,
        )
        available_artists = sorted(
            {
                artist
                for item in base_matches
                for artist in split_artists(item.artists)
            },
            key=str.casefold,
        )
        available_keys = {artist.casefold() for artist in available_artists}
        valid_artists = [
            artist for artist in self.artists
            if artist.casefold() in available_keys
        ]
        matches = (
            filter_library(base_matches, artists=valid_artists)
            if valid_artists
            else base_matches
        )
        ranked = sorted(
            matches,
            key=lambda item: _match_rank(item, self.query),
        )[: self.suggestion_limit]
        suggestions = [
            (item.path, _suggestion_text(item))
            for item in ranked
        ]
        self.finished.emit(
            self.request_id, matches, suggestions, available_artists
        )


class LibraryRecommendationWorker(QObject):
    """Run the local AI recommendation request away from the UI thread."""

    finished = pyqtSignal(object, str)

    def __init__(
        self,
        request_text: str,
        items: list[LibraryItem],
        model: str,
        limit: int,
        *,
        language_continuation: bool = False,
        playlists: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__()
        self.request_text = request_text
        self.items = items
        self.model = model
        self.limit = limit
        self.language_continuation = language_continuation
        self.playlists = playlists or {}

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = recommend_library_tracks(
                self.request_text,
                self.items,
                model=self.model,
                limit=self.limit,
                language_continuation=self.language_continuation,
                playlists=self.playlists,
            )
        except Exception as exc:  # worker boundary must report failures to the UI
            self.finished.emit([], str(exc))
            return
        self.finished.emit(result, "")


class AnimatedSeekSlider(QSlider):
    """A fixed-layout slider with non-blocking animated click-to-seek."""

    seekRequested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._track_thickness = 4.0
        self._pressed = False
        self._dragging = False
        self._press_x = 0.0
        self.setMouseTracking(True)
        self.setMinimumHeight(26)
        self.animation = QPropertyAnimation(self, b"trackThickness", self)
        self.animation.setDuration(150)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.seek_animation = QPropertyAnimation(self, b"value", self)
        self.seek_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def get_track_thickness(self) -> float:
        return self._track_thickness

    def set_track_thickness(self, value: float) -> None:
        self._track_thickness = float(value)
        self.update()

    trackThickness = pyqtProperty(
        float, get_track_thickness, set_track_thickness
    )

    def enterEvent(self, event) -> None:  # noqa: N802
        self._animate_to(7.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_to(4.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self._dragging = False
            self._press_x = event.position().x()
            self.sliderPressed.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._pressed:
            if abs(event.position().x() - self._press_x) >= 4.0:
                if not self._dragging:
                    self.seek_animation.stop()
                    self._dragging = True
                self._set_from_x(event.position().x(), seek=False)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._pressed and event.button() == Qt.MouseButton.LeftButton:
            target = self._value_from_x(event.position().x())
            if self._dragging:
                self.setValue(target)
            else:
                self._animate_seek_to(target)
            self.sliderMoved.emit(target)
            self.seekRequested.emit(target)
            self._pressed = False
            self._dragging = False
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        margin = 8.0
        usable = max(1.0, self.width() - margin * 2)
        ratio = (
            (self.value() - self.minimum()) / (self.maximum() - self.minimum())
            if self.maximum() > self.minimum()
            else 0.0
        )
        center_y = self.height() / 2
        thickness = self._track_thickness
        track = QRectF(margin, center_y - thickness / 2, usable, thickness)
        played = QRectF(margin, center_y - thickness / 2, usable * ratio, thickness)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 34))
        painter.drawRoundedRect(track, thickness / 2, thickness / 2)
        painter.setBrush(QColor(112, 140, 255, 225))
        painter.drawRoundedRect(played, thickness / 2, thickness / 2)
        radius = 6.0 if thickness < 6 else 7.0
        handle_x = margin + usable * ratio
        painter.setBrush(QColor(230, 235, 255))
        painter.setPen(QColor(112, 140, 255, 240))
        painter.drawEllipse(QRectF(handle_x - radius, center_y - radius, radius * 2, radius * 2))

    def _animate_to(self, thickness: float) -> None:
        self.animation.stop()
        self.animation.setStartValue(self._track_thickness)
        self.animation.setEndValue(thickness)
        self.animation.start()

    def _set_from_x(self, x_position: float, *, seek: bool) -> None:
        value = self._value_from_x(x_position)
        self.setValue(value)
        self.sliderMoved.emit(value)
        if seek:
            self.seekRequested.emit(value)

    def _value_from_x(self, x_position: float) -> int:
        margin = 8.0
        usable = max(1.0, self.width() - margin * 2)
        ratio = max(0.0, min(1.0, (x_position - margin) / usable))
        return round(self.minimum() + ratio * (self.maximum() - self.minimum()))

    def _animate_seek_to(self, target: int) -> None:
        self.seek_animation.stop()
        distance = abs(target - self.value())
        span = max(1, self.maximum() - self.minimum())
        self.seek_animation.setDuration(160 + round(140 * distance / span))
        self.seek_animation.setStartValue(self.value())
        self.seek_animation.setEndValue(target)
        self.seek_animation.start()

    def is_seek_animating(self) -> bool:
        return self.seek_animation.state() == QPropertyAnimation.State.Running


class WindowsMediaKeyFilter(QAbstractNativeEventFilter):
    """Consume WM_APPCOMMAND media keys before Windows forwards them elsewhere."""

    WM_APPCOMMAND = 0x0319
    COMMANDS = {11: "next", 12: "previous", 13: "stop", 14: "toggle"}

    def __init__(self, callback) -> None:
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if sys.platform != "win32":
            return False, 0
        try:
            native_message = wintypes.MSG.from_address(int(message))
            if native_message.message != self.WM_APPCOMMAND:
                return False, 0
            command_id = (int(native_message.lParam) >> 16) & 0x0FFF
            command = self.COMMANDS.get(command_id)
            if command is None:
                return False, 0
            log_diagnostic(
                "MEDIA-KEY",
                f"Consumed WM_APPCOMMAND command={command} id={command_id}",
            )
            self.callback(command, "windows-native")
            return True, 0
        except (AttributeError, TypeError, ValueError, OSError) as exc:
            log_diagnostic("MEDIA-KEY", f"Native filter error: {exc}")
            return False, 0


class SortableTableItem(QTableWidgetItem):
    """Display friendly text while sorting on a separate typed value."""

    SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(self.SORT_ROLE)
        right = other.data(self.SORT_ROLE)
        if left is not None and right is not None:
            return left < right
        return self.text().casefold() < other.text().casefold()


class AnimatedQueueList(QListWidget):
    """Single-row internal-move list with a short visual glide after dropping."""

    orderChanged = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)
        self._drop_animation: QPropertyAnimation | None = None
        self._drop_overlay: QLabel | None = None
        self._drop_item: QListWidgetItem | None = None
        self._drop_foreground = None

    def dropEvent(self, event) -> None:  # noqa: N802
        moving = self.currentItem()
        old_row = self.row(moving) if moving is not None else -1
        old_rect = self.visualItemRect(moving) if moving is not None else QRect()
        snapshot = self.viewport().grab(old_rect) if old_rect.isValid() else QPixmap()
        super().dropEvent(event)
        if moving is None:
            return
        new_row = self.row(moving)
        new_rect = self.visualItemRect(moving)
        self.orderChanged.emit()
        if old_row != new_row and old_rect.isValid() and new_rect.isValid() and not snapshot.isNull():
            self._animate_drop(moving, snapshot, old_rect, new_rect)

    def _animate_drop(
        self,
        moving: QListWidgetItem,
        snapshot: QPixmap,
        start: QRect,
        finish: QRect,
    ) -> None:
        if self._drop_animation is not None:
            self._drop_animation.stop()
        if self._drop_item is not None and self._drop_foreground is not None:
            self._drop_item.setForeground(self._drop_foreground)
        if self._drop_overlay is not None:
            self._drop_overlay.deleteLater()
        foreground = moving.foreground()
        moving.setForeground(QColor(0, 0, 0, 0))
        overlay = QLabel(self.viewport())
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        overlay.setPixmap(snapshot)
        overlay.setScaledContents(True)
        overlay.setGeometry(start)
        overlay.show()
        overlay.raise_()
        animation = QPropertyAnimation(overlay, b"geometry", self)
        animation.setDuration(190)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.setStartValue(start)
        animation.setEndValue(finish)
        self._drop_item = moving
        self._drop_foreground = foreground
        self._drop_overlay = overlay
        self._drop_animation = animation

        def finish_animation() -> None:
            moving.setForeground(foreground)
            overlay.deleteLater()
            if self._drop_item is moving:
                self._drop_item = None
                self._drop_foreground = None
                self._drop_overlay = None
                self._drop_animation = None

        animation.finished.connect(finish_animation)
        animation.start()


class AlbumGridListWidget(QListWidget):
    """Album grid whose wheel advances exactly one visual row per notch."""

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        angle_delta = event.angleDelta().y()
        if not angle_delta:
            super().wheelEvent(event)
            return

        notch_count = max(1, abs(angle_delta) // 120)
        direction = -1 if angle_delta > 0 else 1
        scrollbar = self.verticalScrollBar()
        row_height = max(1, self.gridSize().height())
        scrollbar.setValue(
            scrollbar.value() + direction * notch_count * row_height
        )
        event.accept()


def _transport_icon(name: str) -> QIcon:
    """Create crisp monochrome controls without platform-dependent emoji glyphs."""

    pixmap = QPixmap(28, 28)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("#f7f9ff")
    painter.setPen(QPen(color, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.setBrush(color)
    if name == "play":
        painter.drawPolygon(QPolygonF([QPointF(9, 6), QPointF(22, 14), QPointF(9, 22)]))
    elif name == "pause":
        painter.drawRoundedRect(8, 6, 4, 16, 1, 1)
        painter.drawRoundedRect(16, 6, 4, 16, 1, 1)
    elif name == "stop":
        painter.drawRoundedRect(7, 7, 14, 14, 2, 2)
    elif name == "previous":
        painter.drawLine(7, 7, 7, 21)
        painter.drawPolygon(QPolygonF([QPointF(20, 6), QPointF(9, 14), QPointF(20, 22)]))
    elif name == "next":
        painter.drawLine(21, 7, 21, 21)
        painter.drawPolygon(QPolygonF([QPointF(8, 6), QPointF(19, 14), QPointF(8, 22)]))
    painter.end()
    return QIcon(pixmap)


class MediaLibraryPage(QWidget):
    """Library browser whose player remains alive when another page is selected."""

    request_search_song = pyqtSignal(str)
    request_edit_file = pyqtSignal(str)
    request_edit_album = pyqtSignal(str)
    request_album_enricher = pyqtSignal(str)
    request_track_reorder = pyqtSignal(str)
    spectrum_ready = pyqtSignal(object)
    spectrum_buffer_ready = pyqtSignal(object)
    visualizer_playback_changed = pyqtSignal(bool)

    def __init__(self, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.ai_identity_resolver: Callable[[], tuple[str, str]] | None = None
        self.items: list[LibraryItem] = []
        self.filtered: list[LibraryItem] = []
        self.queue: list[LibraryItem] = []
        self._queue_source: list[LibraryItem] = []
        self.queue_index = -1
        self.playlists = decode_playlists(
            self.settings.value("library/playlists", "")
        )
        self._active_playlist = str(
            self.settings.value("library/active_playlist", "") or ""
        )
        repeat_mode = str(self.settings.value("library/repeat_mode", "off"))
        self._repeat_mode = (
            repeat_mode if repeat_mode in {"off", "all", "one"} else "off"
        )
        self._shuffle_enabled = str(
            self.settings.value("library/shuffle", "false")
        ).casefold() in {"1", "true", "yes"}
        self._open_album_items: list[LibraryItem] = []
        self._open_album_name = ""
        self._open_album_artists: list[str] = []
        self._artwork_cache: dict[str, QIcon] = {}
        self._album_art_generation = 0
        self._pending_album_art: list[tuple[QListWidgetItem, str]] = []
        self._scanner_thread: QThread | None = None
        self._scanner_worker: LibraryScanner | None = None
        self._scan_refresh_pending = False
        self._shutting_down = False
        self._scan_started_at = 0.0
        self._search_thread: QThread | None = None
        self._search_worker: LibrarySearchWorker | None = None
        self._recommendation_thread: QThread | None = None
        self._recommendation_worker: LibraryRecommendationWorker | None = None
        self._last_recommendation_request = ""
        self._last_recommendations: list[LibraryRecommendation] = []
        self._search_request_id = 0
        self._applied_query = ""
        self._applied_media_type = "all"
        self._search_pending = False
        self._suggestion_limit = max(
            1, int(self.settings.value("defaults/search_suggestions", 10))
        )
        self._suggestions_by_text: dict[str, LibraryItem] = {}
        self._seeking = False
        self._player_fullscreen = False
        self._fullscreen_hidden_widgets: list[tuple[QWidget, bool]] = []
        self._fullscreen_window: QWidget | None = None
        self._fullscreen_window_state = Qt.WindowState.WindowNoState
        self._consecutive_playback_errors = 0
        self._last_media_command_at = 0.0
        self._native_media_filter: WindowsMediaKeyFilter | None = None
        self._spectrum_thread: QThread | None = None
        self._spectrum_worker: SpectrumAnalyzer | None = None
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(180)
        self._search_debounce.timeout.connect(self._start_background_search)
        self._build_ui()
        self._connect_player()
        self._install_media_shortcuts()
        self._load_folders()
        self.refresh_library()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(15000)
        self.refresh_timer.timeout.connect(self.refresh_library)
        self.refresh_timer.start()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 8, 12)
        layout.setSpacing(10)
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("Media Library")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.library_refresh_button = QPushButton("Refresh")
        self.library_refresh_button.setObjectName("secondaryButton")
        self.library_refresh_button.setToolTip("Rescan every configured library folder")
        self.library_refresh_button.clicked.connect(self.refresh_library)
        header.addWidget(self.library_refresh_button)
        self.playlist_toggle_button = QPushButton("Playlists (0) ‹")
        self.playlist_toggle_button.setObjectName("secondaryButton")
        self.playlist_toggle_button.setCheckable(True)
        self.playlist_toggle_button.setToolTip("Create and browse saved local playlists")
        self.playlist_toggle_button.toggled.connect(self._toggle_playlist_drawer)
        header.addWidget(self.playlist_toggle_button)
        self.queue_toggle_button = QPushButton("Queue (0) ›")
        self.queue_toggle_button.setObjectName("secondaryButton")
        self.queue_toggle_button.setCheckable(True)
        self.queue_toggle_button.setToolTip("Show or hide the current playback queue")
        self.queue_toggle_button.toggled.connect(self._toggle_queue_drawer)
        header.addWidget(self.queue_toggle_button)
        main_layout.addLayout(header)
        subtitle = QLabel(
            "Browse your library, build precise queues, and keep playback running "
            "while using every other workspace."
        )
        subtitle.setObjectName("mutedLabel")
        main_layout.addWidget(subtitle)
        main_layout.addWidget(self._build_folder_card())
        main_layout.addLayout(self._build_search_row())
        main_layout.addWidget(self._build_recommendation_card())
        self.library_splitter = QSplitter(Qt.Orientation.Vertical)
        self.library_splitter.addWidget(self._build_browser())
        self.library_splitter.addWidget(self._build_album_section())
        self.library_splitter.setStretchFactor(0, 2)
        self.library_splitter.setStretchFactor(1, 3)
        self.library_splitter.setSizes([320, 430])
        main_layout.addWidget(self.library_splitter, 1)
        main_layout.addWidget(self._build_player())
        self.playlist_drawer = self._build_playlist_drawer()
        self.playlist_drawer.setVisible(False)
        layout.addWidget(self.playlist_drawer)
        layout.addWidget(main, 1)
        self.queue_drawer = self._build_queue_drawer()
        self.queue_drawer.setVisible(False)
        layout.addWidget(self.queue_drawer)
        self._sync_queue_drawer()
        self._render_playlists()
        self._sync_media_type_layout()

    def _build_folder_card(self) -> QWidget:
        card = GlassCard()
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 9, 12, 9)
        self.folder_list = QListWidget(card)
        self.folder_list.setVisible(False)
        self.folder_chip_host = QWidget()
        self.folder_chip_host.setStyleSheet("background: transparent;")
        self.folder_chip_layout = QHBoxLayout(self.folder_chip_host)
        self.folder_chip_layout.setContentsMargins(0, 0, 0, 0)
        self.folder_chip_layout.setSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { "
            "background: transparent; border: none; }"
        )
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(46)
        scroll.setWidget(self.folder_chip_host)
        row.addWidget(scroll, 1)
        add_button = QPushButton("+")
        add_button.setObjectName("primaryButton")
        add_button.setToolTip("Add a media library folder")
        add_button.setAccessibleName("Add library folder")
        add_button.clicked.connect(self.add_folder)
        row.addWidget(add_button)
        return card

    def _render_folder_chips(self) -> None:
        while self.folder_chip_layout.count():
            item = self.folder_chip_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        folders = self.folders()
        if not folders:
            empty = QLabel("No folders added")
            empty.setObjectName("mutedLabel")
            self.folder_chip_layout.addWidget(empty)
        for path in folders:
            chip = GlassCard()
            chip_row = QHBoxLayout(chip)
            chip_row.setContentsMargins(8, 3, 3, 3)
            chip_row.setSpacing(5)
            label = QLabel(Path(path).name or path)
            label.setToolTip(path)
            chip_row.addWidget(label)
            remove = QPushButton("×")
            remove.setObjectName("secondaryButton")
            remove.setToolTip(f"Remove {path} from the library")
            remove.clicked.connect(
                lambda _checked=False, value=path: self._remove_folder_path(value)
            )
            chip_row.addWidget(remove)
            self.folder_chip_layout.addWidget(chip)
        self.folder_chip_layout.addStretch(1)

    def _build_search_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.search.setPlaceholderText("Search title, album, artist, year, or filename…")
        self.search.textChanged.connect(self._schedule_search)
        self.search.returnPressed.connect(self.search_or_download)
        self.suggestion_model = QStringListModel(self)
        self.search_completer = QCompleter(self.suggestion_model, self)
        self.search_completer.setCompletionMode(
            QCompleter.CompletionMode.UnfilteredPopupCompletion
        )
        self.search_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search_completer.setMaxVisibleItems(self._suggestion_limit)
        self.search_completer.activated[str].connect(self._suggestion_activated)
        self.search.setCompleter(self.search_completer)
        row.addWidget(self.search, 1)
        self.clear_search_button = QPushButton("Clear")
        self.clear_search_button.setObjectName("secondaryButton")
        self.clear_search_button.setToolTip("Clear the library search")
        self.clear_search_button.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.clear_search_button.clicked.connect(self.search.clear)
        row.addWidget(self.clear_search_button)
        self.media_type_filter = QComboBox()
        self.media_type_filter.addItem("All media", "all")
        self.media_type_filter.addItem("Music", "audio")
        self.media_type_filter.addItem("Videos", "video")
        saved_media_type = str(
            self.settings.value("library/media_type_filter", "all") or "all"
        )
        saved_index = self.media_type_filter.findData(saved_media_type)
        self.media_type_filter.setCurrentIndex(max(0, saved_index))
        self.media_type_filter.setToolTip(
            "Keep music and video browsing separate, or show the complete library"
        )
        self.media_type_filter.currentIndexChanged.connect(
            self._media_type_changed
        )
        row.addWidget(self.media_type_filter)
        self.year_from = QSpinBox()
        self.year_to = QSpinBox()
        for spin, label in ((self.year_from, "From year"), (self.year_to, "To year")):
            spin.setRange(0, 2100)
            spin.setSpecialValueText(label)
            spin.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )
            spin.setFixedWidth(
                max(112, spin.fontMetrics().horizontalAdvance(label) + 52)
            )
            spin.valueChanged.connect(self._schedule_search)
            row.addWidget(spin)
        self.online_search_button = QPushButton("Search online if missing")
        self.online_search_button.setObjectName("secondaryButton")
        self.online_search_button.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.online_search_button.clicked.connect(self.search_or_download)
        row.addWidget(self.online_search_button)
        return row

    def _build_recommendation_card(self) -> QWidget:
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(6)
        self.recommendation_toggle = QPushButton("Smart Library Curator  ›")
        self.recommendation_toggle.setObjectName("secondaryButton")
        self.recommendation_toggle.setCheckable(True)
        self.recommendation_toggle.setToolTip(
            "Open natural-language local-library curation"
        )
        container_layout.addWidget(self.recommendation_toggle)
        card = GlassCard()
        card.setObjectName("libraryRecommendationCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        row = QHBoxLayout()
        heading = QLabel("Smart Library Curator")
        heading.setObjectName("sectionTitle")
        heading.setToolTip(
            "Uses the Global Settings agentic model and only indexed library metadata."
        )
        row.addWidget(heading)
        self.recommendation_ai_enabled = QCheckBox("Use AI for this section")
        section_value = self.settings.value("ai/tools/media_library")
        self.recommendation_ai_enabled.setChecked(
            self.settings.value("defaults/ai_enabled", True, type=bool)
            if section_value is None
            else str(section_value).casefold() in {"1", "true", "yes"}
        )
        self.recommendation_ai_enabled.setToolTip(
            "Off sends this request directly to plain YouTube internet search."
        )
        self.recommendation_ai_enabled.toggled.connect(
            self._save_recommendation_ai_setting
        )
        row.addWidget(self.recommendation_ai_enabled)
        self.recommendation_request = QLineEdit()
        self.recommendation_request.setPlaceholderText(
            "Describe a mix by artist, language, era, genre, mood, energy, or occasion..."
        )
        self.recommendation_request.returnPressed.connect(
            self.request_ai_recommendations
        )
        row.addWidget(self.recommendation_request, 1)
        self.recommendation_limit = QSpinBox()
        self.recommendation_limit.setRange(1, 20)
        self.recommendation_limit.setValue(self._suggestion_limit)
        self.recommendation_limit.setSuffix(" results")
        self.recommendation_limit.setToolTip(
            "Maximum exact matches to display; a number in the request overrides this value."
        )
        row.addWidget(self.recommendation_limit)
        self.recommendation_button = QPushButton(
            "Find in my library"
            if self.recommendation_ai_enabled.isChecked()
            else "Search internet"
        )
        self.recommendation_button.setObjectName("primaryButton")
        self.recommendation_button.clicked.connect(self.request_ai_recommendations)
        row.addWidget(self.recommendation_button)
        self.start_mix_button = QPushButton("Start mix")
        self.start_mix_button.setObjectName("secondaryButton")
        self.start_mix_button.setEnabled(False)
        self.start_mix_button.setToolTip(
            "Play exact matches first, then continue with verified tracks in the same language."
        )
        self.start_mix_button.clicked.connect(self.start_recommendation_mix)
        row.addWidget(self.start_mix_button)
        self.online_recommendation_button = QPushButton("Search YouTube too")
        self.online_recommendation_button.setObjectName("secondaryButton")
        self.online_recommendation_button.clicked.connect(
            self.search_recommendation_online
        )
        row.addWidget(self.online_recommendation_button)
        self.clear_recommendation_button = QPushButton("Clear")
        self.clear_recommendation_button.setObjectName("secondaryButton")
        self.clear_recommendation_button.clicked.connect(
            self.clear_ai_recommendations
        )
        row.addWidget(self.clear_recommendation_button)
        self.recommendation_status = QLabel("AI idle")
        self.recommendation_status.setObjectName("mutedLabel")
        self.recommendation_status.setMinimumWidth(130)
        row.addWidget(self.recommendation_status)
        layout.addLayout(row)
        self.recommendations = QListWidget()
        self.recommendations.setMaximumHeight(150)
        self.recommendations.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.recommendations.setToolTip(
            "AI suggestions only. No files are edited and nothing is downloaded."
        )
        self.recommendations.itemDoubleClicked.connect(
            self._play_recommended_item
        )
        self.recommendations.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.recommendations.customContextMenuRequested.connect(
            lambda point: self._show_list_song_context_menu(self.recommendations, point)
        )
        self.recommendations.setVisible(False)
        layout.addWidget(self.recommendations)
        card.setVisible(False)
        self.recommendation_toggle.toggled.connect(card.setVisible)
        self.recommendation_toggle.toggled.connect(
            lambda expanded: self.recommendation_toggle.setText(
                "Smart Library Curator  ⌄"
                if expanded
                else "Smart Library Curator  ›"
            )
        )
        container_layout.addWidget(card)
        return container

    def request_ai_recommendations(self) -> None:
        self.recommendation_toggle.setChecked(True)
        if self._recommendation_thread is not None:
            return
        request_text = self.recommendation_request.text().strip()
        if not request_text:
            self.recommendation_status.setText("Describe what to suggest")
            return
        if not self.recommendation_ai_enabled.isChecked():
            self._last_recommendation_request = request_text
            self.recommendation_status.setText("AI off · opening internet search")
            self.search_recommendation_online()
            return
        recommendation_items = self._media_type_items()
        if not recommendation_items:
            self.recommendation_status.setText("Library is empty")
            return
        provider, model, identity = self._resolve_ai_identity()
        self._last_recommendation_request = request_text
        requested_limit = _requested_result_limit(
            request_text, self.recommendation_limit.value()
        )
        self.recommendation_button.setEnabled(False)
        self.recommendation_status.setText(
            f"AI working · {identity or 'not configured'}"
        )
        self.recommendations.clear()
        self.recommendations.setVisible(False)
        log_diagnostic(
            "AI-START",
            f"Library recommendations | provider={provider!r} "
            f"model={model!r} items={len(recommendation_items)}",
        )
        self._run_recommendation_worker(
            request_text,
            recommendation_items,
            model,
            requested_limit,
            finished_slot=self._recommendations_finished,
        )

    def _resolve_ai_identity(self) -> tuple[str, str, str]:
        fallback_model = str(
            self.settings.value("defaults/agentic_model", "qwen2.5:7b") or ""
        )
        provider, model = (
            self.ai_identity_resolver()
            if self.ai_identity_resolver is not None
            else configured_primary_identity(fallback_model)
        )
        identity = " · ".join(value for value in (provider, model) if value)
        return provider, model, identity

    def _run_recommendation_worker(
        self,
        request_text: str,
        items: list[LibraryItem],
        model: str,
        limit: int,
        *,
        finished_slot: Callable[[object, str], None],
        language_continuation: bool = False,
    ) -> None:
        thread = QThread(self)
        worker = LibraryRecommendationWorker(
            request_text,
            list(items),
            model,
            limit,
            language_continuation=language_continuation,
            playlists={name: list(paths) for name, paths in self.playlists.items()},
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(finished_slot)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._recommendation_thread_finished)
        self._recommendation_thread = thread
        self._recommendation_worker = worker
        thread.start()

    def _save_recommendation_ai_setting(self, enabled: bool) -> None:
        self.settings.setValue("ai/tools/media_library", bool(enabled))
        self.settings.sync()
        self.recommendation_button.setText(
            "Find in my library" if enabled else "Search internet"
        )

    def _recommendations_finished(self, result: object, error: str) -> None:
        self._recommendation_worker = None
        recommendations = (
            [value for value in result if isinstance(value, LibraryRecommendation)]
            if isinstance(result, list)
            else []
        )
        self._last_recommendations = recommendations if not error else []
        self.start_mix_button.setEnabled(bool(self._last_recommendations))
        if error:
            self.recommendation_status.setText("AI unavailable")
            self.recommendations.addItem(f"Could not create suggestions: {error}")
            log_diagnostic("AI-FALLBACK", f"Library recommendations failed | {error}")
        elif not recommendations:
            self.recommendation_status.setText("No matching local tracks")
            self.recommendations.addItem(
                "The curator could not independently verify every requested filter "
                "for a local track. "
                "Use 'Search YouTube too' for external discovery."
            )
            log_diagnostic("AI-REVIEW", "Library recommendations returned no tracks")
        else:
            self.recommendation_status.setText(
                f"AI suggested {len(recommendations)} local track(s)"
            )
            for recommendation in recommendations:
                availability = (
                    "LOCAL" if recommendation.exists_locally else "INDEXED · FILE MISSING"
                )
                row = QListWidgetItem(
                    f"[{availability}] {recommendation.item.title} — "
                    f"{recommendation.item.artists} · {recommendation.reason}"
                )
                row.setData(Qt.ItemDataRole.UserRole, recommendation.item.path)
                self.recommendations.addItem(row)
            log_diagnostic(
                "AI-VERIFIED",
                f"Library recommendations grounded={len(recommendations)}",
            )
        self.recommendations.setVisible(True)

    def _recommendation_thread_finished(self) -> None:
        self._recommendation_thread = None
        self.recommendation_button.setEnabled(True)

    def clear_ai_recommendations(self) -> None:
        """Clear the AI prompt and rendered suggestions without changing playback."""
        if self._recommendation_thread is not None:
            return
        self.recommendation_request.clear()
        self._last_recommendation_request = ""
        self._last_recommendations.clear()
        self.start_mix_button.setEnabled(False)
        self.recommendations.clear()
        self.recommendations.setVisible(False)
        self.recommendation_status.setText("AI idle")

    def start_recommendation_mix(self) -> None:
        """Queue exact matches, then find verified same-language continuation tracks."""

        if self._recommendation_thread is not None or not self._last_recommendations:
            return
        exact = [
            value.item for value in self._last_recommendations
            if value.exists_locally
        ]
        if not exact:
            self.recommendation_status.setText("Matching local files are missing")
            return
        self._replace_queue(exact)
        exact_paths = {item.path.casefold() for item in exact}
        remaining = [
            item
            for item in self._media_type_items()
            if item.path.casefold() not in exact_paths
        ]
        if not remaining:
            self.recommendation_status.setText(f"Mix started · {len(exact)} track(s)")
            return
        _provider, model, identity = self._resolve_ai_identity()
        self.start_mix_button.setEnabled(False)
        self.recommendation_status.setText(
            f"Mix started · {identity} · finding continuation tracks"
        )
        self._run_recommendation_worker(
            self._last_recommendation_request,
            remaining,
            model,
            20,
            language_continuation=True,
            finished_slot=self._finish_recommendation_mix,
        )

    def _finish_recommendation_mix(
        self, result: object, error: str
    ) -> None:
        self._recommendation_worker = None
        recommendations = (
            [value for value in result if isinstance(value, LibraryRecommendation)]
            if isinstance(result, list)
            else []
        )
        continuation = [value.item for value in recommendations if value.exists_locally]
        added = self._append_to_queue(continuation)
        self.start_mix_button.setEnabled(bool(self._last_recommendations))
        suffix = f" · {added} related track(s) queued" if added else ""
        if error:
            suffix = " · continuation unavailable"
        self.recommendation_status.setText(
            f"Mix playing {len(self.queue)} track(s){suffix}"
        )

    def reset_page(self) -> bool:
        """Clear library configuration and UI state without deleting media files."""

        if any(
            thread is not None
            for thread in (
                self._scanner_thread,
                self._search_thread,
                self._recommendation_thread,
            )
        ):
            return False
        self._search_debounce.stop()
        if self.player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            self.stop()
        self.folder_list.clear()
        self._render_folder_chips()
        self.search.clear()
        self.media_type_filter.setCurrentIndex(0)
        self.year_from.setValue(0)
        self.year_to.setValue(0)
        self.clear_ai_recommendations()
        self.items.clear()
        self.filtered.clear()
        self.queue.clear()
        self._queue_source.clear()
        self.queue_index = -1
        self.playlists.clear()
        self._active_playlist = ""
        self._open_album_items.clear()
        self._open_album_name = ""
        self._open_album_artists.clear()
        self.facets.clear()
        self.table.setRowCount(0)
        self.albums.clear()
        self.album_tracks.setRowCount(0)
        self.album_stack.setCurrentIndex(0)
        self.suggestion_model.setStringList([])
        self._suggestions_by_text.clear()
        self.match_status.clear()
        self.album_browser_context.setText("Albums from all artists")
        self.now_playing.setText("Nothing playing")
        self._update_queue_status()
        self.elapsed.setText("0:00 / 0:00")
        self.now_playing_art.clear()
        self.settings.remove("library")
        self._render_playlists()
        self._search_debounce.stop()
        return True

    def search_recommendation_online(self) -> None:
        """Route the unchanged AI request to verified YouTube Search Song results."""
        query = self.recommendation_request.text().strip() or self._last_recommendation_request
        if not query:
            self.recommendation_status.setText("Describe what to search")
            return
        self.recommendation_status.setText("Opening YouTube search…")
        query = playlist_taste_search_query(query, self.items, self.playlists)
        self.request_search_song.emit(query)

    def _play_recommended_item(self, row: QListWidgetItem) -> None:
        path = str(row.data(Qt.ItemDataRole.UserRole) or "")
        item = next((value for value in self.items if value.path == path), None)
        if item is None or not Path(item.path).is_file():
            self.recommendation_status.setText("Selected local file is missing")
            return
        self._replace_queue([item])

    def _build_browser(self) -> QWidget:
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.addWidget(QLabel("Artists • select one or several"), 0, 0)
        self.facets = QListWidget()
        self.facets.setProperty("persistentFilterSelection", True)
        self.facets.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.facets.itemSelectionChanged.connect(self._schedule_search)
        self.facets.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.facets.customContextMenuRequested.connect(
            self._show_artist_context_menu
        )
        grid.addWidget(self.facets, 1, 0)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.media_results_label = QLabel("Songs and videos")
        actions.addWidget(self.media_results_label)
        self.all_tracks_button = QPushButton("‹ All tracks")
        self.all_tracks_button.setObjectName("secondaryButton")
        self.all_tracks_button.setToolTip("Clear artist selection and show all tracks")
        self.all_tracks_button.clicked.connect(self._show_all_albums)
        self.all_tracks_button.setVisible(False)
        actions.addWidget(self.all_tracks_button)
        self.match_status = QLabel()
        self.match_status.setObjectName("mutedLabel")
        actions.addWidget(self.match_status)
        actions.addStretch(1)
        self.add_selected_to_playlist_button = QPushButton("Add to playlist")
        self.add_selected_to_playlist_button.setObjectName("secondaryButton")
        self.add_selected_to_playlist_button.setToolTip(
            "Add the selected tracks to a saved playlist"
        )
        self.add_selected_to_playlist_button.clicked.connect(
            self.add_selected_tracks_to_playlist
        )
        actions.addWidget(self.add_selected_to_playlist_button)
        for text, handler, primary in (
            ("Play selected", self.play_selected, True),
            ("Queue selected", self.enqueue_selected, False),
            ("Play all matches", self.play_all_matches, False),
            ("Shuffle all", self.shuffle_all_matches, False),
        ):
            button = QPushButton(text)
            button.setObjectName("primaryButton" if primary else "secondaryButton")
            button.clicked.connect(handler)
            actions.addWidget(button)
        grid.addLayout(actions, 0, 1)

        self.table = self._new_media_table(
            ["Title", "Artist", "Album", "Year", "Type", "Length"]
        )
        self.table.doubleClicked.connect(lambda _index: self.play_selected())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            lambda point: self._show_table_song_context_menu(self.table, point)
        )
        grid.addWidget(self.table, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 4)
        return widget

    def _build_album_section(self) -> QWidget:
        self.album_stack = QStackedWidget()
        self.album_stack.setMinimumHeight(330)

        browser = QWidget()
        browser_layout = QVBoxLayout(browser)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        heading = QHBoxLayout()
        self.all_albums_button = QPushButton("‹ All albums")
        self.all_albums_button.setObjectName("secondaryButton")
        self.all_albums_button.setToolTip("Clear artist selection and show all albums")
        self.all_albums_button.clicked.connect(self._show_all_albums)
        self.all_albums_button.setVisible(False)
        heading.addWidget(self.all_albums_button)
        self.album_browser_context = QLabel("Albums from all artists")
        self.album_browser_context.setObjectName("sectionTitle")
        heading.addWidget(self.album_browser_context)
        heading.addStretch(1)
        helper = QLabel("Open an album to select individual tracks")
        helper.setObjectName("mutedLabel")
        heading.addWidget(helper)
        self.add_album_to_playlist_button = QPushButton("Add album to playlist")
        self.add_album_to_playlist_button.setObjectName("secondaryButton")
        self.add_album_to_playlist_button.setToolTip(
            "Add every track from the selected album to a playlist"
        )
        self.add_album_to_playlist_button.clicked.connect(
            self.add_selected_album_to_playlist
        )
        heading.addWidget(self.add_album_to_playlist_button)
        browser_layout.addLayout(heading)
        self.albums = AlbumGridListWidget()
        self.albums.setViewMode(QListWidget.ViewMode.IconMode)
        self.albums.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.albums.setIconSize(QSize(108, 108))
        self.albums.setGridSize(QSize(178, 165))
        self.albums.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.albums.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.albums.setWordWrap(True)
        self.albums.itemSelectionChanged.connect(self._update_album_browser_context)
        self.albums.itemDoubleClicked.connect(self.open_album)
        self.albums.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.albums.customContextMenuRequested.connect(self._show_album_context_menu)
        browser_layout.addWidget(self.albums, 1)
        self.album_stack.addWidget(browser)

        details = QWidget()
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_header = QHBoxLayout()
        self.album_back_button = QPushButton("‹ All albums")
        self.album_back_button.setObjectName("secondaryButton")
        self.album_back_button.clicked.connect(lambda: self.album_stack.setCurrentIndex(0))
        detail_header.addWidget(self.album_back_button)
        self.album_detail_title = QLabel("Tracks from album")
        self.album_detail_title.setObjectName("sectionTitle")
        self.album_detail_title.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.album_detail_title.customContextMenuRequested.connect(
            self._show_open_album_context_menu
        )
        detail_header.addWidget(self.album_detail_title)
        self.album_detail_context = QLabel()
        self.album_detail_context.setObjectName("mutedLabel")
        detail_header.addWidget(self.album_detail_context)
        detail_header.addStretch(1)
        for text, mode in (
            ("Play all", "play"),
            ("Shuffle all", "shuffle"),
        ):
            button = QPushButton(text)
            button.setObjectName("primaryButton" if text == "Play all" else "secondaryButton")
            button.clicked.connect(
                lambda _checked=False, queue_mode=mode:
                self.play_album_tracks(queue_mode, False)
            )
            detail_header.addWidget(button)
        self.add_open_album_to_playlist_button = QPushButton("Add to playlist")
        self.add_open_album_to_playlist_button.setObjectName("secondaryButton")
        self.add_open_album_to_playlist_button.setToolTip(
            "Add selected album tracks, or the full album when none are selected"
        )
        self.add_open_album_to_playlist_button.clicked.connect(
            self.add_open_album_tracks_to_playlist
        )
        detail_header.addWidget(self.add_open_album_to_playlist_button)
        for text, mode, selected in (
            ("Play selected", "play", True),
            ("Queue selected", "queue", True),
            ("Shuffle selected", "shuffle", True),
        ):
            button = QPushButton(text)
            button.setObjectName("primaryButton" if text == "Play all" else "secondaryButton")
            button.clicked.connect(
                lambda _checked=False, queue_mode=mode, only_selected=selected:
                self.play_album_tracks(queue_mode, only_selected)
            )
            detail_header.addWidget(button)
        detail_layout.addLayout(detail_header)
        self.album_tracks = self._new_media_table(["Title", "Artist", "Year", "Length"])
        self.album_tracks.itemSelectionChanged.connect(self._update_album_track_context)
        self.album_tracks.doubleClicked.connect(
            lambda _index: self.play_album_tracks("play", True)
        )
        self.album_tracks.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.album_tracks.customContextMenuRequested.connect(
            lambda point: self._show_table_song_context_menu(self.album_tracks, point)
        )
        detail_layout.addWidget(self.album_tracks, 1)
        self.album_stack.addWidget(details)
        return self.album_stack

    def _build_playlist_drawer(self) -> QWidget:
        drawer = GlassCard()
        drawer.setObjectName("playlistDrawer")
        drawer.setMinimumWidth(320)
        drawer.setMaximumWidth(390)
        layout = QVBoxLayout(drawer)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        heading = QLabel("Playlists")
        heading.setObjectName("sectionTitle")
        header.addWidget(heading)
        header.addStretch(1)
        close_button = QPushButton("‹")
        close_button.setObjectName("secondaryButton")
        close_button.setToolTip("Collapse playlists")
        close_button.clicked.connect(
            lambda: self.playlist_toggle_button.setChecked(False)
        )
        header.addWidget(close_button)
        layout.addLayout(header)

        self.playlist_list = QListWidget()
        self.playlist_list.setAccessibleName("Saved playlists")
        self.playlist_list.itemSelectionChanged.connect(
            self._playlist_selection_changed
        )
        layout.addWidget(self.playlist_list, 1)

        playlist_actions = QHBoxLayout()
        for text, handler in (
            ("New", self.create_playlist),
            ("Rename", self.rename_playlist),
            ("Delete", self.delete_playlist),
        ):
            button = QPushButton(text)
            button.setObjectName("secondaryButton")
            button.clicked.connect(handler)
            playlist_actions.addWidget(button)
        layout.addLayout(playlist_actions)

        self.playlist_track_status = QLabel("Select or create a playlist")
        self.playlist_track_status.setObjectName("mutedLabel")
        self.playlist_track_status.setWordWrap(True)
        layout.addWidget(self.playlist_track_status)
        self.playlist_tracks = QListWidget()
        self.playlist_tracks.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.playlist_tracks.setAccessibleName("Tracks in selected playlist")
        self.playlist_tracks.itemDoubleClicked.connect(
            lambda _entry: self.play_selected_playlist_tracks()
        )
        self.playlist_tracks.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.playlist_tracks.customContextMenuRequested.connect(
            self._show_playlist_track_context_menu
        )
        layout.addWidget(self.playlist_tracks, 3)

        track_actions = QHBoxLayout()
        for text, handler, primary in (
            ("Play", self.play_selected_playlist_tracks, True),
            ("Queue", self.queue_selected_playlist_tracks, False),
            ("Remove", self.remove_selected_playlist_tracks, False),
        ):
            button = QPushButton(text)
            button.setObjectName("primaryButton" if primary else "secondaryButton")
            button.clicked.connect(handler)
            track_actions.addWidget(button)
        layout.addLayout(track_actions)
        return drawer

    def _toggle_playlist_drawer(self, visible: bool) -> None:
        self.playlist_drawer.setVisible(bool(visible))
        arrow = "›" if visible else "‹"
        self.playlist_toggle_button.setText(
            f"Playlists ({len(self.playlists)}) {arrow}"
        )

    def _save_playlists(self) -> None:
        self.settings.setValue(
            "library/playlists", encode_playlists(self.playlists)
        )
        self.settings.setValue("library/active_playlist", self._active_playlist)
        self.settings.sync()

    def _render_playlists(self) -> None:
        self.playlist_list.blockSignals(True)
        self.playlist_list.clear()
        selected_row = -1
        for row, name in enumerate(sorted(self.playlists, key=str.casefold)):
            entry = QListWidgetItem(
                f"{name}  ·  {len(self.playlists[name])} track(s)"
            )
            entry.setData(Qt.ItemDataRole.UserRole, name)
            self.playlist_list.addItem(entry)
            if name == self._active_playlist:
                selected_row = row
        if selected_row < 0 and self.playlist_list.count():
            selected_row = 0
            self._active_playlist = str(
                self.playlist_list.item(0).data(Qt.ItemDataRole.UserRole)
            )
        if selected_row >= 0:
            self.playlist_list.setCurrentRow(selected_row)
        self.playlist_list.blockSignals(False)
        self._render_playlist_tracks()
        arrow = "›" if self.playlist_drawer.isVisible() else "‹"
        self.playlist_toggle_button.setText(
            f"Playlists ({len(self.playlists)}) {arrow}"
        )

    def _playlist_selection_changed(self) -> None:
        selected = self.playlist_list.currentItem()
        self._active_playlist = (
            str(selected.data(Qt.ItemDataRole.UserRole)) if selected else ""
        )
        self._save_playlists()
        self._render_playlist_tracks()

    def _library_item_for_path(self, path: str) -> LibraryItem:
        wanted = path.casefold()
        indexed = next(
            (item for item in self.items if item.path.casefold() == wanted), None
        )
        if indexed is not None:
            return indexed
        source = Path(path)
        return LibraryItem(
            path=path,
            title=source.stem or source.name,
            album="File not currently indexed",
            artists="Unknown artist",
            year=None,
            duration_ms=0,
            media_type="video" if source.suffix.casefold() in {
                ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"
            } else "audio",
            modified_ns=0,
        )

    def _active_playlist_items(self) -> list[LibraryItem]:
        return [
            self._library_item_for_path(path)
            for path in self.playlists.get(self._active_playlist, [])
        ]

    def _render_playlist_tracks(self) -> None:
        self.playlist_tracks.clear()
        paths = self.playlists.get(self._active_playlist, [])
        if not self._active_playlist:
            self.playlist_track_status.setText("Select or create a playlist")
            return
        self.playlist_track_status.setText(
            f"{self._active_playlist} · {len(paths)} track(s)"
        )
        for item in self._active_playlist_items():
            exists = Path(item.path).is_file()
            prefix = "" if exists else "[Missing] "
            entry = QListWidgetItem(
                f"{prefix}{item.title}\n{item.artists} · {item.album}"
            )
            entry.setData(Qt.ItemDataRole.UserRole, item.path)
            entry.setToolTip(item.path)
            self.playlist_tracks.addItem(entry)

    def create_playlist(self, name: str | None = None) -> str:
        if name is None:
            name, accepted = QInputDialog.getText(
                self, "Create playlist", "Playlist name"
            )
            if not accepted:
                return ""
        name = str(name).strip()
        if not name:
            return ""
        existing = next(
            (value for value in self.playlists if value.casefold() == name.casefold()),
            "",
        )
        if existing:
            self._active_playlist = existing
        else:
            self.playlists[name] = []
            self._active_playlist = name
        self._save_playlists()
        self._render_playlists()
        return self._active_playlist

    def rename_playlist(self) -> None:
        if not self._active_playlist:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Rename playlist",
            "Playlist name",
            text=self._active_playlist,
        )
        name = name.strip()
        if not accepted or not name or name == self._active_playlist:
            return
        if any(
            existing.casefold() == name.casefold()
            for existing in self.playlists
            if existing != self._active_playlist
        ):
            QMessageBox.warning(self, "Playlist exists", "That playlist already exists.")
            return
        paths = self.playlists.pop(self._active_playlist)
        self.playlists[name] = paths
        self._active_playlist = name
        self._save_playlists()
        self._render_playlists()

    def delete_playlist(self) -> None:
        if not self._active_playlist:
            return
        answer = QMessageBox.question(
            self,
            "Delete playlist",
            f'Delete "{self._active_playlist}"? No media files will be deleted.',
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self.playlists[self._active_playlist]
        self._active_playlist = ""
        self._save_playlists()
        self._render_playlists()

    def _choose_playlist(self) -> str:
        if not self.playlists:
            return self.create_playlist()
        names = sorted(self.playlists, key=str.casefold)
        create_label = "Create new playlist…"
        choices = [create_label, *names]
        current = (
            names.index(self._active_playlist) + 1
            if self._active_playlist in names
            else 0
        )
        choice, accepted = QInputDialog.getItem(
            self,
            "Add to playlist",
            "Playlist",
            choices,
            current,
            False,
        )
        if not accepted:
            return ""
        return self.create_playlist() if choice == create_label else str(choice)

    def add_items_to_playlist(
        self,
        items: list[LibraryItem],
        playlist_name: str | None = None,
        *,
        duplicate_policy: str | None = None,
    ) -> int:
        """Add track links and request one explicit decision when duplicates exist."""

        if not items:
            return 0
        name = playlist_name or self._choose_playlist()
        if not name or name not in self.playlists:
            return 0
        existing = self.playlists[name]
        known = {path.casefold() for path in existing}
        duplicate_count = sum(item.path.casefold() in known for item in items)
        policy = duplicate_policy
        if duplicate_count and policy not in {"skip", "add"}:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Track already present")
            box.setText("Track already present")
            box.setInformativeText(
                f"{duplicate_count} selected track(s) already exist in {name}."
            )
            skip_button = box.addButton(
                "Skip duplicates", QMessageBox.ButtonRole.AcceptRole
            )
            add_button = box.addButton(
                "Add anyway", QMessageBox.ButtonRole.DestructiveRole
            )
            box.exec()
            policy = "skip" if box.clickedButton() is skip_button else "add"
            if box.clickedButton() not in {skip_button, add_button}:
                return 0
        result = add_playlist_paths(
            existing,
            [item.path for item in items],
            skip_duplicates=policy != "add",
        )
        self.playlists[name] = result.paths
        self._active_playlist = name
        self._save_playlists()
        self._render_playlists()
        self.playlist_track_status.setText(
            f"{name} · added {result.added} track(s)"
            + (f" · skipped {result.duplicates} duplicate(s)" if policy != "add" and result.duplicates else "")
        )
        return result.added

    def _selected_playlist_items(self) -> list[LibraryItem]:
        rows = sorted(
            {index.row() for index in self.playlist_tracks.selectionModel().selectedRows()}
        )
        items = self._active_playlist_items()
        return [items[row] for row in rows if 0 <= row < len(items)]

    def play_selected_playlist_tracks(self) -> None:
        selected = self._selected_playlist_items() or self._active_playlist_items()
        self._replace_queue(selected)

    def queue_selected_playlist_tracks(self) -> None:
        selected = self._selected_playlist_items() or self._active_playlist_items()
        self._append_to_queue(selected)

    def remove_selected_playlist_tracks(self) -> None:
        if not self._active_playlist:
            return
        rows = sorted(
            {index.row() for index in self.playlist_tracks.selectionModel().selectedRows()},
            reverse=True,
        )
        paths = self.playlists[self._active_playlist]
        for row in rows:
            if 0 <= row < len(paths):
                paths.pop(row)
        self._save_playlists()
        self._render_playlists()

    def _show_playlist_track_context_menu(self, position: QPoint) -> None:
        entry = self.playlist_tracks.itemAt(position)
        if entry is None:
            return
        clicked_path = str(entry.data(Qt.ItemDataRole.UserRole) or "")
        selected = self._selected_playlist_items()
        if not any(item.path == clicked_path for item in selected):
            self.playlist_tracks.setCurrentItem(entry)
            selected = [self._library_item_for_path(clicked_path)]
        self._show_song_context_menu(
            selected,
            self.playlist_tracks.viewport().mapToGlobal(position),
            source_playlist=self._active_playlist,
        )

    def _build_queue_drawer(self) -> QWidget:
        drawer = GlassCard()
        drawer.setObjectName("playbackQueueDrawer")
        drawer.setMinimumWidth(300)
        drawer.setMaximumWidth(370)
        layout = QVBoxLayout(drawer)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        heading = QLabel("Current queue")
        heading.setObjectName("sectionTitle")
        header.addWidget(heading)
        header.addStretch(1)
        close_button = QPushButton("›")
        close_button.setObjectName("secondaryButton")
        close_button.setToolTip("Collapse queue")
        close_button.clicked.connect(lambda: self.queue_toggle_button.setChecked(False))
        header.addWidget(close_button)
        layout.addLayout(header)
        hint = QLabel("Drag one track up or down to change the playback order.")
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.queue_list = AnimatedQueueList()
        self.queue_list.setAccessibleName("Current playback queue")
        self.queue_list.itemDoubleClicked.connect(self._play_queue_entry)
        self.queue_list.orderChanged.connect(self._queue_reordered)
        self.queue_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(
            lambda point: self._show_list_song_context_menu(self.queue_list, point)
        )
        layout.addWidget(self.queue_list, 1)
        actions = QHBoxLayout()
        for text, handler in (
            ("Play", self._play_selected_queue_entry),
            ("Remove", self._remove_selected_queue_entry),
            ("Clear", self.clear_playback_queue),
        ):
            button = QPushButton(text)
            button.setObjectName("secondaryButton")
            button.clicked.connect(handler)
            actions.addWidget(button)
        layout.addLayout(actions)
        return drawer

    def _build_player(self) -> QWidget:
        self._player_host = QWidget()
        self._player_host_layout = QVBoxLayout(self._player_host)
        self._player_host_layout.setContentsMargins(0, 0, 0, 0)
        self._player_host_layout.setSpacing(0)
        card = GlassCard()
        self.player_card = card
        card.setObjectName("playerCard")
        card.setMinimumHeight(166)
        grid = QGridLayout(card)
        self.player_grid = grid
        grid.setContentsMargins(18, 13, 18, 13)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(320)
        self.video.setMaximumHeight(520)
        self.video.setToolTip(
            "Double-click the video to enter or leave full-screen playback"
        )
        self.video.installEventFilter(self)
        self.video.setVisible(False)
        grid.addWidget(self.video, 0, 0, 1, 13)
        self.now_playing_art = QLabel()
        self.now_playing_art.setObjectName("nowPlayingArtwork")
        self.now_playing_art.setFixedSize(112, 112)
        self.now_playing_art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.now_playing_art.setToolTip("Now-playing album artwork")
        grid.addWidget(self.now_playing_art, 1, 0, 3, 1)
        self.now_playing = QLabel("Nothing playing")
        self.now_playing.setObjectName("sectionTitle")
        self.now_playing.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.now_playing.customContextMenuRequested.connect(
            self._show_now_playing_context_menu
        )
        grid.addWidget(self.now_playing, 1, 1, 1, 9)
        self.queue_status = QLabel("Queue is empty")
        self.queue_status.setObjectName("mutedLabel")
        self.queue_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.queue_status, 1, 10, 1, 3)
        self.position = AnimatedSeekSlider()
        self.position.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.position.sliderReleased.connect(
            lambda: setattr(self, "_seeking", False)
        )
        self.position.seekRequested.connect(self.player_seek_requested)
        self.elapsed = QLabel("0:00 / 0:00")
        self.elapsed.setMinimumWidth(96)
        self.elapsed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.position, 2, 1, 1, 11)
        grid.addWidget(self.elapsed, 2, 12)
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        controls_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.shuffle_button = QPushButton()
        self.shuffle_button.setObjectName("playerModeButton")
        self.shuffle_button.setCheckable(True)
        self.shuffle_button.clicked.connect(self.set_shuffle_enabled)
        controls_layout.addWidget(self.shuffle_button)
        for offset, (icon_name, handler, tooltip) in enumerate(
            (
                ("previous", self.previous, "Previous"),
                ("play", self.toggle_play, "Play / Pause"),
                ("stop", self.stop, "Stop"),
                ("next", self.next, "Next"),
            )
        ):
            button = QPushButton()
            button.setObjectName(
                "playerPrimaryButton" if offset == 1 else "playerControlButton"
            )
            button.setFixedSize(56 if offset == 1 else 46, 46)
            icon_size = 27 if offset == 1 else 23
            button.setIcon(_transport_icon(icon_name))
            button.setIconSize(QSize(icon_size, icon_size))
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.clicked.connect(handler)
            controls_layout.addWidget(button)
            if offset == 1:
                self.play_button = button
        self.repeat_button = QPushButton()
        self.repeat_button.setObjectName("playerModeButton")
        self.repeat_button.clicked.connect(self.cycle_repeat_mode)
        controls_layout.addWidget(self.repeat_button)
        self.fullscreen_button = QPushButton("Full screen")
        self.fullscreen_button.setObjectName("playerModeButton")
        self.fullscreen_button.setEnabled(False)
        self.fullscreen_button.setToolTip(
            "Show the video and playback controls full screen"
        )
        self.fullscreen_button.setAccessibleName("Toggle full-screen video")
        self.fullscreen_button.clicked.connect(self.toggle_video_fullscreen)
        controls_layout.addWidget(self.fullscreen_button)
        self._update_playback_mode_buttons()
        grid.addWidget(controls, 3, 1, 1, 9)
        volume_label = QLabel("Volume")
        volume_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(volume_label, 3, 10)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(self.settings.value("library/volume", 75)))
        self.volume.setMinimumWidth(150)
        self.volume.setMaximumWidth(240)
        grid.addWidget(self.volume, 3, 11, 1, 2)
        grid.setColumnStretch(4, 1)
        grid.setColumnStretch(9, 1)
        self.fullscreen_shortcut = QShortcut(QKeySequence("Esc"), card)
        self.fullscreen_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.fullscreen_shortcut.activated.connect(self.exit_video_fullscreen)
        self._player_host_layout.addWidget(card)
        return self._player_host

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.video and event.type() == QEvent.Type.MouseButtonDblClick:
            self.toggle_video_fullscreen()
            return True
        return super().eventFilter(watched, event)

    def toggle_video_fullscreen(self) -> None:
        """Show the existing video player and controls in a full-screen window."""

        if self._player_fullscreen:
            self.exit_video_fullscreen()
            return
        if not self.video.isVisible():
            return
        self._player_fullscreen = True
        self._fullscreen_window = self.window()
        self._fullscreen_window_state = self._fullscreen_window.windowState()
        self._fullscreen_hidden_widgets = []
        branch: QWidget = self.player_card
        parent = branch.parentWidget()
        while parent is not None:
            for sibling in parent.findChildren(
                QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly
            ):
                if sibling is branch:
                    continue
                was_hidden = sibling.isHidden()
                self._fullscreen_hidden_widgets.append((sibling, was_hidden))
                sibling.hide()
            if parent is self._fullscreen_window:
                break
            branch = parent
            parent = branch.parentWidget()
        self.video.setMinimumHeight(0)
        self.video.setMaximumHeight(16_777_215)
        self.player_grid.setRowStretch(0, 1)
        self.fullscreen_button.setText("Exit full screen")
        self._fullscreen_window.showFullScreen()
        self.video.setFocus()

    def exit_video_fullscreen(self) -> None:
        """Restore the player card to the Media Library workspace."""

        if not self._player_fullscreen:
            return
        self._player_fullscreen = False
        fullscreen_window = self._fullscreen_window
        if fullscreen_window is not None:
            fullscreen_window.setWindowState(self._fullscreen_window_state)
            fullscreen_window.show()
        for widget, was_hidden in self._fullscreen_hidden_widgets:
            widget.setVisible(not was_hidden)
        self._fullscreen_hidden_widgets = []
        self._fullscreen_window = None
        self.video.setMinimumHeight(320)
        self.video.setMaximumHeight(520)
        self.player_grid.setRowStretch(0, 0)
        self.fullscreen_button.setText("Full screen")

    @staticmethod
    def _new_media_table(labels: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(labels))
        table.setHorizontalHeaderLabels(labels)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Preserve native desktop selection semantics: ordinary click selects
        # one row, Ctrl (Command on macOS) adds/removes rows, and Shift selects
        # the complete range from the current selection anchor.
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setToolTip(
            "Click to select; Ctrl/Cmd-click adds rows; Shift-click selects a range"
        )
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(True)
        header = table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(False)
        for column in range(table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        table.setProperty("initialContentSizingDone", False)
        table.sortItems(0, Qt.SortOrder.AscendingOrder)
        return table

    def _connect_player(self) -> None:
        self.audio_output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_buffer_output = QAudioBufferOutput(self)
        self.player.setAudioBufferOutput(self.audio_buffer_output)
        self._spectrum_thread = QThread(self)
        self._spectrum_thread.setObjectName("media-spectrum-analysis")
        self._spectrum_worker = SpectrumAnalyzer()
        self._spectrum_worker.moveToThread(self._spectrum_thread)
        # PyQt 6.11 does not directly connect QAudioBuffer's const-reference
        # signal to Python QObject slots. Copy the implicitly shared handle on
        # the UI thread, then queue only that handle to the analysis thread.
        self.audio_buffer_output.audioBufferReceived.connect(
            lambda buffer: self.spectrum_buffer_ready.emit(QAudioBuffer(buffer))
        )
        self.spectrum_buffer_ready.connect(
            self._spectrum_worker.analyze,
            Qt.ConnectionType.QueuedConnection,
        )
        self._spectrum_worker.spectrum_ready.connect(self.spectrum_ready)
        self._spectrum_thread.finished.connect(self._spectrum_worker.deleteLater)
        self._spectrum_thread.start()
        self.player.setVideoOutput(self.video)
        self.audio_output.setVolume(self.volume.value() / 100)
        self.volume.valueChanged.connect(
            lambda value: self.audio_output.setVolume(value / 100)
        )
        self.volume.valueChanged.connect(
            lambda value: self.settings.setValue("library/volume", value)
        )
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(
            lambda value: self.position.setMaximum(max(0, value))
        )
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.player.errorOccurred.connect(self._playback_error)

    def _install_media_shortcuts(self) -> None:
        keys = (
            (Qt.Key.Key_MediaTogglePlayPause, "toggle"),
            (Qt.Key.Key_MediaPlay, "play"),
            (Qt.Key.Key_MediaPause, "pause"),
            (Qt.Key.Key_MediaStop, "stop"),
            (Qt.Key.Key_MediaPrevious, "previous"),
            (Qt.Key.Key_MediaNext, "next"),
        )
        self.shortcuts = []
        for key, command in keys:
            shortcut = QShortcut(QKeySequence(key), self.window())
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(
                lambda action=command: self._dispatch_media_command(
                    action, "qt-shortcut"
                )
            )
            self.shortcuts.append(shortcut)
        for sequence, command in (
            ("Ctrl+Space", "toggle"),
            ("Ctrl+Left", "previous"),
            ("Ctrl+Right", "next"),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(
                lambda action=command: self._dispatch_media_command(
                    action, "qt-shortcut"
                )
            )
            self.shortcuts.append(shortcut)
        application = QApplication.instance()
        if sys.platform == "win32" and application is not None:
            self._native_media_filter = WindowsMediaKeyFilter(
                self._dispatch_media_command
            )
            application.installNativeEventFilter(self._native_media_filter)

    def _dispatch_media_command(self, command: str, source: str) -> None:
        now = time.monotonic()
        if now - self._last_media_command_at < 0.25:
            log_diagnostic(
                "MEDIA-KEY", f"Ignored duplicate command={command} source={source}"
            )
            return
        self._last_media_command_at = now
        handlers = {
            "toggle": self.toggle_play,
            "play": self.play,
            "pause": self.pause,
            "stop": self.stop,
            "previous": self.previous,
            "next": self.next,
        }
        handler = handlers.get(command)
        if handler is None:
            return
        log_diagnostic(
            "MEDIA-KEY",
            f"Dispatch command={command} source={source} "
            f"state={self.player.playbackState().name}",
        )
        QTimer.singleShot(0, handler)

    def _load_folders(self) -> None:
        folders = self.settings.value("library/folders", [], type=list)
        self.folder_list.addItems([str(value) for value in folders])
        self._render_folder_chips()

    def folders(self) -> list[str]:
        return [
            self.folder_list.item(index).text()
            for index in range(self.folder_list.count())
        ]

    def add_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Add media library folder")
        if not selected or selected in self.folders():
            return
        self.folder_list.addItem(str(Path(selected).resolve()))
        self._render_folder_chips()
        self._save_folders()
        self.refresh_library()

    def remove_folder(self) -> None:
        for item in self.folder_list.selectedItems():
            self.folder_list.takeItem(self.folder_list.row(item))
        self._render_folder_chips()
        self._save_folders()
        self.refresh_library()

    def _remove_folder_path(self, path: str) -> None:
        for index in range(self.folder_list.count() - 1, -1, -1):
            if self.folder_list.item(index).text() == path:
                self.folder_list.takeItem(index)
        self._render_folder_chips()
        self._save_folders()
        self.refresh_library()

    def _save_folders(self) -> None:
        self.settings.setValue("library/folders", self.folders())

    def refresh_library(self) -> None:
        if self._shutting_down:
            return
        if self._scanner_thread is not None:
            self._scan_refresh_pending = True
            self.library_refresh_button.setText("Refresh queued")
            self.library_refresh_button.setToolTip(
                "Another rescan will run as soon as the current scan finishes"
            )
            return
        if not self.folders():
            self.items = []
            self.apply_filters()
            self._render_playlist_tracks()
            return
        self._scan_refresh_pending = False
        self.library_refresh_button.setEnabled(False)
        self.library_refresh_button.setText("Scanning…")
        self._scan_started_at = time.monotonic()
        log_diagnostic(
            "LIBRARY", f"Background scan started; folders={self.folders()!r}"
        )
        thread = QThread(self)
        worker = LibraryScanner(self.folders())
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._scan_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._scanner_thread_finished)
        self._scanner_thread = thread
        self._scanner_worker = worker
        thread.start()

    def _scanner_thread_finished(self) -> None:
        self._scanner_thread = None
        self.library_refresh_button.setEnabled(True)
        self.library_refresh_button.setText("Refresh")
        self.library_refresh_button.setToolTip(
            "Rescan every configured library folder"
        )
        if self._scan_refresh_pending:
            self._scan_refresh_pending = False
            QTimer.singleShot(0, self.refresh_library)

    def _scan_finished(self, items: object) -> None:
        self._scanner_worker = None
        scanned_items = list(items) if isinstance(items, list) else []
        log_diagnostic(
            "LIBRARY",
            f"Background scan finished; items={len(scanned_items)} "
            f"elapsed={time.monotonic() - self._scan_started_at:.3f}s",
        )
        if scanned_items == self.items:
            log_diagnostic(
                "LIBRARY", "Scan contents unchanged; preserving current browser UI"
            )
            return
        self.items = scanned_items
        available_paths = {item.path.casefold() for item in scanned_items}
        current_path = (
            self.queue[self.queue_index].path
            if 0 <= self.queue_index < len(self.queue)
            else ""
        )
        self.queue = [
            item for item in self.queue if item.path.casefold() in available_paths
        ]
        self._queue_source = [
            item
            for item in self._queue_source
            if item.path.casefold() in available_paths
        ]
        self.queue_index = next(
            (
                index
                for index, item in enumerate(self.queue)
                if item.path == current_path
            ),
            0 if self.queue else -1,
        )
        self._update_queue_status()
        self.apply_filters()
        self._render_playlist_tracks()
        log_diagnostic(
            "LIBRARY",
            f"Initial bounded render complete; table_rows={self.table.rowCount()} "
            f"albums={self.albums.count()}",
        )

    def apply_filters(self) -> None:
        selected_artists = [item.text() for item in self.facets.selectedItems()]
        base_matches = filter_library(
            self.items,
            query=self.search.text(),
            year_from=self.year_from.value() or None,
            year_to=self.year_to.value() or None,
            media_type=self._selected_media_type(),
        )
        available_artists = self._available_artists(base_matches)
        valid_keys = {artist.casefold() for artist in available_artists}
        valid_selected = [
            artist for artist in selected_artists
            if artist.casefold() in valid_keys
        ]
        matches = (
            filter_library(base_matches, artists=valid_selected)
            if valid_selected
            else base_matches
        )
        self._update_artist_facets(available_artists, valid_selected)
        self._apply_search_results(matches)
        self._set_suggestions(matches[: self._suggestion_limit])

    def _selected_media_type(self) -> str:
        value = str(self.media_type_filter.currentData() or "all")
        return value if value in {"all", "audio", "video"} else "all"

    def _media_type_items(self) -> list[LibraryItem]:
        return filter_library(
            self.items, media_type=self._selected_media_type()
        )

    def _media_type_changed(self, _index: int) -> None:
        self.settings.setValue(
            "library/media_type_filter", self._selected_media_type()
        )
        self._sync_media_type_layout()
        self._schedule_search()

    def _sync_media_type_layout(self) -> None:
        media_type = self._selected_media_type()
        labels = {
            "all": "Songs and videos",
            "audio": "Music",
            "video": "Videos",
        }
        self.media_results_label.setText(labels[media_type])
        self.all_tracks_button.setText(
            "‹ All videos" if media_type == "video" else "‹ All tracks"
        )
        show_albums = media_type != "video"
        self.library_splitter.widget(1).setVisible(show_albums)
        if not show_albums:
            self.album_stack.setCurrentIndex(0)

    def _schedule_search(self, *_args: object) -> None:
        self._search_request_id += 1
        self._search_debounce.start()

    def _start_background_search(self) -> None:
        if self._search_thread is not None:
            self._search_pending = True
            return
        request_id = self._search_request_id
        log_diagnostic(
            "LIBRARY-SEARCH",
            f"request={request_id} query={self.search.text()!r} "
            f"items={len(self.items)}",
        )
        artists = [item.text() for item in self.facets.selectedItems()]
        thread = QThread(self)
        worker = LibrarySearchWorker(
            request_id,
            list(self.items),
            self.search.text(),
            artists,
            self.year_from.value() or None,
            self.year_to.value() or None,
            self._selected_media_type(),
            self._suggestion_limit,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._background_search_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._background_search_thread_finished)
        self._search_thread = thread
        self._search_worker = worker
        thread.start()

    def _background_search_finished(
        self,
        request_id: int,
        matches: object,
        suggestions: object,
        available_artists: object,
    ) -> None:
        self._search_worker = None
        if request_id != self._search_request_id:
            self._search_pending = True
            return
        typed_matches = list(matches) if isinstance(matches, list) else []
        log_diagnostic(
            "LIBRARY-SEARCH",
            f"applying request={request_id} matches={len(typed_matches)}",
        )
        typed_artists = (
            [str(value) for value in available_artists]
            if isinstance(available_artists, list)
            else []
        )
        current = [item.text() for item in self.facets.selectedItems()]
        valid_keys = {artist.casefold() for artist in typed_artists}
        valid_selected = [
            artist for artist in current if artist.casefold() in valid_keys
        ]
        self._update_artist_facets(typed_artists, valid_selected)
        self._apply_search_results(typed_matches)
        suggested_items: list[LibraryItem] = []
        by_path = {item.path: item for item in typed_matches}
        for value in suggestions if isinstance(suggestions, list) else []:
            if isinstance(value, tuple) and value and value[0] in by_path:
                suggested_items.append(by_path[value[0]])
        self._set_suggestions(suggested_items)

    def _background_search_thread_finished(self) -> None:
        self._search_thread = None
        if self._search_pending:
            self._search_pending = False
            QTimer.singleShot(0, self._start_background_search)

    def _apply_search_results(self, matches: list[LibraryItem]) -> None:
        self.filtered = matches
        self._applied_query = self.search.text().strip()
        self._applied_media_type = self._selected_media_type()
        self._populate_table(self.table, self.filtered, include_album_and_type=True)
        self.match_status.setText(f"{len(self.filtered):,} match(es)")
        self._render_albums()
        if self.album_stack.currentIndex() == 1:
            valid_paths = {item.path for item in self.filtered}
            remaining = [
                item for item in self._open_album_items
                if item.path in valid_paths
            ]
            if remaining:
                self._open_album_items = remaining
                self._open_album_artists = sorted({item.artists for item in remaining})
                self._populate_table(
                    self.album_tracks,
                    remaining,
                    include_album_and_type=False,
                )
                self._update_album_track_context()
            else:
                self._open_album_items = []
                self._open_album_name = ""
                self._open_album_artists = []
                self.album_tracks.setRowCount(0)
                self.album_stack.setCurrentIndex(0)

    @staticmethod
    def _available_artists(items: list[LibraryItem]) -> list[str]:
        return sorted(
            {
                artist
                for item in items
                for artist in split_artists(item.artists)
            },
            key=str.casefold,
        )

    def _update_artist_facets(
        self, artists: list[str], selected: list[str]
    ) -> None:
        selected_keys = {artist.casefold() for artist in selected}
        self.facets.blockSignals(True)
        self.facets.clear()
        for artist in artists:
            entry = QListWidgetItem(artist)
            self.facets.addItem(entry)
            entry.setSelected(artist.casefold() in selected_keys)
        self.facets.blockSignals(False)
        self.all_albums_button.setVisible(bool(selected_keys))
        self.all_tracks_button.setVisible(bool(selected_keys))

    def _show_all_albums(self) -> None:
        """Return from an artist-filtered collection to the complete album grid."""

        self._search_request_id += 1
        self._search_debounce.stop()
        self.facets.blockSignals(True)
        self.facets.clearSelection()
        self.facets.blockSignals(False)
        self.apply_filters()

    def _set_suggestions(self, matches: list[LibraryItem]) -> None:
        query = self.search.text().strip()
        self._suggestions_by_text = {
            _suggestion_text(item): item
            for item in matches[: self._suggestion_limit]
        }
        values = list(self._suggestions_by_text)
        if query and not values:
            values = ["No search found"]
        self.suggestion_model.setStringList(values)
        self.search_completer.setMaxVisibleItems(self._suggestion_limit)
        if query and self.search.hasFocus():
            self.search_completer.complete()

    def _suggestion_activated(self, text: str) -> None:
        if text == "No search found":
            self.search.setText(self._applied_query)
            return
        item = self._suggestions_by_text.get(text)
        if item is not None:
            self.search.setText(item.title)

    def set_suggestion_limit(self, value: int) -> None:
        self._suggestion_limit = max(1, min(20, int(value)))
        self.search_completer.setMaxVisibleItems(self._suggestion_limit)
        if hasattr(self, "recommendation_limit"):
            self.recommendation_limit.setValue(self._suggestion_limit)
        self._schedule_search()

    def _populate_table(
        self,
        table: QTableWidget,
        items: list[LibraryItem],
        *,
        include_album_and_type: bool,
    ) -> None:
        # A periodic library scan or completed background search can rebuild the
        # table while a song is playing.  Preserve identity by path: retaining a
        # numeric row across the subsequent sort makes the highlight appear to
        # jump to an unrelated song even though the player queue is unchanged.
        selected_paths = {
            str(table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole))
            for index in table.selectionModel().selectedRows()
            if table.item(index.row(), 0) is not None
        }
        current_item = table.currentItem()
        current_path = (
            str(current_item.data(Qt.ItemDataRole.UserRole))
            if current_item is not None
            else ""
        )
        vertical_scroll = table.verticalScrollBar().value()
        horizontal_scroll = table.horizontalScrollBar().value()
        header = table.horizontalHeader()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        table.setUpdatesEnabled(False)
        table.setSortingEnabled(False)
        table.setRowCount(len(items))
        try:
            for row, item in enumerate(items):
                if include_album_and_type:
                    values = (
                        (item.title, item.title.casefold()),
                        (item.artists, item.artists.casefold()),
                        (item.album, item.album.casefold()),
                        (str(item.year or ""), item.year or 0),
                        (item.media_type.title(), item.media_type),
                        (self._time(item.duration_ms), item.duration_ms),
                    )
                else:
                    values = (
                        (item.title, item.title.casefold()),
                        (item.artists, item.artists.casefold()),
                        (str(item.year or ""), item.year or 0),
                        (self._time(item.duration_ms), item.duration_ms),
                    )
                for column, (text, sort_value) in enumerate(values):
                    cell = table.item(row, column)
                    if not isinstance(cell, SortableTableItem):
                        cell = SortableTableItem()
                        table.setItem(row, column, cell)
                    cell.setText(text)
                    cell.setData(Qt.ItemDataRole.UserRole, item.path)
                    cell.setData(SortableTableItem.SORT_ROLE, sort_value)
            table.setSortingEnabled(True)
            if 0 <= sort_column < table.columnCount():
                table.sortItems(sort_column, sort_order)
            selection_model = table.selectionModel()
            selection_model.clearSelection()
            selection_model.setCurrentIndex(
                QModelIndex(), QItemSelectionModel.SelectionFlag.NoUpdate
            )
            rows_by_path = {
                str(table.item(row, 0).data(Qt.ItemDataRole.UserRole)): row
                for row in range(table.rowCount())
                if table.item(row, 0) is not None
            }
            for path in selected_paths:
                row = rows_by_path.get(path)
                if row is not None:
                    selection_model.select(
                        table.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select
                        | QItemSelectionModel.SelectionFlag.Rows,
                    )
            current_row = rows_by_path.get(current_path)
            if current_row is not None:
                selection_model.setCurrentIndex(
                    table.model().index(current_row, 0),
                    QItemSelectionModel.SelectionFlag.NoUpdate,
                )
            if not bool(table.property("initialContentSizingDone")):
                table.resizeColumnsToContents()
                table.setProperty("initialContentSizingDone", True)
        finally:
            table.verticalScrollBar().setValue(vertical_scroll)
            table.horizontalScrollBar().setValue(horizontal_scroll)
            table.setUpdatesEnabled(True)
            table.viewport().update()

    def _render_albums(self) -> None:
        vertical_scroll = self.albums.verticalScrollBar().value()
        selected_album = ""
        selected_entries = self.albums.selectedItems()
        if selected_entries:
            selected_album = str(
                selected_entries[0].data(Qt.ItemDataRole.UserRole) or ""
            )
        self._album_art_generation += 1
        generation = self._album_art_generation
        self._pending_album_art = []
        self.albums.clear()
        groups: dict[str, list[LibraryItem]] = {}
        for item in self.filtered:
            groups.setdefault(item.album, []).append(item)
        for album, tracks in sorted(groups.items(), key=lambda pair: pair[0].casefold()):
            entry = QListWidgetItem(
                f"{album}\n{tracks[0].artists} • {len(tracks)} track(s)"
            )
            entry.setData(Qt.ItemDataRole.UserRole, album)
            artwork_path = tracks[0].path
            icon = self._artwork_cache.get(artwork_path)
            if icon is None:
                self._pending_album_art.append((entry, artwork_path))
            if icon is not None and not icon.isNull():
                entry.setIcon(icon)
            self.albums.addItem(entry)
            if album == selected_album:
                entry.setSelected(True)
        self._update_album_browser_context()
        generation_for_scroll = self._album_art_generation
        QTimer.singleShot(
            0,
            lambda: self._restore_album_scroll(
                generation_for_scroll, vertical_scroll
            ),
        )
        if self._pending_album_art:
            QTimer.singleShot(0, lambda: self._load_next_album_art(generation))

    def _restore_album_scroll(self, generation: int, value: int) -> None:
        if generation == self._album_art_generation:
            self.albums.verticalScrollBar().setValue(value)

    def _load_next_album_art(self, generation: int) -> None:
        if generation != self._album_art_generation or not self._pending_album_art:
            return
        entry, artwork_path = self._pending_album_art.pop(0)
        cover = artwork_bytes(artwork_path)
        pixmap = QPixmap()
        if cover and pixmap.loadFromData(cover):
            thumbnail = pixmap.scaled(
                108,
                108,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon = QIcon(thumbnail)
        else:
            icon = QIcon()
        self._artwork_cache[artwork_path] = icon
        if generation == self._album_art_generation and not icon.isNull():
            entry.setIcon(icon)
        if self._pending_album_art:
            QTimer.singleShot(5, lambda: self._load_next_album_art(generation))

    def open_album(self, entry: QListWidgetItem) -> None:
        album = str(entry.data(Qt.ItemDataRole.UserRole) or "")
        self._open_album_items = [item for item in self.filtered if item.album == album]
        if not self._open_album_items:
            return
        self._open_album_name = album
        self._open_album_artists = sorted(
            {item.artists for item in self._open_album_items}, key=str.casefold
        )
        self.album_detail_title.setText(f"Tracks from: {album}")
        self._populate_table(
            self.album_tracks,
            self._open_album_items,
            include_album_and_type=False,
        )
        self._update_album_track_context()
        self.album_stack.setCurrentIndex(1)

    def _update_album_browser_context(self) -> None:
        artists = [item.text() for item in self.facets.selectedItems()]
        album_count = self.albums.count()
        count_text = f"{album_count:,} album{'s' if album_count != 1 else ''}"
        if artists:
            artist_text = ", ".join(artists)
            context = f"Albums by selected artist(s): {artist_text}  •  {count_text}"
            short_artist_text = artists[0] if len(artists) == 1 else f"{artists[0]} +{len(artists) - 1}"
            self.album_back_button.setText(f"‹ Albums by {short_artist_text}")
            self.album_back_button.setToolTip(
                f"Return to albums filtered by: {artist_text}"
            )
        else:
            context = f"Albums from all artists  •  {count_text}"
            self.album_back_button.setText("‹ All albums")
            self.album_back_button.setToolTip("Return to the complete album grid")
        selected = self.albums.selectedItems()
        if selected:
            album = str(selected[0].data(Qt.ItemDataRole.UserRole) or "")
            if album:
                context += f"  •  Selected album: {album}"
        self.album_browser_context.setText(context)
        self.album_browser_context.setToolTip(context)

    def _update_album_track_context(self) -> None:
        if not self._open_album_name:
            self.album_detail_context.clear()
            return
        selected_count = len(self.album_tracks.selectionModel().selectedRows())
        track_count = len(self._open_album_items)
        artists = ", ".join(self._open_album_artists) or "Unknown artist"
        context = (
            f"Artists: {artists}  •  {track_count:,} track"
            f"{'s' if track_count != 1 else ''}  •  {selected_count:,} selected"
        )
        self.album_detail_context.setText(context)
        self.album_detail_context.setToolTip(context)

    def _show_table_song_context_menu(
        self, table: QTableWidget, position: QPoint
    ) -> None:
        index = table.indexAt(position)
        if not index.isValid():
            return
        path_item = table.item(index.row(), 0)
        if path_item is None:
            return
        path = str(path_item.data(Qt.ItemDataRole.UserRole) or "")
        if path:
            candidates = (
                self._open_album_items if table is self.album_tracks else self.filtered
            )
            selected = self._selected_items(table, candidates)
            if not any(item.path == path for item in selected):
                selected = [self._library_item_for_path(path)]
            self._show_song_context_menu(
                selected, table.viewport().mapToGlobal(position)
            )

    def _show_list_song_context_menu(
        self, widget: QListWidget, position: QPoint
    ) -> None:
        entry = widget.itemAt(position)
        if entry is None:
            return
        path = str(entry.data(Qt.ItemDataRole.UserRole) or "")
        if path:
            self._show_song_context_menu(
                [self._library_item_for_path(path)],
                widget.viewport().mapToGlobal(position),
            )

    def _show_now_playing_context_menu(self, position: QPoint) -> None:
        if not 0 <= self.queue_index < len(self.queue):
            return
        self._show_song_context_menu(
            [self.queue[self.queue_index]],
            self.now_playing.mapToGlobal(position),
        )

    def _show_song_context_menu(
        self,
        items: list[LibraryItem],
        global_position: QPoint,
        *,
        source_playlist: str = "",
    ) -> None:
        if not items:
            return
        menu = QMenu(self)
        self._add_playlist_destinations(menu, items)
        if source_playlist:
            remove_action = menu.addAction("Remove from this playlist")
            remove_action.triggered.connect(self.remove_selected_playlist_tracks)
        menu.addSeparator()
        edit_action = menu.addAction("Edit File…")
        edit_action.setEnabled(len(items) == 1)
        edit_action.setToolTip(
            "Open this media file in the Edit File workspace"
            if len(items) == 1
            else "Edit File accepts one track at a time"
        )
        edit_action.triggered.connect(
            lambda _checked=False, selected_path=items[0].path: self.request_edit_file.emit(
                selected_path
            )
        )
        menu.exec(global_position)

    def _add_playlist_destinations(
        self, menu: QMenu, items: list[LibraryItem], *, label: str = "Add to playlist"
    ) -> None:
        submenu = menu.addMenu(label)
        new_action = submenu.addAction("New playlist…")
        new_action.triggered.connect(
            lambda _checked=False, selected=list(items): self._add_to_new_playlist(selected)
        )
        if self.playlists:
            submenu.addSeparator()
        for name in sorted(self.playlists, key=str.casefold):
            action = submenu.addAction(name)
            action.triggered.connect(
                lambda _checked=False, playlist=name, selected=list(items):
                self.add_items_to_playlist(selected, playlist)
            )

    def _add_to_new_playlist(self, items: list[LibraryItem]) -> None:
        name = self.create_playlist()
        if name:
            self.add_items_to_playlist(items, name)

    def add_selected_tracks_to_playlist(self) -> None:
        selected = self._selected_items(self.table, self.filtered)
        if selected:
            self.add_items_to_playlist(selected)

    def add_selected_album_to_playlist(self) -> None:
        selected = self.albums.selectedItems()
        if not selected:
            return
        album = str(selected[0].data(Qt.ItemDataRole.UserRole) or "")
        tracks = [item for item in self.filtered if item.album == album]
        self.add_items_to_playlist(tracks)

    def add_open_album_tracks_to_playlist(self) -> None:
        selected = self._selected_items(self.album_tracks, self._open_album_items)
        self.add_items_to_playlist(selected or list(self._open_album_items))

    def _show_artist_context_menu(self, position: QPoint) -> None:
        entry = self.facets.itemAt(position)
        if entry is None:
            return
        artist = entry.text()
        tracks = [
            item for item in self.items
            if artist.casefold() in {value.casefold() for value in split_artists(item.artists)}
        ]
        menu = QMenu(self)
        menu.setTitle(artist)
        self._add_playlist_destinations(
            menu, tracks, label="Add artist tracks to playlist"
        )
        menu.exec(self.facets.viewport().mapToGlobal(position))

    def _album_folders(self, album: str) -> list[Path]:
        folders = {
            Path(item.path).expanduser().parent.resolve()
            for item in self.filtered
            if item.album == album and item.path
        }
        return sorted(folders, key=lambda path: str(path).casefold())

    def _show_album_context_menu(self, position: QPoint) -> None:
        entry = self.albums.itemAt(position)
        if entry is None:
            return
        album = str(entry.data(Qt.ItemDataRole.UserRole) or "")
        self._show_album_folder_context_menu(
            album,
            self._album_folders(album),
            [item for item in self.filtered if item.album == album],
            self.albums.viewport().mapToGlobal(position),
        )

    def _show_open_album_context_menu(self, position: QPoint) -> None:
        if not self._open_album_name:
            return
        folders = sorted(
            {
                Path(item.path).expanduser().parent.resolve()
                for item in self._open_album_items
                if item.path
            },
            key=lambda path: str(path).casefold(),
        )
        self._show_album_folder_context_menu(
            self._open_album_name,
            folders,
            list(self._open_album_items),
            self.album_detail_title.mapToGlobal(position),
        )

    def _show_album_folder_context_menu(
        self,
        album: str,
        folders: list[Path],
        tracks: list[LibraryItem],
        global_position: QPoint,
    ) -> None:
        if not folders and not tracks:
            return
        menu = QMenu(self)
        menu.setTitle(album)
        self._add_playlist_destinations(
            menu, tracks, label="Add album to playlist"
        )
        if folders:
            menu.addSeparator()
        if folders:
            self._add_album_folder_actions(
                menu,
                "Edit album metadata",
                folders,
                self.request_edit_album,
            )
            self._add_album_folder_actions(
                menu,
                "Consolidate / Album enricher",
                folders,
                self.request_album_enricher,
            )
            self._add_album_folder_actions(
                menu,
                "Track reorder",
                folders,
                self.request_track_reorder,
            )
        menu.exec(global_position)

    @staticmethod
    def _add_album_folder_actions(
        menu: QMenu,
        label: str,
        folders: list[Path],
        signal,
    ) -> None:
        if len(folders) == 1:
            action = menu.addAction(label)
            action.setToolTip(str(folders[0]))
            action.triggered.connect(
                lambda _checked=False, folder=str(folders[0]): signal.emit(folder)
            )
            return
        submenu = menu.addMenu(label)
        submenu.setToolTip("This album is indexed in more than one folder")
        for folder in folders:
            action = submenu.addAction(str(folder))
            action.triggered.connect(
                lambda _checked=False, selected=str(folder): signal.emit(selected)
            )

    def _selected_items(
        self, table: QTableWidget, candidates: list[LibraryItem]
    ) -> list[LibraryItem]:
        rows = sorted({index.row() for index in table.selectionModel().selectedRows()})
        paths = [
            str(table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in rows
            if table.item(row, 0) is not None
        ]
        by_path = {item.path: item for item in candidates}
        return [by_path[path] for path in paths if path in by_path]

    def play_selected(self) -> None:
        selected = self._selected_items(self.table, self.filtered)
        if not selected and self.filtered:
            selected = [self.filtered[0]]
        self._replace_queue(selected)

    def enqueue_selected(self) -> None:
        selected = self._selected_items(self.table, self.filtered)
        self._append_to_queue(selected)

    def _append_to_queue(self, selected: list[LibraryItem]) -> int:
        """Append unique tracks without interrupting the item currently playing."""

        if not selected:
            return 0
        if not self._queue_source:
            self._queue_source = list(self.queue)
        existing_paths = {item.path.casefold() for item in self._queue_source}
        selected = [
            item for item in selected if item.path.casefold() not in existing_paths
        ]
        if not selected:
            return 0
        self._queue_source.extend(selected)
        if self._shuffle_enabled and self.queue:
            prefix = self.queue[: self.queue_index + 1]
            tail = self.queue[self.queue_index + 1:] + selected
            random.shuffle(tail)
            self.queue = prefix + tail
        else:
            self.queue.extend(selected)
        if self.queue_index < 0 and self.queue:
            self.queue_index = 0
        self._update_queue_status()
        return len(selected)

    def play_all_matches(self) -> None:
        self._replace_queue(list(self.filtered))

    def shuffle_all_matches(self) -> None:
        self._replace_queue(list(self.filtered), shuffle=True)

    def play_album_tracks(self, mode: str, selected_only: bool) -> None:
        tracks = (
            self._selected_items(self.album_tracks, self._open_album_items)
            if selected_only
            else list(self._open_album_items)
        )
        if not tracks:
            return
        if mode == "queue":
            self._append_to_queue(tracks)
        else:
            self._replace_queue(tracks, shuffle=(mode == "shuffle") or None)

    def _toggle_queue_drawer(self, visible: bool) -> None:
        self.queue_drawer.setVisible(bool(visible))
        arrow = "‹" if visible else "›"
        self.queue_toggle_button.setText(f"Queue ({len(self.queue)}) {arrow}")

    @staticmethod
    def _queue_entry_text(item: LibraryItem, index: int, current: bool) -> str:
        marker = "▶ " if current else ""
        album = f" · {item.album}" if item.album else ""
        return f"{marker}{index + 1:02d}  {item.title}\n{item.artists}{album}"

    def _sync_queue_drawer(self) -> None:
        if not hasattr(self, "queue_list"):
            return
        selected_path = ""
        selected = self.queue_list.currentItem()
        if selected is not None:
            selected_path = str(selected.data(Qt.ItemDataRole.UserRole) or "")
        self.queue_list.blockSignals(True)
        self.queue_list.clear()
        selected_row = -1
        for index, item in enumerate(self.queue):
            current = index == self.queue_index
            entry = QListWidgetItem(self._queue_entry_text(item, index, current))
            entry.setData(Qt.ItemDataRole.UserRole, item.path)
            entry.setToolTip(item.path)
            if current:
                font = entry.font()
                font.setBold(True)
                entry.setFont(font)
                entry.setForeground(QColor("#9db0ff"))
            self.queue_list.addItem(entry)
            if item.path == selected_path:
                selected_row = index
        if selected_row >= 0:
            self.queue_list.setCurrentRow(selected_row)
        self.queue_list.blockSignals(False)
        arrow = "‹" if self.queue_drawer.isVisible() else "›"
        self.queue_toggle_button.setText(f"Queue ({len(self.queue)}) {arrow}")

    def _refresh_queue_list_labels(self) -> None:
        for index, item in enumerate(self.queue):
            entry = self.queue_list.item(index)
            if entry is None:
                continue
            current = index == self.queue_index
            entry.setText(self._queue_entry_text(item, index, current))
            font = entry.font()
            font.setBold(current)
            entry.setFont(font)
            if current:
                entry.setForeground(QColor("#9db0ff"))
            else:
                # Remove the explicit foreground role so Qt uses the list's
                # palette.  An invalid QColor still becomes an item-level
                # brush and can render non-current rows as dark/faded.
                entry.setData(Qt.ItemDataRole.ForegroundRole, None)

    def _queue_reordered(self) -> None:
        current_path = (
            self.queue[self.queue_index].path
            if 0 <= self.queue_index < len(self.queue)
            else ""
        )
        by_path = {item.path: item for item in self.queue}
        reordered = [
            by_path[path]
            for row in range(self.queue_list.count())
            if (path := str(self.queue_list.item(row).data(Qt.ItemDataRole.UserRole) or ""))
            in by_path
        ]
        if len(reordered) != len(self.queue):
            self._sync_queue_drawer()
            return
        self.queue = reordered
        self._queue_source = list(reordered)
        self.queue_index = next(
            (index for index, item in enumerate(self.queue) if item.path == current_path),
            0 if self.queue else -1,
        )
        if self._shuffle_enabled:
            self._shuffle_enabled = False
            self.settings.setValue("library/shuffle", False)
            self._update_playback_mode_buttons()
        self._refresh_queue_list_labels()
        self._update_queue_status(rebuild_drawer=False)

    def _play_queue_entry(self, entry: QListWidgetItem) -> None:
        row = self.queue_list.row(entry)
        if 0 <= row < len(self.queue):
            self.queue_index = row
            self._load_current()

    def _play_selected_queue_entry(self) -> None:
        entry = self.queue_list.currentItem()
        if entry is not None:
            self._play_queue_entry(entry)

    def _remove_selected_queue_entry(self) -> None:
        row = self.queue_list.currentRow()
        if not 0 <= row < len(self.queue):
            return
        removed_current = row == self.queue_index
        self.queue.pop(row)
        self._queue_source = list(self.queue)
        if not self.queue:
            self.clear_playback_queue()
        elif removed_current:
            self.queue_index = min(row, len(self.queue) - 1)
            self._load_current()
        else:
            if row < self.queue_index:
                self.queue_index -= 1
            self._update_queue_status()

    def clear_playback_queue(self) -> None:
        self.exit_video_fullscreen()
        self.player.stop()
        self.player.setSource(QUrl())
        self.queue.clear()
        self._queue_source.clear()
        self.queue_index = -1
        self.now_playing.setText("Nothing playing")
        self._set_now_playing_art(None)
        self.position.setValue(0)
        self.elapsed.setText("0:00 / 0:00")
        self._update_queue_status()

    def _replace_queue(
        self, items: list[LibraryItem], *, shuffle: bool | None = None
    ) -> None:
        unique_items: list[LibraryItem] = []
        seen_paths: set[str] = set()
        for item in items:
            path_key = item.path.casefold()
            if path_key not in seen_paths:
                seen_paths.add(path_key)
                unique_items.append(item)
        if not unique_items:
            return
        if shuffle is not None:
            self._shuffle_enabled = shuffle
            self.settings.setValue("library/shuffle", shuffle)
            self._update_playback_mode_buttons()
        self._queue_source = list(unique_items)
        self.queue = list(unique_items)
        if self._shuffle_enabled:
            # Shuffle once when the queue is created. Previous/next only move
            # the index through this stable order and never choose a new random
            # item, so every track occurs once per queue cycle.
            random.shuffle(self.queue)
        self.queue_index = 0
        self._load_current()

    def _load_current(self) -> None:
        if not 0 <= self.queue_index < len(self.queue):
            return
        item = self.queue[self.queue_index]
        if not Path(item.path).is_file():
            self._set_now_playing_art(None)
            self.now_playing.setText(f"File is no longer available: {item.title}")
            self._remove_unavailable_queue_item()
            return
        # MP3s commonly expose embedded cover art to FFmpeg as an attached video
        # stream.  Keeping QVideoWidget attached for audio needlessly initializes
        # the native video renderer and has caused process-level failures under
        # memory pressure on Windows.  Attach it only for actual video files.
        log_diagnostic(
            "PLAYER",
            f"Loading queue_index={self.queue_index} queue_size={len(self.queue)} "
            f"type={item.media_type} path={item.path!r}",
        )
        self.player.stop()
        if item.media_type != "video":
            self.exit_video_fullscreen()
        self.player.setVideoOutput(self.video if item.media_type == "video" else None)
        self.player.setSource(QUrl.fromLocalFile(item.path))
        self.now_playing.setText(f"{item.title} — {item.artists}  •  {item.album}")
        self._set_now_playing_art(item)
        self.video.setVisible(item.media_type == "video")
        self.fullscreen_button.setEnabled(item.media_type == "video")
        self._update_queue_status()
        self.player.play()

    def _remove_unavailable_queue_item(self) -> None:
        if not 0 <= self.queue_index < len(self.queue):
            return
        unavailable = self.queue.pop(self.queue_index)
        self._queue_source = [
            item for item in self._queue_source if item.path != unavailable.path
        ]
        if not self.queue:
            self.queue_index = -1
            self._set_now_playing_art(None)
            self._update_queue_status()
            return
        self.queue_index %= len(self.queue)
        QTimer.singleShot(0, self._load_current)

    def _set_now_playing_art(self, item: LibraryItem | None) -> None:
        """Show the loaded track's embedded cover, or a deliberately blank tile."""

        self.now_playing_art.clear()
        if item is None:
            return
        icon = self._artwork_cache.get(item.path)
        if icon is None:
            cover = artwork_bytes(item.path)
            source = QPixmap()
            if cover and source.loadFromData(cover):
                thumbnail = source.scaled(
                    104,
                    104,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                icon = QIcon(thumbnail)
            else:
                icon = QIcon()
            self._artwork_cache[item.path] = icon
        if not icon.isNull():
            self.now_playing_art.setPixmap(icon.pixmap(QSize(104, 104)))

    def _update_queue_status(self, *, rebuild_drawer: bool = True) -> None:
        if not self.queue:
            self.queue_status.setText("Queue is empty")
        else:
            self.queue_status.setText(
                f"Track {max(0, self.queue_index) + 1} of {len(self.queue)}"
            )
        if rebuild_drawer:
            self._sync_queue_drawer()

    def play(self) -> None:
        log_diagnostic("PLAYER", "play requested")
        if self.player.source().isEmpty() and self.queue:
            self._load_current()
        else:
            self.player.play()

    def pause(self) -> None:
        log_diagnostic("PLAYER", "pause requested")
        self.player.pause()

    def toggle_play(self) -> None:
        state = self.player.playbackState()
        log_diagnostic("PLAYER", f"toggle requested; state={state.name}")
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        log_diagnostic("PLAYER", "stop requested")
        self.player.stop()

    def previous(self) -> None:
        if self.player.position() >= 5000:
            self.player.setPosition(0)
        elif self.queue and self._repeat_mode == "one":
            self._restart_current()
        elif self.queue_index > 0:
            self.queue_index -= 1
            self._load_current()
        elif self.queue and self._repeat_mode == "all":
            self.queue_index = len(self.queue) - 1
            self._load_current()
        elif self.queue:
            self.stop()

    def next(self) -> None:
        if self.queue and self._repeat_mode == "one":
            self._restart_current()
        elif self.queue_index + 1 < len(self.queue):
            self.queue_index += 1
            self._load_current()
        elif self.queue and self._repeat_mode == "all":
            self.queue_index = 0
            self._load_current()
        elif self.queue:
            self.stop()

    def _restart_current(self) -> None:
        if 0 <= self.queue_index < len(self.queue):
            self.player.setPosition(0)
            self.player.play()

    def _seek(self) -> None:
        self.player.setPosition(self.position.value())
        self._seeking = False

    def player_seek_requested(self, value: int) -> None:
        self.player.setPosition(value)

    def _position_changed(self, value: int) -> None:
        if not self._seeking and not self.position.is_seek_animating():
            self.position.setValue(value)
        self.elapsed.setText(
            f"{self._time(value)} / {self._time(self.player.duration())}"
        )

    def _state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        log_diagnostic("PLAYER", f"playbackStateChanged={state.name}")
        self.play_button.setIcon(
            _transport_icon(
                "pause" if state == QMediaPlayer.PlaybackState.PlayingState else "play"
            )
        )
        self.visualizer_playback_changed.emit(
            state == QMediaPlayer.PlaybackState.PlayingState
        )

    def _update_playback_mode_buttons(self) -> None:
        repeat_labels = {
            "off": "Repeat off",
            "all": "Repeat all",
            "one": "Repeat 1",
        }
        self.repeat_button.setText(repeat_labels[self._repeat_mode])
        self.repeat_button.setToolTip(repeat_labels[self._repeat_mode])
        self.repeat_button.setProperty("active", self._repeat_mode != "off")
        self.shuffle_button.setChecked(self._shuffle_enabled)
        self.shuffle_button.setText(
            "Shuffle on" if self._shuffle_enabled else "Shuffle off"
        )
        self.shuffle_button.setToolTip(self.shuffle_button.text())
        self.repeat_button.style().unpolish(self.repeat_button)
        self.repeat_button.style().polish(self.repeat_button)

    def cycle_repeat_mode(self) -> None:
        modes = ("off", "all", "one")
        self._repeat_mode = modes[
            (modes.index(self._repeat_mode) + 1) % len(modes)
        ]
        self.settings.setValue("library/repeat_mode", self._repeat_mode)
        self._update_playback_mode_buttons()

    def set_shuffle_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._shuffle_enabled:
            self._update_playback_mode_buttons()
            return
        current = (
            self.queue[self.queue_index]
            if 0 <= self.queue_index < len(self.queue)
            else None
        )
        if not self._queue_source:
            self._queue_source = list(self.queue)
        self._shuffle_enabled = enabled
        if current is not None:
            if enabled:
                remaining = [
                    item for item in self.queue if item.path != current.path
                ]
                random.shuffle(remaining)
                self.queue = [current, *remaining]
                self.queue_index = 0
            else:
                self.queue = list(self._queue_source)
                self.queue_index = next(
                    (
                        index
                        for index, item in enumerate(self.queue)
                        if item.path == current.path
                    ),
                    0,
                )
        self.settings.setValue("library/shuffle", enabled)
        self._update_queue_status()
        self._update_playback_mode_buttons()

    def _advance_after_end(self) -> None:
        if not self.queue:
            return
        if self._repeat_mode == "one":
            self._restart_current()
        elif self.queue_index + 1 < len(self.queue):
            self.queue_index += 1
            self._load_current()
        elif self._repeat_mode == "all":
            self.queue_index = 0
            self._load_current()
        else:
            self.stop()
            self._update_queue_status()

    def _media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        log_diagnostic(
            "PLAYER",
            f"mediaStatusChanged={status.name} position={self.player.position()} "
            f"duration={self.player.duration()}",
        )
        if status in {
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        }:
            self._consecutive_playback_errors = 0
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._advance_after_end()

    def _playback_error(
        self, error: QMediaPlayer.Error, error_text: str
    ) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        log_diagnostic(
            "PLAYER",
            f"errorOccurred={error.name} text={error_text!r} "
            f"queue_index={self.queue_index}",
        )
        title = (
            self.queue[self.queue_index].title
            if 0 <= self.queue_index < len(self.queue)
            else "media"
        )
        self.now_playing.setText(f"Could not play {title}: {error_text or error.name}")
        self._consecutive_playback_errors += 1
        if self.queue and self._consecutive_playback_errors < len(self.queue):
            QTimer.singleShot(350, self.next)

    def search_or_download(self) -> None:
        query = self.search.text().strip()
        matches = self.filtered
        if (
            query != self._applied_query
            or self._selected_media_type() != self._applied_media_type
        ):
            matches = filter_library(
                self.items,
                query=query,
                artists=[item.text() for item in self.facets.selectedItems()],
                year_from=self.year_from.value() or None,
                year_to=self.year_to.value() or None,
                media_type=self._selected_media_type(),
            )
        if query and not matches:
            self.request_search_song.emit(query)
        elif query and matches:
            self.table.selectRow(0)

    def shutdown(self) -> None:
        log_diagnostic("PLAYER", "MediaLibraryPage shutdown requested")
        self.exit_video_fullscreen()
        self._shutting_down = True
        self._scan_refresh_pending = False
        self.refresh_timer.stop()
        application = QApplication.instance()
        if application is not None and self._native_media_filter is not None:
            application.removeNativeEventFilter(self._native_media_filter)
            self._native_media_filter = None
        self.player.stop()
        self.player.setSource(QUrl())
        self.visualizer_playback_changed.emit(False)
        if self._spectrum_thread is not None:
            self._spectrum_thread.requestInterruption()
            self._spectrum_thread.quit()
            self._spectrum_thread.wait(2000)
            self._spectrum_thread = None
            self._spectrum_worker = None
        if self._scanner_thread is not None:
            self._scanner_thread.requestInterruption()
            self._scanner_thread.quit()
            self._scanner_thread.wait(2000)
        if self._search_thread is not None:
            self._search_thread.requestInterruption()
            self._search_thread.quit()
            self._search_thread.wait(2000)
        if self._recommendation_thread is not None:
            self._recommendation_thread.requestInterruption()
            self._recommendation_thread.quit()
            self._recommendation_thread.wait(2000)

    @staticmethod
    def _time(milliseconds: int) -> str:
        seconds = max(0, int(milliseconds) // 1000)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return (
            f"{hours}:{minutes:02d}:{seconds:02d}"
            if hours
            else f"{minutes}:{seconds:02d}"
        )


def _suggestion_text(item: LibraryItem) -> str:
    year = f" ({item.year})" if item.year else ""
    return f"{item.title} — {item.artists} • {item.album}{year}"


_MIN_RECOMMENDATION_RESULTS = 1
_MAX_RECOMMENDATION_RESULTS = 20


def _clamp_recommendation_results(value: int) -> int:
    return max(
        _MIN_RECOMMENDATION_RESULTS,
        min(_MAX_RECOMMENDATION_RESULTS, int(value)),
    )


def _requested_result_limit(request: str, fallback: int) -> int:
    """Honor an explicit 'N results/tracks/songs' count without involving the model."""

    patterns = (
        r"\b(?:return|show|give|find|suggest|play)\s+(?:me\s+)?(\d{1,2})"
        r"\s+(?:results?|songs?|tracks?|matches?)\b",
        r"\b(\d{1,2})\s+(?:results?|songs?|tracks?|matches?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, request, flags=re.IGNORECASE)
        if match:
            return _clamp_recommendation_results(int(match.group(1)))
    return _clamp_recommendation_results(fallback)


def _match_rank(item: LibraryItem, query: str) -> tuple[int, int, str]:
    needle = query.strip().casefold()
    title = item.title.casefold()
    combined = f"{item.title} {item.artists} {item.album} {item.year or ''}".casefold()
    if not needle:
        tier = 3
    elif title == needle:
        tier = 0
    elif title.startswith(needle):
        tier = 1
    elif needle in title:
        tier = 2
    else:
        tier = 3
    position = combined.find(needle) if needle else 0
    return tier, position if position >= 0 else len(combined), title
