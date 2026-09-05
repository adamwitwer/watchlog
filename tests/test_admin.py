#!/usr/bin/env python3
"""The admin surface: auth, editing details, and adding entries by hand.

Runs against a throwaway database, never the real one -- config.DB_PATH is
repointed before anything touches sqlite, so a failing test can't leave debris
in the live log.

Run: python -m tests.test_admin
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchlog import config

_tmp = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmp.name) / "test.db"
config.ADMIN_TOKEN = config.ADMIN_TOKEN or "test-token"

from watchlog import admin, db          # noqa: E402  (after DB_PATH is repointed)
from watchlog.grouping import night_of  # noqa: E402

published, enriched = [], []
_real_push = admin.publish.push        # the stub below replaces it globally
admin.render.write_page = lambda: published.append("render")
admin.publish.push = lambda: published.append("push")
admin.enrich.enrich_pending = lambda: enriched.append(1)

db.init()

failures = []


def check(name, condition):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition:
        failures.append(name)


def rows(title):
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM events WHERE title = ? ORDER BY id", (title,)
        ).fetchall()


client = admin.app.test_client()
SHOW, FILM, DAY = "Test Show", "Test Film", "2021-06-15"

# --- auth ------------------------------------------------------------------

check("the index 404s without a token", client.get("/").status_code == 404)
check("a wrong token 404s",
      client.get("/?token=nope").status_code == 404)
check("adding 404s without a token",
      client.post("/add", data={"title": SHOW, "service": "Netflix",
                                "date": DAY}).status_code == 404)
check("nothing was written by the unauthorised POST", not rows(SHOW))
check("the right token gets in",
      client.get(f"/?token={config.ADMIN_TOKEN}").status_code == 200)

client.set_cookie("watchlog_admin", config.ADMIN_TOKEN)

# --- adding ----------------------------------------------------------------

published.clear(); enriched.clear()
response = client.post("/add", data={
    "title": SHOW, "service": "Netflix", "date": DAY, "media_type": "episode",
    "season": "2", "episode": "5", "episode_title": "  The Test  ",
    "year": "2019"})
added = rows(SHOW)
check("adding redirects", response.status_code == 302)
check("one row is written", len(added) == 1)
check("the source records that it was typed", added[0]["source"] == "manual")
check("the service is kept verbatim", added[0]["service"] == "Netflix")
check("season and episode are stored",
      (added[0]["season"], added[0]["episode"]) == (2, 5))
check("the episode title is trimmed", added[0]["episode_title"] == "The Test")
check("the year is stored", added[0]["year"] == 2019)
check("it files under the night that was typed in, not the day after",
      str(night_of(added[0]["watched_at"])) == DAY)
check("enrichment runs, so the IMDb link fills itself in", enriched == [1])
check("it republishes", published == ["render", "push"])

published.clear()
response = client.post("/add", data={
    "title": SHOW, "service": "Netflix", "date": DAY, "media_type": "episode",
    "season": "2", "episode": "5"})
check("the same episode on the same night is refused", len(rows(SHOW)) == 1)
check("the refusal is explained rather than silent",
      "error=" in response.headers.get("Location", ""))
check("a refused add does not republish", published == [])

client.post("/add", data={"title": SHOW, "service": "Netflix", "date": DAY,
                          "media_type": "episode", "season": "2", "episode": "6"})
check("the next episode the same night is accepted", len(rows(SHOW)) == 2)

client.post("/add", data={
    "title": FILM, "service": "Theater", "date": DAY, "media_type": "movie",
    "season": "3", "episode": "9", "episode_title": "nonsense"})
film = rows(FILM)[0]
check("a movie is stored as a movie", film["media_type"] == "movie")
check("a movie drops season, episode and episode title",
      film["season"] is None and film["episode"] is None
      and film["episode_title"] is None)

for label, data in [
    ("a missing title is rejected", {"title": " ", "service": "N", "date": DAY}),
    ("a missing service is rejected", {"title": "X", "service": "", "date": DAY}),
    ("a bad date is rejected", {"title": "X", "service": "N", "date": "nope"}),
    ("a non-numeric season is rejected",
     {"title": "X", "service": "N", "date": DAY, "season": "two"}),
]:
    published.clear()
    response = client.post("/add", data=data)
    check(label, response.status_code == 302
          and "error=" in response.headers.get("Location", "")
          and not rows("X") and published == [])

# --- editing ---------------------------------------------------------------

target = rows(SHOW)[0]["id"]
published.clear()
client.post("/edit", data={"id": target, "season": "3", "episode": "7",
                           "episode_title": "  Renamed  "})
with db.connect() as conn:
    edited = conn.execute("SELECT * FROM events WHERE id = ?", (target,)).fetchone()
check("an edit stores season and episode",
      (edited["season"], edited["episode"]) == (3, 7))
check("an edit trims the episode title", edited["episode_title"] == "Renamed")
check("an edit republishes", published == ["render", "push"])

client.post("/edit", data={"id": target, "season": "", "episode": "",
                           "episode_title": ""})
with db.connect() as conn:
    cleared = conn.execute("SELECT * FROM events WHERE id = ?", (target,)).fetchone()
check("blank fields clear back to NULL",
      cleared["season"] is None and cleared["episode"] is None
      and cleared["episode_title"] is None)

client.post("/edit", data={"id": target, "season": "4", "episode_title": "Kept"})
published.clear()
response = client.post("/edit", data={"id": target, "season": "four"})
with db.connect() as conn:
    intact = conn.execute("SELECT * FROM events WHERE id = ?", (target,)).fetchone()
check("a rejected edit changes nothing",
      intact["season"] == 4 and intact["episode_title"] == "Kept")
check("a rejected edit does not republish", published == [])

# --- health ----------------------------------------------------------------
# Four moving parts, every one of them silent when it is working. These lines
# are the only thing that separates healthy from dead.

from datetime import timedelta, timezone  # noqa: E402
from watchlog import plex_history, plex_webhook   # noqa: E402

_real_reconcile = plex_history._reconcile


def now(offset_hours=0):
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).isoformat()


def beat(label):
    """Look a health line up by name; the order on the page is not the contract."""
    return next(b for b in admin._health() if b["label"] == label)


def clear_meta():
    with db.connect() as conn:
        conn.execute("DELETE FROM meta")


clear_meta()
labels = [b["label"] for b in admin._health()]
check("every part of the chain gets a line, in the order the chain runs",
      labels == ["Plex webhook", "Apple TV listener polled",
                 "Last reconcile", "Last publish"])
check("with nothing recorded, each one reads as unknown rather than fine",
      all(b["state"] == "unknown" for b in admin._health()))

# --- reconcile -------------------------------------------------------------

# The recording itself: a success clears any standing error, a failure keeps it
# and still raises so systemd sees a failed unit.
plex_history._reconcile = lambda days=None, dry_run=False: 0
plex_history.reconcile()
check("a successful reconcile records a heartbeat",
      db.get_meta(plex_history.OK_AT) is not None)
check("a successful reconcile clears the error",
      db.get_meta(plex_history.ERROR) == "")


def boom(days=None, dry_run=False):
    raise ConnectionError("No route to host")


plex_history._reconcile = boom
raised = False
try:
    plex_history.reconcile()
except ConnectionError:
    raised = True
check("a failing reconcile still raises, so systemd marks it failed", raised)
check("a failing reconcile records why",
      "No route to host" in (db.get_meta(plex_history.ERROR) or ""))

# What the admin page makes of it.
db.set_meta(plex_history.OK_AT, now())
db.set_meta(plex_history.ERROR, "")
health = beat("Last reconcile")
check("a fresh heartbeat reads as ok", health["state"] == "ok")
check("and says how long ago", "ago" in health["text"] or "just now" in health["text"])
check("no error is shown while healthy", health["error"] is None)

db.set_meta(plex_history.OK_AT, now(-5))
db.set_meta(plex_history.ERROR, "ConnectionError: No route to host")
db.set_meta(plex_history.ERROR_AT, now(-1))
health = beat("Last reconcile")
check("a heartbeat older than the threshold reads as stale",
      health["state"] == "stale")
check("stale says plainly that something is wrong",
      "something is wrong" in health["text"])
check("stale surfaces the error", "No route to host" in health["error"])

page = client.get("/").get_data(as_text=True)
check("the stale state renders on the page", "is-stale" in page)
check("the error text renders too", "No route to host" in page)

# The hole an age check alone leaves: the most recent run blew up, but the last
# success is still inside the staleness window, so the line used to read green.
db.set_meta(plex_history.OK_AT, now(-1))
db.set_meta(plex_history.ERROR, "ConnectionError: No route to host")
db.set_meta(plex_history.ERROR_AT, now())
health = beat("Last reconcile")
check("a run that just failed reads as broken even with a recent success",
      health["state"] == "stale")
check("and says it failed rather than quoting the last success",
      "failed" in health["text"])
check("the error is shown while it is the current story",
      "No route to host" in health["error"])

db.set_meta(plex_history.OK_AT, now())
db.set_meta(plex_history.ERROR, "")
check("recovering clears the warning", beat("Last reconcile")["state"] == "ok")

# --- the Apple TV listener -------------------------------------------------
# It is silent when idle and was found wedged for 11 hours with no error, so a
# heartbeat is the only thing that separates working from stuck.

db.set_meta(config.META_APPLETV_OK, now())
check("a fresh Apple TV heartbeat reads as ok",
      beat("Apple TV listener polled")["state"] == "ok")

db.set_meta(config.META_APPLETV_OK, now(-1))
atv = beat("Apple TV listener polled")
check("an Apple TV heartbeat an hour old reads as stale", atv["state"] == "stale")
check("and says so plainly", "something is wrong" in atv["text"])

with db.connect() as conn:
    conn.execute("DELETE FROM meta WHERE key = ?", (config.META_APPLETV_OK,))
check("never having reported reads as unknown, not ok",
      beat("Apple TV listener polled")["state"] == "unknown")
db.set_meta(config.META_APPLETV_OK, now())

# --- publishing ------------------------------------------------------------
# The last link in the chain, and the one with no cadence: a week without a
# publish is a week without anything to publish, not a fault.

db.set_meta(config.META_PUBLISH_OK, now(-72))
pub = beat("Last publish")
check("a quiet three days is not a publish failure", pub["state"] == "ok")

db.set_meta(config.META_PUBLISH_ERROR, "RuntimeError: rsync failed: timed out")
db.set_meta(config.META_PUBLISH_ERROR_AT, now())
pub = beat("Last publish")
check("an attempt that failed reads as broken", pub["state"] == "stale")
check("and says it failed", "failed" in pub["text"])
check("and shows what went wrong", "rsync failed" in pub["error"])

db.set_meta(config.META_PUBLISH_OK, now())
db.set_meta(config.META_PUBLISH_ERROR, "")
check("a later success clears it", beat("Last publish")["state"] == "ok")

# The record is written inside push(), because two of its three callers swallow
# the exception and would otherwise leave a dead publisher looking healthy.
from watchlog import publish as publish_module   # noqa: E402

clear_meta()
publish_module._push = lambda local_path=None: True
_real_push()
check("a successful push records its own heartbeat",
      db.get_meta(config.META_PUBLISH_OK) is not None)


def rsync_died(local_path=None):
    raise RuntimeError("rsync failed: Connection timed out")


publish_module._push = rsync_died
raised = False
try:
    _real_push()
except RuntimeError:
    raised = True
check("a failing push still raises for its caller", raised)
check("a failing push records why",
      "Connection timed out" in (db.get_meta(config.META_PUBLISH_ERROR) or ""))
check("and the admin page turns red on it", beat("Last publish")["state"] == "stale")

# --- the Plex webhook ------------------------------------------------------
# Reconcile backfills whatever the webhook drops, so a dead webhook now has no
# visible consequence at all. What gives it away is reconcile having to recover
# anything: those are plays the webhook should have delivered.

clear_meta()
check("a webhook that has never delivered reads as unknown",
      beat("Plex webhook")["state"] == "unknown")

config.PLEX_WEBHOOK_SECRET = "test-secret"
plex_webhook.schedule_publish = lambda: None
hook = plex_webhook.app.test_client()
scrobble = {
    "event": "media.scrobble",
    "Account": {"title": config.PLEX_ACCOUNT_TITLE},
    "Metadata": {"type": "episode", "grandparentTitle": "Hook Test",
                 "title": "Pilot", "parentIndex": 1, "index": 1, "year": 2020},
}
import json as _json   # noqa: E402
hook.post("/plex/test-secret", data={"payload": _json.dumps(scrobble)})
check("an accepted scrobble is the webhook's proof of life",
      db.get_meta(config.META_WEBHOOK_OK) is not None)
check("which the admin page reads as ok", beat("Plex webhook")["state"] == "ok")

# A reconcile that recovers something is the webhook admitting it missed one.
plex_history._reconcile = _real_reconcile
plex_history._account = lambda: None
plex_history.fetch_history = lambda account=None, since=None: []
plex_history._import = lambda rows, dry_run=False: (2, 2, 0)
published.clear()
plex_history.reconcile()
check("recovering plays records that the webhook missed them",
      db.get_meta(config.META_WEBHOOK_MISSED) == "2")
web = beat("Plex webhook")
check("a recovery newer than the last delivery reads as broken",
      web["state"] == "stale")
check("and says how many it had to clean up after", "2 plays" in web["text"])
check("and when the webhook was last actually working",
      "Last live delivery" in web["error"])

scrobble["Metadata"] = dict(scrobble["Metadata"], index=2, title="Second")
hook.post("/plex/test-secret", data={"payload": _json.dumps(scrobble)})
check("a delivery after the recovery means it came back",
      beat("Plex webhook")["state"] == "ok")

plex_history._import = lambda rows, dry_run=False: (2, 0, 0)
db.set_meta(config.META_WEBHOOK_MISSED_AT, "")
plex_history.reconcile()
check("a reconcile that finds nothing new says nothing about the webhook",
      beat("Plex webhook")["state"] == "ok")

# --- correcting a bad match ------------------------------------------------
# TMDb search takes the most popular result, which is sometimes the wrong show.
# The correction is against the title, so it fixes every entry for that show.

from watchlog.grouping import normalize as _normalize   # noqa: E402

# Two months apart: the spellings normalise to one dedup key, so a night or two
# between them would make the second look like a duplicate of the first.
for spelling, day in (("The Wrong Show", "2024-05-01"), ("Wrong Show", "2024-07-01")):
    db.insert_event({
        "watched_at": f"{day}T20:00:00+00:00",
        "source": "test", "service": "Plex", "media_type": "episode",
        "title": spelling, "episode_title": None, "year": 2015,
        "season": 1, "episode": 1, "imdb_id": "tt0000001", "tmdb_id": "999",
        "dedup_key": f"{_normalize(spelling)}|episode|1|1", "hidden": 0,
        "raw": None,
    })

published.clear()
response = client.post("/match", data={"title": "The Wrong Show",
                                       "imdb_id": "tt9876543", "year": "2024"})
fixed = rows("The Wrong Show") + rows("Wrong Show")
check("fixing a match redirects", response.status_code == 302)
check("every entry for that show is repointed",
      all(r["imdb_id"] == "tt9876543" for r in fixed))
check("including a different spelling that normalises the same",
      len(fixed) == 2)
check("the year is corrected too", all(r["year"] == 2024 for r in fixed))
check("the stale tmdb id is dropped rather than left pointing at the wrong show",
      all(r["tmdb_id"] is None for r in fixed))
check("fixing a match republishes", published == ["render", "push"])

with db.connect() as conn:
    pinned = conn.execute("SELECT * FROM titles WHERE norm_title = ?",
                          (_normalize("The Wrong Show"),)).fetchone()
check("the title cache is corrected, so the next entry resolves right",
      pinned["imdb_id"] == "tt9876543")
check("and pinned, which is the first thing to ever set locked",
      pinned["locked"] == 1)

published.clear()
response = client.post("/match", data={"title": "The Wrong Show",
                                       "imdb_id": "tt123", "year": "2024"})
check("a malformed IMDb id is rejected",
      "error=" in response.headers.get("Location", ""))
check("and changes nothing",
      rows("The Wrong Show")[0]["imdb_id"] == "tt9876543" and published == [])

client.post("/match", data={"title": "The Wrong Show", "imdb_id": "", "year": ""})
check("blanking the id removes the link rather than restoring the bad one",
      rows("The Wrong Show")[0]["imdb_id"] is None)

client.delete_cookie("watchlog_admin")
response = client.post("/match", data={"title": "The Wrong Show",
                                       "imdb_id": "tt1111111"})
check("fixing a match 404s without a token", response.status_code == 404)
client.set_cookie("watchlog_admin", config.ADMIN_TOKEN)

# --- the page itself -------------------------------------------------------

page = client.get("/").get_data(as_text=True)
check("the add form is rendered", 'action="/add"' in page)
check("the service suggestions are offered", "Prime Video" in page)
check("the date defaults to today",
      datetime.now().astimezone().date().isoformat() in page)
check("the match form is rendered", 'action="/match"' in page)
check("each entry shows what it resolved to", "no IMDb match" in page
      or "imdb.com/title/" in page)
check("the edit form is offered for single-event entries",
      'name="episode_title"' in page)
check("an error is shown when one is passed back",
      "must be a whole number"
      in client.get("/?error=season+must+be+a+whole+number").get_data(as_text=True))

print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
