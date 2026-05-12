from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPixmap, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QProgressBar, QFrame, QMessageBox,
    QLineEdit, QListWidget, QListWidgetItem, QApplication,
)

from vrc_organizer.database.queries import Queries
from vrc_organizer.models.asset import Asset
from vrc_organizer.tag_data import TAG_HIERARCHY
from vrc_organizer.ui.chip_button import ChipToggleButton
from vrc_organizer.ui.flow_layout import FlowLayout

THUMB_SIZE = 200


class _SearchOverlay(QFrame):
    """Dropdown list of search results positioned below the search bar."""

    tag_selected = Signal(int, str)  # tag_id, tag_name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Plain)
        self._list = QListWidget(self)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._list)
        self.setMaximumHeight(220)
        self.setMinimumWidth(200)

    def set_results(self, results: list[tuple[int, str, str, int]]):
        self._list.clear()
        if not results:
            self.hide()
            return
        for tid, name, color, count in results:
            item = QListWidgetItem(f"{name}  ({count})")
            item.setData(Qt.UserRole, tid)
            item.setData(Qt.UserRole + 1, name)
            self._list.addItem(item)
        self._list.setFixedHeight(
            min(200, self._list.sizeHintForRow(0) * len(results) + 4)
        )
        self.adjustSize()

    def _on_item_clicked(self, item: QListWidgetItem):
        tid = item.data(Qt.UserRole)
        name = item.data(Qt.UserRole + 1)
        self.hide()
        self.tag_selected.emit(tid, name)


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
        self._suggested_chips: list[ChipToggleButton] = []
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._do_search)

        self.setWindowTitle("Review Auto-Tags")
        self.resize(750, 600)
        self.setFocusPolicy(Qt.StrongFocus)
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

        self._renew_btn = QPushButton("Renew")
        self._renew_btn.setToolTip("Load fresh batch of 15 assets")
        self._renew_btn.clicked.connect(self._load_assets)
        self._renew_btn.setVisible(False)
        header.addWidget(self._renew_btn)

        layout.addLayout(header)

        # Instruction
        self._instruction = QLabel(
            "Click a tag chip to reject it. Active (blue) = keep, inactive (gray) = remove.\n"
            "Space = Save & Next   |   Esc = Close"
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

        # Right side: filename + search + tag chips + suggested
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

        right.addSpacing(8)

        # Search bar
        search_row = QHBoxLayout()
        self._search_bar = QLineEdit()
        self._search_bar.setPlaceholderText("Search tags to add...")
        self._search_bar.textChanged.connect(self._on_search_text_changed)
        search_row.addWidget(self._search_bar)
        right.addLayout(search_row)

        # Search result overlay (hidden until results available)
        self._search_overlay = _SearchOverlay(self)
        self._search_overlay.tag_selected.connect(self._on_search_tag_selected)

        tag_label = QLabel("Tags on this asset:")
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

        # Suggested tags section
        self._suggested_label = QLabel("Suggested:")
        self._suggested_label.setStyleSheet("font-weight: bold; color: #64748b;")
        self._suggested_label.setVisible(False)
        right.addWidget(self._suggested_label)

        self._suggested_container = QWidget()
        self._suggested_layout = FlowLayout(spacing=5)
        self._suggested_container.setLayout(self._suggested_layout)
        self._suggested_container.setVisible(False)
        right.addWidget(self._suggested_container)

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
        self._assets = self._queries.get_unreviewed_tagged_assets(limit=15)
        if not self._assets:
            self._title_label.setText("All reviewed!")
            self._instruction.setText(
                "No more assets with unreviewed tags. Import more assets or check back later."
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
        self._clear_chips()
        self._search_bar.clear()

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

        if not tags:
            no_tags = QLabel("(No tags on this asset)")
            no_tags.setStyleSheet("color: #94a3b8; font-style: italic;")
            no_tags.setAlignment(Qt.AlignCenter)
            self._chip_layout.addWidget(no_tags)

        self._load_suggestions(asset.id, tags)

    def _clear_chips(self):
        for layout, chip_list in [
            (self._chip_layout, self._chips),
            (self._suggested_layout, self._suggested_chips),
        ]:
            while layout.count():
                item = layout.takeAt(0)
                if item and item.widget():
                    w = item.widget()
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()
            chip_list.clear()
        QApplication.processEvents()

    def _load_suggestions(self, asset_id: int, existing_tags: list[tuple[int, str, str]]):
        self._suggested_container.setVisible(False)
        self._suggested_label.setVisible(False)

        existing_ids = {t[0] for t in existing_tags}
        existing_names_lower = {t[1].lower() for t in existing_tags}
        suggested: dict[int, tuple[str, int]] = {}  # tag_id -> (name, score)

        # Gather related tags from co-occurrence
        for tid in existing_ids:
            related = self._queries.get_related_tags(tid, limit=5)
            for rid, rname, rcount in related:
                if rid not in existing_ids and rid not in suggested:
                    suggested[rid] = (rname, rcount)

        # Gather from TAG_HIERARCHY — parents and children
        for _tid, name, _color in existing_tags:
            # Parents
            for parent_name, children in TAG_HIERARCHY.items():
                if name in children and parent_name.lower() not in existing_names_lower:
                    # Find parent tag id
                    parent_tag = self._queries.get_tag_by_name(parent_name)
                    if parent_tag and parent_tag[0] not in existing_ids:
                        suggested[parent_tag[0]] = (parent_name, 100)
            # Children
            if name in TAG_HIERARCHY:
                for child_name in TAG_HIERARCHY[name]:
                    if child_name.lower() not in existing_names_lower:
                        child_tag = self._queries.get_tag_by_name(child_name)
                        if child_tag and child_tag[0] not in existing_ids:
                            suggested[child_tag[0]] = (child_name, 90)

        if not suggested:
            return

        self._suggested_label.setVisible(True)
        self._suggested_container.setVisible(True)

        # Sort by score descending
        sorted_suggestions = sorted(suggested.items(), key=lambda x: -x[1][1])
        if len(sorted_suggestions) > 12:
            sorted_suggestions = sorted_suggestions[:12]

        for tid, (tname, _score) in sorted_suggestions:
            chip = ChipToggleButton(tname)
            chip.setProperty("tag_id", tid)
            chip.setProperty("suggested", True)
            chip.set_active(False)
            chip.setStyleSheet(
                "QPushButton { background: #f1f5f9; color: #64748b; border: 1px dashed #cbd5e1; "
                "border-radius: 10px; padding: 3px 10px; font-size: 11px; }"
                "QPushButton:hover { background: #dbeafe; border-color: #3b82f6; color: #1e40af; }"
            )
            chip.clicked.connect(self._make_suggestion_handler(tid, tname))
            self._suggested_layout.addWidget(chip)
            self._suggested_chips.append(chip)

    def _make_suggestion_handler(self, tid: int, tname: str):
        def handler():
            asset = self._assets[self._current_idx]
            self._queries.add_tag_to_asset(asset.id, tid)
            # Record co-occurrence with existing tags
            existing_ids = [c.property("tag_id") for c in self._chips]
            existing_ids.append(tid)
            self._queries.record_tag_cooccurrence(existing_ids)
            # Refresh display
            self._show_current()
        return handler

    # ── Search ─────────────────────────────────────────────

    def _on_search_text_changed(self, text: str):
        if len(text.strip()) < 1:
            self._search_overlay.hide()
            return
        self._search_timer.start()  # debounce

    def _do_search(self):
        text = self._search_bar.text().strip()
        if len(text) < 1:
            self._search_overlay.hide()
            return
        results = self._queries.search_tags(text, limit=12)
        self._search_overlay.set_results(results)
        if results:
            # Position overlay below search bar
            pos = self._search_bar.mapToGlobal(self._search_bar.rect().bottomLeft())
            self._search_overlay.move(pos)
            self._search_overlay.show()

    def _on_search_tag_selected(self, tag_id: int, tag_name: str):
        asset = self._assets[self._current_idx]
        # Don't add if already on the asset
        existing_ids = {c.property("tag_id") for c in self._chips}
        if tag_id in existing_ids:
            self._search_bar.clear()
            return
        self._queries.add_tag_to_asset(asset.id, tag_id)
        # Record co-occurrence
        existing_ids.add(tag_id)
        self._queries.record_tag_cooccurrence(list(existing_ids))
        self._search_bar.clear()
        self._show_current()

    # ── Actions ────────────────────────────────────────────

    def _on_skip(self):
        self._advance()

    def _on_save_next(self):
        asset = self._assets[self._current_idx]
        accepted_ids: list[int] = []
        for chip in self._chips:
            tag_id = chip.property("tag_id")
            accepted = chip.isChecked()
            self._queries.save_tag_review(asset.id, tag_id, accepted)
            if not accepted:
                self._queries.remove_tag_from_asset(asset.id, tag_id)
            else:
                accepted_ids.append(tag_id)
        if len(accepted_ids) >= 2:
            self._queries.record_tag_cooccurrence(accepted_ids)
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

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Space:
            self._on_save_next()
        elif event.key() == Qt.Key_Escape:
            self._on_done()
        else:
            super().keyPressEvent(event)
