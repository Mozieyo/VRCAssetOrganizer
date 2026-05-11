"""Build orchestrator for VRC Asset Organizer.

Usage:
    python build.py              # onedir (release)
    python build.py --clean      # clean + rebuild
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
MAIN = "vrc_organizer/main.py"

HIDDEN_IMPORTS = [
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    "PIL._webp", "PIL._imagingft", "PIL.PsdImagePlugin",
    "sqlite3", "tarfile", "zipfile", "ctypes",
    "rarfile", "send2trash",
]

# Standard library / misc exclusions (unused modules shipped by default)
EXCLUDES = ["tkinter", "unittest", "email", "http", "xml", "pydoc", "pdb"]

# Aggressive Qt module exclusions — shaves ~60-90 MB from the bundle.
# Every excluded module is also one fewer DLL loaded into RAM at runtime.
QT_EXCLUDES = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngine",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtXml",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtPrintSupport",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtStateMachine",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtTextToSpeech",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtConcurrent",
    "PySide6.QtDBus",
]


def clean():
    for d in (DIST, BUILD):
        if d.exists():
            shutil.rmtree(d)
    for f in ROOT.glob("*.pyc"):
        f.unlink()
    print("Cleaned build artifacts.")


def _qt_plugin_path(subdir: str) -> str:
    from PySide6 import QtCore
    plugins = Path(QtCore.__file__).parent / "plugins" / subdir
    return str(plugins)


# DLLs to strip post-build — not needed by a pure QtWidgets app.
# Combined savings: ~50 MB disk + equivalent RAM not loaded at runtime.
BLOAT_DLLS = [
    "opengl32sw.dll",        # software OpenGL fallback (19.7 MB)
    "Qt6Quick*",             # QML engine (~12 MB)
    "Qt6Qml*",               # QML engine
    "Qt6Pdf*",               # PDF module (4.4 MB)
    "Qt6OpenGL*",            # OpenGL module (1.9 MB)
    "Qt6Network*",           # Network module (1.7 MB)
    "Qt6Svg*",               # SVG module (0.6 MB)
    "Qt6WebEngine*",         # Chromium
    "Qt6WebChannel*",        # WebChannel
    "Qt6WebSockets*",        # WebSockets
    "Qt6Multimedia*",        # Multimedia
    "Qt6Designer*",          # UI designer
    "Qt6Help*",              # Help system
    "Qt63D*",                # 3D modules
    "Qt6Charts*",            # Charts
    "Qt6DataVisualization*", # Data visualization
    "Qt6Sensors*",           # Sensors
    "Qt6Serial*",            # Serial port/bus
    "Qt6Bluetooth*",         # Bluetooth
    "Qt6Nfc*",               # NFC
    "Qt6Positioning*",       # Positioning
    "Qt6Location*",          # Location
    "Qt6TextToSpeech*",      # Text to speech
    "Qt6VirtualKeyboard*",   # Virtual keyboard
    "Qt6Concurrent*",        # Concurrent
    "Qt6DBus*",              # D-Bus
    "Qt6Test*",              # Test
    "Qt6Xml*",               # XML
    "Qt6Sql*",               # SQL
    "Qt6StateMachine*",      # State machine
    "_avif*",                # AVIF codec (7.5 MB)
    "libcrypto-3*",          # OpenSSL (5.0 MB — only needed by QtNetwork)
    "libssl-3*",             # OpenSSL
    "qdirect2d*",            # experimental Direct2D platform plugin (1 MB)
    "qminimal*",             # minimal platform plugin
    "qoffscreen*",           # offscreen platform plugin
]

PLUGIN_DIRS = ("platforms", "imageformats", "styles")


def strip_bloat(dist_dir: Path) -> int:
    """Remove unused DLLs from the built bundle. Returns bytes saved."""
    saved = 0
    for pattern in BLOAT_DLLS:
        for f in dist_dir.rglob(pattern):
            if f.is_file():
                saved += f.stat().st_size
                f.unlink()
                print(f"  Stripped: {f.relative_to(dist_dir)}")
    return saved


def build():
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=VrcAssetOrganizer",
        "--noconsole",
        "--onedir",
        "--noconfirm",
        "--clean",
    ]
    for hi in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", hi]
    for ex in EXCLUDES + QT_EXCLUDES:
        cmd += ["--exclude-module", ex]
    for sub in PLUGIN_DIRS:
        cmd += ["--add-data", f"{_qt_plugin_path(sub)};PySide6/plugins/{sub}"]
    cmd.append(MAIN)

    print(f"Running PyInstaller...")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("Build failed.", file=sys.stderr)
        sys.exit(result.returncode)

    # Post-build: strip unused Qt DLLs to shrink bundle and RAM footprint
    bundle_dir = DIST / "VrcAssetOrganizer"
    saved = strip_bloat(bundle_dir)
    saved_mb = saved / (1024 * 1024)

    exe = bundle_dir / "VrcAssetOrganizer.exe"
    if exe.exists():
        size_mb = exe.stat().st_size / (1024 * 1024)
        total_mb = sum(
            f.stat().st_size for f in bundle_dir.rglob("*") if f.is_file()
        ) / (1024 * 1024)
        print(f"Build successful: {exe} ({size_mb:.1f} MB exe, {total_mb:.1f} MB bundle, {saved_mb:.1f} MB stripped)")
    else:
        print(f"Build may have failed: {exe} not found.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build VRC Asset Organizer")
    parser.add_argument("--clean", action="store_true", help="Clean before build")
    args = parser.parse_args()

    if args.clean:
        clean()

    build()
