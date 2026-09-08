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


# --- the library sweep ------------------------------------------------------
#
# Plex has two records of what has been watched and they do not agree. The play
# HISTORY is a list of sessions, which is what the webhook reports and what
# reconcile re-reads. The LIBRARY carries viewCount and lastViewedAt per item,
# and marking a season watched in the UI sets those without ever creating a
# session. So an episode can be watched in Plex and absent from every source
# this project otherwise reads -- with nothing anywhere reporting a problem,
# because nothing went wrong.
#
# The timestamp is therefore a mark time, not a watch time. Five episodes
# marked in one click all land within the same minute. That is a real loss of
# fidelity and it is still much better than the alternative, which is the log
# saying a show was abandoned when it was finished.

LIBRARY_TYPE = {"movie": 1, "episode": 4}


def _sections():
    return _get("/library/sections").get("Directory", [])


def _library_items(section_key, kind):
    """Every item of one kind in one section, paginated."""
    start = 0
    while True:
        container = _get(
            f"/library/sections/{section_key}/all",
            type=LIBRARY_TYPE[kind],
            **{"X-Plex-Container-Start": start, "X-Plex-Container-Size": PAGE_SIZE},
        )
        items = container.get("Metadata", [])
        if not items:
            return
        yield from items
        start += len(items)
        if start >= int(container.get("totalSize", 0)):
            return


def _as_history_row(item):
    """A library item, wearing the shape `to_event` reads.

    Two differences to bridge. Library items date themselves with lastViewedAt
    rather than viewedAt, and they carry grandparentRatingKey directly where a
    history row only has the path in grandparentKey -- so the path is rebuilt
    here rather than teaching `_rating_key` a second shape.
    """
    row = dict(item)
    row["viewedAt"] = item.get("lastViewedAt")
    parent = item.get("grandparentRatingKey")
    if parent and not row.get("grandparentKey"):
        row["grandparentKey"] = f"/library/metadata/{parent}"
    return row


def watched_in_library():
    """Every watched item Plex's library knows about, as history-shaped rows.

    No account filter: viewCount and lastViewedAt are returned for the user
    whose token this is, which is already the right person.
    """
    for section in _sections():
        kind = {"movie": "movie", "show": "episode"}.get(section.get("type"))
        if not kind:
            continue
        for item in _library_items(section["key"], kind):
            if int(item.get("viewCount") or 0) < 1:
                continue
            if not item.get("lastViewedAt"):
                continue
            yield _as_history_row(item)


def _sweep_due():
    """Whether a day has passed since the last sweep."""
    from datetime import datetime, timedelta, timezone
    last = db.get_meta(config.META_SWEEP_OK)
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - when >= timedelta(
        hours=config.SWEEP_EVERY_HOURS)


def sweep(dry_run=False, accept=None):
    """Import what the library says was watched and the history never saw.

    Only fills holes. An item whose dedup key is already in the log is left
    alone whatever its timestamps say -- the point is the episodes that are
    missing entirely, and matching on the key rather than on time is what stops
    this turning every rewatch into a duplicate.

    `accept` is a set of titles to take as visible entries; anything else found
    is imported hidden. That is not a half-measure, it is the only way to say
    no permanently: a show left out entirely is offered again tomorrow and
    every day after, whereas a hidden row is a decision the sweep already
    respects -- it never resurrects one -- and one the admin page can undo.
    Marking a whole series watched for someone else's benefit is a real thing
    people do to a Plex library, and it is not viewing.

    None accepts everything, which is the right default once a library has
    stopped being seeded.
    """
    _account()                       # for the PLEX_TOKEN check and db.init()
    known = db.dedup_keys()
    # Plex sends its own spelling of every title, so without this a sweep would
    # quietly undo a rename -- 24 episodes of "INVINCIBLE (2021)" walking back
    # in the day after it was corrected to "Invincible (2021)".
    spellings = db.title_spellings()
    floor = config.SWEEP_SINCE or db.first_event_date() or ""
    log.info("sweeping the library back to %s", floor or "the beginning")
    cache = {}
    seen = found = skipped_old = dismissed = 0
    for row in watched_in_library():
        seen += 1
        event = to_event(row, cache)
        if event is None or event["dedup_key"] in known:
            continue
        # Older than the log itself. Not a gap -- the log never claimed to
        # cover it, and importing it silently would rewrite what this page is.
        if floor and event["watched_at"][:10] < floor:
            skipped_old += 1
            continue
        # Marked, not played -- and said so, because the timestamp on these is
        # when the box was ticked and anything reading the row should know.
        event["source"] = "plex-sweep"
        event["title"] = spellings.get(normalize(event["title"]), event["title"])
        wanted = accept is None or normalize(event["title"]) in accept
        event["hidden"] = 0 if wanted else 1
        found += wanted
        dismissed += not wanted
        known.add(event["dedup_key"])
        log.info("%s%s %s S%sE%s, marked watched %s",
                 "would " if dry_run else "",
                 "import" if wanted else "dismiss",
                 event["title"], event["season"], event["episode"],
                 event["watched_at"][:10])
        if not dry_run:
            db.insert_event(event)

    log.info("library sweep: %d watched items, %d imported, %d dismissed, "
             "%d older than the log itself", seen, found, dismissed, skipped_old)
    if not dry_run:
        db.set_meta(config.META_SWEEP_OK, _now())
        if found:
            db.set_meta(config.META_SWEEP_FOUND, str(found))
            db.set_meta(config.META_SWEEP_FOUND_AT, _now())
    return found


