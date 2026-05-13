from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QPixmap, QFont, QPalette
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QFormLayout, QTreeWidget, QTreeWidgetItem, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QFrame, QMenu, QApplication,
    QDialog, QMessageBox,
)

from vrc_organizer.tag_data import ALL_AVATAR_NAMES, TOP_AVATARS, GENRE_NAMES
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


class TagChip(QFrame):
    """Tag chip with double-click rename and right-click context menu."""
    remove_from_asset = Signal(int)      # tag_id
    delete_requested = Signal(int)       # tag_id
    rename_requested = Signal(int, str)  # tag_id, new_name

    def __init__(self, tag_id: int, name: str, color: str,
                 removable: bool = True, renameable: bool = False,
                 queries=None):
        super().__init__()
        self._tag_id = tag_id
        self._name = name
        self._color = color
        self._queries = queries
        self._removable = removable
        self._renameable = renameable
        self._editing = False

        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(24)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 2, 8, 2)
        self._layout.setSpacing(0)

        self._label = QLabel(name)
        self._label.setStyleSheet("color: white; font-size: 11px; background: transparent;")
        self._layout.addWidget(self._label)

        self._refresh_style()

    def _refresh_style(self):
        self.setStyleSheet(
            f"TagChip {{ background: {self._color}; border-radius: 10px; }}"
        )

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self._renameable:
            self._start_rename()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton and self._removable:
            self._show_context_menu(event.globalPos())
            return
        super().mousePressEvent(event)

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        if self._removable:
            menu.addAction("Remove from Asset",
                           lambda: self.remove_from_asset.emit(self._tag_id))
        if self._renameable:
            menu.addAction("Rename", self._start_rename)
            menu.addSeparator()
            menu.addAction("Delete Tag", self._confirm_delete)
        menu.exec(pos)

    def _confirm_delete(self):
        count = 0
        if self._queries:
            count = self._queries.get_tag_usage_count(self._tag_id)
        if count > 5:
            reply = QMessageBox.warning(
                self, "Delete Tag?",
                f"Tag \"{self._name}\" is used by {count} assets.\n\n"
                "Deleting it will remove it from all assets.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
        else:
            reply = QMessageBox.question(
                self, "Delete Tag?",
                f"Delete tag \"{self._name}\"?\n"
                f"It is used by {count} asset(s).",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
        if reply == QMessageBox.Yes:
            self.delete_requested.emit(self._tag_id)

    @staticmethod
    def _is_dark() -> bool:
        app = QApplication.instance()
        if app:
            return app.palette().color(QPalette.Window).lightness() < 128
        return False

    def _start_rename(self):
        if self._editing:
            return
        self._editing = True
        self._label.hide()
        edit = QLineEdit(self._name)
        is_dark = self._is_dark()
        if is_dark:
            edit.setStyleSheet(
                "QLineEdit { color: white; font-size: 11px; background: rgba(255,255,255,0.15); "
                "border: 1px solid rgba(255,255,255,0.5); border-radius: 4px; padding: 0 4px; }"
            )
        else:
            edit.setStyleSheet(
                "QLineEdit { color: white; font-size: 11px; background: rgba(0,0,0,0.3); "
                "border: 1px solid rgba(255,255,255,0.5); border-radius: 4px; padding: 0 4px; }"
            )
        edit.selectAll()
        edit.setFixedWidth(max(60, self._label.sizeHint().width() + 8))
        edit.returnPressed.connect(lambda: self._finish_rename(edit))
        edit.editingFinished.connect(lambda: self._finish_rename(edit))
        edit.installEventFilter(self)
        self._layout.insertWidget(1, edit)
        edit.setFocus()
        self._edit = edit

    def eventFilter(self, obj, event):
        if event.type() == QEvent.FocusOut and self._editing and obj is self._edit:
            self._finish_rename(self._edit)
        return super().eventFilter(obj, event)

    def _finish_rename(self, edit: QLineEdit = None):
        if not self._editing:
            return
        if edit is None:
            edit = self._edit
        if edit is None:
            self._editing = False
            return
        new_name = edit.text().strip()
        if edit is self._edit:
            edit.removeEventFilter(self)
        edit.deleteLater()
        self._edit = None
        self._label.show()
        self._editing = False
        if new_name and new_name != self._name:
            self.rename_requested.emit(self._tag_id, new_name)

    @property
    def tag_name(self) -> str:
        return self._name

    def update_name(self, new_name: str):
        self._name = new_name
        self._label.setText(new_name)


class AddTagChip(QFrame):
    """Inline tag creation chip that expands into a text input."""
    tag_created = Signal(str)

    LIGHT_STYLE = (
        "AddTagChip { background: #e2e8f0; border-radius: 10px; }"
        "AddTagChip:hover { background: #cbd5e1; }"
    )
    DARK_STYLE = (
        "AddTagChip { background: #374151; border-radius: 10px; }"
        "AddTagChip:hover { background: #4b5563; }"
    )

    def __init__(self, placeholder: str = "+"):
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(24)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 2, 8, 2)
        self._layout.setSpacing(0)

        self._label = QLabel(placeholder)
        self._label.setStyleSheet(
            "color: #64748b; font-size: 11px; background: transparent; font-weight: bold;"
        )
        self._layout.addWidget(self._label)

        self._apply_style()
        self._editing = False
        self._edit = None

    @staticmethod
    def _is_dark() -> bool:
        app = QApplication.instance()
        if app:
            return app.palette().color(QPalette.Window).lightness() < 128
        return False

    def _apply_style(self):
        self.setStyleSheet(self.DARK_STYLE if self._is_dark() else self.LIGHT_STYLE)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._editing:
            self._start_input()
            return
        super().mousePressEvent(event)

    def _start_input(self):
        self._editing = True
        self._label.hide()
        edit = QLineEdit()
        edit.setPlaceholderText("New tag...")
        is_dark = self._is_dark()
        edit_bg = "#374151" if is_dark else "white"
        edit_color = "#e5e7eb" if is_dark else "black"
        edit.setStyleSheet(
            f"QLineEdit {{ font-size: 11px; background: {edit_bg}; color: {edit_color}; "
            f"border: 1px solid #3b82f6; border-radius: 4px; padding: 0 4px; }}"
        )
        edit.setFixedWidth(100)
        edit.returnPressed.connect(lambda: self._finish(edit))
        edit.editingFinished.connect(lambda: self._finish(edit))
        edit.installEventFilter(self)
        self._layout.insertWidget(1, edit)
        edit.setFocus()
        self._edit = edit

    def eventFilter(self, obj, event):
        if event.type() == QEvent.FocusOut and self._editing and obj is self._edit:
            self._finish(self._edit)
        return super().eventFilter(obj, event)

    def _finish(self, edit: QLineEdit = None):
        if not self._editing:
            return
        if edit is None:
            edit = self._edit
        if edit is None:
            self._editing = False
            return
        name = edit.text().strip()
        if edit is self._edit:
            edit.removeEventFilter(self)
        edit.deleteLater()
        self._edit = None
        self._label.show()
        self._editing = False
        if name:
            self.tag_created.emit(name)


class InspectorPanel(QWidget):
    tag_added = Signal(int, int)         # asset_id, tag_id
    tag_removed = Signal(int, int)       # asset_id, tag_id
    tag_renamed = Signal(int, str)       # tag_id, new_name
    tag_deleted = Signal(int)            # tag_id
    notes_changed = Signal(int, str)     # asset_id, notes
    open_with = Signal(str, Path, int)   # tool_name, filepath, asset_id

    def __init__(self, queries: Queries, tool_registry=None, parent=None):
        super().__init__(parent)
        self._queries = queries
        self._tool_registry = tool_registry
        self._asset: Asset | None = None
        self._avatar_search_chips: dict[str, ChipToggleButton] = {}
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
        self._avatar_search = QLineEdit()
        self._avatar_search.setPlaceholderText("Search avatars...")
        self._avatar_search.textChanged.connect(self._on_avatar_search)
        avatar_layout.addWidget(self._avatar_search)
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

        # Additional Tags (flow layout in a scroll area)
        tags_group = QGroupBox("Additional Tags")
        tags_layout = QVBoxLayout(tags_group)
        tags_layout.setContentsMargins(0, 4, 0, 0)
        tags_scroll = QScrollArea()
        tags_scroll.setWidgetResizable(True)
        tags_scroll.setMaximumHeight(160)
        tags_scroll.setFrameShape(QFrame.NoFrame)
        self._tags_container = QWidget()
        self._tags_container.setMinimumHeight(32)
        self._tags_flow = FlowLayout(spacing=4)
        self._tags_container.setLayout(self._tags_flow)
        tags_scroll.setWidget(self._tags_container)
        tags_layout.addWidget(tags_scroll)
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
            chip.setVisible(True)
        for chip in self._avatar_search_chips.values():
            chip.hide()
            chip.deleteLater()
        self._avatar_search_chips.clear()
        self._avatar_search.clear()
        self._clear_flow(self._tags_flow)
        if hasattr(self, '_avatar_add_chip'):
            try:
                self._avatar_add_chip.hide()
            except RuntimeError:
                del self._avatar_add_chip
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

        self._modified_label.setText(
            datetime.fromtimestamp(asset.mod_time).strftime("%Y-%m-%d %H:%M")
        )
        self._added_label.setText(
            datetime.fromtimestamp(asset.date_added).strftime("%Y-%m-%d %H:%M")
        )

        # Tags
        self._refresh_tags()

        # Contents - build hierarchical tree from paths
        self._contents_tree.clear()
        scan_results = self._queries.get_scan_results(asset.id)
        if scan_results:
            self._build_contents_tree(scan_results)
            self._contents_tree.expandToDepth(1)
            self._contents_tree.setVisible(True)
        else:
            self._contents_tree.setVisible(False)

        # Notes
        self._notes_edit.setText(asset.notes)
        self._notes_edit.blockSignals(False)

        # Tools
        self._refresh_tools()

    def _build_contents_tree(self, scan_results: list[tuple[str, str, int]]):
        """Build a hierarchical tree from flat entry paths."""
        # nodes[path] = QTreeWidgetItem for that folder
        nodes: dict[str, QTreeWidgetItem] = {}

        for name, etype, size in sorted(scan_results, key=lambda x: x[0].lower()):
            parts = name.replace("\\", "/").split("/")
            parent_item = None

            # Create folder nodes as needed
            for i in range(len(parts) - 1):
                folder_path = "/".join(parts[:i + 1])
                if folder_path not in nodes:
                    folder_name = parts[i]
                    node = QTreeWidgetItem([folder_name, "", ""])
                    node.setData(0, Qt.UserRole + 1, "folder")
                    if parent_item:
                        parent_item.addChild(node)
                    else:
                        self._contents_tree.addTopLevelItem(node)
                    nodes[folder_path] = node
                parent_item = nodes[folder_path]

            # Add the file entry
            filename = parts[-1]
            size_str = _format_size(size)
            entry_type = _detailed_type(name, etype)
            child = QTreeWidgetItem([filename, entry_type, size_str])
            child.setData(0, Qt.UserRole, name)
            child.setToolTip(0, name)  # Show full path on hover
            if parent_item:
                parent_item.addChild(child)
            else:
                self._contents_tree.addTopLevelItem(child)

    def _clear_flow(self, flow: FlowLayout):
        while flow.count():
            item = flow.takeAt(0)
            if item and item.widget():
                item.widget().hide()
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
            chip = TagChip(tag_id, name, color, removable=True, renameable=True,
                           queries=self._queries)
            chip.remove_from_asset.connect(
                lambda tid=tag_id: self.tag_removed.emit(self._asset.id, tid)
            )
            chip.rename_requested.connect(self._on_tag_renamed)
            chip.delete_requested.connect(self._on_tag_deleted)
            self._tags_flow.addWidget(chip)

        # Add inline creation chip after assigned tags
        add_chip = AddTagChip()
        add_chip.tag_created.connect(self._on_inline_tag_created)
        self._tags_flow.addWidget(add_chip)

        self._tags_flow.invalidate()
        self._tags_flow.activate()
        # Set minimum height so the QScrollArea doesn't collapse the container
        needed = self._tags_flow.heightForWidth(self._tags_container.width())
        self._tags_container.setMinimumHeight(max(needed, 32))
        self._tags_container.adjustSize()

        # Update genre chips
        for name, chip in self._genre_chips.items():
            chip.set_active(name == current_genre)

        # Update avatar chips — selected first, "+" chip, then unselected
        self._reorder_avatar_flow(assigned_avatars)

    def _reorder_avatar_flow(self, assigned: set[str]):
        """Reorder avatar flow: selected chips first, then '+' chip, then unselected."""
        # Update active states for base and search chips
        for name, chip in self._avatar_chips.items():
            chip.set_active(name in assigned)
        for name, chip in self._avatar_search_chips.items():
            chip.set_active(name in assigned)

        # Remove all items from flow without deleting chip widgets
        old_widgets: list[QWidget] = []
        while self._avatar_flow.count():
            item = self._avatar_flow.takeAt(0)
            if item:
                w = item.widget()
                if w is not None:
                    old_widgets.append(w)

        # Delete any non-chip widgets (old AddTagChips)
        chip_set = set(self._avatar_chips.values()) | set(self._avatar_search_chips.values())
        for w in old_widgets:
            if w not in chip_set:
                w.deleteLater()
                if hasattr(self, '_avatar_add_chip') and w is self._avatar_add_chip:
                    del self._avatar_add_chip

        # Collect all chips, ordered: selected → unselected
        all_chips: list[tuple[str, ChipToggleButton]] = []
        all_chips.extend(self._avatar_chips.items())
        all_chips.extend(self._avatar_search_chips.items())
        selected = [c for n, c in all_chips if n in assigned]
        unselected = [c for n, c in all_chips if n not in assigned]

        for chip in selected:
            self._avatar_flow.addWidget(chip)
        if not hasattr(self, '_avatar_add_chip'):
            self._avatar_add_chip = AddTagChip()
            self._avatar_add_chip.tag_created.connect(self._on_inline_tag_created)
        self._avatar_flow.addWidget(self._avatar_add_chip)
        for chip in unselected:
            self._avatar_flow.addWidget(chip)

    def _on_inline_tag_created(self, name: str):
        """Handle tag creation from '+' chip."""
        if self._asset is None:
            return
        tag_id = self._find_or_create_tag(name)
        if tag_id:
            self.tag_added.emit(self._asset.id, tag_id)

    def _on_avatar_search(self, text: str):
        """Filter avatar chips by search text and add DB matches."""
        query = text.lower().strip()

        # Remove previous dynamic search chips
        for chip in self._avatar_search_chips.values():
            chip.hide()
            chip.deleteLater()
        self._avatar_search_chips.clear()

        if query:
            # Query DB for avatar tags matching the search
            for tag_id, name, color, count in self._queries.get_all_tags():
                if name in ALL_AVATAR_NAMES and name not in self._avatar_chips and query in name.lower():
                    chip = ChipToggleButton(f"{name} ({count})")
                    chip.toggled.connect(lambda checked, n=name: self._on_avatar_chip_toggled(n, checked))
                    self._avatar_search_chips[name] = chip

        # Filter base chip visibility
        for name, chip in self._avatar_chips.items():
            chip.setVisible(not query or query in name.lower())

        # Reorder with current asset state
        if self._asset is not None:
            self._refresh_tags()

    def _on_tag_renamed(self, tag_id: int, new_name: str):
        """Forward tag rename to main window."""
        self.tag_renamed.emit(tag_id, new_name)

    def _on_tag_deleted(self, tag_id: int):
        """Forward tag deletion to main window."""
        self.tag_deleted.emit(tag_id)

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

    def _find_or_create_tag(self, name: str, color: str = "#6366f1") -> int:
        """Return tag ID for name, creating if needed."""
        existing = self._queries.get_tag_by_name(name)
        if existing:
            return existing[0]
        return self._queries.create_tag(name, color)

    def _on_avatar_chip_toggled(self, name: str, checked: bool):
        if self._asset is None:
            return
        asset_tags = self._queries.get_tags_for_asset(self._asset.id)
        assigned = {t[1]: t[0] for t in asset_tags}

        if checked:
            if name in assigned:
                return
            tag_id = self._find_or_create_tag(name, "#8b5cf6")
            if tag_id:
                self.tag_added.emit(self._asset.id, tag_id)
        else:
            if name in assigned:
                self.tag_removed.emit(self._asset.id, assigned[name])

    def _on_genre_chip_toggled(self, new_genre: str):
        if self._asset is None:
            return
        asset_tags = self._queries.get_tags_for_asset(self._asset.id)

        for tag_id, name, _ in asset_tags:
            if name in self.GENRE_SET:
                self.tag_removed.emit(self._asset.id, tag_id)

        tag_id = self._find_or_create_tag(new_genre)
        if tag_id:
            self.tag_added.emit(self._asset.id, tag_id)

    def _on_notes_changed(self):
        if self._asset is None:
            return
        self.notes_changed.emit(self._asset.id, self._notes_edit.text())
