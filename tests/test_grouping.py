#!/usr/bin/env python3
"""Grouping and episode-title rules, exercised without a database.

Run: python -m tests.test_grouping
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchlog.grouping import episode_names, group

failures = []


def check(name, condition):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition:
        failures.append(name)


def row(id=1, title="Reacher", episode_title=None, season=4, episode=1,
        watched_at="2026-09-02T23:00:00+00:00", media_type="episode",
        service="Plex", year=2022, imdb_id="tt9288030"):
    return {
        "id": id, "title": title, "episode_title": episode_title,
        "season": season, "episode": episode, "watched_at": watched_at,
        "media_type": media_type, "service": service, "year": year,
        "imdb_id": imdb_id,
    }


# --- episode_names -----------------------------------------------------------

check("no episode titles means no line at all",
      episode_names([row(episode_title=None)]) is None)

check("blank episode titles count as absent",
      episode_names([row(episode_title="   ")]) is None)

check("a single episode title comes through",
      episode_names([row(episode_title="Plum Out of Luck")]) == "Plum Out of Luck")

two = [row(id=2, episode=2, episode_title="Second"),
       row(id=1, episode=1, episode_title="First")]
check("titles are listed in episode order, not row order",
      episode_names(two) == "First · Second")

three = [row(id=i, episode=i, episode_title=f"Ep{i}") for i in (3, 2, 1)]
check("three titles still list", episode_names(three) == "Ep1 · Ep2 · Ep3")

four = [row(id=i, episode=i, episode_title=f"Ep{i}") for i in (4, 3, 2, 1)]
check("a long binge withholds them rather than wrapping the page",
      episode_names(four) is None)

check("the limit is a parameter, not a rule",
      episode_names(four, limit=4) == "Ep1 · Ep2 · Ep3 · Ep4")

# An Apple TV row has no episode number; ordering must not blow up on None.
mixed = [row(id=2, episode=None, episode_title="Unnumbered"),
         row(id=1, episode=1, episode_title="Numbered")]
check("a missing episode number sorts last instead of raising",
      episode_names(mixed) == "Numbered · Unnumbered")

check("Plex's 'Episode 4' placeholder is not a title",
      episode_names([row(episode_title="Episode 4")]) is None)

check("a real title that merely looks numbered is kept",
      episode_names([row(episode_title="Chapter 5")]) == "Chapter 5")

check("placeholders drop out, real titles survive alongside them",
      episode_names([row(id=2, episode=2, episode_title="Episode 2"),
                     row(id=1, episode=1, episode_title="Pilot")]) == "Pilot")

# --- group -------------------------------------------------------------------

entries = group([row(id=2, episode=2, episode_title="Second"),
                 row(id=1, episode=1, episode_title="First")])
check("one night of episodes is one entry", len(entries) == 1)
check("the entry carries the episode range", entries[0]["detail"] == "S4 E1-E2")
check("the entry carries the episode names",
      entries[0]["episode_names"] == "First · Second")

movie = group([row(media_type="movie", episode_title=None, season=None,
                   episode=None, title="Tuner")])
check("movies stay individual and carry no episode names",
      len(movie) == 1 and movie[0]["episode_names"] is None)

appletv = group([row(id=9, title="Silo", season=None, episode=None,
                     episode_title=None, service="Apple TV")])
check("an unedited Apple TV entry has neither label nor names",
      appletv[0]["detail"] is None and appletv[0]["episode_names"] is None)

edited = group([row(id=9, title="Silo", season=2, episode=4,
                    episode_title="Descent", service="Apple TV")])
check("once typed in by hand, it reads like a Plex entry",
      edited[0]["detail"] == "S2 E4" and edited[0]["episode_names"] == "Descent")

print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
