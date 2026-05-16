"""Tag Labeler — fast captcha-style UI for reviewing auto-assigned tags."""
from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer, QSettings
from PySide6.QtGui import QPixmap, QKeyEvent
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QFrame, QMessageBox, QLineEdit, QScrollArea,
)

from vrc_organizer.database.queries import Queries
from vrc_organizer.models.asset import Asset
from vrc_organizer.romaji import has_japanese, to_romaji
from vrc_organizer.tag_data import GENRE_NAMES, TAG_HIERARCHY
from vrc_organizer.auto_tagger import suggest_tags

THUMB_SIZE = 120


class TagLabelerDialog(QDialog):
    """Fast tag review dialog. Space = save, S = skip, 1-4 = genre, click tags to toggle."""
    labeling_complete = Signal()

    def __init__(self, queries: Queries, thumb_cache_dir: Path, parent=None):
        super().__init__(parent)
        # See note in TagDialog: required to avoid leaking the dialog into
        # MainWindow's child list every time it opens.
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._queries = queries
        self._thumb_cache_dir = thumb_cache_dir
        self._assets: list[Asset] = []
        self._idx = 0
        self._session = str(uuid.uuid4())[:8]
        self._orig_tags: set[int] = set()
        self._orig_genre: int | None = None
        self._labeled = 0
        self._chips: list[tuple[QWidget, int, str]] = []  # (widget, tag_id, state)
        # Undo stack: each entry is (asset_id, tag_ids_before_save). Pop on
        # back; restore the asset's tags to exactly the saved snapshot.
        self._history: list[tuple[int, set[int]]] = []

        self.setWindowTitle("Tag Review")
        self.setMinimumSize(700, 500)
        self._build_ui()
        self._load_queue()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(16, 12, 16, 12)

        # Top bar: progress + hints
        top = QHBoxLayout()
        self._progress_lbl = QLabel("Loading...")
        self._progress_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        top.addWidget(self._progress_lbl)
        top.addStretch()
        hints = QLabel("Space = save & next   ·   S = skip   ·   Ctrl+Z = back   ·   Esc = done")
        hints.setStyleSheet("color: #475569; font-size: 11px;")
        top.addWidget(hints)
        root.addLayout(top)

        # Main area
        main = QHBoxLayout()
        main.setSpacing(16)

        # Left: thumbnail + filename + asset context (folders / readme)
        left = QVBoxLayout()
        left.setSpacing(8)
        self._thumb = QLabel()
        self._thumb.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setStyleSheet("background: #1e293b; border-radius: 4px;")
        left.addWidget(self._thumb)
        self._filename = QLabel()
        self._filename.setWordWrap(True)
        self._filename.setMaximumWidth(THUMB_SIZE)
        # Palette-driven so it stays legible in light AND dark mode. The old
        # hardcoded #e2e8f0 went near-white on light backgrounds.
        self._filename.setStyleSheet(
            "color: palette(window-text); font-size: 12px; font-weight: 700;"
        )
        left.addWidget(self._filename)
        self._romaji_label = QLabel()
        self._romaji_label.setWordWrap(True)
        self._romaji_label.setMaximumWidth(THUMB_SIZE)
        self._romaji_label.setStyleSheet(
            "color: palette(mid); font-size: 10px;"
        )
        self._romaji_label.setVisible(False)
        left.addWidget(self._romaji_label)
        # Context: short list of meaningful path tokens from the archive.
        # Gives reviewers something to anchor tags against beyond the filename.
        self._context = QLabel()
        self._context.setWordWrap(True)
        self._context.setMaximumWidth(THUMB_SIZE)
        self._context.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._context.setStyleSheet(
            "color: palette(window-text); font-size: 10px; line-height: 14px;"
        )
        left.addWidget(self._context)
        left.addStretch()
        main.addLayout(left)

        # Right: genre + tags
        right = QVBoxLayout()
        right.setSpacing(10)

        # Genre buttons — click to pick. (Hotkeys removed: spacebar advances.)
        # autoDefault/default are explicitly OFF: QDialog otherwise treats
        # the first QPushButton as the dialog's default and any Enter press
        # in the search field would click it (silently flipping genre to
        # Avatar Base every time the user created a new tag).
        genre_row = QHBoxLayout()
        genre_row.setSpacing(6)
        self._genre_btns: dict[str, QPushButton] = {}
        for name in GENRE_NAMES:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setMinimumHeight(36)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, n=name: self._select_genre(n))
            self._genre_btns[name] = btn
            genre_row.addWidget(btn)
        right.addLayout(genre_row)
        self._update_genre_styles()

        # Tag search/add — palette-driven for legibility, lives between
        # genre row and chip list. Live-filters the suggested chips and
        # creates the tag on Enter when nothing matches.
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search tags or press Enter to add a new one")
        self._search.setMinimumHeight(28)
        self._search.textChanged.connect(self._on_search_changed)
        self._search.returnPressed.connect(self._on_search_enter)
        right.addWidget(self._search)

        # Tags area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._tags_box = QWidget()
        self._tags_box.setStyleSheet("background: transparent;")
        self._tags_layout = QVBoxLayout(self._tags_box)
        self._tags_layout.setSpacing(6)
        self._tags_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self._tags_box)
        right.addWidget(scroll, 1)

        main.addLayout(right, 1)
        root.addLayout(main, 1)

        # Bottom buttons
        btm = QHBoxLayout()
        btm.addStretch()
        self._back_btn = QPushButton("← Back")
        self._back_btn.setMinimumWidth(80)
        self._back_btn.setAutoDefault(False)
        self._back_btn.setToolTip("Re-review the previous asset (Ctrl+Z)")
        self._back_btn.setEnabled(False)
        self._back_btn.clicked.connect(self._undo)
        btm.addWidget(self._back_btn)
        skip_btn = QPushButton("Skip")
        skip_btn.setMinimumWidth(80)
        skip_btn.setAutoDefault(False)
        skip_btn.clicked.connect(self._skip)
        btm.addWidget(skip_btn)
        save_btn = QPushButton("Save")
        save_btn.setMinimumWidth(80)
        save_btn.setAutoDefault(False)
        save_btn.setStyleSheet("background: #3b82f6; color: white; font-weight: bold;")
        save_btn.clicked.connect(self._save)
        btm.addWidget(save_btn)
        done_btn = QPushButton("Done")
        done_btn.setMinimumWidth(80)
        done_btn.setAutoDefault(False)
        done_btn.clicked.connect(self._finish)
        btm.addWidget(done_btn)
        root.addLayout(btm)

    def _load_queue(self):
        self._assets = self._queries.get_unlabeled_tag_assets(limit=200)
        if not self._assets:
            self._progress_lbl.setText("All done!")
            return
        self._idx = 0
        self._show()

    def _show(self):
        if self._idx >= len(self._assets):
            self._finish()
            return

        asset = self._assets[self._idx]
        self._progress_lbl.setText(f"{self._idx + 1} / {len(self._assets)}")
        self._filename.setText(asset.filename)
        if QSettings().value("show_romaji", True, type=bool) and has_japanese(asset.filename):
            self._romaji_label.setText(to_romaji(asset.filename))
            self._romaji_label.setVisible(True)
        else:
            self._romaji_label.setVisible(False)
        self._context.setText(self._build_context_text(asset))

        # Thumbnail
        p = self._thumb_cache_dir / f"{asset.id}.png"
        if p.exists():
            pix = QPixmap(str(p))
            self._thumb.setPixmap(pix.scaled(THUMB_SIZE, THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._thumb.clear()
            self._thumb.setText("?")

        # Load tags
        tags = self._queries.get_tags_for_asset(asset.id)
        self._orig_tags = {t[0] for t in tags}
        self._orig_genre = None

        # Reset genre
        for btn in self._genre_btns.values():
            btn.setChecked(False)

        # Populate
        active: list[tuple[int, str]] = []
        for tid, name, _ in tags:
            if name in GENRE_NAMES:
                self._genre_btns[name].setChecked(True)
                self._orig_genre = tid
            else:
                active.append((tid, name))

        self._update_genre_styles()
        self._build_tags(active, asset)

    def _build_tags(self, active: list[tuple[int, str]], asset: Asset):
        # Clear old
        for w, _, _ in self._chips:
            w.deleteLater()
        self._chips.clear()
        while self._tags_layout.count():
            self._tags_layout.takeAt(0)

        # Existing tags (active)
        if active:
            row = self._make_row("Current", "#3b82f6")
            for tid, name in active:
                chip = self._make_chip(tid, name, "active")
                row.addWidget(chip)
            row.addStretch()
            self._tags_layout.addLayout(row)

        # Suggestions
        suggestions = self._get_suggestions(asset, active)
        if suggestions:
            row = self._make_row("Suggested", "#94a3b8")
            for tid, name in suggestions[:8]:
                chip = self._make_chip(tid, name, "suggested")
                row.addWidget(chip)
            row.addStretch()
            self._tags_layout.addLayout(row)

        self._tags_layout.addStretch()

    def _make_row(self, label: str, color: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel(label)
        lbl.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 700; min-width: 60px;"
        )
        row.addWidget(lbl)
        return row

    def _build_context_text(self, asset) -> str:
        """Pull a few useful path tokens out of the asset's scan_results so
        the reviewer has visible context beyond the filename."""
        try:
            entries = self._queries.get_scan_results(asset.id)
        except Exception:
            return ""
        if not entries:
            return ""

        # Collect distinct top-level folders and a few notable file basenames.
        folders: list[str] = []
        files: list[str] = []
        seen_folders: set[str] = set()
        notable = (".prefab", ".fbx", ".blend", ".psd", ".png", ".jpg", ".mat", ".controller", ".anim", ".cs")
        for name, etype, size in entries[:400]:
            n = name.replace("\\", "/")
            parts = n.split("/")
            if len(parts) > 1:
                top = parts[0]
                if top not in seen_folders and len(folders) < 5:
                    seen_folders.add(top)
                    folders.append(top)
            base = parts[-1].lower()
            if any(base.endswith(ext) for ext in notable) and len(files) < 6:
                if base not in files:
                    files.append(parts[-1])

        lines: list[str] = []
        if folders:
            lines.append("📁 " + " / ".join(folders))
        if files:
            lines.append("📄 " + ", ".join(files[:6]))
        return "\n".join(lines)

    def _make_chip(self, tid: int, name: str, state: str) -> QPushButton:
        btn = QPushButton(name)
        btn.setCheckable(True)
        btn.setChecked(state == "active")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumHeight(28)
        btn.clicked.connect(lambda: self._toggle_chip(btn, tid, name))
        self._chips.append((btn, tid, state))
        self._style_chip(btn, state if state == "suggested" and not btn.isChecked() else ("active" if btn.isChecked() else "rejected"))
        return btn

    def _style_chip(self, btn: QPushButton, state: str):
        if state == "active":
            btn.setStyleSheet("""
                QPushButton { background: #3b82f6; color: white; border: none;
                    border-radius: 7px; padding: 4px 12px; font-size: 12px; }
                QPushButton:hover { background: #2563eb; }
            """)
        elif state == "rejected":
            btn.setStyleSheet("""
                QPushButton { background: #7f1d1d; color: #fca5a5; border: none;
                    border-radius: 7px; padding: 4px 12px; font-size: 12px;
                    text-decoration: line-through; }
                QPushButton:hover { background: #991b1b; }
            """)
        else:  # suggested — bumped contrast: bright text on dashed border
            btn.setStyleSheet("""
                QPushButton { background: transparent; color: #e2e8f0;
                    border: 1px dashed #64748b; border-radius: 7px;
                    padding: 4px 12px; font-size: 12px; font-weight: 500; }
                QPushButton:hover { background: #1e3a5f; border-color: #3b82f6; color: white; }
            """)

    def _toggle_chip(self, btn: QPushButton, tid: int, name: str):
        # Find chip state
        for i, (w, t, s) in enumerate(self._chips):
            if w is btn:
                if btn.isChecked():
                    new_state = "active"
                else:
                    new_state = "rejected" if s != "suggested" else "suggested"
                self._chips[i] = (w, t, new_state if s == "suggested" else s)
                self._style_chip(btn, new_state)
                # When the user accepts a suggested tag, pull in MORE related
                # suggestions via co-occurrence — related tags often travel
                # in groups, so surfacing the next layer saves clicks.
                if new_state == "active":
                    self._extend_suggestions(tid)
                break

    def _extend_suggestions(self, source_tid: int):
        """Append related-tag chips that aren't already on screen."""
        existing_ids = {t for _, t, _ in self._chips}
        target_row = None
        for i in range(self._tags_layout.count()):
            item = self._tags_layout.itemAt(i)
            inner = item.layout() if item else None
            if inner is None:
                continue
            # Heuristic: pick the row whose label says "Suggested".
            lbl_item = inner.itemAt(0)
            lbl = lbl_item.widget() if lbl_item else None
            if isinstance(lbl, QLabel) and lbl.text().startswith("Suggested"):
                target_row = inner
                break
        added = 0
        for rid, rname, _count in self._queries.get_related_tags(source_tid, limit=6):
            if added >= 4 or rid in existing_ids or rname in GENRE_NAMES:
                continue
            chip = self._make_chip(rid, rname, "suggested")
            if target_row is not None:
                # Drop in before the trailing stretch.
                target_row.insertWidget(target_row.count() - 1, chip)
            else:
                # No suggested row yet — make one.
                row = self._make_row("Suggested", "#94a3b8")
                row.addWidget(chip)
                row.addStretch()
                self._tags_layout.addLayout(row)
                target_row = row
            existing_ids.add(rid)
            added += 1

    def _get_suggestions(self, asset: Asset, existing: list[tuple[int, str]]) -> list[tuple[int, str]]:
        existing_ids = {t[0] for t in existing}
        existing_names = {t[1].lower() for t in existing}
        seen: set[int] = set(existing_ids)
        result: list[tuple[int, str]] = []

        # Auto-tagger
        auto_ids = suggest_tags(self._queries, asset.filename, None)
        id_to_name = {t[0]: t[1] for t in self._queries.get_all_tags()}
        for tid in auto_ids:
            name = id_to_name.get(tid)
            if name and tid not in seen and name not in GENRE_NAMES:
                seen.add(tid)
                result.append((tid, name))

        # Co-occurrence
        for tid in existing_ids:
            for rid, rname, _ in self._queries.get_related_tags(tid, limit=2):
                if rid not in seen and rname not in GENRE_NAMES:
                    seen.add(rid)
                    result.append((rid, rname))

        return result

    def _select_genre(self, name: str):
        for n, btn in self._genre_btns.items():
            btn.setChecked(n == name)
        self._update_genre_styles()

    def _update_genre_styles(self):
        for name, btn in self._genre_btns.items():
            if btn.isChecked():
                btn.setStyleSheet("""
                    QPushButton { background: #22c55e; color: white; border: none;
                        border-radius: 3px; font-weight: bold; }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton { background: #1e293b; color: #94a3b8; border: 1px solid #334155;
                        border-radius: 3px; }
                    QPushButton:hover { background: #334155; color: white; }
                """)

    def _on_search_changed(self, text: str):
        """Live filter the chip rows. A chip whose name contains the query
        stays visible; everything else fades out. Empty query shows all.

        Includes chips just created in this session (they're in self._chips
        so they get filtered the same way as the pre-existing ones).
        """
        q = text.lower().strip()
        for btn, _tid, _state in self._chips:
            if not q:
                btn.setVisible(True)
            else:
                btn.setVisible(q in btn.text().lower())

    def _on_search_enter(self):
        text = self._search.text().strip()
        if not text:
            return
        self._search.clear()

        # Find or create tag
        existing = self._queries.get_tag_by_name(text)
        if existing:
            tid, name, _ = existing
        else:
            tid = self._queries.create_tag(text)
            name = text
        if not tid:
            return

        if name in GENRE_NAMES:
            return

        # Already showing this chip? Toggle it on instead of duplicating.
        for btn, t, _ in self._chips:
            if t == tid:
                if not btn.isChecked():
                    btn.setChecked(True)
                    self._style_chip(btn, "active")
                return

        chip = self._make_chip(tid, name, "active")
        # Try to insert into the first chip row (the "Current" group). If the
        # tags layout has no row yet — typical for an asset with no existing
        # tags — build one inline so the chip is actually visible.
        inserted = False
        for i in range(self._tags_layout.count()):
            item = self._tags_layout.itemAt(i)
            inner = item.layout() if item else None
            if inner is not None:
                inner.insertWidget(inner.count() - 1, chip)
                inserted = True
                break
        if not inserted:
            row = self._make_row("Current", "#2563eb")
            row.addWidget(chip)
            row.addStretch()
            # Drop the row above any trailing stretch so chips don't collapse.
            self._tags_layout.insertLayout(0, row)

    def _save(self):
        if self._idx >= len(self._assets):
            return
        asset = self._assets[self._idx]
        # Snapshot for undo BEFORE we mutate. Captures the literal set of
        # tag ids assigned to this asset so _undo can restore it exactly.
        before = {t[0] for t in self._queries.get_tags_for_asset(asset.id)}
        self._history.append((asset.id, before))
        if hasattr(self, "_back_btn"):
            self._back_btn.setEnabled(True)
        accepted, rejected, added = [], [], []

        for btn, tid, orig_state in list(self._chips):
            # Defensive: a chip may have been deleted between when its
            # signal fired and now (Qt fires queued events). Skip the
            # zombie instead of crashing the whole save.
            try:
                is_checked = btn.isChecked()
            except RuntimeError:
                continue
            was_original = tid in self._orig_tags

            if is_checked:
                if was_original:
                    accepted.append(tid)
                else:
                    added.append(tid)
                    self._queries.add_tag_to_asset(asset.id, tid)
            elif was_original:
                rejected.append(tid)
                self._queries.remove_tag_from_asset(asset.id, tid)

        # Genre
        selected_genre_id = None
        for name, btn in self._genre_btns.items():
            if btn.isChecked():
                tag = self._queries.get_tag_by_name(name)
                if tag:
                    selected_genre_id = tag[0]
                    if selected_genre_id != self._orig_genre:
                        self._queries.add_tag_to_asset(asset.id, selected_genre_id)
                break

        if self._orig_genre and self._orig_genre != selected_genre_id:
            self._queries.remove_tag_from_asset(asset.id, self._orig_genre)

        # Record
        self._queries.save_tag_label(
            asset_id=asset.id, session_id=self._session,
            original_tags=list(self._orig_tags),
            accepted_tags=accepted, rejected_tags=rejected, added_tags=added,
            genre_tag_id=selected_genre_id,
        )

        final = accepted + added
        if selected_genre_id:
            final.append(selected_genre_id)
        if len(final) >= 2:
            self._queries.record_tag_cooccurrence(final)

        self._labeled += 1
        self._next()

    def _skip(self):
        if self._idx >= len(self._assets):
            return
        # Skips don't mutate the DB, so we don't need a history entry — but
        # we still want Back to jump to the skipped asset so the user can
        # change their mind. Push a None snapshot to mark "no DB rollback".
        asset = self._assets[self._idx]
        self._history.append((asset.id, set()))
        # Mark as a skip-only entry by sentinel — second tuple slot is the
        # set we'd restore TO, and an empty set on an asset that has tags
        # would wipe them. Use a wrapper to keep skip distinct.
        # Replace the just-pushed entry with the sentinel form:
        self._history[-1] = (asset.id, None)
        if hasattr(self, "_back_btn"):
            self._back_btn.setEnabled(True)
        self._next()

    def _undo(self):
        """Rewind to the previously-reviewed asset, restoring its tag set
        for save entries and just re-showing for skip entries."""
        if not self._history:
            return
        asset_id, before = self._history.pop()
        if before is not None:
            # Restore the exact pre-save tag set: remove anything currently
            # on the asset that wasn't in `before`, and add back anything
            # that was in `before` but isn't now.
            current = {t[0] for t in self._queries.get_tags_for_asset(asset_id)}
            for tid in current - before:
                self._queries.remove_tag_from_asset(asset_id, tid)
            for tid in before - current:
                self._queries.add_tag_to_asset(asset_id, tid)
            # Also drop the tag_label session row so the asset reappears as
            # "needs labeling" — otherwise get_unlabeled_tag_assets would
            # never offer it again.
            self._queries.delete_tag_label(asset_id)
            self._labeled = max(0, self._labeled - 1)

        # Find the asset in the queue and jump to it; inject if missing.
        for i, a in enumerate(self._assets):
            if a.id == asset_id:
                self._idx = i
                self._show()
                self._back_btn.setEnabled(bool(self._history))
                return
        asset = self._queries.get_asset(asset_id)
        if asset is not None:
            self._assets.insert(self._idx, asset)
            self._show()
        self._back_btn.setEnabled(bool(self._history))

    def _next(self):
        self._idx += 1
        if len(self._assets) - self._idx < 10:
            more = self._queries.get_unlabeled_tag_assets(limit=100)
            existing = {a.id for a in self._assets}
            self._assets.extend(a for a in more if a.id not in existing)
        if hasattr(self, "_back_btn"):
            self._back_btn.setEnabled(bool(self._history))
        self._show()

    def _finish(self):
        if self._labeled:
            QMessageBox.information(self, "Done", f"Labeled {self._labeled} asset(s).")
        self.labeling_complete.emit()
        self.accept()

    def keyPressEvent(self, e: QKeyEvent):
        # Don't hijack keys while the user is typing into the tag search.
        if self._search.hasFocus():
            super().keyPressEvent(e)
            return

        k = e.key()
        if k == Qt.Key_Space:
            self._save()
        elif k == Qt.Key_S:
            self._skip()
        elif k == Qt.Key_Z and e.modifiers() & Qt.ControlModifier:
            self._undo()
        elif k == Qt.Key_Escape:
            self._finish()
        else:
            super().keyPressEvent(e)
