"""Folder crawler that mines existing asset layouts for training data.

What it harvests:
    - Filename tokens for every recognized asset file (.zip / .rar /
      .unitypackage / .blend / .fbx / .obj / .psd / images)
    - Containing-folder tokens (Mochiyama, Mamehinata, etc. very often live
      as a folder above the actual product).
    - **Interior signals** — for .unitypackage and .zip archives:
        * pathnames inside the package (the Unity project layout, including
          creator/product folders inside the archive itself).
        * readme / `*.txt` / `*.md` contents (capped at READ_BYTES per file).
          A keyword extractor pulls candidate tag-like tokens out of the
          first chunk.
    - Co-occurrence of all tokens within the same archive.

What it skips:
    - Anything not in the asset extension whitelist (junk .ini / .lnk / .log)
    - Files under MIN_BYTES (typically loose .meta noise)
    - Hidden / system files
    - Anything matching SKIP_TOKEN_RE (version markers, "new folder", etc.)

Output:
    Pushes pairs into the existing `tag_cooccurrence` table so the autotag
    suggestion code benefits without extra wiring. Returns a summary dict
    with counts the UI shows.
"""
from __future__ import annotations

import io
import re
import tarfile
import zipfile
from pathlib import Path
from typing import Iterable

from vrc_organizer.database.queries import Queries

ASSET_EXTS = frozenset({
    ".zip", ".rar", ".7z", ".unitypackage", ".blend", ".fbx",
    ".obj", ".gltf", ".glb", ".psd", ".png", ".jpg", ".jpeg", ".webp",
})

MIN_BYTES = 4096

# How much text to read out of a single readme file. Tag-relevant content
# usually appears at the top; long files would just bloat the co-occurrence
# matrix without helping.
READ_BYTES = 4 * 1024

# Filenames inside an archive that we'll mine for text tokens. Lowercased.
README_PATTERNS = re.compile(
    r"(?:^|/)(readme|read_me|read me|read_first|説明|お読み|手順|installation)"
    r"[^/]*\.(txt|md|rtf)$",
    re.I,
)

# Tokens we drop from training data — they're so common they produce noise.
SKIP_TOKENS = frozenset({
    "new", "folder", "untitled", "copy", "rar", "zip", "package",
    "unitypackage", "ver", "version", "v1", "v2", "v3", "v4", "v5",
    "final", "fix", "update", "test", "wip", "old", "backup",
    "the", "and", "for", "with", "from", "to", "of", "in", "on", "by",
    "you", "your", "this", "that", "are", "is", "be", "or", "an", "a",
    # Unity internal layout tokens
    "assets", "editor", "plugins", "resources", "scripts", "scenes",
    "prefabs", "textures", "materials", "shaders", "models", "animations",
    "fonts", "audio", "preview", "icons", "icon", "thumbnail", "thumb",
    "data", "files", "file", "src", "tmp", "temp", "build",
    # Tech-givens that get massive co-occurrence weight without signal
    "physbone", "liltoon", "poiyomi", "blendshape", "vrcfury", "modular",
    "expressions", "shapes",
})

# Match "v1.0", "v1.0.2", "20240101", "2024-01-01", standalone "1", "01"
SKIP_TOKEN_RE = re.compile(
    r"^(v?\d+(\.\d+)*|\d{6,}|\d{4}[-_]\d{2}[-_]\d{2}|\d{1,3})$", re.I
)


