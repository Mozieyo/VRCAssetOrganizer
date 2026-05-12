from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPixmap, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QProgressBar, QFrame, QMessageBox,
)

from vrc_organizer.database.queries import Queries
from vrc_organizer.models.asset import Asset
from vrc_organizer.ui.flow_layout import FlowLayout

THUMB_SIZE = 160


def _extract_archive_image(filepath: Path, entry_name: str) -> bytes | None:
    """Extract image bytes from within an archive or loose image file."""
    suffix = filepath.suffix.lower()
    basename = entry_name.rsplit("/", 1)[-1] if "/" in entry_name else entry_name
    try:
        if suffix == ".zip":
            with zipfile.ZipFile(filepath, "r") as zf:
                try:
                    return zf.read(entry_name)
                except KeyError:
                    pass
                # Try by basename
                for info in zf.infolist():
                    if info.filename.endswith("/" + basename) or info.filename == basename:
                        return zf.read(info)
                # Try without first path component (some zips drop the top folder)
                if "/" in entry_name:
                    shorter = entry_name.split("/", 1)[1]
                    try:
                        return zf.read(shorter)
                    except KeyError:
                        pass
        elif suffix == ".unitypackage":
            try:
                with tarfile.open(filepath, "r:gz") as tf:
                    for member in tf.getmembers():
                        if member.isfile() and member.name.endswith("/" + basename):
                            f = tf.extractfile(member)
                            if f:
                                return f.read()
            except tarfile.ReadError:
                # Some .unitypackage files are actually zip format
                with zipfile.ZipFile(filepath, "r") as zf:
                    try:
                        return zf.read(entry_name)
                    except KeyError:
                        pass
                    for info in zf.infolist():
                        if info.filename.endswith("/" + basename):
                            return zf.read(info)
        elif suffix == ".rar":
            import rarfile
            with rarfile.RarFile(filepath, "r") as rf:
                try:
                    return rf.read(entry_name)
                except KeyError:
                    pass
                for info in rf.infolist():
                    if info.filename.endswith("/" + basename) or info.filename == basename:
                        return rf.read(info)
        elif suffix == ".7z":
            # 7z support via py7zr if available
            try:
                import py7zr
                with py7zr.SevenZipFile(filepath, "r") as szf:
                    szf.reset()
                    for name, bio in szf.read([entry_name]).items():
                        if bio is not None:
                            return bio.read()
                    # Try by basename
                    all_entries = szf.getnames()
                    for ename in all_entries:
                        if ename.endswith("/" + basename) or ename == basename:
                            for rname, bio in szf.read([ename]).items():
                                if bio is not None:
                                    return bio.read()
            except ImportError:
                pass
    except Exception:
        return None
    return None


def _get_image_entries(asset: Asset, queries: Queries) -> list[str]:
    """Return entry names for all images in an asset's scan results."""
    results = queries.get_scan_results(asset.id)
    return [name for name, etype, _ in results if etype == "image"]


class _ImageCard(QFrame):
    clicked = Signal(str)  # entry_name

    def __init__(self, entry_name: str, pixmap: QPixmap | None, parent=None):
        super().__init__(parent)
        self._entry_name = entry_name
        self.setFixedSize(THUMB_SIZE + 16, THUMB_SIZE + 40)
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Plain)
        self._selected = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        if pixmap and not pixmap.isNull():
            img = QLabel()
            scaled = pixmap.scaled(THUMB_SIZE, THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            img.setPixmap(scaled)
            img.setAlignment(Qt.AlignCenter)
            layout.addWidget(img)
        else:
            placeholder = QLabel("?")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setFixedSize(THUMB_SIZE, THUMB_SIZE)
            placeholder.setStyleSheet("background: #e2e8f0; border-radius: 4px;")
            layout.addWidget(placeholder)

        basename = entry_name.rsplit("/", 1)[-1] if "/" in entry_name else entry_name
        label = QLabel(basename)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        font = QFont()
        font.setPixelSize(10)
        label.setFont(font)
        layout.addWidget(label)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._entry_name)
        super().mousePressEvent(event)

    def set_selected(self, sel: bool):
        self._selected = sel
        if sel:
            self.setStyleSheet("border: 3px solid #3b82f6; background: #dbeafe;")
        else:
            self.setStyleSheet("border: 1px solid #cbd5e1; background: transparent;")


