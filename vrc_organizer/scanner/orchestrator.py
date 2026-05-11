from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ScanReport:
    filetype: str
    thumbnail_source: Optional[bytes] = None
    contents: list[tuple[str, str, int]] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


def scan_file(filepath: Path) -> ScanReport:
    suffix = filepath.suffix.lower()

    if suffix in (".png", ".jpg", ".jpeg", ".webp", ".psd"):
        from vrc_organizer.scanner.imagefile import scan_image
        return scan_image(filepath)

    if suffix in (".fbx", ".obj"):
        from vrc_organizer.scanner.fbx_obj import scan_fbx_obj
        return scan_fbx_obj(filepath)

    if suffix == ".blend":
        from vrc_organizer.scanner.blendfile import scan_blend
        return scan_blend(filepath)

    if suffix == ".unitypackage":
        from vrc_organizer.scanner.unitypackage import scan_unitypackage
        return scan_unitypackage(filepath)

    if suffix == ".zip":
        from vrc_organizer.scanner.booth_zip import scan_booth_zip
        return scan_booth_zip(filepath)

    if suffix == ".rar":
        from vrc_organizer.scanner.rarfile_scanner import scan_rar
        return scan_rar(filepath)

    from vrc_organizer.scanner.generic import scan_generic
    return scan_generic(filepath)