def _tokenize(text: str) -> list[str]:
    """Identifier-style tokenizer. Splits camelCase, snake_case, paths, etc."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"[_\-\.\,\[\]\(\)\{\}\s\+/\\:]+", " ", text)
    out: list[str] = []
    for tok in text.split():
        tok = tok.strip().lower()
        if len(tok) < 2:
            continue
        if tok in SKIP_TOKENS or SKIP_TOKEN_RE.match(tok):
            continue
        out.append(tok)
    return out


def _tokens_from_readme(raw: bytes) -> set[str]:
    """Pick interesting tokens out of a readme excerpt.

    Plain decoder — tries utf-8, falls back to cp932/shift_jis (very common
    in Japanese asset readmes), then latin-1 as a last resort. Long stop-
    word lists keep generic prose ("thank you for purchasing") from
    flooding the co-occurrence map.
    """
    for enc in ("utf-8", "utf-8-sig", "cp932", "shift_jis", "latin-1"):
        try:
            text = raw.decode(enc, errors="ignore")
            break
        except Exception:
            continue
    else:
        return set()

    toks = _tokenize(text)
    # Keep only "interesting" tokens: enough length, not too common.
    # Heuristic: a token that appears multiple times in the readme is
    # signal-bearing; one-shot tokens are usually prose noise.
    counts: dict[str, int] = {}
    for t in toks:
        if len(t) < 3:
            continue
        counts[t] = counts.get(t, 0) + 1
    return {t for t, c in counts.items() if c >= 2}


def _walk(root: Path, max_depth: int = 6) -> Iterable[Path]:
    """Yield asset files under root. Capped depth to keep the crawl bounded."""
    if not root.exists():
        return
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        cur, depth = stack.pop()
        try:
            for child in cur.iterdir():
                name = child.name
                if name.startswith(".") or name.startswith("$"):
                    continue
                if child.is_dir() and depth < max_depth:
                    stack.append((child, depth + 1))
                    continue
                if child.suffix.lower() in ASSET_EXTS:
                    try:
                        if child.stat().st_size >= MIN_BYTES:
                            yield child
                    except OSError:
                        continue
        except (PermissionError, OSError):
            continue


def _harvest_zip(fp: Path) -> set[str]:
    """Pull tokens from a .zip's pathnames + any readme files inside."""
    tokens: set[str] = set()
    try:
        with zipfile.ZipFile(fp, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                tokens.update(_tokenize(name))
                if README_PATTERNS.search(name) and info.file_size <= READ_BYTES * 4:
                    try:
                        with zf.open(info) as f:
                            raw = f.read(READ_BYTES)
                            tokens.update(_tokens_from_readme(raw))
                    except Exception:
                        pass
    except (zipfile.BadZipFile, OSError):
        pass
    return tokens


def _harvest_unitypackage(fp: Path) -> set[str]:
    """A .unitypackage is a gzip'd tar. Each entry is one Unity asset GUID
    folder containing a `pathname` file (the original Unity project path)
    and `asset.meta`. We mine the pathnames — they encode creator and
    product structure even when the outer filename is garbage.
    """
    tokens: set[str] = set()
    try:
        with tarfile.open(fp, "r:gz") as tf:
            for member in tf:
                if not member.isfile():
                    continue
                if member.name.endswith("/pathname") and member.size <= 4096:
                    try:
                        f = tf.extractfile(member)
                        if f:
                            raw = f.read(4096)
                            try:
                                pn = raw.decode("utf-8", errors="ignore").strip()
                            except Exception:
                                pn = ""
                            tokens.update(_tokenize(pn))
                    except Exception:
                        continue
                elif README_PATTERNS.search(member.name) and member.size <= READ_BYTES * 4:
                    try:
                        f = tf.extractfile(member)
                        if f:
                            raw = f.read(READ_BYTES)
                            tokens.update(_tokens_from_readme(raw))
                    except Exception:
                        pass
    except (tarfile.TarError, OSError):
        pass
    return tokens


def _harvest_interior(fp: Path) -> set[str]:
    """Dispatch by archive type. Other formats contribute filename tokens only."""
    suffix = fp.suffix.lower()
    if suffix == ".zip":
        return _harvest_zip(fp)
    if suffix == ".unitypackage":
        return _harvest_unitypackage(fp)
    # .rar would need the rarfile lib + unrar exe — skip for now to keep
    # the crawler dependency-free. Filename tokens still apply.
    return set()


def crawl_directory(
    queries: Queries,
    root: Path,
    max_depth: int = 6,
    progress=None,
) -> dict:
    """Walk the directory, mine token co-occurrence, write to the DB.

    Returns a summary dict: {files, tokens, pairs_written, archives_opened}.
    """
    files = list(_walk(root, max_depth))
    total = len(files)

    existing = {name.lower(): tid for tid, name, _c, _n in queries.get_all_tags()}

    token_to_id: dict[str, int] = {}

    def ensure_tag(token: str) -> int:
        if token in token_to_id:
            return token_to_id[token]
        if token in existing:
            tid = existing[token]
        else:
            tid = queries.create_tag(token, "#475569")
            existing[token] = tid
        token_to_id[token] = tid
        return tid

    pairs_written = 0
    tokens_seen = 0
    archives_opened = 0

    for i, fp in enumerate(files):
        if progress is not None:
            try:
                progress(i, total, fp)
            except Exception:
                pass

        # Tokens from the last few path components.
        parts = list(fp.parts[-4:])
        path_tokens = set(_tokenize(" ".join(parts)))

        # Interior tokens — actually open the archive (only for zip /
        # unitypackage, where it's cheap enough). Skip if the archive is huge
        # to keep the crawl tractable.
        interior_tokens: set[str] = set()
        try:
            size = fp.stat().st_size
        except OSError:
            size = 0
        if fp.suffix.lower() in (".zip", ".unitypackage") and size <= 500 * 1024 * 1024:
            interior_tokens = _harvest_interior(fp)
            if interior_tokens:
                archives_opened += 1

        all_tokens = sorted(path_tokens | interior_tokens)
        # Cap to avoid one giant archive dominating co-occurrence.
        if len(all_tokens) > 60:
            all_tokens = all_tokens[:60]
        if len(all_tokens) < 2:
            continue
        tokens_seen += len(all_tokens)

        tag_ids = [ensure_tag(t) for t in all_tokens]
        if len(tag_ids) >= 2:
            queries.record_tag_cooccurrence(tag_ids)
            pairs_written += len(tag_ids) * (len(tag_ids) - 1) // 2

    return {
        "files": total,
        "tokens": tokens_seen,
        "pairs_written": pairs_written,
        "archives_opened": archives_opened,
        "root": str(root),
    }
