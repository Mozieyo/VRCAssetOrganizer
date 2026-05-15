from __future__ import annotations

from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt


def _set_title_bar_dark(dark: bool):
    """Set Windows 11 dark/light title bar via DWM API."""
    import ctypes
    app = QApplication.instance()
    if app is None:
        return
    window = app.activeWindow()
    if window is None:
        return
    hwnd = int(window.winId())
    try:
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(ctypes.c_int(dark)), ctypes.sizeof(ctypes.c_int)
        )
    except Exception:
        pass


# Global stylesheet applied on top of palette
DARK_STYLESHEET = """
QMainWindow, QDialog {
    background: #0f172a;
}
QScrollArea {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: #1e293b;
    width: 8px;
    border-radius: 4px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #475569;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #64748b;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: #1e293b;
    height: 8px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: #475569;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #64748b;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QLineEdit {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 3px;
    padding: 6px 10px;
    color: #e2e8f0;
    selection-background-color: #3b82f6;
}
QLineEdit:focus {
    border-color: #3b82f6;
}
QLineEdit:disabled {
    background: #0f172a;
    color: #64748b;
}
QPushButton {
    background: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 3px;
    padding: 6px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background: #334155;
    border-color: #475569;
}
QPushButton:pressed {
    background: #0f172a;
}
QPushButton:disabled {
    background: #0f172a;
    color: #475569;
    border-color: #1e293b;
}
QLabel {
    color: #e2e8f0;
}
QGroupBox {
    color: #94a3b8;
    border: 1px solid #334155;
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QTreeWidget {
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 3px;
    color: #e2e8f0;
}
QTreeWidget::item {
    padding: 4px;
}
QTreeWidget::item:hover {
    background: #1e293b;
}
QTreeWidget::item:selected {
    background: #1e3a5f;
    color: white;
}
QHeaderView::section {
    background: #1e293b;
    color: #94a3b8;
    border: none;
    border-bottom: 1px solid #334155;
    padding: 6px;
    font-weight: 600;
}
QMenuBar {
    background: #0f172a;
    color: #e2e8f0;
    border-bottom: 1px solid #1e293b;
}
QMenuBar::item:selected {
    background: #1e293b;
}
QMenu {
    background: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 3px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 24px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #334155;
}
QMenu::separator {
    height: 1px;
    background: #334155;
    margin: 4px 8px;
}
QToolBar {
    background: #0f172a;
    border: none;
    spacing: 4px;
    padding: 4px;
}
QStatusBar {
    background: #0f172a;
    color: #94a3b8;
    border-top: 1px solid #1e293b;
}
QDockWidget {
    color: #e2e8f0;
    titlebar-close-icon: none;
}
QDockWidget::title {
    background: #1e293b;
    padding: 8px;
    font-weight: 600;
}
QProgressBar {
    background: #1e293b;
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #3b82f6, stop:1 #8b5cf6);
    border-radius: 4px;
}
QSlider::groove:horizontal {
    background: #1e293b;
    height: 6px;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #3b82f6;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 4px;
}
QSlider::handle:horizontal:hover {
    background: #60a5fa;
}
QCheckBox {
    color: #e2e8f0;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #475569;
    background: #1e293b;
}
QCheckBox::indicator:checked {
    background: #3b82f6;
    border-color: #3b82f6;
}
QSplitter::handle {
    background: #334155;
}
QSplitter::handle:horizontal {
    width: 2px;
}
QSplitter::handle:vertical {
    height: 2px;
}
QTabWidget::pane {
    border: 1px solid #334155;
    border-radius: 3px;
    background: #0f172a;
}
QTabBar::tab {
    background: #1e293b;
    color: #94a3b8;
    padding: 8px 16px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #0f172a;
    color: #e2e8f0;
}
QTabBar::tab:hover:!selected {
    background: #334155;
}
QMessageBox {
    background: #1e293b;
}
QMessageBox QLabel {
    color: #e2e8f0;
}
"""