class CoverTrainerDialog(QDialog):
    """Training jig for labeling the best cover image per asset."""

    training_complete = Signal()

    def __init__(self, queries: Queries, parent=None):
        super().__init__(parent)
        self._queries = queries
        self._assets: list[Asset] = []
        self._current_idx = 0
        self._selected_entry: str | None = None
        self._cards: list[_ImageCard] = []

        self.setWindowTitle("Cover Image Trainer")
        self.resize(900, 650)
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
        skip_btn.setToolTip("Skip this asset — no good cover image")
        skip_btn.clicked.connect(self._on_skip)
        header.addWidget(skip_btn)

        self._renew_btn = QPushButton("Renew")
        self._renew_btn.setToolTip("Load fresh batch of 15 assets")
        self._renew_btn.clicked.connect(self._load_assets)
        self._renew_btn.setVisible(False)
        header.addWidget(self._renew_btn)

        layout.addLayout(header)

        # Instruction
        self._instruction = QLabel(
            "Click the image that best represents this asset as its cover/thumbnail.\n"
            "Space = Save & Next   |   Esc = Close"
        )
        self._instruction.setWordWrap(True)
        layout.addWidget(self._instruction)

        # Image grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._grid_container = QWidget()
        self._grid_layout = FlowLayout(spacing=8)
        self._grid_container.setLayout(self._grid_layout)
        scroll.setWidget(self._grid_container)
        layout.addWidget(scroll)

        # Footer
        footer = QHBoxLayout()
        self._asset_info = QLabel("")
        footer.addWidget(self._asset_info)
        footer.addStretch()

        self._save_btn = QPushButton("Save && Next")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save_next)
        footer.addWidget(self._save_btn)

        done_btn = QPushButton("Done")
        done_btn.clicked.connect(self._on_done)
        footer.addWidget(done_btn)

        layout.addLayout(footer)

    def _load_assets(self):
        self._assets = self._queries.get_trainable_assets(limit=15)
        if not self._assets:
            self._title_label.setText("All done!")
            self._instruction.setText(
                "No more assets with unlabeled images. Import more assets or check back later."
            )
            self._progress_bar.setVisible(False)
            self._save_btn.setEnabled(False)
            self._renew_btn.setVisible(False)
            return
        self._current_idx = 0
        self._progress_bar.setRange(0, len(self._assets))
        self._renew_btn.setVisible(True)
        self._show_current()

    def _show_current(self):
        self._selected_entry = None
        self._save_btn.setEnabled(False)

        # Clear cards
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._cards.clear()

        asset = self._assets[self._current_idx]
        self._title_label.setText(f"Asset {self._current_idx + 1} of {len(self._assets)}")
        self._progress_bar.setValue(self._current_idx)
        self._asset_info.setText(f"{asset.filename}  •  {asset.filetype}")

        entries = _get_image_entries(asset, self._queries)
        if not entries:
            # No images — show message and let user skip
            no_img = QLabel("(No extractable images found for this asset)")
            no_img.setStyleSheet("color: #94a3b8; font-style: italic;")
            no_img.setAlignment(Qt.AlignCenter)
            self._grid_layout.addWidget(no_img)
            return

        for entry_name in entries:
            data = _extract_archive_image(asset.filepath, entry_name)
            pix = QPixmap()
            if data:
                pix.loadFromData(data)
            card = _ImageCard(entry_name, pix if not pix.isNull() else None)
            card.clicked.connect(self._on_card_clicked)
            self._grid_layout.addWidget(card)
            self._cards.append(card)

    def _on_card_clicked(self, entry_name: str):
        self._selected_entry = entry_name
        for card in self._cards:
            card.set_selected(card._entry_name == entry_name)
        self._save_btn.setEnabled(True)

    def _on_skip(self):
        self._advance()

    def _on_save_next(self):
        if self._selected_entry is None:
            return
        asset = self._assets[self._current_idx]
        self._queries.save_cover_label(asset.id, self._selected_entry)
        self._advance()

    def _advance(self):
        self._current_idx += 1
        if self._current_idx < len(self._assets):
            self._show_current()
        else:
            QMessageBox.information(
                self, "Training Complete",
                f"All {len(self._assets)} asset(s) have been reviewed.\n\n"
                "Thank you! The labeled data will help improve cover detection."
            )
            self.training_complete.emit()
            self.accept()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Space:
            self._on_save_next()
        elif event.key() == Qt.Key_Escape:
            self._on_done()
        else:
            super().keyPressEvent(event)

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
        self.training_complete.emit()
        self.accept()
