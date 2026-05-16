from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal, QSettings, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QLineEdit, QFrame, QSplitter,
)

from vrc_organizer.database.queries import Queries
from vrc_organizer.tag_data import (
    ALL_AVATAR_NAMES, AVATAR_TAG_COLOR, GENRE_NAMES, TOP_AVATARS, is_avatar_tag,
)
from vrc_organizer.ui.body_map import BodyMapWidget
from vrc_organizer.ui.chip_button import ChipToggleButton
from vrc_organizer.ui.flow_layout import FlowLayout


class CollapsibleSection(QWidget):
    """Clickable header that hides/shows its content."""

    collapsed_changed = Signal(bool)  # True when collapsed

    def __init__(self, title: str, parent=None, *, start_collapsed: bool = False):
        super().__init__(parent)
        self._title = title
        arrow = "▸" if start_collapsed else "▾"
        self._header = QPushButton(f"{arrow}  {title}")
        self._header.setFlat(True)
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #64748b;
                border: none;
                padding: 8px 0;
                font-size: 11px;
                font-weight: 600;
                text-align: left;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            QPushButton:hover { color: #94a3b8; }
        """)
        self._header.clicked.connect(self._toggle)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 8)
        self._content_layout.setSpacing(6)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header)
        layout.addWidget(self._content)

        self._collapsed = start_collapsed
        self._content.setVisible(not start_collapsed)

    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._content.setVisible(not self._collapsed)
        arrow = "▸" if self._collapsed else "▾"
        self._header.setText(f"{arrow}  {self._title}")
        self.collapsed_changed.emit(self._collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed


class Sidebar(QWidget):
    tag_filter_changed = Signal(list, list)   # or_tag_ids, and_tag_ids
    manage_tags = Signal()
    clear_filters = Signal()

    def __init__(self, queries: Queries, parent=None):
        super().__init__(parent)
        self._queries = queries
        self._or_ids: list[int] = []
        self._and_ids: list[int] = []
        self._genre_ids: set[int] = set()
        self._avatar_ids: set[int] = set()
        self._tag_cache: dict[str, int] = {}  # name → id
        self._in_filter_emit = False
        self._setup_ui()

    def _setup_ui(self):
        # The four sidebar sections live inside a vertical QSplitter so the
        # user can drag the dividers to give Avatars or Tags more breathing
        # room (or pull Body Map up if they want it permanently visible).
        # Sizes persist via QSettings.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setChildrenCollapsible(False)
        # 1 px hairline handle — visible but unobtrusive. The QSplitter
        # widget extends the hit area beyond the rendered handle so users
        # can still grab it even though it's only a pixel wide.
        self._splitter.setHandleWidth(1)

        # ── Body Map (collapsible) ──
        body_section = CollapsibleSection("Body Map", start_collapsed=True)
        self._body_map = BodyMapWidget()
        self._body_map.segment_toggled.connect(self._on_body_segment_toggled)
        # Horizontal centering only — vertical alignment is handled by the
        # trailing addStretch so the silhouette pins to the top of its pane
        # instead of floating in the middle when the user drags the splitter
        # to give Body Map extra height.
        body_section.content_layout().addWidget(
            self._body_map, alignment=Qt.AlignHCenter | Qt.AlignTop,
        )
        body_section.content_layout().addStretch(1)
        self._splitter.addWidget(body_section)

        # ── Genres (collapsible, mutually exclusive chips) ──
        genre_section = CollapsibleSection("Genres")
        genre_flow = FlowLayout(spacing=4)
        genre_container = QWidget()
        genre_container.setLayout(genre_flow)
        self._genre_chips: dict[str, ChipToggleButton] = {}
        for name in GENRE_NAMES:
            chip = ChipToggleButton(name, exclusive_group="genre")
            chip.toggled.connect(lambda checked, n=name: self._on_genre_toggled(n, checked))
            genre_flow.addWidget(chip)
            self._genre_chips[name] = chip
        genre_section.content_layout().addWidget(genre_container)
        # Pin the chip row to the top of the pane — without this, an
        # over-tall Genre pane stretches the container and centers chips.
        genre_section.content_layout().addStretch(1)
        self._splitter.addWidget(genre_section)

        # ── Avatars (collapsible, chip grid with search-and-add) ──
        avatar_section = CollapsibleSection("Avatars")
        self._avatar_search = QLineEdit()
        self._avatar_search.setPlaceholderText("Search or add avatar")
        self._avatar_search.textChanged.connect(self._on_avatar_search)
        self._avatar_search.returnPressed.connect(self._on_avatar_search_submit)
        avatar_section.content_layout().addWidget(self._avatar_search)

        avatar_scroll = QScrollArea()
        avatar_scroll.setWidgetResizable(True)
        avatar_scroll.setFrameShape(QFrame.NoFrame)
        self._avatar_container = QWidget()
        self._avatar_flow = FlowLayout(spacing=4)
        self._avatar_chips: dict[str, ChipToggleButton] = {}
        self._avatar_container.setLayout(self._avatar_flow)
        avatar_scroll.setWidget(self._avatar_container)
        avatar_section.content_layout().addWidget(avatar_scroll)
        self._splitter.addWidget(avatar_section)

        # ── Tags (search-and-add, AND-filter chips) ──
        tag_section = CollapsibleSection("Tags")
        self._tag_search = QLineEdit()
        self._tag_search.setPlaceholderText("Search or add tag")
        self._tag_search.textChanged.connect(self._on_tag_search)
        self._tag_search.returnPressed.connect(self._on_tag_search_submit)
        tag_section.content_layout().addWidget(self._tag_search)
        tag_scroll = QScrollArea()
        tag_scroll.setWidgetResizable(True)
        tag_scroll.setFrameShape(QFrame.NoFrame)
        self._tag_container = QWidget()
        self._tag_chips_layout = FlowLayout(spacing=4)
        self._tag_chips: dict[int, ChipToggleButton] = {}
        self._tag_container.setLayout(self._tag_chips_layout)
        tag_scroll.setWidget(self._tag_container)
        tag_section.content_layout().addWidget(tag_scroll)

        clear_btn = QPushButton("Clear Filters")
        clear_btn.clicked.connect(self._on_clear_filters)
        tag_section.content_layout().addWidget(clear_btn)

        manage_btn = QPushButton("Manage Tags")
        manage_btn.clicked.connect(self.manage_tags.emit)
        tag_section.content_layout().addWidget(manage_btn)
        self._splitter.addWidget(tag_section)

        # Initial stretch: Body Map + Genres are small intrinsic-height
        # widgets, Avatars and Tags want the rest. The QSettings restore
        # below overrides these on subsequent launches.
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 0)
        self._splitter.setStretchFactor(2, 2)
        self._splitter.setStretchFactor(3, 3)

        layout.addWidget(self._splitter)

        # Keep a list of section widgets for the rebalancer.
        self._sections: list[CollapsibleSection] = [
            body_section, genre_section, avatar_section, tag_section,
        ]
        # When a section collapses, donate its slack to its expanded
        # neighbours so we don't leave a gap below a collapsed header.
        for sec in self._sections:
            sec.collapsed_changed.connect(self._rebalance_after_collapse)

        # Debounced sidebar-splitter persistence — dragging fires the signal
        # on every mouse move, so we coalesce into one settle write.
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.setInterval(250)
        self._splitter_save_timer.timeout.connect(self._save_splitter_sizes)
        self._splitter.splitterMoved.connect(
            lambda *_: self._splitter_save_timer.start()
        )

        # Restore prior sizes after the first show — at construction time
        # the splitter has no allocated height yet, so setSizes would do
        # nothing useful. QTimer.singleShot(0) defers to after layout.
        QTimer.singleShot(0, self._restore_splitter_sizes)

    def _save_splitter_sizes(self):
        sizes = self._splitter.sizes()
        if any(sizes):
            QSettings().setValue(
                "sidebar_splitter_sizes", ",".join(str(s) for s in sizes)
            )

    def _rebalance_after_collapse(self, _collapsed: bool):
        """When a section toggles collapsed state, redistribute pane sizes
        so collapsed sections shrink to header height and expanded ones
        absorb the slack. Without this, collapsing an expanded section
        leaves an empty band of space below its header."""
        sizes = self._splitter.sizes()
        if not sizes:
            return
        total = sum(sizes)
        # Collect target sizes: collapsed sections claim only their header
        # (sizeHint), expanded ones split the remainder proportionally to
        # their current size.
        headers = []
        expanded_idx = []
        for i, sec in enumerate(self._sections):
            if sec.is_collapsed():
                headers.append((i, max(28, sec._header.sizeHint().height() + 6)))
            else:
                expanded_idx.append(i)
        new = list(sizes)
        for i, h in headers:
            new[i] = h
        remaining = total - sum(h for _, h in headers)
        if expanded_idx and remaining > 0:
            expanded_total = sum(max(1, sizes[i]) for i in expanded_idx)
            for i in expanded_idx:
                share = max(1, sizes[i]) / expanded_total
                new[i] = max(80, int(remaining * share))
        self._splitter.setSizes(new)
        self._save_splitter_sizes()

    def _restore_splitter_sizes(self):
        raw = QSettings().value("sidebar_splitter_sizes", "", type=str)
        if not raw:
            return
        try:
            sizes = [int(x) for x in raw.split(",")]
        except ValueError:
            return
        if len(sizes) == self._splitter.count() and all(s >= 0 for s in sizes):
            self._splitter.setSizes(sizes)

    # ── Tag chips rebuild ──

    # Hard cap on how many tag chips we render at once. The training crawler
    # can mint thousands of low-signal tags; cramming them all into one flow
    # layout silently collapses the panel and the user sees nothing. Default
    # behaviour: show tags actually in use; the search field surfaces the rest.
    _MAX_TAG_CHIPS = 200

    # Regex for the chip text format produced by _rebuild_*_chips
    # ("TagName (count)") — used by the surgical-update fast path.
    _CHIP_TEXT_RE = re.compile(r"^(.*) \((\d+)\)$")

    def bump_tag_counts(self, tag_ids: list[int], delta: int = 1) -> bool:
        """Adjust the visible counts on a small set of chips without
        rebuilding the whole sidebar. Returns True if every tag was
        handled in-place; False if any tag wasn't in a chip yet (caller
        should fall back to a full refresh in that case).

        ~1 ms vs ~160 ms for refresh() on a sidebar with ~200 chips.
        Position order isn't rebalanced — counts shift, but a chip that
        moves up the sort would only relocate on the next full refresh.
        That's fine for the typical interactive single-tag-add case.
        """
        for tag_id in tag_ids:
            chip = self._tag_chips.get(tag_id)
            if chip is None:
                # Avatar chips are keyed by name, not id — look up the
                # name with a single SQL hit rather than enumerating all
                # tags. Returns None if the tag was just deleted.
                tag = self._queries.get_tag_by_id(tag_id)
                if tag is None:
                    return False
                # Genre tags don't display a count in their chip text
                # (they're the five always-visible buttons), so there's
                # nothing to update — the call is a no-op for them.
                if tag[1] in GENRE_NAMES:
                    continue
                chip = self._avatar_chips.get(tag[1])
            if chip is None:
                return False  # not on screen; need a full refresh
            m = self._CHIP_TEXT_RE.match(chip.text())
            if m is None:
                return False
            base, cnt = m.group(1), int(m.group(2))
            chip.setText(f"{base} ({max(0, cnt + delta)})")
        return True

    def _rebuild_tag_chips(self, all_tags=None):
        while self._tag_chips_layout.count():
            item = self._tag_chips_layout.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.hide()
                w.deleteLater()
        self._tag_chips.clear()
        self._tag_cache.clear()

        query = self._tag_search.text().lower().strip()

        if all_tags is None:
            all_tags = self._queries.get_all_tags()
        # Cache every tag id by name so the AND-filter logic still works even
        # though we don't render a chip for each one.
        for tag_id, name, _color, _count in all_tags:
            self._tag_cache[name] = tag_id

        # With a search query: filter by substring across the full tag list.
        # Without a query: show only tags that are currently assigned to at
        # least one asset (+ anything in the active AND-filter set so its
        # chip stays visible while toggled on).
        candidates: list[tuple[int, str, int]] = []
        for tag_id, name, color, count in all_tags:
            if name in GENRE_NAMES or is_avatar_tag(name, color):
                continue
            if query:
                if query not in name.lower():
                    continue
            else:
                if count <= 0 and tag_id not in self._and_ids:
                    continue
            candidates.append((tag_id, name, count))

        # Selected first, then by usage count (most-used surface first).
        candidates.sort(key=lambda r: (
            0 if r[0] in self._and_ids else 1, -r[2], r[1].lower(),
        ))

        hidden_count = max(0, len(candidates) - self._MAX_TAG_CHIPS)
        candidates = candidates[: self._MAX_TAG_CHIPS]

        for tag_id, name, count in candidates:
            chip = ChipToggleButton(f"{name} ({count})")
            chip.setProperty("tag_id", tag_id)
            chip.toggled.connect(
                lambda checked, tid=tag_id: self._on_tag_chip_toggled(tid, checked)
            )
            self._tag_chips[tag_id] = chip
            if tag_id in self._and_ids:
                chip.set_active(True)
            self._tag_chips_layout.addWidget(chip)
            # Explicit show() — FlowLayout._do_layout skips items where
            # isVisible() is False, and freshly-created widgets default to
            # invisible until their parent gets its first paint pass. Without
            # this the sidebar appears empty after a restart even though the
            # tags are loaded.
            chip.show()

        # Footer hint when the list was trimmed — keeps the user from
        # thinking the panel is broken when the crawler leaves 2000 tags.
        if hidden_count > 0:
            hint = QLabel(f"+ {hidden_count} more — type to search")
            hint.setStyleSheet("color: palette(mid); font-size: 10px; padding: 4px;")
            self._tag_chips_layout.addWidget(hint)
            hint.show()

    def _on_tag_chip_toggled(self, tag_id: int, checked: bool):
        self._emit_filters()

    def refresh(self):
        # Refresh chips after a tag-state change (import, add, remove, rename,
        # delete). Selection state is held in id sets, not in the chips
        # themselves, so we drop any IDs that no longer reference live tags
        # and let the rebuild restore active state from the cleaned set.
        #
        # We fetch get_all_tags ONCE and reuse the rows in both rebuilds —
        # the previous version hit the DB three times per refresh (here +
        # each rebuild_*_chips). On larger libraries that adds up because
        # get_all_tags JOINs against asset_tags + assets for the count.
        all_tags = self._queries.get_all_tags()
        valid_tag_ids = {tid for tid, _, _, _ in all_tags}
        pre_and = set(self._and_ids)
        pre_avatar = set(self._avatar_ids)
        pre_genre = set(self._genre_ids)
        self._avatar_ids = self._avatar_ids & valid_tag_ids
        self._genre_ids = self._genre_ids & valid_tag_ids
        self._and_ids = [tid for tid in self._and_ids if tid in valid_tag_ids]

        self._rebuild_tag_chips(all_tags)
        self._rebuild_avatar_chips(all_tags)

        # Re-emit filters only if the active set actually changed (e.g. a
        # filtered-on tag was deleted). The common case — counts changed but
        # selection unchanged — must NOT re-emit, because the caller will
        # have already refreshed the model and a redundant emit triggers a
        # second model.refresh() through tag_filter_changed → _apply_filters.
        if (pre_and != set(self._and_ids)
                or pre_avatar != self._avatar_ids
                or pre_genre != self._genre_ids):
            self._emit_filters()

    _MAX_AVATAR_CHIPS = 120
    # TOP_AVATARS preserves rank order from the bundled ontology — used as a
    # tiebreaker when sorting equally-used chips. Hoisted to a class constant
    # so we don't rebuild the dict on every refresh.
    _AVATAR_RANK = {n: i for i, n in enumerate(TOP_AVATARS)}

    def _rebuild_avatar_chips(self, all_tags=None):
        while self._avatar_flow.count():
            item = self._avatar_flow.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.hide()
                w.deleteLater()
        self._avatar_chips.clear()

        query = self._avatar_search.text().lower().strip()

        if all_tags is None:
            all_tags = self._queries.get_all_tags()

        # A tag counts as an avatar if its name is in the bundled ontology
        # OR its color matches the avatar marker (user-coined avatars).
        avatar_rows: list[tuple[int, str, int]] = []
        for tag_id, name, color, count in all_tags:
            self._tag_cache[name] = tag_id
            if not is_avatar_tag(name, color):
                continue
            if query:
                if query not in name.lower():
                    continue
            else:
                # No query: only show avatars actually in use or already
                # picked as a filter. Without this guard, a long-tail
                # ontology of 200+ avatar names crashes the layout.
                if count <= 0 and tag_id not in self._avatar_ids:
                    continue
            avatar_rows.append((tag_id, name, count))

        avatar_rows.sort(key=lambda r: (
            0 if r[0] in self._avatar_ids else 1,
            -r[2], self._AVATAR_RANK.get(r[1], 999), r[1].lower(),
        ))

        hidden = max(0, len(avatar_rows) - self._MAX_AVATAR_CHIPS)
        avatar_rows = avatar_rows[: self._MAX_AVATAR_CHIPS]

        for tag_id, name, count in avatar_rows:
            chip = ChipToggleButton(f"{name} ({count})")
            chip.toggled.connect(lambda checked, n=name: self._on_avatar_chip_toggled(n, checked))
            self._avatar_chips[name] = chip
            if tag_id in self._avatar_ids:
                chip.set_active(True)
            self._avatar_flow.addWidget(chip)
            chip.show()  # See note in _rebuild_tag_chips.

        if hidden > 0:
            hint = QLabel(f"+ {hidden} more — type to search")
            hint.setStyleSheet("color: palette(mid); font-size: 10px; padding: 4px;")
            self._avatar_flow.addWidget(hint)
            hint.show()

    # ── Genre (mutually exclusive via ChipToggleButton exclusive_group) ──

    def _on_genre_toggled(self, genre_name: str, checked: bool):
        tag_id = self._find_tag_by_name(genre_name)
        if tag_id == 0:
            return
        if checked:
            self._genre_ids.add(tag_id)
        else:
            self._genre_ids.discard(tag_id)
        self._emit_filters()

    # ── Body map ──

    def _on_body_segment_toggled(self, tag_name: str, checked: bool):
        self._emit_filters()

    # ── Avatar chips ──

    def _on_sidebar_add_avatar(self, name: str):
        """Create a new avatar tag from the '+' chip."""
        existing = self._queries.get_tag_by_name(name)
        tag_id = existing[0] if existing else self._queries.create_tag(name, AVATAR_TAG_COLOR)
        if tag_id:
            self._avatar_ids.add(tag_id)
            self.refresh()
            self._emit_filters()

    def _on_sidebar_add_tag(self, name: str):
        """Create a new additional tag from the '+' chip."""
        existing = self._queries.get_tag_by_name(name)
        tag_id = existing[0] if existing else self._queries.create_tag(name)
        if tag_id:
            self.refresh()
            self._emit_filters()

    def _on_avatar_chip_toggled(self, name: str, checked: bool):
        tag_id = self._find_tag_by_name(name)
        if tag_id == 0:
            tag_id = self._queries.create_tag(name, AVATAR_TAG_COLOR)
            if tag_id == 0:
                return
        if checked:
            self._avatar_ids.add(tag_id)
        else:
            self._avatar_ids.discard(tag_id)
        self._emit_filters()

    def _on_avatar_search(self, text: str):
        query = text.lower().strip()
        for name, chip in self._avatar_chips.items():
            chip.setVisible(query in name.lower() if query else True)

    def _on_avatar_search_submit(self):
        """Press Enter on the avatar search — create a new avatar tag."""
        text = self._avatar_search.text().strip()
        if not text:
            return
        self._on_sidebar_add_avatar(text)
        self._avatar_search.clear()

    def _on_tag_search(self, _text: str):
        self._rebuild_tag_chips()

    def _on_tag_search_submit(self):
        """Press Enter on the tag search — create the tag if missing."""
        text = self._tag_search.text().strip()
        if not text:
            return
        self._on_sidebar_add_tag(text)
        self._tag_search.clear()

    # ── Clear ──

    def _on_clear_filters(self):
        for chip in self._tag_chips.values():
            chip.set_active(False)
        self._body_map.clear_active()
        self._genre_ids.clear()
        self._avatar_ids.clear()
        for chip in self._genre_chips.values():
            chip.set_active(False)
        for chip in self._avatar_chips.values():
            chip.set_active(False)
        self._emit_filters()

    # ── Emit ──

    def _emit_filters(self):
        if self._in_filter_emit:
            return
        self._in_filter_emit = True
        try:
            self._or_ids = list(dict.fromkeys(
                list(self._genre_ids) + list(self._avatar_ids) +
                [self._find_tag_by_name(t) for t in self._body_map.active_tags()]
            ))
            self._or_ids = [tid for tid in self._or_ids if tid != 0]

            self._and_ids = []
            for tag_id, chip in self._tag_chips.items():
                if chip.isChecked() and tag_id not in self._or_ids:
                    self._and_ids.append(tag_id)

            self.tag_filter_changed.emit(self._or_ids, self._and_ids)
        finally:
            self._in_filter_emit = False

    def _find_tag_by_name(self, name: str) -> int:
        if name in self._tag_cache:
            return self._tag_cache[name]
        # Fallback: check DB in case tag was created outside refresh
        for tag_id, tag_name, _, _ in self._queries.get_all_tags():
            self._tag_cache[tag_name] = tag_id
            if tag_name == name:
                return tag_id
        return 0


