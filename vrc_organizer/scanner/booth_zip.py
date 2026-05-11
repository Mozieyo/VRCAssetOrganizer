from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from PIL import Image

from vrc_organizer.scanner.orchestrator import ScanReport
from vrc_organizer.scanner.generic import classify
from vrc_organizer.scanner.unitypackage import (
    _read_guid_pathnames, _resolve_display_name, _thumb_score as _up_thumb_score,
)

THUMB_SIZE = (256, 256)
MAX_THUMB_SOURCE = 16 * 1024 * 1024


def scan_booth_zip(filepath: Path) -> ScanReport:
    try:
        zf = zipfile.ZipFile(filepath, "r")
    except (zipfile.BadZipFile, OSError):
        return ScanReport(filetype="booth_zip", thumbnail_source=None, contents=[], metadata={})

    with zf:
        contents, thumb_data = _scan_zip_recursive(zf, max_depth=4)

    unitypackage_names = [n for n, _, _ in contents if n.lower().endswith(".unitypackage")]

    return ScanReport(
        filetype="booth_zip",
        thumbnail_source=thumb_data,
        contents=contents,
        metadata={
            "entry_count": str(len(contents)),
            "has_unitypackage": str(len(unitypackage_names) > 0),
            "unitypackage_count": str(len(unitypackage_names)),
            "unitypackage_names": ",".join(unitypackage_names),
        },
    )


def _scan_zip_recursive(zf: zipfile.ZipFile, max_depth: int, prefix: str = ""
                        ) -> tuple[list[tuple[str, str, int]], bytes | None]:
    """Recursively scan a ZipFile, collecting all contents and best thumbnail."""
    contents: list[tuple[str, str, int]] = []
    best_thumb_score = 0
    best_thumb_data: bytes | None = None

    for info in zf.infolist():
        if info.is_dir():
            continue

        name = f"{prefix}/{info.filename}" if prefix else info.filename
        size = info.file_size

        entry_type, _ = classify(Path(info.filename))
        contents.append((name, entry_type, size))

        # Thumbnail from images
        if _is_image(info.filename) and size < MAX_THUMB_SOURCE:
            score = _thumb_score(name, size)
            if score > best_thumb_score:
                best_thumb_score = score
                try:
                    raw = zf.read(info)
                    best_thumb_data = _make_thumb(raw)
                except Exception:
                    pass

        # Recurse into nested archives
        if max_depth > 1:
            low = info.filename.lower()
            if low.endswith(".unitypackage"):
                try:
                    up_data = zf.read(info)
                    nc, nt = _scan_unitypackage_data(up_data, max_depth - 1, name)
                    contents.extend(nc)
                    if nt and best_thumb_score == 0:
                        best_thumb_data = nt
                except Exception:
                    pass
            elif low.endswith(".zip") and size < 200 * 1024 * 1024:
                try:
                    inner_data = zf.read(info)
                    with zipfile.ZipFile(io.BytesIO(inner_data), "r") as inner:
                        nc, nt = _scan_zip_recursive(inner, max_depth - 1, name)
                        contents.extend(nc)
                        if nt and best_thumb_score == 0:
                            best_thumb_data = nt
                except Exception:
                    pass

    return contents, best_thumb_data


def _scan_unitypackage_data(data: bytes, max_depth: int, prefix: str
                            ) -> tuple[list[tuple[str, str, int]], bytes | None]:
    """Scan a .unitypackage tar.gz in memory, resolving GUIDs to human names."""
    contents: list[tuple[str, str, int]] = []
    best_thumb_score = 0
    best_thumb_data: bytes | None = None

    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except Exception:
        return contents, None

    try:
        guid_names = _read_guid_pathnames(tf)

        for member in tf.getmembers():
            if not member.isfile():
                continue
            mname = member.name
            msize = member.size

            display_name = _resolve_display_name(mname, guid_names)
            if display_name is None:
                continue
            full_name = f"{prefix}/{display_name}"

            entry_type, _ = classify(Path(mname))
            contents.append((full_name, entry_type, msize))

            if _is_image(mname) and msize < MAX_THUMB_SOURCE:
                score = _up_thumb_score(display_name, msize)
                if score > best_thumb_score:
                    best_thumb_score = score
                    try:
                        f = tf.extractfile(member)
                        if f:
                            best_thumb_data = _make_thumb(f.read())
                    except Exception:
                        pass

            # Recurse into nested archives inside unitypackage
            if max_depth > 1:
                low = mname.lower()
                if low.endswith((".zip", ".unitypackage")):
                    pass
    finally:
        tf.close()

    return contents, best_thumb_data


def _make_thumb(raw: bytes) -> bytes:
    """Convert raw image bytes to a thumbnail PNG."""
    img = Image.open(io.BytesIO(raw))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")
    img.thumbnail(THUMB_SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _thumb_score(name: str, size: int) -> int:
    """Score an image for thumbnail selection. Higher = better candidate."""
    score = 0
    name_lower = name.lower()
    base = name_lower.rsplit("/", 1)[-1] if "/" in name_lower else name_lower

    if base in ("main.png", "main.jpg", "main.jpeg", "main.webp"):
        score += 100
    elif base.startswith("00") and base.endswith((".jpg", ".jpeg", ".png", ".webp")):
        score += 90
    elif "cover" in name_lower or "eyecatch" in name_lower or "eye_catch" in name_lower:
        score += 85
    elif base.startswith("01") and base.endswith((".jpg", ".jpeg", ".png", ".webp")):
        score += 80
    elif "top" in base and base.endswith((".png", ".jpg", ".jpeg", ".webp")):
        score += 70
    elif "preview" in name_lower:
        score += 60
    elif "thumbnail" in name_lower or "thumb" in name_lower:
        score += 50
    elif "body" in name_lower and "nobody" not in name_lower:
        score += 30

    if "icon" in name_lower:
        score -= 20

    score += min(size // (100 * 1024), 40)

    depth = name.count("/")
    if depth <= 1:
        score += 15
    elif depth <= 2:
        score += 5

    parts = name_lower.replace("\\", "/").split("/")
    for p in parts[:-1]:
        if p in ("textures", "texture", "tex"):
            score += 10
            break
    return score


def _is_image(name: str) -> bool:
    return name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".psd"))


