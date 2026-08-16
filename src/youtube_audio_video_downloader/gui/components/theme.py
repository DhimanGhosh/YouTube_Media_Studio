"""Shared visual theme for the PyQt6 desktop application."""

from __future__ import annotations

APP_STYLE = r"""
* {
    font-family: "Segoe UI", "Inter", sans-serif;
    color: #eef3ff;
    outline: none;
}

QMainWindow, QWidget#rootWindow {
    background: transparent;
}

QWidget#windowShell {
    background: rgba(9, 14, 29, 218);
    border: none;
    border-radius: 0;
}

QLabel#appTitle {
    font-size: 15px;
    font-weight: 700;
    color: #ffffff;
}

QLabel#appSubtitle, QLabel#mutedLabel {
    color: rgba(220, 229, 248, 165);
    font-size: 12px;
}

QLabel#appVersionLabel {
    color: rgba(220, 229, 248, 150);
    font-size: 11px;
    padding: 3px 0 0 0;
}

QPushButton#windowButton {
    background: rgba(255, 255, 255, 10);
    border: 1px solid rgba(255, 255, 255, 12);
    border-radius: 9px;
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    font-size: 15px;
}

QPushButton#windowButton:hover {
    background: rgba(255, 255, 255, 28);
}

QPushButton#closeButton {
    background: rgba(255, 255, 255, 10);
    border: 1px solid rgba(255, 255, 255, 12);
    border-radius: 9px;
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    font-size: 14px;
}

QPushButton#closeButton:hover {
    background: rgba(255, 76, 103, 190);
    border-color: rgba(255, 120, 140, 210);
}

QWidget#sidebar {
    background: rgba(255, 255, 255, 10);
    border: 1px solid rgba(255, 255, 255, 20);
    border-radius: 20px;
}

QPushButton#navButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 13px;
    text-align: left;
    padding: 11px 14px;
    color: rgba(224, 232, 249, 180);
    font-size: 13px;
    font-weight: 600;
}

QPushButton#navButton:hover {
    background: rgba(255, 255, 255, 12);
    color: #ffffff;
}

QPushButton#navButton:checked {
    background: rgba(107, 135, 255, 48);
    border: 1px solid rgba(164, 183, 255, 82);
    color: #ffffff;
}

QFrame#glassCard, QWidget#glassCard {
    background: rgba(255, 255, 255, 14);
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 18px;
}

QFrame#heroCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(96, 126, 255, 58),
        stop:0.55 rgba(173, 104, 255, 36),
        stop:1 rgba(44, 214, 200, 30));
    border: 1px solid rgba(190, 205, 255, 58);
    border-radius: 22px;
}

QLabel#pageTitle {
    font-size: 28px;
    font-weight: 800;
    color: #ffffff;
}

QLabel#sectionTitle {
    font-size: 15px;
    font-weight: 700;
    color: #ffffff;
}

QLabel#heroMetric {
    font-size: 24px;
    font-weight: 800;
    color: #ffffff;
}

QLabel#statusBadge {
    background: rgba(104, 230, 188, 34);
    border: 1px solid rgba(104, 230, 188, 90);
    border-radius: 10px;
    padding: 5px 10px;
    color: #9cf3d3;
    font-size: 11px;
    font-weight: 700;
}

QLabel#warningBadge {
    background: rgba(255, 188, 85, 30);
    border: 1px solid rgba(255, 188, 85, 88);
    border-radius: 10px;
    padding: 5px 10px;
    color: #ffd18a;
    font-size: 11px;
    font-weight: 700;
}

QLabel#aiStatusBadge {
    background: rgba(126, 143, 180, 24);
    border: 1px solid rgba(170, 184, 215, 58);
    border-radius: 9px;
    padding: 4px 9px;
    color: rgba(220, 229, 248, 190);
    font-size: 10px;
    font-weight: 700;
}

QLabel#aiStatusBadge[active="true"] {
    background: rgba(114, 103, 255, 34);
    border-color: rgba(151, 140, 255, 105);
    color: #c8c2ff;
}

QLabel#aiStatusBadge[review="true"] {
    background: rgba(255, 188, 85, 30);
    border-color: rgba(255, 188, 85, 88);
    color: #ffd18a;
}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {
    background: rgba(4, 9, 21, 125);
    border: 1px solid rgba(255, 255, 255, 32);
    border-radius: 11px;
    padding: 9px 11px;
    selection-background-color: rgba(112, 140, 255, 180);
    color: #f5f7ff;
}

QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover,
QTextEdit:hover, QPlainTextEdit:hover {
    border-color: rgba(171, 190, 255, 72);
}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QTextEdit:focus, QPlainTextEdit:focus {
    border: 1px solid rgba(130, 157, 255, 155);
    background: rgba(8, 14, 31, 170);
}

QComboBox::drop-down {
    border: none;
    width: 28px;
}

QComboBox QAbstractItemView {
    background: #11182b;
    border: 1px solid rgba(255, 255, 255, 35);
    selection-background-color: rgba(108, 137, 255, 120);
    padding: 5px;
}

QCheckBox {
    spacing: 8px;
    color: rgba(235, 240, 255, 215);
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 6px;
    border: 1px solid rgba(255, 255, 255, 55);
    background: rgba(2, 8, 19, 100);
}

QCheckBox::indicator:checked {
    background: rgba(109, 139, 255, 220);
    border-color: rgba(185, 199, 255, 220);
}

QPushButton#primaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(94, 124, 255, 235),
        stop:1 rgba(144, 93, 255, 235));
    border: 1px solid rgba(205, 214, 255, 100);
    border-radius: 12px;
    padding: 10px 18px;
    min-height: 18px;
    color: #ffffff;
    font-weight: 700;
}

QPushButton#primaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(112, 141, 255, 255),
        stop:1 rgba(160, 112, 255, 255));
}

QPushButton#primaryButton:disabled {
    background: rgba(255, 255, 255, 18);
    border-color: rgba(255, 255, 255, 20);
    color: rgba(255, 255, 255, 75);
}

QPushButton#secondaryButton, QToolButton#secondaryButton {
    background: rgba(255, 255, 255, 13);
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 11px;
    padding: 9px 13px;
    color: rgba(240, 244, 255, 220);
    font-weight: 600;
}

QPushButton#secondaryButton:hover, QToolButton#secondaryButton:hover {
    background: rgba(255, 255, 255, 25);
    border-color: rgba(190, 205, 255, 65);
}

QPushButton#dangerButton {
    background: rgba(255, 85, 112, 30);
    border: 1px solid rgba(255, 102, 128, 72);
    border-radius: 11px;
    padding: 9px 14px;
    color: #ffafbd;
    font-weight: 700;
}

QPushButton#dangerButton:hover {
    background: rgba(255, 85, 112, 55);
}

QPushButton#dangerButton:disabled {
    background: rgba(255, 255, 255, 8);
    border-color: rgba(255, 255, 255, 14);
    color: rgba(220, 229, 248, 65);
}

QProgressBar {
    background: rgba(255, 255, 255, 12);
    border: 1px solid rgba(255, 255, 255, 24);
    border-radius: 7px;
    min-height: 18px;
    max-height: 18px;
    text-align: center;
    color: rgba(238, 243, 255, 225);
    font-size: 10px;
    font-weight: 700;
}

QProgressBar::chunk {
    border-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(95, 132, 255, 235),
        stop:0.55 rgba(155, 95, 255, 235),
        stop:1 rgba(51, 221, 194, 235));
}

QTabWidget::pane {
    border: 1px solid rgba(255, 255, 255, 24);
    border-radius: 14px;
    background: rgba(255, 255, 255, 7);
    top: -1px;
}

QTabBar::tab {
    background: transparent;
    color: rgba(223, 230, 247, 155);
    padding: 9px 15px;
    border-bottom: 2px solid transparent;
}

QTabBar::tab:selected {
    color: #ffffff;
    border-bottom: 2px solid rgba(124, 150, 255, 230);
}

QScrollArea {
    border: none;
    background: transparent;
}

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 4px 0;
}

QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 35);
    border-radius: 5px;
    min-height: 40px;
}

QScrollBar::handle:vertical:hover {
    background: rgba(255, 255, 255, 62);
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
    height: 0;
}

QTableWidget, QListWidget {
    background: transparent;
    border: 1px solid rgba(255, 255, 255, 20);
    border-radius: 10px;
    gridline-color: rgba(255, 255, 255, 16);
    alternate-background-color: rgba(255, 255, 255, 5);
}

QListWidget::item {
    border-radius: 8px;
    padding: 5px;
}

QListWidget::item:selected {
    background: rgba(108, 137, 255, 105);
}

QSlider::groove:horizontal {
    height: 6px;
    background: rgba(255, 255, 255, 25);
    border-radius: 3px;
}

QSlider::sub-page:horizontal {
    background: rgba(112, 140, 255, 210);
    border-radius: 3px;
}

QSlider::handle:horizontal {
    width: 16px;
    margin: -5px 0;
    background: #dce4ff;
    border: 2px solid rgba(112, 140, 255, 230);
    border-radius: 8px;
}

QFrame#playerCard {
    background: rgba(18, 26, 48, 225);
    border: 1px solid rgba(135, 158, 255, 70);
}

QLabel#nowPlayingArtwork {
    background: rgba(3, 8, 20, 115);
    border: 1px solid rgba(255, 255, 255, 28);
    border-radius: 12px;
    padding: 3px;
}

QPushButton#playerControlButton, QPushButton#playerPrimaryButton {
    padding: 0;
    border-radius: 14px;
    font-size: 19px;
    font-weight: 700;
}

QPushButton#playerControlButton {
    background: rgba(255, 255, 255, 18);
    border: 1px solid rgba(255, 255, 255, 35);
}

QPushButton#playerControlButton:hover {
    background: rgba(255, 255, 255, 36);
    border-color: rgba(164, 183, 255, 95);
}

QPushButton#playerPrimaryButton {
    background: rgba(108, 137, 255, 235);
    border: 1px solid rgba(205, 214, 255, 130);
    font-size: 22px;
}

QPushButton#playerPrimaryButton:hover {
    background: rgba(132, 157, 255, 255);
}

QPushButton#playerModeButton {
    min-height: 40px;
    padding: 0 13px;
    border-radius: 12px;
    background: rgba(255, 255, 255, 14);
    border: 1px solid rgba(255, 255, 255, 32);
    color: rgba(235, 240, 255, 205);
    font-size: 11px;
    font-weight: 700;
}

QPushButton#playerModeButton:hover {
    background: rgba(255, 255, 255, 30);
    border-color: rgba(164, 183, 255, 90);
    color: #ffffff;
}

QPushButton#playerModeButton:checked,
QPushButton#playerModeButton[active="true"] {
    background: rgba(108, 137, 255, 125);
    border-color: rgba(180, 196, 255, 150);
    color: #ffffff;
}

QHeaderView::section {
    background: rgba(255, 255, 255, 10);
    border: none;
    border-bottom: 1px solid rgba(255, 255, 255, 25);
    padding: 8px;
    color: rgba(224, 232, 248, 170);
    font-weight: 700;
}

QToolTip {
    background: #11182b;
    color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 40);
    padding: 6px;
}
"""


