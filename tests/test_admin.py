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

# --- reconcile health ------------------------------------------------------

from datetime import timedelta, timezone  # noqa: E402
from watchlog import plex_history          # noqa: E402

def now(offset_hours=0):
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).isoformat()

with db.connect() as conn:
    conn.execute("DELETE FROM meta")
check("with nothing recorded, health reads as unknown",
      admin._health()[0]["state"] == "unknown")

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
health = admin._health()[0]
check("a fresh heartbeat reads as ok", health["state"] == "ok")
check("and says how long ago", "ago" in health["text"] or "just now" in health["text"])
check("no error is shown while healthy", health["error"] is None)

db.set_meta(plex_history.OK_AT, now(-5))
db.set_meta(plex_history.ERROR, "ConnectionError: No route to host")
db.set_meta(plex_history.ERROR_AT, now(-1))
health = admin._health()[0]
check("a heartbeat older than the threshold reads as stale",
      health["state"] == "stale")
check("stale says plainly that something is wrong",
      "something is wrong" in health["text"])
check("stale surfaces the error", "No route to host" in health["error"])

page = client.get("/").get_data(as_text=True)
check("the stale state renders on the page", "is-stale" in page)
check("the error text renders too", "No route to host" in page)

db.set_meta(plex_history.OK_AT, now())
db.set_meta(plex_history.ERROR, "")
check("recovering clears the warning", admin._health()[0]["state"] == "ok")

# --- the Apple TV listener's own heartbeat ---------------------------------
# It is silent when idle and was found wedged for 11 hours with no error, so a
# heartbeat is the only thing that separates working from stuck.

check("both sensors get a health line", len(admin._health()) == 2)

db.set_meta(config.META_APPLETV_OK, now())
atv = admin._health()[1]
check("a fresh Apple TV heartbeat reads as ok", atv["state"] == "ok")

db.set_meta(config.META_APPLETV_OK, now(-1))
atv = admin._health()[1]
check("an Apple TV heartbeat an hour old reads as stale", atv["state"] == "stale")
check("and says so plainly", "something is wrong" in atv["text"])

with db.connect() as conn:
    conn.execute("DELETE FROM meta WHERE key = ?", (config.META_APPLETV_OK,))
check("never having reported reads as unknown, not ok",
      admin._health()[1]["state"] == "unknown")
db.set_meta(config.META_APPLETV_OK, now())

# --- the page itself -------------------------------------------------------

page = client.get("/").get_data(as_text=True)
check("the add form is rendered", 'action="/add"' in page)
check("the service suggestions are offered", "Prime Video" in page)
check("the date defaults to today",
      datetime.now().astimezone().date().isoformat() in page)
check("the edit form is offered for single-event entries",
      'name="episode_title"' in page)
check("an error is shown when one is passed back",
      "must be a whole number"
      in client.get("/?error=season+must+be+a+whole+number").get_data(as_text=True))

print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
