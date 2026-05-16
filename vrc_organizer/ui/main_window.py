from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import send2trash
from PySide6.QtCore import Qt, QSize, QThreadPool, QTimer, QSettings
from PySide6.QtGui import QPixmapCache
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QDockWidget, QLabel,
    QStatusBar, QMenuBar, QMenu, QToolBar, QLineEdit, QSlider,
    QVBoxLayout, QHBoxLayout, QProgressBar, QFileDialog, QCheckBox, QMessageBox,
    QPushButton, QDialog, QDialogButtonBox, QScrollArea, QApplication,
    QListWidget, QListWidgetItem, QGroupBox, QRadioButton,
)

from vrc_organizer.app import VrcApp, _save_db_path
from vrc_organizer.database.queries import Queries
from vrc_organizer.ui.theme import ThemeManager
from vrc_organizer.ui.thumbnail_grid import (
    AssetListModel, ThumbnailDelegate, AssetListView, THUMB_SIZE,
    set_show_romaji_cached,
)
from vrc_organizer.ui.inspector import InspectorPanel
from vrc_organizer.ui.tag_dialog import TagDialog
from vrc_organizer.ui.context_menu import AssetContextMenu
from vrc_organizer.ui.drop_overlay import DropOverlay
from vrc_organizer.ui.sidebar import Sidebar
from vrc_organizer.ui.settings_dialog import SettingsDialog
from vrc_organizer.ui.cover_labeler import CoverLabelerDialog
from vrc_organizer.ui.tag_labeler import TagLabelerDialog
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
        self._queries.requeue_failed_thumbs()
        # Rename "Outfit & Acce" → "Outfit" (the new taxonomy splits it
        # from Accessory). One-shot, idempotent.
        self._queries.migrate_legacy_genres()
        self._sidebar.refresh()
        self._model.refresh()

        # Start background thumbnail generation for any pending assets
        QTimer.singleShot(500, self._start_background_thumbs)

    def showEvent(self, event):
        super().showEvent(event)
        # Auto-open a labeler on first show if there's work to do
        if not hasattr(self, '_auto_open_done'):
            self._auto_open_done = True
            QTimer.singleShot(800, self._maybe_auto_open_labeler)

    def _maybe_auto_open_labeler(self):
        cover_count = len(self._queries.get_unlabeled_cover_assets(limit=1))
        tag_count = len(self._queries.get_unlabeled_tag_assets(limit=1))
        if cover_count == 0 and tag_count == 0:
            return
        s = QSettings()
        last = s.value("last_auto_labeler", "tag")
        # Alternate which labeler opens
        if last == "tag" and cover_count > 0:
            s.setValue("last_auto_labeler", "cover")
            self._on_label_covers()
        elif tag_count > 0:
            s.setValue("last_auto_labeler", "tag")
            self._on_label_tags()
        elif cover_count > 0:
            self._on_label_covers()

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

        view_menu.addSeparator()
        # Show romaji "furigana" alongside Japanese asset titles.
        # Persisted via QSettings so the preference survives a restart.
        self._show_romaji_action = view_menu.addAction("Show Romaji (furigana)")
        self._show_romaji_action.setCheckable(True)
        self._show_romaji_action.setChecked(
            QSettings().value("show_romaji", True, type=bool)
        )
        self._show_romaji_action.toggled.connect(self._on_toggle_romaji)

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
        tools_menu.addAction("Rescan All Assets...", self._on_rescan_all)
        tools_menu.addSeparator()
        tools_menu.addAction("Label Tags...", self._on_label_tags)
        tools_menu.addAction("Label Covers...", self._on_label_covers)
        tools_menu.addAction("Manage Tags...", self._on_manage_tags)
        tools_menu.addSeparator()

        # Training submenu — three related families:
        #   1. Crawl folders to mine tag signals from filenames/archives
        #   2. Share the cross-user-portable slice (tag pool) with friends
        #   3. Back up / restore the full per-asset training data of THIS
        #      library (cover labels + tag reviews). Not shareable; it's
        #      effectively a personal training-data backup.
        training_menu = tools_menu.addMenu("&Training")
        training_menu.addAction(
            "Crawl Folder for Training Signals...", self._on_crawl_training
        )
        training_menu.addSeparator()
        training_menu.addAction(
            "Share Tag Pool with a Friend...", self._on_export_pool
        )
        training_menu.addAction(
            "Merge Tag Pool from a Friend...", self._on_import_pool
        )
        training_menu.addSeparator()
        training_menu.addAction(
            "Back Up This Library's Training Data...", self._on_export_training
        )
        training_menu.addAction(
            "Restore Training Data Backup...", self._on_import_training
        )

        tools_menu.addSeparator()
        tools_menu.addAction("Purge Cache && Packages...", self._on_purge_cache)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction("About")

    # ── Toolbar ─────────────────────────────────────────────

    def _setup_toolbar(self):
        # Toolbar widgets are hosted inside the grid panel (search left, asset
        # count + density slider right). Built here so they exist before the
        # central widget references them.
        self._search_bar = QLineEdit()
        self._search_bar.setPlaceholderText("Search assets")
        self._search_bar.setClearButtonEnabled(True)
        self._search_bar.setMaximumWidth(280)
        # No fixed height — the theme stylesheet sets vertical padding, and a
        # 26px clamp crops descenders on Korean/Japanese characters.
        self._search_bar.setMinimumHeight(28)
        self._search_bar.textChanged.connect(self._on_search_changed)

        self._global_search_cb = QCheckBox("All")
        self._global_search_cb.setToolTip("Search across all assets, ignoring type and tag filters")
        self._global_search_cb.toggled.connect(lambda: self._apply_filters())

        # Live asset count label — populated by _refresh_asset_count().
        self._asset_count_label = QLabel("0 assets loaded")
        self._asset_count_label.setStyleSheet("color: palette(mid); font-size: 11px;")

        s = QSettings()
        saved_density = int(s.value("grid_density", 5))
        self._grid_slider = QSlider(Qt.Horizontal)
        self._grid_slider.setRange(1, 10)
        self._grid_slider.setValue(max(1, min(10, saved_density)))
        self._grid_slider.setFixedWidth(140)
        self._grid_slider.setTickInterval(1)
        self._grid_slider.valueChanged.connect(self._on_grid_size_changed)

    def _refresh_asset_count(self):
        if not hasattr(self, "_asset_count_label") or not hasattr(self, "_model"):
            return
        n = self._model.rowCount()
        self._asset_count_label.setText(f"{n:,} assets loaded")

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
        grid_layout.setSpacing(4)

        # Header row hosting search (left), grid density (right) — sits
        # inside the grid panel so it shares the cards' horizontal real estate.
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 6, 8, 4)
        header_layout.setSpacing(8)
        header_layout.addWidget(self._search_bar)
        header_layout.addWidget(self._global_search_cb)
        header_layout.addWidget(self._asset_count_label)
        header_layout.addStretch(1)
        grid_label = QLabel("Grid")
        grid_label.setStyleSheet("font-size: 11px;")
        header_layout.addWidget(grid_label)
        header_layout.addWidget(self._grid_slider)
        grid_layout.addWidget(header)

        self._model = AssetListModel(self._queries)

        self._grid = AssetListView()
        # Push the persisted density into the grid before setModel() so the
        # first relayout uses the saved card width — otherwise cards render
        # at the hardcoded default (5) until the user touches the slider.
        self._grid.set_density(self._grid_slider.value())
        self._grid.setModel(self._model)
        self._grid.files_dropped.connect(self._on_files_dropped)
        self._grid.delete_requested.connect(self._on_delete_selected)
        self._grid.selection_changed.connect(self._on_selection_changed)
        self._grid.customContextMenuRequested.connect(self._on_context_menu)
        self._grid.doubleClicked.connect(self._on_grid_double_clicked)
        # Keep the "N assets loaded" label in sync with whatever the model holds.
        self._model.modelReset.connect(self._refresh_asset_count)
        self._model.rowsInserted.connect(self._refresh_asset_count)
        self._model.rowsRemoved.connect(self._refresh_asset_count)

        grid_layout.addWidget(self._grid, 1)

        splitter.addWidget(sidebar)
        splitter.addWidget(grid_panel)
        # Wider sidebar default so Avatars/Tags chips have room to wrap
        # comfortably instead of stacking one chip per row.
        splitter.setSizes([280, 1000])

        self.setCentralWidget(splitter)

        self._drop_overlay = DropOverlay(self._grid.viewport())
        self._grid.drag_entered.connect(self._drop_overlay.show_overlay)
        self._grid.drag_left.connect(self._drop_overlay.hide_overlay)

    # ── Inspector Dock ──────────────────────────────────────

    def _setup_inspector_dock(self):
        self._inspector = InspectorPanel(self._queries, self._tool_registry)
        self._inspector.tag_added.connect(self._on_tag_add_request)
        self._inspector.tag_removed.connect(self._on_tag_removed)
        self._inspector.tag_renamed.connect(self._on_tag_renamed)
        self._inspector.tag_deleted.connect(self._on_tag_deleted)
        self._inspector.notes_changed.connect(self._on_notes_save)
        self._inspector.open_with.connect(self._on_open_with)
        self._inspector.import_to_unity.connect(self._on_import_to_unity)
        self._inspector.change_thumbnail.connect(self._on_change_thumbnail)

        self._inspector_dock = QDockWidget("Inspector", self)
        self._inspector_dock.setWidget(self._inspector)
        self._inspector_dock.setFeatures(
            QDockWidget.DockWidgetMovable |
            QDockWidget.DockWidgetFloatable |
            QDockWidget.DockWidgetClosable
        )
        self._inspector.setMinimumWidth(360)
        saved_w = int(QSettings().value("inspector_width", 560))
        saved_w = max(360, min(1200, saved_w))
        self._inspector_dock.resize(saved_w, self._inspector_dock.height())
        self._inspector_dock.visibilityChanged.connect(
            lambda v: self._inspector_toggle_action.setChecked(v)
        )
        self.addDockWidget(Qt.RightDockWidgetArea, self._inspector_dock)
        self.resizeDocks([self._inspector_dock], [saved_w], Qt.Horizontal)
        # Catch live resizes (drag the splitter) so the chosen width sticks.
        self._inspector_dock.installEventFilter(self)
        # Coalesce QSettings writes from a drag into one settle write — the
        # raw resize event fires once per pixel of cursor motion, so an
        # eager write would touch the registry hundreds of times per drag.
        self._dock_save_timer = QTimer(self)
        self._dock_save_timer.setSingleShot(True)
        self._dock_save_timer.setInterval(250)
        self._dock_save_timer.timeout.connect(self._save_inspector_width)

    def _save_inspector_width(self):
        w = self._inspector_dock.width()
        if w >= 360:
            QSettings().setValue("inspector_width", int(w))

    def eventFilter(self, obj, event):
        # Schedule a debounced QSettings write on dock resize. QDockWidget
        # doesn't have a dedicated resize signal, so we hook the event.
        if obj is getattr(self, "_inspector_dock", None) and event.type() == event.Type.Resize:
            self._dock_save_timer.start()
        return super().eventFilter(obj, event)

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
        if hasattr(self, '_current_worker') and self._current_worker is not None:
            self._current_worker.cancel()
            self._current_worker = None

        # Transparent import message: the user wanted to know exactly what's
        # happening on disk during an import. Source files stay where they
        # are (in-place mode); only metadata and thumbnails land in the
        # AppData cache directories.
        thumb_dir = self._app.thumb_cache_dir
        db_path = self._app.db_path
        self._status_label.setText(
            f"Importing {len(paths)} file(s) in-place — sources stay put. "
            f"Metadata → {db_path.name}, thumbnails → {thumb_dir}"
        )
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
        # Imports can upsert (filename/size/mod_time changing on re-import),
        # so drop the per-asset cache before refresh.
        self._model.invalidate_caches()
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
            # Thumb worker wrote new thumbnail paths for a known set of
            # assets. Each row's cached Asset (and pixmap) is stale, but
            # the filtered set is unchanged — redraw just those cards.
            self._model.refresh_assets(found_ids)
        # Continue processing remaining pending thumbnails
        QTimer.singleShot(50, self._start_background_thumbs)

    def _check_multi_unitypackage(self, asset_ids: list[int]):
        """If any imported asset is a zip with multiple .unitypackage files,
        offer to extract them as separate assets."""

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

            # Multi-select split dialog. Wording: default (unchecked) is the
            # safe path — keep the archive as one asset. Checking an entry
            # promotes that .unitypackage into its OWN asset card.
            dlg = QDialog(self)
            dlg.setAttribute(Qt.WA_DeleteOnClose)
            dlg.setWindowTitle("Split Multi-Package Archive?")
            dlg.setMinimumWidth(440)

            layout = QVBoxLayout(dlg)
            layout.addWidget(QLabel(
                f"<b>{asset.filename}</b> contains {len(up_entries)} "
                ".unitypackage files.<br>"
                "By default the archive is imported as a single asset. "
                "Tick the packages below that you'd rather import as "
                "<b>their own separate cards</b>."
            ))

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            check_layout = QVBoxLayout(container)
            cbs = {}
            for name, etype in sorted(up_entries):
                cb = QCheckBox(name.split("/")[-1])
                cb.setToolTip(name)
                cb.setChecked(False)  # default OFF — see dialog title
                check_layout.addWidget(cb)
                cbs[name] = cb
            check_layout.addStretch()
            scroll.setWidget(container)
            layout.addWidget(scroll)

            btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            btns.button(QDialogButtonBox.Ok).setText("Split selected")
            btns.button(QDialogButtonBox.Cancel).setText("Keep as one asset")
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            layout.addWidget(btns)

            if dlg.exec() != QDialog.Accepted:
                continue

            selected = [name for name, cb in cbs.items() if cb.isChecked()]
            if not selected:
                continue

            try:
                with zipfile.ZipFile(asset.filepath, "r") as zf:
                    for name in selected:
                        data = zf.read(name)
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

    def _on_selection_changed(self, *args):
        selected_ids = self._grid.selected_asset_ids()
        count = len(selected_ids)
        total = self._model.rowCount()

        if count == 1:
            asset = self._queries.get_asset(selected_ids[0])
            if asset:
                self._inspector.show_asset(asset)
        else:
            self._inspector.show_empty()

        self._status_label.setText(f"{total} assets • {count} selected")

    def _on_grid_double_clicked(self, index):
        if not index.isValid():
            return
        asset = self._queries.get_asset(index.data(Qt.UserRole))
        if asset and asset.filepath.exists():
            os.startfile(str(asset.filepath))

    # ── Slots: Context Menu ─────────────────────────────────

    def _on_context_menu(self, pos):
        aid = self._grid.current_asset_id()
        if aid is None:
            return

        asset = self._queries.get_asset(aid)
        if asset is None:
            return

        menu = AssetContextMenu(
            asset.id, asset.filename, asset.filepath,
            asset.filetype, self._queries, self._tool_registry, self
        )
        menu.open_in.connect(self._on_open_with)
        menu.add_tag.connect(lambda tid: self._do_add_tag(asset.id, tid))
        menu.delete_asset.connect(self._on_delete_asset)
        menu.delete_file.connect(self._on_delete_file_from_disk)
        menu.rescan.connect(self._on_rescan_asset)
        menu.exec(self._grid.viewport().mapToGlobal(pos))

    # ── Slots: Tags ─────────────────────────────────────────

    def _on_tag_add_request(self, asset_id: int, tag_id: int):
        self._do_add_tag(asset_id, tag_id)

    def _has_active_tag_filter(self) -> bool:
        """True if any tag-based filter is set on the grid model.

        When no filter is active, a single tag add/remove can't move the
        asset in or out of the visible set — so we can skip the full
        SQL refresh and only redraw the one card. Search-text filter
        also counts because filenames/tag-text could match.
        """
        return bool(
            getattr(self, "_or_tag_filters", None)
            or getattr(self, "_and_tag_filters", None)
            or getattr(self, "_search_terms", None)
        )

    def _on_tag_removed(self, asset_id: int, tag_id: int):
        self._queries.remove_tag_from_asset(asset_id, tag_id)
        self._refresh_inspector_for(asset_id)
        # Try the fast path first — chip count delta only. Falls back
        # to a full rebuild if the chip isn't on screen yet.
        if not self._sidebar.bump_tag_counts([tag_id], delta=-1):
            self._sidebar.refresh()
        if self._has_active_tag_filter():
            self._model.refresh()
        else:
            self._model.refresh_asset(asset_id)

    def _on_tag_renamed(self, tag_id: int, new_name: str):
        self._queries.rename_tag(tag_id, new_name)
        self._sidebar.refresh()
        # Tag rename doesn't move any asset in/out of the filter and the
        # grid cards don't display tag names, so no model refresh — the
        # inspector pulls the new name on its own refresh.
        self._refresh_inspector()

    def _on_tag_deleted(self, tag_id: int):
        self._queries.delete_tag(tag_id)
        self._sidebar.refresh()
        # Tag deletion can shift filter membership (if the deleted tag
        # was in the active filter) — safer to do a full refresh.
        self._model.refresh()
        self._refresh_inspector()

    def _do_add_tag(self, asset_id: int, tag_id: int):
        self._queries.add_tag_to_asset(asset_id, tag_id)
        # Record co-occurrence with existing tags on this asset
        existing = [t[0] for t in self._queries.get_tags_for_asset(asset_id)]
        if len(existing) >= 2:
            self._queries.record_tag_cooccurrence(existing)
        self._refresh_inspector_for(asset_id)
        # Fast path on the sidebar: just bump the chip count. Falls back
        # to a full rebuild for tags that don't have a chip yet (newly
        # created tag, count was at 0, etc.).
        if not self._sidebar.bump_tag_counts([tag_id], delta=+1):
            self._sidebar.refresh()
        if self._has_active_tag_filter():
            self._model.refresh()
        else:
            # Common case (genre swap, adding a tag with no filter
            # active): nothing changes in the grid except this one
            # card's cached state. Skip the full SQL refresh.
            self._model.refresh_asset(asset_id)

    def _on_manage_tags(self):
        dlg = TagDialog(self._queries, self)
        dlg.tags_changed.connect(self._refresh_inspector)
        dlg.tags_changed.connect(lambda: self._sidebar.refresh())
        dlg.tags_changed.connect(lambda: self._model.refresh())
        dlg.exec()

    # ── Slots: Notes ────────────────────────────────────────

    def _on_notes_save(self, asset_id: int, notes: str):
        self._queries.update_notes(asset_id, notes)
        # The model's cached Asset has stale notes; drop it so the next
        # inspector visit or grid tooltip pulls fresh data. Notes aren't
        # rendered on the card itself, so no visible card update needed.
        self._model.refresh_asset(asset_id)

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
        dlg.setAttribute(Qt.WA_DeleteOnClose)
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
        """Right-click → Remove from Library. DB-only, file untouched."""
        asset = self._queries.get_asset(asset_id)
        if asset is None:
            return
        reply = QMessageBox.question(
            self, "Remove from Library",
            f"Remove \"{asset.filename}\" from the library?\n\n"
            f"The source file at:\n  {asset.filepath}\n"
            f"will NOT be deleted — only this app's record of it.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._remove_asset_db_only(asset)
        self._model.refresh()
        self._status_label.setText(f"Removed from library — {self._model.rowCount()} total")

    def _on_delete_file_from_disk(self, asset_id: int):
        """Right-click → Delete File from Disk. Recycle bin + DB row."""
        asset = self._queries.get_asset(asset_id)
        if asset is None:
            return
        reply = QMessageBox.warning(
            self,
            "Delete File from Disk?",
            "This sends the actual file to the Recycle Bin and removes it "
            "from the library.\n\n"
            f"  File: {asset.filename}\n"
            f"  Path: {asset.filepath}\n\n"
            "Recoverable from the Recycle Bin until you empty it.\n\n"
            "Are you sure?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._recycle_file_for_asset(asset)
        self._model.refresh()
        self._status_label.setText(
            f"Recycled \"{asset.filename}\" — {self._model.rowCount()} total"
        )

    def _remove_asset_db_only(self, asset):
        """Drop the asset's DB row without touching the file."""
        self._queries.delete_asset(asset.id)

    def _recycle_file_for_asset(self, asset):
        """Send the asset's file to the Recycle Bin, then drop its DB row."""
        try:
            send2trash.send2trash(str(asset.filepath))
        except Exception:
            pass
        self._queries.delete_asset(asset.id)

    def _on_delete_selected(self):
        """Delete key on the grid — bulk Remove from Library (DB-only).

        Multi-select file deletion is intentionally NOT bound to a key.
        Recycling files goes one-at-a-time through the right-click menu so
        an accidental keypress can never wipe a batch of source files.
        """
        ids = self._grid.selected_asset_ids()
        if not ids:
            return
        count = len(ids)
        reply = QMessageBox.question(
            self, "Remove from Library",
            f"Remove {count} asset(s) from the library?\n\n"
            "Source files on disk will NOT be deleted — only this app's "
            "records of them.\n\n"
            "(To delete a file from disk, use right-click → "
            "\"Delete File from Disk…\" on a single asset.)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for asset_id in ids:
            asset = self._queries.get_asset(asset_id)
            if asset:
                self._remove_asset_db_only(asset)
        self._model.refresh()
        self._status_label.setText(
            f"{count} asset(s) removed from library — {self._model.rowCount()} total"
        )

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
        # Slider 1 = smallest cards (most columns), 10 = largest cards.
        # Debounce the QSettings write — dragging the slider fires this
        # signal once per integer step.
        self._pending_density = int(value)
        if not hasattr(self, "_density_save_timer"):
            self._density_save_timer = QTimer(self)
            self._density_save_timer.setSingleShot(True)
            self._density_save_timer.setInterval(250)
            self._density_save_timer.timeout.connect(
                lambda: QSettings().setValue("grid_density", self._pending_density)
            )
        self._density_save_timer.start()
        self._grid.set_density(value)
        # Pull fresh pixmaps at the new resolution. Direct call to the view
        # method instead of emitting model.dataChanged across every row —
        # both do the same work, but the direct call skips Qt's signal
        # bookkeeping for every visible card.
        self._grid._refresh_card_data()  # type: ignore[attr-defined]

    # ── Menu actions ────────────────────────────────────────

    def _on_select_all(self):
        if self._model.rowCount() > 0:
            self._grid.select_all()

    def _on_toggle_inspector(self, visible: bool):
        self._inspector_dock.setVisible(visible)

    def _on_import_to_unity(self, asset_id: int):
        """Launch Unity Editor with the asset's .unitypackage as an argument.

        This is the placeholder until the proper Editor plugin lands. We
        already have a configured Unity path in Preferences; using it here
        means the user gets *something* working today without a plugin
        round-trip.
        """
        asset = self._queries.get_asset(asset_id)
        if asset is None:
            return
        unity_path = self._queries.get_setting("unity_path", "")
        if not unity_path or not Path(unity_path).exists():
            QMessageBox.warning(
                self, "Unity not configured",
                "Set the Unity Editor path in Preferences first."
            )
            return
        # The plugin will eventually do a richer handoff (project pick,
        # status report back). For now: spawn Unity with the package and
        # let the user pick a target project in Unity's import dialog.
        try:
            subprocess.Popen(
                [unity_path, "-importPackage", str(asset.filepath)],
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
            self._status_label.setText(
                f"Sent to Unity: {asset.filename} (plugin in development)"
            )
        except OSError as e:
            QMessageBox.critical(
                self, "Failed to launch Unity",
                f"Could not start Unity Editor:\n{e}"
            )

    def _on_toggle_romaji(self, on: bool):
        QSettings().setValue("show_romaji", on)
        # Keep the thumbnail-grid cache in sync — paintEvent reads from this
        # cached value to avoid hitting QSettings on every redraw.
        set_show_romaji_cached(on)
        # Force a redraw — cards repaint, inspector + tag review pick up the
        # setting on next show.
        self._grid.viewport().update()
        if self._grid._cards:  # type: ignore[attr-defined]
            self._grid._refresh_card_data()  # type: ignore[attr-defined]
        if hasattr(self._inspector, "_asset") and self._inspector._asset is not None:  # type: ignore[attr-defined]
            self._inspector.show_asset(self._inspector._asset)  # type: ignore[attr-defined]

    def _on_open_settings(self):
        dlg = SettingsDialog(self._tool_registry, self)
        dlg.exec()

    def _on_rescan_asset(self, asset_id: int):
        asset = self._queries.get_asset(asset_id)
        if asset is None:
            return
        self._queries.update_scan_state(asset_id, "pending")
        self._queries.clear_scan_results(asset_id)
        report = scan_file(asset.filepath)
        if report.contents:
            self._queries.insert_scan_results(asset_id, report.contents)
        self._queries.update_scan_state(asset_id, "done")
        self._queries.update_thumbnail(asset_id, None, "pending")
        # Was missing — the cached Asset has a stale thumbnail path now,
        # and without this the grid card kept rendering the old thumb
        # until some unrelated refresh kicked in.
        self._model.refresh_asset(asset_id)
        self._refresh_inspector()
        self._start_background_thumbs()
        self._status_label.setText(f"Re-scanned: {asset.filename}")

    def _on_rescan_all(self):
        asset_ids = self._queries.get_all_asset_ids()
        if not asset_ids:
            self._status_label.setText("No assets to rescan.")
            return
        reply = QMessageBox.question(
            self, "Rescan All Assets",
            f"Rescan {len(asset_ids)} asset(s)?\n\nThis may take a while.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._status_label.setText(f"Rescanning {len(asset_ids)} assets...")
        for i, aid in enumerate(asset_ids):
            asset = self._queries.get_asset(aid)
            if asset is None or not asset.filepath.exists():
                continue
            self._queries.clear_scan_results(aid)
            try:
                report = scan_file(asset.filepath)
                if report.contents:
                    self._queries.insert_scan_results(aid, report.contents)
                self._queries.update_scan_state(aid, "done")
            except Exception:
                pass
            if (i + 1) % 10 == 0:
                self._status_label.setText(f"Rescanning... {i + 1}/{len(asset_ids)}")
        self._status_label.setText(f"Rescanned {len(asset_ids)} assets.")
        self._model.refresh()
        self._sidebar.refresh()

    # ── Helpers ─────────────────────────────────────────────

    def _refresh_inspector(self):
        ids = self._grid.selected_asset_ids()
        if len(ids) == 1:
            self._refresh_inspector_for(ids[0])

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
        # Filepath fields on moved assets just changed — drop the cache.
        self._model.invalidate_caches()
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
        dlg.setAttribute(Qt.WA_DeleteOnClose)
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

    def _on_label_tags(self):
        if not self._queries.get_unlabeled_tag_assets(limit=1):
            self._status_label.setText("No assets need tag labeling — all caught up.")
            return
        dlg = TagLabelerDialog(self._queries, self._app.thumb_cache_dir, self)
        dlg.labeling_complete.connect(lambda: self._sidebar.refresh())
        dlg.labeling_complete.connect(lambda: self._model.refresh())
        dlg.exec()

    def _on_label_covers(self):
        if not self._queries.get_unlabeled_cover_assets(limit=1):
            self._status_label.setText("No assets need cover labeling — all caught up.")
            return
        dlg = CoverLabelerDialog(self._queries, self._app.thumb_cache_dir, self)
        dlg.labeling_complete.connect(self._regenerate_labeled_thumbs)
        dlg.exec()

    def _on_change_thumbnail(self, asset_id: int):
        """Inspector thumbnail was double-clicked — open the cover picker
        scoped to a single asset. After the dialog closes, mark this asset's
        thumbnail as pending and kick off the background worker so the new
        cover replaces the old one in the grid + inspector."""
        asset = self._queries.get_asset(asset_id)
        if asset is None:
            return
        dlg = CoverLabelerDialog(
            self._queries, self._app.thumb_cache_dir, self,
            single_asset=asset,
        )
        dlg.exec()
        label = self._queries.get_cover_label(asset_id)
        if not label or label in ("__skipped__", "__cached__"):
            return
        if label.startswith("__custom__:"):
            # The labeler already wrote the user-picked image to the
            # thumb cache and set the asset's thumbnail field to ready.
            # Don't requeue — that would let the thumb worker overwrite
            # the custom image with a freshly-mined cover.
            self._refresh_inspector_for(asset_id)
            self._model.refresh_asset(asset_id)
            return
        self._queries.update_thumbnail(asset_id, None, "pending")
        self._refresh_inspector_for(asset_id)
        # Thumbnail field on this asset just changed — drop just this
        # asset's cached copy so the grid picks up the new path on next
        # paint. Other cards are unaffected.
        self._model.refresh_asset(asset_id)
        self._start_background_thumbs()

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

    def _on_export_training(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Training Data", "training_data.json",
            "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        data = self._queries.export_training_data()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            QMessageBox.information(
                self, "Export Complete",
                f"Exported:\n"
                f"  Cover labels: {len(data.get('cover_labels', []))}\n"
                f"  Tag reviews: {len(data.get('tag_reviews', []))}\n"
                f"  Tag labels: {len(data.get('tag_labels', []))}\n"
                f"  Tag co-occurrence: {len(data.get('tag_cooccurrence', []))}"
            )
        except OSError as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _on_import_training(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Training Data", "",
            "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.critical(self, "Import Failed", str(e))
            return
        result = self._queries.import_training_data(data)
        details = []
        if result.get("cover_labels", 0):
            details.append(f"  Cover labels: {result['cover_labels']}")
        if result.get("tag_reviews", 0):
            details.append(f"  Tag reviews: {result['tag_reviews']}")
        if result.get("tag_cooccurrence", 0):
            details.append(f"  Tag co-occurrence: {result['tag_cooccurrence']}")
        details_str = "\n".join(details) if details else "  (no data)"
        QMessageBox.information(
            self, "Import Complete",
            f"Imported {result.get('imported', 0)} record(s):\n{details_str}\n\n"
            f"Skipped {result.get('skipped', 0)} (asset/tag not found)."
        )
        self._sidebar.refresh()
        self._model.refresh()

    def _on_export_pool(self):
        """Export the cross-user shareable slice of training data.

        Just the tag names and their pairwise co-occurrence — the bits that
        actually merge meaningfully into someone else's library. Cover
        labels and per-asset tag reviews stay in the regular export, which
        is meant for backing up your own DB rather than cross-pollination.
        """
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Shared Tag Pool", "tag_pool.json",
            "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        data = self._queries.export_cooccurrence_pool()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            QMessageBox.critical(self, "Export Failed", str(e))
            return
        QMessageBox.information(
            self, "Pool Exported",
            f"{len(data['tags'])} tag(s), {len(data['cooccurrence'])} "
            "co-occurrence pair(s) exported.\n\n"
            "Send the file to a friend; they import it via Tools → "
            "Import Shared Tag Pool. Counts add up cumulatively."
        )

    def _on_import_pool(self):
        """Merge a friend's exported pool into the local DB. Cumulative."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Shared Tag Pool", "",
            "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.critical(self, "Import Failed", str(e))
            return
        result = self._queries.import_cooccurrence_pool(data)
        if "error" in result:
            QMessageBox.critical(self, "Wrong File Type", result["error"])
            return
        if result.get("already_imported"):
            QMessageBox.information(
                self, "Already Imported",
                "This exact pool export was imported before — no changes.\n\n"
                f"  Export id: {result['export_id']}\n"
                f"  Previously: {result['tags_added']} tags / "
                f"{result['pairs_merged']} pairs\n\n"
                "Ask the friend to re-export — the new file gets a fresh id "
                "and will merge cleanly."
            )
            return
        self._sidebar.refresh()
        QMessageBox.information(
            self, "Pool Imported",
            f"New tags created: {result['tags_added']}\n"
            f"Co-occurrence pairs merged: {result['pairs_merged']}\n\n"
            "Counts for tags you already had increase additively. "
            "New tag suggestions will reflect the merged pool on next import."
        )

    def _on_crawl_training(self):
        """Walk a user-chosen folder and harvest tag co-occurrence signals.

        Uses the existing `tag_cooccurrence` table so the autotag pipeline
        learns from the on-disk layout (folder names, archive names) without
        needing the user to manually label every asset.
        """
        folder = QFileDialog.getExistingDirectory(
            self, "Choose a folder to crawl for training signals", ""
        )
        if not folder:
            return
        root = Path(folder)
        reply = QMessageBox.question(
            self,
            "Crawl for training signals?",
            f"Walk every supported asset under:\n  {root}\n\n"
            "Filename + folder tokens get added to the autotag co-occurrence "
            "table. Source files are NOT modified.\n\n"
            "Junk files (.lnk, .ini, .txt, version markers) are skipped.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        from vrc_organizer.tools.training_crawler import crawl_directory
        self._status_label.setText(f"Crawling {root} for training signals...")
        QApplication.processEvents()
        try:
            summary = crawl_directory(self._queries, root)
        except Exception as e:
            QMessageBox.critical(self, "Crawl Failed", str(e))
            self._status_label.setText("Crawl failed.")
            return
        self._sidebar.refresh()
        self._model.refresh()
        QMessageBox.information(
            self,
            "Crawl Complete",
            f"Files seen: {summary['files']}\n"
            f"Archives opened (pathnames + readmes mined): "
            f"{summary.get('archives_opened', 0)}\n"
            f"Tokens collected: {summary['tokens']}\n"
            f"Tag-pair signals written: {summary['pairs_written']}\n\n"
            "These feed into tag suggestions on next import."
        )
        self._status_label.setText(
            f"Crawl complete — {summary['pairs_written']} pairs written"
        )

    def _on_purge_cache(self):
        """Debug-grade purge: wipes thumbnails, extracted packages, AND the
        DB rows for assets/tags/labels/scan results. User explicitly asked
        for this to be a full reset (not just cache)."""
        thumb_dir = self._app.thumb_cache_dir
        library_str = self._queries.get_setting("library_dir", "")
        library_dir = Path(library_str) if library_str else (
            Path(os.environ.get("LOCALAPPDATA", "")) / "VrcAssetOrganizer" / "Library"
        )

        # Count what will be deleted
        thumb_count = len(list(thumb_dir.glob("*.png"))) if thumb_dir.exists() else 0
        lib_count = 0
        lib_size = 0
        if library_dir.exists():
            for p in library_dir.iterdir():
                lib_count += 1
                if p.is_dir():
                    lib_size += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                elif p.is_file():
                    lib_size += p.stat().st_size

        if thumb_count == 0 and lib_count == 0:
            QMessageBox.information(self, "Nothing to Purge",
                "No cached thumbnails or extracted packages found.")
            return

        thumb_size_str = f"{thumb_count} thumbnail(s)"
        lib_size_str = f"{lib_count} item(s)" if lib_count else "none"
        if lib_size:
            if lib_size >= 1_000_000:
                lib_size_str += f" ({lib_size / 1_000_000:.1f} MB)"
            elif lib_size >= 1_000:
                lib_size_str += f" ({lib_size / 1_000:.0f} KB)"

        # Always offer the nuke option, even with zero cache items — assets
        # in the DB still want clearing in debug mode.
        asset_total = self._model.rowCount() if hasattr(self, "_model") else 0
        reply = QMessageBox.warning(
            self, "Purge Library (DEBUG)",
            "This is a full reset and CANNOT be undone:\n\n"
            f"  • {thumb_size_str} from thumbnail cache\n"
            f"  • {lib_size_str} from extracted packages\n"
            f"  • {asset_total} asset card(s), all tags, labels, "
            f"scan results, and tag co-occurrence rows from the DB\n\n"
            "Source files on disk are NOT touched.\n"
            "Default tag set will be re-seeded on next launch.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        purged_thumbs = 0
        errors = []

        # Purge thumbnail cache
        if thumb_dir.exists():
            for f in thumb_dir.glob("*.png"):
                try:
                    f.unlink()
                    purged_thumbs += 1
                except OSError as e:
                    errors.append(str(f))

        # Purge extracted packages
        purged_libs = 0
        if library_dir.exists():
            for p in library_dir.iterdir():
                try:
                    if p.is_dir():
                        shutil.rmtree(p)
                    else:
                        p.unlink()
                    purged_libs += 1
                except OSError as e:
                    errors.append(str(p))

        if errors:
            QMessageBox.warning(
                self, "Purge Complete (with errors)",
                f"Purged {purged_thumbs} thumbnail(s) and {purged_libs} package(s).\n\n"
                f"Failed to remove {len(errors)} item(s):\n"
                + "\n".join(errors[:5]) +
                ("\n..." if len(errors) > 5 else "")
            )
        else:
            QMessageBox.information(
                self, "Purge Complete",
                f"Purged {purged_thumbs} thumbnail(s) and {purged_libs} package(s)."
            )

        # Debug nuke: wipe DB-side state too. After this the library is
        # empty and the user can re-import from scratch.
        db_counts = self._queries.hard_purge_all()
        # Re-seed default genre/avatar tags so the UI has something to bind to.
        self._seed_default_tags()
        QPixmapCache.clear()
        self._model.invalidate_caches()
        self._model.refresh()
        self._sidebar.refresh()
        self._inspector.show_empty()

        QMessageBox.information(
            self, "Purge complete",
            "Wiped:\n  • "
            + "\n  • ".join(f"{n} {k}" for k, n in db_counts.items() if n)
            + f"\n  • {purged_thumbs} cached thumbnail(s)"
            + f"\n  • {purged_libs} extracted package(s)"
        )

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
