"""Check compositor fallback and preserve opaque controls when enabling glass."""
from __future__ import annotations

import os
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from youtube_audio_video_downloader.gui.components import native_glass  # noqa: E402


def test_native_glass_preserves_frame_and_text_opacity():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    flags = window.windowFlags()
    with patch.object(native_glass, "native_glass_supported", return_value=True):
        native_glass.prepare_native_glass(window)
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert window.windowFlags() == flags
    assert window.windowOpacity() == 1.0
    window.close()
    assert app is not None


def test_native_glass_requests_acrylic_and_can_disable_it():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    dwm = Mock()
    dwm.DwmExtendFrameIntoClientArea.return_value = 0
    dwm.DwmSetWindowAttribute.return_value = 0
    with patch.object(native_glass, "native_glass_supported", return_value=True), patch.object(
        native_glass.ctypes, "WinDLL", return_value=dwm, create=True,
    ):
        assert native_glass.apply_native_glass(window, 65)
        assert dwm.DwmSetWindowAttribute.call_args_list[0].args[1] == 38
        assert dwm.DwmSetWindowAttribute.call_args_list[0].args[2]._obj.value == 3
        dwm.reset_mock()
        assert not native_glass.apply_native_glass(window, 0)
        assert dwm.DwmSetWindowAttribute.call_args_list[0].args[2]._obj.value == 1
        dwm.DwmSetWindowAttribute.return_value = -1
        assert not native_glass.apply_native_glass(window, 65)
    window.close()
    assert app is not None