LIGHT_STYLESHEET = """
QWidget {
    color: #0f172a;
}
QMainWindow, QDialog {
    background: #f1f5f9;
    color: #0f172a;
}
QLabel {
    color: #0f172a;
}
QScrollArea {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: #e2e8f0;
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #94a3b8;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #64748b;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QLineEdit {
    background: white;
    border: 1px solid #94a3b8;
    border-radius: 3px;
    padding: 6px 10px;
    color: #0f172a;
}
QLineEdit:focus {
    border-color: #2563eb;
}
QLineEdit:disabled {
    background: #e2e8f0;
    color: #64748b;
}
QPushButton {
    background: white;
    color: #0f172a;
    border: 1px solid #94a3b8;
    border-radius: 3px;
    padding: 6px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background: #e2e8f0;
    border-color: #64748b;
}
QPushButton:pressed {
    background: #cbd5e1;
}
QPushButton:disabled {
    background: #e2e8f0;
    color: #94a3b8;
    border-color: #cbd5e1;
}
QGroupBox {
    color: #334155;
    border: 1px solid #94a3b8;
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: 700;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #0f172a;
}
QTreeWidget, QTreeView {
    background: white;
    border: 1px solid #94a3b8;
    border-radius: 3px;
    color: #0f172a;
}
QTreeWidget::item, QTreeView::item {
    padding: 4px;
    color: #0f172a;
}
QTreeWidget::item:hover, QTreeView::item:hover {
    background: #e2e8f0;
}
QTreeWidget::item:selected, QTreeView::item:selected {
    background: #bfdbfe;
    color: #0c1e3a;
}
QHeaderView::section {
    background: #e2e8f0;
    color: #0f172a;
    border: none;
    border-bottom: 1px solid #94a3b8;
    padding: 6px;
    font-weight: 700;
}
QMenuBar {
    background: #e2e8f0;
    color: #0f172a;
    border-bottom: 1px solid #94a3b8;
}
QMenuBar::item:selected {
    background: #cbd5e1;
}
QMenu {
    background: white;
    color: #0f172a;
    border: 1px solid #94a3b8;
    border-radius: 3px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 24px;
    border-radius: 4px;
    color: #0f172a;
}
QMenu::item:selected {
    background: #e2e8f0;
}
QMenu::separator {
    height: 1px;
    background: #cbd5e1;
    margin: 4px 8px;
}
QToolBar {
    background: #e2e8f0;
    border: none;
    spacing: 4px;
    padding: 4px;
}
QStatusBar {
    background: #e2e8f0;
    color: #0f172a;
    border-top: 1px solid #94a3b8;
}
QDockWidget {
    color: #0f172a;
}
QDockWidget::title {
    background: #e2e8f0;
    color: #0f172a;
    padding: 8px;
    font-weight: 700;
}
QProgressBar {
    background: #cbd5e1;
    border: none;
    border-radius: 4px;
    height: 6px;
    color: #0f172a;
}
QProgressBar::chunk {
    background: #2563eb;
    border-radius: 4px;
}
QSlider::groove:horizontal {
    background: #cbd5e1;
    height: 6px;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #2563eb;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 4px;
}
QSplitter::handle {
    background: #94a3b8;
}
QCheckBox {
    color: #0f172a;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #64748b;
    background: white;
}
QCheckBox::indicator:checked {
    background: #2563eb;
    border-color: #2563eb;
}
"""


class ThemeManager:
    def __init__(self):
        self._dark = False

    @property
    def is_dark(self) -> bool:
        return self._dark

    def apply_light(self):
        self._dark = False
        app = QApplication.instance()
        if app is None:
            return
        app.setStyle("Fusion")
        p = QPalette()

        # Slate-900 text on slate-100 background for solid AA contrast.
        p.setColor(QPalette.Window, QColor(241, 245, 249))
        p.setColor(QPalette.WindowText, QColor(15, 23, 42))
        p.setColor(QPalette.Base, QColor(255, 255, 255))
        p.setColor(QPalette.AlternateBase, QColor(226, 232, 240))
        p.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
        p.setColor(QPalette.ToolTipText, QColor(15, 23, 42))
        p.setColor(QPalette.Text, QColor(15, 23, 42))
        p.setColor(QPalette.Button, QColor(255, 255, 255))
        p.setColor(QPalette.ButtonText, QColor(15, 23, 42))
        p.setColor(QPalette.BrightText, QColor(190, 30, 30))
        p.setColor(QPalette.Link, QColor(37, 99, 235))
        p.setColor(QPalette.Highlight, QColor(37, 99, 235))
        p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        p.setColor(QPalette.PlaceholderText, QColor(100, 116, 139))
        p.setColor(QPalette.Midlight, QColor(226, 232, 240))
        p.setColor(QPalette.Mid, QColor(148, 163, 184))

        p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(148, 163, 184))
        p.setColor(QPalette.Disabled, QPalette.Text, QColor(148, 163, 184))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(148, 163, 184))

        app.setPalette(p)
        app.setStyleSheet(LIGHT_STYLESHEET)
        _set_title_bar_dark(False)

    def apply_dark(self):
        self._dark = True
        app = QApplication.instance()
        if app is None:
            return
        app.setStyle("Fusion")
        p = QPalette()

        p.setColor(QPalette.Window, QColor(15, 23, 42))
        p.setColor(QPalette.WindowText, QColor(226, 232, 240))
        p.setColor(QPalette.Base, QColor(30, 41, 59))
        p.setColor(QPalette.AlternateBase, QColor(51, 65, 85))
        p.setColor(QPalette.ToolTipBase, QColor(30, 41, 59))
        p.setColor(QPalette.ToolTipText, QColor(226, 232, 240))
        p.setColor(QPalette.Text, QColor(226, 232, 240))
        p.setColor(QPalette.Button, QColor(30, 41, 59))
        p.setColor(QPalette.ButtonText, QColor(226, 232, 240))
        p.setColor(QPalette.BrightText, QColor(248, 113, 113))
        p.setColor(QPalette.Link, QColor(96, 165, 250))
        p.setColor(QPalette.Highlight, QColor(59, 130, 246))
        p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        p.setColor(QPalette.PlaceholderText, QColor(100, 116, 139))
        p.setColor(QPalette.Midlight, QColor(30, 41, 59))

        p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(100, 116, 139))
        p.setColor(QPalette.Disabled, QPalette.Text, QColor(100, 116, 139))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(100, 116, 139))

        app.setPalette(p)
        app.setStyleSheet(DARK_STYLESHEET)
        _set_title_bar_dark(True)

    def toggle(self):
        if self._dark:
            self.apply_light()
        else:
            self.apply_dark()
