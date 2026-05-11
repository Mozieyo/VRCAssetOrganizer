from __future__ import annotations

import ctypes
from pathlib import Path
from PySide6.QtCore import QSharedMemory
from PySide6.QtWidgets import QApplication
import sys


def ensure_single_instance() -> QSharedMemory | None:
    """Check single-instance using a Win32 named mutex (auto-cleaned on exit/crash)."""
    kernel32 = ctypes.windll.kernel32
    mutex_name = "Global\\VrcAssetOrganizerSingleInstance"
    mutex = kernel32.CreateMutexW(None, False, mutex_name)
    if mutex == 0:
        # Mutex creation failed entirely — fall back to shared memory
        shared = QSharedMemory("VrcAssetOrganizerSingleInstance")
        if shared.create(1):
            return shared
        if shared.attach():
            shared.detach()
            if shared.create(1):
                return shared
            if shared.attach():
                return shared
        return None

    ERROR_ALREADY_EXISTS = 183
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(mutex)
        return None

    # We own the mutex — store handle so it lives as long as the process
    # Return a sentinel QSharedMemory for the is_single_instance interface
    shared = QSharedMemory("VrcAssetOrganizerSingleInstance")
    shared.create(1)  # May fail silently if previous crash remnant; mutex already won
    # Store the mutex handle on the shared object so it doesn't get GC'd
    shared.setObjectName(f"mutex:{mutex}")
    return shared


def get_app_data_dir() -> Path:
    base = Path.home() / "AppData" / "Local" / "VrcAssetOrganizer"
    base.mkdir(parents=True, exist_ok=True)
    return base


class VrcApp(QApplication):
    def __init__(self, argv: list[str]):
        super().__init__(argv)
        self.setOrganizationName("VrcAssetOrganizer")
        self.setApplicationName("VrcAssetOrganizer")
        self.setApplicationVersion("0.1.0")

        self.app_data_dir = get_app_data_dir()
        self.db_path = self.app_data_dir / "vrc_assets.db"
        self.thumb_cache_dir = self.app_data_dir / "thumbnails"
        self.thumb_cache_dir.mkdir(exist_ok=True)

        self._shared_mem = ensure_single_instance()

    @property
    def is_single_instance(self) -> bool:
        return self._shared_mem is not None
