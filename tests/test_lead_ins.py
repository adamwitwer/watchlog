#!/usr/bin/env python3
"""Lead-ins: last week's episode, re-played for its ending before the next one.

The final fifteen minutes cross the 90% mark like any viewing, so Plex logs a
second view of an episode already in the log -- days later, outside the dedup
window. The rule hides a repeat only when a new episode of the same show
follows it that night, so a genuine rewatch survives.

Runs against a throwaway database. Nothing touches the real log.

Run: python -m tests.test_lead_ins
"""
import contextlib
import importlib.util
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from watchlog import config

_tmp = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmp.name) / "test.db"

from watchlog import db                              # noqa: E402
from watchlog.grouping import normalize              # noqa: E402

failures = []


def check(name, condition):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition:
        failures.append(name)


def watch(title, season, episode, when, source="plex", hidden=0):
    """Record one viewing the way the webhook or reconcile would."""
    if source == "appletv":
        key = f"{normalize(title)}|appletv|{when[:10]}"
    else:
        key = f"{normalize(title)}|episode|{season}|{episode}"
    return db.insert_event({
        "watched_at": when, "source": source, "service": "Plex",
        "media_type": "episode", "title": title,
        "season": season, "episode": episode,
        "dedup_key": key, "hidden": hidden,
    })


def hidden(row_id):
    with db.connect() as conn:
        return bool(conn.execute("SELECT hidden FROM events WHERE id = ?",
                                 (row_id,)).fetchone()["hidden"])


db.init()

print("\nthe lead-in")

watch("Night Shift", 1, 1, "2026-08-17T02:00:00+00:00")
again = watch("Night Shift", 1, 1, "2026-08-24T01:30:00+00:00")
check("a repeat is recorded at first -- nothing follows it yet",
      again is not None and not hidden(again))
nxt = watch("Night Shift", 1, 2, "2026-08-24T02:30:00+00:00")
check("the next episode that night hides the repeat", hidden(again))
check("...and is itself shown", not hidden(nxt))
check("the original viewing a week earlier is untouched",
      not hidden(1))

print("\nreconcile order: newest first")

watch("Low Tide", 1, 5, "2026-08-11T02:00:00+00:00")
nxt = watch("Low Tide", 1, 6, "2026-08-18T03:10:00+00:00")
again = watch("Low Tide", 1, 5, "2026-08-18T02:10:00+00:00")
check("a lead-in arriving after its successor is still hidden", hidden(again))
check("...and the successor still shown", not hidden(nxt))

print("\nacross a season boundary")

watch("Ember Road", 3, 10, "2026-05-01T01:00:00+00:00")
again = watch("Ember Road", 3, 10, "2026-06-20T00:30:00+00:00")
watch("Ember Road", 4, 1, "2026-06-20T01:20:00+00:00")
check("last season's finale before the premiere is a lead-in", hidden(again))

print("\nrewatches that are real")

watch("Quiet Lane", 1, 1, "2026-01-11T23:00:00+00:00")
alone = watch("Quiet Lane", 1, 1, "2026-04-09T23:00:00+00:00")
check("one episode put on again, alone, stays", not hidden(alone))

for n in (1, 2, 3):
    watch("Glass House", 1, n, f"2026-02-0{n}T01:00:00+00:00")
binge = [watch("Glass House", 1, n, f"2026-03-01T0{n}:00:00+00:00") for n in (1, 2, 3)]
check("a season replayed in one sitting stays, all of it",
      not any(hidden(i) for i in binge))
watch("Glass House", 1, 4, "2026-03-01T03:50:00+00:00")
check("...even when it runs on into an episode never seen before",
      not hidden(binge[0]) and not hidden(binge[1]))
check("...except the one right before it, which is a lead-in by shape",
      hidden(binge[2]))

watch("Far Shore", 1, 4, "2026-07-01T01:00:00+00:00")
watch("Far Shore", 1, 5, "2026-07-08T00:00:00+00:00")
after = watch("Far Shore", 1, 4, "2026-07-08T01:30:00+00:00")
check("a repeat after the new episode is not leading into anything",
      not hidden(after))

print("\nan episode first seen on the Apple TV")

# Numbered by hand afterwards: its key is still per-night, so the rule has to
# compare show and number to know the Plex play a week later is a repeat.
watch("Gull's Harbour", None, None, "2026-09-27T03:00:00+00:00", source="appletv")
with db.connect() as conn:
    conn.execute("UPDATE events SET season = 1, episode = 1 "
                 "WHERE title = 'Gull''s Harbour'")
again = watch("Gull's Harbour", 1, 1, "2026-10-04T00:30:00+00:00")
watch("Gull's Harbour", 1, 2, "2026-10-04T01:20:00+00:00")
check("a repeat of an Apple TV episode is still a lead-in", hidden(again))


print("\nwhat does not count")

