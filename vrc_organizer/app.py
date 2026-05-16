from __future__ import annotations

import ctypes
from pathlib import Path
from PySide6.QtCore import QSharedMemory, QSettings
from PySide6.QtGui import QPixmapCache
from PySide6.QtWidgets import QApplication
import sys


def ensure_single_instance() -> QSharedMemory | None:
    """Check single-instance using a Win32 named mutex (auto-cleaned on exit/crash)."""
    kernel32 = ctypes.windll.kernel32
    mutex_name = "Global\\VrcAssetOrganizerSingleInstance"
    mutex = kernel32.CreateMutexW(None, False, mutex_name)

    ERROR_ALREADY_EXISTS = 183
    if mutex == 0 or kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if mutex:
            kernel32.CloseHandle(mutex)
        return None

    shared = QSharedMemory("VrcAssetOrganizerSingleInstance")
    shared.create(1)
    shared.setObjectName(f"mutex:{mutex}")
    return shared


def get_app_data_dir() -> Path:
    base = Path.home() / "AppData" / "Local" / "VrcAssetOrganizer"
    base.mkdir(parents=True, exist_ok=True)
    return base


def resolve_db_path() -> Path:
    """Return the DB path, preferring the assets storage directory.

    When the user sets an assets storage path and the DB lives alongside
    the extracted assets, it survives app reinstall — just re-point to the
    same folder and all metadata is intact.
    """
    s = QSettings("VrcAssetOrganizer", "VrcAssetOrganizer")
    saved = s.value("db_path", "")
    if saved:
        p = Path(saved)
        if p.exists():
            return p
    return get_app_data_dir() / "vrc_assets.db"


def _save_db_path(path: Path):
    """Persist the DB path to registry so future launches find it."""
    s = QSettings("VrcAssetOrganizer", "VrcAssetOrganizer")
    s.setValue("db_path", str(path))


class VrcApp(QApplication):
    def __init__(self, argv: list[str]):
        super().__init__(argv)
        self.setOrganizationName("VrcAssetOrganizer")
        self.setApplicationName("VrcAssetOrganizer")
        self.setApplicationVersion("0.1.1-alpha-hotfix")

        self.app_data_dir = get_app_data_dir()
        self.db_path = resolve_db_path()
        self.thumb_cache_dir = self.app_data_dir / "thumbnails"
        self.thumb_cache_dir.mkdir(exist_ok=True)

        # Cap Qt's global pixmap cache (default is 10 MB). The grid model
        # has its own per-asset cache and the only other heavy pixmap use
        # is the inspector thumbnail. 4 MB is enough.
        QPixmapCache.setCacheLimit(4 * 1024)

        self._shared_mem = ensure_single_instance()

    @property
    def is_single_instance(self) -> bool:
        return self._shared_mem is not None
