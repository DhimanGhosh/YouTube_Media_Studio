"""Exercise popup ownership, late submenus, and native-glass fallback."""

import os
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMenu, QWidget  # noqa: E402

from youtube_audio_video_downloader.gui.components.menu_glass import MenuGlassController  # noqa: E402


def test_late_submenus_follow_crystalness_and_keep_keyboard_actions():
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    level = [100]
    controller = MenuGlassController(owner, lambda: level[0])
    with patch("youtube_audio_video_downloader.gui.components.menu_glass.native_glass_supported", return_value=True), patch(
        "youtube_audio_video_downloader.gui.components.native_glass.native_glass_supported", return_value=True,
    ), patch("youtube_audio_video_downloader.gui.components.menu_glass.apply_native_glass",
             side_effect=lambda _menu, value: bool(value)) as apply:
        menu = QMenu(owner)
        submenu = menu.addMenu("More")
        selected = submenu.addAction("Selected action")
        triggered = Mock()
        selected.triggered.connect(triggered)
        submenu.popup(QPoint(10, 10))
        app.processEvents()
        assert submenu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert submenu.windowFlags() & Qt.WindowType.Popup
        apply.assert_any_call(submenu, 100)
        image = submenu.grab().toImage()
        clear_alpha = image.pixelColor(10, image.height() - 3).alpha()
        level[0] = 0
        controller.refresh()
        apply.assert_any_call(submenu, 0)
        image = submenu.grab().toImage()
        assert clear_alpha < image.pixelColor(10, image.height() - 3).alpha() == 255
        submenu.setActiveAction(selected)
        QTest.keyClick(submenu, Qt.Key.Key_Return)
        assert triggered.call_count == 1
        menu.close()
    owner.deleteLater()
    app.processEvents()


def test_unavailable_compositor_keeps_popup_opaque_and_other_windows_untouched():
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    other = QWidget()
    controller = MenuGlassController(owner, lambda: 100)
    with patch("youtube_audio_video_downloader.gui.components.menu_glass.native_glass_supported", return_value=False), patch(
        "youtube_audio_video_downloader.gui.components.menu_glass.apply_native_glass", return_value=False,
    ) as apply:
        menu = QMenu(owner)
        menu.addAction("Owned")
        unrelated = QMenu(other)
        unrelated.addAction("Unrelated")
        unrelated.ensurePolished()
        menu.popup(QPoint(10, 10))
        app.processEvents()
        image = menu.grab().toImage()
        assert image.pixelColor(10, image.height() - 3).alpha() == 255
        assert unrelated.styleSheet() == ""
        assert all(call.args[0] is menu for call in apply.call_args_list)
        controller.refresh()
        menu.close()
    owner.deleteLater()
    other.deleteLater()
    app.processEvents()
