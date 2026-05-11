from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 4

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
    CREATE TABLE IF NOT EXISTS cover_labels (
        asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        image_name TEXT NOT NULL,
        chosen_at  REAL DEFAULT (strftime('%s', 'now')),
        PRIMARY KEY (asset_id, image_name)
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

        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,)
        )
        conn.commit()
