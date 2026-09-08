"""Everything this project asks TMDb for.

Two jobs. **Resolving titles** to IMDb ids and years: only Apple TV entries
need this, since Plex supplies GUIDs directly and its rows arrive already
identified. Titles are ambiguous -- "Dark Matter" is both a 2024 Apple TV+
series and a 2015 Syfy one -- so results are taken in TMDb's popularity order,
which puts the intended match first in practice. Every resolution is cached in
the titles table, and a row can be pinned with locked=1 so a bad match can be
corrected by hand and never overwritten.

And **season lengths**: how many episodes each season of a watched show has.
See `refresh_seasons` at the foot of this file for why the log cannot be read
without them.
"""
import logging
from datetime import datetime, timezone

import requests

from . import config, db
from .grouping import normalize

log = logging.getLogger("watchlog.enrich")

SEARCH = "https://api.themoviedb.org/3/search/multi"
EXTERNAL = "https://api.themoviedb.org/3/{kind}/{id}/external_ids"
FIND = "https://api.themoviedb.org/3/find/{imdb_id}"
SHOW = "https://api.themoviedb.org/3/tv/{tmdb_id}"
TIMEOUT = 20


def _search(title):
    response = requests.get(
        SEARCH,
        params={"api_key": config.TMDB_API_KEY, "query": title},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    for item in response.json().get("results", []):
        if item.get("media_type") in ("tv", "movie"):
            return item
    return None


def _imdb_id(kind, tmdb_id):
    response = requests.get(
        EXTERNAL.format(kind=kind, id=tmdb_id),
        params={"api_key": config.TMDB_API_KEY},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json().get("imdb_id")


def resolve(title):
    """(imdb_id, tmdb_id, year), cached. Returns (None, None, None) on no match."""
    key = normalize(title)

    with db.connect() as conn:
        cached = conn.execute(
            "SELECT imdb_id, tmdb_id, year FROM titles WHERE norm_title = ?", (key,)
        ).fetchone()
    if cached:
        return cached["imdb_id"], cached["tmdb_id"], cached["year"]

    imdb = tmdb = year = None
    try:
        match = _search(title)
        if match:
            kind = match["media_type"]
            tmdb = str(match["id"])
            date = match.get("first_air_date") or match.get("release_date") or ""
            year = int(date[:4]) if date[:4].isdigit() else None
            imdb = _imdb_id(kind, match["id"])
            log.info("resolved %r -> %s (%s) via %s", title, imdb, year, kind)
        else:
            log.warning("no TMDb match for %r", title)
    except Exception as exc:
        log.warning("TMDb lookup failed for %r: %s", title, exc)
        return None, None, None          # don't cache a transport failure

    with db.connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO titles
                   (norm_title, imdb_id, tmdb_id, year, locked, updated_at)
               VALUES (?, ?, ?, ?,
                       COALESCE((SELECT locked FROM titles WHERE norm_title = ?), 0),
                       ?)""",
            (key, imdb, tmdb, year, key, datetime.now(timezone.utc).isoformat()),
        )
    return imdb, tmdb, year


def enrich_pending(limit=200):
    """Fill in ids for events that arrived without them."""
    if not config.TMDB_API_KEY or config.TMDB_API_KEY.startswith("TODO"):
        log.info("TMDB_API_KEY not set; skipping enrichment")
        return 0

    with db.connect() as conn:
        pending = conn.execute(
            """SELECT DISTINCT title FROM events
               WHERE imdb_id IS NULL AND hidden = 0 LIMIT ?""",
            (limit,),
        ).fetchall()

    updated = 0
    for row in pending:
        title = row["title"]
        imdb, tmdb, year = resolve(title)
        if not imdb and not year:
            continue
        with db.connect() as conn:
            cursor = conn.execute(
                """UPDATE events
                      SET imdb_id = COALESCE(imdb_id, ?),
                          tmdb_id = COALESCE(tmdb_id, ?),
                          year    = COALESCE(year, ?)
                    WHERE title = ? AND imdb_id IS NULL""",
                (imdb, tmdb, year, title),
            )
            updated += cursor.rowcount

    log.info("enriched %d events across %d titles", updated, len(pending))
    return updated


# --- season lengths ---------------------------------------------------------


def _tv_id(imdb_id):
    """TMDb's id for a show, from its IMDb one."""
    response = requests.get(
        FIND.format(imdb_id=imdb_id),
        params={"api_key": config.TMDB_API_KEY, "external_source": "imdb_id"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    results = response.json().get("tv_results") or []
    return results[0]["id"] if results else None


def _seasons(tmdb_id):
    """({season_number: episode_count}, the season still airing or None).

    Season 0 is TMDb's bucket for specials. It is dropped: nobody means the
    Christmas special when they ask whether they finished a season, and
    counting it makes every complete season look incomplete.

    The second value is what stops the log calling someone a quitter for being
    up to date. A season part-way through its run has an episode_count covering
    episodes that have not aired yet, so "7 of 12, quiet for two months" is what
    both abandoning a show and waiting for it look like.
    """
    response = requests.get(
        SHOW.format(tmdb_id=tmdb_id),
        params={"api_key": config.TMDB_API_KEY},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    counts = {
        season["season_number"]: season.get("episode_count")
        for season in body.get("seasons", [])
        if season.get("season_number") and season.get("episode_count")
    }
    upcoming = body.get("next_episode_to_air") or {}
    return counts, upcoming.get("season_number")


def _stale(checked_at, now):
    if not checked_at:
        return True
    try:
        age = now - datetime.fromisoformat(checked_at)
    except ValueError:
        return True
    return age.days >= config.SEASONS_REFRESH_DAYS


def refresh_seasons(limit=100):
    """Learn how long each watched show's seasons are.

    Without this the log cannot tell finishing something from giving up on it.
    "Eight episodes then nothing" is a complete season of one show and a walkout
    halfway through another, and those are opposite facts about the same number
    -- which makes them the difference between a favourite and a write-off.

    Re-read rather than fetched once: a season still airing gains episodes as
    they are announced, so a count cached in week one is wrong by the finale.
    """
    if not config.TMDB_API_KEY or config.TMDB_API_KEY.startswith("TODO"):
        log.info("TMDB_API_KEY not set; skipping season lengths")
        return 0

    now = datetime.now(timezone.utc)
    checked = db.seasons_checked_at()

    with db.connect() as conn:
        shows = conn.execute(
            """SELECT DISTINCT imdb_id FROM events
                WHERE media_type = 'episode' AND hidden = 0
                  AND imdb_id IS NOT NULL LIMIT ?""",
            (limit,),
        ).fetchall()

    learned = 0
    for row in shows:
        imdb_id = row["imdb_id"]
        if not _stale(checked.get(imdb_id), now):
            continue
        try:
            tmdb_id = _tv_id(imdb_id)
            if not tmdb_id:
                log.warning("no TMDb show for %s", imdb_id)
                continue
            counts, airing = _seasons(tmdb_id)
        except Exception as exc:
            # One show failing is not a reason to abandon the rest, and a
            # transport failure must not be cached as "this show has no seasons".
            log.warning("season lookup failed for %s: %s", imdb_id, exc)
            continue
        if counts:
            db.set_season_lengths(imdb_id, counts, airing_season=airing)
            learned += 1

    log.info("refreshed season lengths for %d show(s)", learned)
    return learned


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db.init()
    enrich_pending()
    refresh_seasons()
