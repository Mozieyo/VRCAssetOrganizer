from __future__ import annotations

import io
import tarfile
import zipfile
from collections import Counter
from pathlib import Path

from PIL import Image

from vrc_organizer.scanner.orchestrator import ScanReport
from vrc_organizer.scanner.generic import classify
from vrc_organizer.scanner.unitypackage import (
    _read_guid_pathnames, _resolve_display_name, _thumb_score as _up_thumb_score,
    _extract_creator_product, _extract_readme_content, _parse_readme_avatars,
)
from vrc_organizer.tag_data import TOP_AVATARS

THUMB_SIZE = (256, 256)
MAX_THUMB_SOURCE = 16 * 1024 * 1024


def scan_booth_zip(filepath: Path) -> ScanReport:
    try:
        zf = zipfile.ZipFile(filepath, "r")
    except (zipfile.BadZipFile, OSError):
        return ScanReport(filetype="booth_zip", thumbnail_source=None, contents=[], metadata={})

    with zf:
        contents, thumb_data, nested_metadata = _scan_zip_recursive(zf, max_depth=4)

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
            "creator": nested_metadata.get("creator", ""),
            "product": nested_metadata.get("product", ""),
            "readme_avatars": nested_metadata.get("readme_avatars", ""),
        },
    )


def _scan_zip_recursive(zf: zipfile.ZipFile, max_depth: int, prefix: str = ""
                        ) -> tuple[list[tuple[str, str, int]], bytes | None, dict[str, str]]:
    """Recursively scan a ZipFile, collecting all contents, best thumbnail, and metadata."""
    contents: list[tuple[str, str, int]] = []
    best_thumb_score = 0
    best_thumb_data: bytes | None = None
    # Aggregate metadata from nested unitypackages
    creators: list[str] = []
    products: list[str] = []
    readme_avatars: set[str] = set()

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
                    nc, nt, nm = _scan_unitypackage_data(up_data, max_depth - 1, name)
                    contents.extend(nc)
                    if nt and best_thumb_score == 0:
                        best_thumb_data = nt
                    # Aggregate metadata
                    if nm.get("creator"):
                        creators.append(nm["creator"])
                    if nm.get("product"):
                        products.append(nm["product"])
                    if nm.get("readme_avatars"):
                        readme_avatars.update(nm["readme_avatars"].split(","))
                except Exception:
                    pass
            elif low.endswith(".zip") and size < 200 * 1024 * 1024:
                try:
                    inner_data = zf.read(info)
                    with zipfile.ZipFile(io.BytesIO(inner_data), "r") as inner:
                        nc, nt, nm = _scan_zip_recursive(inner, max_depth - 1, name)
                        contents.extend(nc)
                        if nt and best_thumb_score == 0:
                            best_thumb_data = nt
                        # Aggregate metadata
                        if nm.get("creator"):
                            creators.append(nm["creator"])
                        if nm.get("product"):
                            products.append(nm["product"])
                        if nm.get("readme_avatars"):
                            readme_avatars.update(nm["readme_avatars"].split(","))
                except Exception:
                    pass

    # Use most common creator/product if multiple unitypackages
    metadata = {
        "creator": Counter(creators).most_common(1)[0][0] if creators else "",
        "product": Counter(products).most_common(1)[0][0] if products else "",
        "readme_avatars": ",".join(sorted(readme_avatars)) if readme_avatars else "",
    }

    return contents, best_thumb_data, metadata


