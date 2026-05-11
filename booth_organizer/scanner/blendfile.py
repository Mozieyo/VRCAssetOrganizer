from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image

from booth_organizer.scanner.orchestrator import ScanReport

THUMB_SIZE = (256, 256)
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def scan_blend(filepath: Path) -> ScanReport:
    thumb_data = _extract_rend_preview(filepath)
    return ScanReport(
        filetype="blend",
        thumbnail_source=thumb_data,
        contents=[],
        metadata={},
    )


def _extract_rend_preview(filepath: Path) -> bytes | None:
    try:
        with open(filepath, "rb") as f:
            header = f.read(12)
            if len(header) < 12 or header[:7] != b"BLENDER":
                return None

            ptr_size_char = header[7:8]
            endian_char = header[8:9]

            if ptr_size_char == b"_":
                ptr_size = 8
            elif ptr_size_char == b"-":
                ptr_size = 4
            else:
                return None

            endian = "<" if endian_char == b"v" else ">"

            while True:
                code = f.read(4)
                if len(code) < 4:
                    break
                size_bytes = f.read(4)
                if len(size_bytes) < 4:
                    break
                size = struct.unpack(endian + "I", size_bytes)[0]

                # Skip old_addr (ptr_size), sdna_index (ptr_size), count (4 bytes)
                skip = ptr_size * 2 + 4
                f.read(skip)

                if code == b"REND":
                    data = f.read(size)
                    idx = data.find(PNG_HEADER)
                    if idx >= 0:
                        iend_idx = data.find(b"IEND", idx)
                        if iend_idx >= 0:
                            png_data = data[idx:iend_idx + 8]
                            try:
                                img = Image.open(io.BytesIO(png_data))
                                img = img.convert("RGBA")
                                img.thumbnail(THUMB_SIZE, Image.LANCZOS)
                                buf = io.BytesIO()
                                img.save(buf, format="PNG")
                                return buf.getvalue()
                            except Exception:
                                return None
                else:
                    f.seek(size, 1)

    except (OSError, struct.error):
        pass

    return None
