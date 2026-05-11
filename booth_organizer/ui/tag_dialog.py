from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QLabel, QColorDialog,
    QDialogButtonBox, QMessageBox,
)

from booth_organizer.database.queries import Queries

PRESET_COLORS = [
    "#ef4444", "#f97316", "#eab308", "#22c55e", "#14b8a6",
    "#3b82f6", "#6366f1", "#8b5cf6", "#ec4899", "#6b7280",
]


class TagDialog(QDialog):
    tags_changed = Signal()

    def __init__(self, queries: Queries, parent=None):
        super().__init__(parent)
        self._queries = queries
        self.setWindowTitle("Manage Tags")
        self.setMinimumSize(400, 350)
        self._setup_ui()
        self._load_tags()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Create new tag
        create_layout = QHBoxLayout()
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("New tag name...")
        create_layout.addWidget(self._name_input)

        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(32, 32)
        self._color_btn.setStyleSheet(
            f"QPushButton {{ background: {PRESET_COLORS[0]}; border: 1px solid #ccc; border-radius: 4px; }}"
        )
        self._color_btn.clicked.connect(self._pick_color)
        create_layout.addWidget(self._color_btn)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_tag)
        create_layout.addWidget(add_btn)
        layout.addLayout(create_layout)

        # Tag list
        layout.addWidget(QLabel("Existing tags:"))
        self._list = QListWidget()
        layout.addWidget(self._list)

        # Delete button
        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._delete_tag)
        layout.addWidget(delete_btn)

        # Close
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _pick_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self._color_btn.setStyleSheet(
                f"QPushButton {{ background: {color.name()}; "
                f"border: 1px solid #ccc; border-radius: 4px; }}"
            )

    def _add_tag(self):
        name = self._name_input.text().strip()
        if not name:
            return
        color = self._color_btn.styleSheet().split("background: ")[1].split(";")[0]
        self._queries.create_tag(name, color)
        self._name_input.clear()
        self._load_tags()
        self.tags_changed.emit()

    def _delete_tag(self):
        item = self._list.currentItem()
        if item is None:
            return
        tag_id = item.data(Qt.UserRole)
        reply = QMessageBox.question(
            self, "Delete Tag", f"Delete tag '{item.text()}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._queries.delete_tag(tag_id)
            self._load_tags()
            self.tags_changed.emit()

    def _load_tags(self):
        self._list.clear()
        for tag_id, name, color, count in self._queries.get_all_tags():
            item = QListWidgetItem(f"{name}  ({count})")
            item.setData(Qt.UserRole, tag_id)
            # Color dot
            pixmap = self._color_dot(color)
            item.setIcon(pixmap)
            self._list.addItem(item)

    @staticmethod
    def _color_dot(hex_color: str):
        from PySide6.QtGui import QPixmap, QPainter, QBrush, QColor, QPen
        pix = QPixmap(16, 16)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor(hex_color)))
        painter.setPen(QPen(QColor(0, 0, 0, 40), 1))
        painter.drawEllipse(2, 2, 12, 12)
        painter.end()
        return pix
