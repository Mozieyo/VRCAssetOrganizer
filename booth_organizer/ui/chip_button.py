from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton


class ChipToggleButton(QPushButton):
    """Toggleable tag chip with optional exclusive-group (radio-like) behavior."""

    toggled_on = Signal(str)   # chip text emitted on activation

    ACTIVE_STYLE = (
        "QPushButton { background: #3b82f6; color: white; border: none; "
        "border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: bold; }"
        "QPushButton:hover { background: #2563eb; }"
    )
    INACTIVE_STYLE = (
        "QPushButton { background: #e2e8f0; color: #334155; border: 1px solid #cbd5e1; "
        "border-radius: 10px; padding: 3px 10px; font-size: 11px; }"
        "QPushButton:hover { background: #cbd5e1; }"
    )

    def __init__(self, text: str, exclusive_group: str = "", parent=None):
        super().__init__(text, parent)
        self._exclusive_group = exclusive_group
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._apply_style(False)
        self.toggled.connect(self._on_toggled)

    def _apply_style(self, active: bool):
        self.setStyleSheet(self.ACTIVE_STYLE if active else self.INACTIVE_STYLE)

    def _on_toggled(self, checked: bool):
        self._apply_style(checked)
        if checked:
            if self._exclusive_group:
                self._uncheck_siblings()
            self.toggled_on.emit(self.text())

    def _uncheck_siblings(self):
        """Deselect other chips in the same exclusive group."""
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
        """Passthrough for convenience — chip.blockSignals(True) before batch changes."""
        super().blockSignals(block)
