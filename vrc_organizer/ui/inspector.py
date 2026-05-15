from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QEvent, QTimer
from PySide6.QtGui import QFont, QPalette, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QFormLayout, QTreeWidget, QTreeWidgetItem, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QFrame, QMenu, QApplication,
    QDialog, QMessageBox,
)

from vrc_organizer.tag_data import ALL_AVATAR_NAMES, TOP_AVATARS, GENRE_NAMES
from vrc_organizer.ui.chip_button import ChipToggleButton
from vrc_organizer.ui.flow_layout import FlowLayout
from vrc_organizer.romaji import has_japanese, to_romaji


def _romaji_enabled() -> bool:
    from PySide6.QtCore import QSettings
    return bool(QSettings().value("show_romaji", True, type=bool))

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


# Single-character glyphs to mark filetypes in the contents tree. Picked from
# the basic Unicode set so they render consistently on Windows 10/11.
_TYPE_ICON: dict[str, str] = {
    "image": "🖼",
    "video": "🎥",
    "audio": "🔊",
    "model": "🔷",
    "mesh": "🔷",
    "prefab": "🧩",
    "material": "🎨",
    "shader": "🎨",
    "animation": "🎞",
    "script": "⚙",
    "text": "📄",
    "readme": "📄",
    "archive": "📦",
    "unitypackage": "📦",
}


