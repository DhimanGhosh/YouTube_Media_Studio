"""Main liquid-glass PyQt6 window for the downloader suite."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QSettings, QStandardPaths, Qt, QThread, QTimer, QUrl
from PyQt6.QtGui import QCloseEvent, QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from youtube_audio_video_downloader.gui.components.theme import crystal_style
from youtube_audio_video_downloader.gui.runtime.ai_usage import operation_ai_usage
from youtube_audio_video_downloader.gui.media.media_player import MediaLibraryPage
from youtube_audio_video_downloader.gui.media.audio_visualizer import MusicVisualizer
from youtube_audio_video_downloader.gui.runtime.crash_reporter import log_diagnostic
from youtube_audio_video_downloader.config.settings import (
    MAX_PARALLEL_WORKERS,
    machine_parallel_workers,
)
from youtube_audio_video_downloader.config.app_identity import (
    APP_DISPLAY_NAME,
    ORGANIZATION_NAME,
    SETTINGS_APPLICATION_NAME,
)
from youtube_audio_video_downloader.config.app_storage import (
    copy_application_data,
    default_data_directory,
    platform_data_directory,
    save_data_directory_choice,
    settings_file as application_settings_file,
)
from youtube_audio_video_downloader.gui.components.widgets import (
    BlankClickSelectionFilter,
    CollapsibleSection,
    GlassCard,
    JsonBatchEditor,
    LiquidBackground,
    MetricCard,
    PathPicker,
    PulseDot,
)
from youtube_audio_video_downloader.gui.runtime.workers import (
    OperationWorker,
    estimate_eta_seconds,
    format_eta,
    running_operation_text,
)
from youtube_audio_video_downloader.services.media.audio_trimmer import (
    format_timestamp,
    probe_audio_duration,
)
from youtube_audio_video_downloader.services.albums.album_editor import inspect_album_folder
from youtube_audio_video_downloader.services.media.media_metadata import read_media_metadata
from youtube_audio_video_downloader.services.albums.album_folders import resolve_album_folder_successor
from youtube_audio_video_downloader.services.ai.ai_provider import (
    DEFAULT_NVIDIA_MODEL,
    DEFAULT_OLLAMA_MODEL,
    NVIDIA_API_KEY_ENV,
    NVIDIA_MODEL_ENV,
    OLLAMA_MODEL_ENV,
    configure_ai_environment,
    configured_primary_identity,
    configured_primary_model,
)
from youtube_audio_video_downloader.services.ai.agno_provider import (
    configure_agno_environment,
)
from youtube_audio_video_downloader.services.ai.ai_provider_registry import (
    AI_PROVIDER_API_KEY_ENV,
    AI_PROVIDER_ENV,
    AI_PROVIDER_MODEL_ENV,
    PROVIDERS,
    provider_definition,
)
from youtube_audio_video_downloader.services.downloads.song_search import (
    available_ollama_models,
    routed_result_title,
)
from youtube_audio_video_downloader.services.metadata.serpapi_metadata import (
    SERPAPI_API_KEY_ENV,
    configure_serpapi_environment,
)
from youtube_audio_video_downloader.services.albums.track_reorder import list_track_files
from youtube_audio_video_downloader.services.media.video_transformer import (
    VIDEO_ASPECT_OPTIONS,
    VIDEO_CROP_OPTIONS,
    VIDEO_EXTENSIONS,
)
from youtube_audio_video_downloader.version import application_version


class MainWindow(QMainWindow):
    """Primary desktop window."""

    def __init__(
        self,
        *,
        settings: QSettings | None = None,
        data_directory: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_DISPLAY_NAME} {application_version()}")
        self.setMinimumSize(1120, 720)
        self.resize(1380, 860)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.settings = settings or QSettings(
            ORGANIZATION_NAME, SETTINGS_APPLICATION_NAME
        )
        self._data_directory = (
            Path(data_directory).resolve() if data_directory is not None else None
        )
        self._configure_ai_from_settings()
        self._apply_crystalness(
            self._default_value("crystalness", 65), persist=False
        )
        app_data = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        self._metadata_tracker_file = str(
            (self._data_directory or Path(app_data or Path.home() / ".youtube_media_studio"))
            / "album_enrichment_tracker.json"
        )
        self._eta_profiles = self._load_eta_profiles()
        self._active_eta_key = ""
        self._active_eta_phase = "main"
        self._active_operation_params: dict[str, Any] = {}
        self._active_progress_unit = "items"
        self._operation_started_at = 0.0
        self._active_progress_current = 0
        self._active_progress_total = 0
        self._active_thread: QThread | None = None
        self._active_worker: OperationWorker | None = None
        self._parallel_jobs: dict[QThread, tuple[str, OperationWorker]] = {}
        self._parallel_entry_names: dict[QThread, list[str]] = {}
        self._session_jobs = 0
        self._session_completed = 0
        self._session_failed = 0
        self._last_output_folder = ""
        self._history: list[dict[str, Any]] = []
        self._form_runs: dict[str, QPushButton] = {}
        self._nav_buttons: list[QPushButton] = []
        self._active_operation_name = ""
        self._active_entry_names: list[str] = []
        self._album_statuses: dict[str, str] = {}
        self._song_search_results: list[dict[str, Any]] = []
        self._song_search_intent: dict[str, str] = {}
        self._tool_ai_checks: dict[str, QCheckBox] = {}
        self._background: LiquidBackground | None = None
        self._resize_idle_timer: QTimer | None = None

        self._build_window()
        self._resize_idle_timer = QTimer(self)
        self._resize_idle_timer.setSingleShot(True)
        self._resize_idle_timer.setInterval(140)
        self._resize_idle_timer.timeout.connect(
            lambda: self._set_background_interactive(False)
        )
        self._blank_click_selection_filter = BlankClickSelectionFilter(self)
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self._blank_click_selection_filter)
        self._restore_window_state()
        self._restore_workspace_state()
        self._workspace_autosave = QTimer(self)
        self._workspace_autosave.setInterval(5000)
        self._workspace_autosave.timeout.connect(self._save_workspace_state)
        self._workspace_autosave.start()
        self._restore_last_page()
        self._append_log("Application ready. Select a workflow from the sidebar.")

    # ------------------------------------------------------------------ shell
    def _build_window(self) -> None:
        root = QWidget()
        root.setObjectName("rootWindow")
        root_layout = QGridLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)

        background = LiquidBackground(root)
        self._background = background
        root_layout.addWidget(background, 0, 0)

        shell = QWidget(root)
        shell.setObjectName("windowShell")
        self.shell = shell
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(14, 14, 14, 12)
        body_layout.setSpacing(14)

        body_layout.addWidget(self._build_sidebar())
        body_layout.addWidget(self._build_content(), 1)
        shell_layout.addWidget(body, 1)
        shell_layout.addWidget(self._build_activity_bar())

        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(shell, 0, 0)
        self.setCentralWidget(root)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(218)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(11, 14, 11, 14)
        layout.setSpacing(5)

        section = QLabel("WORKSPACES")
        section.setObjectName("mutedLabel")
        section.setContentsMargins(10, 0, 0, 6)
        layout.addWidget(section)

        navigation_scroll = QScrollArea()
        navigation_scroll.setWidgetResizable(True)
        navigation_scroll.setFrameShape(QFrame.Shape.NoFrame)
        navigation_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        navigation_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        navigation_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        navigation = QWidget()
        navigation.setStyleSheet("background: transparent;")
        navigation_layout = QVBoxLayout(navigation)
        navigation_layout.setContentsMargins(0, 0, 0, 0)
        navigation_layout.setSpacing(5)

        items = [
            ("⌂  Dashboard", 0),
            ("⌕  Search Song", 1),
            ("♫  Audio Downloader", 2),
            ("▶  Video Downloader", 3),
            ("▤  Album Splitter", 4),
            ("≋  Jukebox Splitter", 5),
            ("#  Track Reorder", 6),
            ("✎  Edit File", 7),
            ("▤  Edit Album", 8),
            ("▣  Album Consolidator", 9),
            ("⌘  Utilities", 10),
            ("›_  Live Logs", 11),
            ("⚙  Global Settings", 12),
            ("♫  Media Library", 13),
        ]
        for text, index in items:
            button = QPushButton(text)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, page=index: self._set_page(page))
            navigation_layout.addWidget(button)
            self._nav_buttons.append(button)

        navigation_layout.addStretch(1)
        navigation_scroll.setWidget(navigation)
        layout.addWidget(navigation_scroll, 1)

        spectrum_card = GlassCard()
        spectrum_card.setFixedHeight(108)
        spectrum_layout = QVBoxLayout(spectrum_card)
        spectrum_layout.setContentsMargins(8, 5, 8, 5)
        self.music_visualizer = MusicVisualizer()
        spectrum_layout.addWidget(self.music_visualizer)
        layout.addWidget(spectrum_card)
        self.version_label = QLabel(f"Version {application_version()}")
        self.version_label.setObjectName("appVersionLabel")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.version_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.version_label.setToolTip(f"Installed {APP_DISPLAY_NAME} version")
        layout.addWidget(self.version_label)
        return sidebar

    def _build_content(self) -> QWidget:
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_dashboard_page())
        self.pages.addWidget(self._build_search_song_page())
        self.pages.addWidget(self._build_audio_page())
        self.pages.addWidget(self._build_video_page())
        self.pages.addWidget(self._build_album_page())
        self.pages.addWidget(self._build_jukebox_page())
        self.pages.addWidget(self._build_track_reorder_page())
        self.pages.addWidget(self._build_edit_file_page())
        self.pages.addWidget(self._build_edit_album_page())
        self.pages.addWidget(self._build_album_consolidator_page())
        self.pages.addWidget(self._build_utilities_page())
        self.pages.addWidget(self._build_logs_page())
        self.pages.addWidget(self._build_settings_page())
        self.media_library = MediaLibraryPage(self.settings, self)
        self.media_library.ai_identity_resolver = self._active_ai_identity
        self.media_library.request_search_song.connect(self._search_missing_library_song)
        self.media_library.request_edit_file.connect(self._edit_library_file)
        self.media_library.request_edit_video_display.connect(
            self._edit_library_video_display
        )
        self.media_library.request_edit_album.connect(self._edit_library_album)
        self.media_library.request_album_enricher.connect(
            self._open_library_album_enricher
        )
        self.media_library.request_track_reorder.connect(
            self._open_library_track_reorder
        )
        self.media_library.spectrum_ready.connect(self.music_visualizer.set_levels)
        self.media_library.visualizer_playback_changed.connect(
            self.music_visualizer.set_playing
        )
        self.pages.addWidget(self.media_library)
        return self.pages

    def _build_activity_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(76)
        layout = QGridLayout(bar)
        layout.setContentsMargins(22, 7, 15, 9)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(5)

        self.pulse_dot = PulseDot()
        self.pulse_dot.setVisible(False)
        self.activity_label = QLabel("Idle")
        self.activity_label.setObjectName("mutedLabel")
        provider_id = os.environ.get(AI_PROVIDER_ENV, "ollama").strip() or "ollama"
        provider_key = os.environ.get(AI_PROVIDER_API_KEY_ENV, "").strip()
        ollama_model = os.environ.get(OLLAMA_MODEL_ENV, "").strip()
        ready_model = self._agentic_model()
        if not self.settings.value("defaults/ai_enabled", True, type=bool):
            ready_provider = "DISABLED"
            ready_model = "internet/deterministic mode"
        elif provider_id == "ollama" and ollama_model:
            ready_provider = "OLLAMA"
            ready_model = ollama_model
        elif provider_id == "custom":
            ready_provider = provider_definition(provider_id).label.upper()
            ready_model = os.environ.get(AI_PROVIDER_MODEL_ENV, "").strip()
        elif provider_key or (
            provider_id == "nvidia"
            and os.environ.get(NVIDIA_API_KEY_ENV, "").strip()
        ):
            ready_provider = provider_definition(provider_id).label.upper()
            ready_model = (
                os.environ.get(AI_PROVIDER_MODEL_ENV, "").strip()
                or os.environ.get(NVIDIA_MODEL_ENV, "").strip()
            )
        elif ollama_model:
            ready_provider = "OLLAMA FALLBACK"
            ready_model = ollama_model
        else:
            ready_provider = "STATIC FALLBACK"
            ready_model = "no model configured"
        self.ai_status_badge = QLabel(f"AI READY · {ready_provider} · {ready_model}")
        self.ai_status_badge.setObjectName("aiStatusBadge")
        self.ai_status_badge.setProperty("active", True)
        self.ai_status_badge.setToolTip(
            "Shows the selected hosted or local provider, its model, and whether "
            "evidence was verified or left for deterministic review."
        )
        self.activity_progress = QProgressBar()
        self.activity_progress.setRange(0, 100)
        self.activity_progress.setValue(0)
        self.activity_progress.setFormat("")
        self.activity_progress.setTextVisible(True)
        self.cancel_button = QPushButton("Stop")
        self.cancel_button.setObjectName("dangerButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_active_operation)
        self.open_output_button = QPushButton("Open output")
        self.open_output_button.setObjectName("secondaryButton")
        self.open_output_button.setEnabled(False)
        self.open_output_button.setToolTip(
            "Open the folder used by the most recently completed operation"
        )
        self.open_output_button.clicked.connect(self._open_last_output)
        layout.addWidget(self.activity_progress, 0, 0, 1, 3)
        layout.addWidget(self.pulse_dot, 1, 0)
        layout.addWidget(self.activity_label, 1, 1)
        layout.addWidget(self.ai_status_badge, 1, 2, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.open_output_button, 0, 3, 2, 1)
        layout.addWidget(self.cancel_button, 0, 4, 2, 1)
        layout.setColumnStretch(1, 1)
        return bar

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Keep native edge resizing responsive while the window is being dragged."""

        self._set_background_interactive(True)
        super().resizeEvent(event)

    def _set_background_interactive(self, interactive: bool) -> None:
        if self._background is not None:
            self._background.set_interactive_resize(interactive)
        if interactive and self._resize_idle_timer is not None:
            self._resize_idle_timer.start()

    # --------------------------------------------------------------- page base
    def _page_container(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 3, 8, 18)
        layout.setSpacing(14)

        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("mutedLabel")
        subtitle_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        operation = {
            "search song": "search_song",
            "audio downloader": "audio",
            "video downloader": "video",
            "album splitter": "album",
            "jukebox splitter": "jukebox",
            "track number reorder": "track_reorder",
            "edit file": "edit_media",
            "album consolidator": "album_consolidator",
            "utilities": "utilities",
        }.get(title.strip().casefold())
        if operation:
            layout.addWidget(self._ai_task_control(operation))
        scroll.setWidget(content)
        return scroll, layout

    def _ai_task_control(self, operation: str, label: str = "Use AI for this task") -> QWidget:
        """Create a persistent per-section AI switch with an internet-only explanation."""

        control = GlassCard()
        row = QHBoxLayout(control)
        row.setContentsMargins(14, 9, 14, 9)
        checkbox = self._check(label, self._ai_enabled_for(operation))
        checkbox.setToolTip(
            "Off means no NVIDIA/Ollama calls: use internet sources and deterministic rules only."
        )
        checkbox.toggled.connect(
            lambda enabled, name=operation: self._save_tool_ai_setting(name, enabled)
        )
        self._tool_ai_checks[operation] = checkbox
        explanation = QLabel("Off = internet search + deterministic verification only")
        explanation.setObjectName("mutedLabel")
        row.addWidget(checkbox)
        row.addStretch(1)
        row.addWidget(explanation)
        return control

    def _save_tool_ai_setting(self, operation: str, enabled: bool) -> None:
        self.settings.setValue(f"ai/tools/{operation}", bool(enabled))
        self.settings.sync()
        self._show_workspace_ai_policy(operation)

    def _show_workspace_ai_policy(self, operation: str) -> None:
        """Keep the footer synchronized with the current workspace switch."""

        if self._active_thread is not None:
            return
        enabled = self._ai_enabled_for(operation)
        params = {
            "ai_enabled": enabled,
            "agentic_model": self._agentic_model() if enabled else "",
        }
        usage = operation_ai_usage(operation, params)
        self._ai_enabled_current = usage.active
        self._set_ai_status(usage.badge_text, active=usage.active)

    def _ai_enabled_for(self, operation: str) -> bool:
        aliases = {
            "enrich_song": "search_song",
            "album_metadata_enricher": "album_consolidator",
            "duplicate_links": "utilities",
            "format_artists": "utilities",
            "parse_tracks": "utilities",
        }
        name = aliases.get(operation, operation)
        key = f"ai/tools/{name}"
        if self.settings.contains(key):
            return self._setting_bool(key, True)
        return self._setting_bool("defaults/ai_enabled", True)

    @staticmethod
    def _form_card(title: str, description: str = "") -> tuple[GlassCard, QVBoxLayout, QFormLayout]:
        card = GlassCard()
        outer = QVBoxLayout(card)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        outer.addWidget(heading)
        if description:
            helper = QLabel(description)
            helper.setObjectName("mutedLabel")
            helper.setWordWrap(True)
            outer.addWidget(helper)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(11)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(form)
        return card, outer, form

    def _settings_group(
        self,
        title: str,
        description: str,
        key: str,
        *,
        expanded: bool,
    ) -> tuple[CollapsibleSection, QVBoxLayout, QFormLayout]:
        """Create a persistent collapsible group for related global settings."""

        section = CollapsibleSection(title, removable=False)
        section.setProperty("settingsGroup", key)
        body_layout = QVBoxLayout(section.body)
        body_layout.setContentsMargins(8, 4, 8, 8)
        body_layout.setSpacing(10)
        helper = QLabel(description)
        helper.setObjectName("mutedLabel")
        helper.setWordWrap(True)
        body_layout.addWidget(helper)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(11)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        body_layout.addLayout(form)
        section.set_expanded(
            self._setting_bool(f"ui/settings_sections/{key}", expanded)
        )
        section.toggle.toggled.connect(
            lambda value, setting_key=key: self.settings.setValue(
                f"ui/settings_sections/{setting_key}", bool(value)
            )
        )
        return section, body_layout, form

    def _capture_ai_provider_draft(self) -> None:
        provider_id = getattr(self, "_active_ai_provider", "")
        if not provider_id or not hasattr(self, "settings_ai_api_key"):
            return
        self._ai_provider_drafts[provider_id] = {
            "api_key": self.settings_ai_api_key.text().strip(),
            "model": self.settings_ai_model.text().strip(),
            "base_url": self.settings_ai_base_url.text().strip(),
        }

    def _show_ai_provider_draft(self, provider_id: str) -> None:
        definition = provider_definition(provider_id)
        draft = self._ai_provider_drafts.get(
            definition.id,
            {
                "api_key": "",
                "model": definition.default_model,
                "base_url": definition.base_url,
            },
        )
        is_local = definition.id == "ollama"
        self.settings_ai_api_key.setText(draft["api_key"])
        self.settings_ai_api_key.setPlaceholderText(
            "Not required for local Ollama" if is_local else definition.key_placeholder
        )
        self.settings_ai_api_key.setEnabled(not is_local)
        self.settings_ai_model.setText(draft["model"])
        self.settings_ai_model.setPlaceholderText(
            "Uses the Ollama model below" if is_local else definition.default_model
        )
        self.settings_ai_model.setEnabled(not is_local)
        self.settings_ai_base_url.setText(draft["base_url"])
        self.settings_ai_base_url.setEnabled(definition.id == "custom")
        self.settings_ai_base_url.setToolTip(
            "Editable for custom providers. Built-in providers use their official endpoint."
        )

    def _ai_provider_selection_changed(self) -> None:
        self._capture_ai_provider_draft()
        self._active_ai_provider = str(self.settings_ai_provider.currentData())
        self._show_ai_provider_draft(self._active_ai_provider)

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSuffix(suffix)
        return spin

    @staticmethod
    def _double_spin(
        minimum: float,
        maximum: float,
        value: float,
        step: float = 0.1,
        suffix: str = "",
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSingleStep(step)
        spin.setSuffix(suffix)
        spin.setDecimals(2)
        return spin

    @staticmethod
    def _check(text: str, checked: bool = False) -> QCheckBox:
        box = QCheckBox(text)
        box.setChecked(checked)
        return box

    def _run_row(self, operation: str, callback: Callable[[], dict[str, Any]], text: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addStretch(1)
        button = QPushButton(text)
        button.setObjectName("primaryButton")
        button.setMinimumWidth(170)
        button.clicked.connect(lambda: self._start_operation(operation, callback()))
        layout.addWidget(button)
        self._form_runs[operation] = button
        return row

    def _add_header_run_button(
        self,
        outer: QVBoxLayout,
        operation: str,
        callback: Callable[[], dict[str, Any]],
        text: str,
    ) -> None:
        """Place a workflow's primary action beside the card heading."""
        heading = outer.itemAt(0).widget()
        if heading is None:
            raise RuntimeError("Form card heading is missing")
        outer.removeWidget(heading)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)
        header_layout.addWidget(heading)
        header_layout.addStretch(1)
        button = QPushButton(text)
        button.setObjectName("primaryButton")
        button.setMinimumWidth(170)
        button.clicked.connect(
            lambda checked=False: self._start_operation(operation, callback())
        )
        header_layout.addWidget(button)
        outer.insertWidget(0, header)
        self._form_runs[operation] = button

    # --------------------------------------------------------------- dashboard
    def _build_dashboard_page(self) -> QWidget:
        page, layout = self._page_container(
            "Media workspace",
            "A responsive desktop control center for every downloader, splitter, and metadata utility in the project.",
        )

        hero = GlassCard(hero=True)
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(22, 20, 22, 20)
        hero_text = QVBoxLayout()
        hero_title = QLabel("AI-assisted media workflows")
        hero_title.setObjectName("sectionTitle")
        hero_title.setStyleSheet("font-size: 21px;")
        hero_subtitle = QLabel(
            "Search, download, organize, and verify media with one globally "
            "configured AI model while background workers keep the interface responsive."
        )
        hero_subtitle.setObjectName("mutedLabel")
        hero_subtitle.setWordWrap(True)
        self.dashboard_state_badge = QLabel("READY")
        self.dashboard_state_badge.setObjectName("statusBadge")
        self.dashboard_state_badge.setFixedWidth(68)
        self.dashboard_state_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_text.addWidget(hero_title)
        hero_text.addWidget(hero_subtitle)
        hero_text.addSpacing(5)
        hero_text.addWidget(self.dashboard_state_badge, 0, Qt.AlignmentFlag.AlignLeft)
        hero_layout.addLayout(hero_text, 1)
        hero_icon = QLabel("◌")
        hero_icon.setStyleSheet("font-size: 74px; color: rgba(198, 210, 255, 190);")
        hero_layout.addWidget(hero_icon)
        layout.addWidget(hero)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.jobs_metric = MetricCard("Jobs started")
        self.completed_metric = MetricCard("Completed")
        self.failed_metric = MetricCard("Failed")
        self.workers_metric = MetricCard(
            "Default workers",
            str(self._default_value("workers", machine_parallel_workers())),
        )
        for card in (self.jobs_metric, self.completed_metric, self.failed_metric, self.workers_metric):
            metrics.addWidget(card)
        layout.addLayout(metrics)

        actions = GlassCard()
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(18, 16, 18, 18)
        actions_layout.addWidget(self._section_label("Quick launch"))
        buttons = QGridLayout()
        quick_items = [
            ("Open media library", 12),
            ("Search for music", 1),
            ("Download MP3", 2),
            ("Download video", 3),
            ("Split album", 4),
            ("Split jukebox", 5),
            ("Reorder album tracks", 6),
            ("Edit an existing file", 7),
            ("Trim or retag audio", 7),
            ("Consolidate albums", 8),
            ("Open utilities", 9),
        ]
        for index, (text, page_index) in enumerate(quick_items):
            button = QPushButton(text)
            button.setObjectName("secondaryButton")
            button.setMinimumHeight(46)
            button.clicked.connect(lambda checked=False, target=page_index: self._set_page(target))
            buttons.addWidget(button, index // 3, index % 3)
        actions_layout.addLayout(buttons)
        layout.addWidget(actions)

        history_card = GlassCard()
        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(18, 16, 18, 18)
        history_header = QWidget()
        history_header_layout = QHBoxLayout(history_header)
        history_header_layout.setContentsMargins(0, 0, 0, 0)
        history_header_layout.addWidget(self._section_label("Session history"))
        history_header_layout.addStretch(1)
        self.dashboard_clear_button = QPushButton("Clear")
        self.dashboard_clear_button.setObjectName("secondaryButton")
        self.dashboard_clear_button.setToolTip("Clear session counters and history")
        self.dashboard_clear_button.clicked.connect(self._clear_dashboard)
        history_header_layout.addWidget(self.dashboard_clear_button)
        history_layout.addWidget(history_header)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(["Time", "Workflow", "Status", "Items", "Details"])
        self.history_table.setAlternatingRowColors(True)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.history_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.history_table.setMinimumHeight(220)
        history_layout.addWidget(self.history_table)
        layout.addWidget(history_card)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------ song search
    def _build_search_song_page(self) -> QWidget:
        page, layout = self._page_container(
            "Search Song",
            "Describe a song, artist, album, movie, music video, or jukebox in plain language. "
            "The request is understood first, then matched with previewable YouTube results.",
        )
        card, outer, form = self._form_card(
            "What do you want to find?",
            "Example: Find Tumko Dekha Toh by Kumar Sanu and Alka Yagnik from "
            "the movie Hamara Dil Aapke Paas Hai.",
        )
        self.song_search_text = QLineEdit()
        self.song_search_text.setPlaceholderText(
            "Song name, artist, album, movie, or a full jukebox request"
        )
        self.song_search_text.returnPressed.connect(self._start_song_search)
        self.song_search_limit = self._spin(1, 12, 8)
        form.addRow("Request", self.song_search_text)
        form.addRow("Results", self.song_search_limit)
        search_button = QPushButton("Understand and search")
        search_button.setObjectName("primaryButton")
        search_button.clicked.connect(self._start_song_search)
        outer.addWidget(search_button, 0, Qt.AlignmentFlag.AlignRight)
        self._form_runs["search_song"] = search_button
        layout.addWidget(card)

        understood = GlassCard()
        understood_layout = QVBoxLayout(understood)
        understood_layout.setContentsMargins(18, 14, 18, 14)
        understood_layout.addWidget(self._section_label("Understood request"))
        self.song_search_understanding = QLabel(
            "Your interpreted title, artist, collection, and target workflow will appear here."
        )
        self.song_search_understanding.setObjectName("mutedLabel")
        self.song_search_understanding.setWordWrap(True)
        understood_layout.addWidget(self.song_search_understanding)
        layout.addWidget(understood)

        results_card = GlassCard()
        results_layout = QVBoxLayout(results_card)
        results_layout.setContentsMargins(18, 14, 18, 18)
        results_layout.addWidget(self._section_label("YouTube matches"))
        self.song_search_table = QTableWidget(0, 5)
        self.song_search_table.setHorizontalHeaderLabels(
            ["Title", "Channel", "Length", "Views", "Preview"]
        )
        self.song_search_table.verticalHeader().setVisible(False)
        self.song_search_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.song_search_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.song_search_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.song_search_table.verticalHeader().setDefaultSectionSize(44)
        self.song_search_table.verticalHeader().setMinimumSectionSize(44)
        search_header = self.song_search_table.horizontalHeader()
        search_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        search_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for column in (2, 3):
            search_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        search_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.song_search_table.setColumnWidth(4, 126)
        self.song_search_table.setMinimumHeight(250)
        results_layout.addWidget(self.song_search_table)

        route_row = QHBoxLayout()
        route_row.addWidget(QLabel("Send selected result to"))
        self.song_search_route = QComboBox()
        self.song_search_route.addItem("Audio Downloader (MP3 + metadata)", "audio")
        self.song_search_route.addItem("Video Downloader", "video")
        self.song_search_route.addItem("Album Splitter", "album")
        self.song_search_route.addItem("Jukebox Splitter", "jukebox")
        route_row.addWidget(self.song_search_route, 1)
        route_button = QPushButton("Use selected result")
        route_button.setObjectName("primaryButton")
        route_button.clicked.connect(self._route_song_search_result)
        route_row.addWidget(route_button)
        results_layout.addLayout(route_row)
        layout.addWidget(results_card)
        layout.addStretch(1)
        return page

    def _start_song_search(self) -> None:
        self._start_operation(
            "search_song",
            {
                "request_text": self.song_search_text.text(),
                "model": self._agentic_model(),
                "limit": self.song_search_limit.value(),
            },
        )

    def _search_missing_library_song(self, query: str) -> None:
        """Continue an unmatched local search in the Search Song workspace."""
        self.song_search_text.setText(query)
        self._set_page(1)
        self._append_log(f"[LIBRARY] No local match for {query!r}; opened Search Song.")

    def _edit_library_file(self, selected_path: str) -> None:
        """Open a right-clicked library track with metadata already loaded."""

        path = Path(selected_path).expanduser().resolve()
        if not path.is_file():
            QMessageBox.warning(
                self,
                "Media file unavailable",
                f"The selected library file no longer exists:\n{path}",
            )
            return
        self._set_page(7)
        self.edit_file_input.set_text(str(path))
        self._edit_file_load_timer.stop()
        self._load_edit_file(str(path))
        self.edit_file_action.setFocus(Qt.FocusReason.OtherFocusReason)
        self._save_workspace_state()
        self._append_log(f"[LIBRARY] Opened Edit File for: {path}")

    def _edit_library_video_display(self, selected_path: str) -> None:
        """Open a library video directly in the permanent display editor."""

        self._edit_library_file(selected_path)
        if self.edit_file_input.text():
            index = self.edit_file_action.findData("video_display")
            self.edit_file_action.setCurrentIndex(index)

    def _edit_library_album(self, selected_folder: str) -> None:
        """Open a browsed library album in the bulk album metadata editor."""

        folder = Path(selected_folder).expanduser().resolve()
        if not folder.is_dir():
            QMessageBox.warning(
                self,
                "Album folder unavailable",
                f"The selected album folder no longer exists:\n{folder}",
            )
            return
        self._set_page(8)
        self.edit_album_folder.set_text(str(folder))
        self._edit_album_load_timer.stop()
        self._load_edit_album_folder(str(folder))
        self.edit_album_name.setFocus(Qt.FocusReason.OtherFocusReason)
        self._save_workspace_state()
        self._append_log(f"[LIBRARY] Opened Edit Album for: {folder}")

    def _open_library_album_enricher(self, selected_folder: str) -> None:
        """Populate only Album Enricher source; preserve the move destination."""

        folder = Path(selected_folder).expanduser().resolve()
        if not folder.is_dir():
            QMessageBox.warning(
                self,
                "Album folder unavailable",
                f"The selected album folder no longer exists:\n{folder}",
            )
            return
        self._set_page(9)
        self.album_consolidator_source.set_text(str(folder))
        self.album_consolidator_source.line_edit.setFocus(
            Qt.FocusReason.OtherFocusReason
        )
        self._save_workspace_state()
        self._append_log(f"[LIBRARY] Opened Album Enricher for: {folder}")

    def _open_library_track_reorder(self, selected_folder: str) -> None:
        """Open the selected library album in Track Reorder and load its rows."""

        folder = Path(selected_folder).expanduser().resolve()
        if not folder.is_dir():
            QMessageBox.warning(
                self,
                "Album folder unavailable",
                f"The selected album folder no longer exists:\n{folder}",
            )
            return
        self._set_page(6)
        self._track_folder_load_timer.stop()
        previous = self.track_reorder_folder.line_edit.blockSignals(True)
        self.track_reorder_folder.set_text(str(folder))
        self.track_reorder_folder.line_edit.blockSignals(previous)
        self._load_track_reorder_folder()
        self._save_workspace_state()
        self._append_log(f"[LIBRARY] Opened Track Reorder for: {folder}")

    @staticmethod
    def _format_duration(seconds: int) -> str:
        seconds = max(0, int(seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"

    def _render_song_search(self, output_text: str) -> None:
        payload = json.loads(output_text)
        intent = payload.get("intent", {})
        results = payload.get("results", [])
        self._song_search_intent = intent if isinstance(intent, dict) else {}
        self._song_search_results = [item for item in results if isinstance(item, dict)]
        parts = []
        for label, key in (
            ("Title", "title"), ("Artist", "artists"), ("Album", "album"),
            ("Movie", "movie"), ("Year", "release_year"), ("Workflow", "workflow"),
        ):
            value = str(self._song_search_intent.get(key) or "").strip()
            if value:
                parts.append(f"{label}: {value}")
        engine = str(self._song_search_intent.get("engine") or "local")
        explanation = str(self._song_search_intent.get("explanation") or "")
        metadata_warning = str(self._song_search_intent.get("metadata_warnings") or "")
        self.song_search_understanding.setText(
            " · ".join(parts) + f"\nUnderstanding: {engine}. {explanation}"
            + (f"\nMetadata note: {metadata_warning}" if metadata_warning else "")
        )
        workflow = str(self._song_search_intent.get("workflow") or "audio")
        route_index = self.song_search_route.findData(workflow)
        self.song_search_route.setCurrentIndex(max(0, route_index))

        self.song_search_table.setRowCount(len(self._song_search_results))
        for row, result in enumerate(self._song_search_results):
            values = (
                str(result.get("title") or ""),
                str(result.get("channel") or ""),
                self._format_duration(int(result.get("duration") or 0)),
                f"{int(result.get('views') or 0):,}",
            )
            for column, value in enumerate(values):
                self.song_search_table.setItem(row, column, QTableWidgetItem(value))
            preview = QPushButton("Play preview")
            preview.setObjectName("secondaryButton")
            preview.setMinimumSize(116, 36)
            preview.setToolTip("Open this result on YouTube")
            preview.clicked.connect(
                lambda checked=False, url=str(result.get("url") or ""): QDesktopServices.openUrl(QUrl(url))
            )
            self.song_search_table.setCellWidget(row, 4, preview)
        if self._song_search_results:
            self.song_search_table.selectRow(0)

    def _route_song_search_result(self) -> None:
        row = self.song_search_table.currentRow()
        if not 0 <= row < len(self._song_search_results):
            QMessageBox.information(self, "Choose a result", "Select a YouTube result first.")
            return
        result = self._song_search_results[row]
        intent = self._song_search_intent
        workflow = str(self.song_search_route.currentData() or "audio")
        selected_title = str(result.get("title") or "").strip()
        title = str(intent.get("title") or selected_title or "Song").strip()
        routed_title = routed_result_title(selected_title, title, workflow)
        artists = str(intent.get("artists") or "Unknown").strip()
        album = str(intent.get("album") or intent.get("movie") or "Unknown").strip()
        year = str(intent.get("release_year") or "").strip()
        album_art = str(intent.get("album_art") or "").strip()
        url = str(result.get("url") or "")
        if workflow == "audio":
            self._start_operation(
                "enrich_song",
                {
                    "url": url,
                    "title": title,
                    "album": album,
                    "artists": artists,
                    "thumbnail": str(result.get("thumbnail") or ""),
                    "model": self._agentic_model(),
                    "request_text": self.song_search_text.text(),
                },
            )
            return
        elif workflow == "video":
            self.video_input.add_entry(
                routed_title, {"ytb_link": url, "file_name": routed_title}
            )
            target = 3
        elif workflow == "album":
            collection = str(intent.get("album") or intent.get("movie") or title).strip()
            self.album_input.add_entry(
                collection, {
                    "ytb_link": url, "album": collection,
                    "release_year": year, "album_art": album_art,
                }
            )
            target = 4
        else:
            # A jukebox is the selected compilation video, not necessarily the
            # broad album/search phrase understood from the user's request.
            # Preserve its actual YouTube result title as the job identity.
            collection = routed_title
            self.jukebox_input.add_entry(
                collection,
                {"ytb_link": url},
                auto_extract=True,
            )
            target = 5
        self._save_workspace_state()
        self._set_page(target)
        self._append_log(
            f"[ROUTE] Added {selected_title or title} to "
            f"{workflow.replace('_', ' ').title()}."
        )

    def _route_enriched_audio_song(self, output_text: str) -> None:
        """Add background-enriched song metadata to the Audio Downloader."""
        payload = json.loads(output_text)
        title = str(payload.get("title") or "Song")
        self.audio_input.add_entry(
            title,
            {
                "ytb_link": str(payload.get("url") or ""),
                "title": title,
                "album": str(payload.get("album") or "Unknown"),
                "artists": str(payload.get("artists") or "Unknown"),
                "release_year": str(payload.get("release_year") or ""),
                "album_art": str(payload.get("album_art") or ""),
            },
        )
        self._save_workspace_state()
        self._set_page(2)
        self._append_log(f"[ROUTE] Added {title} to Audio Downloader with YouTube metadata.")

    # ------------------------------------------------------------------ audio
    def _build_audio_page(self) -> QWidget:
        page, layout = self._page_container(
            "Audio downloader",
            "Download best available YouTube audio as tagged MP3 files, or retag and rename existing MP3 files.",
        )
        card, outer, form = self._form_card(
            "Audio job",
            "Output names are created automatically as Title - Album - Artists.mp3.",
        )
        self._add_header_run_button(outer, "audio", self._audio_params, "Start audio job")
        self.audio_input = JsonBatchEditor(
            "audio", retry_attempts=self._default_value("retries", 3)
        )
        self.audio_input.log_requested.connect(self._append_log)
        self.audio_mode = QComboBox()
        self.audio_mode.addItem("Download MP3 files", "download")
        self.audio_mode.addItem("Tag existing MP3 files", "tag-existing")
        self.audio_mode.currentIndexChanged.connect(
            lambda: self.audio_input.set_audio_mode(str(self.audio_mode.currentData()))
        )
        self.audio_output = PathPicker(placeholder="Optional MP3 output folder", mode="folder")
        self.audio_overwrite = self._check("Overwrite existing MP3 files")
        form.addRow("Songs", self.audio_input)
        form.addRow("Mode", self.audio_mode)
        form.addRow("Output folder", self.audio_output)
        form.addRow("Existing files", self.audio_overwrite)
        self.audio_mode.currentIndexChanged.connect(self._audio_mode_changed)
        self._audio_mode_changed()
        layout.addWidget(card)
        layout.addWidget(self._feature_card("Included", [
            "Best-source audio extraction through yt-dlp and FFmpeg",
            "Parallel downloads with independent randomized delays",
            "ID3 title, album, artists, year, artwork, and track numbering",
            "Safe existing-file handling and JSON result reports",
            "Workers, delays, retries, MP3 quality, and sample rate are managed in Global Settings",
        ]))
        layout.addStretch(1)
        return page

    def _audio_params(self) -> dict[str, Any]:
        return {
            "input_data": self.audio_input.data(),
            "mode": self.audio_mode.currentData(),
            "output_dir": self.audio_output.text(),
            "overwrite": self.audio_overwrite.isChecked(),
        }

    def _audio_mode_changed(self) -> None:
        self.audio_input.set_audio_mode(str(self.audio_mode.currentData()))

    # ------------------------------------------------------------------ video
    def _build_video_page(self) -> QWidget:
        page, layout = self._page_container(
            "Video downloader",
            "Inspect or download selected video qualities, with optional MP3 extraction from the same JSON batch.",
        )
        card, outer, form = self._form_card("Video job")
        self._add_header_run_button(outer, "video", self._video_params, "Start video job")
        self.video_input = JsonBatchEditor(
            "video", retry_attempts=self._default_value("retries", 3)
        )
        self.video_input.log_requested.connect(self._append_log)
        self.video_mp3_mode = QComboBox()
        self.video_mp3_mode.addItem("MP3 only when selected", "audio-only")
        self.video_mp3_mode.addItem("Selected video and MP3", "both")
        self.video_output = PathPicker(placeholder="Optional video output folder", mode="folder")
        self.video_audio_output = PathPicker(placeholder="Optional MP3 output folder", mode="folder")
        self.video_merge = QComboBox()
        self.video_merge.addItems(["mp4", "mkv", "webm"])
        self.video_report = self._check("Write result report", True)
        self.video_overwrite = self._check("Overwrite existing output")
        form.addRow("Videos", self.video_input)
        form.addRow("When MP3 is selected", self.video_mp3_mode)
        form.addRow("Video output", self.video_output)
        form.addRow("Audio output", self.video_audio_output)
        form.addRow("Merge container", self.video_merge)
        form.addRow("Reporting", self.video_report)
        form.addRow("Existing files", self.video_overwrite)
        layout.addWidget(card)
        layout.addWidget(self._feature_card("Global Settings", [
            "Workers, download delays, retry behavior, MP3 quality, and sample rate are configured once in Global Settings.",
        ]))
        layout.addStretch(1)
        return page

    def _video_params(self) -> dict[str, Any]:
        return {
            "input_data": self.video_input.data(),
            "resolution": "best",
            "mp3_mode": self.video_mp3_mode.currentData(),
            "output_dir": self.video_output.text(),
            "audio_output_dir": self.video_audio_output.text(),
            "merge_format": self.video_merge.currentText(),
            "info_mode": False,
            "write_report": self.video_report.isChecked(),
            "overwrite": self.video_overwrite.isChecked(),
        }

    # ------------------------------------------------------------------ album
    def _build_album_page(self) -> QWidget:
        page, layout = self._page_container(
            "Album splitter",
            "Split a full-album source by timestamps or silence detection, or process per-track links defined under one album.",
        )
        card, outer, form = self._form_card("Album extraction job")
        self._add_header_run_button(outer, "album", self._album_params, "Start album split")
        self.album_input = JsonBatchEditor(
            "album",
            retry_attempts=self._default_value("retries", 3),
        )
        self.album_input.log_requested.connect(self._append_log)
        self.album_output = PathPicker(placeholder="Optional output folder", mode="folder")
        self.album_threshold = self._double_spin(-90.0, -1.0, -35.0, 1.0, " dB")
        self.album_silence = self._double_spin(0.1, 30.0, 1.5, 0.1, " s")
        self.album_track_duration = self._double_spin(1.0, 3600.0, 45.0, 1.0, " s")
        self.album_padding = self._double_spin(0.0, 10.0, 0.25, 0.05, " s")
        self.album_keep_temp = self._check("Keep temporary source audio")
        self.album_report = self._check("Write result report", True)
        self.album_overwrite = self._check("Overwrite existing tracks")
        form.addRow("Albums and tracks", self.album_input)
        form.addRow("Output folder", self.album_output)
        form.addRow("Silence threshold", self.album_threshold)
        form.addRow("Minimum silence", self.album_silence)
        form.addRow("Minimum track", self.album_track_duration)
        form.addRow("Trim padding", self.album_padding)
        form.addRow("Temporary files", self.album_keep_temp)
        form.addRow("Reporting", self.album_report)
        form.addRow("Existing files", self.album_overwrite)
        layout.addWidget(card)
        layout.addWidget(self._feature_card("Global Settings", [
            "Workers, download delays, retries, MP3 bitrate, and sample rate are configured once in Global Settings.",
        ]))
        layout.addStretch(1)
        return page

    def _album_params(self) -> dict[str, Any]:
        return {
            "input_data": self.album_input.data(),
            "output_dir": self.album_output.text(),
            "silence_threshold_db": self.album_threshold.value(),
            "min_silence_duration": self.album_silence.value(),
            "min_track_duration": self.album_track_duration.value(),
            "trim_silence_padding": self.album_padding.value(),
            "keep_temp": self.album_keep_temp.isChecked(),
            "write_report": self.album_report.isChecked(),
            "overwrite": self.album_overwrite.isChecked(),
        }

    # ---------------------------------------------------------------- jukebox
    def _build_jukebox_page(self) -> QWidget:
        page, layout = self._page_container(
            "Jukebox splitter",
            "Extract manually timed songs from one or more compilation videos with per-song metadata and artwork.",
        )
        card, outer, form = self._form_card("Jukebox extraction job")
        self._add_header_run_button(
            outer, "jukebox", self._jukebox_params, "Start jukebox split"
        )
        self.jukebox_input = JsonBatchEditor(
            "jukebox",
            retry_attempts=self._default_value("retries", 3),
        )
        self.jukebox_input.log_requested.connect(self._append_log)
        self.jukebox_output = PathPicker(placeholder="Optional output folder", mode="folder")
        self.jukebox_keep_temp = self._check("Keep temporary source audio")
        self.jukebox_report = self._check("Write result report", True)
        self.jukebox_overwrite = self._check("Overwrite existing songs")
        form.addRow("Jukeboxes and tracks", self.jukebox_input)
        form.addRow("Output folder", self.jukebox_output)
        form.addRow("Temporary files", self.jukebox_keep_temp)
        form.addRow("Reporting", self.jukebox_report)
        form.addRow("Existing files", self.jukebox_overwrite)
        layout.addWidget(card)
        layout.addWidget(self._feature_card("Global Settings", [
            "Workers, download delays, retries, MP3 bitrate, and sample rate are configured once in Global Settings.",
        ]))
        layout.addStretch(1)
        return page

    def _jukebox_params(self) -> dict[str, Any]:
        return {
            "input_data": self.jukebox_input.data(),
            "output_dir": self.jukebox_output.text(),
            "keep_temp": self.jukebox_keep_temp.isChecked(),
            "write_report": self.jukebox_report.isChecked(),
            "overwrite": self.jukebox_overwrite.isChecked(),
        }

    # ----------------------------------------------------------- track reorder
    def _build_track_reorder_page(self) -> QWidget:
        page, layout = self._page_container(
            "Track number reorder",
            "Choose an album folder, drag songs into the desired order, then update only their track-number tags.",
        )
        card, outer, form = self._form_card(
            "Reset album track order",
            "Files keep the same names and media data. Title, artist, album, year, artwork, and all other metadata remain unchanged.",
        )
        self.track_reorder_folder = PathPicker(
            placeholder="Select a folder containing the album songs", mode="folder"
        )
        self.track_reorder_list = QListWidget()
        self.track_reorder_list.setMinimumHeight(330)
        self.track_reorder_list.setAlternatingRowColors(True)
        self.track_reorder_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.track_reorder_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        self.track_reorder_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.track_reorder_list.setDropIndicatorShown(True)
        self.track_reorder_list.model().rowsMoved.connect(
            self._refresh_track_reorder_labels
        )
        self._track_folder_load_timer = QTimer(self)
        self._track_folder_load_timer.setSingleShot(True)
        self._track_folder_load_timer.setInterval(180)
        self._track_folder_load_timer.timeout.connect(self._load_track_reorder_folder)
        self.track_reorder_folder.line_edit.textChanged.connect(
            self._queue_track_folder_load
        )
        form.addRow("Album folder", self.track_reorder_folder)
        form.addRow("Drag to reorder", self.track_reorder_list)

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 4, 0, 0)
        reload_button = QPushButton("Reload folder")
        reload_button.setObjectName("secondaryButton")
        reload_button.clicked.connect(
            lambda checked=False: self._load_track_reorder_folder()
        )
        clear_button = QPushButton("Clear")
        clear_button.setObjectName("secondaryButton")
        clear_button.setToolTip("Clear the selected album folder and track list")
        clear_button.clicked.connect(
            lambda checked=False: self._clear_track_reorder()
        )
        run_button = QPushButton("Reorder track numbers")
        run_button.setObjectName("primaryButton")
        run_button.setMinimumWidth(190)
        run_button.clicked.connect(
            lambda checked=False: self._start_operation(
                "track_reorder", self._track_reorder_params()
            )
        )
        actions_layout.addWidget(reload_button)
        actions_layout.addWidget(clear_button)
        actions_layout.addStretch(1)
        actions_layout.addWidget(run_button)
        outer.addWidget(actions)
        self._form_runs["track_reorder"] = run_button
        layout.addWidget(card)
        layout.addWidget(self._feature_card("Safety", [
            "Only the track-number tag is changed to 1, 2, 3, and so on",
            "An existing track total such as /8 is preserved",
            "Songs are not renamed, moved, decoded, or re-encoded",
        ]))
        layout.addStretch(1)
        return page

    def _queue_track_folder_load(self, value: str) -> None:
        self._track_folder_load_timer.start()

    def _clear_track_reorder(self) -> None:
        """Clear the reorder workspace without touching media files."""

        self._track_folder_load_timer.stop()
        previous = self.track_reorder_folder.line_edit.blockSignals(True)
        self.track_reorder_folder.set_text("")
        self.track_reorder_folder.line_edit.blockSignals(previous)
        self.track_reorder_list.clear()
        self.settings.remove("workspace/track_reorder_folder")
        self.settings.sync()

    def _load_track_reorder_folder(self, requested_path: str | None = None) -> None:
        folder_text = self.track_reorder_folder.text()
        if requested_path is not None and requested_path != folder_text:
            return
        self.track_reorder_list.clear()
        if not folder_text:
            return
        requested_folder = Path(folder_text).expanduser()
        resolved_folder = resolve_album_folder_successor(requested_folder)
        if resolved_folder != requested_folder:
            previous = self.track_reorder_folder.line_edit.blockSignals(True)
            self.track_reorder_folder.set_text(str(resolved_folder))
            self.track_reorder_folder.line_edit.blockSignals(previous)
            self.settings.setValue("workspace/track_reorder_folder", str(resolved_folder))
            self.settings.sync()
            self._append_log(
                f"[RESTORED] Album folder path updated after rename: {resolved_folder}"
            )
            folder_text = str(resolved_folder)
        try:
            tracks = list_track_files(Path(folder_text))
        except (OSError, ValueError) as exc:
            self._append_log(f"[WARNING] Could not load album folder: {exc}")
            return
        for track in tracks:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, str(track.path))
            old_number = "none" if track.track_number is None else str(track.track_number)
            item.setToolTip(f"Current track number: {old_number}\n{track.path}")
            self.track_reorder_list.addItem(item)
        self._refresh_track_reorder_labels()

    def _refresh_track_reorder_labels(self, *args: object) -> None:
        width = max(2, len(str(self.track_reorder_list.count())))
        for row in range(self.track_reorder_list.count()):
            item = self.track_reorder_list.item(row)
            path = Path(str(item.data(Qt.ItemDataRole.UserRole)))
            item.setText(f"{row + 1:0{width}d}   {path.name}")

    def _track_reorder_params(self) -> dict[str, Any]:
        return {
            "paths": [
                str(self.track_reorder_list.item(row).data(Qt.ItemDataRole.UserRole))
                for row in range(self.track_reorder_list.count())
            ]
        }

    # --------------------------------------------------------------- edit file
    def _build_edit_file_page(self) -> QWidget:
        page, layout = self._page_container(
            "Edit File",
            "Load an existing song once, then update its metadata, trim it losslessly, "
            "or replace its media from YouTube in one workflow.",
        )
        media_filter = (
            "Media files (*.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wav *.aiff "
            "*.mp4 *.mkv *.webm *.mov *.avi *.m4v *.ts);;All files (*)"
        )
        card, outer, form = self._form_card(
            "File operation",
            "Metadata-only edits always replace the selected file atomically. Trim and "
            "redownload actions can replace it or create a copy.",
        )
        self.edit_file_action = QComboBox()
        self.edit_file_action.addItem("Update metadata only", "metadata")
        self.edit_file_action.addItem("Trim the selected local file", "trim")
        self.edit_file_action.addItem("Replace media from YouTube", "redownload")
        self.edit_file_action.addItem(
            "Apply video crop / aspect permanently", "video_display"
        )
        self.edit_file_input = PathPicker(
            placeholder="Select the existing media file", file_filter=media_filter
        )
        self.edit_file_duration = QLabel("Select a file to load its duration and metadata.")
        self.edit_file_duration.setObjectName("mutedLabel")
        self.edit_file_url = QLineEdit()
        self.edit_file_url.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.edit_file_content = QComboBox()
        self.edit_file_content.addItem("Automatic (match source file)", "auto")
        self.edit_file_content.addItem("Audio only", "audio")
        self.edit_file_content.addItem("Video only", "video")
        self.edit_file_content.addItem("Audio and video", "both")
        self.edit_file_download_start = QLineEdit("00:00")
        self.edit_file_download_start.setPlaceholderText("00:00")
        self.edit_file_download_end = QLineEdit()
        self.edit_file_download_end.setPlaceholderText(
            "Optional — download to the end"
        )
        self.edit_file_start = QLineEdit("00:00")
        self.edit_file_end = QLineEdit()
        self.edit_file_end.setPlaceholderText("Loaded local file duration")
        self.edit_file_crop_ratio = QComboBox()
        self.edit_file_crop_ratio.addItems(VIDEO_CROP_OPTIONS)
        self.edit_file_aspect_ratio = QComboBox()
        self.edit_file_aspect_ratio.addItems(VIDEO_ASPECT_OPTIONS)
        self.edit_file_mode = QComboBox()
        self.edit_file_mode.addItem("Save an edited copy", False)
        self.edit_file_mode.addItem("Replace the existing source file", True)
        self.edit_file_output = PathPicker(
            placeholder="Not applicable to metadata-only editing",
            mode="save",
            file_filter=media_filter,
        )
        self._edit_file_suggested_output = ""
        self._edit_file_loaded_duration = ""
        form.addRow("Edit action", self.edit_file_action)
        form.addRow("Existing media file", self.edit_file_input)
        form.addRow("Media duration", self.edit_file_duration)
        form.addRow("YouTube link", self.edit_file_url)
        form.addRow("Download content", self.edit_file_content)
        form.addRow("Download start", self.edit_file_download_start)
        form.addRow("Download end", self.edit_file_download_end)
        form.addRow("Permanent crop ratio", self.edit_file_crop_ratio)
        form.addRow("Permanent aspect ratio", self.edit_file_aspect_ratio)
        form.addRow("Save behavior", self.edit_file_mode)
        form.addRow("Copy destination", self.edit_file_output)
        self.edit_file_action.currentIndexChanged.connect(self._edit_file_action_changed)
        self.edit_file_mode.currentIndexChanged.connect(self._edit_file_action_changed)
        self._edit_file_load_timer = QTimer(self)
        self._edit_file_load_timer.setSingleShot(True)
        self._edit_file_load_timer.setInterval(180)
        self._edit_file_load_timer.timeout.connect(
            lambda: self._load_edit_file(self.edit_file_input.text())
        )
        self.edit_file_input.line_edit.textChanged.connect(self._queue_edit_file_load)

        metadata_card, metadata_outer, metadata_form = self._form_card(
            "Song metadata",
            "Existing tags are loaded from the selected file. Empty fields remove that tag.",
        )
        self.edit_meta_status = QLabel("No metadata loaded.")
        self.edit_meta_status.setObjectName("mutedLabel")
        self.edit_meta_title = QLineEdit()
        self.edit_meta_album = QLineEdit()
        self.edit_meta_artists = QLineEdit()
        self.edit_meta_artists.setPlaceholderText("Comma-separated artists")
        self.edit_meta_year = QLineEdit()
        self.edit_meta_track = QLineEdit()
        self.edit_meta_track_total = QLineEdit()
        self.edit_meta_artwork = PathPicker(
            placeholder="Local JPEG/PNG path or https:// image URL",
            file_filter="Images (*.jpg *.jpeg *.png);;All files (*)",
        )
        self.edit_meta_remove_artwork = QCheckBox("Remove existing artwork")
        metadata_form.addRow("Loaded metadata", self.edit_meta_status)
        self.edit_file_action_help = QLabel()
        self.edit_file_action_help.setObjectName("mutedLabel")
        self.edit_file_action_help.setWordWrap(True)
        metadata_form.addRow("", self.edit_file_action_help)
        metadata_form.addRow("Title", self.edit_meta_title)
        metadata_form.addRow("Album", self.edit_meta_album)
        metadata_form.addRow("Artists", self.edit_meta_artists)
        metadata_form.addRow("Year / date", self.edit_meta_year)
        metadata_form.addRow("Track number", self.edit_meta_track)
        metadata_form.addRow("Track total", self.edit_meta_track_total)
        metadata_form.addRow("Local trim start", self.edit_file_start)
        metadata_form.addRow("Local trim end", self.edit_file_end)
        metadata_form.addRow("Artwork path or URL", self.edit_meta_artwork)
        metadata_form.addRow("Artwork behavior", self.edit_meta_remove_artwork)

        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 4, 0, 0)
        clear_button = QPushButton("Clear")
        clear_button.setObjectName("secondaryButton")
        clear_button.clicked.connect(self._clear_edit_file)
        self.edit_file_run_button = QPushButton("Edit file")
        self.edit_file_run_button.setObjectName("primaryButton")
        self.edit_file_run_button.setMinimumWidth(170)
        self.edit_file_run_button.clicked.connect(self._start_edit_file_operation)
        action_layout.addWidget(clear_button)
        action_layout.addStretch(1)
        action_layout.addWidget(self.edit_file_run_button)
        self._form_runs["edit_media"] = self.edit_file_run_button
        metadata_outer.addWidget(action_row)
        layout.addWidget(card)
        layout.addWidget(metadata_card)
        layout.addWidget(self._feature_card("Safe editing", [
            "Metadata-only changes are written to a temporary copy and atomically replace the source",
            "Trimming uses lossless stream copy and then applies the edited metadata",
            "Redownload retains source container data before applying the edited fields",
            "Permanent crop/aspect re-encodes video after confirmation and atomically replaces it",
            "Artwork accepts a local JPEG/PNG or HTTP(S) image URL; leave it empty to preserve the cover",
            "Edited audio is renamed as Title - Album - Artists using filesystem-safe characters",
        ]))
        layout.addStretch(1)
        self._edit_file_action_changed()
        return page

    def _queue_edit_file_load(self, value: str) -> None:
        self._edit_file_load_timer.start()

    def _load_edit_file(self, requested_path: str) -> None:
        if requested_path != self.edit_file_input.text():
            return
        path = Path(requested_path).expanduser()
        if not path.is_file():
            self.edit_file_duration.setText("Select a file to load its duration and metadata.")
            self.edit_meta_status.setText("No metadata loaded.")
            return
        try:
            metadata = read_media_metadata(path)
        except (OSError, RuntimeError, ValueError) as exc:
            self.edit_meta_status.setText(f"Could not load metadata: {exc}")
            return
        try:
            duration_text = format_timestamp(probe_audio_duration(path))
            self._edit_file_loaded_duration = duration_text
            self.edit_file_duration.setText(duration_text)
            if str(self.edit_file_action.currentData()) == "trim":
                self.edit_file_end.setText(duration_text)
        except (OSError, RuntimeError, ValueError) as exc:
            self.edit_file_duration.setText(f"Could not read duration: {exc}")
        fields = {
            "title": self.edit_meta_title, "album": self.edit_meta_album,
            "artists": self.edit_meta_artists, "year": self.edit_meta_year,
            "track_number": self.edit_meta_track, "track_total": self.edit_meta_track_total,
        }
        payload = metadata.as_dict()
        for name, widget in fields.items():
            widget.setText(str(payload.get(name, "") or ""))
        artwork = "Artwork embedded" if metadata.artwork_present else "No embedded artwork"
        self.edit_meta_status.setText(f"Metadata loaded · {artwork}")
        self.edit_meta_artwork.set_text("")
        self.edit_meta_remove_artwork.setChecked(False)
        self._suggest_edit_file_output(path)

    def _edit_file_action_changed(self) -> None:
        action = str(self.edit_file_action.currentData())
        metadata_only = action == "metadata"
        redownload = action == "redownload"
        video_display = action == "video_display"
        if action == "trim" and self._edit_file_loaded_duration:
            self.edit_file_end.setText(self._edit_file_loaded_duration)
        self.edit_file_url.setEnabled(redownload)
        self.edit_file_content.setEnabled(redownload)
        self.edit_file_download_start.setEnabled(redownload)
        self.edit_file_download_end.setEnabled(redownload)
        self.edit_file_start.setEnabled(action == "trim")
        self.edit_file_end.setEnabled(action == "trim")
        self.edit_file_crop_ratio.setEnabled(video_display)
        self.edit_file_aspect_ratio.setEnabled(video_display)
        self.edit_file_mode.setEnabled(not metadata_only and not video_display)
        self.edit_file_output.setEnabled(
            not metadata_only
            and not video_display
            and not bool(self.edit_file_mode.currentData())
        )
        if action == "trim":
            self.edit_file_action_help.setText(
                "Trims the already-downloaded file selected above. No YouTube link or "
                "new download is used. Choose a start/end range, then replace the source "
                "or save a trimmed copy."
            )
            self.edit_file_run_button.setText("Trim local file")
            self.edit_file_end.setPlaceholderText("Loaded file duration")
        elif redownload:
            self.edit_file_action_help.setText(
                "Downloads replacement content from the YouTube link and optionally "
                "limits it to the selected timestamp range."
            )
            self.edit_file_run_button.setText("Redownload and edit")
        elif video_display:
            self.edit_file_action_help.setText(
                "Permanently bakes the selected centered crop and output aspect into "
                "this video. Future playback uses Aspect: Default and Crop: Default."
            )
            self.edit_file_run_button.setText("Apply crop / aspect permanently")
        else:
            self.edit_file_action_help.setText(
                "Updates tags and artwork without changing the local file's audio. "
                "Choose 'Trim the selected local file' to cut an existing download."
            )
            self.edit_file_run_button.setText("Update metadata")
        if metadata_only or video_display:
            reason = (
                "Permanent video display edits always replace the selected file"
                if video_display
                else "Metadata edits always replace the selected file"
            )
            self.edit_file_mode.setToolTip(f"Not applicable: {reason}")
            self.edit_file_output.setToolTip(f"Not applicable: {reason}")
        else:
            self.edit_file_mode.setToolTip("")
            self.edit_file_output.setToolTip("")
            self._suggest_edit_file_output(Path(self.edit_file_input.text()).expanduser())

    def _suggest_edit_file_output(self, path: Path) -> None:
        if not path.is_file() or str(self.edit_file_action.currentData()) in {
            "metadata",
            "video_display",
        }:
            return
        label = "trimmed" if str(self.edit_file_action.currentData()) == "trim" else "redownloaded"
        current = self.edit_file_output.text()
        if not current or current == self._edit_file_suggested_output:
            suggestion = str(path.with_name(f"{path.stem}_{label}{path.suffix}"))
            self.edit_file_output.set_text(suggestion)
            self._edit_file_suggested_output = suggestion

    def _edit_file_params(self) -> dict[str, Any]:
        metadata = {
            "title": self.edit_meta_title.text(), "album": self.edit_meta_album.text(),
            "artists": self.edit_meta_artists.text(), "year": self.edit_meta_year.text(),
            "track_number": self.edit_meta_track.text(), "track_total": self.edit_meta_track_total.text(),
        }
        action = str(self.edit_file_action.currentData())
        range_start = (
            self.edit_file_download_start.text()
            if action == "redownload"
            else self.edit_file_start.text()
        )
        range_end = (
            self.edit_file_download_end.text()
            if action == "redownload"
            else self.edit_file_end.text()
        )
        return {
            "action": action, "input_path": self.edit_file_input.text(),
            "youtube_url": self.edit_file_url.text(),
            "media_mode": str(self.edit_file_content.currentData()),
            "start_timestamp": range_start, "end_timestamp": range_end,
            "overwrite_source": (
                True
                if action in {"metadata", "video_display"}
                else bool(self.edit_file_mode.currentData())
            ),
            "output_path": (
                ""
                if action in {"metadata", "video_display"}
                else self.edit_file_output.text()
            ),
            "metadata": metadata, "artwork_path": self.edit_meta_artwork.text(),
            "remove_artwork": self.edit_meta_remove_artwork.isChecked(),
            "crop_ratio": self.edit_file_crop_ratio.currentText(),
            "aspect_ratio": self.edit_file_aspect_ratio.currentText(),
        }

    def _start_edit_file_operation(self) -> None:
        params = self._edit_file_params()
        if params["action"] != "video_display":
            self._start_operation("edit_media", params)
            return
        source = Path(str(params["input_path"])).expanduser()
        if source.suffix.casefold() not in VIDEO_EXTENSIONS:
            QMessageBox.warning(
                self,
                "Video file required",
                "Permanent crop/aspect editing is available only for supported video files.",
            )
            return
        crop = str(params["crop_ratio"])
        aspect = str(params["aspect_ratio"])
        if crop == "Default" and aspect == "Default":
            QMessageBox.information(
                self,
                "Choose a display edit",
                "Choose a crop ratio, an aspect ratio, or both before continuing.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Apply video display edit permanently?",
            f"File: {source.name}\nCrop: {crop}\nAspect: {aspect}\n\n"
            "This re-encodes and replaces the selected video. The operation cannot be "
            "undone unless you have another copy.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_operation("edit_media", params)

    def _clear_edit_file(self) -> None:
        self._edit_file_load_timer.stop()
        self.edit_file_input.set_text("")
        self.edit_file_action.setCurrentIndex(0)
        self.edit_file_url.clear()
        self.edit_file_content.setCurrentIndex(0)
        self.edit_file_download_start.setText("00:00")
        self.edit_file_download_end.clear()
        self.edit_file_start.setText("00:00")
        self.edit_file_end.clear()
        self.edit_file_crop_ratio.setCurrentText("Default")
        self.edit_file_aspect_ratio.setCurrentText("Default")
        self.edit_file_mode.setCurrentIndex(0)
        self.edit_file_output.set_text("")
        self._edit_file_suggested_output = ""
        self._edit_file_loaded_duration = ""
        for widget in (
            self.edit_meta_title, self.edit_meta_album, self.edit_meta_artists,
            self.edit_meta_year, self.edit_meta_track, self.edit_meta_track_total,
        ):
            widget.clear()
        self.edit_meta_artwork.set_text("")
        self.edit_meta_remove_artwork.setChecked(False)
        self.edit_meta_status.setText("No metadata loaded.")
        self.edit_file_duration.setText("Select a file to load its duration and metadata.")
        self._save_workspace_state()

    # -------------------------------------------------------------- edit album
    def _build_edit_album_page(self) -> QWidget:
        page, layout = self._page_container(
            "Edit Album",
            "Update shared album identity across every supported media file in one folder.",
        )
        card, outer, form = self._form_card(
            "Album-wide metadata",
            "Titles, track numbers, and folder names are preserved. Album, year, and "
            "optional artwork are applied to every file. Artist(s) is an optional override; "
            "leave it blank to preserve each track's existing artist. Filenames are rebuilt "
            "from the preserved title and resulting values, including nested folders.",
        )
        self.edit_album_folder = PathPicker(
            placeholder="Select the complete album folder",
            mode="folder",
        )
        self.edit_album_name = QLineEdit()
        self.edit_album_name.setPlaceholderText("Album name")
        self.edit_album_year = QLineEdit()
        self.edit_album_year.setMaxLength(4)
        self.edit_album_year.setPlaceholderText("Four-digit release year")
        self.edit_album_artist = QLineEdit()
        self.edit_album_artist.setPlaceholderText(
            "Optional shared override; blank preserves each track's artists"
        )
        self.edit_album_artwork = PathPicker(
            placeholder="Optional local JPEG/PNG path or https:// image URL",
            file_filter="Images (*.jpg *.jpeg *.png);;All files (*)",
        )
        self.edit_album_remove_artwork = QCheckBox("Remove artwork from every album file")
        self.edit_album_status = QLabel("Select an album folder to inspect its shared metadata.")
        self.edit_album_status.setObjectName("mutedLabel")
        self.edit_album_status.setWordWrap(True)
        form.addRow("Album folder", self.edit_album_folder)
        form.addRow("Album name", self.edit_album_name)
        form.addRow("Release year", self.edit_album_year)
        form.addRow("Artist(s) override", self.edit_album_artist)
        form.addRow("Album artwork", self.edit_album_artwork)
        form.addRow("Artwork behavior", self.edit_album_remove_artwork)
        form.addRow("Folder status", self.edit_album_status)

        self._edit_album_load_timer = QTimer(self)
        self._edit_album_load_timer.setSingleShot(True)
        self._edit_album_load_timer.setInterval(180)
        self._edit_album_load_timer.timeout.connect(
            lambda: self._load_edit_album_folder(self.edit_album_folder.text())
        )
        self.edit_album_folder.line_edit.textChanged.connect(
            lambda _value: self._edit_album_load_timer.start()
        )

        actions = QHBoxLayout()
        clear_button = QPushButton("Clear")
        clear_button.setObjectName("secondaryButton")
        clear_button.clicked.connect(self._clear_edit_album)
        run_button = QPushButton("Apply to all album files")
        run_button.setObjectName("primaryButton")
        run_button.setMinimumWidth(230)
        run_button.clicked.connect(self._start_edit_album)
        actions.addWidget(clear_button)
        actions.addStretch(1)
        actions.addWidget(run_button)
        outer.addLayout(actions)
        self._form_runs["edit_album"] = run_button
        layout.addWidget(card)
        layout.addWidget(
            self._feature_card(
                "Safety",
                [
                    "Each file is copied, retagged, validated, and atomically replaced.",
                    "Unreadable files are reported individually while other files continue.",
                    "A confirmation shows the exact number of files before changes begin.",
                ],
            )
        )
        layout.addStretch(1)
        return page

    def _load_edit_album_folder(self, requested_folder: str) -> None:
        if requested_folder != self.edit_album_folder.text():
            return
        try:
            summary = inspect_album_folder(requested_folder)
        except (OSError, RuntimeError, ValueError) as exc:
            self.edit_album_status.setText(str(exc))
            return
        self.edit_album_name.setText(summary.album)
        self.edit_album_year.setText(summary.year)
        self.edit_album_artist.setText(summary.artists)
        self.edit_album_artwork.set_text("")
        self.edit_album_remove_artwork.setChecked(False)
        mixed = (
            " · mixed values: " + ", ".join(field.replace("_", " ") for field in summary.mixed_fields)
            if summary.mixed_fields
            else " · shared metadata detected"
        )
        if summary.artwork_files == len(summary.files):
            artwork = "artwork embedded in every file"
        elif summary.artwork_files:
            artwork = f"artwork embedded in {summary.artwork_files}/{len(summary.files)} files"
        else:
            artwork = "no embedded artwork"
        self.edit_album_status.setText(
            f"{len(summary.files)} supported file(s){mixed} · {artwork}"
        )

    def _start_edit_album(self) -> None:
        self._edit_album_load_timer.stop()
        try:
            summary = inspect_album_folder(self.edit_album_folder.text())
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot edit album", str(exc))
            return
        album = self.edit_album_name.text().strip()
        year = self.edit_album_year.text().strip()
        if not album:
            QMessageBox.warning(self, "Album name required", "Enter the album name to apply.")
            return
        artists = self.edit_album_artist.text().strip()
        if year and not re.fullmatch(r"\d{4}", year):
            QMessageBox.warning(
                self, "Invalid release year", "Release year must contain four digits or be blank."
            )
            return
        artwork_path = self.edit_album_artwork.text()
        remove_artwork = self.edit_album_remove_artwork.isChecked()
        if artwork_path and remove_artwork:
            QMessageBox.warning(
                self,
                "Choose artwork behavior",
                "Select replacement artwork or remove existing artwork, not both.",
            )
            return
        artwork_action = (
            f"replace from {artwork_path}"
            if artwork_path
            else "remove from every file"
            if remove_artwork
            else "preserve existing artwork"
        )
        artist_action = artists or "preserve each file's existing artist(s)"
        answer = QMessageBox.question(
            self,
            "Apply album metadata?",
            f"Update album metadata in {len(summary.files)} file(s) inside:\n"
            f"{summary.folder}\n\nAlbum: {album}\nYear: {year or '(clear)'}\n"
            f"Artist(s): {artist_action}\nArtwork: {artwork_action}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_operation(
            "edit_album",
            {
                "folder": str(summary.folder),
                "metadata": {
                    "album": album,
                    "year": year,
                    "artists": artists,
                },
                "artwork_path": artwork_path,
                "remove_artwork": remove_artwork,
                "ai_enabled": False,
            },
        )

    def _clear_edit_album(self) -> None:
        self._edit_album_load_timer.stop()
        self.edit_album_folder.set_text("")
        self.edit_album_name.clear()
        self.edit_album_year.clear()
        self.edit_album_artist.clear()
        self.edit_album_artwork.set_text("")
        self.edit_album_remove_artwork.setChecked(False)
        self.edit_album_status.setText(
            "Select an album folder to inspect its shared metadata."
        )
        self._save_workspace_state()

    # ------------------------------------------------------ album consolidator
    def _build_album_consolidator_page(self) -> QWidget:
        page, layout = self._page_container(
            "Album Consolidator",
            "Read Album metadata from audio and video files, then move each file into "
            "a matching album folder under your selected destination.",
        )
        enrich_card, enrich_outer, enrich_form = self._form_card(
            "1. Album enricher",
            "Search and complete metadata, then retag and rename songs recursively using verified matches.",
        )
        self.album_consolidator_source = PathPicker(
            placeholder="Folder containing album tracks",
            mode="folder",
        )
        self.album_enrich_destination_enabled = self._check(
            "Enable destination path for enrichment",
            False,
        )
        enrich_form.addRow("Source folder", self.album_consolidator_source)
        enrich_form.addRow("Destination scan", self.album_enrich_destination_enabled)
        self.album_enrich_force_recheck = self._check(
            "Recheck files already marked complete (repairs a wrong year)", False
        )
        enrich_form.addRow("Rerun policy", self.album_enrich_force_recheck)
        enrich_action = QWidget()
        enrich_action_layout = QHBoxLayout(enrich_action)
        enrich_action_layout.setContentsMargins(0, 4, 0, 0)
        enrich_action_layout.addStretch(1)
        enrich_button = QPushButton("Run album enricher")
        enrich_button.setObjectName("primaryButton")
        enrich_button.setMinimumWidth(250)
        enrich_button.clicked.connect(
            lambda: self._start_operation(
                "album_metadata_enricher", self._album_metadata_enricher_params()
            )
        )
        enrich_action_layout.addWidget(enrich_button)
        self._form_runs["album_metadata_enricher"] = enrich_button
        enrich_outer.addWidget(enrich_action)

        move_card, move_outer, move_form = self._form_card(
            "2. Move into album folders",
            "Move files into album folders, enrich the selected scope, and apply verified track ordering.",
        )
        self.album_consolidator_destination = PathPicker(
            placeholder="Folder that will contain the album folders",
            mode="folder",
        )
        self.album_move_perform_enrichment = self._check(
            "Perform album enrichment before and after moving",
            True,
        )
        self.album_move_enrich_all_destination = self._check(
            "Include all destination files in enrichment",
            False,
        )
        self.album_move_perform_enrichment.toggled.connect(
            self.album_move_enrich_all_destination.setEnabled
        )
        move_form.addRow("Destination folder", self.album_consolidator_destination)
        move_form.addRow("Metadata", self.album_move_perform_enrichment)
        move_form.addRow("Enrichment scope", self.album_move_enrich_all_destination)

        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 4, 0, 0)
        clear_button = QPushButton("Clear")
        clear_button.setObjectName("secondaryButton")
        clear_button.clicked.connect(self._clear_album_consolidator)
        run_button = QPushButton("Move into album folders")
        run_button.setObjectName("primaryButton")
        run_button.setMinimumWidth(210)
        run_button.clicked.connect(
            lambda: self._start_operation(
                "album_consolidator", self._album_consolidator_params()
            )
        )
        action_layout.addWidget(clear_button)
        action_layout.addStretch(1)
        action_layout.addWidget(run_button)
        self._form_runs["album_consolidator"] = run_button
        move_outer.addWidget(action_row)
        layout.addWidget(enrich_card)
        layout.addWidget(move_card)
        layout.addWidget(self._feature_card("Consolidation rules", [
            "Album Enricher never moves files; Move enrichment is enabled by default",
            "Disable move enrichment after stage 1 to route existing tags without repeating it",
            "Track indexing still runs when move enrichment is disabled",
            "Enable the move scope option to enrich the complete destination tree instead",
            "Enriched files are renamed as Title - Album - Artists",
            "Soundtrack, EP, and Single storefront suffixes are removed from Album tags and searches",
            "Album values are written only from exact soundtrack, discography, or catalog matches",
            "Album folders use Album name (release year) when track metadata provides the year",
            "Matching album/year folders merge without deleting or overwriting any song",
            "Untagged files named Title - Album - Artists are tagged automatically before moving",
            "Album folder names are sanitized using the project's filename rules",
            "Album Enricher searches for Unknown placeholders; Move leaves unresolved files in place",
            "Files with blank, Unknown, or unreadable Album metadata are skipped",
            "Album tags containing a credited artist are removed and those files are not moved",
            "If a title already exists in its destination album folder, the source duplicate is deleted",
            "Existing album folders are reused; existing files are never overwritten",
            "After moving, Wikipedia order is compressed to the downloaded subset as 1, 2, 3…",
            "Source and destination selections persist when the application closes",
            "Workers, retries, network waits, audio defaults, and Wikipedia ordering are managed in Global Settings",
        ]))
        layout.addStretch(1)
        return page

    def _album_consolidator_params(self) -> dict[str, Any]:
        source_folder = self._resolved_album_consolidator_source()
        return {
            "source_folder": source_folder,
            "destination_folder": self.album_consolidator_destination.text(),
            "perform_enrichment": self.album_move_perform_enrichment.isChecked(),
            "enrich_all_destination": self.album_move_enrich_all_destination.isChecked(),
            "tracker_path": self._metadata_tracker_file,
        }

    def _album_metadata_enricher_params(self) -> dict[str, Any]:
        source_folder = self._resolved_album_consolidator_source()
        return {
            "source_folder": source_folder,
            "destination_folder": (
                self.album_consolidator_destination.text()
                if self.album_enrich_destination_enabled.isChecked()
                else ""
            ),
            "tracker_path": self._metadata_tracker_file,
            "force_recheck": self.album_enrich_force_recheck.isChecked(),
        }

    def _resolved_album_consolidator_source(self) -> str:
        """Refresh a persisted album path after enrichment adds its year."""

        text = self.album_consolidator_source.text().strip()
        if not text:
            return ""
        requested = Path(text).expanduser()
        resolved = resolve_album_folder_successor(requested)
        if resolved != requested:
            self.album_consolidator_source.set_text(str(resolved))
            self.settings.setValue(
                "workspace/album_consolidator_source", str(resolved)
            )
            self.settings.sync()
            self._append_log(
                f"[RESTORED] Album source path updated after rename: {resolved}"
            )
        return str(resolved)

    def _clear_album_consolidator(self) -> None:
        self.album_consolidator_source.set_text("")
        self.album_consolidator_destination.set_text("")
        self.album_enrich_destination_enabled.setChecked(False)
        self.album_move_perform_enrichment.setChecked(True)
        self.album_move_enrich_all_destination.setChecked(False)
        self.album_enrich_force_recheck.setChecked(False)
        self._save_workspace_state()

    # --------------------------------------------------------------- utilities
    def _build_utilities_page(self) -> QWidget:
        page, layout = self._page_container(
            "Utilities",
            "Validate batch files, normalize artist metadata, and turn timestamp lists into splitter-ready JSON.",
        )
        tabs = QTabWidget()
        tabs.addTab(self._build_artist_tab(), "Artist formatter")
        tabs.addTab(self._build_tracks_tab(), "Timestamp parser")
        tabs.setMinimumHeight(590)
        layout.addWidget(tabs)
        layout.addStretch(1)
        return page

    def _build_artist_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        self.artist_input = QTextEdit()
        self.artist_input.setPlaceholderText("Example: Kumar Sanu, Udit Narayan and Alka Yagnik")
        self.artist_input.setMaximumHeight(150)
        self.artist_output = QLineEdit()
        self.artist_output.setReadOnly(True)
        run = QPushButton("Format artist names")
        run.setObjectName("primaryButton")
        run.clicked.connect(lambda: self._start_operation("format_artists", {
            "input_text": self.artist_input.toPlainText(),
        }))
        copy_button = QPushButton("Copy result")
        copy_button.setObjectName("secondaryButton")
        copy_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.artist_output.text()))
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(copy_button)
        button_row.addWidget(run)
        layout.addWidget(QLabel("Raw artist text"))
        layout.addWidget(self.artist_input)
        layout.addWidget(QLabel("Formatted result"))
        layout.addWidget(self.artist_output)
        layout.addLayout(button_row)
        layout.addStretch(1)
        return tab

    def _build_tracks_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        self.tracks_input_file = PathPicker(
            placeholder="Optional config/timestamps.txt",
            file_filter="Text files (*.txt);;All files (*)",
        )
        self.tracks_text = QPlainTextEdit()
        self.tracks_text.setPlaceholderText("00:00 - Song One by Artist\n04:12 - Song Two by Artist")
        self.tracks_end_field = QComboBox()
        self.tracks_end_field.addItem("end (jukebox)", "end")
        self.tracks_end_field.addItem("stop (album)", "stop")
        self.tracks_unknown = QLineEdit("Unknown")
        self.tracks_keep_case = self._check("Preserve title casing")
        self.tracks_output_path = PathPicker(placeholder="Optional tracks.json", mode="save", file_filter="JSON files (*.json)")
        self.tracks_result = QPlainTextEdit()
        self.tracks_result.setReadOnly(True)
        controls = QGridLayout()
        controls.addWidget(QLabel("End field"), 0, 0)
        controls.addWidget(self.tracks_end_field, 0, 1)
        controls.addWidget(QLabel("Unknown artist"), 0, 2)
        controls.addWidget(self.tracks_unknown, 0, 3)
        controls.addWidget(self.tracks_keep_case, 1, 0, 1, 2)
        run = QPushButton("Parse timestamps")
        run.setObjectName("primaryButton")
        run.clicked.connect(lambda: self._start_operation("parse_tracks", {
            "input_path": self.tracks_input_file.text(),
            "input_text": self.tracks_text.toPlainText(),
            "end_field": self.tracks_end_field.currentData(),
            "unknown_artists": self.tracks_unknown.text(),
            "keep_case": self.tracks_keep_case.isChecked(),
            "output_path": self.tracks_output_path.text(),
        }))
        layout.addWidget(QLabel("Optional input file"))
        layout.addWidget(self.tracks_input_file)
        layout.addWidget(QLabel("Timestamp text"))
        layout.addWidget(self.tracks_text)
        layout.addLayout(controls)
        layout.addWidget(QLabel("Optional output file"))
        layout.addWidget(self.tracks_output_path)
        layout.addWidget(run, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(QLabel("Generated JSON"))
        layout.addWidget(self.tracks_result, 1)
        return tab

    # ------------------------------------------------------------------- logs
    def _build_logs_page(self) -> QWidget:
        page, layout = self._page_container(
            "Live logs",
            "All service output is captured from the background worker, including output produced by internal download threads.",
        )
        card = GlassCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setStyleSheet("font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 12px;")
        controls = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.setObjectName("secondaryButton")
        clear.clicked.connect(self.log_view.clear)
        save = QPushButton("Save log")
        save.setObjectName("secondaryButton")
        save.clicked.connect(self._save_log)
        copy = QPushButton("Copy all")
        copy.setObjectName("secondaryButton")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.log_view.toPlainText()))
        controls.addStretch(1)
        controls.addWidget(copy)
        controls.addWidget(clear)
        controls.addWidget(save)
        card_layout.addWidget(self.log_view, 1)
        card_layout.addLayout(controls)
        layout.addWidget(card, 1)
        return page

    # ---------------------------------------------------------------- settings
    def _build_settings_page(self) -> QWidget:
        page, layout = self._page_container(
            "Global Settings",
            "Configure application-wide defaults. Expand only the section you need.",
        )
        self.settings_workers = self._spin(
            1,
            MAX_PARALLEL_WORKERS,
            self._default_value("workers", machine_parallel_workers()),
        )
        self.settings_workers.setToolTip(
            f"Minimum value: 1 · Maximum for this machine: {MAX_PARALLEL_WORKERS}"
        )
        self.settings_min_delay = self._spin(0, 600, self._default_value("min_delay", 10), " s")
        self.settings_max_delay = self._spin(0, 600, self._default_value("max_delay", 25), " s")
        self.settings_retries = self._spin(1, 20, self._default_value("retries", 3))
        self.settings_retry_wait = self._spin(
            0, 600, self._default_value("retry_wait", 60), " s"
        )
        self.settings_rate_limit_wait = self._spin(
            1, 3600, self._default_value("rate_limit_wait", 180), " s"
        )
        self.settings_audio_quality = QComboBox()
        self.settings_audio_quality.addItems(["320", "256", "192", "128"])
        self.settings_audio_quality.setCurrentText(
            str(self.settings.value("defaults/audio_quality", "320"))
        )
        self.settings_sample_rate = QComboBox()
        self.settings_sample_rate.addItems(["44100", "48000"])
        self.settings_sample_rate.setCurrentText(
            str(self.settings.value("defaults/sample_rate", "44100"))
        )
        self.settings_video_seek_seconds = self._spin(
            1,
            60,
            self._default_value("video_seek_seconds", 10),
            " s",
        )
        self.settings_video_seek_seconds.setToolTip(
            "Seconds skipped by the << and >> media controls and by Left/Right "
            "during video playback; Shift skips twice this value."
        )
        self.settings_remember_video_display_modes = self._check(
            "Remember crop and aspect ratio for the next video",
            self._setting_bool("defaults/remember_video_display_modes", False),
        )
        self.settings_remember_video_display_modes.setToolTip(
            "Off: every newly loaded video starts with Default crop and aspect ratio. "
            "On: the last choices carry over to the next video and app session."
        )
        self.settings_wikipedia_order = self._check(
            "Use verified Wikipedia album order and compress downloaded songs to 1..N",
            self._setting_bool("defaults/wikipedia_track_order", True),
        )
        self.settings_ai_enabled = self._check(
            "Use AI by default for tools without a saved per-section choice",
            self._setting_bool("defaults/ai_enabled", True),
        )
        self.settings_crash_reports = self._check(
            "Save diagnostic crash reports on this device",
            self._setting_bool("privacy/crash_reports_enabled", False),
        )
        self.settings_agentic_model = QComboBox()
        self.settings_agentic_model.setEditable(True)
        try:
            agent_models = available_ollama_models()
        except (OSError, ValueError, json.JSONDecodeError):
            agent_models = []
        self.settings_agentic_model.addItems(agent_models or ["qwen2.5:7b"])
        saved_ollama_model = self.settings.value("defaults/agentic_model", None)
        self.settings_agentic_model.setCurrentText(
            DEFAULT_OLLAMA_MODEL
            if saved_ollama_model is None
            else str(saved_ollama_model or "").strip()
        )
        self.settings_agentic_model.setToolTip(
            "Local model used as the primary provider or automatic hosted-provider fallback."
        )
        legacy_nvidia_key = self._saved_secret(
            "defaults/nvidia_api_key", NVIDIA_API_KEY_ENV
        )
        saved_provider = str(self.settings.value("defaults/ai_provider", "") or "").strip()
        if not saved_provider:
            saved_provider = "nvidia" if legacy_nvidia_key else "ollama"
        self._ai_provider_drafts: dict[str, dict[str, str]] = {}
        for provider in PROVIDERS:
            key = str(
                self.settings.value(f"defaults/ai_providers/{provider.id}/api_key", "")
                or ""
            ).strip()
            model = str(
                self.settings.value(
                    f"defaults/ai_providers/{provider.id}/model", provider.default_model
                )
                or ""
            ).strip()
            base_url = str(
                self.settings.value(
                    f"defaults/ai_providers/{provider.id}/base_url", provider.base_url
                )
                or ""
            ).strip()
            if provider.id == "nvidia":
                key = key or legacy_nvidia_key
                legacy_model = self.settings.value("defaults/nvidia_model", None)
                model = (
                    model
                    if legacy_model is None
                    else str(legacy_model or "").strip()
                )
            self._ai_provider_drafts[provider.id] = {
                "api_key": key,
                "model": model,
                "base_url": base_url,
            }
        self.settings_ai_provider = QComboBox()
        for provider in PROVIDERS:
            self.settings_ai_provider.addItem(provider.label, provider.id)
        provider_index = self.settings_ai_provider.findData(saved_provider)
        self.settings_ai_provider.setCurrentIndex(max(0, provider_index))
        self._active_ai_provider = str(self.settings_ai_provider.currentData())
        self.settings_ai_api_key = QLineEdit()
        self.settings_ai_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.settings_ai_api_key.setToolTip(
            "Saved locally for the selected provider and never placed in operation logs."
        )
        self.settings_ai_model = QLineEdit()
        self.settings_ai_model.setToolTip("Provider model ID used by every Agno-backed task.")
        self.settings_ai_base_url = QLineEdit()
        self.settings_ai_base_url.setPlaceholderText(
            "Required only for a custom OpenAI-compatible provider"
        )
        self.settings_ai_provider.currentIndexChanged.connect(
            self._ai_provider_selection_changed
        )
        self._show_ai_provider_draft(self._active_ai_provider)
        # Compatibility aliases retained for integrations that referenced the old controls.
        self.settings_nvidia_api_key = self.settings_ai_api_key
        self.settings_nvidia_model = self.settings_ai_model
        self.settings_serpapi_api_key = QLineEdit()
        self.settings_serpapi_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.settings_serpapi_api_key.setPlaceholderText("Optional SerpApi key")
        self.settings_serpapi_api_key.setText(
            self._saved_secret("defaults/serpapi_api_key", SERPAPI_API_KEY_ENV)
        )
        self.settings_serpapi_api_key.setToolTip(
            "Optional SerpApi credential used only when Wikipedia and Apple cannot "
            "identify missing album/movie metadata. It is never written to logs."
        )
        self.settings_persist_state = self._check(
            "Restore forms, statuses, output folders, and history after restart",
            self._setting_bool("workspace/persist_enabled", True),
        )
        self.settings_persist_state.toggled.connect(self._toggle_workspace_persistence)
        self.settings_search_suggestions = self._spin(
            1, 20, self._default_value("search_suggestions", 10), " matches"
        )
        self.settings_search_suggestions.setToolTip(
            "Maximum ranked matches shown beneath the Media Library search field."
        )
        self.settings_data_directory = PathPicker(
            placeholder="Folder for settings, enrichment history, and diagnostics",
            mode="folder",
        )
        if self._data_directory is not None:
            self.settings_data_directory.set_text(self._data_directory)
        else:
            settings_path = Path(self.settings.fileName())
            if settings_path.suffix.casefold() == ".ini":
                self.settings_data_directory.set_text(settings_path.parent)
        self.settings_data_directory.setToolTip(
            "All persistent app files are kept together here. A changed location "
            "takes effect after restarting the application."
        )
        crystal_control = QWidget()
        crystal_layout = QHBoxLayout(crystal_control)
        crystal_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_crystalness = QSlider(Qt.Orientation.Horizontal)
        self.settings_crystalness.setRange(0, 100)
        self.settings_crystalness.setValue(self._default_value("crystalness", 65))
        self.settings_crystalness.setToolTip(
            "Adjust glass transparency, specular highlights, and border brightness live."
        )
        self._crystal_preview_timer = QTimer(self)
        self._crystal_preview_timer.setSingleShot(True)
        self._crystal_preview_timer.setInterval(40)
        self._crystal_preview_timer.timeout.connect(
            lambda: self._apply_crystalness(
                self.settings_crystalness.value(), persist=False
            )
        )
        self.settings_crystalness_value = QLabel(
            f"{self.settings_crystalness.value()}%"
        )
        self.settings_crystalness_value.setMinimumWidth(42)
        self.settings_crystalness.valueChanged.connect(self._preview_crystalness)
        crystal_layout.addWidget(self.settings_crystalness, 1)
        crystal_layout.addWidget(self.settings_crystalness_value)

        actions_card = GlassCard()
        settings_actions = QHBoxLayout(actions_card)
        settings_actions.setContentsMargins(14, 10, 14, 10)
        reset = QPushButton("Reset app")
        reset.setObjectName("dangerButton")
        reset.setToolTip(
            "Clear every tool, remove saved provider credentials/models, and restore defaults"
        )
        reset.clicked.connect(self._reset_app)
        save = QPushButton("Save and apply defaults")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save_defaults)
        settings_actions.addWidget(reset)
        settings_actions.addStretch(1)
        settings_actions.addWidget(save)
        layout.addWidget(actions_card)

        self.settings_sections: dict[str, CollapsibleSection] = {}
        batch_section, _batch_body, batch_form = self._settings_group(
            "Batch processing and network",
            "Concurrency, pacing, retry, and rate-limit defaults used by download workflows.",
            "batch_network",
            expanded=True,
        )
        batch_form.addRow("Parallel workers", self.settings_workers)
        batch_form.addRow("Minimum delay", self.settings_min_delay)
        batch_form.addRow("Maximum delay", self.settings_max_delay)
        batch_form.addRow("Retries", self.settings_retries)
        batch_form.addRow("Retry delay", self.settings_retry_wait)
        batch_form.addRow("Rate-limit wait", self.settings_rate_limit_wait)
        self.settings_sections["batch_network"] = batch_section
        layout.addWidget(batch_section)

        audio_section, _audio_body, audio_form = self._settings_group(
            "Audio and metadata defaults",
            "Output quality and deterministic album ordering applied across media workflows.",
            "audio_metadata",
            expanded=False,
        )
        audio_form.addRow("Default MP3 bitrate", self.settings_audio_quality)
        audio_form.addRow("Default sample rate", self.settings_sample_rate)
        audio_form.addRow("Album track ordering", self.settings_wikipedia_order)
        self.settings_sections["audio_metadata"] = audio_section
        layout.addWidget(audio_section)

        video_section, _video_body, video_form = self._settings_group(
            "Media Playback",
            "Audio/video seek controls and video display behavior.",
            "video_playback",
            expanded=False,
        )
        video_form.addRow("Seek interval", self.settings_video_seek_seconds)
        video_form.addRow(
            "Crop/aspect memory", self.settings_remember_video_display_modes
        )
        self.settings_sections["video_playback"] = video_section
        layout.addWidget(video_section)

        ai_section, _ai_body, ai_form = self._settings_group(
            "AI providers and online evidence",
            "Choose a local or hosted Agno provider. Hosted providers automatically fall back "
            "to the selected Ollama model. Credentials remain local and are never logged.",
            "ai_providers",
            expanded=True,
        )
        ai_form.addRow("Default AI policy", self.settings_ai_enabled)
        ai_form.addRow("Primary provider", self.settings_ai_provider)
        ai_form.addRow("Provider API key", self.settings_ai_api_key)
        ai_form.addRow("Provider model", self.settings_ai_model)
        ai_form.addRow("Provider base URL", self.settings_ai_base_url)
        ai_form.addRow("Ollama local / fallback", self.settings_agentic_model)
        ai_form.addRow("SerpApi key", self.settings_serpapi_api_key)
        self.settings_sections["ai_providers"] = ai_section
        layout.addWidget(ai_section)

        behavior_section, _behavior_body, behavior_form = self._settings_group(
            "Application behavior and privacy",
            "Control workspace restoration, local diagnostics, and Media Library suggestion size.",
            "behavior_privacy",
            expanded=False,
        )
        behavior_form.addRow("Workspace state", self.settings_persist_state)
        behavior_form.addRow("Crash-report storage", self.settings_crash_reports)
        behavior_form.addRow("Library suggestions", self.settings_search_suggestions)
        self.settings_sections["behavior_privacy"] = behavior_section
        layout.addWidget(behavior_section)

        storage_section, storage_body, storage_form = self._settings_group(
            "Storage and appearance",
            "Choose where persistent app data lives and tune the glass appearance.",
            "storage_appearance",
            expanded=False,
        )
        storage_form.addRow("Application data folder", self.settings_data_directory)
        storage_form.addRow("Crystalness", crystal_control)
        storage_actions = QHBoxLayout()
        open_storage = QPushButton("Open data folder")
        open_storage.setObjectName("secondaryButton")
        open_storage.clicked.connect(self._open_data_directory)
        storage_note = QLabel(
            "Changing this folder safely copies existing data and is applied on next start."
        )
        storage_note.setObjectName("mutedLabel")
        storage_actions.addWidget(storage_note)
        storage_actions.addStretch(1)
        storage_actions.addWidget(open_storage)
        storage_body.addLayout(storage_actions)
        self.settings_sections["storage_appearance"] = storage_section
        layout.addWidget(storage_section)

        layout.addStretch(1)
        return page

    # ------------------------------------------------------------- UI helpers
    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _feature_card(self, title: str, items: list[str]) -> GlassCard:
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(8)
        layout.addWidget(self._section_label(title))
        for item in items:
            label = QLabel(f"•  {item}")
            label.setObjectName("mutedLabel")
            label.setWordWrap(True)
            layout.addWidget(label)
        return card

    def _set_page(self, index: int) -> None:
        if not 0 <= index < self.pages.count():
            index = 0
        self.pages.setCurrentIndex(index)
        for button_index, button in enumerate(self._nav_buttons):
            button.setChecked(button_index == index)
        self.settings.setValue("window/last_page", index)
        operation = {
            1: "search_song",
            2: "audio",
            3: "video",
            4: "album",
            5: "jukebox",
            6: "track_reorder",
            7: "edit_media",
            9: "album_consolidator",
            10: "utilities",
        }.get(index)
        if operation:
            self._show_workspace_ai_policy(operation)

    def _restore_last_page(self) -> None:
        """Open the page that was selected when the previous session ended."""
        try:
            index = int(self.settings.value("window/last_page", 0))
        except (TypeError, ValueError):
            index = 0
        # Preserve the selected workflow as new pages are inserted.
        schema = int(self.settings.value("window/navigation_schema", 1))
        if schema < 2:
            if index >= 5:
                index += 1
        if schema < 3:
            if index >= 6:
                index += 1
        if schema < 4 and index >= 1:
            index += 1
        if schema < 5 and index >= 6:
            index += 1
        if schema < 6:
            if index in {7, 8}:
                index = 7
            elif index >= 9:
                index -= 1
        if schema < 7 and index >= 8:
            index += 1
        if schema < 8 and index >= 8:
            index += 1
        self.settings.setValue("window/navigation_schema", 8)
        self._set_page(index)

    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ----------------------------------------------------------- worker logic
    def _start_operation(self, operation: str, params: dict[str, Any]) -> None:
        if self._operation_is_running(operation):
            QMessageBox.information(
                self,
                "Job already running",
                "That workspace already has a running job. Other workspaces remain available.",
            )
            return
        if self._active_thread is not None:
            self._start_parallel_operation(operation, params)
            return

        params = dict(params)
        params["workers"] = self._default_value("workers", machine_parallel_workers())
        params["min_delay"] = self._default_value("min_delay", 10)
        params["max_delay"] = self._default_value("max_delay", 25)
        params["retries"] = self._default_value("retries", 3)
        params["retry_wait"] = self._default_value("retry_wait", 60)
        params["rate_limit_wait"] = self._default_value("rate_limit_wait", 180)
        params["preferred_mp3_quality"] = str(
            self.settings.value("defaults/audio_quality", "320")
        )
        params["audio_sample_rate"] = str(
            self.settings.value("defaults/sample_rate", "44100")
        )
        params["wikipedia_track_order"] = self._setting_bool(
            "defaults/wikipedia_track_order", True
        )
        ai_enabled = self._ai_enabled_for(operation)
        params["ai_enabled"] = ai_enabled
        params["agentic_model"] = self._agentic_model() if ai_enabled else ""
        if "model" in params:
            params["model"] = self._agentic_model() if ai_enabled else ""
        # All downloaders share the same completion tracker so their automatic
        # post-download enrichment does not repeat verified network work.
        params.setdefault("tracker_path", self._metadata_tracker_file)
        self._show_ai_usage(operation, params)
        self._active_operation_name = operation
        input_data = params.get("input_data")
        self._active_entry_names = list(input_data) if isinstance(input_data, dict) else []
        if operation == "album":
            for name in self._active_entry_names:
                self._album_statuses[name] = "Running"
            self.album_input.set_statuses(self._album_statuses)
        self._save_workspace_state()

        worker = OperationWorker(operation, params)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._append_log)
        worker.progress.connect(self._update_operation_progress)
        worker.phase_changed.connect(self._operation_phase_changed)
        worker.file_in_use.connect(self._show_file_in_use_warning)
        worker.item_finished.connect(self._mark_batch_item_finished)
        worker.finished.connect(self._operation_finished)
        worker.failed.connect(self._operation_failed)
        worker.cancelled.connect(self._operation_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.cancelled.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_worker_refs)

        self._active_worker = worker
        self._active_thread = thread
        self._active_operation_params = dict(params)
        self._active_eta_phase = (
            "album_enrichment" if operation == "album_metadata_enricher" else "main"
        )
        self._active_progress_unit = self._progress_unit(operation, self._active_eta_phase)
        self._active_eta_key = self._eta_profile_key(
            operation, params, self._active_eta_phase
        )
        self._operation_started_at = time.monotonic()
        self._active_progress_current = 0
        self._active_progress_total = 0
        self._session_jobs += 1
        self.jobs_metric.set_value(self._session_jobs)
        self.dashboard_state_badge.setText("RUNNING")
        self.activity_label.setText(
            running_operation_text(operation, "Preparing operation")
        )
        self.pulse_dot.setVisible(True)
        self.activity_progress.setRange(0, 0)
        self.activity_progress.setFormat("Preparing…")
        self._sync_stop_button()
        self._sync_run_buttons()
        if operation == "album_metadata_enricher":
            # Disabling the clicked button makes Qt advance focus to the next
            # editable widget, which is the Move destination PathPicker.  That
            # looked like the application was choosing/changing a destination.
            # Keep focus in the enrichment card and leave both paths unselected.
            QTimer.singleShot(0, self._restore_album_enricher_focus)
        thread.start()

    def _restore_album_enricher_focus(self) -> None:
        for picker in (
            self.album_consolidator_source,
            self.album_consolidator_destination,
        ):
            picker.line_edit.deselect()
        self.album_enrich_destination_enabled.setFocus(
            Qt.FocusReason.OtherFocusReason
        )

    def _start_parallel_operation(self, operation: str, params: dict[str, Any]) -> None:
        """Run a different workspace without interrupting existing jobs."""

        if self._operation_is_running(operation):
            QMessageBox.information(
                self,
                "Job already running",
                "That workspace already has a running job. Other workspaces remain available.",
            )
            return
        params = dict(params)
        params["workers"] = self._default_value("workers", machine_parallel_workers())
        params["min_delay"] = self._default_value("min_delay", 10)
        params["max_delay"] = self._default_value("max_delay", 25)
        params["retries"] = self._default_value("retries", 3)
        params["retry_wait"] = self._default_value("retry_wait", 60)
        params["rate_limit_wait"] = self._default_value("rate_limit_wait", 180)
        params["preferred_mp3_quality"] = str(
            self.settings.value("defaults/audio_quality", "320")
        )
        params["audio_sample_rate"] = str(
            self.settings.value("defaults/sample_rate", "44100")
        )
        params["wikipedia_track_order"] = self._setting_bool(
            "defaults/wikipedia_track_order", True
        )
        ai_enabled = self._ai_enabled_for(operation)
        params["ai_enabled"] = ai_enabled
        params["agentic_model"] = self._agentic_model() if ai_enabled else ""
        if "model" in params:
            params["model"] = self._agentic_model() if ai_enabled else ""
        params.setdefault("tracker_path", self._metadata_tracker_file)
        usage = operation_ai_usage(operation, params)
        if usage.active:
            self._set_ai_status(usage.badge_text, active=True)

        worker = OperationWorker(operation, params)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(lambda line, name=operation: self._append_log(f"[{name}] {line}"))
        worker.file_in_use.connect(
            lambda path, action, current=worker: self._show_parallel_file_warning(
                current, path, action
            )
        )
        worker.finished.connect(
            lambda summary, current=thread: self._parallel_operation_finished(
                current, summary
            )
        )
        worker.failed.connect(
            lambda message, traceback_text, current=thread: self._parallel_operation_failed(
                current, message, traceback_text
            )
        )
        worker.cancelled.connect(
            lambda current=thread: self._parallel_operation_cancelled(current)
        )
        worker.item_finished.connect(
            lambda item, successful, name=operation: (
                self._mark_parallel_batch_item_finished(name, item, successful)
            )
        )
        for signal in (worker.finished, worker.failed, worker.cancelled):
            signal.connect(thread.quit)
            signal.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda current=thread: self._clear_parallel_job(current))
        self._parallel_jobs[thread] = (operation, worker)
        input_data = params.get("input_data")
        entry_names = list(input_data) if isinstance(input_data, dict) else []
        self._parallel_entry_names[thread] = entry_names
        if operation == "album":
            for name in entry_names:
                self._album_statuses[name] = "Running"
            self.album_input.set_statuses(self._album_statuses)
            self._save_workspace_state()
        self._sync_stop_button()
        self._sync_run_buttons()
        self._session_jobs += 1
        self.jobs_metric.set_value(self._session_jobs)
        self._append_log(
            f"[PARALLEL] Started {operation}; other workspace jobs continue."
        )
        thread.start()

    def _show_parallel_file_warning(
        self, worker: OperationWorker, path_text: str, action: str
    ) -> None:
        path = Path(path_text)
        QMessageBox.warning(
            self,
            "File is being used",
            f'"{path.name}" is open in another application while {action}.\n\n'
            "Close the application using it, then press OK to retry.",
            QMessageBox.StandardButton.Ok,
        )
        worker.acknowledge_file_in_use()

    def _parallel_operation_finished(
        self, thread: QThread, summary: dict[str, Any]
    ) -> None:
        operation = self._parallel_jobs.get(thread, ("Job", None))[0]
        self._session_completed += 1
        self.completed_metric.set_value(self._session_completed)
        self._handle_operation_output(summary)
        editor = self._batch_editor_for_operation(
            str(summary.get("operation", operation))
        )
        if editor is not None:
            editor.disable_completed(
                summary.get("completed_items", ()),
                summary.get("failed_items", ()),
            )
        details = self._summary_text(summary)
        self._append_log(f"[PARALLEL-COMPLETE] {operation}: {details}")
        self._add_history(operation, "Completed", summary.get("total", 0), details)
        if operation == "album":
            status = "Partial" if int(summary.get("failed", 0) or 0) else "Completed"
            self._set_parallel_album_status(thread, status)
        self._save_workspace_state()
        self.media_library.refresh_library()

    def _parallel_operation_failed(
        self, thread: QThread, message: str, traceback_text: str
    ) -> None:
        operation = self._parallel_jobs.get(thread, ("Job", None))[0]
        self._session_failed += 1
        self.failed_metric.set_value(self._session_failed)
        self._append_log(f"[PARALLEL-ERROR] {operation}: {message}")
        self._append_log(traceback_text.rstrip())
        self._add_history(operation, "Failed", 0, message)
        if operation == "album":
            self._set_parallel_album_status(thread, "Failed")
        self._save_workspace_state()
        QMessageBox.critical(self, f"{operation} failed", message)

    def _parallel_operation_cancelled(self, thread: QThread) -> None:
        operation = self._parallel_jobs.get(thread, ("Job", None))[0]
        self._append_log(f"[PARALLEL-CANCELLED] {operation}")
        self._add_history(operation, "Cancelled", 0, "Stopped by user")
        if operation == "album":
            self._set_parallel_album_status(thread, "Cancelled")
        self._save_workspace_state()

    def _clear_parallel_job(self, thread: QThread) -> None:
        self._parallel_jobs.pop(thread, ("", None))
        self._parallel_entry_names.pop(thread, None)
        self._sync_run_buttons()
        self._sync_stop_button()
        if self._active_thread is None and not self._parallel_jobs:
            self._set_idle_state("Completed")

    def _operation_phase_changed(self, label: str, total: int) -> None:
        """Start a separately timed phase instead of corrupting the prior ETA."""

        self._learn_eta_profile()
        phase = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_") or "next"
        self._active_eta_phase = phase
        self._active_progress_unit = self._progress_unit(
            self._active_operation_name, phase
        )
        self._active_eta_key = self._eta_profile_key(
            self._active_operation_name,
            self._active_operation_params,
            phase,
        )
        self._operation_started_at = time.monotonic()
        self._active_progress_current = 0
        self._active_progress_total = max(0, int(total))

    def _cancel_active_operation(self) -> None:
        if self._active_worker is None and not self._parallel_jobs:
            return
        self.cancel_button.setEnabled(False)
        self.activity_label.setText(
            running_operation_text(self._active_operation_name, "Cancelling safely")
        )
        if self._active_worker is not None:
            self._active_worker.cancel()
        for _operation, worker in self._parallel_jobs.values():
            worker.cancel()

    def _sync_stop_button(self) -> None:
        """Enable Stop exactly while at least one cancellable worker exists."""

        self.cancel_button.setEnabled(
            self._active_worker is not None or bool(self._parallel_jobs)
        )

    def _update_operation_progress(self, current: int, total: int, detail: str) -> None:
        self._active_progress_current = max(0, int(current))
        self._active_progress_total = max(0, int(total))
        if total > 0:
            self.activity_progress.setRange(0, total)
            self.activity_progress.setValue(max(0, min(current, total)))
            profile = self._eta_profiles.get(self._active_eta_key, {})
            learned_rate = profile.get("seconds_per_item")
            elapsed = max(0.0, time.monotonic() - self._operation_started_at)
            eta = estimate_eta_seconds(current, total, elapsed, learned_rate)
            self.activity_progress.setFormat(
                f"%v / %m {self._active_progress_unit}  ·  %p%  ·  ETA {format_eta(eta)}"
            )
        else:
            self.activity_progress.setRange(0, 0)
            self.activity_progress.setFormat("Working…")
        current_action = detail.strip() or "Working"
        self.activity_label.setText(
            running_operation_text(self._active_operation_name, current_action)
        )

    def _show_file_in_use_warning(self, path_text: str, action: str) -> None:
        path = Path(path_text)
        self.activity_label.setText(
            running_operation_text(
                self._active_operation_name,
                f"Waiting for file: {path.name}",
            )
        )
        QMessageBox.warning(
            self,
            "File is being used",
            f'"{path.name}" is open in another application while {action}.\n\n'
            "Stop playback or close the application using this file, then press OK. "
            "The operation will retry once. If the file is still locked, only this "
            "file will be skipped and the remaining files will continue.",
            QMessageBox.StandardButton.Ok,
        )
        worker = self._active_worker
        if worker is not None:
            worker.acknowledge_file_in_use()

    def _mark_batch_item_finished(self, item: str, successful: bool) -> None:
        self._mark_parallel_batch_item_finished(
            self._active_operation_name, item, successful
        )

    def _batch_editor_for_operation(
        self, operation: str
    ) -> JsonBatchEditor | None:
        editor = {
            "audio": getattr(self, "audio_input", None),
            "video": getattr(self, "video_input", None),
            "album": getattr(self, "album_input", None),
            "jukebox": getattr(self, "jukebox_input", None),
        }.get(operation)
        return editor if isinstance(editor, JsonBatchEditor) else None

    def _mark_parallel_batch_item_finished(
        self, operation: str, item: str, successful: bool
    ) -> None:
        editor = self._batch_editor_for_operation(operation)
        if editor is None:
            return
        editor.disable_completed(
            (item,) if successful else (),
            () if successful else (item,),
        )
        self._save_workspace_state()

    def _operation_finished(self, summary: dict[str, Any]) -> None:
        self._learn_eta_profile()
        self._session_completed += 1
        self.completed_metric.set_value(self._session_completed)
        self.dashboard_state_badge.setText("READY")
        self._set_idle_state("Completed")
        if getattr(self, "_ai_enabled_current", False) and not getattr(
            self, "_ai_invoked_current", False
        ):
            self._set_ai_status(
                "AI ENABLED · no model call was needed", active=True
            )
        self._handle_operation_output(summary)
        editor = self._batch_editor_for_operation(str(summary.get("operation", "")))
        if editor is not None:
            editor.disable_completed(
                summary.get("completed_items", ()),
                summary.get("failed_items", ()),
            )
        details = self._summary_text(summary)
        self._append_log(f"[COMPLETE] {details}")
        self._add_history(summary.get("operation", "Job"), "Completed", summary.get("total", 0), details)
        if self._active_operation_name == "album":
            status = "Partial" if int(summary.get("failed", 0) or 0) else "Completed"
            self._set_active_album_status(status)
        self._save_workspace_state()
        self.media_library.refresh_library()

    def _operation_failed(self, message: str, traceback_text: str) -> None:
        self._session_failed += 1
        self.failed_metric.set_value(self._session_failed)
        self.dashboard_state_badge.setText("ERROR")
        self._set_idle_state("Failed")
        self._append_log(f"[ERROR] {message}")
        self._append_log(traceback_text.rstrip())
        self._add_history("Current job", "Failed", 0, message)
        if self._active_operation_name == "album":
            self._set_active_album_status("Failed")
        self._save_workspace_state()
        QMessageBox.critical(self, "Operation failed", message)

    def _operation_cancelled(self) -> None:
        self.dashboard_state_badge.setText("READY")
        self._set_idle_state("Cancelled")
        self._append_log("[CANCELLED] Operation cancelled by user.")
        self._add_history("Current job", "Cancelled", 0, "Stopped by user")
        if self._active_operation_name == "album":
            self._set_active_album_status("Cancelled")
        self._save_workspace_state()

    def _set_active_album_status(self, status: str) -> None:
        for name in self._active_entry_names:
            self._album_statuses[name] = status
        self.album_input.set_statuses(self._album_statuses)

    def _set_parallel_album_status(self, thread: QThread, status: str) -> None:
        for name in self._parallel_entry_names.get(thread, []):
            self._album_statuses[name] = status
        self.album_input.set_statuses(self._album_statuses)

    def _set_idle_state(self, label: str) -> None:
        if self._parallel_jobs:
            self.activity_label.setText(
                f"{len(self._parallel_jobs)} parallel job(s) still running"
            )
            self.pulse_dot.setVisible(True)
            self.activity_progress.setRange(0, 0)
            self.activity_progress.setFormat("Working…")
            self._sync_stop_button()
            self._sync_run_buttons()
            return
        self.activity_label.setText(label)
        self.pulse_dot.setVisible(False)
        self.activity_progress.setRange(0, 100)
        self.activity_progress.setValue(100 if label == "Completed" else 0)
        self.activity_progress.setFormat(label if label != "Completed" else "Completed · 100%")
        self.cancel_button.setEnabled(False)
        self._sync_run_buttons()
        QTimer.singleShot(2500, self._reset_activity_label)

    def _reset_activity_label(self) -> None:
        if self._active_thread is None and not self._parallel_jobs:
            self.activity_label.setText("Idle")
            self.activity_progress.setValue(0)
            self.activity_progress.setFormat("")

    def _clear_worker_refs(self) -> None:
        self._active_worker = None
        self._active_thread = None
        self._sync_stop_button()
        self._sync_run_buttons()

    def _running_operations(self) -> set[str]:
        operations = {name for name, _worker in self._parallel_jobs.values()}
        if self._active_thread is not None and self._active_operation_name:
            operations.add(self._active_operation_name)
        return operations

    def _operation_is_running(self, operation: str) -> bool:
        return operation in self._running_operations()

    def _sync_run_buttons(self) -> None:
        running = self._running_operations()
        for operation, button in self._form_runs.items():
            button.setEnabled(operation not in running)
        self.dashboard_clear_button.setEnabled(not running)

    def _append_log(self, line: str) -> None:
        if not hasattr(self, "log_view"):
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {line}")
        self._update_ai_status_from_log(line)
        scroll = self.log_view.verticalScrollBar()
        scroll.setValue(scroll.maximum())

    def _show_ai_usage(self, operation: str, params: dict[str, Any]) -> None:
        usage = operation_ai_usage(operation, params)
        self._ai_enabled_current = usage.active
        self._ai_invoked_current = False
        self._set_ai_status(usage.badge_text, active=usage.active)

    def _set_ai_status(self, text: str, *, active: bool, review: bool = False) -> None:
        if not hasattr(self, "ai_status_badge"):
            return
        self.ai_status_badge.setText(text)
        self.ai_status_badge.setProperty("active", active)
        self.ai_status_badge.setProperty("review", review)
        style = self.ai_status_badge.style()
        style.unpolish(self.ai_status_badge)
        style.polish(self.ai_status_badge)

    def _update_ai_status_from_log(self, line: str) -> None:
        """Reflect actual model calls/results, including fallback and review."""

        text = str(line or "")
        if "[AI-PROVIDER-FALLBACK]" in text:
            self._ai_invoked_current = True
            self._set_ai_status(
                "AI PROVIDER FALLBACK · trying Ollama", active=True, review=True
            )
        elif "[AI-PROVIDER]" in text:
            self._ai_invoked_current = True
            detail = text.split("[AI-PROVIDER]", 1)[1].strip()
            self._set_ai_status(f"AI ACTIVE · {detail}", active=True)
        elif "[AI-STATIC-FALLBACK]" in text:
            self._ai_invoked_current = True
            self._set_ai_status(
                "STATIC FALLBACK · no AI provider available", active=False, review=True
            )
        elif "[AI-PREFLIGHT-FALLBACK]" in text:
            self._ai_invoked_current = True
            self._set_ai_status(
                "AI PREFLIGHT FALLBACK · explicit request preserved",
                active=False,
                review=True,
            )
        elif "[AI-PREFLIGHT-REVIEW]" in text:
            self._ai_invoked_current = True
            self._set_ai_status(
                "AI PREFLIGHT REVIEW · request preserved", active=True, review=True
            )
        elif "[AI-PREFLIGHT-VERIFIED]" in text:
            self._ai_invoked_current = True
            self._set_ai_status("AI PREFLIGHT VERIFIED", active=True)
        elif "[AI-PREFLIGHT-START]" in text:
            self._ai_invoked_current = True
            model = re.search(r"model=([^|]+)", text)
            suffix = f" · {model.group(1).strip()}" if model else ""
            self._set_ai_status(f"AI PREFLIGHT WORKING{suffix}", active=True)
        elif "[AI-NOT-USED]" in text:
            if getattr(self, "_ai_enabled_current", False) and self._active_thread is not None:
                return
            detail = text.split("[AI-NOT-USED]", 1)[1].strip()
            self._set_ai_status(f"AI NOT USED · {detail}", active=False)
        elif "[AI-FALLBACK]" in text:
            self._ai_invoked_current = True
            self._set_ai_status(
                "AI FALLBACK · local deterministic processing", active=False, review=True
            )
        elif "[AI-REVIEW]" in text or "[AGENT-REVIEW]" in text:
            self._ai_invoked_current = True
            self._set_ai_status(
                "AI REVIEW · no changes applied", active=True, review=True
            )
        elif "[AI-VERIFIED]" in text or "[AGENT-VERIFIED]" in text:
            self._ai_invoked_current = True
            confidence = re.search(r"(?:confidence[= ]|\()(\d{1,3}%)", text, re.I)
            suffix = f" · {confidence.group(1)}" if confidence else ""
            self._set_ai_status(f"AI VERIFIED{suffix}", active=True)
        elif "[AI-ENABLED]" in text:
            model = re.search(r"model=([^|]+)", text)
            suffix = f" · {model.group(1).strip()}" if model else ""
            self._set_ai_status(f"AI ENABLED{suffix}", active=True)
        elif "[AI-START]" in text or "[AGENT-PRE-MOVE]" in text:
            self._ai_invoked_current = True
            model = re.search(r"model=([^|]+)", text)
            suffix = f" · {model.group(1).strip()}" if model else ""
            self._set_ai_status(f"AI WORKING{suffix}", active=True)

    def _summary_text(self, summary: dict[str, Any]) -> str:
        parts = [f"items={summary.get('total', 0)}"]
        for key in (
            "downloaded", "moved", "deleted", "reordered", "tagged", "tracked",
            "skipped", "listed", "failed"
        ):
            value = int(summary.get(key, 0) or 0)
            if value:
                parts.append(f"{key}={value}")
        return ", ".join(parts)

    def _handle_operation_output(self, summary: dict[str, Any]) -> None:
        operation = summary.get("operation")
        output_text = str(summary.get("output_text", "") or "")
        output_path = str(summary.get("output_path", "") or "")
        if operation == "format_artists":
            self.artist_output.setText(output_text)
        elif operation == "parse_tracks":
            self.tracks_result.setPlainText(output_text)
        elif operation == "search_song":
            self._render_song_search(output_text)
        elif operation == "enrich_song":
            self._route_enriched_audio_song(output_text)
        if output_path:
            path = Path(output_path).expanduser().resolve()
            self._last_output_folder = str(path if path.is_dir() else path.parent)
            self.open_output_button.setEnabled(
                Path(self._last_output_folder).is_dir()
            )

    def _open_last_output(self) -> None:
        folder = Path(self._last_output_folder).expanduser()
        if not self._last_output_folder or not folder.is_dir():
            self.open_output_button.setEnabled(False)
            QMessageBox.warning(
                self,
                "Output folder unavailable",
                "The most recent output folder no longer exists.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def _add_history(self, operation: str, status: str, total: int, details: str) -> None:
        self._history.insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "operation": str(operation).replace("_", " ").title(),
            "status": status,
            "total": total,
            "details": details,
        })
        self._history = self._history[:30]
        self._refresh_history_table()

    def _refresh_history_table(self) -> None:
        self.history_table.setRowCount(len(self._history))
        for row, item in enumerate(self._history):
            values = [
                str(item.get("time", "")),
                str(item.get("operation", "")),
                str(item.get("status", "")),
                str(item.get("total", 0)),
                str(item.get("details", "")),
            ]
            for column, value in enumerate(values):
                self.history_table.setItem(row, column, QTableWidgetItem(value))

    def _clear_dashboard(self) -> None:
        if self._active_thread is not None or self._parallel_jobs:
            return
        self._session_jobs = 0
        self._session_completed = 0
        self._session_failed = 0
        self.jobs_metric.set_value(0)
        self.completed_metric.set_value(0)
        self.failed_metric.set_value(0)
        self._history.clear()
        self._refresh_history_table()
        self._save_workspace_state()

    # -------------------------------------------------------------- persistence
    def _load_eta_profiles(self) -> dict[str, dict[str, float | int]]:
        """Load validated learned throughput profiles from previous completed runs."""

        raw = str(self.settings.value("analytics/eta_profiles", "") or "")
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        profiles: dict[str, dict[str, float | int]] = {}
        for key, value in payload.items():
            if not isinstance(value, dict):
                continue
            try:
                rate = float(value.get("seconds_per_item", 0))
                samples = int(value.get("samples", 0))
            except (TypeError, ValueError):
                continue
            if rate > 0 and samples > 0:
                profiles[str(key)] = {
                    "seconds_per_item": rate,
                    "samples": samples,
                }
        return profiles

    @staticmethod
    def _eta_profile_key(
        operation: str, params: dict[str, Any], phase: str = "main"
    ) -> str:
        """Group runs only with workloads having similar concurrency and scope."""

        parts = [str(operation)]
        if phase != "main":
            parts.append(f"phase={phase}")
        parts.append(f"workers={int(params.get('workers', 1) or 1)}")
        if operation in {"audio", "video", "album", "jukebox"}:
            parts.extend(
                (
                    f"delay={int(params.get('min_delay', 0) or 0)}-"
                    f"{int(params.get('max_delay', 0) or 0)}",
                    f"overwrite={bool(params.get('overwrite', False))}",
                    f"quality={params.get('preferred_mp3_quality', '320')}",
                    f"sample-rate={params.get('audio_sample_rate', '44100')}",
                )
            )
        if operation == "album_metadata_enricher":
            parts.append(
                "source+destination" if params.get("destination_folder") else "source-only"
            )
        elif operation == "album_consolidator":
            parts.append(
                "all-destination"
                if params.get("enrich_all_destination")
                else "moved-only"
            )
        elif params.get("mode"):
            parts.append(f"mode={params['mode']}")
        if operation == "video":
            parts.extend(
                (
                    f"mp3={params.get('mp3_mode', 'audio-only')}",
                    f"resolution={params.get('resolution', 'best')}",
                    f"container={params.get('merge_format', 'mp4')}",
                )
            )
        return "|".join(parts)

    @staticmethod
    def _progress_unit(operation: str, phase: str) -> str:
        if "album_order" in phase or "wikipedia_album" in phase:
            return "albums"
        if operation in {"album_metadata_enricher", "album_consolidator"}:
            return "files"
        if operation in {"audio", "video"}:
            return "songs"
        if operation in {"album", "jukebox"}:
            return "tracks"
        return "items"

    def _learn_eta_profile(self) -> None:
        """Update an exponential moving average from one successfully completed run."""

        if not self._active_eta_key or self._operation_started_at <= 0:
            return
        total = max(self._active_progress_current, self._active_progress_total)
        elapsed = max(0.0, time.monotonic() - self._operation_started_at)
        if total <= 0 or elapsed <= 0:
            return
        observed_rate = elapsed / total
        previous = self._eta_profiles.get(self._active_eta_key, {})
        previous_rate = float(previous.get("seconds_per_item", observed_rate))
        samples = int(previous.get("samples", 0))
        alpha = 1.0 if samples == 0 else 0.35
        learned_rate = previous_rate * (1.0 - alpha) + observed_rate * alpha
        self._eta_profiles[self._active_eta_key] = {
            "seconds_per_item": learned_rate,
            "samples": min(samples + 1, 10000),
        }
        self.settings.setValue(
            "analytics/eta_profiles",
            json.dumps(self._eta_profiles, ensure_ascii=False),
        )
        self.settings.sync()

    def _default_value(self, key: str, fallback: int) -> int:
        try:
            return int(self.settings.value(f"defaults/{key}", fallback))
        except (TypeError, ValueError):
            return fallback

    def _setting_bool(self, key: str, fallback: bool) -> bool:
        value = self.settings.value(key, fallback)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"false", "0", "no", "off", ""}

    def _saved_secret(self, key: str, environment_name: str) -> str:
        """Return a saved credential, including an intentionally saved blank value."""

        saved_value = self.settings.value(key, None)
        if saved_value is not None:
            return str(saved_value or "").strip()
        return os.environ.get(environment_name, "").strip()

    def _agentic_model(self) -> str:
        """Return the selected provider model or Ollama fallback model."""

        saved_model = self.settings.value("defaults/agentic_model", None)
        return configured_primary_model(
            DEFAULT_OLLAMA_MODEL
            if saved_model is None
            else str(saved_model or "").strip()
        )

    def _active_ai_identity(self) -> tuple[str, str]:
        """Refresh saved provider settings and return the effective task identity."""

        self._configure_ai_from_settings()
        saved_model = self.settings.value("defaults/agentic_model", None)
        return configured_primary_identity(
            DEFAULT_OLLAMA_MODEL
            if saved_model is None
            else str(saved_model or "").strip()
        )

    def _configure_ai_from_settings(self) -> None:
        """Apply global provider settings before any workspace starts an AI task."""

        saved_ollama_model = self.settings.value("defaults/agentic_model", None)
        legacy_nvidia_key = self._saved_secret(
            "defaults/nvidia_api_key", NVIDIA_API_KEY_ENV
        )
        provider_id = str(self.settings.value("defaults/ai_provider", "") or "").strip()
        if not provider_id:
            provider_id = "nvidia" if legacy_nvidia_key else "ollama"
        definition = provider_definition(provider_id)
        api_key = str(
            self.settings.value(
                f"defaults/ai_providers/{definition.id}/api_key",
                legacy_nvidia_key if definition.id == "nvidia" else "",
            )
            or ""
        ).strip()
        legacy_nvidia_model = self.settings.value("defaults/nvidia_model", None)
        default_provider_model = (
            DEFAULT_NVIDIA_MODEL
            if definition.id == "nvidia" and legacy_nvidia_model is None
            else definition.default_model
        )
        provider_model = str(
            self.settings.value(
                f"defaults/ai_providers/{definition.id}/model",
                legacy_nvidia_model
                if definition.id == "nvidia" and legacy_nvidia_model is not None
                else default_provider_model,
            )
            or ""
        ).strip()
        base_url = str(
            self.settings.value(
                f"defaults/ai_providers/{definition.id}/base_url",
                definition.base_url,
            )
            or ""
        ).strip()
        ollama_model = (
            DEFAULT_OLLAMA_MODEL
            if saved_ollama_model is None
            else str(saved_ollama_model or "").strip()
        )
        configure_ai_environment(
            nvidia_api_key=api_key if definition.id == "nvidia" else "",
            nvidia_model=provider_model if definition.id == "nvidia" else "",
            ollama_model=ollama_model,
        )
        configure_agno_environment(
            provider=definition.id,
            api_key=api_key,
            model=provider_model,
            base_url=base_url,
        )
        configure_serpapi_environment(
            self._saved_secret("defaults/serpapi_api_key", SERPAPI_API_KEY_ENV)
        )

    def _preview_crystalness(self, value: int) -> None:
        self.settings_crystalness_value.setText(f"{value}%")
        self._crystal_preview_timer.start()

    def _apply_crystalness(self, value: int, *, persist: bool) -> None:
        level = max(0, min(100, int(value)))
        self.setStyleSheet(crystal_style(level))
        if persist:
            self.settings.setValue("defaults/crystalness", level)

    def _toggle_workspace_persistence(self, enabled: bool) -> None:
        self.settings.setValue("workspace/persist_enabled", enabled)
        if enabled:
            self._save_workspace_state()
        else:
            self.settings.remove("workspace/audio_data")
            self.settings.remove("workspace/song_search_text")
            self.settings.remove("workspace/song_search_model")
            self.settings.remove("workspace/video_data")
            self.settings.remove("workspace/album_data")
            self.settings.remove("workspace/jukebox_data")
            self.settings.remove("workspace/audio_output")
            self.settings.remove("workspace/video_output")
            self.settings.remove("workspace/video_audio_output")
            self.settings.remove("workspace/album_output")
            self.settings.remove("workspace/jukebox_output")
            self.settings.remove("workspace/track_reorder_folder")
            self.settings.remove("workspace/audio_trim_input")
            self.settings.remove("workspace/audio_trim_output")
            self.settings.remove("workspace/audio_trim_start")
            self.settings.remove("workspace/audio_trim_end")
            self.settings.remove("workspace/audio_trim_overwrite")
            self.settings.remove("workspace/redownload_input")
            self.settings.remove("workspace/redownload_output")
            self.settings.remove("workspace/redownload_url")
            self.settings.remove("workspace/redownload_start")
            self.settings.remove("workspace/redownload_end")
            self.settings.remove("workspace/redownload_content")
            self.settings.remove("workspace/redownload_overwrite")
            for key in (
                "edit_file_input", "edit_file_output", "edit_file_artwork", "edit_file_action",
                "edit_file_url", "edit_file_start", "edit_file_end", "edit_file_content",
                "edit_file_download_start", "edit_file_download_end",
                "edit_file_overwrite", "edit_file_metadata", "edit_file_remove_artwork",
                "edit_file_crop_ratio", "edit_file_aspect_ratio",
                "edit_album_folder", "edit_album_artwork", "edit_album_metadata",
                "edit_album_remove_artwork",
                "album_consolidator_source", "album_consolidator_destination",
                "album_enrich_destination_enabled",
                "album_move_perform_enrichment",
                "album_move_enrich_all_destination",
            ):
                self.settings.remove(f"workspace/{key}")
            self.settings.remove("workspace/album_statuses")
            self.settings.remove("workspace/history")
            self.settings.sync()

    @staticmethod
    def _reset_default_values() -> dict[str, Any]:
        return {
            "workers": machine_parallel_workers(),
            "min_delay": 10,
            "max_delay": 25,
            "retries": 3,
            "retry_wait": 60,
            "rate_limit_wait": 180,
            "audio_quality": "320",
            "sample_rate": "44100",
            "wikipedia_track_order": True,
            "ai_enabled": True,
            "ai_provider": "ollama",
            "crash_reports_enabled": False,
            "agentic_model": "",
            "nvidia_api_key": "",
            "nvidia_model": "",
            "serpapi_api_key": "",
            "search_suggestions": 10,
            "video_seek_seconds": 10,
            "remember_video_display_modes": False,
            "crystalness": 65,
        }

    @staticmethod
    def _write_reset_settings(settings: QSettings, values: dict[str, Any]) -> None:
        settings.clear()
        for key, value in values.items():
            if key == "crash_reports_enabled":
                continue
            settings.setValue(f"defaults/{key}", value)
        settings.setValue(
            "privacy/crash_reports_enabled", values["crash_reports_enabled"]
        )
        settings.setValue("workspace/persist_enabled", True)
        settings.sync()

    def _reset_app(self) -> None:
        """Restore defaults and clear every workspace without deleting media."""

        library_busy = any(
            getattr(self.media_library, name, None) is not None
            for name in (
                "_scanner_thread",
                "_search_thread",
                "_recommendation_thread",
                "_video_thumbnail_thread",
            )
        )
        if self._active_thread is not None or self._parallel_jobs or library_busy:
            QMessageBox.warning(
                self,
                "Reset unavailable",
                "Wait for every running or background operation to finish before resetting the app.",
            )
            return
        answer = QMessageBox.question(
            self,
            f"Reset {APP_DISPLAY_NAME}?",
            "This will remove saved AI-provider and SerpApi credentials and all model selections, "
            "restore global defaults, clear every tool form, library folder, status, "
            "and history, and return application storage to its default folder.\n\n"
            "Downloaded and edited media files will not be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._workspace_autosave.stop()
        values = self._reset_default_values()
        default_directory = default_data_directory().resolve()
        try:
            default_directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            default_directory = platform_data_directory().resolve()
            default_directory.mkdir(parents=True, exist_ok=True)
        original_settings = self.settings

        persist_signals = self.settings_persist_state.blockSignals(True)
        self.settings_persist_state.setChecked(False)
        self.settings_persist_state.blockSignals(persist_signals)
        self._reset_all_tool_forms()

        self._write_reset_settings(original_settings, values)
        default_settings_path = application_settings_file(default_directory).resolve()
        if Path(original_settings.fileName()).resolve() == default_settings_path:
            reset_settings = original_settings
        else:
            reset_settings = QSettings(
                str(default_settings_path),
                QSettings.Format.IniFormat,
            )
            self._write_reset_settings(reset_settings, values)
        save_data_directory_choice(default_directory)
        self.settings = reset_settings
        self.media_library.settings = reset_settings
        self.media_library.recommendation_ai_enabled.setChecked(
            bool(values["ai_enabled"])
        )
        self._data_directory = default_directory
        self._metadata_tracker_file = str(default_directory / "album_enrichment_tracker.json")

        self.settings_workers.setValue(int(values["workers"]))
        self.settings_min_delay.setValue(int(values["min_delay"]))
        self.settings_max_delay.setValue(int(values["max_delay"]))
        self.settings_retries.setValue(int(values["retries"]))
        self.settings_retry_wait.setValue(int(values["retry_wait"]))
        self.settings_rate_limit_wait.setValue(int(values["rate_limit_wait"]))
        self.settings_audio_quality.setCurrentText(str(values["audio_quality"]))
        self.settings_sample_rate.setCurrentText(str(values["sample_rate"]))
        self.settings_wikipedia_order.setChecked(bool(values["wikipedia_track_order"]))
        self.settings_ai_enabled.setChecked(bool(values["ai_enabled"]))
        self.settings_crash_reports.setChecked(bool(values["crash_reports_enabled"]))
        for checkbox in self._tool_ai_checks.values():
            checkbox.setChecked(bool(values["ai_enabled"]))
        self._ai_provider_drafts = {
            provider.id: {
                "api_key": "",
                "model": provider.default_model,
                "base_url": provider.base_url,
            }
            for provider in PROVIDERS
        }
        provider_signals = self.settings_ai_provider.blockSignals(True)
        self.settings_ai_provider.setCurrentIndex(
            self.settings_ai_provider.findData("ollama")
        )
        self.settings_ai_provider.blockSignals(provider_signals)
        self._active_ai_provider = "ollama"
        self._show_ai_provider_draft("ollama")
        self.settings_serpapi_api_key.clear()
        self.settings_agentic_model.setCurrentText("")
        self.settings_search_suggestions.setValue(int(values["search_suggestions"]))
        self.settings_video_seek_seconds.setValue(int(values["video_seek_seconds"]))
        self.settings_remember_video_display_modes.setChecked(
            bool(values["remember_video_display_modes"])
        )
        self.settings_crystalness.setValue(int(values["crystalness"]))
        self.settings_data_directory.set_text(default_directory)
        persist_signals = self.settings_persist_state.blockSignals(True)
        self.settings_persist_state.setChecked(True)
        self.settings_persist_state.blockSignals(persist_signals)

        configure_ai_environment(nvidia_api_key="", nvidia_model="", ollama_model="")
        configure_agno_environment(provider="ollama", api_key="", model="", base_url="")
        configure_serpapi_environment("")
        self.ai_status_badge.setText("AI READY · STATIC FALLBACK · no model configured")
        self.workers_metric.set_value(values["workers"])
        self._apply_crystalness(int(values["crystalness"]), persist=True)
        self.media_library.set_suggestion_limit(int(values["search_suggestions"]))
        self.media_library.set_video_seek_seconds(int(values["video_seek_seconds"]))
        self.media_library.set_remember_video_display_modes(
            bool(values["remember_video_display_modes"])
        )
        for editor in (self.audio_input, self.video_input, self.album_input, self.jukebox_input):
            editor.retry_attempts = int(values["retries"])
        self._eta_profiles.clear()
        self._save_workspace_state()
        self.settings.sync()
        self._workspace_autosave.start()
        self._append_log(
            f"[RESET] App defaults restored; data folder={default_directory}; "
            f"workers={values['workers']}"
        )
        QMessageBox.information(
            self,
            "Application reset",
            "All tool forms and saved settings were cleared. Global defaults are active.\n\n"
            f"Application data folder: {default_directory}",
        )

    def _reset_all_tool_forms(self) -> None:
        """Clear every workspace UI while preserving user media on disk."""

        self.song_search_text.clear()
        self.song_search_limit.setValue(8)
        self.song_search_understanding.setText(
            "Your interpreted title, artist, collection, and target workflow will appear here."
        )
        self.song_search_table.setRowCount(0)
        self.song_search_route.setCurrentIndex(0)
        self._song_search_results.clear()
        self._song_search_intent.clear()

        for editor in (self.audio_input, self.video_input, self.album_input, self.jukebox_input):
            editor.load_data({})
        self.audio_mode.setCurrentIndex(0)
        self.audio_output.set_text("")
        self.audio_overwrite.setChecked(False)
        self.video_mp3_mode.setCurrentIndex(0)
        self.video_output.set_text("")
        self.video_audio_output.set_text("")
        self.video_merge.setCurrentText("mp4")
        self.video_report.setChecked(True)
        self.video_overwrite.setChecked(False)
        self.album_output.set_text("")
        self.album_threshold.setValue(-35.0)
        self.album_silence.setValue(1.5)
        self.album_track_duration.setValue(45.0)
        self.album_padding.setValue(0.25)
        self.album_keep_temp.setChecked(False)
        self.album_report.setChecked(True)
        self.album_overwrite.setChecked(False)
        self.jukebox_output.set_text("")
        self.jukebox_keep_temp.setChecked(False)
        self.jukebox_report.setChecked(True)
        self.jukebox_overwrite.setChecked(False)
        self._album_statuses.clear()

        self._clear_track_reorder()
        self._clear_edit_file()
        self._clear_edit_album()
        self._clear_album_consolidator()
        self.album_enrich_force_recheck.setChecked(False)
        self.artist_input.clear()
        self.artist_output.clear()
        self.tracks_input_file.set_text("")
        self.tracks_text.clear()
        self.tracks_end_field.setCurrentIndex(0)
        self.tracks_unknown.setText("Unknown")
        self.tracks_keep_case.setChecked(False)
        self.tracks_output_path.set_text("")
        self.tracks_result.clear()
        self.media_library.reset_page()
        self.log_view.clear()
        self._clear_dashboard()
        self._last_output_folder = ""
        self.open_output_button.setEnabled(False)

    def _save_defaults(self) -> None:
        min_delay = self.settings_min_delay.value()
        max_delay = self.settings_max_delay.value()
        if min_delay > max_delay:
            QMessageBox.warning(self, "Invalid settings", "Minimum delay cannot exceed maximum delay.")
            return
        self._capture_ai_provider_draft()
        provider_id = self._active_ai_provider
        provider_draft = self._ai_provider_drafts[provider_id]
        values = {
            "workers": self.settings_workers.value(),
            "min_delay": min_delay,
            "max_delay": max_delay,
            "retries": self.settings_retries.value(),
            "retry_wait": self.settings_retry_wait.value(),
            "rate_limit_wait": self.settings_rate_limit_wait.value(),
            "audio_quality": self.settings_audio_quality.currentText(),
            "sample_rate": self.settings_sample_rate.currentText(),
            "wikipedia_track_order": self.settings_wikipedia_order.isChecked(),
            "ai_enabled": self.settings_ai_enabled.isChecked(),
            "agentic_model": self.settings_agentic_model.currentText().strip(),
            "ai_provider": provider_id,
            "serpapi_api_key": self.settings_serpapi_api_key.text().strip(),
            "search_suggestions": self.settings_search_suggestions.value(),
            "video_seek_seconds": self.settings_video_seek_seconds.value(),
            "remember_video_display_modes": (
                self.settings_remember_video_display_modes.isChecked()
            ),
            "crystalness": self.settings_crystalness.value(),
        }
        for key, value in values.items():
            self.settings.setValue(f"defaults/{key}", value)
        for saved_provider, draft in self._ai_provider_drafts.items():
            for field, value in draft.items():
                self.settings.setValue(
                    f"defaults/ai_providers/{saved_provider}/{field}", value
                )
        nvidia_draft = self._ai_provider_drafts["nvidia"]
        self.settings.setValue("defaults/nvidia_api_key", nvidia_draft["api_key"])
        self.settings.setValue("defaults/nvidia_model", nvidia_draft["model"])
        self.settings.setValue(
            "privacy/crash_reports_enabled",
            self.settings_crash_reports.isChecked(),
        )
        self.settings.sync()
        configure_ai_environment(
            nvidia_api_key=(
                provider_draft["api_key"] if provider_id == "nvidia" else ""
            ),
            nvidia_model=(provider_draft["model"] if provider_id == "nvidia" else ""),
            ollama_model=str(values["agentic_model"]),
        )
        configure_agno_environment(
            provider=provider_id,
            api_key=provider_draft["api_key"],
            model=provider_draft["model"],
            base_url=provider_draft["base_url"],
        )
        configure_serpapi_environment(str(values["serpapi_api_key"]))
        if not bool(values["ai_enabled"]):
            provider = "DISABLED"
            ready_model = "internet/deterministic mode"
        elif provider_id == "ollama" and str(values["agentic_model"]):
            provider = "OLLAMA"
            ready_model = str(values["agentic_model"])
        elif provider_draft["api_key"] or provider_id == "custom":
            provider = provider_definition(provider_id).label.upper()
            ready_model = provider_draft["model"]
        else:
            provider = "STATIC FALLBACK"
            ready_model = "no model configured"
        self.ai_status_badge.setText(f"AI READY · {provider} · {ready_model}")
        for editor in (
            self.audio_input,
            self.video_input,
            self.album_input,
            self.jukebox_input,
        ):
            editor.retry_attempts = values["retries"]
        self.workers_metric.set_value(values["workers"])
        self._apply_crystalness(int(values["crystalness"]), persist=True)
        self.media_library.set_suggestion_limit(int(values["search_suggestions"]))
        self.media_library.set_video_seek_seconds(int(values["video_seek_seconds"]))
        self.media_library.set_remember_video_display_modes(
            bool(values["remember_video_display_modes"])
        )
        storage_changed = self._save_data_directory()
        QMessageBox.information(
            self,
            "Global settings saved",
            "The new values will be used by every relevant workflow. "
            "Crash-report storage changes take effect the next time the app starts."
            + (
                "\n\nThe application data folder was changed. Restart the app "
                "to use the new location."
                if storage_changed
                else ""
            ),
        )

    def _open_data_directory(self) -> None:
        selected = self.settings_data_directory.text()
        if selected:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(selected))))

    def _save_data_directory(self) -> bool:
        """Copy current state and remember a newly selected folder for next start."""

        selected_text = self.settings_data_directory.text()
        if not selected_text or self._data_directory is None:
            return False
        selected = Path(os.path.expandvars(selected_text)).expanduser().resolve()
        if selected == self._data_directory:
            return False
        try:
            copy_application_data(self._data_directory, selected)
            save_data_directory_choice(selected)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Application data folder not changed",
                f"Could not prepare the selected folder:\n{exc}",
            )
            self.settings_data_directory.set_text(self._data_directory)
            return False
        return True

    def _restore_window_state(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def _restore_workspace_state(self) -> None:
        """Restore form drafts and run history from the previous app session."""
        if not self.settings_persist_state.isChecked():
            return
        try:
            editors = {
                "audio": self.audio_input,
                "video": self.video_input,
                "album": self.album_input,
                "jukebox": self.jukebox_input,
            }
            for name, editor in editors.items():
                raw = str(self.settings.value(f"workspace/{name}_data", "") or "")
                if raw:
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        editor.load_data(payload)

            self.song_search_text.setText(
                str(self.settings.value("workspace/song_search_text", "") or "")
            )
            path_fields = {
                "audio_output": self.audio_output,
                "video_output": self.video_output,
                "video_audio_output": self.video_audio_output,
                "album_output": self.album_output,
                "jukebox_output": self.jukebox_output,
                "track_reorder_folder": self.track_reorder_folder,
                "edit_file_input": self.edit_file_input,
                "edit_file_output": self.edit_file_output,
                "edit_file_artwork": self.edit_meta_artwork,
                "edit_album_folder": self.edit_album_folder,
                "edit_album_artwork": self.edit_album_artwork,
                "album_consolidator_source": self.album_consolidator_source,
                "album_consolidator_destination": self.album_consolidator_destination,
            }
            for name, picker in path_fields.items():
                value = str(self.settings.value(f"workspace/{name}", "") or "")
                if name == "track_reorder_folder" and value:
                    restored_folder = resolve_album_folder_successor(
                        Path(value).expanduser()
                    )
                    if not restored_folder.is_dir():
                        # Output folders can disappear between builds or after
                        # album consolidation. A stale saved draft is not a
                        # runtime warning and must not trigger the user-action
                        # loader connected to this field.
                        self.settings.remove("workspace/track_reorder_folder")
                        continue
                    value = str(restored_folder)
                    self.settings.setValue(
                        "workspace/track_reorder_folder", value
                    )
                if value:
                    picker.set_text(value)

            action_index = self.edit_file_action.findData(
                str(self.settings.value("workspace/edit_file_action", "metadata") or "metadata")
            )
            self.edit_file_action.setCurrentIndex(max(0, action_index))
            self.edit_file_url.setText(
                str(self.settings.value("workspace/edit_file_url", "") or "")
            )
            self.edit_file_download_start.setText(
                str(
                    self.settings.value(
                        "workspace/edit_file_download_start", "00:00"
                    )
                    or "00:00"
                )
            )
            self.edit_file_download_end.setText(
                str(
                    self.settings.value("workspace/edit_file_download_end", "")
                    or ""
                )
            )
            self.edit_file_start.setText(
                str(self.settings.value("workspace/edit_file_start", "00:00") or "00:00")
            )
            self.edit_file_end.setText(
                str(self.settings.value("workspace/edit_file_end", "") or "")
            )
            content_index = self.edit_file_content.findData(
                str(self.settings.value("workspace/edit_file_content", "auto") or "auto")
            )
            self.edit_file_content.setCurrentIndex(max(0, content_index))
            self.edit_file_mode.setCurrentIndex(
                1 if self._setting_bool("workspace/edit_file_overwrite", False) else 0
            )
            self.edit_file_crop_ratio.setCurrentText(
                str(
                    self.settings.value(
                        "workspace/edit_file_crop_ratio", "Default"
                    )
                    or "Default"
                )
            )
            self.edit_file_aspect_ratio.setCurrentText(
                str(
                    self.settings.value(
                        "workspace/edit_file_aspect_ratio", "Default"
                    )
                    or "Default"
                )
            )
            metadata_raw = str(self.settings.value("workspace/edit_file_metadata", "") or "")
            if metadata_raw:
                saved_metadata = json.loads(metadata_raw)
                metadata_widgets = {
                    "title": self.edit_meta_title, "album": self.edit_meta_album,
                    "artists": self.edit_meta_artists, "year": self.edit_meta_year,
                    "track_number": self.edit_meta_track,
                    "track_total": self.edit_meta_track_total,
                }
                for key, widget in metadata_widgets.items():
                    widget.setText(str(saved_metadata.get(key, "") or ""))
            self.edit_meta_remove_artwork.setChecked(
                self._setting_bool("workspace/edit_file_remove_artwork", False)
            )
            self.edit_album_remove_artwork.setChecked(
                self._setting_bool("workspace/edit_album_remove_artwork", False)
            )
            album_metadata_raw = str(
                self.settings.value("workspace/edit_album_metadata", "") or ""
            )
            if album_metadata_raw:
                album_metadata = json.loads(album_metadata_raw)
                self.edit_album_name.setText(str(album_metadata.get("album", "") or ""))
                self.edit_album_year.setText(str(album_metadata.get("year", "") or ""))
                self.edit_album_artist.setText(
                    str(
                        album_metadata.get("artists")
                        or album_metadata.get("album_artist")
                        or ""
                    )
                )
            self.album_enrich_destination_enabled.setChecked(
                self._setting_bool(
                    "workspace/album_enrich_destination_enabled", False
                )
            )
            self.album_move_enrich_all_destination.setChecked(
                self._setting_bool(
                    "workspace/album_move_enrich_all_destination", False
                )
            )
            self.album_move_perform_enrichment.setChecked(
                self._setting_bool(
                    "workspace/album_move_perform_enrichment", True
                )
            )
            self._edit_file_action_changed()
            statuses_raw = str(self.settings.value("workspace/album_statuses", "") or "")
            if statuses_raw:
                statuses = json.loads(statuses_raw)
                if isinstance(statuses, dict):
                    self._album_statuses = {str(k): str(v) for k, v in statuses.items()}
                    self.album_input.set_statuses(self._album_statuses)

            history_raw = str(self.settings.value("workspace/history", "") or "")
            if history_raw:
                history = json.loads(history_raw)
                if isinstance(history, list):
                    self._history = [item for item in history if isinstance(item, dict)][:30]
                    self._refresh_history_table()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._append_log(f"[WARNING] Could not restore saved workspace: {exc}")

    def _save_workspace_state(self) -> None:
        if not self.settings_persist_state.isChecked():
            return
        editors = {
            "audio": self.audio_input,
            "video": self.video_input,
            "album": self.album_input,
            "jukebox": self.jukebox_input,
        }
        for name, editor in editors.items():
            self.settings.setValue(
                f"workspace/{name}_data",
                json.dumps(editor.data(), ensure_ascii=False),
            )
        self.settings.setValue("workspace/song_search_text", self.song_search_text.text())
        path_fields = {
            "audio_output": self.audio_output,
            "video_output": self.video_output,
            "video_audio_output": self.video_audio_output,
            "album_output": self.album_output,
            "jukebox_output": self.jukebox_output,
            "track_reorder_folder": self.track_reorder_folder,
            "edit_file_input": self.edit_file_input,
            "edit_file_output": self.edit_file_output,
            "edit_file_artwork": self.edit_meta_artwork,
            "edit_album_folder": self.edit_album_folder,
            "edit_album_artwork": self.edit_album_artwork,
            "album_consolidator_source": self.album_consolidator_source,
            "album_consolidator_destination": self.album_consolidator_destination,
        }
        for name, picker in path_fields.items():
            self.settings.setValue(f"workspace/{name}", picker.text())
        self.settings.setValue(
            "workspace/album_enrich_destination_enabled",
            self.album_enrich_destination_enabled.isChecked(),
        )
        self.settings.setValue(
            "workspace/album_move_perform_enrichment",
            self.album_move_perform_enrichment.isChecked(),
        )
        self.settings.setValue(
            "workspace/album_move_enrich_all_destination",
            self.album_move_enrich_all_destination.isChecked(),
        )
        self.settings.setValue("workspace/edit_file_action", self.edit_file_action.currentData())
        self.settings.setValue("workspace/edit_file_url", self.edit_file_url.text())
        self.settings.setValue(
            "workspace/edit_file_download_start",
            self.edit_file_download_start.text(),
        )
        self.settings.setValue(
            "workspace/edit_file_download_end",
            self.edit_file_download_end.text(),
        )
        self.settings.setValue("workspace/edit_file_start", self.edit_file_start.text())
        self.settings.setValue("workspace/edit_file_end", self.edit_file_end.text())
        self.settings.setValue("workspace/edit_file_content", self.edit_file_content.currentData())
        self.settings.setValue(
            "workspace/edit_file_overwrite", bool(self.edit_file_mode.currentData())
        )
        self.settings.setValue(
            "workspace/edit_file_crop_ratio",
            self.edit_file_crop_ratio.currentText(),
        )
        self.settings.setValue(
            "workspace/edit_file_aspect_ratio",
            self.edit_file_aspect_ratio.currentText(),
        )
        self.settings.setValue(
            "workspace/edit_file_metadata",
            json.dumps(self._edit_file_params()["metadata"], ensure_ascii=False),
        )
        self.settings.setValue(
            "workspace/edit_file_remove_artwork", self.edit_meta_remove_artwork.isChecked()
        )
        self.settings.setValue(
            "workspace/edit_album_metadata",
            json.dumps(
                {
                    "album": self.edit_album_name.text(),
                    "year": self.edit_album_year.text(),
                    "artists": self.edit_album_artist.text(),
                },
                ensure_ascii=False,
            ),
        )
        self.settings.setValue(
            "workspace/edit_album_remove_artwork",
            self.edit_album_remove_artwork.isChecked(),
        )
        self.settings.setValue(
            "workspace/album_statuses", json.dumps(self._album_statuses, ensure_ascii=False)
        )
        self.settings.setValue("workspace/history", json.dumps(self._history, ensure_ascii=False))
        self.settings.sync()

    def _save_log(self) -> None:
        default = str(Path.cwd() / f"youtube_media_studio_{datetime.now():%Y%m%d_%H%M%S}.log")
        selected, _ = QFileDialog.getSaveFileName(self, "Save log", default, "Log files (*.log *.txt)")
        if not selected:
            return
        Path(selected).write_text(self.log_view.toPlainText() + "\n", encoding="utf-8")
        self._append_log(f"[SAVED] Log saved to: {selected}")

    def request_graceful_shutdown(self) -> None:
        """Close immediately on a console interrupt or shutdown request."""

        if self._active_worker is not None or self._parallel_jobs:
            self.close()
            return
        self._append_log("[SHUTDOWN] Console interrupt received; closing application.")
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self._blank_click_selection_filter)
        log_diagnostic(
            "WINDOW",
            f"closeEvent active_worker={self._active_worker is not None} "
            f"parallel_jobs={len(self._parallel_jobs)}",
        )
        self.media_library.shutdown()
        if self._active_worker is not None or self._parallel_jobs:
            self.settings.setValue("window/geometry", self.saveGeometry())
            self._save_workspace_state()
            if self._active_worker is not None:
                self._active_worker.cancellation_token.cancel()
                self._active_worker.acknowledge_file_in_use()
            for _operation, worker in self._parallel_jobs.values():
                worker.cancellation_token.cancel()
                worker.acknowledge_file_in_use()
            event.accept()
            self.hide()
            log_diagnostic(
                "SHUTDOWN",
                "Intentional forced exit while an operation worker was active",
            )
            os._exit(0)
        background_threads = []
        for editor in (self.audio_input, self.video_input, self.album_input, self.jukebox_input):
            background_threads.extend(editor.cancel_background_tasks())
        if background_threads:
            self.settings.setValue("window/geometry", self.saveGeometry())
            self._save_workspace_state()
            event.accept()
            self.hide()
            log_diagnostic(
                "SHUTDOWN",
                f"Intentional forced exit while {len(background_threads)} inline "
                "background task(s) were active",
            )
            os._exit(0)
        self.settings.setValue("window/geometry", self.saveGeometry())
        self._save_workspace_state()
        event.accept()
