"""Reusable widgets used by the liquid-glass desktop interface."""

from __future__ import annotations

import math
import re
from pathlib import Path
from urllib.request import Request, urlopen

from PyQt6 import sip
from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QModelIndex,
    QObject,
    QPropertyAnimation,
    QRectF,
    Qt,
    QThread,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSlider,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from youtube_audio_video_downloader.utils.track_timestamp_parser import parse_tracks_to_json
from youtube_audio_video_downloader.utils.time_utils import parse_timestamp_to_seconds
from youtube_audio_video_downloader.utils.artist_name_formatter import (
    format_artist_names,
)
from youtube_audio_video_downloader.services.album_art_finder import find_album_art
from youtube_audio_video_downloader.services.album_art_finder import (
    find_catalog_song_metadata,
)
from youtube_audio_video_downloader.services.video_downloader import YouTubeVideoDownloader
from youtube_audio_video_downloader.services.youtube_search import find_album_jukebox_video
from youtube_audio_video_downloader.services.youtube_track_extractor import extract_tracks_from_youtube
from youtube_audio_video_downloader.services.release_year_finder import find_album_release_year
from youtube_audio_video_downloader.services.individual_track_search import find_individual_album_tracks


def _qt_alive(*objects: object) -> bool:
    """Return false when an async callback's Qt target has been deleted."""
    return all(obj is not None and not sip.isdeleted(obj) for obj in objects)


def _finish_async_button(button: QPushButton, text: str) -> None:
    if _qt_alive(button):
        button.setEnabled(True)
        button.setText(text)


class BlankClickSelectionFilter(QObject):
    """Clear item-view highlights when the user clicks non-interactive space."""

    _INTERACTIVE_WIDGETS = (
        QAbstractButton,
        QAbstractSlider,
        QAbstractSpinBox,
        QCheckBox,
        QComboBox,
        QLineEdit,
        QMenu,
        QPlainTextEdit,
        QTextEdit,
    )

    def __init__(self, root: QWidget) -> None:
        super().__init__(root)
        self.root = root

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            event.type() != QEvent.Type.MouseButtonPress
            or event.button() != Qt.MouseButton.LeftButton
            or not isinstance(watched, QWidget)
            or watched.window() is not self.root.window()
        ):
            return False

        if event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.MetaModifier
            | Qt.KeyboardModifier.ShiftModifier
        ):
            # Modified clicks belong to a native multi-selection gesture.
            # Clearing any view here can emit filter/album change signals and
            # rebuild the clicked table between Ctrl/Cmd/Shift operations.
            return False

        clicked_view = self._item_view_ancestor(watched)
        if clicked_view is not None:
            viewport_position = clicked_view.viewport().mapFrom(
                watched, event.position().toPoint()
            )
            clicked_index = clicked_view.indexAt(viewport_position)
            if clicked_index.isValid():
                self.clear_selections(except_view=clicked_view)
                return False
            self.clear_selections()
            return False

        if self._interactive_ancestor(watched) is None:
            self.clear_selections()
        return False

    def clear_selections(
        self, *, except_view: QAbstractItemView | None = None
    ) -> None:
        for view in self.root.findChildren(QAbstractItemView):
            if view is except_view or bool(view.property("persistentFilterSelection")):
                continue
            selection_model = view.selectionModel()
            if selection_model is not None:
                selection_model.clearSelection()
                view.setCurrentIndex(QModelIndex())

    @staticmethod
    def _item_view_ancestor(widget: QWidget) -> QAbstractItemView | None:
        current: QWidget | None = widget
        while current is not None:
            if isinstance(current, QAbstractItemView):
                return current
            current = current.parentWidget()
        return None

    @classmethod
    def _interactive_ancestor(cls, widget: QWidget) -> QWidget | None:
        current: QWidget | None = widget
        while current is not None:
            if isinstance(current, cls._INTERACTIVE_WIDGETS):
                return current
            current = current.parentWidget()
        return None


class RetryingThread(QThread):
    """QThread with the shared bounded retry behavior used by inline lookups."""

    def __init__(
        self, parent: QWidget | None = None, *, retry_attempts: int = 3
    ) -> None:
        super().__init__(parent)
        self.retry_attempts = max(1, int(retry_attempts))

    def retry_call(self, action):
        for attempt in range(1, self.retry_attempts + 1):
            if self.isInterruptionRequested():
                raise InterruptedError("Operation cancelled")
            try:
                return action()
            except Exception:
                if attempt >= self.retry_attempts:
                    raise
                self.msleep(min(2 ** (attempt - 1), 5) * 1000)
        raise RuntimeError("Retry loop ended unexpectedly")


class VideoQualityScanner(RetryingThread):
    """Fetch video title and available formats without blocking the GUI."""

    scanned = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(
        self, url: str, parent: QWidget | None = None, *, retry_attempts: int = 3
    ) -> None:
        super().__init__(parent, retry_attempts=retry_attempts)
        self.url = url

    def run(self) -> None:
        try:
            result = self.retry_call(
                lambda: YouTubeVideoDownloader(
                    interactive_prompts=False
                ).scan_available_qualities(self.url)
            )
            self.scanned.emit(self.url, result)
        except Exception as exc:  # yt-dlp/network errors are displayed inline.
            self.failed.emit(self.url, str(exc))


class AlbumArtSearcher(RetryingThread):
    """Find square album artwork without blocking the GUI."""

    found = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)

    def __init__(
        self,
        album_name: str,
        parent: QWidget | None = None,
        *,
        retry_attempts: int = 3,
        exclude_url: str = "",
    ) -> None:
        super().__init__(parent, retry_attempts=retry_attempts)
        self.album_name = album_name
        self.exclude_url = exclude_url

    def run(self) -> None:
        try:
            self.found.emit(
                self.album_name,
                self.retry_call(
                    lambda: find_album_art(
                        self.album_name, exclude_url=self.exclude_url
                    )
                ),
            )
        except Exception as exc:  # Network/search errors are displayed inline.
            self.failed.emit(self.album_name, str(exc))


