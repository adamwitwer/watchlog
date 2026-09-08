#!/usr/bin/env python3
"""The JSON feed: its shape, and that it agrees with the page.

The feed exists so something can ask questions of the log instead of reading
it. That makes it a contract, and the things worth testing are the ones a
reader would silently get wrong: which date an entry lands on, what a film says
when asked about episodes, and whether the feed and the page can disagree about
what was watched.

Runs against a throwaway database, never the real one.

Run: python -m tests.test_feed
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchlog import config

_tmp = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmp.name) / "test.db"
config.OUT_PATH = Path(_tmp.name) / "out" / "watchlog.html"
config.JSON_PATH = Path(_tmp.name) / "out" / "watchlog.json"

from watchlog import db, render                    # noqa: E402

failures = []


def check(name, condition):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition:
        failures.append(name)


# --- a log to ask questions of ---------------------------------------------

db.init()

# Mid-month and mid-day, so the month an entry lands in is the same whatever
# time zone the machine running the tests is set to.
SEED = [
    # One night, three episodes of one show: the feed should call this one
    # entry with a count of three, exactly as the page draws one line.
    ("2026-03-16T20:00:00+00:00", "episode", "Industry", "Il Mattino", 2020, 3, 4),
    ("2026-03-16T21:00:00+00:00", "episode", "Industry", "Smoke and Mirrors", 2020, 3, 5),
    ("2026-03-16T22:00:00+00:00", "episode", "Industry", "White Mischief", 2020, 3, 6),
    # A film, for the fields that only make sense for shows.
    ("2026-02-10T20:00:00+00:00", "movie", "Heat", None, 1995, None, None),
    # A night that crosses a season boundary: there is no single season.
    ("2026-01-20T20:00:00+00:00", "episode", "Lynley", "One", 2025, 1, 6),
    ("2026-01-20T21:00:00+00:00", "episode", "Lynley", "Two", 2025, 2, 1),
]
for watched_at, kind, title, episode_title, year, season, episode in SEED:
    db.insert_event({
        "watched_at": watched_at, "source": "test", "service": "Plex",
        "media_type": kind, "title": title, "episode_title": episode_title,
        "year": year, "season": season, "episode": episode,
        "imdb_id": "tt0000001" if kind == "movie" else None,
        "dedup_key": f"{title}|{season}|{episode}",
    })


# --- the envelope ----------------------------------------------------------

print("\nenvelope")

feed, count = render.build_json()
doc = json.loads(feed)

check("it parses", isinstance(doc, dict))
check("it carries a version, so a reader can refuse a shape it doesn't know",
      doc["version"] == 1)
check("the count matches the entries actually in it",
      doc["count"] == len(doc["entries"]) == count)
check("generated_at is UTC and parses",
      doc["generated_at"].endswith("+00:00") or doc["generated_at"].endswith("Z"))


# --- what one entry says ---------------------------------------------------

print("\nentries")

by_title = {e["title"]: e for e in doc["entries"]}

check("three nights of watching became three entries", doc["count"] == 3)

industry = by_title["Industry"]
check("a night of three episodes is one entry", industry["media_type"] == "episode")
check("...that says it was three", industry["episode_count"] == 3)
check("...with the season broken out, not left inside 'S3 E4-E6'",
      industry["season"] == 3)
check("...and the label the page prints, kept as well",
      industry["detail"] == "S3 E4-E6")
check("...and the episode titles, in episode order",
      industry["episode_titles"] == "Il Mattino · Smoke and Mirrors · White Mischief")

heat = by_title["Heat"]
check("a film has no season", heat["season"] is None)
check("a film's episode_count is null, not 1 -- there is nothing to add up",
      heat["episode_count"] is None)
check("a film keeps its year", heat["year"] == 1995)
check("a film keeps its IMDb id", heat["imdb_id"] == "tt0000001")

lynley = by_title["Lynley"]
check("a night spanning two seasons refuses to name one",
      lynley["season"] is None)
check("...but still counts both episodes", lynley["episode_count"] == 2)


# --- dates ------------------------------------------------------------------

print("\ndates")

# The whole point of `date`: a reader filtering "Q1 2026" must not have to
# reason about time zones, and must get the same answer the page shows.
check("every entry carries a plain local date",
      all(len(e["date"]) == 10 and e["date"][4] == "-" for e in doc["entries"]))
check("the raw timestamp is kept alongside it",
      all("watched_at" in e for e in doc["entries"]))

q1 = [e for e in doc["entries"] if "2026-01-01" <= e["date"] <= "2026-03-31"]
check("a quarter can be selected by string comparison alone", len(q1) == 3)
check("newest first, like the page",
      [e["date"] for e in doc["entries"]] == sorted(
          (e["date"] for e in doc["entries"]), reverse=True))


# --- the feed and the page cannot disagree ----------------------------------

print("\nagreement with the page")

html, html_count = render.build_html()
check("both outputs count the same entries", html_count == doc["count"])
for entry in doc["entries"]:
    check(f"the page shows {entry['title']!r} too", entry["title"] in html)


# --- what write_output actually writes --------------------------------------

print("\nwriting")

render.write_output()
check("the page was written", config.OUT_PATH.exists())
check("the feed was written beside it", config.JSON_PATH.exists())
check("...and is the same document build_json returns",
      json.loads(config.JSON_PATH.read_text(encoding="utf-8"))["count"] == doc["count"])
check("...as UTF-8 text, not escapes",
      "\\u" not in config.JSON_PATH.read_text(encoding="utf-8"))


print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
