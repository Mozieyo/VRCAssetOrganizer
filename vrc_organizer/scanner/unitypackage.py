from __future__ import annotations

import io
import re
import tarfile
from collections import Counter
from pathlib import Path

from PIL import Image

from vrc_organizer.scanner.orchestrator import ScanReport
from vrc_organizer.scanner.generic import classify

THUMB_SIZE = (256, 256)
MAX_THUMB_SOURCE = 16 * 1024 * 1024

# Generic folder names to skip when extracting creator/product
_GENERIC_FOLDERS = frozenset({
    "assets", "textures", "texture", "tex", "materials", "material", "mat",
    "prefabs", "prefab", "scripts", "script", "fbx", "models", "model",
    "animations", "animation", "anim", "shaders", "shader", "editor",
    "plugins", "plugin", "resources", "dependencies", "demo", "samples",
    "packages", "scenes", "scene", "sounds", "audio", "fonts", "sprites",
})


def _extract_creator_product(pathnames: list[str]) -> tuple[str | None, str | None]:
    """Extract creator and product names from Unity pathnames.

    Most packages follow: Assets/{Creator}/{Product}/...
    Returns (creator, product) or (None, None) if not detected.
    """
    creators: Counter[str] = Counter()
    products: Counter[str] = Counter()

    for path in pathnames:
        parts = path.replace("\\", "/").split("/")
        if len(parts) < 2:
            continue

        # Standard Unity structure: Assets/Creator/Product/...
        if parts[0].lower() == "assets" and len(parts) >= 3:
            creator = parts[1]
            if creator.lower() not in _GENERIC_FOLDERS and len(creator) > 1:
                creators[creator] += 1
            if len(parts) >= 4:
                product = parts[2]
                if product.lower() not in _GENERIC_FOLDERS and len(product) > 1:
                    products[product] += 1
        # Packages structure: Packages/com.creator.product/...
        elif parts[0].lower() == "packages" and len(parts) >= 2:
            pkg_name = parts[1]
            # Parse com.creator.productname format
            if pkg_name.startswith("com.") and "." in pkg_name[4:]:
                pkg_parts = pkg_name.split(".")
                if len(pkg_parts) >= 3:
                    creators[pkg_parts[1]] += 1
                    products[pkg_parts[2]] += 1

    creator = creators.most_common(1)[0][0] if creators else None
    product = products.most_common(1)[0][0] if products else None

    return creator, product


def _extract_readme_content(tf: tarfile.TarFile, guid_names: dict[str, str]) -> str | None:
    """Extract first README/利用規約 file content (first 2000 chars)."""
    readme_patterns = ("readme", "read me", "利用規約", "説明", "howto", "manual")

    # Find readme GUID
    target_guid = None
    for guid, path in guid_names.items():
        path_lower = path.lower()
        if path_lower.endswith((".txt", ".md")):
            basename = path_lower.rsplit("/", 1)[-1] if "/" in path_lower else path_lower
            if any(p in basename for p in readme_patterns):
                target_guid = guid
                break

    if not target_guid:
        return None

    # Read the asset file
    asset_path = f"{target_guid}/asset"
    for member in tf.getmembers():
        if member.name == asset_path and member.isfile():
            try:
                f = tf.extractfile(member)
                if f:
                    content = f.read(2000).decode("utf-8", errors="replace")
                    return content
            except Exception:
                pass

    return None


def _parse_readme_avatars(content: str, top_avatars: list[str]) -> list[str]:
    """Extract avatar names from readme compatibility lists."""
    if not content:
        return []

    found: list[str] = []
    content_lower = content.lower()

    # Japanese: 対応アバター: A, B, C
    match = re.search(r"対応[^:：\n]*[:\s：]+([^\n]+)", content)
    if match:
        line = match.group(1).lower()
        for avatar in top_avatars:
            if avatar.lower() in line:
                found.append(avatar)

    # English: Compatible with A, B, C
    match = re.search(r"[Cc]ompatible[^:\n]*:\s*([^\n]+)", content)
    if match:
        line = match.group(1).lower()
        for avatar in top_avatars:
            if avatar.lower() in line:
                found.append(avatar)

    # Also check whole content for avatar names (lower confidence)
    if not found:
        for avatar in top_avatars:
            if avatar.lower() in content_lower:
                found.append(avatar)

    return list(set(found))  # deduplicate


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

        # Extract creator/product early so we can use product_name in thumbnail scoring
        all_pathnames = list(guid_names.values())
        creator, product = _extract_creator_product(all_pathnames)
        product_name = product or ""

        # Extract readme content and parse for avatar names
        readme_content = _extract_readme_content(tf, guid_names)
        readme_avatars: list[str] = []
        if readme_content:
            from vrc_organizer.tag_data import TOP_AVATARS
            readme_avatars = _parse_readme_avatars(readme_content, TOP_AVATARS)

        for member in members:
            if not member.isfile():
                continue

            name = member.name
            size = member.size

            # Thumbnail: check raw name before filtering non-asset entries.
            # preview.png and similar are good thumbnail candidates even
            # though _resolve_display_name returns None for them.
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and size < MAX_THUMB_SOURCE:
                score = _thumb_score(name, size, product_name)
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
                score = _thumb_score(display_name, size, product_name)
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
            "creator": creator or "",
            "product": product or "",
            "readme_avatars": ",".join(readme_avatars) if readme_avatars else "",
        },
    )


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
    if "uv" in base and not "uvs" in base:
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
    # (but cap it to avoid overweighting massive textures)
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

    # Preview.png inside GUID folders (Unity's asset preview)
    if "/preview.png" in name_lower or name_lower == "preview.png":
        score += 70

    return score