def _entry_icon(name: str, etype: str) -> str:
    """Return a 1-char icon for an entry based on its scanner type or suffix."""
    if etype in _TYPE_ICON:
        return _TYPE_ICON[etype]
    suffix = Path(name).suffix.lower().lstrip(".")
    if suffix in ("png", "jpg", "jpeg", "webp", "psd", "tga", "bmp"):
        return _TYPE_ICON["image"]
    if suffix in ("fbx", "blend", "obj", "gltf", "glb"):
        return _TYPE_ICON["model"]
    if suffix in ("wav", "mp3", "ogg"):
        return _TYPE_ICON["audio"]
    if suffix in ("cs", "py", "js"):
        return _TYPE_ICON["script"]
    if suffix in ("zip", "rar", "7z", "unitypackage"):
        return _TYPE_ICON["archive"]
    if suffix in ("mat",):
        return _TYPE_ICON["material"]
    if suffix in ("prefab",):
        return _TYPE_ICON["prefab"]
    return "•"


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
        self.setFixedHeight(26)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(10, 2, 10, 2)
        self._layout.setSpacing(0)

        self._label = QLabel(name)
        self._label.setStyleSheet("color: white; font-size: 11px; background: transparent; font-weight: 500;")
        self._layout.addWidget(self._label)

        self._refresh_style()

    def _refresh_style(self):
        self.setStyleSheet(
            f"TagChip {{ background: {self._color}; border-radius: 12px; }}"
            f"TagChip:hover {{ background: {self._color}; opacity: 0.9; }}"
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
        "AddTagChip { background: #f1f5f9; border: 1px dashed #cbd5e1; border-radius: 12px; }"
        "AddTagChip:hover { background: #e2e8f0; border-color: #94a3b8; }"
    )
    DARK_STYLE = (
        "AddTagChip { background: #1e293b; border: 1px dashed #475569; border-radius: 12px; }"
        "AddTagChip:hover { background: #334155; border-color: #64748b; }"
    )

    def __init__(self, placeholder: str = "+"):
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(26)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(10, 2, 10, 2)
        self._layout.setSpacing(0)

        self._label = QLabel(placeholder)
        self._label.setStyleSheet(
            "color: #64748b; font-size: 12px; background: transparent; font-weight: 600;"
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
    import_to_unity = Signal(int)        # asset_id

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
        layout.setSpacing(10)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header row: small thumbnail + filename (wraps, no truncation).
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(56, 56)
        self._thumb_label.setAlignment(Qt.AlignCenter)
        self._thumb_label.setStyleSheet(
            "QLabel { background: palette(alternate-base); border-radius: 3px; }"
        )
        header_row.addWidget(self._thumb_label, 0, Qt.AlignTop)
        # Title column: filename + an optional romaji "furigana" line for
        # Japanese asset titles. The romaji label hides itself when there's
        # nothing to transliterate.
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)
        self._filename_label = QLabel("Select an asset")
        self._filename_label.setWordWrap(True)
        self._filename_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._filename_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        self._filename_label.setFont(font)
        title_col.addWidget(self._filename_label)
        self._romaji_label = QLabel()
        self._romaji_label.setWordWrap(True)
        self._romaji_label.setStyleSheet(
            "color: palette(mid); font-size: 10px;"
        )
        self._romaji_label.setVisible(False)
        title_col.addWidget(self._romaji_label)
        header_row.addLayout(title_col, 1)
        layout.addLayout(header_row)

        # Contents tree — promoted to the top of the inspector.
        contents_group = QGroupBox("Contents")
        contents_layout = QVBoxLayout(contents_group)
        contents_layout.setContentsMargins(8, 8, 8, 8)

        # Type-filter chips: one per distinct entry type, click to toggle.
        # No leading "Show:" label — the chips speak for themselves.
        self._type_filter_flow = FlowLayout(spacing=4)
        type_filter_container = QWidget()
        type_filter_container.setLayout(self._type_filter_flow)
        contents_layout.addWidget(type_filter_container)
        self._type_filter_chips: dict[str, ChipToggleButton] = {}
        self._type_filter_state: dict[str, bool] = {}

        self._contents_tree = QTreeWidget()
        self._contents_tree.setHeaderLabels(["Name", "Size"])
        self._contents_tree.setRootIsDecorated(True)
        self._contents_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._contents_tree.setTextElideMode(Qt.ElideNone)
        self._contents_tree.setIndentation(14)
        self._contents_tree.setUniformRowHeights(True)
        # Thin tree connector lines, narrow indent — old-school hierarchical
        # viewer feel. The default branch art is heavy; we keep showSubControls
        # off and rely on the tree's own connectors via stylesheet.
        self._contents_tree.setStyleSheet(
            "QTreeView { show-decoration-selected: 1; }"
            "QTreeView::branch { background: transparent; }"
            "QTreeView::item { padding-top: 1px; padding-bottom: 1px; }"
        )
        header = self._contents_tree.header()
        from PySide6.QtWidgets import QHeaderView
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)
        self._contents_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._contents_tree.customContextMenuRequested.connect(self._on_contents_context_menu)
        self._contents_tree.itemDoubleClicked.connect(self._on_contents_double_clicked)
        contents_layout.addWidget(self._contents_tree)
        layout.addWidget(contents_group, 1)

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
        self._avatar_search.setPlaceholderText("Search avatars or press Enter to add a new one")
        self._avatar_search.setFixedHeight(24)
        self._avatar_search.textChanged.connect(self._on_avatar_search)
        self._avatar_search.returnPressed.connect(self._on_avatar_search_submit)
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

        # Tags (assigned + suggested unassigned — click suggestion to add).
        # A search field lets the user filter or type a new tag name; pressing
        # Enter on a name that doesn't match anything creates that tag.
        tags_group = QGroupBox("Tags")
        tags_layout = QVBoxLayout(tags_group)
        tags_layout.setContentsMargins(8, 8, 8, 8)
        tags_layout.setSpacing(6)

        self._tags_search = QLineEdit()
        self._tags_search.setPlaceholderText("Search tags or press Enter to add a new one")
        self._tags_search.setFixedHeight(24)
        self._tags_search.textChanged.connect(self._on_tags_search)
        self._tags_search.returnPressed.connect(self._on_tags_search_submit)
        tags_layout.addWidget(self._tags_search)

        self._tags_container = QWidget()
        self._tags_flow = FlowLayout(spacing=4)
        self._tags_container.setLayout(self._tags_flow)
        # Plain widget (no scrollarea) — the inspector itself scrolls, and a
        # nested QScrollArea + FlowLayout was collapsing the chip container
        # to one row, hiding everything past the first wrap.
        tags_layout.addWidget(self._tags_container)
        layout.addWidget(tags_group)

        # Open With box removed — double-clicking a content entry already
        # opens it in the OS handler. Tools-layout stays as a dummy so any
        # straggling _refresh_tools() call doesn't blow up.
        self._tools_layout = QVBoxLayout()

        # Unity import: a single square button. Stub for now — wired through
        # the queries layer; the real Editor plugin is a future-phase build.
        unity_row = QHBoxLayout()
        unity_row.setContentsMargins(0, 4, 0, 0)
        self._unity_btn = QPushButton("Import to Unity")
        self._unity_btn.setToolTip(
            "Send this .unitypackage to the Unity Editor.\n"
            "(Plugin in development — currently launches Unity with the "
            "package argument.)"
        )
        self._unity_btn.setFixedSize(120, 28)
        self._unity_btn.clicked.connect(self._on_import_to_unity)
        unity_row.addWidget(self._unity_btn)
        unity_row.addStretch(1)
        layout.addLayout(unity_row)

        layout.addStretch()

        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def show_empty(self):
        self._asset = None
        self._filename_label.setText("Select an asset")
        self._romaji_label.setVisible(False)
        self._romaji_label.clear()
        self._thumb_label.clear()
        self._type_label.setText("-")
        self._size_label.setText("-")
        self._path_label.setText("-")
        self._modified_label.setText("-")
        self._added_label.setText("-")
        self._notes_edit.blockSignals(True)
        self._notes_edit.clear()
        self._notes_edit.blockSignals(False)
        self._contents_tree.clear()
        self._clear_type_filter()
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
        # Preserve cached "+" chips across an empty-state pass so quick
        # reselection of an asset doesn't destroy any in-progress typing.
        self._clear_flow(self._tags_flow, preserve=AddTagChip)
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
        if _romaji_enabled() and has_japanese(asset.filename):
            self._romaji_label.setText(to_romaji(asset.filename))
            self._romaji_label.setVisible(True)
        else:
            self._romaji_label.setVisible(False)
            self._romaji_label.clear()

        # Small header thumbnail (56x56). Falls back to a "?" placeholder.
        if asset.thumbnail:
            pix = QPixmap(str(asset.thumbnail))
            if not pix.isNull():
                self._thumb_label.setPixmap(
                    pix.scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            else:
                self._thumb_label.setText("?")
        else:
            self._thumb_label.setText("?")

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
            self._contents_tree.expandAll()
            self._contents_tree.setVisible(True)
        else:
            self._contents_tree.setVisible(False)

        # Notes
        self._notes_edit.setText(asset.notes)
        self._notes_edit.blockSignals(False)

        # Tools
        self._refresh_tools()

    def _build_contents_tree(self, scan_results: list[tuple[str, str, int]]):
        """Build a hierarchical tree from flat entry paths.

        Two columns only — Name (stretches, full text, hover shows the full
        path) and Size (auto-fits, right-aligned). Filetype is conveyed by a
        leading icon glyph; type filtering happens via the chip row above.
        """
        nodes: dict[str, QTreeWidgetItem] = {}
        distinct_types: set[str] = set()

        for name, etype, size in sorted(scan_results, key=lambda x: x[0].lower()):
            parts = name.replace("\\", "/").split("/")
            parent_item = None

            for i in range(len(parts) - 1):
                folder_path = "/".join(parts[:i + 1])
                if folder_path not in nodes:
                    folder_name = parts[i]
                    node = QTreeWidgetItem([f"📁  {folder_name}", ""])
                    node.setData(0, Qt.UserRole + 1, "folder")
                    node.setToolTip(0, folder_path)
                    if parent_item:
                        parent_item.addChild(node)
                    else:
                        self._contents_tree.addTopLevelItem(node)
                    nodes[folder_path] = node
                parent_item = nodes[folder_path]

            filename = parts[-1]
            size_str = _format_size(size)
            distinct_types.add(etype)
            icon = _entry_icon(filename, etype)

            child = QTreeWidgetItem([f"{icon}  {filename}", size_str])
            child.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            child.setData(0, Qt.UserRole, name)
            child.setData(0, Qt.UserRole + 2, etype)  # for type-filter hiding
            # Tooltip carries the full path + detailed type so a hover gives
            # the reader everything that's no longer in the visible row.
            child.setToolTip(0, f"{name}\nType: {_detailed_type(name, etype)}")
            if parent_item:
                parent_item.addChild(child)
            else:
                self._contents_tree.addTopLevelItem(child)

        self._populate_type_filter(sorted(distinct_types))

    def _clear_type_filter(self):
        while self._type_filter_flow.count():
            item = self._type_filter_flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._type_filter_chips.clear()
        self._type_filter_state.clear()

    def _populate_type_filter(self, types: list[str]):
        self._clear_type_filter()
        if len(types) <= 1:
            return
        for t in types:
            chip = ChipToggleButton(t)
            chip.set_active(True)
            self._type_filter_state[t] = True
            chip.toggled.connect(lambda checked, name=t: self._on_type_filter_toggled(name, checked))
            self._type_filter_flow.addWidget(chip)
            self._type_filter_chips[t] = chip

    def _on_type_filter_toggled(self, etype: str, active: bool):
        self._type_filter_state[etype] = active
        self._apply_type_filter()

    def _apply_type_filter(self):
        # Walk the tree. A folder is visible if any descendant file is visible.
        def visit(item: QTreeWidgetItem) -> bool:
            etype = item.data(0, Qt.UserRole + 2)
            if etype is not None:
                visible = self._type_filter_state.get(etype, True)
                item.setHidden(not visible)
                return visible
            # Folder
            any_child_visible = False
            for i in range(item.childCount()):
                if visit(item.child(i)):
                    any_child_visible = True
            item.setHidden(not any_child_visible)
            return any_child_visible

        root = self._contents_tree.invisibleRootItem()
        for i in range(root.childCount()):
            visit(root.child(i))

    def _clear_flow(self, flow: FlowLayout, preserve: type | None = None):
        """Remove all widgets from `flow`. Instances of `preserve` are taken
        out of the layout but not deleted, so callers can re-insert them.
        This protects widgets that may hold user input state (e.g. an
        AddTagChip mid-typing) from being destroyed on every refresh."""
        while flow.count():
            item = flow.takeAt(0)
            if not item:
                continue
            w = item.widget()
            if w is None:
                continue
            if preserve is not None and isinstance(w, preserve):
                continue
            w.hide()
            w.deleteLater()

    def _refresh_tags(self):
        # Clear all chips — search input is a sibling widget, not in the flow.
        self._clear_flow(self._tags_flow)

        if self._asset is None:
            return

        asset_tags = self._queries.get_tags_for_asset(self._asset.id)
        avatar_names = set(TOP_AVATARS)

        current_genre = None
        assigned_avatars: set[str] = set()
        assigned_extra: list[tuple[int, str, str]] = []
        for tag_id, name, color in asset_tags:
            if name in GENRE_NAMES:
                current_genre = name
                continue
            if name in avatar_names:
                assigned_avatars.add(name)
                continue
            assigned_extra.append((tag_id, name, color))

        query = self._tags_search.text().lower().strip() if hasattr(self, "_tags_search") else ""

        # Assigned tag chips (removable, renameable). Hidden when the search
        # filter excludes them so the user can dial down to suggestions only.
        for tag_id, name, color in assigned_extra:
            if query and query not in name.lower():
                continue
            chip = TagChip(tag_id, name, color, removable=True, renameable=True,
                           queries=self._queries)
            chip.remove_from_asset.connect(
                lambda tid=tag_id: self.tag_removed.emit(self._asset.id, tid)
            )
            chip.rename_requested.connect(self._on_tag_renamed)
            chip.delete_requested.connect(self._on_tag_deleted)
            self._tags_flow.addWidget(chip)

        # Suggested unassigned tags — click to add. Pulls from the auto-tagger,
        # co-occurrence, and (when the user is filtering) any DB tag whose
        # name matches the search query.
        assigned_ids = {t[0] for t in asset_tags}
        excluded_names = self.GENRE_SET | avatar_names
        suggestions = self._collect_tag_suggestions(
            assigned_ids, excluded_names, query=query, limit=20
        )
        for tag_id, name, color in suggestions:
            chip = self._make_suggested_chip(tag_id, name, color)
            self._tags_flow.addWidget(chip)

        # Newly created/reparented chips can land with isVisible()==False if
        # the parent hasn't been drawn yet. FlowLayout's _do_layout skips
        # invisible items, which collapses the whole row to height 0 and
        # makes the chips silently disappear. Force-show after add.
        for i in range(self._tags_flow.count()):
            item = self._tags_flow.itemAt(i)
            w = item.widget() if item else None
            if w is not None:
                w.show()

        self._tags_flow.invalidate()
        self._tags_flow.activate()
        # Tell the container exactly how tall the flow needs to be at the
        # current width. Without this the parent layout assumes a single row.
        QTimer.singleShot(0, self._update_tags_container_height)

        # Update genre chips
        for name, chip in self._genre_chips.items():
            chip.set_active(name == current_genre)

        # Update avatar chips — selected first, "+" chip, then unselected.
        # This MUST stay in _refresh_tags where assigned_avatars is in scope;
        # an earlier edit accidentally moved the call into the deferred
        # height-update slot and produced a NameError.
        self._reorder_avatar_flow(assigned_avatars)

    def _update_tags_container_height(self):
        width = self._tags_container.width()
        if width <= 0:
            return
        needed = self._tags_flow.heightForWidth(width)
        # Add a tiny pad so descenders / chip borders aren't clipped on the
        # last row.
        self._tags_container.setMinimumHeight(max(32, needed + 4))

    def _on_tags_search(self, _text: str):
        # Re-render chips with the new filter applied.
        if self._asset is not None:
            self._refresh_tags()

    def _on_tags_search_submit(self):
        """Press Enter in the tag search — create the tag if no match."""
        if self._asset is None:
            return
        text = self._tags_search.text().strip()
        if not text:
            return
        existing = self._queries.get_tag_by_name(text)
        tag_id = existing[0] if existing else self._queries.create_tag(text)
        if tag_id:
            self.tag_added.emit(self._asset.id, tag_id)
        self._tags_search.clear()

    def _collect_tag_suggestions(
        self, assigned_ids: set[int], excluded_names: set[str],
        query: str = "", limit: int = 12,
    ) -> list[tuple[int, str, str]]:
        if self._asset is None:
            return []
        from vrc_organizer.auto_tagger import suggest_tags

        all_tags = self._queries.get_all_tags()
        by_id = {t[0]: (t[0], t[1], t[2]) for t in all_tags}  # tag_id → (id, name, color)

        seen: set[int] = set(assigned_ids)
        out: list[tuple[int, str, str]] = []

        # When the user is searching, surface every matching DB tag first.
        if query:
            for tid, name, color, _count in all_tags:
                if tid in seen or name in excluded_names:
                    continue
                if query in name.lower():
                    seen.add(tid)
                    out.append((tid, name, color))
                    if len(out) >= limit:
                        return out

        # Auto-tagger from filename
        for tid in suggest_tags(self._queries, self._asset.filename, None):
            info = by_id.get(tid)
            if info and tid not in seen and info[1] not in excluded_names:
                if query and query not in info[1].lower():
                    continue
                seen.add(tid)
                out.append(info)
                if len(out) >= limit:
                    return out

        # Co-occurrence from already-assigned tags
        for tid in list(assigned_ids):
            for rid, rname, _color in self._queries.get_related_tags(tid, limit=4):
                info = by_id.get(rid)
                if info and rid not in seen and rname not in excluded_names:
                    if query and query not in rname.lower():
                        continue
                    seen.add(rid)
                    out.append(info)
                    if len(out) >= limit:
                        return out
        return out

    def _make_suggested_chip(self, tag_id: int, name: str, color: str) -> QPushButton:
        btn = QPushButton(name)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(26)
        btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {color}; "
            f"border: 1px dashed {color}; border-radius: 12px; "
            f"padding: 0 10px; font-size: 11px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {color}; color: white; }}"
        )
        btn.clicked.connect(
            lambda checked=False, tid=tag_id: self.tag_added.emit(self._asset.id, tid)
        )
        return btn

    def _reorder_avatar_flow(self, assigned: set[str]):
        """Reorder avatar flow: selected chips first, then '+' chip, then unselected.

        The cached chips (`self._avatar_chips`, `self._avatar_search_chips`,
        `self._avatar_add_chip`) survive across refreshes — we only re-order
        them in the layout. The previous version aggressively deleted any
        widget not in the ChipToggleButton set, which always included the
        AddTagChip and forced a recreation on every refresh, destroying any
        mid-typing input the user had.
        """
        # Update active states for base and search chips
        for name, chip in self._avatar_chips.items():
            chip.set_active(name in assigned)
        for name, chip in self._avatar_search_chips.items():
            chip.set_active(name in assigned)

        # Take all items out of the flow without deleting any widget — the
        # cached chips remain parented to the container and ready to re-add.
        while self._avatar_flow.count():
            self._avatar_flow.takeAt(0)

        # Order: selected → '+' → unselected
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

    def _on_avatar_search_submit(self):
        """Press Enter in the avatar search — create the avatar tag if missing."""
        if self._asset is None:
            return
        text = self._avatar_search.text().strip()
        if not text:
            return
        existing = self._queries.get_tag_by_name(text)
        tag_id = existing[0] if existing else self._queries.create_tag(text, "#8b5cf6")
        if tag_id:
            self.tag_added.emit(self._asset.id, tag_id)
        self._avatar_search.clear()

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
        # Tools panel removed — content double-click is the canonical
        # "open in viewer" path now. Keep this method as a no-op so older
        # callers don't NameError.
        pass

    def _on_import_to_unity(self):
        if self._asset is None:
            return
        # Only meaningful for unity packages; for other types just nudge.
        if self._asset.filetype != "unitypackage":
            QMessageBox.information(
                self, "Not a Unity package",
                "This action only applies to .unitypackage files."
            )
            return
        self.import_to_unity.emit(self._asset.id)

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
                f"padding: 4px 10px; border-radius: 5px; font-size: 11px; border: none; }}"
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
        """Single DB call enforces mutual exclusivity instead of a remove+add
        loop where a UI refresh in the middle would briefly drop the genre."""
        if self._asset is None:
            return
        new_id = self._queries.set_genre(self._asset.id, new_genre)
        # Use the existing add-tag signal so MainWindow runs its refresh
        # cascade (inspector + sidebar + grid model).
        self.tag_added.emit(self._asset.id, new_id)

    def _on_notes_changed(self):
        if self._asset is None:
            return
        self.notes_changed.emit(self._asset.id, self._notes_edit.text())
