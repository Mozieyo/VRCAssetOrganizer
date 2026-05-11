from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QFormLayout, QTreeWidget, QTreeWidgetItem, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QFrame, QMenu, QApplication,
    QDialog,
)

from vrc_organizer.tag_data import TOP_AVATARS, GENRE_NAMES
from vrc_organizer.ui.chip_button import ChipToggleButton
from vrc_organizer.ui.flow_layout import FlowLayout

from vrc_organizer.database.queries import Queries
from vrc_organizer.models.asset import Asset


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size:,} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


def _detailed_type(name: str, etype: str) -> str:
    """Append file extension to generic types like 'image' → 'image (psd)'."""
    if etype in ("image", "video", "audio", "shader"):
        suffix = Path(name).suffix.lower()
        if suffix:
            return f"{etype} ({suffix[1:]})"
    return etype


class TagChip(QLabel):
    remove_clicked = Signal(int)

    def __init__(self, tag_id: int, name: str, color: str, removable: bool = True):
        super().__init__(name)
        self._tag_id = tag_id
        self.setStyleSheet(
            f"QLabel {{ background: {color}; color: white; "
            f"padding: 2px 8px; border-radius: 8px; font-size: 11px; }}"
        )
        if removable:
            self.setToolTip("Right-click to remove tag")
            self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.remove_clicked.emit(self._tag_id)


