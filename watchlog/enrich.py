"""Resolve titles to IMDb ids and years via TMDb.

Only Apple TV entries need this. Plex supplies GUIDs directly, so its rows
arrive already identified.

Titles are ambiguous -- "Dark Matter" is both a 2024 Apple TV+ series and a
2015 Syfy one -- so results are taken in TMDb's popularity order, which puts
the intended match first in practice. Every resolution is cached in the titles
table, and a row can be pinned with locked=1 so a bad match can be corrected
by hand and never overwritten.
"""
import logging
from datetime import datetime, timezone

import requests

from . import config, db
from .grouping import normalize

log = logging.getLogger("watchlog.enrich")

SEARCH = "https://api.themoviedb.org/3/search/multi"
EXTERNAL = "https://api.themoviedb.org/3/{kind}/{id}/external_ids"
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db.init()
    enrich_pending()
