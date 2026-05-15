from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 10

CREATE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assets (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        filename    TEXT NOT NULL,
        filepath    TEXT NOT NULL UNIQUE,
        filetype    TEXT NOT NULL,
        file_size   INTEGER NOT NULL,
        mod_time    REAL NOT NULL,
        date_added  REAL NOT NULL DEFAULT (strftime('%s', 'now')),
        thumbnail   TEXT,
        thumb_state TEXT DEFAULT 'pending',
        notes       TEXT DEFAULT '',
        scan_state  TEXT DEFAULT 'pending'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tags (
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        name  TEXT NOT NULL UNIQUE,
        color TEXT DEFAULT '#6366f1'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_tags (
        asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        PRIMARY KEY (asset_id, tag_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scan_results (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_id    INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        entry_name  TEXT NOT NULL,
        entry_type  TEXT NOT NULL,
        entry_size  INTEGER,
        UNIQUE(asset_id, entry_name)
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
        filename, notes, content='assets', content_rowid='id'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS assets_ai AFTER INSERT ON assets BEGIN
        INSERT INTO assets_fts(rowid, filename, notes)
        VALUES (new.id, new.filename, new.notes);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS assets_ad AFTER DELETE ON assets BEGIN
        INSERT INTO assets_fts(assets_fts, rowid, filename, notes)
        VALUES ('delete', old.id, old.filename, old.notes);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS assets_au AFTER UPDATE ON assets BEGIN
        INSERT INTO assets_fts(assets_fts, rowid, filename, notes)
        VALUES ('delete', old.id, old.filename, old.notes);
        INSERT INTO assets_fts(rowid, filename, notes)
        VALUES (new.id, new.filename, new.notes);
    END
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at REAL DEFAULT (strftime('%s', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tag_cooccurrence (
        tag_a_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        tag_b_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        count    INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (tag_a_id, tag_b_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS imported_pools (
        export_id    TEXT PRIMARY KEY,
        imported_at  REAL NOT NULL DEFAULT (strftime('%s', 'now')),
        tags_added   INTEGER NOT NULL DEFAULT 0,
        pairs_merged INTEGER NOT NULL DEFAULT 0
    )
    """,
]

# Covering indexes for common queries
INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_assets_filetype ON assets(filetype)",
    "CREATE INDEX IF NOT EXISTS idx_assets_date_added ON assets(date_added DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assets_thumb_state ON assets(thumb_state)",
    "CREATE INDEX IF NOT EXISTS idx_asset_tags_asset ON asset_tags(asset_id)",
    "CREATE INDEX IF NOT EXISTS idx_asset_tags_tag ON asset_tags(tag_id)",
    "CREATE INDEX IF NOT EXISTS idx_scan_results_asset ON scan_results(asset_id)",
    "CREATE INDEX IF NOT EXISTS idx_tag_cooccur_a ON tag_cooccurrence(tag_a_id)",
    "CREATE INDEX IF NOT EXISTS idx_tag_cooccur_b ON tag_cooccurrence(tag_b_id)",
]


def init_schema(conn: sqlite3.Connection):
    try:
        current = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0] or 0
    except sqlite3.OperationalError:
        current = 0

    if current < SCHEMA_VERSION:
        for stmt in CREATE_STATEMENTS + INDEX_STATEMENTS:
            conn.execute(stmt)

        # Migration: v2 → v3 — add trash_date for soft-delete
        if current < 3:
            try:
                conn.execute("ALTER TABLE assets ADD COLUMN trash_date REAL")
            except sqlite3.OperationalError:
                pass  # column already exists

        # Migration: v3 → v4 — cover_labels table
        if current < 4:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS cover_labels (
                    asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    image_name TEXT NOT NULL,
                    chosen_at  REAL DEFAULT (strftime('%s', 'now')),
                    PRIMARY KEY (asset_id, image_name)
                )"""
            )

        # Migration: v4 → v5 — tag_reviews table
        if current < 5:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS tag_reviews (
                    asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    accepted   INTEGER NOT NULL DEFAULT 1,
                    reviewed_at REAL DEFAULT (strftime('%s', 'now')),
                    PRIMARY KEY (asset_id, tag_id)
                )"""
            )

        # Migration: v5 → v6 — tag_cooccurrence table
        if current < 6:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS tag_cooccurrence (
                    tag_a_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    tag_b_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    count    INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (tag_a_id, tag_b_id)
                )"""
            )

        # Migration: v6 → v7 — labeling tables for ML training
        if current < 7:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS cover_labels_v2 (
                    asset_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
                    image_name TEXT NOT NULL,
                    image_width INTEGER,
                    image_height INTEGER,
                    archive_depth INTEGER,
                    filename_score INTEGER,
                    images_shown INTEGER,
                    chosen_at REAL DEFAULT (strftime('%s', 'now'))
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS tag_labels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL,
                    original_tags TEXT,
                    accepted_tags TEXT,
                    rejected_tags TEXT,
                    added_tags TEXT,
                    genre_tag_id INTEGER REFERENCES tags(id),
                    labeled_at REAL DEFAULT (strftime('%s', 'now')),
                    UNIQUE(asset_id, session_id)
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_labels_asset ON tag_labels(asset_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_labels_session ON tag_labels(session_id)")
            conn.execute(
                """INSERT OR IGNORE INTO cover_labels_v2 (asset_id, image_name)
                   SELECT asset_id, image_name FROM cover_labels"""
            )

        # Migration: v7 → v8 — formerly dropped+recreated cover_labels_v2 and
        # tag_labels to "remove timing columns", but the new schemas were
        # identical to v7 so the only effect was data loss. The block has been
        # removed; v8 is functionally identical to v7 and v8→v9 recovers any
        # labels that survived in the legacy `cover_labels` table.

        # Migration: v8 → v9 — collapse to a single cover label table.
        # Backfills `cover_labels_v2` from the legacy `cover_labels` (recovering
        # rows that were destroyed by the old v7→v8 bug), then drops the legacy
        # table so save/get/reset paths only ever touch one table.
        if current < 9:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO cover_labels_v2 (asset_id, image_name)
                       SELECT asset_id, image_name FROM cover_labels"""
                )
                conn.execute("DROP TABLE cover_labels")
            except sqlite3.OperationalError:
                # legacy table doesn't exist on this install — nothing to migrate
                pass

        # Migration: v9 → v10 — track which shared-tag-pool exports have
        # already been imported so re-importing the same JSON 100 times
        # doesn't multiply co-occurrence counts 100×.
        if current < 10:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS imported_pools (
                    export_id TEXT PRIMARY KEY,
                    imported_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                    tags_added INTEGER NOT NULL DEFAULT 0,
                    pairs_merged INTEGER NOT NULL DEFAULT 0
                )"""
            )

        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,)
        )
        conn.commit()
