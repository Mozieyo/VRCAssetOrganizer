from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QThreadPool
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QDockWidget, QLabel,
    QStatusBar, QMenuBar, QMenu, QToolBar, QLineEdit, QSlider,
    QVBoxLayout, QProgressBar, QFileDialog, QCheckBox, QMessageBox,
    QPushButton, QDialog, QDialogButtonBox, QScrollArea,
    QListWidget, QListWidgetItem, QGroupBox, QRadioButton,
)

from vrc_organizer.app import VrcApp, _save_db_path
from vrc_organizer.database.queries import Queries
from vrc_organizer.ui.theme import ThemeManager
from vrc_organizer.ui.thumbnail_grid import (
    AssetListModel, ThumbnailDelegate, AssetListView, THUMB_SIZE,
)
from vrc_organizer.ui.inspector import InspectorPanel
from vrc_organizer.ui.tag_dialog import TagDialog
from vrc_organizer.ui.context_menu import AssetContextMenu
from vrc_organizer.ui.drop_overlay import DropOverlay
from vrc_organizer.ui.sidebar import Sidebar
from vrc_organizer.ui.settings_dialog import SettingsDialog
from vrc_organizer.ui.cover_trainer import CoverTrainerDialog
from vrc_organizer.ui.tag_reviewer import TagReviewerDialog
from vrc_organizer.tools.registry import ToolRegistry
from vrc_organizer.tools.launcher import ToolLauncher
from vrc_organizer.unity_windows import find_unity_editors, find_unity_project_path
from vrc_organizer.workers.importer import ImportWorker
from vrc_organizer.workers.thumb_worker import ThumbWorker
from vrc_organizer.scanner.orchestrator import scan_file