watch("Paper Moon", 1, 2, "2026-07-01T01:00:00+00:00")
again = watch("Paper Moon", 1, 2, "2026-07-09T00:00:00+00:00")
watch("Other Show", 5, 1, "2026-07-09T01:00:00+00:00")
check("a different show afterwards does not hide it", not hidden(again))
watch("Paper Moon", None, None, "2026-07-09T01:30:00+00:00", source="appletv")
check("nor does an Apple TV row, which has no episode number to be new",
      not hidden(again))
watch("Paper Moon", 1, 3, "2026-07-10T01:00:00+00:00")
check("nor the next episode on a different night", not hidden(again))

print("\ndeletions")

gone = watch("Cold Front", 1, 6, "2026-09-01T01:00:00+00:00", hidden=1)
again = watch("Cold Front", 1, 6, "2026-09-08T00:30:00+00:00")
watch("Cold Front", 1, 7, "2026-09-08T01:30:00+00:00")
check("after a deleted first viewing, the second is the real one and stays",
      not hidden(again))
check("...and the deletion stays deleted", hidden(gone))

kept = watch("Iron Bridge", 1, 1, "2026-09-10T01:00:00+00:00", hidden=1)
watch("Iron Bridge", 1, 2, "2026-09-10T02:00:00+00:00")
check("a hidden row is never un-hidden by the rule", hidden(kept))

print("\nwhen the next episode starts")

# Hidden at 90% of the next episode is too late: that episode is often watched
# in halves, and the lead-in would sit on the page until the second half. So
# the webhook acts on Plex's media.play instead, before anything is recorded.
import json                                          # noqa: E402
from datetime import datetime, timedelta, timezone   # noqa: E402
from watchlog import plex_webhook                    # noqa: E402

config.PLEX_WEBHOOK_SECRET = "test-secret"
config.PLEX_ACCOUNT_TITLE = "the-viewer"
published = []
plex_webhook.schedule_publish = lambda: published.append(1)
hook = plex_webhook.app.test_client()


def ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def plex(event, title, season, episode, account=None):
    return hook.post("/plex/test-secret", data={"payload": json.dumps({
        "event": event,
        "Account": {"title": account or config.PLEX_ACCOUNT_TITLE},
        "Metadata": {"type": "episode", "grandparentTitle": title,
                     "title": "Whatever", "parentIndex": season, "index": episode},
    })})


def count(title):
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) n FROM events WHERE title = ?",
                            (title,)).fetchone()["n"]


watch("Harbour", 1, 2, ago(days=8))
again = watch("Harbour", 1, 2, ago(seconds=30))
plex("media.pause", "Harbour", 1, 3)
check("pausing is not starting", not hidden(again))
plex("media.play", "Harbour", 1, 3, account="someone-else")
check("someone else's account starting it is not him", not hidden(again))
plex("media.play", "Other Harbour", 1, 3)
check("a different show starting does not count", not hidden(again))
plex("media.play", "Harbour", 1, 2)
check("re-starting the repeat itself does not count", not hidden(again))
check("...and nothing has been published for any of that", published == [])

plex("media.play", "Harbour", 1, 3)
check("the next episode starting hides the lead-in at once", hidden(again))
check("...and publishes, so the page loses it too", published == [1])
check("...without recording the episode that has only just started",
      count("Harbour") == 2)

published.clear()
plex("media.resume", "Harbour", 1, 3)
check("resuming it later, with nothing left to hide, publishes nothing",
      published == [])

watch("Shoreline", 2, 1, ago(days=30))
watch("Shoreline", 2, 2, ago(days=29))
again = watch("Shoreline", 2, 1, ago(seconds=30))
plex("media.play", "Shoreline", 2, 2)
check("starting another repeat -- a rewatch in progress -- hides nothing",
      not hidden(again))


print("\nthe backfill tool")

# Rows from before the rule: written straight in, so nothing hid them.
with db.connect() as conn:
    for when, ep in (("2026-01-05T01:00:00+00:00", 3),
                     ("2026-01-12T01:00:00+00:00", 3),
                     ("2026-01-12T02:00:00+00:00", 4)):
        conn.execute(
            """INSERT INTO events (watched_at, source, service, media_type,
                                   title, season, episode, dedup_key)
               VALUES (?, 'plex', 'Plex', 'episode', 'Old Mill', 1, ?, ?)""",
            (when, ep, f"knight|episode|1|{ep}"))
    old = conn.execute("SELECT id FROM events WHERE title = 'Old Mill' "
                       "AND watched_at LIKE '2026-01-12T01%'").fetchone()["id"]

spec = importlib.util.spec_from_file_location("hide_lead_ins",
                                              ROOT / "tools" / "hide_lead_ins.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

out = io.StringIO()
with contextlib.redirect_stdout(out):
    tool.main(dry_run=True)
check("a dry run finds the old lead-in", f"#{old}" in out.getvalue())
check("...and only that one", out.getvalue().startswith("1 lead-ins"))
check("...and hides nothing", not hidden(old))
with contextlib.redirect_stdout(io.StringIO()):
    tool.main(dry_run=False)
check("--apply hides it", hidden(old))


print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
