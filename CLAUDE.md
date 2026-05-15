# CLAUDE.md

## Project
VRC Asset Organizer is a Windows desktop app for VRchat avatar creators to organize, tag, search, and browse 3D asset files downloaded from online marketplaces. Built with PySide6 + SQLite.

## Scope
This is a **single-user desktop tool** — not a server, not a service, not multi-tenant. The target user is a VRchat avatar creator who downloads 10–500+ asset packs from online marketplaces and needs to organize them across multiple Unity projects. The app is a file-organizer with deep format introspection, **not** a Unity replacement and **not** a 3D viewer.

## Expected User Experience
- **Import (in-place):** Drag .zip / .unitypackage / .blend / .fbx / .psd / image files onto the window. The app indexes them where they sit — source files are NOT moved or copied. Metadata, scan results, and thumbnails live in `%LOCALAPPDATA%\VrcAssetOrganizer\`. Archives are read entry-by-entry; `_maybe_extract` exists for legacy support but is no longer called.
- **Browse:** A custom-painted thumbnail grid (not QListView) with rubber-band drag-select. Density slider (1–10) maps to a minimum card width 80–360 px; cards stretch to fill the row. Japanese titles get an optional romaji line.
- **Filter:** Left sidebar has five mutually-exclusive genre chips (Avatar Base, Outfit, Accessory, Gimmick, Tools), an avatar picker (searchable, popularity-ordered, hidden when count==0 unless filtered), a collapsible body-map widget (collapsed by default), and a tag chip cloud (capped at 200 visible by default — search to reveal the rest). **Body map + genre + avatar use OR.** **Tag chips use AND.** Search supports FTS5 prefix matching AND a Python-side romaji match (typing "manuka" finds マヌカ.zip). "Search All" bypasses filters.
- **Inspect:** Right dock shows a small (56×56) header thumbnail next to the filename + optional romaji line. Contents tree at the top (full filenames, hover for path tooltip, filetype glyphs, type-filter chip row). Then Info, Genre (one-of-five chip group), Avatar (search-and-add), Tags (search field + suggestion chips, no QScrollArea wrapper). A square "Import to Unity" button hands off to Unity Editor via shell.
- **Act:** Right-click an asset → Reveal, Open Containing Folder, Open With (filetype-aware), Add Tag, Re-scan, **Remove from Library** (DB-only), **Delete File from Disk…** (Recycle Bin + DB). Delete key bulk-removes (DB-only).
- **Configure:** `Preferences` for Dark Mode, Unity Editor path, Assets Storage Path. `View → Show Romaji` toggles furigana. Grid density + inspector dock width persist in QSettings across restarts.
- **Train:** `Tools → Crawl Folder for Training Signals` walks any directory, opens .zip + .unitypackage to mine pathnames + readme tokens, writes tag co-occurrence. `Tools → Export/Import Shared Tag Pool` exchanges co-occurrence data with friends (stamped with UUID; re-importing the same file is a no-op).
- **Purge:** `Tools → Purge Cache && Packages` is a DEBUG nuke. Wipes all assets, tags, labels, scan results, co-occurrence rows, the thumbnail cache, and the legacy library_dir. Source files untouched.

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
main.py → qInstallMessageHandler (drops libpng noise)
        → VrcApp (QApplication)
        → DatabaseManager (connection pool, WAL mode, thread-local connections)
        → Queries (all SQL — CRUD, search w/ romaji match, set_genre, hard_purge_all, pool import/export)
        → MainWindow
            ├── Toolbar widgets hosted INSIDE the grid panel (search left + count + slider right)
            ├── Sidebar (collapsible Body Map, genre chips, avatar search-and-add, tag search-and-add)
            ├── AssetListView (custom QScrollArea + _GridContainer + AssetCard widgets,
            │                   marquee overlay raised above cards, no QListView)
            │   ├── AssetListModel (lazy PAGE_SIZE=100, OR/AND tag filter, romaji-aware count)
            │   └── AssetCard (manually painted: thumb, filename, optional romaji line)
            ├── InspectorPanel (contents tree at top, info form, genre chips,
            │                    avatar search, tags search+suggestions, Unity-import button)
            ├── AssetContextMenu (right-click: Remove from Library + Delete File from Disk…)
            ├── ToolRegistry / ToolLauncher
            └── training_crawler.crawl_directory (Tools → Crawl Folder for Training Signals)
```
- **Database:** SQLite WAL, single writer (mutex), multi-reader. FTS5 contentless table over `assets(filename, notes)`. `imported_pools` table dedupes pool imports by export_id UUID.
- **Import flow:** Drag → MainWindow._on_files_dropped → ImportWorker (QRunnable) → scan_file (reads archive in place, no extraction) → DB insert → ImportWorker._save_thumbnail → auto_tagger.suggest_tags + suggest_genre → model.refresh + sidebar.refresh + grid.select_asset_ids.
- **Filter flow:** Sidebar.tag_filter_changed(or_ids, and_ids) → MainWindow._on_tag_filter → _apply_filters → model.set_filter.
- **Search:** FTS5 prefix match **plus** a Python pass that romanizes each filename and substring-matches the (ASCII) query. So "manuka" finds マヌカ.zip.
- **Genre system:** 5 mutually-exclusive genres — Avatar Base, Outfit, Accessory, Gimmick, Tools. `Queries.set_genre` atomically swaps the active genre row. `migrate_legacy_genres` rewrites old "Outfit & Acce" rows on startup.
- **Autotag:** WORD_TO_TAG dictionary + JP avatar map + user-created tags (every live tag becomes an alias) + CREATOR_BY_AVATAR (Mamehinata → MOCHIYAMA, etc.). Short avatar names (<5 chars) require exact token match — kills the "Ash" false-positive cascade.
- **Training crawler:** Walks any folder, opens .zip and .unitypackage archives, reads pathnames + readme files (4 KB cap, multi-encoding decode), tokenizes, writes tag_cooccurrence pairs. `Tools → Export Shared Tag Pool` emits a UUID-stamped JSON that friends merge cumulatively.
- **Purge:** `_on_purge_cache` calls `Queries.hard_purge_all` (drops assets/tags/labels/cooccurrence) plus filesystem wipe of thumbnail cache + legacy library_dir. Re-seeds defaults on next startup.

