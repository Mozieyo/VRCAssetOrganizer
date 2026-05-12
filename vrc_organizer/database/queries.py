from __future__ import annotations

import re
import sqlite3
import unicodedata
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
        """Reset thumb_state to 'pending' for assets with cover_labels.

        After cover training, this lets ThumbWorker regenerate thumbnails
        using the user-labeled cover image instead of the heuristic pick.
        """
        with self._db.write_connection() as conn:
            cur = conn.execute(
                "UPDATE assets SET thumb_state = 'pending' "
                "WHERE id IN (SELECT asset_id FROM cover_labels) "
                "AND thumb_state IN ('ready', 'error') "
                "AND trash_date IS NULL"
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

            # OR group: asset must have AT LEAST ONE of these tags
            if or_tag_ids:
                placeholders = ",".join("?" for _ in or_tag_ids)
                query += f" AND id IN (SELECT asset_id FROM asset_tags WHERE tag_id IN ({placeholders}))"
                params.extend(or_tag_ids)

            # AND tags: asset must have ALL of these tags
            if and_tag_ids:
                for tid in and_tag_ids:
                    query += " AND id IN (SELECT asset_id FROM asset_tags WHERE tag_id = ?)"
                    params.append(tid)

            if search_query:
                query += " AND rowid IN (SELECT rowid FROM assets_fts WHERE assets_fts MATCH ?)"
                params.append(self._fts5_prefix_query(search_query))

            safe_sort = sort if sort in self._VALID_SORTS else "date_added DESC"
            query += f" ORDER BY {safe_sort} LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            return [asset_from_row(r) for r in rows]

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

    def delete_tag(self, tag_id: int):
        with self._db.write_connection() as conn:
            conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            conn.commit()

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

    # ── Cover Label Training ─────────────────────────────────

    def get_trainable_assets(self) -> list[Asset]:
        """Return assets with image entries in scan_results but no cover_label yet."""
        with self._db.connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT a.* FROM assets a
                   INNER JOIN scan_results sr ON a.id = sr.asset_id
                   WHERE sr.entry_type = 'image'
                     AND a.trash_date IS NULL
                     AND a.id NOT IN (SELECT asset_id FROM cover_labels)
                   ORDER BY a.date_added DESC
                   LIMIT 200"""
            ).fetchall()
            return [asset_from_row(r) for r in rows]

    def get_unreviewed_tagged_assets(self, limit: int = 50) -> list[Asset]:
        """Return recently imported assets with tags that haven't been reviewed yet."""
        with self._db.connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT a.* FROM assets a
                   INNER JOIN asset_tags at ON a.id = at.asset_id
                   WHERE a.trash_date IS NULL
                     AND a.id NOT IN (SELECT DISTINCT asset_id FROM tag_reviews)
                   GROUP BY a.id ORDER BY a.date_added DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            return [asset_from_row(r) for r in rows]

    def get_cover_label(self, asset_id: int) -> str | None:
        """Return the labeled cover image name for an asset, or None."""
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT image_name FROM cover_labels WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            return row[0] if row else None

    def save_cover_label(self, asset_id: int, image_name: str):
        with self._db.write_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cover_labels (asset_id, image_name) VALUES (?, ?)",
                (asset_id, image_name)
            )
            conn.commit()

    def save_tag_review(self, asset_id: int, tag_id: int, accepted: bool):
        with self._db.write_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tag_reviews (asset_id, tag_id, accepted) "
                "VALUES (?, ?, ?)",
                (asset_id, tag_id, 1 if accepted else 0)
            )
            conn.commit()
