from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from vrc_organizer.database.connection import DatabaseManager
from vrc_organizer.models.asset import Asset


def asset_from_row(row: sqlite3.Row) -> Asset:
    return Asset(
        id=row["id"],
        filename=row["filename"],
        filepath=Path(row["filepath"]),
        filetype=row["filetype"],
        file_size=row["file_size"],
        mod_time=row["mod_time"],
        date_added=row["date_added"],
        thumbnail=Path(row["thumbnail"]) if row["thumbnail"] else None,
        thumb_state=row["thumb_state"],
        notes=row["notes"] or "",
        scan_state=row["scan_state"],
    )


class Queries:
    _VALID_SORTS = frozenset({
        "date_added ASC", "date_added DESC",
        "filename ASC", "filename DESC",
        "file_size ASC", "file_size DESC",
        "filetype ASC", "filetype DESC",
        "mod_time ASC", "mod_time DESC",
    })

    def __init__(self, db: DatabaseManager):
        self._db = db

    # ── Asset CRUD ──────────────────────────────────────────

    def insert_asset(self, filename: str, filepath: Path, filetype: str,
                     file_size: int, mod_time: float, thumb_state: str = "pending",
                     thumbnail: Optional[Path] = None) -> int:
        with self._db.write_connection() as conn:
            cur = conn.execute(
                """INSERT INTO assets (filename, filepath, filetype, file_size,
                   mod_time, thumb_state, thumbnail)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(filepath) DO UPDATE SET
                   filename=excluded.filename, file_size=excluded.file_size,
                   mod_time=excluded.mod_time
                   RETURNING id""",
                (filename, str(filepath), filetype, file_size, mod_time,
                 thumb_state, str(thumbnail) if thumbnail else None)
            )
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else 0

    def get_asset(self, asset_id: int) -> Optional[Asset]:
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM assets WHERE id = ? AND trash_date IS NULL",
                (asset_id,)
            ).fetchone()
            return asset_from_row(row) if row else None

    def get_asset_by_path(self, filepath: Path) -> Optional[Asset]:
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM assets WHERE filepath = ?", (str(filepath),)
            ).fetchone()
            return asset_from_row(row) if row else None

    def update_thumbnail(self, asset_id: int, thumb_path: Optional[Path],
                         state: str = "ready"):
        with self._db.write_connection() as conn:
            conn.execute(
                "UPDATE assets SET thumbnail = ?, thumb_state = ? WHERE id = ?",
                (str(thumb_path) if thumb_path else None, state, asset_id)
            )
            conn.commit()

    def reset_thumbs_for_labeled(self) -> int:
        """Reset thumb_state to 'pending' for assets with a real cover label.

        After cover labeling, this lets ThumbWorker regenerate thumbnails
        using the user-labeled cover image. We skip rows whose image_name
        is '__cached__' (the labeler's fallback marker meaning "no images
        in the archive — keep the existing cached thumb").

        We deliberately keep the existing `thumbnail` path so that if
        regeneration fails the old image still renders instead of leaving
        the asset with a blank tile.
        """
        with self._db.write_connection() as conn:
            cur = conn.execute(
                "UPDATE assets SET thumb_state = 'pending' "
                "WHERE id IN (SELECT asset_id FROM cover_labels_v2 "
                "             WHERE image_name NOT IN ('__cached__', '__skipped__')) "
                "AND thumb_state IN ('ready', 'error') "
                "AND trash_date IS NULL"
            )
            conn.commit()
            return cur.rowcount

    def reset_all_thumbs_pending(self) -> int:
        """Reset thumb_state to 'pending' for all non-trashed assets.

        Used after purging the thumbnail cache so all assets regenerate thumbnails.
        """
        with self._db.write_connection() as conn:
            cur = conn.execute(
                "UPDATE assets SET thumb_state = 'pending', thumbnail = NULL "
                "WHERE trash_date IS NULL"
            )
            conn.commit()
            return cur.rowcount

    def update_notes(self, asset_id: int, notes: str):
        with self._db.write_connection() as conn:
            conn.execute(
                "UPDATE assets SET notes = ? WHERE id = ?", (notes, asset_id)
            )
            conn.commit()

    def update_scan_state(self, asset_id: int, state: str):
        with self._db.write_connection() as conn:
            conn.execute(
                "UPDATE assets SET scan_state = ? WHERE id = ?", (state, asset_id)
            )
            conn.commit()

    def delete_asset(self, asset_id: int):
        with self._db.write_connection() as conn:
            conn.execute(
                "UPDATE assets SET trash_date = strftime('%s', 'now') WHERE id = ?",
                (asset_id,)
            )
            conn.commit()

    def purge_expired_trash(self, days: int = 30):
        with self._db.write_connection() as conn:
            conn.execute(
                "DELETE FROM assets WHERE trash_date IS NOT NULL "
                "AND trash_date < strftime('%s', 'now') - ? * 86400",
                (days,)
            )
            conn.commit()

    # ── Asset Listing ───────────────────────────────────────

    _FTS5_RESERVED = {"AND", "OR", "NOT", "NEAR"}

    @staticmethod
    def _fts5_prefix_query(search_text: str) -> str:
        """Convert user search to FTS5 prefix query.

        Normalizes fullwidth→halfwidth (NFKC), splits camelCase and
        digit-letter boundaries, and replaces common delimiters so that
        concatenated input like "TwinTails" matches "Twin Tails" in filenames.
        """
        text = unicodedata.normalize('NFKC', search_text)
        # Split camelCase: "TwinTails" → "Twin Tails"
        text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
        text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
        # Split digit-letter boundaries: "ver2Outfit" → "ver2 Outfit"
        text = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text)
        text = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", text)
        # Replace common delimiters with spaces
        text = re.sub(r"[_\-\.\,\[\]\(\)\{\}\s\+]+", " ", text)

        terms = re.findall(r'\w+', text)
        if not terms:
            return search_text
        escaped = []
        for term in terms:
            if term.upper() in Queries._FTS5_RESERVED:
                escaped.append(f'"{term}"*')
            else:
                escaped.append(f"{term}*")
        return " AND ".join(escaped)

    def list_assets(self, offset: int = 0, limit: int = 100,
                    filetypes: Optional[list[str]] = None,
                    or_tag_ids: Optional[list[int]] = None,
                    and_tag_ids: Optional[list[int]] = None,
                    search_query: Optional[str] = None,
                    sort: str = "date_added DESC") -> list[Asset]:
        with self._db.connection() as conn:
            query = "SELECT * FROM assets WHERE trash_date IS NULL"
            params: list = []

            if filetypes:
                placeholders = ",".join("?" for _ in filetypes)
                query += f" AND filetype IN ({placeholders})"
                params.extend(filetypes)

            if or_tag_ids:
                placeholders = ",".join("?" for _ in or_tag_ids)
                query += f" AND id IN (SELECT asset_id FROM asset_tags WHERE tag_id IN ({placeholders}))"
                params.extend(or_tag_ids)

            if and_tag_ids:
                for tid in and_tag_ids:
                    query += " AND id IN (SELECT asset_id FROM asset_tags WHERE tag_id = ?)"
                    params.append(tid)

            extra_ids = self._romaji_extra_ids(conn, search_query) if search_query else []
            if search_query:
                if extra_ids:
                    placeholders = ",".join("?" for _ in extra_ids)
                    query += (
                        " AND ("
                        " rowid IN (SELECT rowid FROM assets_fts WHERE assets_fts MATCH ?)"
                        f" OR id IN ({placeholders})"
                        " )"
                    )
                    params.append(self._fts5_prefix_query(search_query))
                    params.extend(extra_ids)
                else:
                    query += " AND rowid IN (SELECT rowid FROM assets_fts WHERE assets_fts MATCH ?)"
                    params.append(self._fts5_prefix_query(search_query))

            safe_sort = sort if sort in self._VALID_SORTS else "date_added DESC"
            query += f" ORDER BY {safe_sort} LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            return [asset_from_row(r) for r in rows]

    def _romaji_extra_ids(self, conn, search_query: str) -> list[int]:
        """Return asset ids whose ROMANIZED filename contains the query.

        Only fires for ASCII queries — typing "manuka" finds マヌカ.zip
        because the kana romanizes to "manuka". Japanese queries skip this
        path (the FTS column already indexes the original text).
        """
        q = (search_query or "").strip().lower()
        if not q or not q.isascii():
            return []
        from vrc_organizer.romaji import to_romaji, has_japanese
        ids: list[int] = []
        rows = conn.execute(
            "SELECT id, filename FROM assets WHERE trash_date IS NULL"
        ).fetchall()
        for r in rows:
            name = r["filename"] if isinstance(r, dict) else r[1]
            aid = r["id"] if isinstance(r, dict) else r[0]
            if has_japanese(name) and q in to_romaji(name).lower():
                ids.append(aid)
        return ids

    def count_assets(self, filetypes: Optional[list[str]] = None,
                     or_tag_ids: Optional[list[int]] = None,
                     and_tag_ids: Optional[list[int]] = None,
                     search_query: Optional[str] = None) -> int:
        with self._db.connection() as conn:
            query = "SELECT COUNT(*) FROM assets WHERE trash_date IS NULL"
            params: list = []

            if filetypes:
                placeholders = ",".join("?" for _ in filetypes)
                query += f" AND filetype IN ({placeholders})"
                params.extend(filetypes)

            if or_tag_ids:
                placeholders = ",".join("?" for _ in or_tag_ids)
                query += f" AND id IN (SELECT asset_id FROM asset_tags WHERE tag_id IN ({placeholders}))"
                params.extend(or_tag_ids)

            if and_tag_ids:
                for tid in and_tag_ids:
                    query += " AND id IN (SELECT asset_id FROM asset_tags WHERE tag_id = ?)"
                    params.append(tid)

            if search_query:
                extra_ids = self._romaji_extra_ids(conn, search_query)
                if extra_ids:
                    placeholders = ",".join("?" for _ in extra_ids)
                    query += (
                        " AND ("
                        " rowid IN (SELECT rowid FROM assets_fts WHERE assets_fts MATCH ?)"
                        f" OR id IN ({placeholders})"
                        " )"
                    )
                    params.append(self._fts5_prefix_query(search_query))
                    params.extend(extra_ids)
                else:
                    query += " AND rowid IN (SELECT rowid FROM assets_fts WHERE assets_fts MATCH ?)"
                    params.append(self._fts5_prefix_query(search_query))

            return conn.execute(query, params).fetchone()[0]

    def count_by_type(self) -> list[tuple[str, int]]:
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT filetype, COUNT(*) as cnt FROM assets "
                "WHERE trash_date IS NULL GROUP BY filetype ORDER BY cnt DESC"
            ).fetchall()
            return [(r[0], r[1]) for r in rows]

    def requeue_failed_thumbs(self) -> int:
        """Move assets out of the 'error' thumb state once per session.

        Previously, a failed regeneration after cover-labeling would null
        the existing `thumbnail` path and lock the asset in 'error'. The
        new worker preserves the old path on failure, but rows already
        stuck need a retry. This requeues them so the next ThumbWorker
        run picks them up.
        """
        with self._db.write_connection() as conn:
            cur = conn.execute(
                "UPDATE assets SET thumb_state = 'pending' "
                "WHERE thumb_state = 'error' AND trash_date IS NULL"
            )
            conn.commit()
            return cur.rowcount

    def get_pending_thumbs(self, limit: int = 100) -> list[Asset]:
        with self._db.connection() as conn:
            if limit > 0:
                rows = conn.execute(
                    "SELECT * FROM assets WHERE trash_date IS NULL "
                    "AND thumb_state IN ('pending', 'generating') "
                    "ORDER BY date_added ASC LIMIT ?",
                    (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM assets WHERE trash_date IS NULL "
                    "AND thumb_state IN ('pending', 'generating') "
                    "ORDER BY date_added ASC"
                ).fetchall()
            return [asset_from_row(r) for r in rows]

    # ── Tags ─────────────────────────────────────────────────

    def create_tag(self, name: str, color: str = "#6366f1") -> int:
        with self._db.write_connection() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?) RETURNING id",
                (name, color)
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return row[0]
            # Tag already exists - fetch its ID
            row = conn.execute(
                "SELECT id FROM tags WHERE name = ?", (name,)
            ).fetchone()
            conn.commit()
            return row[0] if row else 0

    def get_all_tags(self) -> list[tuple[int, str, str, int]]:
        with self._db.connection() as conn:
            rows = conn.execute(
                """SELECT t.id, t.name, t.color, COUNT(at.asset_id) as cnt
                   FROM tags t LEFT JOIN asset_tags at ON t.id = at.tag_id
                   LEFT JOIN assets a ON at.asset_id = a.id AND a.trash_date IS NULL
                   GROUP BY t.id ORDER BY t.name"""
            ).fetchall()
            return [(r[0], r[1], r[2], r[3]) for r in rows]

    def search_tags(self, query: str, limit: int = 15) -> list[tuple[int, str, str, int]]:
        """Search tags by name substring, returning (id, name, color, asset_count)."""
        with self._db.connection() as conn:
            rows = conn.execute(
                """SELECT t.id, t.name, t.color, COUNT(at.asset_id) as cnt
                   FROM tags t LEFT JOIN asset_tags at ON t.id = at.tag_id
                   LEFT JOIN assets a ON at.asset_id = a.id AND a.trash_date IS NULL
                   WHERE t.name LIKE ?
                   GROUP BY t.id ORDER BY cnt DESC LIMIT ?""",
                (f"%{query}%", limit)
            ).fetchall()
            return [(r[0], r[1], r[2], r[3]) for r in rows]

    def get_tag_by_name(self, name: str) -> tuple[int, str, str] | None:
        """Return (id, name, color) for a tag by exact name, or None."""
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT id, name, color FROM tags WHERE name = ?", (name,)
            ).fetchone()
            return (row[0], row[1], row[2]) if row else None

    def delete_tag(self, tag_id: int):
        with self._db.write_connection() as conn:
            conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            conn.commit()

    def rename_tag(self, tag_id: int, new_name: str):
        with self._db.write_connection() as conn:
            conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new_name, tag_id))
            conn.commit()

    def get_tag_usage_count(self, tag_id: int) -> int:
        """Return how many non-trashed assets use this tag."""
        with self._db.connection() as conn:
            row = conn.execute(
                """SELECT COUNT(*) FROM asset_tags at
                   INNER JOIN assets a ON a.id = at.asset_id
                   WHERE at.tag_id = ? AND a.trash_date IS NULL""",
                (tag_id,)
            ).fetchone()
            return row[0] if row else 0

    def add_tag_to_asset(self, asset_id: int, tag_id: int):
        with self._db.write_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
                (asset_id, tag_id)
            )
            conn.commit()

    def remove_tag_from_asset(self, asset_id: int, tag_id: int):
        with self._db.write_connection() as conn:
            conn.execute(
                "DELETE FROM asset_tags WHERE asset_id = ? AND tag_id = ?",
                (asset_id, tag_id)
            )
            conn.commit()

    def set_genre(self, asset_id: int, new_genre_name: str, genre_color: str = "#6366f1") -> int:
        """Replace whichever genre tag is on the asset with `new_genre_name`.

        Genres are mutually exclusive — this is the atomic enforcement
        point. Returns the tag_id of the new genre.
        """
        from vrc_organizer.tag_data import GENRE_NAMES
        with self._db.write_connection() as conn:
            cur = conn.execute("SELECT id FROM tags WHERE name = ?", (new_genre_name,))
            row = cur.fetchone()
            if row:
                new_id = row[0]
            else:
                cur = conn.execute(
                    "INSERT INTO tags (name, color) VALUES (?, ?) RETURNING id",
                    (new_genre_name, genre_color),
                )
                new_id = cur.fetchone()[0]
            placeholders = ",".join("?" * len(GENRE_NAMES))
            conn.execute(
                f"""DELETE FROM asset_tags
                    WHERE asset_id = ?
                      AND tag_id IN (SELECT id FROM tags WHERE name IN ({placeholders}))
                      AND tag_id <> ?""",
                (asset_id, *GENRE_NAMES, new_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
                (asset_id, new_id),
            )
            conn.commit()
            return new_id

    def hard_purge_all(self) -> dict:
        """Debug nuke: drop every asset, tag, label, and cooccurrence row.

        Returns counts per table so the UI can show what got wiped. Settings
        and the schema itself stay intact. Default tags re-seed on next launch
        via the main window's `_seed_default_tags`.
        """
        counts: dict = {}
        with self._db.write_connection() as conn:
            for table in (
                "asset_tags",
                "scan_results",
                "cover_labels",
                "cover_labels_v2",
                "tag_labels",
                "tag_cooccurrence",
                "assets",
                "assets_fts",
                "tags",
            ):
                try:
                    cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
                    counts[table] = cur.fetchone()[0]
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    counts[table] = 0
            conn.commit()
        return counts

    def migrate_legacy_genres(self) -> int:
        """Rename any tag row whose name is a legacy genre to its modern slot.

        Safe to run on every startup — it's a no-op once nothing matches.
        """
        from vrc_organizer.tag_data import LEGACY_GENRE_REMAP
        if not LEGACY_GENRE_REMAP:
            return 0
        renamed = 0
        with self._db.write_connection() as conn:
            for old, new in LEGACY_GENRE_REMAP.items():
                cur = conn.execute("SELECT id FROM tags WHERE name = ?", (old,))
                old_row = cur.fetchone()
                if not old_row:
                    continue
                old_id = old_row[0]
                cur = conn.execute("SELECT id FROM tags WHERE name = ?", (new,))
                new_row = cur.fetchone()
                if new_row:
                    new_id = new_row[0]
                    # Move every reference off the legacy id. tag_labels has
                    # an FK with no CASCADE on genre_tag_id, so we have to
                    # rewrite it explicitly before deleting.
                    conn.execute(
                        "UPDATE OR IGNORE asset_tags SET tag_id = ? WHERE tag_id = ?",
                        (new_id, old_id),
                    )
                    conn.execute("DELETE FROM asset_tags WHERE tag_id = ?", (old_id,))
                    conn.execute(
                        "UPDATE tag_labels SET genre_tag_id = ? WHERE genre_tag_id = ?",
                        (new_id, old_id),
                    )
                    conn.execute("DELETE FROM tags WHERE id = ?", (old_id,))
                else:
                    conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new, old_id))
                renamed += 1
            conn.commit()
        return renamed

    def get_tags_for_asset(self, asset_id: int) -> list[tuple[int, str, str]]:
        """Return (tag_id, name, color) for all tags on an asset."""
        with self._db.connection() as conn:
            rows = conn.execute(
                """SELECT t.id, t.name, t.color
                   FROM tags t
                   INNER JOIN asset_tags at ON t.id = at.tag_id
                   WHERE at.asset_id = ?
                   ORDER BY t.name""",
                (asset_id,)
            ).fetchall()
            return [(r[0], r[1], r[2]) for r in rows]

    # ── Scan Results ─────────────────────────────────────────

    def clear_scan_results(self, asset_id: int):
        with self._db.write_connection() as conn:
            conn.execute("DELETE FROM scan_results WHERE asset_id = ?", (asset_id,))
            conn.commit()

    def insert_scan_results(self, asset_id: int,
                            entries: list[tuple[str, str, int]]):
        with self._db.write_connection() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO scan_results
                   (asset_id, entry_name, entry_type, entry_size)
                   VALUES (?, ?, ?, ?)""",
                [(asset_id, name, etype, size) for name, etype, size in entries]
            )
            conn.commit()

    def get_all_asset_ids(self) -> list[int]:
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT id FROM assets WHERE trash_date IS NULL"
            ).fetchall()
            return [r[0] for r in rows]

    def get_scan_results(self, asset_id: int) -> list[tuple[str, str, int]]:
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT entry_name, entry_type, entry_size FROM scan_results "
                "WHERE asset_id = ? ORDER BY entry_type, entry_name",
                (asset_id,)
            ).fetchall()
            return [(r[0], r[1], r[2]) for r in rows]

    # ── Settings ─────────────────────────────────────────────

    def get_setting(self, key: str, default: str = "") -> str:
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else default

    def set_setting(self, key: str, value: str):
        with self._db.write_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )
            conn.commit()

    def get_assets_in_dir(self, dir_path: Path) -> list[Asset]:
        """Return all assets whose filepath is under dir_path."""
        prefix = str(dir_path)
        # Escape LIKE special chars: \ → \\, % → \%, _ → \_
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM assets WHERE filepath LIKE ? || '%' ESCAPE '\\'",
                (escaped,)
            ).fetchall()
            return [asset_from_row(r) for r in rows]

    def update_asset_filepath(self, asset_id: int, new_path: Path):
        with self._db.write_connection() as conn:
            conn.execute(
                "UPDATE assets SET filepath = ? WHERE id = ?",
                (str(new_path), asset_id)
            )
            conn.commit()

    # ── Labeling for ML Training ───────────────────────────────

    def get_unlabeled_cover_assets(self, limit: int = 50) -> list[Asset]:
        """Return assets with images but no cover label yet."""
        with self._db.connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT a.* FROM assets a
                   INNER JOIN scan_results sr ON a.id = sr.asset_id
                   WHERE sr.entry_type = 'image'
                     AND a.trash_date IS NULL
                     AND a.id NOT IN (SELECT asset_id FROM cover_labels_v2)
                   ORDER BY a.date_added DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            return [asset_from_row(r) for r in rows]

    def get_unlabeled_tag_assets(self, limit: int = 50) -> list[Asset]:
        """Return assets with tags that haven't been labeled yet."""
        with self._db.connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT a.* FROM assets a
                   INNER JOIN asset_tags at ON a.id = at.asset_id
                   WHERE a.trash_date IS NULL
                     AND a.id NOT IN (SELECT asset_id FROM tag_labels)
                   ORDER BY a.date_added DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            return [asset_from_row(r) for r in rows]

    def get_cover_label(self, asset_id: int) -> str | None:
        """Return the labeled cover image name for an asset, or None."""
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT image_name FROM cover_labels_v2 WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            return row[0] if row else None

    def save_cover_label_v2(
        self, asset_id: int, image_name: str,
        image_width: int = 0, image_height: int = 0,
        archive_depth: int = 0, filename_score: int = 0,
        images_shown: int = 0
    ):
        """Save cover label with ML training metadata."""
        with self._db.write_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO cover_labels_v2
                   (asset_id, image_name, image_width, image_height,
                    archive_depth, filename_score, images_shown)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (asset_id, image_name, image_width, image_height,
                 archive_depth, filename_score, images_shown)
            )
            conn.commit()

    def save_tag_label(
        self, asset_id: int, session_id: str,
        original_tags: list[int], accepted_tags: list[int],
        rejected_tags: list[int], added_tags: list[int],
        genre_tag_id: int | None
    ):
        """Save tag labeling session with ML training metadata."""
        import json
        with self._db.write_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO tag_labels
                   (asset_id, session_id, original_tags, accepted_tags,
                    rejected_tags, added_tags, genre_tag_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (asset_id, session_id,
                 json.dumps(original_tags), json.dumps(accepted_tags),
                 json.dumps(rejected_tags), json.dumps(added_tags),
                 genre_tag_id)
            )
            conn.commit()

    def record_tag_cooccurrence(self, tag_ids: list[int]):
        """For all pairs in tag_ids, increment co-occurrence count."""
        if len(tag_ids) < 2:
            return
        with self._db.write_connection() as conn:
            for i in range(len(tag_ids)):
                for j in range(i + 1, len(tag_ids)):
                    a, b = tag_ids[i], tag_ids[j]
                    if a > b:
                        a, b = b, a
                    conn.execute(
                        """INSERT INTO tag_cooccurrence (tag_a_id, tag_b_id, count)
                           VALUES (?, ?, 1)
                           ON CONFLICT(tag_a_id, tag_b_id) DO UPDATE
                           SET count = count + 1""",
                        (a, b)
                    )
            conn.commit()

    def get_related_tags(self, tag_id: int, limit: int = 10) -> list[tuple[int, str, int]]:
        """Tags that frequently co-occur with tag_id, sorted by count DESC."""
        with self._db.connection() as conn:
            rows = conn.execute(
                """SELECT t.id, t.name, tc.count FROM tag_cooccurrence tc
                   JOIN tags t ON (t.id = tc.tag_a_id OR t.id = tc.tag_b_id)
                   AND t.id != ?
                   WHERE (tc.tag_a_id = ? OR tc.tag_b_id = ?)
                   ORDER BY tc.count DESC LIMIT ?""",
                (tag_id, tag_id, tag_id, limit)
            ).fetchall()
            return [(r[0], r[1], r[2]) for r in rows]

    # ── Shareable training pool (cross-user) ────────────────

    def export_cooccurrence_pool(self) -> dict:
        """Export the bits of training data that survive crossing user
        boundaries. Asset-keyed records (cover labels, tag reviews per asset)
        only make sense in one library — but tag co-occurrence is purely
        between tag NAMES and merges cleanly into anyone else's DB.

        Every export is stamped with a fresh UUID `export_id`. The recipient's
        import path records that id and refuses to merge the same file a
        second time, so accidentally importing the same JSON 100 times can't
        multiply counts.
        """
        import uuid
        with self._db.connection() as conn:
            cooc = conn.execute(
                """SELECT t1.name, t2.name, tc.count
                   FROM tag_cooccurrence tc
                   JOIN tags t1 ON t1.id = tc.tag_a_id
                   JOIN tags t2 ON t2.id = tc.tag_b_id"""
            ).fetchall()
            # Standalone tag names with their colours so the recipient gets
            # the same tag colour scheme. Tags with zero co-occurrence still
            # ship — they're often creator/product names the friend will
            # encounter.
            tags = conn.execute("SELECT name, color FROM tags").fetchall()
        return {
            "kind": "cooccurrence_pool",
            "version": 2,
            "export_id": str(uuid.uuid4()),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "tags": [{"name": r[0], "color": r[1]} for r in tags],
            "cooccurrence": [
                {"a": r[0], "b": r[1], "count": r[2]} for r in cooc
            ],
        }

    def import_cooccurrence_pool(self, data: dict) -> dict:
        """Merge a co-occurrence pool from another user's export.

        Cumulative: count columns are SUMMED with whatever's already there.
        New tag names are created automatically. Tag colors only fill in
        for tags that didn't exist locally — your local color stays for
        anything you already had.

        Each pool carries an `export_id` UUID. If we've already imported it,
        we return `{"already_imported": ..., **stats from first import}`
        without modifying anything.
        """
        stats = {"tags_added": 0, "pairs_merged": 0}
        if data.get("kind") != "cooccurrence_pool":
            return {"error": "Not a cooccurrence pool export.", **stats}

        export_id = data.get("export_id") or ""
        if export_id:
            with self._db.connection() as conn:
                prior = conn.execute(
                    "SELECT imported_at, tags_added, pairs_merged "
                    "FROM imported_pools WHERE export_id = ?",
                    (export_id,),
                ).fetchone()
            if prior is not None:
                return {
                    "already_imported": True,
                    "export_id": export_id,
                    "imported_at": prior[0],
                    "tags_added": prior[1],
                    "pairs_merged": prior[2],
                }

        with self._db.write_connection() as conn:
            existing = {
                r[0]: r[1] for r in conn.execute(
                    "SELECT name, id FROM tags"
                ).fetchall()
            }

            def get_or_create(name: str, color: str = "#475569") -> int:
                if name in existing:
                    return existing[name]
                conn.execute(
                    "INSERT INTO tags (name, color) VALUES (?, ?)",
                    (name, color),
                )
                tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                existing[name] = tid
                stats["tags_added"] += 1
                return tid

            for entry in data.get("tags", []):
                name = (entry.get("name") or "").strip()
                if not name:
                    continue
                get_or_create(name, entry.get("color") or "#475569")

            for entry in data.get("cooccurrence", []):
                a_name = (entry.get("a") or "").strip()
                b_name = (entry.get("b") or "").strip()
                count = max(1, int(entry.get("count") or 1))
                if not a_name or not b_name or a_name == b_name:
                    continue
                a_id = get_or_create(a_name)
                b_id = get_or_create(b_name)
                a, b = (a_id, b_id) if a_id < b_id else (b_id, a_id)
                conn.execute(
                    """INSERT INTO tag_cooccurrence (tag_a_id, tag_b_id, count)
                       VALUES (?, ?, ?)
                       ON CONFLICT(tag_a_id, tag_b_id) DO UPDATE
                       SET count = count + excluded.count""",
                    (a, b, count),
                )
                stats["pairs_merged"] += 1

            # Record the import so this exact JSON can't be merged twice.
            if export_id:
                conn.execute(
                    """INSERT OR IGNORE INTO imported_pools
                       (export_id, tags_added, pairs_merged) VALUES (?, ?, ?)""",
                    (export_id, stats["tags_added"], stats["pairs_merged"]),
                )

            conn.commit()
        return stats

    def export_training_data(self) -> dict:
        """Export training data keyed by asset filename for portability.

        Includes:
        - cover_labels: chosen cover images per asset
        - tag_reviews: per-tag accept/reject decisions
        - tag_labels: detailed labeling sessions (original/accepted/rejected/added tags)
        - tag_cooccurrence: tag pairs that appear together
        """
        with self._db.connection() as conn:
            # Cover labels
            cover_rows = conn.execute(
                """SELECT a.filename, cl.image_name
                   FROM cover_labels_v2 cl JOIN assets a ON a.id = cl.asset_id
                   WHERE a.trash_date IS NULL"""
            ).fetchall()

            # Tag reviews (simple accept/reject per tag)
            review_rows = conn.execute(
                """SELECT a.filename, t.name, tr.accepted
                   FROM tag_reviews tr
                   JOIN assets a ON a.id = tr.asset_id
                   JOIN tags t ON t.id = tr.tag_id
                   WHERE a.trash_date IS NULL"""
            ).fetchall()

            # Tag labels (detailed labeling sessions)
            label_rows = conn.execute(
                """SELECT a.filename, tl.session_id, tl.original_tags,
                          tl.accepted_tags, tl.rejected_tags, tl.added_tags,
                          tl.genre_tag_id
                   FROM tag_labels tl JOIN assets a ON a.id = tl.asset_id
                   WHERE a.trash_date IS NULL"""
            ).fetchall()

            # Build tag ID to name map for resolving tag_labels
            tags = conn.execute("SELECT id, name FROM tags").fetchall()
            id_to_name = {r[0]: r[1] for r in tags}

            # Tag co-occurrence — full dump. Earlier versions filtered to
            # count >= 2 + LIMIT 500 which silently discarded crawler data
            # (where every pair starts at count == 1). The recipient merges
            # cumulatively, so passing the long tail along is exactly what
            # we want.
            cooc_rows = conn.execute(
                """SELECT t1.name, t2.name, tc.count
                   FROM tag_cooccurrence tc
                   JOIN tags t1 ON t1.id = tc.tag_a_id
                   JOIN tags t2 ON t2.id = tc.tag_b_id
                   ORDER BY tc.count DESC"""
            ).fetchall()

        def resolve_tag_ids(ids_str: str | None) -> list[str]:
            if not ids_str:
                return []
            return [id_to_name.get(int(tid), f"?{tid}") for tid in ids_str.split(",") if tid]

        return {
            "version": 2,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "cover_labels": [
                {"asset_filename": r[0], "image_name": r[1]} for r in cover_rows
            ],
            "tag_reviews": [
                {"asset_filename": r[0], "tag_name": r[1], "accepted": bool(r[2])}
                for r in review_rows
            ],
            "tag_labels": [
                {
                    "asset_filename": r[0],
                    "session_id": r[1],
                    "original_tags": resolve_tag_ids(r[2]),
                    "accepted_tags": resolve_tag_ids(r[3]),
                    "rejected_tags": resolve_tag_ids(r[4]),
                    "added_tags": resolve_tag_ids(r[5]),
                    "genre": id_to_name.get(r[6]) if r[6] else None,
                }
                for r in label_rows
            ],
            "tag_cooccurrence": [
                {"tag_a": r[0], "tag_b": r[1], "count": r[2]} for r in cooc_rows
            ],
        }

    def import_training_data(self, data: dict) -> dict:
        """Import training data, matching by asset filename.

        Supports both v1 and v2 formats. Returns stats dict.
        """
        stats = {"cover_labels": 0, "tag_reviews": 0, "tag_cooccurrence": 0, "skipped": 0}

        with self._db.write_connection() as conn:
            # Index assets by filename
            assets = conn.execute(
                "SELECT id, filename FROM assets WHERE trash_date IS NULL"
            ).fetchall()
            filename_to_id = {r[1]: r[0] for r in assets}

            # Index tags by name (and create missing ones)
            def get_or_create_tag(name: str) -> int | None:
                row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
                if row:
                    return row[0]
                try:
                    conn.execute(
                        "INSERT INTO tags (name, color) VALUES (?, ?)",
                        (name, "#6366f1")
                    )
                    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                except Exception:
                    return None

            # Import cover labels
            for entry in data.get("cover_labels", []):
                asset_id = filename_to_id.get(entry.get("asset_filename"))
                if asset_id is None:
                    stats["skipped"] += 1
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO cover_labels_v2 (asset_id, image_name) VALUES (?, ?)",
                    (asset_id, entry.get("image_name", ""))
                )
                stats["cover_labels"] += 1

            # Import tag reviews
            for entry in data.get("tag_reviews", []):
                asset_id = filename_to_id.get(entry.get("asset_filename"))
                if asset_id is None:
                    stats["skipped"] += 1
                    continue
                tag_id = get_or_create_tag(entry.get("tag_name", ""))
                if tag_id is None:
                    stats["skipped"] += 1
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO tag_reviews (asset_id, tag_id, accepted) VALUES (?, ?, ?)",
                    (asset_id, tag_id, 1 if entry.get("accepted", True) else 0)
                )
                stats["tag_reviews"] += 1

            # Import tag co-occurrence (v2+)
            for entry in data.get("tag_cooccurrence", []):
                tag_a = entry.get("tag_a")
                tag_b = entry.get("tag_b")
                count = entry.get("count", 1)
                if not tag_a or not tag_b:
                    continue
                tag_a_id = get_or_create_tag(tag_a)
                tag_b_id = get_or_create_tag(tag_b)
                if tag_a_id and tag_b_id:
                    a_id, b_id = min(tag_a_id, tag_b_id), max(tag_a_id, tag_b_id)
                    conn.execute(
                        """INSERT INTO tag_cooccurrence (tag_a_id, tag_b_id, count)
                           VALUES (?, ?, ?)
                           ON CONFLICT(tag_a_id, tag_b_id)
                           DO UPDATE SET count = count + excluded.count""",
                        (a_id, b_id, count)
                    )
                    stats["tag_cooccurrence"] += 1

            conn.commit()

        # Backwards compatible return
        return {
            "imported": stats["cover_labels"] + stats["tag_reviews"] + stats["tag_cooccurrence"],
            "skipped": stats["skipped"],
            **stats,
        }
