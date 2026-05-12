from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QProgressBar, QFrame, QMessageBox,
)

from vrc_organizer.database.queries import Queries
from vrc_organizer.models.asset import Asset
from vrc_organizer.ui.chip_button import ChipToggleButton
from vrc_organizer.ui.flow_layout import FlowLayout

THUMB_SIZE = 200


class TagReviewerDialog(QDialog):
    """Review auto-assigned tags: confirm or reject each tag per asset."""

    review_complete = Signal()

    def __init__(self, queries: Queries, thumb_cache_dir: Path, parent=None):
        super().__init__(parent)
        self._queries = queries
        self._thumb_cache_dir = thumb_cache_dir
        self._assets: list[Asset] = []
        self._current_idx = 0
        self._chips: list[ChipToggleButton] = []

        self.setWindowTitle("Review Auto-Tags")
        self.resize(750, 550)
        self._setup_ui()
        self._load_assets()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Header
        header = QHBoxLayout()
        self._title_label = QLabel("Loading...")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        header.addWidget(self._title_label)
        header.addStretch()

        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedWidth(200)
        header.addWidget(self._progress_bar)

        skip_btn = QPushButton("Skip")
        skip_btn.setToolTip("Skip this asset without saving")
        skip_btn.clicked.connect(self._on_skip)
        header.addWidget(skip_btn)

        layout.addLayout(header)

        # Instruction
        self._instruction = QLabel(
            "Click a tag chip to reject it. Active (blue) = keep, inactive (gray) = remove."
        )
        self._instruction.setWordWrap(True)
        layout.addWidget(self._instruction)

        # Content area
        content = QHBoxLayout()

        # Thumbnail
        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self._thumb_label.setAlignment(Qt.AlignCenter)
        self._thumb_label.setStyleSheet(
            "background: #e2e8f0; border-radius: 4px;"
        )
        content.addWidget(self._thumb_label)

        # Right side: filename + tag chips
        right = QVBoxLayout()

        self._filename_label = QLabel("")
        fn_font = QFont()
        fn_font.setPointSize(12)
        self._filename_label.setFont(fn_font)
        self._filename_label.setWordWrap(True)
        right.addWidget(self._filename_label)

        self._filetype_label = QLabel("")
        self._filetype_label.setStyleSheet("color: #64748b;")
        right.addWidget(self._filetype_label)

        right.addSpacing(12)

        tag_label = QLabel("Tags:")
        tag_label.setStyleSheet("font-weight: bold;")
        right.addWidget(tag_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._chip_container = QWidget()
        self._chip_layout = FlowLayout(spacing=5)
        self._chip_container.setLayout(self._chip_layout)
        scroll.setWidget(self._chip_container)
        right.addWidget(scroll)

        content.addLayout(right)
        layout.addLayout(content)

        # Footer
        footer = QHBoxLayout()
        self._count_label = QLabel("")
        footer.addWidget(self._count_label)
        footer.addStretch()

        self._save_btn = QPushButton("Save && Next")
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._on_save_next)
        footer.addWidget(self._save_btn)

        done_btn = QPushButton("Done")
        done_btn.clicked.connect(self._on_done)
        footer.addWidget(done_btn)

        layout.addLayout(footer)

    def _load_assets(self):
        self._assets = self._queries.get_unreviewed_tagged_assets(limit=50)
        if not self._assets:
            self._title_label.setText("All reviewed!")
            self._instruction.setText(
                "No more assets with unreviewed tags. Import more assets or check back later."
            )
            self._progress_bar.setVisible(False)
            self._save_btn.setEnabled(False)
            return
        self._current_idx = 0
        self._progress_bar.setRange(0, len(self._assets))
        self._show_current()

    def _show_current(self):
        self._clear_chips()

        asset = self._assets[self._current_idx]
        self._title_label.setText(f"Asset {self._current_idx + 1} of {len(self._assets)}")
        self._progress_bar.setValue(self._current_idx)
        self._filename_label.setText(asset.filename)
        self._filetype_label.setText(f"{asset.filetype}  •  {asset.file_size:,} bytes")

        # Thumbnail
        thumb_path = self._thumb_cache_dir / f"{asset.id}.webp"
        pix = QPixmap(str(thumb_path))
        if not pix.isNull():
            scaled = pix.scaled(THUMB_SIZE, THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._thumb_label.setPixmap(scaled)
        else:
            self._thumb_label.setText("No preview")
            self._thumb_label.setPixmap(QPixmap())

        # Tag chips — all start active
        tags = self._queries.get_tags_for_asset(asset.id)
        self._count_label.setText(f"{len(tags)} tag(s)")
        for tid, name, color in tags:
            chip = ChipToggleButton(name)
            chip.setProperty("tag_id", tid)
            chip.set_active(True)
            self._chip_layout.addWidget(chip)
            self._chips.append(chip)

    def _clear_chips(self):
        while self._chip_layout.count():
            item = self._chip_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._chips.clear()

    def _on_skip(self):
        self._advance()

    def _on_save_next(self):
        asset = self._assets[self._current_idx]
        for chip in self._chips:
            tag_id = chip.property("tag_id")
            accepted = chip.isChecked()
            self._queries.save_tag_review(asset.id, tag_id, accepted)
            if not accepted:
                self._queries.remove_tag_from_asset(asset.id, tag_id)
        self._advance()

    def _advance(self):
        self._current_idx += 1
        if self._current_idx < len(self._assets):
            self._show_current()
        else:
            QMessageBox.information(
                self, "Review Complete",
                f"All {len(self._assets)} asset(s) have been reviewed.\n\n"
                "The review data will help improve auto-tagging precision."
            )
            self.review_complete.emit()
            self.accept()

    def _on_done(self):
        if self._current_idx < len(self._assets):
            remaining = len(self._assets) - self._current_idx
            reply = QMessageBox.question(
                self, "Quit Early?",
                f"{remaining} asset(s) remaining.\n\nSave progress and quit?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.review_complete.emit()
        self.accept()
