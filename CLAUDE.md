# CLAUDE.md

## Project
Booth Organizer is a Windows desktop app for VRchat avatar creators to organize, tag, search, and browse 3D asset files downloaded from Booth.pm. Built with PySide6 + SQLite.

## Scope
This is a **single-user desktop tool** — not a server, not a service, not multi-tenant. The target user is a VRchat avatar creator who downloads 10–500+ asset packs from Booth.pm and needs to organize them across multiple Unity projects. The app is a file-organizer with deep format introspection, **not** a Unity replacement and **not** a 3D viewer.

## Expected User Experience
- **Import:** Drag .zip / .unitypackage / .blend / .fbx / .png files onto the window. The app scans them, classifies contents, extracts a preview thumbnail, auto-tags from filename/folder analysis, auto-classifies genre, and stores metadata in a local SQLite database. Archives (.zip, .rar) are extracted to a user-configurable library directory so individual files inside can be opened.
- **Browse:** A responsive thumbnail grid with lazy loading. Cards show the preview image and filename. Selection is multi-select. Cards are centered in the viewport.
- **Filter:** Left sidebar has genre toggle buttons (Avatar Base, Outfit & Acce, Gimmick, Tools), an avatar picker (searchable, popularity-ordered), a body-map widget (visual body-part selector), and a tag tree (user-created, color-coded). **Body map + genre + avatar use OR (union) logic.** **Tag tree uses AND (intersection) logic.** A search bar at the top supports partial-word matching (FTS5 prefix). A "Search All" checkbox bypasses active filters.
- **Inspect:** Right dock shows the selected asset's thumbnail, filename, file metadata (type, size, path elided from front, dates), genre combobox (auto-deduced, overridable), avatar tag chips, additional tag chips, a contents tree with specific types (e.g. "image (psd)") and human-readable sizes, notes, and "Open With" buttons for configured tools.
- **Act:** Right-click an asset for context menu — Reveal in File Explorer, Open With, Add Tag, Re-scan, Delete (with confirmation). Double-click a content entry to open the extracted file.
- **Configure:** Preferences menu for Dark Mode toggle, Unity Editor path, Assets Storage Path. Full Settings dialog for tool configuration.

## Design Principles
1. **Fast over fancy.** The import pipeline, search, and filtering must feel instant for libraries up to ~500 assets. Defer expensive work. Lazy-load the grid. Cache what you can.
2. **Don't lose data.** Delete operations must confirm. Import must be atomic-ish — an asset either imports fully or not at all. File extraction failures must log, not silently swallow.
3. **Respect the OS.** Use the system palette for theming. Store data in %LOCALAPPDATA%. Use `os.startfile` for opening files. Use `subprocess.run(['explorer', '/select,', path])` for revealing files.
4. **Flat tags with hierarchy implications.** Tags in the DB are a flat list. TAG_HIERARCHY in tag_data.py is used only during auto-tagging: detecting a child tag (e.g. "Earrings") auto-adds its parent ("Accessory"). The UI displays all tags as flat chips grouped by category (genre, avatar, additional).
5. **Thread safety by isolation.** Workers run on QThreadPool, communicate only via Qt signals (AutoConnection queues to main thread). No shared mutable state between worker and UI.
6. **No premature abstraction.** Three similar lines is better than a premature helper. Don't add service layers, factories, or plugin systems unless the code actually needs them.
7. **Memory-conscious.** QPixmap cache bounded at 200 entries (FIFO eviction). SQLite cache capped at 2 MB. Qt modules aggressively excluded from build to avoid loading unused DLLs into RAM. Thumbnails loaded lazily (PAGE_SIZE=100). Workers nulled after completion.

## Architecture
```
main.py → BoothApp (QApplication)
        → DatabaseManager (connection pool, WAL mode, thread-local connections)
        → Queries (all SQL lives here — CRUD, search, filtering, settings)
        → MainWindow
            ├── Sidebar (genre chips, avatar chips, BodyMapWidget, tag chips)
            ├── AssetListView (QListView IconMode)
            │   ├── AssetListModel (lazy PAGE_SIZE=100, OR/AND tag filter)
            │   └── ThumbnailDelegate (card = thumb + label)
            ├── InspectorPanel (dock widget — genre, avatar, tags, contents, notes, tools)
            ├── AssetContextMenu (right-click on asset)
            ├── ToolRegistry (QSettings-backed tool configs)
            └── ToolLauncher (subprocess.Popen with DETACHED_PROCESS)
```
- **Database:** SQLite WAL mode, single writer (mutex), multi-reader. FTS5 external content table on assets (filename, notes) with triggers for insert/update/delete sync.
- **Import flow:** Drag files → MainWindow._on_files_dropped → ImportWorker (QRunnable) → scan_file → DB insert → auto_tagger.suggest_tags() for tag detection → auto_tagger.suggest_genre() for genre classification → model.refresh() + sidebar.refresh() + grid.select_asset_ids().
- **Filter flow:** Sidebar.tag_filter_changed(or_ids, and_ids) → MainWindow._on_tag_filter → _apply_filters → model.set_filter(or_tag_ids, and_tag_ids). OR group uses `WHERE tag_id IN (...)` subquery (union). AND group uses individual `WHERE tag_id = ?` subqueries (intersection).
- **Search:** FTS5 prefix matching — each word token gets `*` appended. Reserved FTS5 words (AND, OR, NOT, NEAR) get double-quoted.
- **Tag system:** Tags are flat in the DB (no parent/child). Categories enforced at UI level: genre (exactly one of 4), avatar (popularity-ordered list), additional (everything else). TAG_HIERARCHY in tag_data.py implies parent tags during auto-tagging only.
- **Genre system:** 4 genres — Avatar Base, Outfit & Acce, Gimmick, Tools. Auto-detected on import via suggest_genre() using keyword matching + tag analysis + filetype heuristics. User can override via combobox in inspector.

