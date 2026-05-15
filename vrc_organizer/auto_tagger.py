"""Auto-tagging: reads filenames and folder names, detects known terms,
and returns suggested tag IDs. Uses word matching against a comprehensive
dictionary, wildcard-like substring detection, and tag hierarchy rules."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from vrc_organizer.tag_data import (
    TOP_AVATARS, WORD_TO_TAG, JP_AVATAR_TO_EN, TAG_HIERARCHY, CREATOR_BY_AVATAR,
)
from vrc_organizer.database.queries import Queries


def suggest_tags(
    queries: Queries,
    filename: str,
    extracted_path: Path | None = None,
    scan_metadata: dict[str, str] | None = None,
) -> list[int]:
    """Return tag IDs to auto-assign based on filename, folder contents, and scan metadata.

    Matching strategy is recall-leaning but guarded against the classic
    short-key substring FP cascade:
      1. Direct token match (any key length) — high confidence.
      2. Substring match — only for word_keys of length >= 5, and only the
         `word_key in token` direction (e.g. "hair" in "hairstyle"). The
         previous version used a 3-char floor and both directions, which
         caused "ear" to match "heart", "cap" to match "captain", etc.
      3. Multi-word phrase scan on the normalized string for 4+ char keys
         (catches "long hair", "facetracking", "school uniform").
      4. Avatar + JP avatar matching unchanged.
      5. Hierarchy parent promotion fires ONLY for high-confidence
         detections (direct token / phrase / avatar match). Substring-only
         matches don't promote parents — that's where the worst cascading
         FPs were coming from.

    scan_metadata can include:
      - creator: extracted from Unity pathname structure
      - product: extracted from Unity pathname structure
      - readme_avatars: comma-separated avatar names from README files
    """
    suggested_names: set[str] = set()
    confident_names: set[str] = set()

    # Process scanner metadata first (high confidence signals)
    if scan_metadata:
        # Readme avatars are high confidence — they're explicit compatibility lists
        readme_avatars = scan_metadata.get("readme_avatars", "")
        if readme_avatars:
            for avatar in readme_avatars.split(","):
                avatar = avatar.strip()
                if avatar and avatar in TOP_AVATARS:
                    suggested_names.add(avatar)
                    confident_names.add(avatar)

        # Creator and product names are additional text sources to tokenize
        creator = scan_metadata.get("creator", "")
        product = scan_metadata.get("product", "")
    else:
        creator = ""
        product = ""

    # Collect text sources — filename, extracted path, plus creator/product from scanner
    sources = [filename]
    if creator:
        sources.append(creator)
    if product:
        sources.append(product)
    if extracted_path and extracted_path.is_dir():
        sources.append(extracted_path.name)
        try:
            for child in extracted_path.iterdir():
                if child.is_dir():
                    sources.append(child.name)
        except OSError:
            pass

    for source in sources:
        tokens = _tokenize(source)
        normalized = source.lower()
        normalized_spaces = normalized.replace("_", " ").replace("-", " ").replace(",", " ")

        # 1. Direct token match — high confidence
        for token in tokens:
            tag = WORD_TO_TAG.get(token)
            if tag is not None:
                suggested_names.add(tag)
                confident_names.add(tag)

        # 2. Substring match — single direction, 5+ char key floor
        # (eliminates the short-key FP cascade — "ear" no longer matches
        # "heart", "cap" no longer matches "captain", etc.)
        for token in tokens:
            for word_key, tag_name in WORD_TO_TAG.items():
                if len(word_key) >= 5 and word_key != token and word_key in token:
                    suggested_names.add(tag_name)

        # 3. Multi-word phrase scan on the full normalized string
        for word_key, tag_name in WORD_TO_TAG.items():
            if len(word_key) >= 4 and word_key in normalized_spaces:
                suggested_names.add(tag_name)
                confident_names.add(tag_name)

        # 4. Avatar name matching (popularity-ranked list, English + Japanese)
        # Short avatar names like "Ash", "Lime", "Rue", "Nia" used to match
        # any substring of the filename — producing cascading false
        # positives (ashen, splash, smash, etc). Now: a substring hit only
        # counts when the avatar name is long enough to be distinctive
        # (>=5 chars), and short names require an exact token match.
        SHORT_AVATAR_FLOOR = 5
        for avatar in TOP_AVATARS:
            avatar_lower = avatar.lower()
            if len(avatar_lower) >= SHORT_AVATAR_FLOOR:
                if avatar_lower in normalized_spaces or avatar_lower in normalized:
                    suggested_names.add(avatar)
                    confident_names.add(avatar)
            for token in tokens:
                # Exact token match only — never substring for short names.
                if token == avatar_lower:
                    suggested_names.add(avatar)
                    confident_names.add(avatar)

        # Japanese avatar name → English canonical tag
        for jp_name, en_tag in JP_AVATAR_TO_EN.items():
            if jp_name in normalized or jp_name in normalized_spaces:
                suggested_names.add(en_tag)
                confident_names.add(en_tag)
            for token in tokens:
                if len(token) >= 2 and (token == jp_name or jp_name in token):
                    suggested_names.add(en_tag)
                    confident_names.add(en_tag)

        # User-created tags become live aliases for the matcher. Anything
        # the user has tagged manually that shows up in a later import's
        # filename gets re-suggested. Same short-name discipline as
        # avatars: only substring-match if the tag name is >=5 chars long.
        try:
            for tid, tname, _color, _count in queries.get_all_tags():
                if not tname or tname.lower() in WORD_TO_TAG:
                    continue
                tn = tname.lower()
                if len(tn) >= 5:
                    if tn in normalized_spaces or tn in normalized:
                        suggested_names.add(tname)
                        confident_names.add(tname)
                for token in tokens:
                    if token == tn:
                        suggested_names.add(tname)
                        confident_names.add(tname)
        except Exception:
            # Tag DB unavailable — fall back silently.
            pass

    # 5. Apply hierarchy parent promotion — only for confident detections.
    # A substring-only match (e.g. "wears" producing "Wear" via "wear" in
    # "wears") will NOT auto-promote to "Outfit" or any other parent. This
    # stops one wrong fuzzy match from snowballing into 2-3 wrong tags.
    hierarchy_additions: set[str] = set()
    for tag_name in confident_names:
        for parent, children in TAG_HIERARCHY.items():
            if tag_name in children:
                hierarchy_additions.add(parent)
    suggested_names.update(hierarchy_additions)

    # 6. Implicit creator inference — confident avatar matches imply their
    # creator. Mamehinata/Kipfel → MOCHIYAMA, etc. Pulled from
    # CREATOR_BY_AVATAR so the user can extend it from one place.
    for avatar_name in list(confident_names):
        creator = CREATOR_BY_AVATAR.get(avatar_name)
        if creator:
            suggested_names.add(creator)

    # Resolve names to tag IDs, creating tags that don't exist yet
    tag_ids: list[int] = []
    existing_tags = {name: tid for tid, name, _, _ in queries.get_all_tags()}

    for name in suggested_names:
        if name in existing_tags:
            tag_ids.append(existing_tags[name])
        else:
            tid = queries.create_tag(name, _color_for(name))
            if tid:
                tag_ids.append(tid)
                existing_tags[name] = tid

    return tag_ids


# ── Genre Classification ──────────────────────────────────

# Tags that indicate each genre
GENRE_TAG_MAP: dict[str, str] = {
    "Avatar Base": "Avatar Base",
    "Gimmick": "Gimmick",
    "Tool": "Tools",
    "Prefab": "Tools",
    "Shader": "Tools",
    "Scene": "Tools",
    "Script": "Tools",
}

# Keywords in filenames that indicate genre
GENRE_KEYWORDS: dict[str, list[str]] = {
    "Avatar Base": [
        "avatar base", "original body", "素体", "原形", "avatarbase",
    ],
    "Gimmick": [
        "gimmick", "ギミック", "facetracking", "face tracking",
        "facetrack", "表情", "tracking", "emote", "gesture",
        "blendshape", "blend shape",
    ],
    "Tools": [
        "tool", "ツール", "editor", "エディタ", "blendshapeeditor",
        "blendshape editor", "generator", "converter", "prefab",
        "scene", "shader", "シェーダー",
    ],
}

OUTFIT_TAGS = {
    # Clothing — Outfit genre
    "Outfit", "Dress", "Skirt", "Pants", "Shorts", "Shirt",
    "Jacket", "Sweater", "Hoodie", "Vest", "Coat", "Tops",
    "Bodysuit", "Corset", "Jumpsuit",
    "Suit", "Gothic", "Lolita", "Cyberpunk", "Fantasy", "Idol Outfit",
    "Kimono", "Yukata", "Swimsuit", "Lingerie",
    "Pajamas", "Sportswear", "Maid", "School Uniform", "Military Uniform",
    "Wedding", "Bunny Suit", "Casual",
    # Footwear / legwear is part of the outfit
    "Shoes", "Heels", "Boots", "Sandals", "Socks", "Stockings",
}

ACCESSORY_TAGS = {
    "Accessory", "Hat", "Glasses", "Mask", "Necklace", "Choker",
    "Earrings", "Bracelet", "Ring", "Bag", "Gloves",
    "Hair Accessory", "Ribbon", "Collar", "Cape", "Scarf", "Belt",
    "Wings", "Tail", "Ears", "Horns",
    # Per user: props fold into Accessory
    "Weapon", "Shield", "Prop",
    # Hair lives here too — a hair pack is not an outfit
    "Hair", "Hairstyle", "Bangs", "Ponytail", "Twin Tails",
    "Bob Cut", "Long Hair", "Short Hair", "Braids", "Ahoge",
    # Body mods
    "Makeup", "Tattoo",
    # Surface assets a creator drops alongside an outfit
    "Texture", "Material",
}

# Back-compat alias for any callers that imported the old name.
OUTFIT_ACCE_TAGS = OUTFIT_TAGS | ACCESSORY_TAGS


def suggest_genre(
    filename: str,
    filetype: str,
    suggested_tag_names: set[str],
) -> str:
    """Determine which of the 5 genres an asset belongs to.

    Genres are mutually exclusive: Avatar Base | Outfit | Accessory | Gimmick | Tools.
    """
    filename_lower = filename.lower()

    if "Avatar Base" in suggested_tag_names:
        return "Avatar Base"

    for genre, keywords in GENRE_KEYWORDS.items():
        for kw in keywords:
            if kw in filename_lower:
                return genre

    for tag_name in suggested_tag_names:
        if tag_name in GENRE_TAG_MAP:
            return GENRE_TAG_MAP[tag_name]

    # Outfit beats Accessory when both are signaled — a full outfit pack
    # usually ships accessories too, but the category should reflect the
    # primary intent.
    if suggested_tag_names & OUTFIT_TAGS:
        return "Outfit"
    if suggested_tag_names & ACCESSORY_TAGS:
        return "Accessory"

    for avatar in TOP_AVATARS:
        if avatar in suggested_tag_names:
            return "Avatar Base"

    if filetype in ("shader", "prefab"):
        return "Tools"

    # Default: Accessory is a softer landing than the old "Outfit & Acce" —
    # an asset with no signal is more often a small accessory than a full outfit.
    return "Accessory"


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens. Handles camelCase, PascalCase,
    snake_case, kebab-case, spaces, dots, and number-letter boundaries."""
    # Insert spaces at camelCase boundaries: "TwinTails" → "Twin Tails"
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    # Insert spaces at digit-letter boundaries: "ver2Outfit" → "ver2 Outfit"
    text = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", text)
    # Replace common delimiters with spaces
    cleaned = re.sub(r"[_\-\.\,\[\]\(\)\{\}\s\+]+", " ", text)
    return [t.lower() for t in cleaned.split() if len(t) >= 2]


def _color_for(name: str) -> str:
    """Deterministic color for a tag name."""
    colors = [
        "#ef4444", "#f59e0b", "#3b82f6", "#a855f7",
        "#22c55e", "#ec4899", "#f97316", "#6366f1",
        "#14b8a6", "#8b5cf6", "#06b6d4", "#84cc16",
    ]
    h = sum(ord(c) for c in name)
    return colors[h % len(colors)]