class MainWindow(QMainWindow):
    def __init__(self, app: VrcApp, queries: Queries):
        super().__init__()
        self._app = app
        self._queries = queries
        self._theme = ThemeManager()

        self._tool_registry = ToolRegistry()
        self._tool_launcher = ToolLauncher()
        self._tool_launcher.tool_launched.connect(self._on_tool_launched)
        self._tool_launcher.tool_error.connect(self._on_tool_error)

        self.setWindowTitle("VRC Asset Organizer")
        self.resize(1280, 800)
        self.setAcceptDrops(True)

        self._setup_menu_bar()
        self._setup_toolbar()
        self._setup_central()
        self._setup_inspector_dock()
        self._setup_status_bar()

        self._seed_default_tags()
        self._queries.purge_expired_trash(days=30)
        self._sidebar.refresh()
        self._model.refresh()

    # ── Menu Bar ────────────────────────────────────────────

    def _seed_default_tags(self):
        existing = self._queries.get_all_tags()
        if existing:
            return
        tags = [
            ("Hair", "#ef4444"),
            ("Head", "#f59e0b"),
            ("Body", "#3b82f6"),
            ("Hands", "#a855f7"),
            ("Feet", "#22c55e"),
            ("Ears", "#ec4899"),
            ("Tail", "#f97316"),
            ("Accs", "#6366f1"),
        ]
        for name, color in tags:
            self._queries.create_tag(name, color)

    def _setup_menu_bar(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        file_menu.addAction("Import Files...", self._on_import_files)
        file_menu.addAction("Import Folder...", self._on_import_folder)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close)

        edit_menu = mb.addMenu("&Edit")
        select_all_action = edit_menu.addAction("Select All")
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.triggered.connect(self._on_select_all)

        view_menu = mb.addMenu("&View")
        self._inspector_toggle_action = view_menu.addAction("Inspector")
        self._inspector_toggle_action.setCheckable(True)
        self._inspector_toggle_action.setChecked(True)
        self._inspector_toggle_action.triggered.connect(self._on_toggle_inspector)

        prefs_menu = mb.addMenu("&Preferences")
        self._dark_action = prefs_menu.addAction("Dark Mode")
        self._dark_action.setCheckable(True)
        self._dark_action.triggered.connect(self._on_toggle_theme)
        prefs_menu.addSeparator()
        prefs_menu.addAction("Unity Editor Path...", self._on_set_unity_path)
        prefs_menu.addAction("Assets Storage Path...", self._on_set_storage_path)
        prefs_menu.addSeparator()
        prefs_menu.addAction("Settings...", self._on_open_settings)

        tools_menu = mb.addMenu("&Tools")
        tools_menu.addAction("Batch Generate Thumbnails...", self._on_batch_thumbnails)
        tools_menu.addAction("Review Auto-Tags...", self._on_review_tags)
        tools_menu.addAction("Train Cover Detection...", self._on_train_covers)
        tools_menu.addAction("Manage Tags...", self._on_manage_tags)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction("About")

    # ── Toolbar ─────────────────────────────────────────────

    def _setup_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))

        self._search_bar = QLineEdit()
        self._search_bar.setPlaceholderText("Search assets...")
        self._search_bar.setMaximumWidth(300)
        self._search_bar.textChanged.connect(self._on_search_changed)
        tb.addWidget(self._search_bar)

        self._global_search_cb = QCheckBox("Search All")
        self._global_search_cb.setToolTip("Search across all assets, ignoring type and tag filters")
        self._global_search_cb.toggled.connect(lambda: self._apply_filters())
        tb.addWidget(self._global_search_cb)

        tb.addSeparator()
        grid_label = QLabel(" Grid: ")
        grid_label.setToolTip("Slide right for larger cards, left for smaller")
        tb.addWidget(grid_label)

        self._grid_slider = QSlider(Qt.Horizontal)
        self._grid_slider.setRange(1, 10)
        self._grid_slider.setValue(5)
        self._grid_slider.setFixedWidth(120)
        self._grid_slider.setTickInterval(1)
        self._grid_slider.valueChanged.connect(self._on_grid_size_changed)
        tb.addWidget(self._grid_slider)

        self.addToolBar(tb)

    # ── Central Widget ──────────────────────────────────────

    def _setup_central(self):
        splitter = QSplitter(Qt.Horizontal)

        self._sidebar = Sidebar(self._queries)
        self._sidebar.tag_filter_changed.connect(self._on_tag_filter)
        self._sidebar.manage_tags.connect(self._on_manage_tags)
        sidebar = self._sidebar

        grid_panel = QWidget()
        grid_layout = QVBoxLayout(grid_panel)
        grid_layout.setContentsMargins(0, 0, 0, 0)

        self._model = AssetListModel(self._queries)
        self._delegate = ThumbnailDelegate()

        self._grid = AssetListView()
        self._grid.setModel(self._model)
        self._grid.setItemDelegate(self._delegate)
        self._grid.files_dropped.connect(self._on_files_dropped)
        self._grid.delete_requested.connect(self._on_delete_selected)
        self._grid.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self._grid.customContextMenuRequested.connect(self._on_context_menu)
        self._model.modelReset.connect(self._grid.recenter)
        self._model.layoutChanged.connect(self._grid.recenter)

        grid_layout.addWidget(self._grid)

        splitter.addWidget(sidebar)
        splitter.addWidget(grid_panel)
        splitter.setSizes([220, 1060])

        self.setCentralWidget(splitter)

        self._drop_overlay = DropOverlay(self._grid.viewport())
        self._grid.drag_entered.connect(self._drop_overlay.show_overlay)
        self._grid.drag_left.connect(self._drop_overlay.hide_overlay)

    # ── Inspector Dock ──────────────────────────────────────

    def _setup_inspector_dock(self):
        self._inspector = InspectorPanel(self._queries, self._tool_registry)
        self._inspector.tag_added.connect(self._on_tag_add_request)
        self._inspector.tag_removed.connect(self._on_tag_removed)
        self._inspector.notes_changed.connect(self._on_notes_save)
        self._inspector.open_with.connect(self._on_open_with)

        self._inspector_dock = QDockWidget("Inspector", self)
        self._inspector_dock.setWidget(self._inspector)
        self._inspector_dock.setFeatures(
            QDockWidget.DockWidgetMovable |
            QDockWidget.DockWidgetFloatable |
            QDockWidget.DockWidgetClosable
        )
        self._inspector_dock.visibilityChanged.connect(
            lambda v: self._inspector_toggle_action.setChecked(v)
        )
        self.addDockWidget(Qt.RightDockWidgetArea, self._inspector_dock)

    # ── Status Bar ──────────────────────────────────────────

    def _setup_status_bar(self):
        self._status_bar = QStatusBar()
        self._status_label = QLabel("Ready")
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumWidth(200)
        self._progress_bar.setVisible(False)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedHeight(22)
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self._on_cancel_import)
        self._status_bar.addWidget(self._status_label)
        self._status_bar.addPermanentWidget(self._progress_bar)
        self._status_bar.addPermanentWidget(self._cancel_btn)
        self.setStatusBar(self._status_bar)

    # ── Slots: Import ───────────────────────────────────────

    def _on_import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import Files", "",
            "All Assets (*.zip *.unitypackage *.blend *.fbx *.obj *.png *.jpg *.jpeg *.webp *.psd *.mat *.prefab);;All Files (*)"
        )
        if paths:
            self._on_files_dropped(paths)

    def _on_import_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Import Folder")
        if folder:
            paths = [str(p) for p in Path(folder).rglob("*") if p.is_file()]
            if paths:
                self._on_files_dropped(paths)

    def _on_files_dropped(self, paths: list[str]):
        # Cancel any in-progress import before starting a new one
        if hasattr(self, '_current_worker') and self._current_worker is not None:
            self._current_worker.cancel()
            self._current_worker = None

        self._status_label.setText(f"Importing {len(paths)} file(s)...")
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._cancel_btn.setVisible(True)

        try:
            library_str = self._queries.get_setting("library_dir", "")
            library_dir = Path(library_str) if library_str else None
            self._current_worker = ImportWorker(paths, self._queries, self._app.thumb_cache_dir, library_dir)
            self._current_worker.signals.progress.connect(self._on_import_progress)
            self._current_worker.signals.status.connect(self._on_import_status)
            self._current_worker.signals.file_done.connect(self._on_import_file_done)
            self._current_worker.signals.file_failed.connect(self._on_import_file_failed)
            self._current_worker.signals.finished.connect(self._on_import_finished)
            self._current_worker.signals.error.connect(self._on_import_error)
            QThreadPool.globalInstance().start(self._current_worker)
        except Exception as e:
            self._progress_bar.setVisible(False)
            self._cancel_btn.setVisible(False)
            self._status_label.setText(f"Import error: {e}")

    def _on_import_progress(self, pct: int):
        self._progress_bar.setValue(pct)

    def _on_import_status(self, msg: str):
        self._status_label.setText(msg)

    def _on_import_file_done(self, filename: str):
        pass  # status label already shows progress from _on_import_status

    def _on_import_file_failed(self, filename: str, error_msg: str):
        self._status_label.setText(f"Skipped {filename}: {error_msg}")

    def _on_cancel_import(self):
        if hasattr(self, '_current_worker') and self._current_worker:
            self._current_worker.cancel()
            self._status_label.setText("Cancelling...")

    def _on_import_finished(self, asset_ids: list[int]):
        self._progress_bar.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._current_worker = None
        self._status_label.setText(
            f"Imported {len(asset_ids)} asset(s) — {self._model.rowCount()} total"
        )
        self._model.refresh()
        self._sidebar.refresh()
        self._grid.select_asset_ids(asset_ids)
        self._start_background_thumbs()
        self._check_multi_unitypackage(asset_ids)

    def _start_background_thumbs(self):
        # Don't stack thumb workers — if one is already running, let it finish
        if hasattr(self, '_thumb_worker') and self._thumb_worker is not None:
            return
        pending = self._queries.get_pending_thumbs(limit=1)
        if not pending:
            return
        self._thumb_worker = ThumbWorker(self._queries, self._app.thumb_cache_dir, limit=1)
        self._thumb_worker.signals.status.connect(self._on_import_status)
        self._thumb_worker.signals.finished.connect(self._on_thumbs_finished)
        QThreadPool.globalInstance().start(self._thumb_worker)

    def _on_thumbs_finished(self, found_ids: list[int]):
        self._thumb_worker = None
        if found_ids:
            self._model.refresh()

    def _check_multi_unitypackage(self, asset_ids: list[int]):
        """If any imported asset is a zip with multiple .unitypackage files,
        offer to extract them as separate assets."""
        import zipfile

        for aid in asset_ids:
            asset = self._queries.get_asset(aid)
            if not asset or asset.filetype != "booth_zip":
                continue

            # Count .unitypackage entries in scan results
            results = self._queries.get_scan_results(aid)
            up_entries = [(name, etype) for name, etype, _ in results
                          if name.lower().endswith(".unitypackage")]
            if len(up_entries) <= 1:
                continue

            # Show multi-select dialog
            dlg = QDialog(self)
            dlg.setWindowTitle("Multiple Unity Packages Found")
            dlg.setMinimumWidth(400)

            layout = QVBoxLayout(dlg)
            layout.addWidget(QLabel(
                f"<b>{asset.filename}</b> contains {len(up_entries)} .unitypackage files.<br>"
                "Select which ones to import as separate assets:"
            ))

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            check_layout = QVBoxLayout(container)
            cbs = {}
            for name, etype in sorted(up_entries):
                cb = QCheckBox(name.split("/")[-1])
                cb.setToolTip(name)
                cb.setChecked(True)
                check_layout.addWidget(cb)
                cbs[name] = cb
            check_layout.addStretch()
            scroll.setWidget(container)
            layout.addWidget(scroll)

            btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            layout.addWidget(btns)

            if dlg.exec() != QDialog.Accepted:
                continue

            # Extract and import selected .unitypackage files
            selected = [name for name, cb in cbs.items() if cb.isChecked()]
            if not selected:
                continue

            try:
                with zipfile.ZipFile(asset.filepath, "r") as zf:
                    for name in selected:
                        data = zf.read(name)
                        # Write to temp dir and import
                        import tempfile
                        tmp = Path(tempfile.gettempdir()) / "VrcAssetOrganizer" / "extracted"
                        tmp.mkdir(parents=True, exist_ok=True)
                        out = tmp / name.split("/")[-1]
                        out.write_bytes(data)
                        self._on_files_dropped([str(out)])
            except Exception as e:
                self._status_label.setText(f"Failed to extract .unitypackage: {e}")

    def _on_import_error(self, error_msg: str):
        self._progress_bar.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._current_worker = None
        self._status_label.setText(f"Import error: {error_msg}")

    # ── Slots: Selection ────────────────────────────────────

    def _on_selection_changed(self, selected, deselected):
        idxs = self._grid.selectionModel().selectedIndexes()
        count = len(idxs)
        total = self._model.rowCount()

        if count == 1:
            asset_id = idxs[0].data(Qt.UserRole)
            asset = self._queries.get_asset(asset_id)
            if asset:
                self._inspector.show_asset(asset)
        else:
            self._inspector.show_empty()

        self._status_label.setText(f"{total} assets • {count} selected")

    # ── Slots: Context Menu ─────────────────────────────────

    def _on_context_menu(self, pos):
        index = self._grid.indexAt(pos)
        if not index.isValid():
            return

        asset_id = index.data(Qt.UserRole)
        asset = self._queries.get_asset(asset_id)
        if asset is None:
            return

        menu = AssetContextMenu(
            asset.id, asset.filename, asset.filepath,
            asset.filetype, self._queries, self._tool_registry, self
        )
        menu.open_in.connect(self._on_open_with)
        menu.add_tag.connect(lambda tid: self._do_add_tag(asset_id, tid))
        menu.delete_asset.connect(self._on_delete_asset)
        menu.rescan.connect(self._on_rescan_asset)
        menu.exec(self._grid.viewport().mapToGlobal(pos))

    # ── Slots: Tags ─────────────────────────────────────────

    def _on_tag_add_request(self, asset_id: int, tag_id: int):
        self._do_add_tag(asset_id, tag_id)

    def _on_tag_removed(self, asset_id: int, tag_id: int):
        self._queries.remove_tag_from_asset(asset_id, tag_id)
        self._refresh_inspector_for(asset_id)
        self._sidebar.refresh()
        self._model.refresh()

    def _do_add_tag(self, asset_id: int, tag_id: int):
        self._queries.add_tag_to_asset(asset_id, tag_id)
        self._refresh_inspector_for(asset_id)
        self._sidebar.refresh()
        self._model.refresh()

    def _on_manage_tags(self):
        dlg = TagDialog(self._queries, self)
        dlg.tags_changed.connect(self._refresh_inspector)
        dlg.tags_changed.connect(lambda: self._sidebar.refresh())
        dlg.tags_changed.connect(lambda: self._model.refresh())
        dlg.exec()

    # ── Slots: Notes ────────────────────────────────────────

    def _on_notes_save(self, asset_id: int, notes: str):
        self._queries.update_notes(asset_id, notes)

    # ── Slots: Tools ────────────────────────────────────────

    def _on_open_with(self, tool_name: str, filepath: Path, asset_id: int = 0):
        tool = self._tool_registry.get(tool_name)
        if tool is None or not tool.executable:
            self._status_label.setText(
                f"No executable set for {tool_name}. Set it in Preferences."
            )
            return

        asset_tags: list[str] = []
        asset_filetype = ""
        if asset_id:
            asset = self._queries.get_asset(asset_id)
            if asset:
                asset_filetype = asset.filetype
                asset_tags = [name for _, name, _ in self._queries.get_tags_for_asset(asset_id)]

        # Unity Editor: check for running instances to avoid launching fresh
        if tool_name == "Unity Editor":
            editors = find_unity_editors()
            if len(editors) == 0:
                # No Unity running — launch fresh with -executeMethod
                self._status_label.setText(f"Launching Unity for {filepath.name}...")
                unity_project = self._queries.get_setting("unity_project", "")
                self._tool_launcher.launch_unity_fresh(
                    filepath, tool.executable, unity_project,
                    asset_tags, asset_filetype,
                )
            elif len(editors) == 1:
                # One instance — use it directly
                hwnd, title = editors[0]
                proj = find_unity_project_path(hwnd) or title
                self._tool_launcher.open_in_running_unity(hwnd, filepath, asset_tags, asset_filetype)
                self._status_label.setText(
                    f"Sent to {proj}. Switch to Unity and run VRC Thumbnail > Process Single."
                )
            else:
                # Multiple instances — show picker
                self._show_unity_picker(editors, filepath, asset_tags, asset_filetype)
            return

        self._status_label.setText(f"Launching {tool_name} for {filepath.name}...")
        unity_project = self._queries.get_setting("unity_project", "")
        self._tool_launcher.launch(
            tool, filepath, unity_project=unity_project,
            asset_tags=asset_tags, asset_filetype=asset_filetype,
        )

    def _show_unity_picker(self, editors: list[tuple[int, str]], filepath: Path,
                           asset_tags: list[str], asset_filetype: str):
        """Show a dialog to pick which running Unity Editor to target."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Select Unity Editor")
        dlg.setMinimumWidth(400)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(
            f"Multiple Unity Editors are running.<br>"
            f"Select which project to import <b>{filepath.name}</b> into:"
        ))

        lst = QListWidget()
        for hwnd, title in editors:
            proj = find_unity_project_path(hwnd) or title
            item = QListWidgetItem(proj)
            item.setData(Qt.UserRole, hwnd)
            lst.addItem(item)
        lst.setCurrentRow(0)
        layout.addWidget(lst)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() != QDialog.Accepted:
            return

        item = lst.currentItem()
        if item:
            hwnd = item.data(Qt.UserRole)
            self._tool_launcher.open_in_running_unity(hwnd, filepath, asset_tags, asset_filetype)
            self._status_label.setText(
                f"Sent to {item.text()}. Switch to Unity and run VRC Thumbnail > Process Single."
            )

    def _on_tool_launched(self, tool_name: str, filepath: str):
        self._status_label.setText(f"Launched {tool_name} — {Path(filepath).name}")

    def _on_tool_error(self, tool_name: str, error_msg: str):
        self._status_label.setText(f"Error: {tool_name} — {error_msg}")

    def _on_delete_asset(self, asset_id: int):
        asset = self._queries.get_asset(asset_id)
        if asset is None:
            return
        reply = QMessageBox.question(
            self, "Delete Asset",
            f"Move \"{asset.filename}\" to the recycle bin?\n\n"
            f"It will be permanently deleted after 30 days.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._trash_asset(asset)
        self._model.refresh()
        self._status_label.setText(f"Moved to recycle bin — {self._model.rowCount()} total")

    def _trash_asset(self, asset):
        import send2trash
        try:
            send2trash.send2trash(str(asset.filepath))
        except Exception:
            pass
        self._queries.delete_asset(asset.id)

    def _on_delete_selected(self):
        idxs = self._grid.selectionModel().selectedIndexes()
        if not idxs:
            return
        count = len(idxs)
        reply = QMessageBox.question(
            self, "Delete Assets",
            f"Move {count} asset(s) to the recycle bin?\n\n"
            f"They will be permanently deleted after 30 days.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for idx in idxs:
            asset_id = idx.data(Qt.UserRole)
            asset = self._queries.get_asset(asset_id)
            if asset:
                self._trash_asset(asset)
        self._model.refresh()
        self._status_label.setText(f"{count} asset(s) moved to recycle bin — {self._model.rowCount()} total")

    # ── Slots: Search / Grid ────────────────────────────────

    def _on_search_changed(self, text: str):
        self._search_terms = text.strip() if text.strip() else None
        self._apply_filters()

    def _on_tag_filter(self, or_tag_ids: list[int], and_tag_ids: list[int]):
        self._or_tag_filters = or_tag_ids if or_tag_ids else None
        self._and_tag_filters = and_tag_ids if and_tag_ids else None
        self._apply_filters()

    def _apply_filters(self):
        global_search = (
            hasattr(self, '_global_search_cb') and self._global_search_cb.isChecked()
        )
        self._model.set_filter(
            or_tag_ids=None if global_search else getattr(self, '_or_tag_filters', None),
            and_tag_ids=None if global_search else getattr(self, '_and_tag_filters', None),
            search=getattr(self, '_search_terms', None),
        )

    def _on_grid_size_changed(self, value: int):
        # Invert: slider 1 = 10 columns (smallest), slider 10 = 1 column (largest)
        columns = 11 - value
        grid_w = self._grid.viewport().width()
        spacing = self._grid.spacing()
        overhead = 44  # LABEL_HEIGHT + 2 * CARD_PADDING
        thumb_size = max(32, (grid_w + spacing) // columns - overhead - spacing)
        self._delegate.set_thumb_size(thumb_size)
        self._model.set_thumb_size(thumb_size)
        count = self._model.rowCount()
        if count > 0:
            self._model.dataChanged.emit(
                self._model.index(0), self._model.index(count - 1), [Qt.DecorationRole]
            )
        self._grid.scheduleDelayedItemsLayout()
        self._grid.recenter()

    # ── Menu actions ────────────────────────────────────────

    def _on_select_all(self):
        count = self._model.rowCount()
        if count > 0:
            self._grid.selectAll()

    def _on_toggle_inspector(self, visible: bool):
        self._inspector_dock.setVisible(visible)

    def _on_open_settings(self):
        dlg = SettingsDialog(self._tool_registry, self)
        dlg.exec()

    def _on_rescan_asset(self, asset_id: int):
        asset = self._queries.get_asset(asset_id)
        if asset is None:
            return
        self._queries.update_scan_state(asset_id, "pending")
        report = scan_file(asset.filepath)
        if report.contents:
            self._queries.insert_scan_results(asset_id, report.contents)
        self._queries.update_scan_state(asset_id, "done")
        # Mark thumbnail for regeneration (cover label may have been set)
        self._queries.update_thumbnail(asset_id, None, "pending")
        self._refresh_inspector()
        self._start_background_thumbs()
        self._status_label.setText(f"Re-scanned: {asset.filename}")

    # ── Helpers ─────────────────────────────────────────────

    def _refresh_inspector(self):
        idxs = self._grid.selectionModel().selectedIndexes()
        if len(idxs) == 1:
            self._refresh_inspector_for(idxs[0].data(Qt.UserRole))

    def _refresh_inspector_for(self, asset_id: int):
        asset = self._queries.get_asset(asset_id)
        if asset:
            self._inspector.show_asset(asset)

    def _on_set_unity_path(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Unity Editor Executable", "",
            "Unity.exe (Unity.exe);;All Files (*)"
        )
        if path:
            tool = self._tool_registry.get("Unity Editor")
            if tool:
                tool.executable = path
                self._tool_registry.save(tool)
            self._status_label.setText(f"Unity path set: {path}")

    def _on_set_storage_path(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Assets Storage Directory"
        )
        if not folder:
            return

        new_dir = Path(folder)
        old_str = self._queries.get_setting("library_dir", "")
        old_dir = Path(old_str) if old_str else None

        if old_dir is None:
            # First-time setup — if assets were imported to default location, offer migration
            default_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "VrcAssetOrganizer" / "Library"
            stray = self._queries.get_assets_in_dir(default_dir)
            if stray:
                reply = QMessageBox.question(
                    self, "Migrate Existing Assets?",
                    f"{len(stray)} asset(s) were imported to the default location:\n"
                    f"{default_dir}\n\n"
                    f"Move them to the new location?\n{new_dir}",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    self._migrate_assets(stray, default_dir, new_dir)
            self._queries.set_setting("library_dir", str(new_dir))
            self._relocate_db(new_dir)
            self._status_label.setText(f"Storage path set: {folder}")
            return

        if old_dir.resolve() == new_dir.resolve():
            return  # No change

        # Find assets that reference the old directory
        affected = self._queries.get_assets_in_dir(old_dir)
        if not affected:
            self._queries.set_setting("library_dir", str(new_dir))
            self._relocate_db(new_dir)
            self._status_label.setText(f"Storage path set: {folder}")
            return

        # Confirm migration
        count = len(affected)
        reply = QMessageBox.warning(
            self, "Move Assets?",
            f"Changing the storage location will move {count} extracted asset(s)\n\n"
            f"From: {old_dir}\nTo:   {new_dir}\n\n"
            f"Proceed with migration?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self._migrate_assets(affected, old_dir, new_dir)

        # Save new path
        self._queries.set_setting("library_dir", str(new_dir))
        self._relocate_db(new_dir)

    def _migrate_assets(self, affected: list, src_dir: Path, dest_dir: Path):
        """Move assets from src_dir to dest_dir, updating DB references."""
        # Check for conflicts
        conflicts: dict[int, Path] = {}  # asset_id → existing path at destination
        for asset in affected:
            try:
                rel = asset.filepath.relative_to(src_dir)
            except ValueError:
                continue
            dest = dest_dir / rel
            if dest.exists():
                conflicts[asset.id] = dest

        conflict_choice: str = "skip"
        if conflicts:
            choice = self._ask_migration_conflict(conflicts, affected)
            if choice == "cancel":
                return
            conflict_choice = choice

        moved = 0
        skipped = 0
        errors: list[str] = []
        for asset in affected:
            try:
                rel = asset.filepath.relative_to(src_dir)
            except ValueError:
                continue
            dest = dest_dir / rel
            is_conflict = asset.id in conflicts

            if is_conflict and conflict_choice == "skip":
                self._queries.update_asset_filepath(asset.id, dest)
                skipped += 1
                continue

            if is_conflict and conflict_choice == "rename":
                dest = self._numbered_dest(dest)

            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(asset.filepath), str(dest))
                self._queries.update_asset_filepath(asset.id, dest)
                moved += 1
            except OSError as e:
                errors.append(f"{asset.filename}: {e}")

        parts = [f"Moved {moved} asset(s)"]
        if skipped:
            parts.append(f"kept {skipped} existing")
        if errors:
            parts.append(f"{len(errors)} error(s)")
        self._status_label.setText(f"Storage path updated — {', '.join(parts)}")
        self._model.refresh()
        self._sidebar.refresh()

        if errors:
            QMessageBox.warning(
                self, "Migration Errors",
                "Some files could not be moved:\n\n" + "\n".join(errors[:10])
            )

    def _relocate_db(self, storage_dir: Path):
        """Copy the database to the assets storage directory.

        On next launch, resolve_db_path() finds it there and the metadata
        survives app reinstall — just re-point to the same storage folder.
        """
        dest = storage_dir / "vrc_assets.db"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(self._app.db_path), str(dest))
            _save_db_path(dest)
        except OSError as e:
            self._status_label.setText(f"Could not relocate database: {e}")

    def _ask_migration_conflict(self, conflicts: dict[int, Path],
                                 affected: list) -> str | None:
        """Show conflict dialog. Returns 'skip', 'overwrite', 'rename', or 'cancel'."""
        affected_map = {a.id: a for a in affected}

        dlg = QDialog(self)
        dlg.setWindowTitle("Conflicts — Destination Already Has Files")
        dlg.setMinimumSize(520, 340)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel(
            f"{len(conflicts)} file(s) already exist at the new location.\n"
            f"Compare sizes below to decide:"
        ))

        # Scrollable conflict list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setSpacing(4)
        list_layout.setContentsMargins(0, 0, 0, 0)

        from vrc_organizer.models.asset import Asset
        for asset_id, dest_path in list(conflicts.items())[:20]:
            asset = affected_map.get(asset_id)
            if asset is None:
                continue
            dest_size = ""
            try:
                if dest_path.is_file():
                    dest_size = f"{dest_path.stat().st_size:,} B"
                elif dest_path.is_dir():
                    dest_size = "(directory)"
            except OSError:
                dest_size = "(inaccessible)"

            row = QLabel(
                f"• <b>{asset.filename}</b><br>"
                f"&nbsp;&nbsp;Existing: {dest_size} &nbsp;|&nbsp; "
                f"Incoming: {asset.file_size:,} B"
            )
            row.setTextFormat(Qt.RichText)
            row.setWordWrap(True)
            list_layout.addWidget(row)
        list_layout.addStretch()

        scroll.setWidget(list_widget)
        layout.addWidget(scroll)

        # Choice radiobox
        group = QGroupBox("When a file already exists at the destination:")
        group_layout = QVBoxLayout(group)
        rb_skip = QRadioButton("Skip — keep existing files in place, update references")
        rb_overwrite = QRadioButton("Overwrite — replace existing files with incoming ones")
        rb_rename = QRadioButton("Add number — keep both copies (e.g. \"Pack (2)\")")
        rb_skip.setChecked(True)
        group_layout.addWidget(rb_skip)
        group_layout.addWidget(rb_overwrite)
        group_layout.addWidget(rb_rename)
        layout.addWidget(group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() != QDialog.Accepted:
            return "cancel"
        if rb_skip.isChecked():
            return "skip"
        if rb_overwrite.isChecked():
            return "overwrite"
        return "rename"

    @staticmethod
    def _numbered_dest(dest: Path) -> Path:
        """Return dest path with a number appended if it exists: 'Name (2).ext'."""
        stem = dest.stem
        suffix = dest.suffix
        n = 2
        while True:
            candidate = dest.with_name(f"{stem} ({n}){suffix}") if suffix else dest.with_name(f"{stem} ({n})")
            if not candidate.exists():
                return candidate
            n += 1

    def _on_batch_thumbnails(self):
        if hasattr(self, '_thumb_worker') and self._thumb_worker is not None:
            self._thumb_worker.cancel()
            self._thumb_worker = None
        pending = self._queries.get_pending_thumbs(limit=0)
        if not pending:
            self._status_label.setText("No pending thumbnails to generate.")
            return
        self._status_label.setText(f"Generating thumbnails for {len(pending)} asset(s)...")
        self._thumb_worker = ThumbWorker(self._queries, self._app.thumb_cache_dir, limit=0)
        self._thumb_worker.signals.status.connect(self._on_import_status)
        self._thumb_worker.signals.finished.connect(self._on_thumbs_finished)
        QThreadPool.globalInstance().start(self._thumb_worker)

    def _on_review_tags(self):
        dlg = TagReviewerDialog(self._queries, self._app.thumb_cache_dir, self)
        dlg.review_complete.connect(lambda: self._sidebar.refresh())
        dlg.review_complete.connect(lambda: self._model.refresh())
        dlg.exec()

    def _on_train_covers(self):
        dlg = CoverTrainerDialog(self._queries, self)
        dlg.training_complete.connect(self._regenerate_labeled_thumbs)
        dlg.exec()

    def _regenerate_labeled_thumbs(self):
        count = self._queries.reset_thumbs_for_labeled()
        if count == 0:
            self._model.refresh()
            return
        self._status_label.setText(f"Regenerating thumbnails with labeled covers ({count} asset(s))...")
        self._thumb_worker = ThumbWorker(self._queries, self._app.thumb_cache_dir, limit=0)
        self._thumb_worker.signals.status.connect(self._on_import_status)
        self._thumb_worker.signals.finished.connect(self._on_thumbs_finished)
        QThreadPool.globalInstance().start(self._thumb_worker)

    def _on_toggle_theme(self):
        self._theme.toggle()
        self._dark_action.setChecked(self._theme.is_dark)

    # ── Drag & Drop ────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self._drop_overlay.show_overlay()
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drop_overlay.hide_overlay()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._drop_overlay.hide_overlay()
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()]
            if paths:
                self._on_files_dropped(paths)
                event.acceptProposedAction()
