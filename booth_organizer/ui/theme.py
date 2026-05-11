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

        # Base colors
        p.setColor(QPalette.Window, QColor(248, 248, 248))
        p.setColor(QPalette.WindowText, QColor(28, 28, 28))
        p.setColor(QPalette.Base, QColor(255, 255, 255))
        p.setColor(QPalette.AlternateBase, QColor(243, 243, 243))
        p.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
        p.setColor(QPalette.ToolTipText, QColor(28, 28, 28))
        p.setColor(QPalette.Text, QColor(28, 28, 28))
        p.setColor(QPalette.Button, QColor(240, 240, 240))
        p.setColor(QPalette.ButtonText, QColor(28, 28, 28))
        p.setColor(QPalette.BrightText, QColor(220, 50, 50))
        p.setColor(QPalette.Link, QColor(37, 99, 235))
        p.setColor(QPalette.Highlight, QColor(99, 102, 241))
        p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        p.setColor(QPalette.PlaceholderText, QColor(160, 160, 160))

        # Disabled colors
        p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(160, 160, 160))
        p.setColor(QPalette.Disabled, QPalette.Text, QColor(160, 160, 160))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(160, 160, 160))
        p.setColor(QPalette.Disabled, QPalette.HighlightedText, QColor(200, 200, 200))

        app.setPalette(p)
        _set_title_bar_dark(False)

    def apply_dark(self):
        self._dark = True
        app = QApplication.instance()
        if app is None:
            return
        app.setStyle("Fusion")
        p = QPalette()

        p.setColor(QPalette.Window, QColor(30, 30, 30))
        p.setColor(QPalette.WindowText, QColor(228, 228, 228))
        p.setColor(QPalette.Base, QColor(38, 38, 38))
        p.setColor(QPalette.AlternateBase, QColor(45, 45, 45))
        p.setColor(QPalette.ToolTipBase, QColor(45, 45, 45))
        p.setColor(QPalette.ToolTipText, QColor(228, 228, 228))
        p.setColor(QPalette.Text, QColor(228, 228, 228))
        p.setColor(QPalette.Button, QColor(45, 45, 45))
        p.setColor(QPalette.ButtonText, QColor(228, 228, 228))
        p.setColor(QPalette.BrightText, QColor(255, 80, 80))
        p.setColor(QPalette.Link, QColor(96, 165, 250))
        p.setColor(QPalette.Highlight, QColor(99, 102, 241))
        p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        p.setColor(QPalette.PlaceholderText, QColor(128, 128, 128))

        p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(110, 110, 110))
        p.setColor(QPalette.Disabled, QPalette.Text, QColor(110, 110, 110))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(110, 110, 110))
        p.setColor(QPalette.Disabled, QPalette.HighlightedText, QColor(160, 160, 160))

        app.setPalette(p)
        _set_title_bar_dark(True)

    def toggle(self):
        if self._dark:
            self.apply_light()
        else:
            self.apply_dark()
