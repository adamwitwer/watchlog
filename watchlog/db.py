"""SQLite storage.

One row per watch event. Grouping into what the page displays happens at render
time, not here, so the grouping rules can change without touching the record.
"""
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    watched_at      TEXT    NOT NULL,          -- ISO8601 UTC
    source          TEXT    NOT NULL,          -- plex | appletv
    service         TEXT    NOT NULL,          -- Plex | Apple TV+ | ...
    media_type      TEXT    NOT NULL,          -- movie | episode
    title           TEXT    NOT NULL,          -- movie title, or series name
    episode_title   TEXT,
    year            INTEGER,
    season          INTEGER,
    episode         INTEGER,
    imdb_id         TEXT,
    tmdb_id         TEXT,
    dedup_key       TEXT    NOT NULL,
    hidden          INTEGER NOT NULL DEFAULT 0,
    raw             TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_watched  ON events(watched_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_dedup    ON events(dedup_key, watched_at);

-- Resolved metadata, cached so each show is looked up once. Manual overrides
-- live here too: set locked=1 and the enricher will leave the row alone.
CREATE TABLE IF NOT EXISTS titles (
    norm_title  TEXT PRIMARY KEY,
    imdb_id     TEXT,
    tmdb_id     TEXT,
    year        INTEGER,
    locked      INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT
);
"""


@contextmanager
def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)


def insert_event(event):
    """Insert unless an equivalent event was already recorded nearby in time.

    Returns the new row id, or None if this was a duplicate.
    """
    with connect() as conn:
        existing = conn.execute(
            """SELECT id FROM events
               WHERE dedup_key = ?
                 AND ABS(julianday(watched_at) - julianday(?)) * 24 < ?""",
            (event["dedup_key"], event["watched_at"], config.DEDUP_WINDOW_HOURS),
        ).fetchone()
        if existing:
            return None

        columns = ", ".join(event)
        placeholders = ", ".join("?" for _ in event)
        cursor = conn.execute(
            f"INSERT INTO events ({columns}) VALUES ({placeholders})",
            list(event.values()),
        )
        return cursor.lastrowid


def visible_events():
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM events WHERE hidden = 0 ORDER BY watched_at DESC"
        ).fetchall()


def recent_events(limit=50):
    """Everything, hidden included -- this is what the admin page lists."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM events ORDER BY watched_at DESC LIMIT ?", (limit,)
        ).fetchall()


def set_hidden(event_ids, hidden=True):
    with connect() as conn:
        conn.executemany(
            "UPDATE events SET hidden = ? WHERE id = ?",
            [(1 if hidden else 0, i) for i in event_ids],
        )
