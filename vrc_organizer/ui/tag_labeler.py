"""Tag Labeler — captcha-style UI for reviewing and correcting auto-assigned tags."""
from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPixmap, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QProgressBar, QFrame, QMessageBox,
    QLineEdit, QListWidget, QListWidgetItem,
)

from vrc_organizer.database.queries import Queries
from vrc_organizer.models.asset import Asset
from vrc_organizer.tag_data import GENRE_NAMES, TAG_HIERARCHY
from vrc_organizer.ui.flow_layout import FlowLayout
from vrc_organizer.auto_tagger import suggest_tags

THUMB_SIZE = 140


class _TagChip(QPushButton):
    """Tag chip that toggles between active/inactive states."""
    def __init__(self, tag_id: int, name: str, active: bool = True, suggested: bool = False, parent=None):
        super().__init__(name, parent)
        self.tag_id = tag_id
        self.tag_name = name
        self._suggested = suggested
        self.setCheckable(True)
        self.setChecked(active)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(28)
        self._apply_style()
        self.toggled.connect(self._apply_style)

    def _apply_style(self):
        if self._suggested and not self.isChecked():
            self.setStyleSheet("""
                QPushButton {
                    background: transparent; color: #94a3b8;
                    border: 1px dashed #cbd5e1; border-radius: 14px;
                    padding: 4px 14px; font-size: 12px;
                }
                QPushButton:hover { background: #dbeafe; border-color: #3b82f6; color: #1e40af; }
            """)
        elif self.isChecked():
            self.setStyleSheet("""
                QPushButton {
                    background: #3b82f6; color: white; border: none;
                    border-radius: 14px; padding: 4px 14px; font-size: 12px;
                }
                QPushButton:hover { background: #2563eb; }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background: #fee2e2; color: #dc2626; border: none;
                    border-radius: 14px; padding: 4px 14px; font-size: 12px;
                    text-decoration: line-through;
                }
                QPushButton:hover { background: #fecaca; }
            """)


