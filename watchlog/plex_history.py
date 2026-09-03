"""Import from the Plex server's own watch history.

Two jobs, same machinery. `backfill` walks all of history once so the page can
start populated instead of empty. `reconcile` re-walks the last few days on a
timer, and exists because the webhook cannot be trusted to stay alive: PMS
fetches its hook list from plex.tv exactly once, at startup, and if that request
loses a race with DNS after a reboot it silently delivers to zero hooks until
the next restart. History is the server's own record and is never wrong, so a
periodic pass over it closes any gap without anyone noticing one opened.

History rows are sparse -- no GUIDs -- so the IMDb id comes from a second
metadata fetch per distinct show or film, cached.
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


def _rating_key(row):
    """The key to look metadata up by: the series for an episode, the film itself
    for a movie.

    History rows carry grandparentKey ('/library/metadata/123') but NOT
    grandparentRatingKey, so the series key has to be parsed out of the path.
    """
    if row.get("type") == "episode":
        parent = row.get("grandparentKey") or ""
        return parent.rstrip("/").split("/")[-1] or None
    return row.get("ratingKey")


def _metadata(rating_key, cache):
    """(imdb, tmdb, year) for a show or film, fetched once per key.

    History rows have no year either, so it comes from here as well -- and for
    an episode that correctly yields the series' year rather than the episode's.
    """
    if rating_key in cache:
        return cache[rating_key]

    imdb = tmdb = year = None
    try:
        items = _get(f"/library/metadata/{rating_key}").get("Metadata", [])
        for entry in items[:1]:
            year = entry.get("year")
            for guid in entry.get("Guid", []):
                value = guid.get("id", "")
                if value.startswith("imdb://"):
                    imdb = value.split("://", 1)[1]
                elif value.startswith("tmdb://"):
                    tmdb = value.split("://", 1)[1]
    except Exception as exc:
        log.warning("metadata fetch failed for %s: %s", rating_key, exc)

    cache[rating_key] = (imdb, tmdb, year)
    return imdb, tmdb, year


def fetch_history(account=None, since=None):
    """History rows, paginated.

    With no `since`, every row oldest-first. With a `since` epoch, newest-first
    and stopping as soon as the rows get older than the cutoff -- a reconcile
    pass reads one page, not the whole history.
    """
    start = 0
    while True:
        params = {
            "X-Plex-Container-Start": start,
            "X-Plex-Container-Size": PAGE_SIZE,
            "sort": "viewedAt:desc" if since else "viewedAt:asc",
        }
        if account is not None:
            params["accountID"] = account

        container = _get("/status/sessions/history/all", **params)
        rows = container.get("Metadata", [])
        if not rows:
            return
        for row in rows:
            if since and int(row.get("viewedAt") or 0) < since:
                return
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
    else:
        title = row.get("grandparentTitle") or "Unknown"
        season = row.get("parentIndex")
        episode = row.get("index")
        episode_title = row.get("title")

    imdb = tmdb = year = None
    key = _rating_key(row)
    if key:
        imdb, tmdb, year = _metadata(key, cache)

    return {
        "watched_at": watched_at,
        "source": "plex",
        "service": "Plex",
        "media_type": kind,
        "title": title,
        "episode_title": episode_title,
        "year": year,
        "season": season,
        "episode": episode,
        "imdb_id": imdb,
        "tmdb_id": tmdb,
        "dedup_key": f"{normalize(title)}|{kind}|{season}|{episode}",
        "hidden": 0,
        "raw": None,
    }


def _import(rows, dry_run=False):
    """Feed history rows through dedup into the db. Returns (seen, inserted, skipped)."""
    cache = {}
    seen = inserted = skipped = 0
    for row in rows:
        seen += 1
        event = to_event(row, cache)
        if event is None:
            continue
        # A dry run still asks the dedup question, or it reports every row it
        # reads as new and makes a working reconcile look like a duplicate storm.
        if dry_run:
            if db.is_duplicate(event):
                skipped += 1
                continue
            inserted += 1
            if inserted <= 15:
                log.info("would import: %s %s S%sE%s (%s)", event["watched_at"][:10],
                         event["title"], event["season"], event["episode"], event["imdb_id"])
            continue
        if db.insert_event(event) is None:
            skipped += 1
        else:
            inserted += 1
            log.info("imported %s: %s S%sE%s", event["watched_at"][:10],
                     event["title"], event["season"], event["episode"])
    return seen, inserted, skipped


def _account():
    if not config.PLEX_TOKEN:
        raise RuntimeError("PLEX_TOKEN is not set in .env")
    db.init()
    account = account_id(config.PLEX_ACCOUNT_TITLE)
    if account is None:
        log.warning("account %r not matched; importing history for ALL users",
                    config.PLEX_ACCOUNT_TITLE)
    return account


def backfill(dry_run=False):
    account = _account()
    log.info("account %r -> id %s", config.PLEX_ACCOUNT_TITLE, account)
    seen, inserted, skipped = _import(fetch_history(account), dry_run=dry_run)
    log.info("history rows seen: %d, imported: %d, duplicates skipped: %d",
             seen, inserted, skipped)
    return inserted


def reconcile(days=None, dry_run=False):
    """Re-read the recent past and import anything the webhook missed.

    Safe to run as often as you like: the dedup key here is byte-identical to
    the one the webhook writes, so a play that arrived live is recognised and
    skipped rather than doubled. Only publishes when something actually landed.
    """
    import time

    days = days or config.RECONCILE_DAYS
    account = _account()
    cutoff = int(time.time()) - days * 86400
    seen, inserted, skipped = _import(
        fetch_history(account, since=cutoff), dry_run=dry_run
    )
    log.info("reconcile over %d days: %d rows seen, %d imported, %d already known",
             days, seen, inserted, skipped)

    if inserted and not dry_run:
        from . import enrich, publish, render
        try:
            enrich.enrich_pending()
        except Exception:
            log.exception("enrichment failed; publishing anyway")
        render.write_page()
        publish.push()
        log.info("published %d recovered event(s)", inserted)

    return inserted


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dry = "--dry-run" in sys.argv
    if "--reconcile" in sys.argv:
        days = None
        for arg in sys.argv:
            if arg.startswith("--days="):
                days = int(arg.split("=", 1)[1])
        reconcile(days=days, dry_run=dry)
    else:
        backfill(dry_run=dry)