OK_AT = config.META_RECONCILE_OK
ERROR = config.META_RECONCILE_ERROR
ERROR_AT = config.META_RECONCILE_ERROR_AT


def reconcile(days=None, dry_run=False):
    """Re-read the recent past and import anything the webhook missed.

    Safe to run as often as you like: the dedup key here is byte-identical to
    the one the webhook writes, so a play that arrived live is recognised and
    skipped rather than doubled. Only publishes when something actually landed.

    Records its own outcome either way. This job is the safety net for a sensor
    that fails silently, which makes it exactly the thing that must not fail
    silently itself -- on 2026-09-04 it failed hourly for four hours after the
    Plex host changed address, and the only symptom was a missing episode.
    """
    db.init()
    try:
        inserted = _reconcile(days=days, dry_run=dry_run)
    except Exception as exc:
        if not dry_run:
            db.set_meta(ERROR, f"{type(exc).__name__}: {exc}"[:400])
            db.set_meta(ERROR_AT, _now())
        raise                       # systemd should still see this as a failure
    if not dry_run:
        db.set_meta(OK_AT, _now())
        db.set_meta(ERROR, "")
    return inserted


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _reconcile(days=None, dry_run=False):
    import time

    days = days or config.RECONCILE_DAYS
    account = _account()
    cutoff = int(time.time()) - days * 86400
    seen, inserted, skipped = _import(
        fetch_history(account, since=cutoff), dry_run=dry_run
    )
    log.info("reconcile over %d days: %d rows seen, %d imported, %d already known",
             days, seen, inserted, skipped)

    if dry_run:
        return inserted

    from . import enrich, publish, render

    if inserted:
        # Every row recovered here is a play the webhook should have delivered
        # and didn't. That count, not silence, is the measure of whether the
        # webhook is still doing its job.
        db.set_meta(config.META_WEBHOOK_MISSED, str(inserted))
        db.set_meta(config.META_WEBHOOK_MISSED_AT, _now())
        try:
            enrich.enrich_pending()
        except Exception:
            log.exception("enrichment failed; publishing anyway")

    # Season lengths drift with no help from us: a season still airing gains
    # episodes as they are announced, and a show that reached its finale stops
    # being "in progress" without anything new being watched. So this runs on
    # every reconcile, not only the ones that found something.
    try:
        learned = enrich.refresh_seasons()
    except Exception:
        log.exception("season refresh failed; publishing anyway")
        learned = 0

    # And once a day, the thing neither sensor nor history can see. Daily
    # rather than hourly because it reads the whole library, and because an
    # episode marked watched rather than played is in no hurry.
    swept = 0
    try:
        if _sweep_due():
            swept = sweep()
            if swept:
                enrich.enrich_pending()
    except Exception:
        log.exception("library sweep failed; publishing anyway")

    if inserted or learned or swept:
        render.write_output()
        publish.push()
        log.info("published: %d recovered, %d show(s) re-measured, %d swept",
                 inserted, learned, swept)

    return inserted


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dry = "--dry-run" in sys.argv
    if "--sweep" in sys.argv:
        # --only "A|B" takes those titles as real entries and dismisses the
        # rest into the deleted list, where they stop being offered daily.
        accept = None
        for arg in sys.argv:
            if arg.startswith("--only="):
                accept = {normalize(t) for t in arg.split("=", 1)[1].split("|")}
        sweep(dry_run=dry, accept=accept)
    elif "--reconcile" in sys.argv:
        days = None
        for arg in sys.argv:
            if arg.startswith("--days="):
                days = int(arg.split("=", 1)[1])
        reconcile(days=days, dry_run=dry)
    else:
        backfill(dry_run=dry)