def crystal_style(crystalness: int = 65) -> str:
    """Return the base theme with a live, intensity-adjustable glass treatment."""

    level = max(0, min(100, int(crystalness)))
    shell_alpha = round(242 - level * 0.9)
    card_alpha = round(7 + level * 0.19)
    border_alpha = round(18 + level * 0.72)
    sidebar_alpha = round(8 + level * 0.12)
    highlight_alpha = round(18 + level * 0.42)
    return APP_STYLE + f"""
QWidget#windowShell {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(20, 29, 55, {min(245, shell_alpha + 8)}),
        stop:0.45 rgba(9, 14, 29, {shell_alpha}),
        stop:1 rgba(8, 22, 34, {max(120, shell_alpha - 8)}));
    border-color: rgba(220, 230, 255, {border_alpha});
}}
QWidget#sidebar {{
    background: rgba(255, 255, 255, {sidebar_alpha});
    border-color: rgba(220, 230, 255, {border_alpha});
}}
QFrame#glassCard, QWidget#glassCard {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(235, 242, 255, {min(90, card_alpha + 20)}),
        stop:0.18 rgba(185, 205, 255, {card_alpha}),
        stop:0.7 rgba(255, 255, 255, {max(5, card_alpha - 5)}),
        stop:1 rgba(120, 220, 235, {card_alpha}));
    border-color: rgba(220, 230, 255, {border_alpha});
}}
QFrame#glassCard:hover, QWidget#glassCard:hover {{
    background: rgba(205, 220, 255, {highlight_alpha});
    border-color: rgba(225, 234, 255, {min(150, border_alpha + 28)});
}}
QFrame#playerCard {{
    background: rgba(22, 31, 56, {min(238, shell_alpha + 10)});
    border-color: rgba(170, 190, 255, {min(180, border_alpha + 35)});
}}
"""
