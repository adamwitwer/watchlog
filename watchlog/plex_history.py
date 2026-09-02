"""Backfill from the Plex server's own watch history.

Plex keeps every play it ever recorded, with real timestamps, so the page can
start populated instead of empty. History rows are sparse -- no GUIDs -- so the
IMDb id comes from a second metadata fetch per distinct show or film, cached.
"""
import logging

import requests

from . import config, db
from .grouping import normalize

log = logging.getLogger("watchlog.history")

PAGE_SIZE = 200
TIMEOUT = 30


def _get(path, **params):
    params["X-Plex-Token"] = config.PLEX_TOKEN
    response = requests.get(
        f"{config.PLEX_SERVER_URL.rstrip('/')}{path}",
        params=params,
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json().get("MediaContainer", {})


def account_id(title):
    """Plex history covers every user on the server. Find ours."""
    for account in _get("/accounts").get("Account", []):
        if account.get("name") == title:
            return int(account["id"])
    return None


def _guid_from_metadata(rating_key, cache):
    """imdb/tmdb ids for a show or film, fetched once per key."""
    if rating_key in cache:
        return cache[rating_key]

    imdb = tmdb = None
    try:
        items = _get(f"/library/metadata/{rating_key}").get("Metadata", [])
        for entry in items[:1]:
            for guid in entry.get("Guid", []):
                value = guid.get("id", "")
                if value.startswith("imdb://"):
                    imdb = value.split("://", 1)[1]
                elif value.startswith("tmdb://"):
                    tmdb = value.split("://", 1)[1]
    except Exception as exc:
        log.warning("metadata fetch failed for %s: %s", rating_key, exc)

    cache[rating_key] = (imdb, tmdb)
    return imdb, tmdb


def fetch_history(account=None):
    """Every history row, oldest first, paginated."""
    start = 0
    while True:
        params = {
            "X-Plex-Container-Start": start,
            "X-Plex-Container-Size": PAGE_SIZE,
            "sort": "viewedAt:asc",
        }
        if account is not None:
            params["accountID"] = account

        container = _get("/status/sessions/history/all", **params)
        rows = container.get("Metadata", [])
        if not rows:
            return
        for row in rows:
            yield row
        start += len(rows)
        if start >= int(container.get("totalSize", 0)):
            return


def to_event(row, cache):
    kind = row.get("type")
    if kind not in ("movie", "episode"):
        return None

    viewed_at = row.get("viewedAt")
    if not viewed_at:
        return None

    from datetime import datetime, timezone
    watched_at = datetime.fromtimestamp(int(viewed_at), tz=timezone.utc).isoformat()

    if kind == "movie":
        title = row.get("title") or "Unknown"
        season = episode = None
        episode_title = None
        # A film's own metadata carries its ids.
        key = row.get("ratingKey")
    else:
        title = row.get("grandparentTitle") or "Unknown"
        season = row.get("parentIndex")
        episode = row.get("index")
        episode_title = row.get("title")
        # Link the show, not the individual episode.
        key = row.get("grandparentRatingKey")

    imdb = tmdb = None
    if key:
        imdb, tmdb = _guid_from_metadata(key, cache)

    return {
        "watched_at": watched_at,
        "source": "plex",
        "service": "Plex",
        "media_type": kind,
        "title": title,
        "episode_title": episode_title,
        "year": row.get("year"),
        "season": season,
        "episode": episode,
        "imdb_id": imdb,
        "tmdb_id": tmdb,
        "dedup_key": f"{normalize(title)}|{kind}|{season}|{episode}",
        "hidden": 0,
        "raw": None,
    }


def backfill(dry_run=False):
    if not config.PLEX_TOKEN:
        raise RuntimeError("PLEX_TOKEN is not set in .env")

    db.init()
    account = account_id(config.PLEX_ACCOUNT_TITLE)
    log.info("account %r -> id %s", config.PLEX_ACCOUNT_TITLE, account)
    if account is None:
        log.warning("account not matched; importing history for ALL users")

    cache = {}
    seen = inserted = skipped = 0
    for row in fetch_history(account):
        seen += 1
        event = to_event(row, cache)
        if event is None:
            continue
        if dry_run:
            inserted += 1
            if inserted <= 15:
                log.info("would import: %s %s S%sE%s (%s)", event["watched_at"][:10],
                         event["title"], event["season"], event["episode"], event["imdb_id"])
            continue
        if db.insert_event(event) is None:
            skipped += 1
        else:
            inserted += 1

    log.info("history rows seen: %d, imported: %d, duplicates skipped: %d",
             seen, inserted, skipped)
    return inserted


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    backfill(dry_run="--dry-run" in sys.argv)
