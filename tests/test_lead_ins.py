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

watch("Lanterns", 1, 1, "2026-08-17T03:14:24+00:00")
again = watch("Lanterns", 1, 1, "2026-08-24T01:34:41+00:00")
check("a repeat is recorded at first -- nothing follows it yet",
      again is not None and not hidden(again))
nxt = watch("Lanterns", 1, 2, "2026-08-24T02:31:37+00:00")
check("the next episode that night hides the repeat", hidden(again))
check("...and is itself shown", not hidden(nxt))
check("the original viewing a week earlier is untouched",
      not hidden(1))

print("\nreconcile order: newest first")

watch("Furious", 1, 5, "2026-08-11T02:00:00+00:00")
nxt = watch("Furious", 1, 6, "2026-08-18T03:10:00+00:00")
again = watch("Furious", 1, 5, "2026-08-18T02:12:17+00:00")
check("a lead-in arriving after its successor is still hidden", hidden(again))
check("...and the successor still shown", not hidden(nxt))

print("\nacross a season boundary")

watch("Vox", 3, 10, "2026-05-01T01:00:00+00:00")
again = watch("Vox", 3, 10, "2026-06-20T00:30:00+00:00")
watch("Vox", 4, 1, "2026-06-20T01:20:00+00:00")
check("last season's finale before the premiere is a lead-in", hidden(again))

print("\nrewatches that are real")

watch("Marlow", 1, 1, "2026-01-11T23:00:00+00:00")
alone = watch("Marlow", 1, 1, "2026-04-09T23:11:41+00:00")
check("one episode put on again, alone, stays", not hidden(alone))

for n in (1, 2, 3):
    watch("Severance", 1, n, f"2026-02-0{n}T01:00:00+00:00")
binge = [watch("Severance", 1, n, f"2026-03-01T0{n}:00:00+00:00") for n in (1, 2, 3)]
check("a season replayed in one sitting stays, all of it",
      not any(hidden(i) for i in binge))
watch("Severance", 1, 4, "2026-03-01T03:50:00+00:00")
check("...even when it runs on into an episode never seen before",
      not hidden(binge[0]) and not hidden(binge[1]))
check("...except the one right before it, which is a lead-in by shape",
      hidden(binge[2]))

watch("Andor", 1, 4, "2026-07-01T01:00:00+00:00")
watch("Andor", 1, 5, "2026-07-08T00:00:00+00:00")
after = watch("Andor", 1, 4, "2026-07-08T01:30:00+00:00")
check("a repeat after the new episode is not leading into anything",
      not hidden(after))

print("\nan episode first seen on the Apple TV")

# Numbered by hand afterwards: its key is still per-night, so the rule has to
# compare show and number to know the Plex play a week later is a repeat.
watch("Widow's Bay", None, None, "2026-09-27T03:31:34+00:00", source="appletv")
with db.connect() as conn:
    conn.execute("UPDATE events SET season = 1, episode = 1 "
                 "WHERE title = 'Widow''s Bay'")
again = watch("Widow's Bay", 1, 1, "2026-10-04T00:30:00+00:00")
watch("Widow's Bay", 1, 2, "2026-10-04T01:20:00+00:00")
check("a repeat of an Apple TV episode is still a lead-in", hidden(again))


print("\nwhat does not count")

watch("Pluribus", 1, 2, "2026-07-01T01:00:00+00:00")
again = watch("Pluribus", 1, 2, "2026-07-09T00:00:00+00:00")
watch("Slow Horses", 5, 1, "2026-07-09T01:00:00+00:00")
check("a different show afterwards does not hide it", not hidden(again))
watch("Pluribus", None, None, "2026-07-09T01:30:00+00:00", source="appletv")
check("nor does an Apple TV row, which has no episode number to be new",
      not hidden(again))
watch("Pluribus", 1, 3, "2026-07-10T01:00:00+00:00")
check("nor the next episode on a different night", not hidden(again))

print("\ndeletions")

gone = watch("Alien Earth", 1, 6, "2026-09-01T01:00:00+00:00", hidden=1)
again = watch("Alien Earth", 1, 6, "2026-09-08T00:30:00+00:00")
watch("Alien Earth", 1, 7, "2026-09-08T01:30:00+00:00")
check("after a deleted first viewing, the second is the real one and stays",
      not hidden(again))
check("...and the deletion stays deleted", hidden(gone))

kept = watch("Task", 1, 1, "2026-09-10T01:00:00+00:00", hidden=1)
watch("Task", 1, 2, "2026-09-10T02:00:00+00:00")
check("a hidden row is never un-hidden by the rule", hidden(kept))

print("\nthe backfill tool")

# Rows from before the rule: written straight in, so nothing hid them.
with db.connect() as conn:
    for when, ep in (("2026-01-05T01:00:00+00:00", 3),
                     ("2026-01-12T01:00:00+00:00", 3),
                     ("2026-01-12T02:00:00+00:00", 4)):
        conn.execute(
            """INSERT INTO events (watched_at, source, service, media_type,
                                   title, season, episode, dedup_key)
               VALUES (?, 'plex', 'Plex', 'episode', 'Knight', 1, ?, ?)""",
            (when, ep, f"knight|episode|1|{ep}"))
    old = conn.execute("SELECT id FROM events WHERE title = 'Knight' "
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