## Key Conventions
- Python 3.11+ with `from __future__ import annotations`
- PySide6 — use `Qt.AlignCenter` (not `Qt.AlignmentFlag.AlignCenter`), `Qt.ElideLeft` (not `Qt.TextElideMode.ElideStart`)
- Paths are `pathlib.Path`, never strings for file paths
- Signals use `Signal(type, type)` syntax — keep payloads small
- QSettings accessed via `QSettings()` (no-arg, uses app-wide org/app name) — org: "VrcAssetOrganizer", app: "VrcAssetOrganizer"
- SQL queries use parameterized `?` placeholders — never f-string interpolation except for `ORDER BY` (whitelist-validated)
- All DB writes go through `self._db.write_connection()` (acquires mutex); reads use `self._db.connection()` (thread-local)
- Worker threads: extend `BaseWorker`, override `_run()`, emit `signals.finished` / `signals.error` / `signals.progress`
- UI components get a `Queries` reference — no service layer
- **Imports at file top** — no inline `import` statements inside functions; move all imports to the top of the file
- **Caching for N+1 avoidance** — when iterating visible items, cache DB lookups to avoid repeated queries (see `_get_asset_cached()` in thumbnail_grid.py)
- **State sync on refresh** — when rebuilding UI state (e.g., sidebar filter chips), preserve internal state sets across the rebuild and restore chip checked state after recreation

## Files of Note
| File | Role |
|------|------|
| `main_window.py` | Central hub — wires signals, hosts toolbar/menus/splitter/dock |
| `thumbnail_grid.py` | Model + delegate + view — OR/AND tag filter, lazy loading, center justification |
| `inspector.py` | Detail panel — thumbnail, genre chips, avatar chips (searchable), tag chips (inline + creation), contents tree, notes, tools |
| `sidebar.py` | Left panel — genre buttons, avatar picker, body map, AND tag tree, clear/manage buttons |
| `body_map.py` | Custom-painted avatar silhouette — hit-testing via QPainterPath, 2:3 aspect ratio |
| `queries.py` | All SQL — asset CRUD, tag CRUD, OR/AND filtering, search, settings, migration |
| `importer.py` | Async import worker — archive extraction, scan dispatch, thumbnail save, auto-tagging, genre classification |
| `auto_tagger.py` | Tokenizer + WORD_TO_TAG matching + avatar detection (short names: exact token only) + creator inference via CREATOR_BY_AVATAR + live-tag aliasing |
| `tag_data.py` | Dictionaries — TOP_AVATARS, WORD_TO_TAG (techno-givens like physbone/liltoon removed), JP_AVATAR_TO_EN, TAG_HIERARCHY, CREATOR_BY_AVATAR, GENRE_NAMES (5), LEGACY_GENRE_REMAP |
| `romaji.py` | Hepburn kana → romaji transliterator. Used for furigana display + search match. Kanji passes through unchanged. |
| `tools/training_crawler.py` | Walks a folder, opens .zip/.unitypackage to read pathnames + readme files, writes tag co-occurrence. |
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
| `cover_labeler.py` | Captcha-style dialog for cover selection — shows up to 6 images, keyboard shortcuts 1-6, captures image metadata (dimensions, depth, filename score) for ML training; accepts thumb_cache_dir for cached thumbnail fallback when archive extraction yields no images |
| `tag_labeler.py` | Captcha-style dialog for tag review — genre radio buttons, toggleable tag chips, suggestions from co-occurrence, search popup, session-based labeling with timing for ML training |
| `rarfile_scanner.py` | RAR archive scanner — uses rarfile library, requires unrar utility on system PATH |
| `watcher.py` | LibraryWatcher — QFileSystemWatcher for auto-import (NOT YET WIRED UP — planned feature) |

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
- Don't use inline imports inside functions — move all imports to file top for clarity and consistency
- Don't call `get_asset()` in a loop without caching — use a dict cache to avoid N+1 queries
- Don't rebuild UI chip widgets without clearing the corresponding state sets (e.g., `_genre_ids`, `_avatar_ids`)

## Running
```bash
cd BoothOrganizer
PYTHONPATH="." python vrc_organizer/main.py
```

## Building the .exe
```bash
pyinstaller VrcAssetOrganizer.spec
# → dist/VrcAssetOrganizer/VrcAssetOrganizer.exe
```
