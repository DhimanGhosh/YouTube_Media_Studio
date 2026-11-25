"""Windows compositor-backed Acrylic without fading text or replacing native chrome."""

from __future__ import annotations

import ctypes
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QWidget


def native_glass_supported() -> bool:
    app = QApplication.instance()
    return bool(
        sys.platform == "win32" and sys.getwindowsversion().build >= 22621
        and app is not None and app.platformName() == "windows"
    )


def prepare_native_glass(window: QWidget) -> None:
    """Give the compositor an alpha surface before the top-level window is shown."""
    if not native_glass_supported():
        return
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    window.setAutoFillBackground(False)
    palette = window.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
    window.setPalette(palette)


def apply_native_glass(window: QWidget, crystalness: int) -> bool:
    """Request Desktop Acrylic on supported Windows, respecting compositor fallback."""
    if not native_glass_supported():
        return False
    from ctypes import wintypes

    class Margins(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int) for name in ("left", "right", "top", "bottom")]

    try:
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.c_void_p]
        dwm.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
        hwnd = int(window.winId())
        enabled = int(crystalness) > 0
        margins = Margins(*([-1] * 4 if enabled else [0] * 4))
        backdrop = ctypes.c_int(3 if enabled else 1)  # DWMSBT_TRANSIENTWINDOW / NONE
        extended = dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        applied = dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop))
        dark_mode = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_mode), ctypes.sizeof(dark_mode))
        return enabled and extended == 0 and applied == 0
    except (AttributeError, OSError, ValueError):
        return False
