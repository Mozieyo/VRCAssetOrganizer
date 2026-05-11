"""Find running Unity Editor windows on Windows."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010


def find_unity_editors() -> list[tuple[int, str]]:
    """Return list of (hwnd, window_title) for running Unity Editor windows.
    Unity Editor windows have class name 'UnityWndClass'.
    Window titles include the project name and Unity version.
    """
    results: list[tuple[int, str]] = []

    def enum_callback(hwnd: int, _lparam: int) -> int:
        # Check class name
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, buf, 256)
        class_name = buf.value

        if class_name != "UnityWndClass":
            return 1  # Continue enumeration

        # Get window title
        _user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value

        # Skip windows without titles (splash screens, etc.)
        if not title:
            return 1

        results.append((hwnd, title))
        return 1  # Continue

    # EnumWindows callback type
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_int)
    _user32.EnumWindows(WNDENUMPROC(enum_callback), 0)

    return results


def find_unity_project_path(hwnd: int) -> str | None:
    """Try to extract the Unity project path from a window handle.
    Uses the window title which typically ends with ' - ProjectName - Unity <version>'.
    """
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, buf, 512)
    title = buf.value
    # Title format: "SceneName - ProjectName - Unity 2022.3.xf1"
    parts = title.split(" - ")
    if len(parts) >= 2:
        return parts[-2]  # ProjectName
    return None


def bring_to_front(hwnd: int):
    """Bring a Unity Editor window to the foreground."""
    _user32.SetForegroundWindow(hwnd)
    _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
