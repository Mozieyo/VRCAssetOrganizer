from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from booth_organizer.scanner.orchestrator import ScanReport

THUMB_SIZE = (256, 256)


def scan_image(filepath: Path) -> ScanReport:
    try:
        img = Image.open(filepath)
        img = img.copy()  # avoid EXIF orientation issues

        if img.mode in ("RGBA", "P"):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")

        img.thumbnail(THUMB_SIZE, Image.LANCZOS)

        buf = io.BytesIO()
        fmt = "PNG"
        img.save(buf, format=fmt)
        thumb_data = buf.getvalue()

        return ScanReport(
            filetype="image",
            thumbnail_source=thumb_data,
            contents=[],
            metadata={
                "width": str(img.width),
                "height": str(img.height),
                "format": img.format or "unknown",
            },
        )
    except Exception:
        return ScanReport(filetype="image", thumbnail_source=None, contents=[], metadata={})
