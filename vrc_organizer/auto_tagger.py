"""Auto-tagging: reads filenames and folder names, detects known terms,
and returns suggested tag IDs. Uses word matching against a comprehensive
dictionary, wildcard-like substring detection, and tag hierarchy rules."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from vrc_organizer.tag_data import TOP_AVATARS, WORD_TO_TAG, JP_AVATAR_TO_EN, TAG_HIERARCHY
from vrc_organizer.database.queries import Queries


def suggest_tags(
    queries: Queries,
    filename: str,
    extracted_path: Path | None = None,
) -> list[int]:
    """Return tag IDs to auto-assign based on filename and folder contents.

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
    """
    suggested_names: set[str] = set()
    confident_names: set[str] = set()

    # Collect text sources
    sources = [filename]
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
        for avatar in TOP_AVATARS:
            avatar_lower = avatar.lower()
            if avatar_lower in normalized_spaces or avatar_lower in normalized:
                suggested_names.add(avatar)
                confident_names.add(avatar)
            for token in tokens:
                if len(token) >= 3 and token == avatar_lower:
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

OUTFIT_ACCE_TAGS = {
    # Clothing — core VRChat outfit categories
    "Outfit", "Dress", "Skirt", "Pants", "Shorts", "Shirt",
    "Jacket", "Sweater", "Hoodie", "Vest", "Coat", "Tops",
    "Bodysuit", "Corset", "Jumpsuit",
    "Suit", "Gothic", "Lolita", "Cyberpunk", "Fantasy", "Idol Outfit",
    "Kimono", "Yukata", "Swimsuit", "Lingerie",
    "Pajamas", "Sportswear", "Maid", "School Uniform", "Military Uniform",
    "Wedding", "Bunny Suit", "Casual",
    # Footwear / Legwear
    "Shoes", "Heels", "Boots", "Sandals", "Socks", "Stockings", "Gloves",
    # Accessories
    "Accessory", "Hat", "Glasses", "Mask", "Necklace", "Choker",
    "Earrings", "Bracelet", "Ring", "Bag",
    "Hair Accessory", "Ribbon", "Collar", "Cape", "Scarf", "Belt",
    "Wings", "Tail", "Ears", "Horns",
    "Weapon", "Shield", "Prop",
    # Hair
    "Hair", "Hairstyle", "Bangs", "Ponytail", "Twin Tails",
    "Bob Cut", "Long Hair", "Short Hair", "Braids", "Ahoge",
    # Body mods
    "Makeup", "Tattoo", "Chibi",
    # Texture / Material
    "Texture", "Material",
    # Decoration attribute
    "Cute",
}


def suggest_genre(
    filename: str,
    filetype: str,
    suggested_tag_names: set[str],
) -> str:
    """Determine which of the 4 genres an asset belongs to."""
    filename_lower = filename.lower()

    # Direct avatar base tag → Avatar Base
    if "Avatar Base" in suggested_tag_names:
        return "Avatar Base"

    # Check filename keywords (e.g. "facetracking" → Gimmick)
    for genre, keywords in GENRE_KEYWORDS.items():
        for kw in keywords:
            if kw in filename_lower:
                return genre

    # Check tag-based deduction (e.g. "Gimmick" tag → Gimmick, "Prefab" → Tools)
    for tag_name in suggested_tag_names:
        if tag_name in GENRE_TAG_MAP:
            return GENRE_TAG_MAP[tag_name]

    # If any outfit/acce tag is present → Outfit & Acce
    if suggested_tag_names & OUTFIT_ACCE_TAGS:
        return "Outfit & Acce"

    # If an avatar name is detected and nothing more specific matched, it's an Avatar Base
    for avatar in TOP_AVATARS:
        if avatar in suggested_tag_names:
            return "Avatar Base"

    # Filetype heuristics
    if filetype in ("shader", "prefab"):
        return "Tools"

    # Default
    return "Outfit & Acce"


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
