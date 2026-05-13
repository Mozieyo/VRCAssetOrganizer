from __future__ import annotations

import io
import tarfile
from pathlib import Path

from PIL import Image

from vrc_organizer.scanner.orchestrator import ScanReport
from vrc_organizer.scanner.generic import classify

THUMB_SIZE = (256, 256)
MAX_THUMB_SOURCE = 16 * 1024 * 1024


def _read_guid_pathnames(tf: tarfile.TarFile) -> dict[str, str]:
    """Read all pathname files to build GUID → human-readable path map."""
    guid_names: dict[str, str] = {}
    for member in tf.getmembers():
        if not member.isfile():
            continue
        name = member.name
        if name.endswith("/pathname"):
            guid_dir = name.split("/", 1)[0] if "/" in name else ""
            if guid_dir:
                try:
                    f = tf.extractfile(member)
                    if f:
                        original_path = f.read().decode("utf-8", errors="replace").strip()
                        guid_names[guid_dir] = original_path
                except Exception:
                    pass
    return guid_names


def _resolve_display_name(raw_name: str, guid_names: dict[str, str]) -> str | None:
    """Resolve a tar entry name to a human-readable path. Returns None for noise files.

    In unitypackages, content is stored as GUID/asset where:
    - GUID/pathname contains the original Unity path (e.g., "Assets/Textures/Skin.png")
    - GUID/asset contains the actual file content
    - GUID/asset.meta contains Unity metadata
    - GUID/preview.png is an optional preview (NOT the main asset)
    """
    base = raw_name.rsplit("/", 1)[-1] if "/" in raw_name else raw_name
    guid = raw_name.split("/", 1)[0] if "/" in raw_name else ""

    # Only process files named "asset" - these are the actual content
    # Skip everything else: pathname, .meta, preview.png, etc.
    if base != "asset":
        return None

    # Use pathname as display name
    if guid in guid_names:
        return guid_names[guid]

    return None  # No pathname found, can't determine real name


def scan_unitypackage(filepath: Path) -> ScanReport:
    try:
        tf = tarfile.open(filepath, "r:gz")
    except (tarfile.ReadError, OSError):
        return ScanReport(filetype="unitypackage", thumbnail_source=None, contents=[], metadata={})

    contents: list[tuple[str, str, int]] = []
    best_thumb_score = 0
    best_thumb_data: bytes | None = None

    try:
        guid_names = _read_guid_pathnames(tf)
        members = list(tf.getmembers())

        for member in members:
            if not member.isfile():
                continue

            name = member.name
            size = member.size

            # Thumbnail: check raw name before filtering non-asset entries.
            # preview.png and similar are good thumbnail candidates even
            # though _resolve_display_name returns None for them.
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and size < MAX_THUMB_SOURCE:
                score = _thumb_score(name, size)
                if score > best_thumb_score:
                    best_thumb_score = score
                    try:
                        f = tf.extractfile(member)
                        if f:
                            raw = f.read()
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

            display_name = _resolve_display_name(name, guid_names)
            if display_name is None:
                continue

            # Classify based on resolved display name, not raw tar member name
            entry_type, _ = classify(Path(display_name))
            contents.append((display_name, entry_type, size))

            # Thumbnail selection - check resolved display name too
            if display_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and size < MAX_THUMB_SOURCE:
                score = _thumb_score(display_name, size)
                if score > best_thumb_score:
                    best_thumb_score = score
                    try:
                        f = tf.extractfile(member)
                        if f:
                            raw = f.read()
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
    finally:
        tf.close()

    return ScanReport(
        filetype="unitypackage",
        thumbnail_source=best_thumb_data,
        contents=contents,
        metadata={
            "entry_count": str(len(contents)),
            "guid_entries": str(len(guid_names)),
        },
    )


def _thumb_score(name: str, size: int) -> int:
    """Score an image for thumbnail selection. Higher = better candidate."""
    score = 0
    name_lower = name.lower()
    base = name_lower.rsplit("/", 1)[-1] if "/" in name_lower else name_lower

    # Strong signals
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

    # Prefer larger images (up to +40 for 4MB+)
    score += min(size // (100 * 1024), 40)

    # Prefer root/near-root
    depth = name.count("/")
    if depth <= 1:
        score += 15
    elif depth <= 2:
        score += 5

    # Textures folder boost
    parts = name_lower.replace("\\", "/").split("/")
    for p in parts[:-1]:
        if p in ("textures", "texture", "tex"):
            score += 10
            break

    return score
