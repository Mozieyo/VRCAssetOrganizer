from __future__ import annotations

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QPaintEvent,
)
from PySide6.QtWidgets import QWidget


class DropOverlay(QWidget):
    """Translucent overlay shown during drag operations. Covers the parent widget."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAutoFillBackground(False)
        self.hide()

    def show_overlay(self):
        if self.parent():
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()

    def hide_overlay(self):
        self.hide()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Semi-transparent backdrop
        bg = QColor(0, 0, 0, 100)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(bg))
        painter.drawRect(self.rect())

        # Dashed rounded rectangle inset from edges
        margin = 40
        inset = QRect(margin, margin,
                      self.width() - margin * 2,
                      self.height() - margin * 2)
        if inset.width() < 100 or inset.height() < 80:
            return

        pen = QPen(QColor(255, 255, 255, 180), 3, Qt.DashLine)
        pen.setDashPattern([12, 8])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(inset, 20, 20)

        # "Drop files here"
        font = QFont()
        font.setPixelSize(32)
        font.setBold(True)
        painter.setFont(font)
        fm = QFontMetrics(font)
        text = "Drop files here"
        text_rect = fm.boundingRect(text)

        tx = (self.width() - text_rect.width()) // 2
        ty = (self.height() - text_rect.height()) // 2 - text_rect.height() // 2

        # Subtle shadow
        painter.setPen(QColor(0, 0, 0, 80))
        painter.drawText(tx + 2, ty + 2, text)
        # Main text
        painter.setPen(QColor(255, 255, 255, 230))
        painter.drawText(tx, ty, text)

        # Subtitle hint
        sub_font = QFont()
        sub_font.setPixelSize(14)
        painter.setFont(sub_font)
        sub_text = "or use File > Import"
        sub_fm = QFontMetrics(sub_font)
        sub_rect = sub_fm.boundingRect(sub_text)
        sx = (self.width() - sub_rect.width()) // 2
        sy = ty + text_rect.height() + 12
        painter.setPen(QColor(255, 255, 255, 140))
        painter.drawText(sx, sy, sub_text)