def _scan_unitypackage_data(data: bytes, max_depth: int, prefix: str
                            ) -> tuple[list[tuple[str, str, int]], bytes | None, dict[str, str]]:
    """Scan a .unitypackage tar.gz in memory, resolving GUIDs to human names.

    Returns (contents, thumbnail_data, metadata) where metadata includes
    creator, product, and readme_avatars extracted from the package.
    """
    contents: list[tuple[str, str, int]] = []
    best_thumb_score = 0
    best_thumb_data: bytes | None = None
    metadata: dict[str, str] = {}

    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except Exception:
        return contents, None, metadata

    try:
        guid_names = _read_guid_pathnames(tf)

        # Extract creator/product from pathnames
        all_pathnames = list(guid_names.values())
        creator, product = _extract_creator_product(all_pathnames)
        product_name = product or ""

        # Extract readme content and parse for avatar names
        readme_content = _extract_readme_content(tf, guid_names)
        readme_avatars: list[str] = []
        if readme_content:
            readme_avatars = _parse_readme_avatars(readme_content, TOP_AVATARS)

        metadata = {
            "creator": creator or "",
            "product": product or "",
            "readme_avatars": ",".join(readme_avatars) if readme_avatars else "",
        }

        for member in tf.getmembers():
            if not member.isfile():
                continue
            mname = member.name
            msize = member.size

            # Thumbnail: check raw name before filtering non-asset entries
            if _is_image(mname) and msize < MAX_THUMB_SOURCE:
                score = _up_thumb_score(mname, msize, product_name)
                if score > best_thumb_score:
                    best_thumb_score = score
                    try:
                        f = tf.extractfile(member)
                        if f:
                            best_thumb_data = _make_thumb(f.read())
                    except Exception:
                        pass

            display_name = _resolve_display_name(mname, guid_names)
            if display_name is None:
                continue
            full_name = f"{prefix}/{display_name}"

            # Classify based on resolved display name, not raw tar member name
            entry_type, _ = classify(Path(display_name))
            contents.append((full_name, entry_type, msize))

            if _is_image(display_name) and msize < MAX_THUMB_SOURCE:
                score = _up_thumb_score(display_name, msize, product_name)
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

    return contents, best_thumb_data, metadata


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


def _thumb_score(name: str, size: int, product_name: str = "") -> int:
    """Score an image for thumbnail selection. Higher = better candidate.

    Priority order:
    1. Explicit marketing images (main.png, cover, preview, numbered)
    2. Body/face/skin textures (distorted but shows the product)
    3. Product-named textures
    4. Generic textures
    5. Avoid: normal maps, masks, UV maps, icons
    """
    score = 0
    name_lower = name.lower()
    base = name_lower.rsplit("/", 1)[-1] if "/" in name_lower else name_lower

    # === NEGATIVE SIGNALS (check first to early-reject bad candidates) ===

    # Technical texture maps - never use as thumbnail
    if any(x in name_lower for x in (
        "_n.", "_normal", "_nrm", "_norm",
        "_mask", "_alpha", "_ao", "_ambient",
        "_metallic", "_roughness", "_specular", "_gloss",
        "_emission", "_emissive", "_height", "_displacement",
        "_bump", "_detail", "_noise",
    )):
        return -100  # Hard reject

    # UV maps
    if "uv" in base and "uvs" not in base:
        return -80

    # Tiny icons
    if "icon" in name_lower and size < 100_000:
        return -50

    # === POSITIVE SIGNALS ===

    # Tier 1: Explicit marketing images
    if base in ("main.png", "main.jpg", "main.jpeg", "main.webp"):
        score += 100
    elif base.startswith("00") and base.endswith((".jpg", ".jpeg", ".png", ".webp")):
        score += 95
    elif "cover" in name_lower or "eyecatch" in name_lower or "eye_catch" in name_lower:
        score += 90
    elif base.startswith("01") and base.endswith((".jpg", ".jpeg", ".png", ".webp")):
        score += 85
    elif "top" in base and base.endswith((".png", ".jpg", ".jpeg", ".webp")):
        score += 80
    elif "preview" in name_lower or "プレビュー" in name_lower:
        score += 75
    elif "thumbnail" in name_lower or "thumb" in name_lower or "サムネ" in name_lower:
        score += 70

    # Tier 2: Body/face/skin textures (good fallback - shows the product)
    elif any(x in name_lower for x in ("body", "face", "skin", "character")):
        if "nobody" not in name_lower and "facebook" not in name_lower:
            score += 60
    elif any(x in name_lower for x in ("basecolor", "base_color", "diffuse", "albedo")):
        score += 55
    elif "_d." in name_lower or name_lower.endswith("_d.png"):
        score += 50  # Common diffuse suffix

    # Tier 3: Product-named textures
    elif product_name and product_name.lower() in name_lower:
        score += 65

    # Tier 4: Generic but usable
    elif "tex." in name_lower or base == "tex.png":
        score += 30

    # Size bonus - larger textures are usually main body textures
    if size > 1_000_000:  # > 1MB
        score += 25
    elif size > 500_000:  # > 500KB
        score += 15
    elif size > 100_000:  # > 100KB
        score += 5

    # Depth penalty/bonus - root-level images are more likely promo
    depth = name.count("/") + name.count("\\")
    if depth <= 1:
        score += 20
    elif depth <= 2:
        score += 10
    elif depth > 4:
        score -= 10

    return score


def _is_image(name: str) -> bool:
    return name.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))


