"""SQLite storage.

One row per watch event. Grouping into what the page displays happens at render
time, not here, so the grouping rules can change without touching the record.
"""
import logging
import sqlite3
from contextlib import contextmanager

from . import config

log = logging.getLogger("watchlog.db")

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

-- How many episodes each season of a show actually has, from TMDb. Without it
-- "eight episodes then nothing" is unreadable: it could be a season finished
-- or a show abandoned halfway, and those are opposite facts about the same
-- number. Keyed on the IMDb id rather than the title, which is neither stable
-- nor unique.
CREATE TABLE IF NOT EXISTS seasons (
    imdb_id       TEXT    NOT NULL,
    season        INTEGER NOT NULL,
    episode_count INTEGER,
    -- Whether TMDb still has an unaired episode scheduled for this season.
    -- Without it a season watched to the last released episode is
    -- indistinguishable from one walked out on, and the log would accuse him
    -- of abandoning a show he is up to date with.
    airing        INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT,
    PRIMARY KEY (imdb_id, season)
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


# Columns added to a table that already exists somewhere. CREATE TABLE IF NOT
# EXISTS does nothing at all when the table is present, so a column added later
# has to be asked for separately -- otherwise a database written by an older
# revision keeps the old shape and every query naming the column fails at the
# moment it runs, which is long after the deploy that looked fine.
ADDED_COLUMNS = [
    ("seasons", "airing", "INTEGER NOT NULL DEFAULT 0"),
]


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, column, spec in ADDED_COLUMNS:
            present = {r["name"] for r in
                       conn.execute(f"PRAGMA table_info({table})")}
            if present and column not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
                log.info("added %s.%s to an existing database", table, column)


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


def titled_events(media_type=None):
    """Distinct (title, media_type) pairs, hidden rows included.

    The pair, not the title alone: a normalised title is not unique across
    media types. "Furious" is a 2026 series and "The Furious" a 2026 film, and
    normalize() strips the leading article that separates them.
    """
    sql = "SELECT DISTINCT title, media_type FROM events"
    args = []
    if media_type:
        sql += " WHERE media_type = ?"
        args.append(media_type)
    with connect() as conn:
        return [(r["title"], r["media_type"]) for r in conn.execute(sql, args)]


def rename_title(titles, new_title):
    """Point a set of spellings at one canonical title.

    Renaming is not cosmetic. A show spelled two ways is two shows to anything
    that counts them -- the page prints both, and a ranking splits its episodes
    between them. The caller decides which spellings belong together; this only
    writes. Returns the row count.
    """
    if not titles:
        return 0
    with connect() as conn:
        placeholders = ", ".join("?" for _ in titles)
        cursor = conn.execute(
            f"UPDATE events SET title = ? WHERE title IN ({placeholders})",
            [new_title, *titles],
        )
        return cursor.rowcount


def forget_title(norm_title):
    """Drop a cached resolution whose key no longer matches any event.

    After a rename the old normalised key is orphaned. Left behind it is a
    mapping from a spelling nothing uses any more, which is harmless until
    something spells it that way again and gets the stale answer.
    """
    with connect() as conn:
        conn.execute("DELETE FROM titles WHERE norm_title = ?", (norm_title,))


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


def season_lengths():
    """{(imdb_id, season): episode_count} for everything already looked up."""
    with connect() as conn:
        return {(r["imdb_id"], r["season"]): r["episode_count"]
                for r in conn.execute(
                    "SELECT imdb_id, season, episode_count FROM seasons")}


def set_season_lengths(imdb_id, counts, airing_season=None):
    """Record how long each season of one show is. counts: {season: episodes}.

    airing_season is the one TMDb still has an unaired episode scheduled for,
    if any -- the difference between "up to date" and "gave up".
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.executemany(
            """INSERT INTO seasons
                   (imdb_id, season, episode_count, airing, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(imdb_id, season) DO UPDATE SET
                   episode_count = excluded.episode_count,
                   airing = excluded.airing,
                   updated_at = excluded.updated_at""",
            [(imdb_id, season, count, 1 if season == airing_season else 0, now)
             for season, count in counts.items()],
        )


def airing_seasons():
    """{(imdb_id, season)} that still have an episode yet to air."""
    with connect() as conn:
        return {(r["imdb_id"], r["season"]) for r in conn.execute(
            "SELECT imdb_id, season FROM seasons WHERE airing = 1")}


def seasons_checked_at():
    """{imdb_id: the newest updated_at across its seasons}, for the refresh check."""
    with connect() as conn:
        return {r["imdb_id"]: r["checked"] for r in conn.execute(
            "SELECT imdb_id, MAX(updated_at) AS checked FROM seasons GROUP BY imdb_id")}


def first_event_date():
    """The date of the earliest event, or None when the log is empty."""
    with connect() as conn:
        row = conn.execute("SELECT MIN(watched_at) AS first FROM events").fetchone()
    return row["first"][:10] if row and row["first"] else None


def title_spellings():
    """{normalised title: the spelling the log already uses}, most common wins.

    Plex sends its own metadata title on every row, so an incoming event will
    happily reintroduce a spelling that was corrected by hand -- the rename
    fixes what is stored, and the next import puts it straight back. This is
    how something arriving later learns what the log already settled on.
    """
    from collections import Counter, defaultdict
    from .grouping import normalize
    counts = defaultdict(Counter)
    with connect() as conn:
        for row in conn.execute("SELECT title, COUNT(*) n FROM events GROUP BY title"):
            counts[normalize(row["title"])][row["title"]] += row["n"]
    return {key: names.most_common(1)[0][0] for key, names in counts.items()}


def dedup_keys():
    """Every dedup key in the log, hidden rows included.

    Hidden ones matter most: they are the entries that were deliberately
    deleted, and a sweep that re-imported them would undo that quietly and
    keep doing it every day.
    """
    with connect() as conn:
        return {r["dedup_key"] for r in
                conn.execute("SELECT DISTINCT dedup_key FROM events")}


def set_hidden(event_ids, hidden=True):
    with connect() as conn:
        conn.executemany(
            "UPDATE events SET hidden = ? WHERE id = ?",
            [(1 if hidden else 0, i) for i in event_ids],
        )
