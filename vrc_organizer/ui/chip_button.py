from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QPushButton, QApplication


class ChipToggleButton(QPushButton):
    """Toggleable tag chip with optional exclusive-group (radio-like) behavior."""

    toggled_on = Signal(str)

    def __init__(self, text: str, exclusive_group: str = "", parent=None):
        super().__init__(text, parent)
        self._exclusive_group = exclusive_group
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(26)
        self._apply_style(False)
        self.toggled.connect(self._on_toggled)

    @staticmethod
    def _is_dark() -> bool:
        app = QApplication.instance()
        if app:
            return app.palette().color(QPalette.Window).lightness() < 128
        return False

    def _apply_style(self, active: bool):
        if active:
            self.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #3b82f6, stop:1 #2563eb);
                    color: white; border: none;
                    border-radius: 12px; padding: 4px 12px;
                    font-size: 11px; font-weight: 600;
                }
                QPushButton:hover { background: #1d4ed8; }
                QPushButton:pressed { background: #1e40af; }
            """)
        elif self._is_dark():
            self.setStyleSheet("""
                QPushButton {
                    background: #1e293b; color: #94a3b8;
                    border: 1px solid #334155;
                    border-radius: 12px; padding: 4px 12px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background: #334155; color: #e2e8f0;
                    border-color: #475569;
                }
                QPushButton:pressed { background: #0f172a; }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background: #f1f5f9; color: #475569;
                    border: 1px solid #e2e8f0;
                    border-radius: 12px; padding: 4px 12px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background: #e2e8f0; color: #1e293b;
                    border-color: #cbd5e1;
                }
                QPushButton:pressed { background: #cbd5e1; }
            """)

    def _on_toggled(self, checked: bool):
        self._apply_style(checked)
        if checked:
            if self._exclusive_group:
                self._uncheck_siblings()
            self.toggled_on.emit(self.text())

    def _uncheck_siblings(self):
        p = self.parent()
        if p is None:
            return
        for child in p.children():
            if child is self or not isinstance(child, ChipToggleButton):
                continue
            if child._exclusive_group == self._exclusive_group and child.isChecked():
                child.blockSignals(True)
                child.setChecked(False)
                child._apply_style(False)
                child.blockSignals(False)

    def set_active(self, active: bool):
        self.blockSignals(True)
        self.setChecked(active)
        self._apply_style(active)
        self.blockSignals(False)

    def blockSignals(self, block: bool):
        super().blockSignals(block)
