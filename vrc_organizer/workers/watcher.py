from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, QTimer, QFileSystemWatcher
from PySide6.QtWidgets import QApplication


class LibraryWatcher(QObject):
    new_files_detected = Signal(list)  # list of new file paths

    def __init__(self, queries, parent=None):
        super().__init__(parent)
        self._queries = queries
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(2000)
        self._debounce_timer.timeout.connect(self._scan)
        self._paths: set[str] = set()
        self._known_files: dict[str, float] = {}  # path -> mod_time

    def add_watch_dir(self, directory: Path):
        d = str(directory)
        if d not in self._watcher.directories():
            self._watcher.addPath(d)
            self._paths.add(d)
            # Seed known files
            for f in directory.rglob("*"):
                if f.is_file():
                    self._known_files[str(f)] = f.stat().st_mtime

    def remove_watch_dir(self, directory: Path):
        d = str(directory)
        if d in self._watcher.directories():
            self._watcher.removePath(d)
        self._paths.discard(d)

    def watched_dirs(self) -> list[str]:
        return list(self._paths)

    def _on_dir_changed(self, path: str):
        self._debounce_timer.start()

    def _scan(self):
        new_files = []
        for watch_dir in self._paths:
            dir_path = Path(watch_dir)
            if not dir_path.exists():
                continue
            for f in dir_path.rglob("*"):
                if not f.is_file():
                    continue
                fstr = str(f)
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                if fstr not in self._known_files:
                    new_files.append(fstr)
                    self._known_files[fstr] = mtime
                elif self._known_files[fstr] != mtime:
                    new_files.append(fstr)
                    self._known_files[fstr] = mtime

        if new_files:
            self.new_files_detected.emit(new_files)
