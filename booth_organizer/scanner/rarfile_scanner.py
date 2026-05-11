from __future__ import annotations

import io
from pathlib import Path

import rarfile
from PIL import Image

from booth_organizer.scanner.orchestrator import ScanReport
from booth_organizer.scanner.generic import classify
from booth_organizer.scanner.booth_zip import _thumb_score, _is_image

THUMB_SIZE = (256, 256)
MAX_THUMB_SOURCE = 16 * 1024 * 1024


def scan_rar(filepath: Path) -> ScanReport:
    try:
        rf = rarfile.RarFile(filepath)
    except (rarfile.BadRarFile, OSError):
        return ScanReport(filetype="booth_zip", thumbnail_source=None, contents=[], metadata={})

    contents: list[tuple[str, str, int]] = []
    best_thumb_score = 0
    best_thumb_data: bytes | None = None

    try:
        for info in rf.infolist():
            if info.isdir():
                continue
            name = info.filename
            size = info.file_size

            entry_type, _ = classify(Path(name))
            contents.append((name, entry_type, size))

            if _is_image(name) and size < MAX_THUMB_SOURCE:
                score = _thumb_score(name, size)
                if score > best_thumb_score:
                    best_thumb_score = score
                    try:
                        raw = rf.read(name)
                        img = Image.open(io.BytesIO(raw))
                        if img.mode in ("RGBA", "P"):
                            img = img.convert("RGBA")
                        else:
                            img = img.convert("RGB")
                        img.thumbnail(THUMB_SIZE, Image.LANCZOS)
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        best_thumb_data = buf.getvalue()
                    except Exception:
                        pass
    except Exception:
        pass

    return ScanReport(
        filetype="booth_zip",
        thumbnail_source=best_thumb_data,
        contents=contents,
        metadata={
            "entry_count": str(len(contents)),
            "format": "rar",
        },
    )