## Key Conventions
- Python 3.11+ with `from __future__ import annotations`
- PySide6 — use `Qt.AlignCenter` (not `Qt.AlignmentFlag.AlignCenter`), `Qt.ElideLeft` (not `Qt.TextElideMode.ElideStart`)
- Paths are `pathlib.Path`, never strings for file paths
- Signals use `Signal(type, type)` syntax — keep payloads small
- QSettings accessed via `QSettings()` (no-arg, uses app-wide org/app name) — org: "BoothOrganizer", app: "BoothOrganizer"
- SQL queries use parameterized `?` placeholders — never f-string interpolation except for `ORDER BY` (whitelist-validated)
- All DB writes go through `self._db.write_connection()` (acquires mutex); reads use `self._db.connection()` (thread-local)
- Worker threads: extend `BaseWorker`, override `_run()`, emit `signals.finished` / `signals.error` / `signals.progress`
- UI components get a `Queries` reference — no service layer

## Files of Note
| File | Role |
|------|------|
| `main_window.py` | Central hub — wires signals, hosts toolbar/menus/splitter/dock |
| `thumbnail_grid.py` | Model + delegate + view — OR/AND tag filter, lazy loading, center justification |
| `inspector.py` | Detail panel — thumbnail, genre combo, avatar chips, tag chips, contents tree, notes, tools |
| `sidebar.py` | Left panel — genre buttons, avatar picker, body map, AND tag tree, clear/manage buttons |
| `body_map.py` | Custom-painted avatar silhouette — hit-testing via QPainterPath, 2:3 aspect ratio |
| `queries.py` | All SQL — asset CRUD, tag CRUD, OR/AND filtering, search, settings, migration |
| `importer.py` | Async import worker — archive extraction, scan dispatch, thumbnail save, auto-tagging, genre classification |
| `auto_tagger.py` | Tokenizer + WORD_TO_TAG matching + avatar detection + Japanese name resolution + genre classification |
| `tag_data.py` | Dictionaries — TOP_AVATARS (popularity-ordered), WORD_TO_TAG (455 entries), JP_TO_EN, JP_AVATAR_TO_EN, TAG_HIERARCHY |
| `launcher.py` | subprocess.Popen for Blender/Unity/Photoshop with Unity multi-instance support |
| `registry.py` | ToolConfig dataclass + QSettings persistence + DEFAULT_TOOL_MAP |
| `theme.py` | QPalette builder + DWM dark title bar |
| `schema.py` | DDL statements + init_schema() version gate |
| `connection.py` | DatabaseManager — WAL, busy_timeout, foreign_keys, thread-local pool |
| `orchestrator.py` | scan_file() — dispatches to type-specific scanner based on extension |
| `thumb_worker.py` | Background thumbnail generation for pending assets (uses shared _thumb_score from booth_zip) |
| `drop_overlay.py` | Semi-transparent drag-and-drop overlay with "Drop files here" text |
| `unity_windows.py` | Win32 API via ctypes — detects running Unity Editor windows by window class name |
| `chip_button.py` | ChipToggleButton — exclusive_group for radio-button behavior, used by sidebar + inspector |
| `flow_layout.py` | FlowLayout — CSS-flexbox-like layout for chip containers, handles widget reparenting |
| `cover_trainer.py` | Training jig dialog — shows all images in an asset, user clicks the best cover for ML training |
| `rarfile_scanner.py` | RAR archive scanner — uses rarfile library, requires unrar utility on system PATH |

## What NOT to Do
- Don't add `logging` module without discussing — we use `logger = logging.getLogger(__name__)` only in `importer.py` currently
- Don't add a service/repository/DAO layer — `Queries` is the data layer
- Don't add parent/child relationships to the tag DB schema — tags are flat; hierarchy implications are handled in auto_tagger.py
- Don't add async/await — the app uses QThreadPool + signals
- Don't make the body-map segments user-configurable — they're hardcoded to the 10 standard avatar body parts
- Don't port to macOS/Linux — the app uses Windows-specific APIs (DWM, os.startfile, explorer)
- Don't add confirmation dialogs for tag add/remove — only destructive operations (delete) need confirmation
- Don't add startup wizards or onboarding tours — the empty-state placeholder text is sufficient for v0.1
- Don't use `os.system()` for shell commands — use `subprocess.run()` with list arguments

## Running
```bash
cd BoothOrganizer
PYTHONPATH="." python booth_organizer/main.py
```