class _GenreButton(QPushButton):
    """Exclusive genre selection button."""
    def __init__(self, name: str, parent=None):
        super().__init__(name, parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(32)
        self._apply_style()
        self.toggled.connect(self._apply_style)

    def _apply_style(self):
        if self.isChecked():
            self.setStyleSheet("""
                QPushButton {
                    background: #22c55e; color: white; border: none;
                    border-radius: 16px; padding: 6px 16px; font-size: 12px; font-weight: 600;
                }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background: #f1f5f9; color: #64748b; border: none;
                    border-radius: 16px; padding: 6px 16px; font-size: 12px;
                }
                QPushButton:hover { background: #e2e8f0; color: #334155; }
            """)


class _SearchPopup(QFrame):
    """Popup for searching and adding tags."""
    tag_selected = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedWidth(220)
        self.setMaximumHeight(180)
        self.setStyleSheet("QFrame { background: white; border: 1px solid #e2e8f0; border-radius: 8px; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._list = QListWidget()
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setStyleSheet("""
            QListWidget { border: none; background: transparent; }
            QListWidget::item { padding: 6px 8px; border-radius: 4px; }
            QListWidget::item:hover { background: #f1f5f9; }
            QListWidget::item:selected { background: #dbeafe; color: #1e40af; }
        """)
        self._list.itemClicked.connect(self._on_click)
        layout.addWidget(self._list)

    def set_results(self, results: list[tuple[int, str, str, int]]):
        self._list.clear()
        if not results:
            self.hide()
            return
        for tid, name, color, count in results:
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, tid)
            item.setData(Qt.UserRole + 1, name)
            self._list.addItem(item)
        self._list.setFixedHeight(min(160, self._list.sizeHintForRow(0) * len(results) + 8))
        self.adjustSize()

    def _on_click(self, item: QListWidgetItem):
        self.hide()
        self.tag_selected.emit(item.data(Qt.UserRole), item.data(Qt.UserRole + 1))


class TagLabelerDialog(QDialog):
    """Captcha-style dialog for reviewing and correcting auto-assigned tags."""
    labeling_complete = Signal()

    def __init__(self, queries: Queries, thumb_cache_dir: Path, parent=None):
        super().__init__(parent)
        self._queries = queries
        self._thumb_cache_dir = thumb_cache_dir
        self._assets: list[Asset] = []
        self._current_idx = 0
        self._session_id = str(uuid.uuid4())[:8]
        self._loading_more = False
        self._original_tags: list[int] = []
        self._genre_buttons: dict[str, _GenreButton] = {}
        self._tag_chips: list[_TagChip] = []
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._do_search)

        self.setWindowTitle("Label Tags")
        self.resize(640, 520)
        self._setup_ui()
        self._load_assets()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        # Header
        header = QHBoxLayout()
        self._title = QLabel("Loading...")
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        self._title.setFont(font)
        header.addWidget(self._title)
        header.addStretch()

        self._progress = QProgressBar()
        self._progress.setFixedWidth(120)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet("""
            QProgressBar { background: #e2e8f0; border-radius: 4px; height: 8px; }
            QProgressBar::chunk { background: #3b82f6; border-radius: 4px; }
        """)
        header.addWidget(self._progress)
        layout.addLayout(header)

        # Main content: left (thumb) | right (tags)
        content = QHBoxLayout()
        content.setSpacing(20)

        # Left: thumbnail + filename
        left = QVBoxLayout()
        left.setSpacing(8)

        self._thumb = QLabel()
        self._thumb.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setStyleSheet("background: #f1f5f9; border-radius: 8px;")
        left.addWidget(self._thumb)

        self._filename = QLabel("")
        self._filename.setWordWrap(True)
        self._filename.setMaximumWidth(THUMB_SIZE)
        self._filename.setStyleSheet("color: #334155; font-size: 11px;")
        left.addWidget(self._filename)

        self._filetype = QLabel("")
        self._filetype.setStyleSheet("color: #94a3b8; font-size: 10px;")
        left.addWidget(self._filetype)
        left.addStretch()
        content.addLayout(left)

        # Right: genre + tags
        right = QVBoxLayout()
        right.setSpacing(12)

        # Genre row
        genre_label = QLabel("Genre")
        genre_label.setStyleSheet("color: #64748b; font-size: 11px; font-weight: 600;")
        right.addWidget(genre_label)

        genre_row = QHBoxLayout()
        genre_row.setSpacing(8)
        for name in GENRE_NAMES:
            btn = _GenreButton(name)
            btn.clicked.connect(lambda checked, n=name: self._on_genre_clicked(n))
            self._genre_buttons[name] = btn
            genre_row.addWidget(btn)
        genre_row.addStretch()
        right.addLayout(genre_row)

        # Tags section
        tags_label = QLabel("Tags — click to toggle")
        tags_label.setStyleSheet("color: #64748b; font-size: 11px; font-weight: 600; margin-top: 8px;")
        right.addWidget(tags_label)

        # Search bar
        self._search = QLineEdit()
        self._search.setPlaceholderText("Type to add tag...")
        self._search.setFixedHeight(32)
        self._search.setStyleSheet("""
            QLineEdit {
                background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
                padding: 0 12px; font-size: 12px;
            }
            QLineEdit:focus { border-color: #3b82f6; background: white; }
        """)
        self._search.textChanged.connect(lambda: self._search_timer.start())
        self._search.returnPressed.connect(self._on_search_enter)
        right.addWidget(self._search)

        self._search_popup = _SearchPopup(self)
        self._search_popup.tag_selected.connect(self._on_add_tag)

        # Tag chips scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._chips_container = QWidget()
        self._chips_container.setStyleSheet("background: transparent;")
        self._chips_layout = FlowLayout(spacing=8)
        self._chips_container.setLayout(self._chips_layout)
        scroll.setWidget(self._chips_container)
        right.addWidget(scroll, 1)

        content.addLayout(right, 1)
        layout.addLayout(content, 1)

        # Footer
        footer = QHBoxLayout()
        self._hint = QLabel("Space = Save & Next")
        self._hint.setStyleSheet("color: #94a3b8; font-size: 11px;")
        footer.addWidget(self._hint)
        footer.addStretch()

        skip_btn = QPushButton("Skip")
        skip_btn.setFixedWidth(70)
        skip_btn.setStyleSheet("""
            QPushButton { background: #f1f5f9; color: #64748b; border: none;
                          border-radius: 6px; padding: 8px; font-weight: 500; }
            QPushButton:hover { background: #e2e8f0; }
        """)
        skip_btn.clicked.connect(self._on_skip)
        footer.addWidget(skip_btn)

        save_btn = QPushButton("Save")
        save_btn.setFixedWidth(70)
        save_btn.setStyleSheet("""
            QPushButton { background: #3b82f6; color: white; border: none;
                          border-radius: 6px; padding: 8px; font-weight: 500; }
            QPushButton:hover { background: #2563eb; }
        """)
        save_btn.clicked.connect(self._on_save)
        footer.addWidget(save_btn)

        done_btn = QPushButton("Done")
        done_btn.setFixedWidth(70)
        done_btn.setStyleSheet("""
            QPushButton { background: #22c55e; color: white; border: none;
                          border-radius: 6px; padding: 8px; font-weight: 500; }
            QPushButton:hover { background: #16a34a; }
        """)
        done_btn.clicked.connect(self._on_done)
        footer.addWidget(done_btn)
        layout.addLayout(footer)

    def _on_genre_clicked(self, name: str):
        for n, btn in self._genre_buttons.items():
            btn.setChecked(n == name)

    def _load_assets(self):
        self._assets = self._queries.get_unlabeled_tag_assets(limit=200)
        if not self._assets:
            self._title.setText("All Done")
            self._hint.setText("No more assets need tag labels.")
            self._progress.setVisible(False)
            return
        self._current_idx = 0
        self._progress.setRange(0, len(self._assets))
        self._show_current()

    def _prefetch_more(self):
        """Load another batch of assets and append to the queue."""
        if self._loading_more:
            return
        self._loading_more = True
        try:
            more = self._queries.get_unlabeled_tag_assets(limit=100)
            existing_ids = {a.id for a in self._assets}
            new_assets = [a for a in more if a.id not in existing_ids]
            if new_assets:
                self._assets.extend(new_assets)
                self._progress.setRange(0, len(self._assets))
        finally:
            self._loading_more = False

    def _show_current(self):
        self._clear_chips()

        asset = self._assets[self._current_idx]
        self._title.setText(f"{self._current_idx + 1} / {len(self._assets)}")
        self._progress.setValue(self._current_idx)
        self._filename.setText(asset.filename)
        self._filetype.setText(f"{asset.filetype} • {asset.file_size:,} B")

        # Thumbnail
        thumb_path = self._thumb_cache_dir / f"{asset.id}.png"
        pix = QPixmap(str(thumb_path))
        if not pix.isNull():
            self._thumb.setPixmap(pix.scaled(THUMB_SIZE, THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._thumb.clear()
            self._thumb.setText("No preview")

        # Load current tags
        tags = self._queries.get_tags_for_asset(asset.id)
        self._original_tags = [tid for tid, _, _ in tags]

        # Genre detection + uncheck all first
        for btn in self._genre_buttons.values():
            btn.setChecked(False)

        non_genre_tags: list[tuple[int, str, str]] = []
        for tid, name, color in tags:
            if name in GENRE_NAMES:
                self._genre_buttons[name].setChecked(True)
            else:
                non_genre_tags.append((tid, name, color))

        # Create tag chips
        for tid, name, color in non_genre_tags:
            chip = _TagChip(tid, name, active=True)
            self._chips_layout.addWidget(chip)
            self._tag_chips.append(chip)

        # Load suggestions
        self._load_suggestions(asset.id, non_genre_tags)
        self._chips_layout.activate()
        needed = self._chips_layout.heightForWidth(self._chips_container.width())
        self._chips_container.setMinimumHeight(max(needed, 32))
        self._chips_container.adjustSize()

    def _clear_chips(self):
        # Remove all items from flow layout
        while self._chips_layout.count() > 0:
            item = self._chips_layout.takeAt(0)
            if item and item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._tag_chips.clear()
        self._chips_container.adjustSize()

    def _load_suggestions(self, asset_id: int, existing_tags: list[tuple[int, str, str]]):
        existing_ids = {t[0] for t in existing_tags}
        existing_names = {t[1].lower() for t in existing_tags}
        suggested: dict[int, str] = {}

        # 1. Auto-tagger raw suggestions from filename
        asset = self._assets[self._current_idx]
        auto_tag_ids = suggest_tags(self._queries, asset.filename, None)
        id_to_name = {tid: name for tid, name, _, _ in self._queries.get_all_tags()}
        for tid in auto_tag_ids[:6]:
            name = id_to_name.get(tid)
            if name and tid not in existing_ids and name.lower() not in existing_names:
                if name not in GENRE_NAMES:
                    suggested[tid] = name

        # 2. Co-occurrence suggestions
        for tid in existing_ids:
            related = self._queries.get_related_tags(tid, limit=3)
            for rid, rname, _ in related:
                if rid not in existing_ids and rname.lower() not in existing_names:
                    if rname not in GENRE_NAMES:
                        suggested[rid] = rname

        # 3. Hierarchy children suggestions
        for _, name, _ in existing_tags:
            if name in TAG_HIERARCHY:
                children = list(TAG_HIERARCHY[name])[:3]
                for child in children:
                    if child.lower() not in existing_names:
                        tag = self._queries.get_tag_by_name(child)
                        if tag and tag[0] not in existing_ids:
                            suggested[tag[0]] = child

        for tid, name in list(suggested.items())[:6]:
            chip = _TagChip(tid, name, active=False, suggested=True)
            self._chips_layout.addWidget(chip)
            self._tag_chips.append(chip)

    def _do_search(self):
        text = self._search.text().strip()
        if len(text) < 1:
            self._search_popup.hide()
            return
        results = self._queries.search_tags(text, limit=6)
        current_ids = {c.tag_id for c in self._tag_chips}
        filtered = [(tid, name, color, cnt) for tid, name, color, cnt in results
                    if tid not in current_ids and name not in GENRE_NAMES]
        self._search_popup.set_results(filtered)
        if filtered:
            pos = self._search.mapToGlobal(self._search.rect().bottomLeft())
            self._search_popup.move(pos)
            self._search_popup.show()
            self._search.setFocus()  # keep focus on the search field

    def _on_search_enter(self):
        text = self._search.text().strip()
        if not text:
            return

        # If popup is visible with results, select the first one
        if self._search_popup.isVisible() and self._search_popup._list.count() > 0:
            item = self._search_popup._list.item(0)
            tag_id = item.data(Qt.UserRole)
            tag_name = item.data(Qt.UserRole + 1)
            self._on_add_tag(tag_id, tag_name)
            return

        # Check if exact match exists
        existing = self._queries.get_tag_by_name(text)
        if existing:
            tag_id, tag_name, _ = existing
            if tag_name not in GENRE_NAMES and not any(c.tag_id == tag_id for c in self._tag_chips):
                self._on_add_tag(tag_id, tag_name)
            else:
                self._search.clear()
            return

        # Create new tag
        if text in GENRE_NAMES:
            self._search.clear()
            return
        tag_id = self._queries.create_tag(text)
        self._on_add_tag(tag_id, text)

    def _on_add_tag(self, tag_id: int, tag_name: str):
        self._search.clear()
        self._search_popup.hide()
        if any(c.tag_id == tag_id for c in self._tag_chips):
            return
        chip = _TagChip(tag_id, tag_name, active=True)
        self._chips_layout.addWidget(chip)
        self._tag_chips.append(chip)
        self._chips_layout.activate()
        needed = self._chips_layout.heightForWidth(self._chips_container.width())
        self._chips_container.setMinimumHeight(max(needed, 32))
        self._chips_container.adjustSize()

    def _on_skip(self):
        self._advance()

    def _on_save(self):
        asset = self._assets[self._current_idx]

        accepted = []
        rejected = []
        added = []

        for chip in self._tag_chips:
            if chip._suggested:
                if chip.isChecked():
                    added.append(chip.tag_id)
                    self._queries.add_tag_to_asset(asset.id, chip.tag_id)
            else:
                if chip.isChecked():
                    accepted.append(chip.tag_id)
                else:
                    rejected.append(chip.tag_id)
                    self._queries.remove_tag_from_asset(asset.id, chip.tag_id)

        # Get selected genre
        genre_tag_id = None
        selected_genre = None
        for name, btn in self._genre_buttons.items():
            if btn.isChecked():
                selected_genre = name
                tag = self._queries.get_tag_by_name(name)
                if tag:
                    genre_tag_id = tag[0]
                    if genre_tag_id not in self._original_tags:
                        self._queries.add_tag_to_asset(asset.id, genre_tag_id)
                break

        # Remove old genre tags if different
        for name in GENRE_NAMES:
            if name != selected_genre:
                old_tag = self._queries.get_tag_by_name(name)
                if old_tag and old_tag[0] in self._original_tags:
                    self._queries.remove_tag_from_asset(asset.id, old_tag[0])

        # Save label record for ML
        self._queries.save_tag_label(
            asset_id=asset.id,
            session_id=self._session_id,
            original_tags=self._original_tags,
            accepted_tags=accepted,
            rejected_tags=rejected,
            added_tags=added,
            genre_tag_id=genre_tag_id,
        )

        # Record co-occurrence
        all_kept = accepted + added
        if genre_tag_id:
            all_kept.append(genre_tag_id)
        if len(all_kept) >= 2:
            self._queries.record_tag_cooccurrence(all_kept)

        self._advance()

    def _advance(self):
        self._current_idx += 1
        # Prefetch more when fewer than 10 remain
        if len(self._assets) - self._current_idx < 10:
            self._prefetch_more()
        if self._current_idx < len(self._assets):
            self._show_current()
        else:
            self._finish()

    def _finish(self):
        QMessageBox.information(
            self, "Complete",
            f"Labeled {self._current_idx} asset(s).\n\n"
            "Tag corrections have been saved."
        )
        self.labeling_complete.emit()
        self.accept()

    def _on_done(self):
        if self._current_idx < len(self._assets):
            remaining = len(self._assets) - self._current_idx
            reply = QMessageBox.question(
                self, "Exit Early?",
                f"{remaining} asset(s) remaining.\n\nExit now?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.labeling_complete.emit()
        self.accept()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key_Return or key == Qt.Key_Enter:
            if self._search.hasFocus():
                if self._search.text().strip():
                    # Let returnPressed handle tag creation
                    super().keyPressEvent(event)
                # If search is focused but empty, ignore Enter (don't save)
                return
            self._on_save()
            return
        if key == Qt.Key_Escape:
            self._on_done()
        elif key == Qt.Key_Space and not self._search.hasFocus():
            self._on_save()
        else:
            super().keyPressEvent(event)
