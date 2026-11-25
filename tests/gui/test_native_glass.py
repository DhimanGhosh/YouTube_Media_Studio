"""Check compositor fallback and preserve opaque controls when enabling glass."""
from __future__ import annotations

import os
import ctypes
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from youtube_audio_video_downloader.gui.components import native_glass  # noqa: E402
from youtube_audio_video_downloader.gui.components.theme import crystal_style  # noqa: E402


def test_transparency_progresses_continuously_from_zero_to_one_hundred():
    import re
    for native in (False, True):
        alphas = [int(re.search(r"QWidget#settingsSurface \{ background: rgba\(9, 14, 29, (\d+)\)",
                               crystal_style(level, native_glass=native)).group(1)) for level in range(101)]
        assert alphas[0] == 255
        assert all(0 <= left - right <= 3 for left, right in zip(alphas, alphas[1:]))
        assert alphas[-1] == (2 if native else 152)


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


def test_clear_blur_uses_untinted_policy_and_disables_cleanly():
    dwm = Mock()
    dwm.DwmEnableBlurBehindWindow.return_value = 0
    user = Mock()
    policies = []
    def compose(hwnd, data):
        assert hwnd == 0x123456789
        values = data._obj
        assert values.attribute == 19
        policy = (ctypes.c_int * 4).from_address(values.data)
        policies.append(tuple(policy))
        return True
    user.SetWindowCompositionAttribute.side_effect = compose
    with patch.object(native_glass.ctypes, "WinDLL", return_value=user, create=True):
        assert native_glass._set_clear_native_blur(dwm, 0x123456789, True)
        assert native_glass._set_clear_native_blur(dwm, 0x123456789, False)
    assert policies == [(3, 0, 0, 0), (0, 0, 0, 0)]


def test_native_glass_requests_acrylic_and_can_disable_it():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    dwm = Mock()
    dwm.DwmExtendFrameIntoClientArea.return_value = 0
    dwm.DwmSetWindowAttribute.return_value = 0
    with patch.object(native_glass, "native_glass_supported", return_value=True), patch.object(
        native_glass.ctypes, "WinDLL", return_value=dwm, create=True,
    ), patch.object(native_glass, "_set_clear_native_blur", return_value=False):
        assert native_glass.apply_native_glass(window, 65)
        assert dwm.DwmSetWindowAttribute.call_args_list[0].args[1] == 38
        assert dwm.DwmSetWindowAttribute.call_args_list[1].args[2]._obj.value == 3
        dwm.reset_mock()
        assert not native_glass.apply_native_glass(window, 0)
        assert dwm.DwmSetWindowAttribute.call_args_list[0].args[2]._obj.value == 1
        dwm.DwmSetWindowAttribute.return_value = -1
        assert not native_glass.apply_native_glass(window, 65)
    window.close()
    assert app is not None
