from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QLineEdit, QFrame,
)

from vrc_organizer.database.queries import Queries
from vrc_organizer.tag_data import ALL_AVATAR_NAMES, GENRE_NAMES, TOP_AVATARS
from vrc_organizer.ui.body_map import BodyMapWidget
from vrc_organizer.ui.chip_button import ChipToggleButton
from vrc_organizer.ui.flow_layout import FlowLayout


class CollapsibleSection(QWidget):
    """Clickable header that hides/shows its content."""

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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Body Map (collapsible) ──
        body_section = CollapsibleSection("Body Map", start_collapsed=True)
        self._body_map = BodyMapWidget()
        self._body_map.segment_toggled.connect(self._on_body_segment_toggled)
        body_section.content_layout().addWidget(self._body_map, alignment=Qt.AlignCenter)
        layout.addWidget(body_section)
        layout.addWidget(_sep())

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
        layout.addWidget(genre_section)
        layout.addWidget(_sep())

        # ── Avatars (collapsible, chip grid with search-and-add) ──
        avatar_section = CollapsibleSection("Avatars")
        self._avatar_search = QLineEdit()
        self._avatar_search.setPlaceholderText("Search or add avatar")
        self._avatar_search.setFixedHeight(24)
        self._avatar_search.textChanged.connect(self._on_avatar_search)
        self._avatar_search.returnPressed.connect(self._on_avatar_search_submit)
        avatar_section.content_layout().addWidget(self._avatar_search)

        avatar_scroll = QScrollArea()
        avatar_scroll.setWidgetResizable(True)
        avatar_scroll.setMaximumHeight(200)
        avatar_scroll.setFrameShape(QFrame.NoFrame)
        self._avatar_container = QWidget()
        self._avatar_flow = FlowLayout(spacing=3)
        self._avatar_chips: dict[str, ChipToggleButton] = {}
        self._avatar_container.setLayout(self._avatar_flow)
        avatar_scroll.setWidget(self._avatar_container)
        avatar_section.content_layout().addWidget(avatar_scroll)
        layout.addWidget(avatar_section)
        layout.addWidget(_sep())

        # ── Tags (search-and-add, AND-filter chips) ──
        tag_header = QWidget()
        tag_layout = QVBoxLayout(tag_header)
        tag_layout.setContentsMargins(0, 0, 0, 0)
        tag_layout.addWidget(QLabel("Tags"))
        self._tag_search = QLineEdit()
        self._tag_search.setPlaceholderText("Search or add tag")
        self._tag_search.setFixedHeight(24)
        self._tag_search.textChanged.connect(self._on_tag_search)
        self._tag_search.returnPressed.connect(self._on_tag_search_submit)
        tag_layout.addWidget(self._tag_search)
        tag_scroll = QScrollArea()
        tag_scroll.setWidgetResizable(True)
        tag_scroll.setMaximumHeight(300)
        tag_scroll.setFrameShape(QFrame.NoFrame)
        self._tag_container = QWidget()
        self._tag_chips_layout = FlowLayout(spacing=3)
        self._tag_chips: dict[int, ChipToggleButton] = {}
        self._tag_container.setLayout(self._tag_chips_layout)
        tag_scroll.setWidget(self._tag_container)
        tag_layout.addWidget(tag_scroll)

        clear_btn = QPushButton("Clear Filters")
        clear_btn.clicked.connect(self._on_clear_filters)
        tag_layout.addWidget(clear_btn)

        manage_btn = QPushButton("Manage Tags")
        manage_btn.clicked.connect(self.manage_tags.emit)
        tag_layout.addWidget(manage_btn)
        layout.addWidget(tag_header)

        layout.addStretch()

    # ── Tag chips rebuild ──

    # Hard cap on how many tag chips we render at once. The training crawler
    # can mint thousands of low-signal tags; cramming them all into one flow
    # layout silently collapses the panel and the user sees nothing. Default
    # behaviour: show tags actually in use; the search field surfaces the rest.
    _MAX_TAG_CHIPS = 200

    def _rebuild_tag_chips(self):
        while self._tag_chips_layout.count():
            item = self._tag_chips_layout.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.hide()
                w.deleteLater()
        self._tag_chips.clear()
        self._tag_cache.clear()

        query = self._tag_search.text().lower().strip() if hasattr(self, "_tag_search") else ""

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
        for tag_id, name, _color, count in all_tags:
            if name in GENRE_NAMES or name in ALL_AVATAR_NAMES:
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

        # Footer hint when the list was trimmed — keeps the user from
        # thinking the panel is broken when the crawler leaves 2000 tags.
        if hidden_count > 0:
            hint = QLabel(f"+ {hidden_count} more — type to search")
            hint.setStyleSheet("color: palette(mid); font-size: 10px; padding: 4px;")
            self._tag_chips_layout.addWidget(hint)

    def _on_tag_chip_toggled(self, tag_id: int, checked: bool):
        self._emit_filters()

    def refresh(self):
        # Refresh chips after a tag-state change (import, add, remove, rename,
        # delete). Selection state is held in id sets, not in the chips
        # themselves, so we drop any IDs that no longer reference live tags
        # and let the rebuild restore active state from the cleaned set.
        valid_tag_ids = {tid for tid, _, _, _ in self._queries.get_all_tags()}
        pre_and = set(self._and_ids)
        pre_avatar = set(self._avatar_ids)
        pre_genre = set(self._genre_ids)
        self._avatar_ids = self._avatar_ids & valid_tag_ids
        self._genre_ids = self._genre_ids & valid_tag_ids
        self._and_ids = [tid for tid in self._and_ids if tid in valid_tag_ids]

        self._rebuild_tag_chips()
        self._rebuild_avatar_chips()

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

    def _rebuild_avatar_chips(self):
        while self._avatar_flow.count():
            item = self._avatar_flow.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.hide()
                w.deleteLater()
        self._avatar_chips.clear()

        query = self._avatar_search.text().lower().strip() if hasattr(self, "_avatar_search") else ""

        # Every tag whose name is in the avatar ontology counts as an avatar.
        avatar_rows: list[tuple[int, str, int]] = []
        for tag_id, name, _color, count in self._queries.get_all_tags():
            self._tag_cache[name] = tag_id
            if name not in ALL_AVATAR_NAMES:
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

        order = {n: i for i, n in enumerate(TOP_AVATARS)}
        avatar_rows.sort(key=lambda r: (
            0 if r[0] in self._avatar_ids else 1,
            -r[2], order.get(r[1], 999), r[1].lower(),
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

        if hidden > 0:
            hint = QLabel(f"+ {hidden} more — type to search")
            hint.setStyleSheet("color: palette(mid); font-size: 10px; padding: 4px;")
            self._avatar_flow.addWidget(hint)

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
        tag_id = existing[0] if existing else self._queries.create_tag(name, "#8b5cf6")
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
            tag_id = self._queries.create_tag(name, "#8b5cf6")
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


def _sep() -> QWidget:
    w = QWidget()
    w.setFixedHeight(1)
    w.setStyleSheet("background: #334155;")
    return w