class CoverImageLoader(RetryingThread):
    """Download cover bytes for the preview popup without blocking the GUI."""

    loaded = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(
        self, url: str, parent: QWidget | None = None, *, retry_attempts: int = 3
    ) -> None:
        super().__init__(parent, retry_attempts=retry_attempts)
        self.url = url

    def run(self) -> None:
        try:
            def load() -> bytes:
                request = Request(self.url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(request, timeout=15) as response:  # noqa: S310
                    return response.read(15 * 1024 * 1024 + 1)

            data = self.retry_call(load)
            if len(data) > 15 * 1024 * 1024:
                raise ValueError("The cover image is larger than the 15 MB preview limit.")
            self.loaded.emit(self.url, data)
        except Exception as exc:  # Network and invalid-image errors are shown in the GUI.
            self.failed.emit(self.url, str(exc))


class YouTubeAlbumSearcher(RetryingThread):
    """Find the first full-album/jukebox YouTube result in the background."""

    found = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(
        self, album_name: str, release_year: str = "", parent: QWidget | None = None,
        *, retry_attempts: int = 3, exclude_url: str = ""
    ) -> None:
        super().__init__(parent, retry_attempts=retry_attempts)
        self.album_name = album_name
        self.release_year = release_year
        self.exclude_url = exclude_url

    def run(self) -> None:
        try:
            self.found.emit(
                self.album_name,
                self.retry_call(
                    lambda: find_album_jukebox_video(
                        self.album_name,
                        self.release_year,
                        exclude_url=self.exclude_url,
                    )
                ),
            )
        except Exception as exc:  # yt-dlp/network errors are shown inline.
            self.failed.emit(self.album_name, str(exc))


class YouTubeTrackExtractor(RetryingThread):
    """Fetch and parse timestamped tracks from a YouTube description."""

    extracted = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(
        self,
        url: str,
        album_name: str = "",
        release_year: str = "",
        parent: QWidget | None = None,
        *,
        retry_attempts: int = 3,
        model: str = "",
        use_ai: bool = True,
        mixed_albums: bool = False,
    ) -> None:
        super().__init__(parent, retry_attempts=retry_attempts)
        self.url = url
        self.album_name = album_name
        self.release_year = release_year
        self.model = model
        self.use_ai = use_ai
        self.mixed_albums = mixed_albums

    def run(self) -> None:
        try:
            self.extracted.emit(
                self.url,
                self.retry_call(
                    lambda: extract_tracks_from_youtube(
                        self.url,
                        self.album_name,
                        self.release_year,
                        model=self.model,
                        use_ai=self.use_ai,
                        mixed_albums=self.mixed_albums,
                    )
                ),
            )
        except Exception as exc:
            self.failed.emit(self.url, str(exc))


class ReleaseYearSearcher(RetryingThread):
    """Look up an album release year without blocking the GUI."""

    found = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(
        self,
        album_name: str,
        parent: QWidget | None = None,
        *,
        retry_attempts: int = 3,
        exclude_year: str = "",
    ) -> None:
        super().__init__(parent, retry_attempts=retry_attempts)
        self.album_name = album_name
        self.exclude_year = exclude_year

    def run(self) -> None:
        try:
            self.found.emit(
                self.album_name,
                self.retry_call(
                    lambda: find_album_release_year(
                        self.album_name, exclude_year=self.exclude_year
                    )
                ),
            )
        except Exception as exc:
            self.failed.emit(self.album_name, str(exc))


class TrackMetadataSearcher(RetryingThread):
    """Resolve a jukebox track's album, artists, artwork, and year."""

    found = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(
        self,
        song_title: str,
        artists_hint: str = "",
        parent: QWidget | None = None,
        *,
        retry_attempts: int = 3,
        exclude_album: str = "",
        exclude_artists: str = "",
    ) -> None:
        super().__init__(parent, retry_attempts=retry_attempts)
        self.song_title = song_title
        self.artists_hint = artists_hint
        self.exclude_album = exclude_album
        self.exclude_artists = exclude_artists

    def run(self) -> None:
        try:
            result = self.retry_call(
                lambda: find_catalog_song_metadata(
                    self.song_title,
                    self.artists_hint,
                    exclude_album=self.exclude_album,
                    exclude_artists=self.exclude_artists,
                )
            )
            if not result:
                raise LookupError(
                    f'No different catalog metadata was found for "{self.song_title}".'
                )
            self.found.emit(self.song_title, result)
        except Exception as exc:
            self.failed.emit(self.song_title, str(exc))


class AlbumMetadataAutoFiller(RetryingThread):
    """Find year, cover, and a validated YouTube jukebox in one workflow."""

    completed = pyqtSignal(str, object)

    def __init__(
        self, album_name: str, release_year: str = "", parent: QWidget | None = None,
        *, retry_attempts: int = 3
    ) -> None:
        super().__init__(parent, retry_attempts=retry_attempts)
        self.album_name = album_name
        explicit_match = re.search(r"\b(19\d{2}|20\d{2})\b", album_name)
        # A year deliberately included in the album name is the strongest signal,
        # even if a previous failed lookup left a different value in the year field.
        self.release_year = (explicit_match.group(1) if explicit_match else release_year.strip())
        self.lookup_name = re.sub(
            r"\s*[\[(]?\b(?:19\d{2}|20\d{2})\b[\])]?[\s]*$", "", album_name
        ).strip()

    def run(self) -> None:
        result: dict[str, object] = {"errors": []}
        if self.isInterruptionRequested():
            return
        if self.release_year:
            result["year"] = self.release_year
        else:
            try:
                year_result = self.retry_call(
                    lambda: find_album_release_year(self.lookup_name)
                )
                result.update(year_result)
            except Exception as exc:
                result["errors"].append(f"year: {exc}")  # type: ignore[union-attr]
        try:
            result["album_art"] = self.retry_call(
                lambda: find_album_art(
                    self.lookup_name,
                    release_year=str(result.get("year") or ""),
                )
            )
        except Exception as exc:
            result["errors"].append(f"cover: {exc}")  # type: ignore[union-attr]
        if self.isInterruptionRequested():
            return
        try:
            result["youtube"] = self.retry_call(
                lambda: find_album_jukebox_video(
                    self.lookup_name, str(result.get("year") or "")
                )
            )
        except Exception as exc:
            try:
                result["individual_tracks"] = self.retry_call(
                    lambda: find_individual_album_tracks(
                        self.lookup_name,
                        str(result.get("year") or ""),
                        self.isInterruptionRequested,
                    )
                )
                result["fallback_reason"] = str(exc)
            except Exception as fallback_exc:
                result["errors"].append(  # type: ignore[union-attr]
                    f"YouTube jukebox: {exc}; individual tracks: {fallback_exc}"
                )
        if not self.isInterruptionRequested():
            self.completed.emit(self.album_name, result)


class CoverPreviewDialog(QDialog):
    """Modal popup showing a scaled album cover."""

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Album art preview")
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        image = QLabel()
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setPixmap(
            pixmap.scaled(
                520,
                520,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        dimensions = QLabel(f"{pixmap.width()} × {pixmap.height()} pixels")
        dimensions.setObjectName("mutedLabel")
        dimensions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        close_button = QPushButton("Close")
        close_button.setObjectName("primaryButton")
        close_button.clicked.connect(self.accept)
        layout.addWidget(image)
        layout.addWidget(dimensions)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignCenter)


class LiquidBackground(QWidget):
    """Animated blurred-looking gradient blobs behind the glass shell."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._phase = 0.0
        self._interactive_resize = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        timer = QTimer(self)
        timer.timeout.connect(self._advance)
        timer.start(32)
        self._timer = timer

    def set_interactive_resize(self, active: bool) -> None:
        """Use a cheap static background while the native window is resizing."""

        self._interactive_resize = active
        if active:
            self._timer.stop()
        elif not self._timer.isActive():
            self._timer.start(32)
        self.update()

    @property
    def is_interactive_resize(self) -> bool:
        return self._interactive_resize

    @property
    def is_animating(self) -> bool:
        return self._timer.isActive()

    def _advance(self) -> None:
        self._phase = (self._phase + 0.008) % (math.pi * 2)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        base = QLinearGradient(0, 0, self.width(), self.height())
        base.setColorAt(0.0, QColor(7, 11, 27))
        base.setColorAt(0.46, QColor(13, 17, 38))
        base.setColorAt(1.0, QColor(7, 20, 30))
        painter.fillRect(self.rect(), base)

        if self._interactive_resize:
            return

        blobs = (
            (
                0.18 + math.sin(self._phase) * 0.04,
                0.16 + math.cos(self._phase * 0.8) * 0.03,
                0.34,
                QColor(90, 112, 255, 108),
            ),
            (
                0.78 + math.cos(self._phase * 0.65) * 0.05,
                0.28 + math.sin(self._phase * 0.7) * 0.04,
                0.32,
                QColor(175, 74, 255, 82),
            ),
            (
                0.62 + math.sin(self._phase * 0.55) * 0.04,
                0.84 + math.cos(self._phase * 0.9) * 0.025,
                0.38,
                QColor(33, 218, 191, 70),
            ),
        )

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
        for x_ratio, y_ratio, radius_ratio, color in blobs:
            center_x = self.width() * x_ratio
            center_y = self.height() * y_ratio
            radius = min(self.width(), self.height()) * radius_ratio
            gradient = QRadialGradient(center_x, center_y, radius)
            gradient.setColorAt(0.0, color)
            gradient.setColorAt(0.45, QColor(color.red(), color.green(), color.blue(), color.alpha() // 2))
            gradient.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            painter.setBrush(gradient)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(center_x - radius, center_y - radius, radius * 2, radius * 2))


class GlassCard(QFrame):
    """Simple rounded translucent card."""

    def __init__(self, parent: QWidget | None = None, *, hero: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("heroCard" if hero else "glassCard")


class CollapsibleSection(QFrame):
    """Compact, removable editor section whose body can be collapsed."""

    def __init__(self, title: str, *, removable: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("glassCard")
        self.toggle = QPushButton(f"▾  {title}")
        self.toggle.setObjectName("secondaryButton")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.clicked.connect(self._toggle_body)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setObjectName("dangerButton")
        self.remove_button.setVisible(removable)
        self.status_label = QLabel()
        self.status_label.setObjectName("statusBadge")
        self.status_label.setVisible(False)
        self.body = QWidget()

        header = QHBoxLayout()
        header.addWidget(self.toggle, 1)
        header.addWidget(self.status_label)
        header.addWidget(self.remove_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addLayout(header)
        layout.addWidget(self.body)

    def set_title(self, title: str) -> None:
        arrow = "▾" if self.toggle.isChecked() else "▸"
        self.toggle.setText(f"{arrow}  {title}")

    def set_status(self, status: str) -> None:
        self.status_label.setText(status.upper())
        self.status_label.setVisible(bool(status))

    def set_expanded(self, expanded: bool) -> None:
        """Expand or collapse the section while keeping its toggle in sync."""
        self.toggle.setChecked(expanded)
        self._toggle_body(expanded)

    def _toggle_body(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        text = self.toggle.text().split("  ", 1)[-1]
        self.toggle.setText(f"{'▾' if expanded else '▸'}  {text}")


class TimestampImportDialog(QDialog):
    """Small timestamp-parser dialog used while editing an album."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import tracks from timestamps")
        self.setModal(True)
        self.resize(720, 700)
        self.setWindowOpacity(0.0)
        self._parsed_tracks: list[dict] | None = None

        heading = QLabel("Paste track timestamps")
        heading.setObjectName("sectionTitle")
        helper = QLabel(
            "One track per line, for example: 04:28 - Deva Deva by Arijit Singh, Jonita Gandhi"
        )
        helper.setObjectName("mutedLabel")
        helper.setWordWrap(True)
        self.input_text = QPlainTextEdit()
        self.input_text.setPlaceholderText(
            "00:00 - First Song by Artist One\n04:28 - Second Song by Artist Two"
        )
        self.unknown_artists = QLineEdit("Unknown")
        self.keep_case = QCheckBox("Keep title capitalization exactly as entered")
        preview_label = QLabel("JSON preview")
        preview_label.setObjectName("sectionTitle")
        self.preview_output = QPlainTextEdit()
        self.preview_output.setReadOnly(True)
        self.preview_output.setPlaceholderText("Select Preview JSON to verify the generated tracks.")

        options = QFormLayout()
        options.addRow("Unknown artist", self.unknown_artists)
        options.addRow("Title case", self.keep_case)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.preview_button = buttons.addButton(
            "Preview JSON", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.add_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.add_button.setText("Add parsed tracks")
        self.add_button.setEnabled(False)
        self.preview_button.clicked.connect(self._preview_json)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        self.input_text.textChanged.connect(self._invalidate_preview)
        self.unknown_artists.textChanged.connect(self._invalidate_preview)
        self.keep_case.toggled.connect(self._invalidate_preview)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addWidget(heading)
        layout.addWidget(helper)
        layout.addWidget(self.input_text, 1)
        layout.addLayout(options)
        layout.addWidget(preview_label)
        layout.addWidget(self.preview_output, 1)
        layout.addWidget(buttons)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(180)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.start()
        self.input_text.setFocus()

    def _validate_and_accept(self) -> None:
        if self._parsed_tracks is None:
            QMessageBox.information(self, "Preview required", "Preview the JSON before adding tracks.")
            return
        self.accept()

    def _invalidate_preview(self) -> None:
        self._parsed_tracks = None
        self.add_button.setEnabled(False)
        self.preview_output.clear()

    def _preview_json(self) -> None:
        try:
            output, tracks = self._parse()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Cannot parse timestamps", str(exc))
            return
        self.preview_output.setPlainText(output)
        self._parsed_tracks = tracks
        self.add_button.setEnabled(True)

    def parsed_tracks(self) -> list[dict]:
        if self._parsed_tracks is not None:
            return self._parsed_tracks
        _, tracks = self._parse()
        return tracks

    def _parse(self) -> tuple[str, list[dict]]:
        import json

        raw_text = self.input_text.toPlainText()
        if not raw_text.strip():
            raise ValueError("Paste at least one timestamp line.")
        output = parse_tracks_to_json(
            raw_text,
            end_field="end",
            title_case=not self.keep_case.isChecked(),
            unknown_artists=self.unknown_artists.text().strip() or "Unknown",
        )
        payload = json.loads(output)
        tracks = payload.get("tracks", [])
        if not tracks:
            raise ValueError("No tracks could be parsed from the supplied text.")
        return output, tracks


class JsonBatchEditor(QWidget):
    """Form-based editor that emits the same dictionaries as the CLI JSON files."""

    log_requested = pyqtSignal(str)

    FLAT_FIELDS = {
        "audio": [
            "ytb_link", "mp3_file_path", "title", "album", "artists",
            "album_art", "release_year", "track_number", "start_timestamp",
            "end_timestamp",
        ],
        "video": ["ytb_link", "start_timestamp", "end_timestamp"],
        "album": ["ytb_link", "release_year", "album_art"],
        "jukebox": ["ytb_link"],
    }
    TRACK_FIELDS = {
        "album": ["ytb_link", "start", "end", "artists"],
        "jukebox": ["start", "end", "album", "artists", "album_art", "release_year"],
    }
    _AUTO_DISABLED_TRACK = re.compile(
        r"\b(?:mashup|remix(?:es|ed)?|lo[-‐‑‒–—\s]?fi(?:\s+(?:version|flip))?)\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        kind: str,
        parent: QWidget | None = None,
        *,
        retry_attempts: int = 3,
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        self.retry_attempts = max(1, int(retry_attempts))
        self._audio_mode = "download"
        self.entries: list[dict] = []
        self._scanners: set[VideoQualityScanner] = set()
        self._album_art_searchers: set[AlbumArtSearcher] = set()
        self._cover_loaders: set[CoverImageLoader] = set()
        self._cover_preview_dialogs: set[CoverPreviewDialog] = set()
        self._youtube_searchers: set[YouTubeAlbumSearcher] = set()
        self._track_extractors: set[YouTubeTrackExtractor] = set()
        self._release_year_searchers: set[ReleaseYearSearcher] = set()
        self._track_metadata_searchers: set[TrackMetadataSearcher] = set()
        self._album_auto_fillers: set[AlbumMetadataAutoFiller] = set()
        self.entries_layout = QVBoxLayout()
        self.entries_layout.setContentsMargins(0, 0, 0, 0)
        self.entries_layout.setSpacing(9)
        add_button = QPushButton(f"Add {self._entry_label()}")
        add_button.setObjectName("secondaryButton")
        add_button.clicked.connect(lambda checked=False: self.add_entry())
        import_button = QPushButton("Import JSON")
        import_button.setObjectName("secondaryButton")
        import_button.clicked.connect(self.import_json)
        toolbar = QHBoxLayout()
        toolbar.addWidget(add_button)
        toolbar.addWidget(import_button)
        toolbar.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar)
        layout.addLayout(self.entries_layout)
        self.add_entry()

    def _entry_label(self) -> str:
        return {"audio": "song", "video": "video", "album": "album", "jukebox": "jukebox"}[self.kind]

    def background_threads(self) -> list[QThread]:
        """Return live helper threads owned by this editor."""
        groups = (
            self._scanners,
            self._album_art_searchers,
            self._cover_loaders,
            self._youtube_searchers,
            self._track_extractors,
            self._release_year_searchers,
            self._track_metadata_searchers,
            self._album_auto_fillers,
        )
        return [thread for group in groups for thread in group if thread.isRunning()]

    def set_audio_mode(self, mode: str) -> None:
        """Show only fields relevant to downloading or tagging audio."""
        if self.kind != "audio":
            return
        self._audio_mode = mode
        tagging_existing = mode == "tag-existing"
        for entry in self.entries:
            fields = entry["fields"]
            form = entry.get("form")
            if not isinstance(form, QFormLayout):
                continue
            form.setRowVisible(fields["ytb_link"], not tagging_existing)
            form.setRowVisible(fields["mp3_file_path"], tagging_existing)
            form.setRowVisible(fields["start_timestamp"], not tagging_existing)
            form.setRowVisible(fields["end_timestamp"], not tagging_existing)

    def cancel_background_tasks(self) -> list[QThread]:
        """Request cooperative shutdown and return tasks still winding down."""
        threads = self.background_threads()
        for thread in threads:
            thread.requestInterruption()
        return threads

    @staticmethod
    def _text_field(value: object = "") -> QLineEdit:
        edit = QLineEdit(str(value or ""))
        return edit

    @staticmethod
    def _bool_field(value: object = True) -> QCheckBox:
        box = QCheckBox("Enabled")
        box.setChecked(str(value).lower() not in {"false", "0", "no"})
        return box

    def add_entry(
        self,
        name: str = "",
        values: dict | None = None,
        *,
        auto_extract: bool = False,
    ) -> dict:
        values = values or {}
        if self._incoming_entry_has_content(name, values):
            self._remove_blank_entries()
        if self.kind == "audio" and not values.get("title") and values.get("file_name"):
            # Keep older imported JSON/workspace entries compatible now that
            # file names are generated from the individual metadata fields.
            parts = [part.strip() for part in str(values["file_name"]).split(" - ", 2)]
            if len(parts) == 3 and all(parts):
                values = dict(values)
                values.update(title=parts[0], album=parts[1], artists=parts[2])
        section = CollapsibleSection(f"New {self._entry_label()}")
        if self.kind == "album":
            display_name = values.get("album", name)
        elif self.kind == "video":
            display_name = values.get("file_name", name)
        elif self.kind == "audio":
            display_name = values.get("title", name)
        else:
            display_name = name
        name_edit = self._text_field(display_name)
        if self.kind == "album":
            name_edit.setPlaceholderText("Album name")
        elif self.kind == "video":
            name_edit.setPlaceholderText("Optional; fetched from YouTube when blank")
        else:
            name_edit.setPlaceholderText(f"{self._entry_label().title()} name")
        fields: dict[str, QWidget] = {"__name__": name_edit}
        form = QFormLayout(section.body)
        if self.kind == "album":
            name_label = "Album *"
        elif self.kind == "video":
            name_label = "File name"
        else:
            name_label = "Name *"
        if self.kind == "album":
            form.addRow(name_label, self._album_name_row(name_edit, fields, section))
        elif self.kind == "audio":
            # Audio entries use Title as both their visible identity and JSON key.
            # Keep the internal widget only for shared completion/status behavior.
            pass
        else:
            form.addRow(name_label, name_edit)
        for field in self.FLAT_FIELDS[self.kind]:
            widget = self._text_field(values.get(field, ""))
            fields[field] = widget
            if field in {"start_timestamp", "end_timestamp"}:
                widget.setPlaceholderText(
                    "00:00" if field == "start_timestamp" else "Optional — download to the end"
                )
                widget.setToolTip(
                    "Accepted formats: SS, MM:SS, or HH:MM:SS (decimals allowed)"
                )
            if field == "album_art":
                album_name_edit = name_edit if self.kind == "album" else fields.get("album")
                form.addRow(
                    "Album Art",
                    self._album_art_row(widget, album_name_edit, section),
                )
            elif field == "ytb_link" and self.kind in {"album", "jukebox"}:
                form.addRow(
                    "Ytb Link",
                    self._youtube_search_row(widget, name_edit, fields, section),
                )
            elif field == "release_year":
                album_name_edit = name_edit if self.kind == "album" else fields.get("album")
                form.addRow(
                    "Release Year",
                    self._release_year_row(widget, album_name_edit, section),
                )
            else:
                form.addRow(field.replace("_", " ").title(), widget)
        if self.kind == "video":
            resolution = QComboBox()
            requested = str(values.get("resolution", "") or "")
            if requested:
                resolution.addItem(requested, requested)
            else:
                resolution.addItem("Paste a YouTube link to scan qualities", "")
            resolution.setEnabled(bool(requested))
            fields["resolution"] = resolution
            form.addRow("Quality", resolution)
            link_edit = fields["ytb_link"]
            timer = QTimer(section)
            timer.setSingleShot(True)
            timer.setInterval(700)
            timer.timeout.connect(
                lambda: self._scan_video_link(link_edit, name_edit, resolution, section)
            )
            link_edit.textChanged.connect(lambda: timer.start())
            if self._field_value(link_edit):
                timer.start(100)
        download = self._bool_field(values.get("download", True))
        fields["download"] = download
        form.addRow("Download", download)
        if self.kind in {"album", "jukebox"}:
            numbering = self._bool_field(values.get("track_numbering", True))
            fields["track_numbering"] = numbering
            form.addRow("Track numbering", numbering)
            tracks_widget = QWidget()
            tracks_layout = QVBoxLayout(tracks_widget)
            tracks_layout.setContentsMargins(0, 5, 0, 0)
            add_track = QPushButton("Add track")
            add_track.setObjectName("secondaryButton")
            tracks: list[dict] = []
            add_track.clicked.connect(lambda: self._add_track(tracks_layout, tracks))
            track_actions = QWidget()
            actions_layout = QHBoxLayout(track_actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            actions_layout.addWidget(add_track)
            if self.kind == "album":
                import_tracks = QPushButton("Import timestamps")
                import_tracks.setObjectName("secondaryButton")
                import_tracks.setToolTip("Parse timestamp text and add all tracks")
                import_tracks.clicked.connect(
                    lambda checked=False: self._import_timestamp_tracks(tracks_layout, tracks)
                )
                actions_layout.addWidget(import_tracks)
            extract_tracks = QPushButton("Extract tracks")
            extract_tracks.setObjectName("secondaryButton")
            extract_tracks.setToolTip(
                "Extract timestamps and singer credits from the YouTube description"
            )
            extract_tracks.clicked.connect(
                lambda checked=False: self._extract_youtube_tracks(
                    fields["ytb_link"], name_edit, fields.get("release_year"),
                    tracks_layout, tracks, extract_tracks, section
                )
            )
            actions_layout.addWidget(extract_tracks)
            fields["__tracks_layout__"] = tracks_layout  # type: ignore[assignment]
            fields["__extract_button__"] = extract_tracks
            tracks_layout.addWidget(track_actions)
            fields["__tracks__"] = tracks  # type: ignore[assignment]
            form.addRow("Tracks", tracks_widget)
            for track in values.get("tracks", []):
                if isinstance(track, dict) and track:
                    track_name, track_values = next(iter(track.items()))
                    self._add_track(tracks_layout, tracks, str(track_name), track_values)
        record = {"section": section, "fields": fields, "form": form}
        self.entries.append(record)
        section.remove_button.clicked.connect(lambda: self._remove_entry(record))
        name_edit.textChanged.connect(lambda text: section.set_title(text.strip() or f"New {self._entry_label()}"))
        if self.kind == "audio":
            title_edit = fields["title"]
            if isinstance(title_edit, QLineEdit):
                title_edit.textChanged.connect(name_edit.setText)
                name_edit.setText(title_edit.text())
        section.set_title(str(display_name).strip() or f"New {self._entry_label()}")
        self.entries_layout.addWidget(section)
        if self.kind == "audio":
            tagging_existing = self._audio_mode == "tag-existing"
            form.setRowVisible(fields["ytb_link"], not tagging_existing)
            form.setRowVisible(fields["mp3_file_path"], tagging_existing)
            form.setRowVisible(fields["start_timestamp"], not tagging_existing)
            form.setRowVisible(fields["end_timestamp"], not tagging_existing)
        if auto_extract and self.kind == "jukebox":
            QTimer.singleShot(0, lambda: self._extract_entry_tracks(record))
        return record

    def _incoming_entry_has_content(self, name: str, values: dict) -> bool:
        """Return whether a new record contains user data rather than defaults."""
        if str(name or "").strip():
            return True
        if any(str(values.get(field) or "").strip() for field in self.FLAT_FIELDS[self.kind]):
            return True
        return bool(values.get("tracks"))

    def _entry_is_blank(self, record: dict) -> bool:
        fields = record.get("fields", {})
        if self._field_value(fields.get("__name__")):
            return False
        if any(
            self._field_value(fields.get(field))
            for field in self.FLAT_FIELDS[self.kind]
        ):
            return False
        return not fields.get("__tracks__")

    def _remove_blank_entries(self) -> None:
        """Remove launch/manual placeholders before appending a populated record."""
        for record in list(self.entries):
            if self._entry_is_blank(record):
                self._remove_entry(record)

    def _extract_entry_tracks(self, record: dict) -> None:
        """Start extraction for a fully constructed album/jukebox editor record."""
        if record not in self.entries:
            return
        fields = record["fields"]
        tracks_layout = fields.get("__tracks_layout__")
        tracks = fields.get("__tracks__")
        button = fields.get("__extract_button__")
        if not (
            isinstance(tracks_layout, QVBoxLayout)
            and isinstance(tracks, list)
            and isinstance(button, QPushButton)
        ):
            return
        self._extract_youtube_tracks(
            fields["ytb_link"],
            fields["__name__"],
            fields.get("release_year"),
            tracks_layout,
            tracks,
            button,
            record["section"],
        )

    def _album_name_row(
        self,
        name_edit: QLineEdit,
        fields: dict,
        section: CollapsibleSection,
    ) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        button = QPushButton("Auto fill album")
        button.setObjectName("primaryButton")
        button.setToolTip("Find release year, cover, YouTube jukebox, and tracks")
        button.clicked.connect(
            lambda checked=False: self._auto_fill_album(name_edit, fields, button, section)
        )
        layout.addWidget(name_edit, 1)
        layout.addWidget(button)
        return row

    def _auto_fill_album(
        self,
        name_edit: QLineEdit,
        fields: dict,
        button: QPushButton,
        section: CollapsibleSection,
    ) -> None:
        album_name = name_edit.text().strip()
        if not album_name:
            section.set_status("Album needed")
            return
        button.setEnabled(False)
        button.setText("Auto filling…")
        section.set_status("Finding metadata")
        self.log_requested.emit(
            f'[ALBUM-AUTO-FILL] Searching metadata for "{album_name}".'
        )
        year_edit = fields.get("release_year")
        supplied_year = self._field_value(year_edit) if year_edit is not None else ""
        filler = AlbumMetadataAutoFiller(
            album_name,
            supplied_year,
            self,
            retry_attempts=self.retry_attempts,
        )
        self._album_auto_fillers.add(filler)

        def apply_result(searched_name: str, result: dict) -> None:
            if not _qt_alive(name_edit, button, section):
                return
            if name_edit.text().strip() != searched_name:
                return
            self._apply_album_auto_fill_result(searched_name, result, fields, section, button)

        filler.completed.connect(apply_result)
        filler.finished.connect(lambda: _finish_async_button(button, "Auto fill album"))
        filler.finished.connect(lambda: self._album_auto_fillers.discard(filler))
        filler.finished.connect(filler.deleteLater)
        filler.start()

    def _apply_album_auto_fill_result(
        self,
        searched_name: str,
        result: dict,
        fields: dict,
        section: CollapsibleSection,
        button: QPushButton,
    ) -> None:
        if self.kind != "album":
            return
        try:
            year_edit = fields.get("release_year")
            art_edit = fields.get("album_art")
            link_edit = fields.get("ytb_link")
            changed: list[str] = []
            if (
                isinstance(year_edit, QLineEdit)
                and result.get("year")
                and year_edit.text().strip() != str(result["year"]).strip()
            ):
                year_edit.setText(str(result["year"]))
                year_edit.setToolTip(
                    f"Found on Wikipedia: {result.get('page_title', '')}"
                )
                changed.append("release year")
            if (
                isinstance(art_edit, QLineEdit)
                and result.get("album_art")
                and art_edit.text().strip() != str(result["album_art"]).strip()
            ):
                art_edit.setText(str(result["album_art"]))
                changed.append("album art")
            youtube = result.get("youtube")
            if (
                isinstance(link_edit, QLineEdit)
                and isinstance(youtube, dict)
                and youtube.get("url")
            ):
                youtube_url = str(youtube["url"])
                if link_edit.text().strip() != youtube_url:
                    link_edit.setText(youtube_url)
                    changed.append("YouTube link")
                link_edit.setToolTip(
                    str(youtube.get("title") or "YouTube jukebox found")
                )
                tracks_layout = fields.get("__tracks_layout__")
                tracks = fields.get("__tracks__")
                extract_button = fields.get("__extract_button__")
                if (
                    isinstance(tracks_layout, QVBoxLayout)
                    and isinstance(tracks, list)
                    and isinstance(extract_button, QPushButton)
                ):
                    QTimer.singleShot(
                        0,
                        lambda: self._extract_youtube_tracks(
                            link_edit,
                            fields["__name__"],
                            year_edit,
                            tracks_layout,
                            tracks,
                            extract_button,
                            section,
                        ),
                    )
                    section.set_status("Extracting tracks")
                    self.log_requested.emit(
                        "[ALBUM-AUTO-FILL] Found a YouTube jukebox for "
                        f'"{searched_name}" and started track extraction.'
                    )
                    return
            individual_tracks = result.get("individual_tracks")
            if isinstance(individual_tracks, list) and individual_tracks:
                if isinstance(link_edit, QLineEdit):
                    if link_edit.text().strip():
                        changed.append("cleared YouTube link")
                    link_edit.clear()
                    link_edit.setToolTip(
                        "No suitable audio jukebox was found; using individual track links."
                    )
                tracks_layout = fields.get("__tracks_layout__")
                tracks = fields.get("__tracks__")
                if isinstance(tracks_layout, QVBoxLayout) and isinstance(tracks, list):
                    existing_count = len(tracks)
                    for record in list(tracks):
                        self._remove_track(tracks_layout, tracks, record)
                    for track in individual_tracks:
                        if isinstance(track, dict) and track:
                            track_name, values = next(iter(track.items()))
                            self._add_track(tracks_layout, tracks, str(track_name), values)
                    if len(tracks) != existing_count or tracks:
                        changed.append(f"{len(tracks)} track rows")
                    matched_count = sum(
                        1
                        for track in individual_tracks
                        if isinstance(track, dict)
                        and track
                        and next(iter(track.values())).get("ytb_link")
                    )
                    section.set_status(
                        f"{matched_count}/{len(individual_tracks)} individual tracks matched"
                    )
                    button.setToolTip(
                        "No suitable audio jukebox was found. Wikipedia tracks were matched "
                        "to close-duration YouTube audio/lyrical videos."
                    )
                    self.log_requested.emit(
                        "[ALBUM-AUTO-FILL] Filled "
                        f"{len(individual_tracks)} individual tracks for "
                        f'"{searched_name}" ({matched_count} with links).'
                    )
                    return
            errors = result.get("errors", [])
            if changed:
                section.set_status("Partially filled" if errors else "Metadata found")
                self.log_requested.emit(
                    f'[ALBUM-AUTO-FILL] Updated "{searched_name}": '
                    + ", ".join(changed)
                    + "."
                )
            else:
                section.set_status("No new data")
                self.log_requested.emit(
                    f'[ALBUM-AUTO-FILL] No usable metadata found for "{searched_name}".'
                )
            if errors:
                message = "\n".join(str(error) for error in errors)
                button.setToolTip(message)
                self.log_requested.emit(
                    "[ALBUM-AUTO-FILL] Lookup warnings for "
                    f'"{searched_name}": {message}'
                )
        except Exception as exc:
            section.set_status("Auto fill failed")
            self.log_requested.emit(
                f'[ALBUM-AUTO-FILL] Failed to apply metadata for "{searched_name}": {exc}'
            )

    def _scan_video_link(
        self,
        link_edit: QLineEdit,
        name_edit: QLineEdit,
        resolution: QComboBox,
        section: CollapsibleSection,
    ) -> None:
        url = link_edit.text().strip()
        if not url.lower().startswith(("http://", "https://")):
            return
        previous_quality = str(resolution.currentData() or "")
        resolution.clear()
        resolution.addItem("Scanning available qualities…", "")
        resolution.setEnabled(False)
        section.set_status("Scanning")
        scanner = VideoQualityScanner(
            url, self, retry_attempts=self.retry_attempts
        )
        self._scanners.add(scanner)

        def apply_result(scanned_url: str, result: dict) -> None:
            if link_edit.text().strip() != scanned_url:
                return
            resolution.clear()
            resolution.addItem("Automatic / highest available", "best")
            for label in result.get("qualities", []):
                resolution.addItem(str(label), str(label))
            if result.get("mp3_available"):
                resolution.addItem("MP3 audio", "mp3")
            resolution.setEnabled(resolution.count() > 1)
            previous_index = resolution.findData(previous_quality)
            if previous_index >= 0:
                resolution.setCurrentIndex(previous_index)
            if not name_edit.text().strip() and result.get("title"):
                name_edit.setText(str(result["title"]))
            section.set_status("Ready")

        def apply_error(scanned_url: str, message: str) -> None:
            if link_edit.text().strip() != scanned_url:
                return
            resolution.clear()
            resolution.addItem("Scan failed — edit link to retry", "")
            section.set_status("Scan failed")
            resolution.setToolTip(message)

        scanner.scanned.connect(apply_result)
        scanner.failed.connect(apply_error)
        scanner.finished.connect(lambda: self._scanners.discard(scanner))
        scanner.finished.connect(scanner.deleteLater)
        scanner.start()

    def _add_track(
        self,
        layout: QVBoxLayout,
        tracks: list[dict],
        name: str = "",
        values: dict | None = None,
    ) -> dict:
        values = values if isinstance(values, dict) else {}
        section = CollapsibleSection("New track")
        name_edit = self._text_field(name)
        name_edit.setPlaceholderText("Track title")
        fields: dict[str, QWidget] = {"__name__": name_edit}
        form = QFormLayout(section.body)
        form.addRow("Title *", name_edit)
        for field in self.TRACK_FIELDS[self.kind]:
            value = values.get(field, "")
            if self.kind == "album" and field == "end" and not value:
                value = values.get("stop", "")
            if field == "artists":
                value = format_artist_names(str(value or "")) or "Unknown"
            widget = self._text_field(value)
            fields[field] = widget
            if field == "artists":
                widget.editingFinished.connect(
                    lambda edit=widget: edit.setText(
                        format_artist_names(edit.text()) or "Unknown"
                    )
                )
            if self.kind == "jukebox" and field in {"album", "artists"}:
                form.addRow(
                    field.title(),
                    self._track_metadata_row(
                        widget,
                        name_edit,
                        fields,
                        field,
                        section,
                    ),
                )
            elif field == "album_art":
                form.addRow(
                    "Album Art",
                    self._album_art_row(
                        widget,
                        fields.get("album"),
                        section,
                        fields=fields,
                    ),
                )
            elif field == "release_year":
                form.addRow(
                    "Release Year",
                    self._release_year_row(
                        widget,
                        fields.get("album"),
                        section,
                        fields,
                    ),
                )
            else:
                form.addRow(field.replace("_", " ").title(), widget)
        download = self._bool_field(values.get("download", True))
        if self._AUTO_DISABLED_TRACK.search(name):
            download.setChecked(False)
            download.setToolTip(
                "Automatically disabled because this track is a mashup, remix, or lofi version"
            )
        fields["download"] = download
        form.addRow("Download", download)
        record = {"section": section, "fields": fields}
        tracks.append(record)
        section.remove_button.clicked.connect(lambda: self._remove_track(layout, tracks, record))
        def update_track_title(text: str) -> None:
            section.set_title(text.strip() or "New track")
            if self._AUTO_DISABLED_TRACK.search(text):
                download.setChecked(False)
                download.setToolTip(
                    "Automatically disabled because this track is a mashup, remix, or lofi version"
                )

        name_edit.textChanged.connect(update_track_title)
        section.set_title(name.strip() or "New track")
        layout.insertWidget(max(0, layout.count() - 1), section)
        return record

    def _auto_enrich_track(self, record: dict) -> None:
        """Fill every missing metadata field for one extracted jukebox track."""
        if not _qt_alive(record.get("section")):
            return
        fields = record["fields"]
        album_edit = fields.get("album")
        artists_edit = fields.get("artists")
        if not self._field_value(album_edit) or self._field_value(album_edit).casefold() == "unknown":
            self._find_track_metadata(record, target="all")
            return
        if (
            not self._field_value(artists_edit)
            or self._field_value(artists_edit).casefold() == "unknown"
        ):
            self._find_track_metadata(record, target="all")
            return
        self._auto_enrich_track_year_and_cover(record)

    def _auto_enrich_track_year_and_cover(self, record: dict) -> None:
        """Start any remaining album-derived year and artwork lookups."""
        fields = record["fields"]
        album_edit = fields.get("album")
        if not self._field_value(album_edit):
            return
        section = record["section"]
        year_edit = fields.get("release_year")
        year_button = fields.get("__find_year_button__")
        if (
            isinstance(year_edit, QLineEdit)
            and not year_edit.text().strip()
            and isinstance(year_button, QPushButton)
        ):
            self._find_release_year(album_edit, year_edit, year_button, section)
        art_edit = fields.get("album_art")
        art_button = fields.get("__find_cover_button__")
        if (
            isinstance(art_edit, QLineEdit)
            and not art_edit.text().strip()
            and isinstance(art_button, QPushButton)
        ):
            self._find_album_art(album_edit, art_edit, art_button, section)

    def _track_metadata_row(
        self,
        edit: QLineEdit,
        title_edit: QLineEdit,
        fields: dict[str, QWidget],
        field: str,
        section: CollapsibleSection,
    ) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        button = QPushButton("Find album" if field == "album" else "Find artists")
        button.setObjectName("secondaryButton")
        button.setToolTip(
            f"Re-search song catalogs for {field} using the track title and other metadata"
        )
        button.clicked.connect(
            lambda checked=False, target=field: self._find_track_metadata_for_fields(
                title_edit,
                fields,
                section,
                target,
            )
        )
        fields[f"__find_{field}_button__"] = button
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return row

    def _find_track_metadata_for_fields(
        self,
        title_edit: QLineEdit,
        fields: dict[str, QWidget],
        section: CollapsibleSection,
        target: str,
    ) -> None:
        record = next(
            (
                track
                for entry in self.entries
                for track in entry["fields"].get("__tracks__", [])
                if track.get("fields") is fields
            ),
            None,
        )
        if record is not None:
            self._find_track_metadata(record, target=target)

    def _find_track_metadata(self, record: dict, *, target: str) -> None:
        """Search catalog metadata and update a whole, consistent metadata unit."""
        fields = record["fields"]
        title_edit = fields.get("__name__")
        if not isinstance(title_edit, QLineEdit):
            return
        song_title = title_edit.text().strip()
        if not song_title:
            record["section"].set_status("Track title needed")
            return
        album_edit = fields.get("album")
        artists_edit = fields.get("artists")
        current_album = self._field_value(album_edit)
        current_artists = self._field_value(artists_edit)
        artists_hint = (
            ""
            if current_artists.casefold() in {"", "unknown"}
            else current_artists
        )
        album_button = fields.get("__find_album_button__")
        artists_button = fields.get("__find_artists_button__")
        for button in (album_button, artists_button):
            if isinstance(button, QPushButton):
                button.setEnabled(False)
                button.setText("Searching…")
        record["section"].set_status("Finding track metadata")
        searcher = TrackMetadataSearcher(
            song_title,
            artists_hint if target != "artists" else "",
            self,
            retry_attempts=self.retry_attempts,
            exclude_album=current_album if target == "album" else "",
            exclude_artists=current_artists if target == "artists" else "",
        )
        self._track_metadata_searchers.add(searcher)

        def apply_result(searched_title: str, result: dict) -> None:
            if not _qt_alive(title_edit, record["section"]):
                return
            if title_edit.text().strip() != searched_title:
                return
            updates = {
                "album": result.get("album"),
                "artists": result.get("artists"),
                "release_year": result.get("year"),
                "album_art": result.get("album_art"),
            }
            for field_name, value in updates.items():
                widget = fields.get(field_name)
                if isinstance(widget, QLineEdit) and value:
                    text = (
                        format_artist_names(str(value))
                        if field_name == "artists"
                        else str(value)
                    )
                    widget.setText(text)
            record["section"].set_status("Track metadata found")
            self._auto_enrich_track_year_and_cover(record)

        def apply_error(searched_title: str, message: str) -> None:
            if title_edit.text().strip() == searched_title:
                title_edit.setToolTip(message)
                record["section"].set_status("Track metadata not found")

        def finish() -> None:
            if isinstance(album_button, QPushButton):
                _finish_async_button(album_button, "Find album")
            if isinstance(artists_button, QPushButton):
                _finish_async_button(artists_button, "Find artists")

        searcher.found.connect(apply_result)
        searcher.failed.connect(apply_error)
        searcher.finished.connect(finish)
        searcher.finished.connect(
            lambda: self._track_metadata_searchers.discard(searcher)
        )
        searcher.finished.connect(searcher.deleteLater)
        searcher.start()

    def _extract_youtube_tracks(
        self,
        link_edit: QWidget,
        album_edit: QWidget,
        year_edit: QWidget | None,
        tracks_layout: QVBoxLayout,
        tracks: list[dict],
        button: QPushButton,
        section: CollapsibleSection,
    ) -> None:
        url = self._field_value(link_edit)
        if not url.lower().startswith(("http://", "https://")):
            section.set_status("YouTube link needed")
            return
        button.setEnabled(False)
        button.setText("Extracting…")
        owner = self.window()
        enabled_lookup = getattr(owner, "_ai_enabled_for", None)
        model_lookup = getattr(owner, "_agentic_model", None)
        ai_enabled = bool(enabled_lookup(self.kind)) if callable(enabled_lookup) else False
        agentic_model = str(model_lookup() or "") if callable(model_lookup) else ""
        section.set_status(
            "AI extraction + independent validation"
            if ai_enabled
            else "Internet metadata + deterministic parsing"
        )
        self.log_requested.emit(
            f'[TRACK-EXTRACT] Extracting timestamped tracks for "{self._field_value(album_edit)}" '
            f"from {url}."
        )
        extractor = YouTubeTrackExtractor(
            url,
            self._field_value(album_edit),
            self._field_value(year_edit) if year_edit is not None else "",
            self,
            retry_attempts=self.retry_attempts,
            model=agentic_model if ai_enabled else "",
            use_ai=ai_enabled,
            mixed_albums=self.kind == "jukebox",
        )
        self._track_extractors.add(extractor)

        def apply_result(extracted_url: str, result: tuple[str, list[dict]]) -> None:
            if self._field_value(link_edit) != extracted_url:
                return
            timestamp_text, parsed_tracks = result
            for record in list(tracks):
                self._remove_track(tracks_layout, tracks, record)
            added_tracks = []
            for track in parsed_tracks:
                if isinstance(track, dict) and track:
                    name, values = next(iter(track.items()))
                    added_tracks.append(
                        self._add_track(tracks_layout, tracks, str(name), values)
                    )
            button.setToolTip(timestamp_text)
            section.set_status(f"{len(parsed_tracks)} tracks found")
            self.log_requested.emit(
                f"[TRACK-EXTRACT] Extracted {len(parsed_tracks)} timestamped tracks from {extracted_url}."
            )
            self._warn_nonzero_first_track(parsed_tracks, section)
            if self.kind == "jukebox":
                for track_record in added_tracks:
                    QTimer.singleShot(
                        0,
                        lambda record=track_record: self._auto_enrich_track(record),
                    )

        def apply_error(extracted_url: str, message: str) -> None:
            if self._field_value(link_edit) == extracted_url:
                button.setToolTip(message)
                section.set_status(f"Extraction failed: {message[:160]}")
                self.log_requested.emit(
                    f"[TRACK-EXTRACT] Extraction failed for {extracted_url}: {message}"
                )
                QMessageBox.warning(self, "Track extraction failed", message)

        extractor.extracted.connect(apply_result)
        extractor.failed.connect(apply_error)
        extractor.finished.connect(lambda: button.setEnabled(True))
        extractor.finished.connect(lambda: button.setText("Extract tracks"))
        extractor.finished.connect(lambda: self._track_extractors.discard(extractor))
        extractor.finished.connect(extractor.deleteLater)
        extractor.start()

    def _warn_nonzero_first_track(
        self,
        parsed_tracks: list[dict],
        section: CollapsibleSection,
    ) -> None:
        """Alert when an extracted album does not begin exactly at zero."""
        if not parsed_tracks or not isinstance(parsed_tracks[0], dict) or not parsed_tracks[0]:
            return
        track_name, values = next(iter(parsed_tracks[0].items()))
        if not isinstance(values, dict):
            return
        start = str(values.get("start") or "").strip()
        try:
            starts_after_zero = parse_timestamp_to_seconds(start) > 0
        except ValueError:
            return
        if not starts_after_zero:
            return
        section.set_status("Verify first track")
        QMessageBox.warning(
            self,
            "Verify first track start time",
            f'The first track, "{track_name}", starts at {start} instead of 00:00:00.\n\n'
            "Please verify the source and correct the Start field manually if needed.",
        )

    def _release_year_row(
        self,
        year_edit: QLineEdit,
        album_edit: QWidget | None,
        section: CollapsibleSection,
        fields: dict[str, QWidget] | None = None,
    ) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        year_edit.setPlaceholderText("YYYY")
        button = QPushButton("Find year")
        button.setObjectName("secondaryButton")
        button.setToolTip("Find the album release year from Wikipedia")
        button.clicked.connect(
            lambda checked=False: self._find_release_year(
                album_edit, year_edit, button, section
            )
        )
        if fields is not None:
            fields["__find_year_button__"] = button
        layout.addWidget(year_edit, 1)
        layout.addWidget(button)
        return row

    def _find_release_year(
        self,
        album_edit: QWidget | None,
        year_edit: QLineEdit,
        button: QPushButton,
        section: CollapsibleSection,
    ) -> None:
        album_name = self._field_value(album_edit) if album_edit is not None else ""
        if not album_name:
            year_edit.setToolTip("Enter the album name first.")
            section.set_status("Album needed")
            return
        button.setEnabled(False)
        button.setText("Searching…")
        section.set_status("Checking Wikipedia")
        searcher = ReleaseYearSearcher(
            album_name,
            self,
            retry_attempts=self.retry_attempts,
            exclude_year=year_edit.text().strip(),
        )
        self._release_year_searchers.add(searcher)

        def apply_result(searched_name: str, result: dict) -> None:
            if self._field_value(album_edit) != searched_name:
                return
            year_edit.setText(str(result.get("year") or ""))
            page_title = str(result.get("page_title") or "Wikipedia")
            year_edit.setToolTip(f"Found on Wikipedia: {page_title}")
            section.set_status("Year found")

        def apply_error(searched_name: str, message: str) -> None:
            if self._field_value(album_edit) == searched_name:
                year_edit.setToolTip(message)
                section.set_status("Year not found")

        searcher.found.connect(apply_result)
        searcher.failed.connect(apply_error)
        searcher.finished.connect(lambda: button.setEnabled(True))
        searcher.finished.connect(lambda: button.setText("Find year"))
        searcher.finished.connect(lambda: self._release_year_searchers.discard(searcher))
        searcher.finished.connect(searcher.deleteLater)
        searcher.start()

    def _youtube_search_row(
        self,
        link_edit: QLineEdit,
        name_edit: QLineEdit,
        fields: dict[str, QWidget],
        section: CollapsibleSection,
    ) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        link_edit.setPlaceholderText("Paste a URL or find the first full-album result")
        button = QPushButton("Find on YouTube")
        button.setObjectName("secondaryButton")
        button.setToolTip(
            "Search YouTube for '<album name> full album audio jukebox' and use the first result"
        )
        button.clicked.connect(
            lambda checked=False: self._find_youtube_album(
                name_edit, fields.get("release_year"), link_edit, fields, button, section
            )
        )
        layout.addWidget(link_edit, 1)
        layout.addWidget(button)
        return row

    def _find_youtube_album(
        self,
        name_edit: QLineEdit,
        year_edit: QWidget | None,
        link_edit: QLineEdit,
        fields: dict,
        button: QPushButton,
        section: CollapsibleSection,
    ) -> None:
        album_name = name_edit.text().strip()
        if not album_name:
            link_edit.setToolTip("Enter the album or jukebox name first.")
            section.set_status("Name needed")
            return
        button.setEnabled(False)
        button.setText("Searching…")
        section.set_status("Searching YouTube")
        release_year = self._field_value(year_edit) if year_edit is not None else ""
        searcher = YouTubeAlbumSearcher(
            album_name,
            release_year,
            self,
            retry_attempts=self.retry_attempts,
            exclude_url=link_edit.text().strip(),
        )
        self._youtube_searchers.add(searcher)

        def apply_result(searched_name: str, result: dict) -> None:
            if name_edit.text().strip() != searched_name:
                return
            link_edit.setText(str(result.get("url") or ""))
            details = str(result.get("title") or "YouTube result found")
            if result.get("channel"):
                details += f"\nChannel: {result['channel']}"
            if result.get("views"):
                details += f"\nViews: {int(result['views']):,}"
            link_edit.setToolTip(details)
            section.set_status("Link found")
            tracks_layout = fields.get("__tracks_layout__")
            tracks = fields.get("__tracks__")
            extract_button = fields.get("__extract_button__")
            if (
                isinstance(tracks_layout, QVBoxLayout)
                and isinstance(tracks, list)
                and isinstance(extract_button, QPushButton)
            ):
                QTimer.singleShot(
                    0,
                    lambda: self._extract_youtube_tracks(
                        link_edit,
                        name_edit,
                        fields.get("release_year"),
                        tracks_layout,
                        tracks,
                        extract_button,
                        section,
                    ),
                )

        def apply_error(searched_name: str, message: str) -> None:
            if name_edit.text().strip() == searched_name:
                link_edit.setToolTip(message)
                section.set_status("Search failed")

        searcher.found.connect(apply_result)
        searcher.failed.connect(apply_error)
        searcher.finished.connect(lambda: button.setEnabled(True))
        searcher.finished.connect(lambda: button.setText("Find on YouTube"))
        searcher.finished.connect(lambda: self._youtube_searchers.discard(searcher))
        searcher.finished.connect(searcher.deleteLater)
        searcher.start()

    def _album_art_row(
        self,
        art_edit: QLineEdit,
        album_edit: QWidget | None,
        section: CollapsibleSection,
        *,
        show_find: bool = True,
        fields: dict[str, QWidget] | None = None,
    ) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        art_edit.setPlaceholderText("Paste a URL or find a square cover automatically")
        find_button = QPushButton("Find cover")
        find_button.setObjectName("secondaryButton")
        find_button.setToolTip("Search Google Images for the first square album-art image")
        find_button.clicked.connect(
            lambda checked=False: self._find_album_art(
                album_edit, art_edit, find_button, section
            )
        )
        if fields is not None:
            fields["__find_cover_button__"] = find_button
        preview_button = QPushButton("Preview")
        preview_button.setObjectName("secondaryButton")
        preview_button.setToolTip("Open the current album-art URL in a preview popup")
        preview_button.clicked.connect(
            lambda checked=False: self._preview_album_art(
                art_edit, preview_button, section
            )
        )
        layout.addWidget(art_edit, 1)
        layout.addWidget(preview_button)
        if show_find:
            layout.addWidget(find_button)
        else:
            find_button.deleteLater()
        return row

    def _preview_album_art(
        self,
        art_edit: QLineEdit,
        button: QPushButton,
        section: CollapsibleSection,
    ) -> None:
        url = art_edit.text().strip()
        if not url.lower().startswith(("http://", "https://")):
            art_edit.setToolTip("Enter or find a valid http(s) album-art URL first.")
            section.set_status("Cover URL needed")
            return
        button.setEnabled(False)
        button.setText("Loading…")
        section.set_status("Loading preview")
        self.log_requested.emit(f"[COVER-PREVIEW] Loading album art preview: {url}")
        loader = CoverImageLoader(
            url, self, retry_attempts=self.retry_attempts
        )
        self._cover_loaders.add(loader)

        def show_preview(loaded_url: str, data: bytes) -> None:
            if art_edit.text().strip() != loaded_url:
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                art_edit.setToolTip("The URL did not return a supported image.")
                section.set_status("Invalid image")
                self.log_requested.emit(
                    f"[COVER-PREVIEW] Preview failed for {loaded_url}: unsupported image data."
                )
                return
            section.set_status("Preview ready")
            dialog = CoverPreviewDialog(pixmap, self)
            self._cover_preview_dialogs.add(dialog)
            dialog.finished.connect(lambda _result=0, item=dialog: self._cover_preview_dialogs.discard(item))
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            self.log_requested.emit(f"[COVER-PREVIEW] Opened album art preview: {loaded_url}")

        def show_error(loaded_url: str, message: str) -> None:
            if art_edit.text().strip() == loaded_url:
                art_edit.setToolTip(f"Could not load preview: {message}")
                section.set_status("Preview failed")
                self.log_requested.emit(
                    f"[COVER-PREVIEW] Preview failed for {loaded_url}: {message}"
                )

        loader.loaded.connect(show_preview)
        loader.failed.connect(show_error)
        loader.finished.connect(lambda: button.setEnabled(True))
        loader.finished.connect(lambda: button.setText("Preview"))
        loader.finished.connect(lambda: self._cover_loaders.discard(loader))
        loader.finished.connect(loader.deleteLater)
        loader.start()

    def _find_album_art(
        self,
        album_edit: QWidget | None,
        art_edit: QLineEdit,
        button: QPushButton,
        section: CollapsibleSection,
    ) -> None:
        album_name = self._field_value(album_edit) if album_edit is not None else ""
        if not album_name:
            art_edit.setToolTip("Enter the album name first.")
            section.set_status("Album needed")
            return
        button.setEnabled(False)
        button.setText("Searching…")
        section.set_status("Finding cover")
        searcher = AlbumArtSearcher(
            album_name,
            self,
            retry_attempts=self.retry_attempts,
            exclude_url=art_edit.text().strip(),
        )
        self._album_art_searchers.add(searcher)

        def apply_result(searched_name: str, url: str) -> None:
            if self._field_value(album_edit) == searched_name:
                art_edit.setText(url)
                art_edit.setToolTip("Square cover found with Google Images")
                section.set_status("Cover found")

        def apply_error(searched_name: str, message: str) -> None:
            if self._field_value(album_edit) == searched_name:
                art_edit.setToolTip(message)
                section.set_status("Cover not found")

        searcher.found.connect(apply_result)
        searcher.failed.connect(apply_error)
        searcher.finished.connect(lambda: button.setEnabled(True))
        searcher.finished.connect(lambda: button.setText("Find cover"))
        searcher.finished.connect(lambda: self._album_art_searchers.discard(searcher))
        searcher.finished.connect(searcher.deleteLater)
        searcher.start()

    def _import_timestamp_tracks(self, layout: QVBoxLayout, tracks: list[dict]) -> None:
        dialog = TimestampImportDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        parsed_tracks = dialog.parsed_tracks()
        for track in parsed_tracks:
            if isinstance(track, dict) and track:
                name, values = next(iter(track.items()))
                self._add_track(layout, tracks, str(name), values)
        parent_section = self._section_for_tracks(tracks)
        if parent_section is not None:
            self._warn_nonzero_first_track(parsed_tracks, parent_section)

    def _section_for_tracks(self, tracks: list[dict]) -> CollapsibleSection | None:
        for entry in self.entries:
            if entry["fields"].get("__tracks__") is tracks:
                return entry["section"]
        return None

    def _remove_entry(self, record: dict) -> None:
        self.entries.remove(record)
        record["section"].deleteLater()

    @staticmethod
    def _remove_track(layout: QVBoxLayout, tracks: list[dict], record: dict) -> None:
        tracks.remove(record)
        layout.removeWidget(record["section"])
        record["section"].deleteLater()

    @staticmethod
    def _field_value(widget: QWidget) -> str:
        return widget.text().strip() if isinstance(widget, QLineEdit) else ""

    def data(self) -> dict:
        result = {}
        for entry in self.entries:
            fields = entry["fields"]
            name = (
                self._field_value(fields["title"])
                if self.kind == "audio"
                else self._field_value(fields["__name__"])
            )
            if not name:
                continue
            values = {}
            for field in self.FLAT_FIELDS[self.kind]:
                value = self._field_value(fields[field])
                if value:
                    values[field] = value
            if self.kind == "video":
                values["file_name"] = name
                resolution = fields["resolution"].currentData()
                if resolution:
                    values["resolution"] = str(resolution)
            values["download"] = "true" if fields["download"].isChecked() else "false"
            if self.kind in {"album", "jukebox"}:
                if self.kind == "album":
                    values["album"] = name
                values["track_numbering"] = "true" if fields["track_numbering"].isChecked() else "false"
                values["tracks"] = self._track_data(fields["__tracks__"])
            result[name] = values
        return result

    def load_data(self, payload: dict) -> None:
        """Replace editor contents with a previously serialized workspace."""
        for record in list(self.entries):
            self._remove_entry(record)
        for name, values in payload.items():
            if isinstance(values, dict):
                self.add_entry(str(name), values)
        if not self.entries:
            self.add_entry()

    def set_statuses(self, statuses: dict[str, str]) -> None:
        completed_names: list[str] = []
        for entry in self.entries:
            name = self._field_value(entry["fields"]["__name__"])
            status = str(statuses.get(name, ""))
            entry["section"].set_status(status)
            if status.strip().casefold() == "completed":
                completed_names.append(name)
        if completed_names:
            # Reapply the terminal editor state when restoring a workspace that
            # was saved after completion, including records affected by older
            # versions that persisted only the badge text.
            self.disable_completed(tuple(completed_names))

    def disable_completed(
        self,
        completed_items: list[str] | tuple[str, ...],
        failed_items: list[str] | tuple[str, ...] = (),
    ) -> None:
        """Turn off Download for successfully completed entries and tracks."""
        completed = {str(item) for item in completed_items}
        failed = {str(item) for item in failed_items}
        for entry in self.entries:
            fields = entry["fields"]
            name = self._field_value(fields["__name__"])
            prefix = name + " / "
            entry_completed = name in completed
            entry_failed = name in failed or any(item.startswith(prefix) for item in failed)
            if entry_completed and not entry_failed:
                download = fields.get("download")
                if isinstance(download, QCheckBox):
                    download.setChecked(False)
                entry["section"].set_status("Completed")
                entry["section"].set_expanded(False)
            elif entry_failed:
                # Keep failures visible so their inputs can be corrected and retried.
                entry["section"].set_status("Needs attention")
                entry["section"].set_expanded(True)
            for track in fields.get("__tracks__", []):
                track_name = self._field_value(track["fields"]["__name__"])
                if entry_completed and not entry_failed:
                    track_download = track["fields"].get("download")
                    if isinstance(track_download, QCheckBox):
                        track_download.setChecked(False)
                    track["section"].set_status("Completed")
                    track["section"].set_expanded(False)
                    continue
                matching = []
                for item in completed:
                    if not item.startswith(prefix):
                        continue
                    result_track = re.sub(r"^\d+\.\s*", "", item[len(prefix):])
                    if result_track == track_name or result_track.endswith(track_name):
                        matching.append(item)
                if matching:
                    track_download = track["fields"].get("download")
                    if isinstance(track_download, QCheckBox):
                        track_download.setChecked(False)
                    track["section"].set_status("Completed")
                    track["section"].set_expanded(False)
                track_failed = any(
                    item.startswith(prefix)
                    and re.sub(r"^\d+\.\s*", "", item[len(prefix):]) == track_name
                    for item in failed
                )
                if track_failed:
                    track["section"].set_status("Needs attention")
                    track["section"].set_expanded(True)
            tracks = fields.get("__tracks__", [])
            if tracks and all(
                not track["fields"]["download"].isChecked() for track in tracks
            ) and not entry_failed:
                download = fields.get("download")
                if isinstance(download, QCheckBox):
                    download.setChecked(False)
                entry["section"].set_status("Completed")
                entry["section"].set_expanded(False)

    def _track_data(self, tracks: list[dict]) -> list[dict]:
        result = []
        for track in tracks:
            fields = track["fields"]
            name = self._field_value(fields["__name__"])
            if not name:
                continue
            values = {}
            for field in self.TRACK_FIELDS[self.kind]:
                value = self._field_value(fields[field])
                if field == "artists":
                    value = format_artist_names(value) or "Unknown"
                    if isinstance(fields[field], QLineEdit):
                        fields[field].setText(value)
                if value:
                    values[field] = value
            values["download"] = "true" if fields["download"].isChecked() else "false"
            result.append({name: values})
        return result

    def import_json(self) -> None:
        import json

        selected, _ = QFileDialog.getOpenFileName(self, "Import JSON", str(Path.cwd()), "JSON files (*.json *.jsonc)")
        if not selected:
            return
        payload = json.loads(Path(selected).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("The JSON root must be an object")
        for record in list(self.entries):
            self._remove_entry(record)
        for name, values in payload.items():
            self.add_entry(str(name), values if isinstance(values, dict) else {})


class PathPicker(QWidget):
    """Line edit plus native file/folder picker."""

    def __init__(
        self,
        *,
        placeholder: str = "Select a file",
        mode: str = "file",
        file_filter: str = "All files (*)",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.mode = mode
        self.file_filter = file_filter
        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText(placeholder)
        self.browse_button = QPushButton("Browse")
        self.browse_button.setObjectName("secondaryButton")
        self.browse_button.clicked.connect(self._browse)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.line_edit, 1)
        layout.addWidget(self.browse_button)

    def text(self) -> str:
        return self.line_edit.text().strip()

    def set_text(self, value: str | Path) -> None:
        self.line_edit.setText(str(value))

    def _browse(self) -> None:
        start_path = self.text() or str(Path.cwd())
        if self.mode == "folder":
            selected = QFileDialog.getExistingDirectory(self, "Select folder", start_path)
        elif self.mode == "save":
            selected, _ = QFileDialog.getSaveFileName(
                self,
                "Choose output file",
                start_path,
                self.file_filter,
            )
        else:
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "Select file",
                start_path,
                self.file_filter,
            )
        if selected:
            self.set_text(selected)


class MetricCard(GlassCard):
    """Small dashboard statistic card."""

    def __init__(self, label: str, value: str = "0", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("heroMetric")
        caption = QLabel(label)
        caption.setObjectName("mutedLabel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(2)
        layout.addWidget(self.value_label)
        layout.addWidget(caption)

    def set_value(self, value: str | int) -> None:
        self.value_label.setText(str(value))


class PulseDot(QWidget):
    """Animated activity indicator used while a job is running."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._pulse = 0.0
        self._animation = QPropertyAnimation(self, b"pulse", self)
        self._animation.setDuration(1150)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._animation.setLoopCount(-1)
        self._animation.start()

    def get_pulse(self) -> float:
        return self._pulse

    def set_pulse(self, value: float) -> None:
        self._pulse = value
        self.update()

    pulse = pyqtProperty(float, fget=get_pulse, fset=set_pulse)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        alpha = int(85 + 145 * (0.5 + 0.5 * math.sin(self._pulse * math.pi * 2)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(86, 229, 190, alpha // 3))
        painter.drawEllipse(QRectF(0, 0, 14, 14))
        painter.setBrush(QColor(112, 244, 207, alpha))
        painter.drawEllipse(QRectF(4, 4, 6, 6))
