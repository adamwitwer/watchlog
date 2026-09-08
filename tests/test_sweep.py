#!/usr/bin/env python3
"""The library sweep: the only thing that sees an episode marked, not played.

Plex keeps two records and they disagree. The play history is a list of
sessions, which is what the webhook reports and reconcile re-reads. The library
carries viewCount and lastViewedAt, and marking a season watched in the UI sets
those without creating a session -- so those episodes exist in Plex and in no
source this project otherwise reads.

Everything here runs against a throwaway database with the Plex API stubbed
out. Nothing touches the network or the real log.

Run: python -m tests.test_sweep
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchlog import config

_tmp = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmp.name) / "test.db"
config.PLEX_TOKEN = config.PLEX_TOKEN or "test-token"

from watchlog import db, plex_history                 # noqa: E402

failures = []


def check(name, condition):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition:
        failures.append(name)


db.init()

# The Plex API never gets called: _account only exists here for its token check
# and db.init, and metadata lookups would otherwise reach for the network.
plex_history._account = lambda: None
plex_history._metadata = lambda key, cache: ("tt5000000", "9999", 2026)


def library(*episodes):
    """Stub the library listing with (season, episode, title, marked_at)."""
    rows = [{
        "type": "episode",
        "grandparentTitle": "Swept Show",
        "grandparentRatingKey": "1304",
        "parentIndex": season,
        "index": number,
        "title": title,
        "viewCount": 1,
        "lastViewedAt": marked_at,
    } for season, number, title, marked_at in episodes]
    plex_history.watched_in_library = lambda: (
        plex_history._as_history_row(r) for r in rows
    )


def logged():
    with db.connect() as conn:
        return sorted(
            (r["season"], r["episode"], r["source"])
            for r in conn.execute(
                "SELECT season, episode, source FROM events "
                "WHERE title = 'Swept Show'")
        )


# --- adapting a library item ------------------------------------------------

print("\nreading the library")

item = {"type": "episode", "grandparentTitle": "X", "grandparentRatingKey": "77",
        "parentIndex": 1, "index": 2, "title": "Two",
        "viewCount": 1, "lastViewedAt": 1780000000}
row = plex_history._as_history_row(item)
check("lastViewedAt becomes the viewedAt to_event reads",
      row["viewedAt"] == 1780000000)
check("the series key is rebuilt into the path shape a history row has",
      row["grandparentKey"] == "/library/metadata/77")
check("...so the existing rating-key parser reads it unchanged",
      plex_history._rating_key(row) == "77")


# --- filling holes ----------------------------------------------------------

print("\nfilling holes")

# Two episodes genuinely played, the way the webhook would have recorded them.
for number in (1, 2):
    db.insert_event({
        "watched_at": f"2026-06-2{number}T20:00:00+00:00", "source": "plex",
        "service": "Plex", "media_type": "episode", "title": "Swept Show",
        "episode_title": f"Episode {number}", "year": 2026,
        "season": 4, "episode": number,
        "dedup_key": f"swept show|episode|4|{number}",
    })

# The library says five were watched: the two played, and three marked in one
# click a week later -- the exact shape of the real case this was built for.
MARKED = 1782000000
library((4, 1, "One", 1780000000), (4, 2, "Two", 1780100000),
        (4, 3, "Three", MARKED), (4, 4, "Four", MARKED), (4, 5, "Five", MARKED))

found = plex_history.sweep(dry_run=True)
check("a dry run reports the three it would add", found == 3)
check("...and adds nothing", len(logged()) == 2)

found = plex_history.sweep()
check("the sweep imports exactly the missing three", found == 3)
check("the log now has all five", len(logged()) == 5)
check("the two already played were left alone, source and all",
      [r for r in logged() if r[2] == "plex"] == [(4, 1, "plex"), (4, 2, "plex")])
check("the swept ones say so, because their timestamp is a mark not a watch",
      all(r[2] == "plex-sweep" for r in logged() if r[1] >= 3))

check("running it again finds nothing", plex_history.sweep() == 0)
check("...and does not duplicate anything", len(logged()) == 5)


# --- what it must not do ----------------------------------------------------

print("\nwhat it must not resurrect")

with db.connect() as conn:
    conn.execute("UPDATE events SET hidden = 1 "
                 "WHERE title = 'Swept Show' AND episode = 5")
check("an entry was deleted", plex_history.sweep() == 0)
check("a deleted entry is not brought back, today or any other day",
      len(logged()) == 5)

# A rewatch is a new session, so it belongs to the history and the webhook.
# The sweep matches on identity, not time, and must stay out of it.
library((4, 1, "One", 1785000000))
check("a much later lastViewedAt on something already logged is ignored",
      plex_history.sweep() == 0)
check("...so a rewatch is never invented from a timestamp", len(logged()) == 5)


# --- a sweep must not undo a rename -----------------------------------------
# Plex sends its own spelling on every row, so without care the sweep walks a
# corrected title straight back in the day after it was fixed.

print("\nspelling")

db.insert_event({
    "watched_at": "2026-06-22T20:00:00+00:00", "source": "plex", "service": "Plex",
    "media_type": "episode", "title": "Correctly Spelled", "episode_title": "A",
    "year": 2026, "season": 1, "episode": 1,
    "dedup_key": "correctly spelled|episode|1|1",
})
plex_history.watched_in_library = lambda: (plex_history._as_history_row(r) for r in [{
    "type": "episode", "grandparentTitle": "CORRECTLY SPELLED",
    "grandparentRatingKey": "9", "parentIndex": 1, "index": 2, "title": "B",
    "viewCount": 1, "lastViewedAt": 1782000000,
}])
check("the shouty spelling is swept in", plex_history.sweep() == 1)
with db.connect() as conn:
    spellings = {r["title"] for r in conn.execute(
        "SELECT DISTINCT title FROM events WHERE title LIKE '%orrectly%' "
        "OR title LIKE '%ORRECTLY%'")}
check("...but stored under the spelling the log already uses",
      spellings == {"Correctly Spelled"})


# --- how far back it reaches ------------------------------------------------
# Plex's play history is trimmed over time; its library remembers viewCount
# forever. So an unbounded sweep does not find a few marked episodes, it finds
# every play Plex has since forgotten the session for. The floor is what keeps
# a repair from turning into an import of years of prehistory.

print("\nhow far back")

check("with nothing configured, the floor is the log's own first event",
      db.first_event_date() == "2026-06-21")

# Marked well before the log begins. A gap the log never claimed to cover.
library((4, 9, "Ancient", 1600000000))
check("something older than the log is left alone", plex_history.sweep() == 0)

# The same item, once the floor is widened to take it.
config.SWEEP_SINCE = "2000-01-01"
check("...and imported once the floor is moved back deliberately",
      plex_history.sweep() == 1)
config.SWEEP_SINCE = ""

library((4, 10, "Recent", 1782500000))
check("something inside the log's era is still swept normally",
      plex_history.sweep() == 1)


# --- saying no, permanently -------------------------------------------------
# Leaving something out is not saying no: the sweep offers it again tomorrow.
# A hidden row is a decision, and one the sweep already respects.

print("\ndismissing")

library((7, 1, "Wanted", 1782600000), (7, 2, "Wanted too", 1782600000))
plex_history.watched_in_library = (lambda rows: lambda: (
    plex_history._as_history_row(r) for r in rows))([
        {"type": "episode", "grandparentTitle": "Keep This",
         "grandparentRatingKey": "1", "parentIndex": 7, "index": 1,
         "title": "A", "viewCount": 1, "lastViewedAt": 1782600000},
        {"type": "episode", "grandparentTitle": "Family Share",
         "grandparentRatingKey": "2", "parentIndex": 1, "index": 1,
         "title": "B", "viewCount": 1, "lastViewedAt": 1782600000},
        {"type": "episode", "grandparentTitle": "Family Share",
         "grandparentRatingKey": "2", "parentIndex": 1, "index": 2,
         "title": "C", "viewCount": 1, "lastViewedAt": 1782600000},
    ])

from watchlog.grouping import normalize as _norm            # noqa: E402
found = plex_history.sweep(accept={_norm("Keep This")})
check("only the accepted title counts as imported", found == 1)

def state(title):
    with db.connect() as conn:
        return sorted(r["hidden"] for r in conn.execute(
            "SELECT hidden FROM events WHERE title = ?", (title,)))

check("the accepted show is visible", state("Keep This") == [0])
check("the rest is recorded but hidden, not simply skipped",
      state("Family Share") == [1, 1])

check("and running again offers nothing, because a decision was recorded",
      plex_history.sweep(accept={_norm("Keep This")}) == 0)
check("...not even with no accept list at all, which would otherwise take it",
      plex_history.sweep() == 0)
check("nothing was duplicated by either run",
      state("Family Share") == [1, 1] and state("Keep This") == [0])


# --- the heartbeat ----------------------------------------------------------

print("\nsaying it ran")

with db.connect() as conn:
    conn.execute("DELETE FROM meta")
check("with nothing recorded, a sweep is due", plex_history._sweep_due() is True)

library()
plex_history.sweep()
check("a sweep that found nothing still records that it ran",
      db.get_meta(config.META_SWEEP_OK) is not None)
check("...and is not due again straight away",
      plex_history._sweep_due() is False)
check("a sweep that found nothing does not claim a find",
      db.get_meta(config.META_SWEEP_FOUND) is None)

db.set_meta(config.META_SWEEP_OK, "2020-01-01T00:00:00+00:00")
check("a day later it is due again", plex_history._sweep_due() is True)
db.set_meta(config.META_SWEEP_OK, "not a date at all")
check("an unreadable timestamp means run, not skip forever",
      plex_history._sweep_due() is True)


print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
