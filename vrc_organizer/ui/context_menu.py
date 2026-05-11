from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from vrc_organizer.database.queries import Queries


class AssetContextMenu(QMenu):
    open_in = Signal(str, Path, int)    # tool_name, filepath, asset_id
    add_tag = Signal(int)                # asset_id
    delete_asset = Signal(int)           # asset_id
    rescan = Signal(int)                 # asset_id

    def __init__(self, asset_id: int, asset_filename: str,
                 asset_filepath: Path, asset_filetype: str,
                 queries: Queries, tool_registry=None, parent=None):
        super().__init__(parent)
        self._asset_id = asset_id
        self._filepath = asset_filepath
        self._filetype = asset_filetype
        self._queries = queries
        self._tool_registry = tool_registry
        self._build()

    def _build(self):
        self.addAction(self._action("Reveal in File Explorer", self._on_reveal_in_explorer))
        self.addSeparator()
        self.addAction(self._action("Open Containing Folder", self._on_open_folder))

        # Open With submenu
        tools = self._tools_for_type()
        if tools:
            tools_menu = self.addMenu("Open With")
            for tool_name in tools:
                tools_menu.addAction(
                    self._action(f"Open in {tool_name}",
                                lambda checked, tn=tool_name: self.open_in.emit(tn, self._filepath, self._asset_id))
                )

        self.addSeparator()

        # Tag submenu
        tags = self._queries.get_all_tags()
        if tags:
            tags_menu = self.addMenu("Add Tag")
            for tag_id, name, color, _ in tags:
                action = QAction(name)
                # Color dot icon would go here
                action.triggered.connect(
                    lambda checked, tid=tag_id: self.add_tag.emit(tid)
                )
                tags_menu.addAction(action)

        self.addSeparator()
        self.addAction(self._action("Re-scan", lambda: self.rescan.emit(self._asset_id)))
        self.addSeparator()
        self.addAction(self._action("Delete", lambda: self.delete_asset.emit(self._asset_id)))

    def _on_reveal_in_explorer(self):
        subprocess.run(['explorer', '/select,', str(self._filepath)])

    def _on_open_folder(self):
        import os
        path = str(self._filepath)
        if not os.path.isdir(path):
            path = str(self._filepath.parent)
        os.startfile(path)

    def _tools_for_type(self) -> list[str]:
        if self._tool_registry:
            return [t.name for t in self._tool_registry.for_filetype(self._filetype)]
        from vrc_organizer.tools.registry import DEFAULT_TOOL_MAP
        tools = DEFAULT_TOOL_MAP.get(self._filetype, [])
        # Don't show "Default Viewer" in context menu (double-click already opens)
        return [t for t in tools if t != "Default Viewer"]

    @staticmethod
    def _action(text: str, slot) -> QAction:
        action = QAction(text)
        action.triggered.connect(slot)
        return action
