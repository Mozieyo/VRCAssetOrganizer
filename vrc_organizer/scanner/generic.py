from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from vrc_organizer.scanner.orchestrator import ScanReport


# File extension to display-name mapping
TYPE_NAMES: dict[str, tuple[str, str]] = {
    ".unitypackage": ("unitypackage", "Unity Package"),
    ".blend": ("blend", "Blender File"),
    ".fbx": ("fbx", "FBX Model"),
    ".obj": ("obj", "OBJ Model"),
    ".prefab": ("prefab", "Unity Prefab"),
    ".mat": ("mat", "Material"),
    ".png": ("image", "PNG Image"),
    ".jpg": ("image", "JPEG Image"),
    ".jpeg": ("image", "JPEG Image"),
    ".webp": ("image", "WebP Image"),
    ".psd": ("image", "Photoshop Document"),
    ".tga": ("image", "TGA Image"),
    ".shader": ("shader", "Shader"),
    ".anim": ("animation", "Animation"),
    ".zip": ("booth_zip", "Asset Pack"),
    ".7z": ("booth_zip", "Asset Pack"),
    ".rar": ("booth_zip", "Asset Pack"),
}


def classify(filepath: Path) -> tuple[str, str]:
    """Return (filetype_key, display_name) for a given file path."""
    suffix = filepath.suffix.lower()
    if suffix in TYPE_NAMES:
        return TYPE_NAMES[suffix]
    return ("other", f"{suffix.upper()[1:]} File" if suffix else "Unknown")


def scan_generic(filepath: Path) -> ScanReport:
    ft_key, _ = classify(filepath)
    return ScanReport(filetype=ft_key, thumbnail_source=None, contents=[], metadata={})