class InspectorPanel(QWidget):
    tag_added = Signal(int, int)         # asset_id, tag_id
    tag_removed = Signal(int, int)       # asset_id, tag_id
    notes_changed = Signal(int, str)     # asset_id, notes
    open_with = Signal(str, Path, int)   # tool_name, filepath, asset_id

    def __init__(self, queries: Queries, tool_registry=None, parent=None):
        super().__init__(parent)
        self._queries = queries
        self._tool_registry = tool_registry
        self._asset: Asset | None = None
        self._setup_ui()

    def _setup_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(12)

        # Thumbnail
        self._thumb_label = QLabel()
        self._thumb_label.setAlignment(Qt.AlignCenter)
        self._thumb_label.setMinimumHeight(200)
        self._thumb_label.setAutoFillBackground(True)
        self._thumb_label.setStyleSheet("QLabel { border-radius: 8px; }")
        layout.addWidget(self._thumb_label)

        # Filename
        self._filename_label = QLabel("Select an asset")
        self._filename_label.setWordWrap(True)
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        self._filename_label.setFont(font)
        layout.addWidget(self._filename_label)

        # Info form
        info_group = QGroupBox("Info")
        info_form = QFormLayout(info_group)
        self._type_label = QLabel("-")
        self._size_label = QLabel("-")
        self._path_label = QLabel("-")
        self._path_label.setWordWrap(True)
        self._modified_label = QLabel("-")
        self._added_label = QLabel("-")
        self._notes_edit = QLineEdit()
        self._notes_edit.setPlaceholderText("-")
        self._notes_edit.setStyleSheet(
            "QLineEdit { border: none; background: transparent; padding: 0; }"
            "QLineEdit:focus { border: 1px solid #3b82f6; border-radius: 3px; padding: 2px; background: palette(base); }"
        )
        self._notes_edit.textChanged.connect(self._on_notes_changed)
        info_form.addRow("Type:", self._type_label)
        info_form.addRow("Size:", self._size_label)
        info_form.addRow("Path:", self._path_label)
        info_form.addRow("Modified:", self._modified_label)
        info_form.addRow("Added:", self._added_label)
        info_form.addRow("Notes:", self._notes_edit)
        layout.addWidget(info_group)

        # Genre (chip-based, mutually exclusive)
        genre_group = QGroupBox("Genre")
        genre_layout = QVBoxLayout(genre_group)
        self._genre_flow = FlowLayout()
        self._genre_chips: dict[str, ChipToggleButton] = {}
        for name in GENRE_NAMES:
            chip = ChipToggleButton(name, exclusive_group="genre")
            chip.toggled_on.connect(self._on_genre_chip_toggled)
            self._genre_flow.addWidget(chip)
            self._genre_chips[name] = chip
        genre_layout.addLayout(self._genre_flow)
        layout.addWidget(genre_group)

        # Avatar tags (chip-based toggle)
        avatar_group = QGroupBox("Avatar")
        avatar_layout = QVBoxLayout(avatar_group)
        avatar_scroll = QScrollArea()
        avatar_scroll.setWidgetResizable(True)
        avatar_scroll.setMaximumHeight(180)
        avatar_scroll.setFrameShape(QFrame.NoFrame)
        avatar_container = QWidget()
        self._avatar_flow = FlowLayout()
        self._avatar_chips: dict[str, ChipToggleButton] = {}
        for name in TOP_AVATARS[:30]:
            chip = ChipToggleButton(name)
            chip.toggled.connect(lambda checked, n=name: self._on_avatar_chip_toggled(n, checked))
            self._avatar_flow.addWidget(chip)
            self._avatar_chips[name] = chip
        avatar_container.setLayout(self._avatar_flow)
        avatar_scroll.setWidget(avatar_container)
        avatar_layout.addWidget(avatar_scroll)
        layout.addWidget(avatar_group)

        # Additional Tags
        tags_group = QGroupBox("Additional Tags")
        tags_layout = QVBoxLayout(tags_group)
        self._tags_flow = FlowLayout()
        tags_layout.addLayout(self._tags_flow)
        add_tag_btn = QPushButton("+ Add Tag")
        add_tag_btn.clicked.connect(self._on_add_tag)
        tags_layout.addWidget(add_tag_btn)
        layout.addWidget(tags_group)

        # Contents tree
        contents_group = QGroupBox("Contents")
        contents_layout = QVBoxLayout(contents_group)
        self._contents_tree = QTreeWidget()
        self._contents_tree.setHeaderLabels(["Name", "Type", "Size"])
        self._contents_tree.setRootIsDecorated(True)
        self._contents_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._contents_tree.customContextMenuRequested.connect(self._on_contents_context_menu)
        self._contents_tree.itemDoubleClicked.connect(self._on_contents_double_clicked)
        contents_layout.addWidget(self._contents_tree)
        layout.addWidget(contents_group)

        # Open With
        tools_group = QGroupBox("Open With")
        tools_layout = QVBoxLayout(tools_group)
        self._tools_layout = tools_layout
        layout.addWidget(tools_group)

        layout.addStretch()

        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def show_empty(self):
        self._asset = None
        self._thumb_label.setText("Select an asset")
        self._filename_label.setText("Select an asset")
        self._type_label.setText("-")
        self._size_label.setText("-")
        self._path_label.setText("-")
        self._modified_label.setText("-")
        self._added_label.setText("-")
        self._notes_edit.blockSignals(True)
        self._notes_edit.clear()
        self._notes_edit.blockSignals(False)
        self._contents_tree.clear()
        for chip in self._genre_chips.values():
            chip.set_active(False)
        for chip in self._avatar_chips.values():
            chip.set_active(False)
        self._clear_flow(self._tags_flow)
        while self._tools_layout.count():
            item = self._tools_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def show_asset(self, asset: Asset):
        self._asset = asset
        self._notes_edit.blockSignals(True)

        self._filename_label.setText(asset.filename)

        # Thumbnail
        if asset.thumbnail:
            pix = QPixmap(str(asset.thumbnail))
            if not pix.isNull():
                scaled = pix.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self._thumb_label.setPixmap(scaled)
            else:
                self._thumb_label.setText("No preview")
        else:
            self._thumb_label.setText("No preview")

        # Info
        self._type_label.setText(asset.filetype)
        size_mb = asset.file_size / (1024 * 1024)
        self._size_label.setText(f"{size_mb:.1f} MB" if size_mb >= 0.1 else f"{asset.file_size:,} B")
        full_path = str(asset.filepath)
        fm = self._path_label.fontMetrics()
        avail_w = max(self._path_label.width(), 200)
        elided = fm.elidedText(full_path, Qt.ElideLeft, avail_w)
        self._path_label.setText(elided)
        self._path_label.setToolTip(full_path)

        from datetime import datetime
        self._modified_label.setText(
            datetime.fromtimestamp(asset.mod_time).strftime("%Y-%m-%d %H:%M")
        )
        self._added_label.setText(
            datetime.fromtimestamp(asset.date_added).strftime("%Y-%m-%d %H:%M")
        )

        # Tags
        self._refresh_tags()

        # Contents
        self._contents_tree.clear()
        scan_results = self._queries.get_scan_results(asset.id)
        if scan_results:
            by_type: dict[str, list] = {}
            for name, etype, size in scan_results:
                by_type.setdefault(etype, []).append((name, size))

            for etype, entries in sorted(by_type.items()):
                parent = QTreeWidgetItem([f"{etype}s", "", f"{len(entries)} items"])
                for name, size in entries:
                    size_str = _format_size(size)
                    entry_type = _detailed_type(name, etype)
                    child = QTreeWidgetItem(parent, [name.split("/")[-1], entry_type, size_str])
                    child.setData(0, Qt.UserRole, name)  # full entry_name for context actions
                self._contents_tree.addTopLevelItem(parent)
            self._contents_tree.expandAll()
            self._contents_tree.setVisible(True)
        else:
            self._contents_tree.setVisible(False)

        # Notes
        self._notes_edit.setText(asset.notes)
        self._notes_edit.blockSignals(False)

        # Tools
        self._refresh_tools()

    def _clear_flow(self, flow: FlowLayout):
        while flow.count():
            item = flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _refresh_tags(self):
        self._clear_flow(self._tags_flow)

        if self._asset is None:
            return

        asset_tags = self._queries.get_tags_for_asset(self._asset.id)
        avatar_names = set(TOP_AVATARS)

        current_genre = None
        assigned_avatars: set[str] = set()
        for tag_id, name, color in asset_tags:
            if name in GENRE_NAMES:
                current_genre = name
                continue

            if name in avatar_names:
                assigned_avatars.add(name)
                continue

            # Additional tag
            chip = TagChip(tag_id, name, color, removable=True)
            chip.remove_clicked.connect(
                lambda tid=tag_id: self.tag_removed.emit(self._asset.id, tid)
            )
            self._tags_flow.addWidget(chip)

        # Update genre chips
        for name, chip in self._genre_chips.items():
            chip.set_active(name == current_genre)

        # Update avatar chips
        for name, chip in self._avatar_chips.items():
            chip.set_active(name in assigned_avatars)

    def _on_contents_double_clicked(self, item: QTreeWidgetItem, column: int):
        if self._asset is None:
            return
        entry_name = item.data(0, Qt.UserRole)
        filepath = self._asset.filepath
        if entry_name and filepath.is_dir():
            target = filepath / entry_name
            if target.exists():
                os.startfile(str(target))
                return
            # Extracted file not found — open the parent directory instead
            parent = filepath
            while not parent.exists() and parent.parent != parent:
                parent = parent.parent
            if parent.exists():
                os.startfile(str(parent))
                return
        if filepath.exists():
            os.startfile(str(filepath))

    def _on_contents_context_menu(self, pos):
        item = self._contents_tree.itemAt(pos)
        menu = QMenu(self)

        entry_name = item.data(0, Qt.UserRole) if item else None
        if entry_name:
            menu.addAction("Copy Entry Path", lambda: QApplication.clipboard().setText(entry_name))

        if self._asset:
            menu.addAction("Copy Asset Path", lambda: QApplication.clipboard().setText(str(self._asset.filepath)))
            menu.addSeparator()
            menu.addAction("Open Asset File", lambda: os.startfile(str(self._asset.filepath)))
            menu.addAction("Open Containing Folder", lambda: os.startfile(str(self._asset.filepath.parent)))

        menu.exec(self._contents_tree.viewport().mapToGlobal(pos))

    def _refresh_tools(self):
        # Clear existing tool buttons
        while self._tools_layout.count():
            item = self._tools_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self._asset is None:
            return

        if self._tool_registry:
            tools = [t.name for t in self._tool_registry.for_filetype(self._asset.filetype)]
        else:
            from vrc_organizer.tools.registry import DEFAULT_TOOL_MAP
            tools = DEFAULT_TOOL_MAP.get(self._asset.filetype, ["Default Viewer"])

        for tool_name in tools:
            btn = QPushButton(f"Open in {tool_name}")
            btn.clicked.connect(
                lambda checked, tn=tool_name: self.open_with.emit(
                    tn, self._asset.filepath, self._asset.id
                )
            )
            self._tools_layout.addWidget(btn)

    def _on_add_tag(self):
        if self._asset is None:
            return
        all_tags = self._queries.get_all_tags()
        asset_tags = self._queries.get_tags_for_asset(self._asset.id)
        assigned_ids = {t[0] for t in asset_tags}
        excluded_names = self.GENRE_SET | set(TOP_AVATARS)
        unassigned = [(tid, name, color) for tid, name, color, _ in all_tags
                      if tid not in assigned_ids and name not in excluded_names]
        if not unassigned:
            return

        popup = QDialog(self, Qt.Popup)
        popup.setWindowTitle("Add Tag")
        popup.setMinimumWidth(280)
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        search = QLineEdit()
        search.setPlaceholderText("Filter tags...")
        layout.addWidget(search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(220)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        flow = FlowLayout(spacing=4)
        chip_map: dict[str, QPushButton] = {}

        for tag_id, name, color in unassigned:
            chip = QPushButton(name)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(
                f"QPushButton {{ background: {color}; color: white; "
                f"padding: 4px 10px; border-radius: 10px; font-size: 11px; border: none; }}"
                f"QPushButton:hover {{ opacity: 0.85; }}"
            )
            chip.clicked.connect(
                lambda checked=False, tid=tag_id: (
                    self.tag_added.emit(self._asset.id, tid),
                    popup.close(),
                )
            )
            flow.addWidget(chip)
            chip_map[name.lower()] = chip

        def _on_search(text: str):
            q = text.lower().strip()
            for nl, c in chip_map.items():
                c.setVisible(q in nl if q else True)

        search.textChanged.connect(_on_search)

        container.setLayout(flow)
        scroll.setWidget(container)
        layout.addWidget(scroll)

        btn = self.sender()
        if isinstance(btn, QPushButton):
            popup.move(btn.mapToGlobal(btn.rect().bottomLeft()))
        else:
            popup.move(self.mapToGlobal(self.rect().center()))
        popup.exec()

    GENRE_SET = set(GENRE_NAMES)

    def _on_avatar_chip_toggled(self, name: str, checked: bool):
        if self._asset is None:
            return
        asset_tags = self._queries.get_tags_for_asset(self._asset.id)
        assigned = {t[1]: t[0] for t in asset_tags}

        if checked:
            if name in assigned:
                return  # Already assigned
            tag_id = 0
            for tid, tname, _, _ in self._queries.get_all_tags():
                if tname == name:
                    tag_id = tid
                    break
            if not tag_id:
                tag_id = self._queries.create_tag(name, "#8b5cf6")
            if tag_id:
                self.tag_added.emit(self._asset.id, tag_id)
        else:
            if name in assigned:
                self.tag_removed.emit(self._asset.id, assigned[name])

    def _on_genre_chip_toggled(self, new_genre: str):
        if self._asset is None:
            return
        asset_tags = self._queries.get_tags_for_asset(self._asset.id)

        # Remove old genre tags
        for tag_id, name, _ in asset_tags:
            if name in self.GENRE_SET:
                self.tag_removed.emit(self._asset.id, tag_id)

        # Find or create the new genre tag
        tag_id = 0
        for tid, tname, _, _ in self._queries.get_all_tags():
            if tname == new_genre:
                tag_id = tid
                break
        if not tag_id:
            tag_id = self._queries.create_tag(new_genre, "#6366f1")
        if tag_id:
            self.tag_added.emit(self._asset.id, tag_id)

    def _on_notes_changed(self):
        if self._asset is None:
            return
        self.notes_changed.emit(self._asset.id, self._notes_edit.text())
