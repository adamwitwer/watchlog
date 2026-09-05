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

-- Small key/value scratch space: the heartbeats and last-error records that the
-- admin page reads to say whether each moving part is actually running. Every
-- component here is silent when healthy, so silence proves nothing and these
-- rows are the only difference between working and wedged.
CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT
);

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


def set_meta(key, value):
    from datetime import datetime, timezone
    with connect() as conn:
        conn.execute(
            """INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                              updated_at = excluded.updated_at""",
            (key, value, datetime.now(timezone.utc).isoformat()),
        )


def get_meta(key, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def is_duplicate(event):
    """Has an equivalent event already been recorded nearby in time?"""
    with connect() as conn:
        return conn.execute(
            """SELECT id FROM events
               WHERE dedup_key = ?
                 AND ABS(julianday(watched_at) - julianday(?)) * 24 < ?""",
            (event["dedup_key"], event["watched_at"], config.DEDUP_WINDOW_HOURS),
        ).fetchone() is not None


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


def update_details(event_id, season, episode, episode_title):
    """Set the fields a sensor couldn't supply.

    Only ever called from the admin form. The enricher writes imdb_id, tmdb_id
    and year and never these three, so a hand-typed correction is not at risk of
    being overwritten later. dedup_key is left alone too -- it is what stops the
    same night being recorded twice, and rewriting it here would break that.
    """
    with connect() as conn:
        conn.execute(
            """UPDATE events
                  SET season = ?, episode = ?, episode_title = ?
                WHERE id = ?""",
            (season, episode, episode_title, event_id),
        )


def event_titles():
    """Every distinct title in the log, hidden rows included."""
    with connect() as conn:
        return [r["title"] for r in
                conn.execute("SELECT DISTINCT title FROM events").fetchall()]


def set_title_match(norm_title, imdb_id, tmdb_id, year):
    """Pin what a title resolves to, by hand.

    locked=1 is what the schema has always had a column for and nothing ever
    set. The enricher keys its cache on the normalised title, so writing here
    is what stops a corrected show from being re-resolved back to the wrong one
    the next time an entry for it arrives.
    """
    from datetime import datetime, timezone
    with connect() as conn:
        conn.execute(
            """INSERT INTO titles
                   (norm_title, imdb_id, tmdb_id, year, locked, updated_at)
               VALUES (?, ?, ?, ?, 1, ?)
               ON CONFLICT(norm_title) DO UPDATE SET
                   imdb_id = excluded.imdb_id, tmdb_id = excluded.tmdb_id,
                   year = excluded.year, locked = 1,
                   updated_at = excluded.updated_at""",
            (norm_title, imdb_id, tmdb_id, year,
             datetime.now(timezone.utc).isoformat()),
        )


def update_identity(titles, imdb_id, tmdb_id, year):
    """Point existing events at a corrected match.

    Unconditional, unlike the enricher's COALESCE: the whole reason to be here
    is that the ids already on these rows are wrong. Returns the row count.
    """
    if not titles:
        return 0
    with connect() as conn:
        placeholders = ", ".join("?" for _ in titles)
        cursor = conn.execute(
            f"""UPDATE events SET imdb_id = ?, tmdb_id = ?, year = ?
                 WHERE title IN ({placeholders})""",
            [imdb_id, tmdb_id, year, *titles],
        )
        return cursor.rowcount


def set_hidden(event_ids, hidden=True):
    with connect() as conn:
        conn.executemany(
            "UPDATE events SET hidden = ? WHERE id = ?",
            [(1 if hidden else 0, i) for i in event_ids],
        )
