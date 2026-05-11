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


_COLLAPSED_STYLE = (
    "QPushButton { background: transparent; color: #64748b; border: none; "
    "padding: 4px 0; font-size: 12px; font-weight: bold; text-align: left; }"
    "QPushButton:hover { color: #334155; }"
)


class CollapsibleSection(QWidget):
    """Clickable header that hides/shows its content."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._header = QPushButton(f"  {title}")
        self._header.setFlat(True)
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setStyleSheet(_COLLAPSED_STYLE)
        self._header.clicked.connect(self._toggle)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(4, 0, 4, 4)
        self._content_layout.setSpacing(4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header)
        layout.addWidget(self._content)

        self._collapsed = False

    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._content.setVisible(not self._collapsed)
        arrow = "  " if self._collapsed else "  "
        self._header.setText(f"{arrow}{self._header.text()[2:]}")


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
        body_section = CollapsibleSection("Body Map")
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

        # ── Avatars (collapsible, chip grid with search) ──
        avatar_section = CollapsibleSection("Avatars")
        self._avatar_search = QLineEdit()
        self._avatar_search.setPlaceholderText("Search avatars...")
        self._avatar_search.textChanged.connect(self._on_avatar_search)
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

        # ── Tags (scrollable AND-filter chips) ──
        tag_header = QWidget()
        tag_layout = QVBoxLayout(tag_header)
        tag_layout.setContentsMargins(0, 0, 0, 0)
        tag_layout.addWidget(QLabel("Tags"))
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

        manage_btn = QPushButton("+ Manage Tags")
        manage_btn.clicked.connect(self.manage_tags.emit)
        tag_layout.addWidget(manage_btn)
        layout.addWidget(tag_header)

        layout.addStretch()

    # ── Tag chips rebuild ──

    def _rebuild_tag_chips(self):
        while self._tag_chips_layout.count():
            item = self._tag_chips_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._tag_chips.clear()
        self._tag_cache.clear()

        for tag_id, name, color, count in self._queries.get_all_tags():
            self._tag_cache[name] = tag_id
            if name in GENRE_NAMES or name in ALL_AVATAR_NAMES:
                continue
            chip = ChipToggleButton(f"{name} ({count})")
            chip.setProperty("tag_id", tag_id)
            chip.toggled.connect(
                lambda checked, tid=tag_id: self._on_tag_chip_toggled(tid, checked)
            )
            self._tag_chips_layout.addWidget(chip)
            self._tag_chips[tag_id] = chip

    def _on_tag_chip_toggled(self, tag_id: int, checked: bool):
        self._emit_filters()

    def refresh(self):
        self._rebuild_tag_chips()
        self._rebuild_avatar_chips()

    def _rebuild_avatar_chips(self):
        while self._avatar_flow.count():
            item = self._avatar_flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._avatar_chips.clear()

        avatar_counts: dict[str, tuple[int, int]] = {}  # name → (tag_id, count)
        for tag_id, name, color, count in self._queries.get_all_tags():
            if name in ALL_AVATAR_NAMES:
                avatar_counts[name] = (tag_id, count)
                self._tag_cache[name] = tag_id

        for name in TOP_AVATARS:
            if name not in avatar_counts:
                continue
            tag_id, count = avatar_counts[name]
            if count == 0:
                continue
            chip = ChipToggleButton(f"{name} ({count})")
            chip.toggled.connect(lambda checked, n=name: self._on_avatar_chip_toggled(n, checked))
            self._avatar_flow.addWidget(chip)
            self._avatar_chips[name] = chip
            # Restore checked state if this avatar is in the active filter
            if tag_id in self._avatar_ids:
                chip.set_active(True)

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
    w.setStyleSheet("background: #e2e8f0;")
    return w
