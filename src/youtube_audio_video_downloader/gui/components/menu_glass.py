"""Give owned popup menus the same compositor and tint as the application shell."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QColor, QIcon, QPainter
from PyQt6.QtWidgets import QApplication, QMenu, QStyle, QWidget

from .native_glass import apply_native_glass, native_glass_supported, prepare_native_glass
from .theme import menu_glass_style


def visible_menu_icon(widget: QWidget, symbol: QStyle.StandardPixmap) -> QIcon:
    """Lighten monochrome system glyphs without recoloring folder/document icons."""
    original = widget.style().standardIcon(symbol)
    if symbol not in {
        QStyle.StandardPixmap.SP_MediaVolume, QStyle.StandardPixmap.SP_MediaPlay,
        QStyle.StandardPixmap.SP_MediaSkipForward, QStyle.StandardPixmap.SP_ArrowUp,
    }:
        return original
    icon = QIcon()
    for size in (16, 24, 32, 48, 64):
        pixmap = original.pixmap(size, size)
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor("#eef3ff"))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class MenuGlassController(QObject):
    """Style existing and dynamically created menus, including nested submenus."""

    def __init__(self, owner: QWidget, level: Callable[[], int]) -> None:
        super().__init__(owner)
        self.owner = owner
        self.level = level
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            event.type() in {QEvent.Type.Polish, QEvent.Type.Show}
            and isinstance(watched, QMenu)
            and self._owns(watched)
        ):
            self._apply(watched, showing=event.type() == QEvent.Type.Show)
        return False

    def _owns(self, menu: QMenu) -> bool:
        # QWidget.isAncestorOf stops at popup window boundaries; QObject
        # ownership also includes QMenu windows and their nested submenus.
        parent = menu.parent()
        while parent is not None:
            if parent is self.owner:
                return True
            parent = parent.parent()
        return False

    def _apply(self, menu: QMenu, *, showing: bool = False) -> None:
        prepare_native_glass(menu)
        level = max(0, min(100, int(self.level())))
        native = bool(level and native_glass_supported())
        if showing or menu.isVisible():
            native = apply_native_glass(menu, level)
        style = menu_glass_style(level, native_glass=native)
        if menu.styleSheet() != style:
            menu.setStyleSheet(style)

    def refresh(self) -> None:
        for menu in self.owner.findChildren(QMenu):
            self._apply(menu)
