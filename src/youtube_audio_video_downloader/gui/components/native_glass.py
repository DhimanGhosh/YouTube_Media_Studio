"""Windows compositor-backed glass without fading text or replacing native chrome."""

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
    """Prefer untinted Aero blur, with Desktop Acrylic as a compositor fallback."""
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
        backdrop = ctypes.c_int(1)  # Disable the system material's fixed opaque tint.
        extended = dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        applied = dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop))
        clear_blur = _set_clear_native_blur(dwm, hwnd, enabled)
        if enabled and not clear_blur:
            backdrop = ctypes.c_int(3)  # Desktop Acrylic fallback.
            applied = dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop))
        dark_mode = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_mode), ctypes.sizeof(dark_mode))
        return enabled and extended == 0 and (clear_blur or applied == 0)
    except (AttributeError, OSError, ValueError):
        return False


def _set_clear_native_blur(dwm, hwnd: int, enabled: bool) -> bool:
    """Enable alpha compositing and untinted native blur when the API is available."""
    from ctypes import wintypes

    class BlurBehind(ctypes.Structure):
        _fields_ = [("flags", wintypes.DWORD), ("enabled", wintypes.BOOL),
                    ("region", ctypes.c_void_p), ("transition", wintypes.BOOL)]

    class AccentPolicy(ctypes.Structure):
        _fields_ = [("state", ctypes.c_int), ("flags", ctypes.c_int),
                    ("color", ctypes.c_uint32), ("animation", ctypes.c_int)]

    class CompositionData(ctypes.Structure):
        _fields_ = [("attribute", ctypes.c_int), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]

    try:
        user = ctypes.WinDLL("user32")
        compose = user.SetWindowCompositionAttribute
        compose.argtypes = [wintypes.HWND, ctypes.POINTER(CompositionData)]
        compose.restype = wintypes.BOOL
        dwm.DwmEnableBlurBehindWindow.argtypes = [wintypes.HWND, ctypes.POINTER(BlurBehind)]
        dwm.DwmEnableBlurBehindWindow.restype = ctypes.c_long
        blur = BlurBehind(1, enabled, None, False)
        if dwm.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(blur)) != 0:
            return False
        # WCA_ACCENT_POLICY / ACCENT_ENABLE_BLURBEHIND. The UI supplies the
        # adjustable tint, avoiding Acrylic's fixed grey material at 100%.
        policy = AccentPolicy(3 if enabled else 0, 0, 0, 0)
        data = CompositionData(19, ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p), ctypes.sizeof(policy))
        return bool(compose(hwnd, ctypes.byref(data)))
    except (AttributeError, OSError, ValueError):
        return False
