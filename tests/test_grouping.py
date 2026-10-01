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
        service="Plex", year=2022, imdb_id="tt9288030", standout=0, standout_note=None):
    return {
        "id": id, "title": title, "episode_title": episode_title,
        "season": season, "episode": episode, "watched_at": watched_at,
        "media_type": media_type, "service": service, "year": year,
        "imdb_id": imdb_id, "standout": standout,
        "standout_note": standout_note,
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

# One episode twice in a night -- a rewatch, or Plex scrobbling again. The
# episode range already collapses to "E7"; the titles should match it.
twice = group([row(id=20, episode=7, episode_title="The Jordan Boys Legacy",
                   watched_at="2026-09-27T23:42:00+00:00"),
               row(id=21, episode=7, episode_title="The Jordan Boys Legacy",
                   watched_at="2026-09-28T02:52:00+00:00")])
check("the same episode twice in a night is named once",
      twice[0]["episode_names"] == "The Jordan Boys Legacy")
check("...and its range says E7, not E7 twice", twice[0]["detail"] == "S4 E7")

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

# --- standouts ---------------------------------------------------------------
# Marked per episode, shown per entry. A night of two where one was the one
# still reads as a standout night; which episode it was is the admin page's
# business, since that is where the marking happens.

print("\nstandouts")

night = group([row(id=1, episode=1, episode_title="One"),
               row(id=2, episode=2, episode_title="Two", standout=1)])
check("a night is a standout if any episode in it was",
      night[0]["standout"] is True)
check("an ordinary night is not", group([row(id=3)])[0]["standout"] is False)

film = group([row(id=4, media_type="movie", season=None, episode=None,
                  episode_title=None, standout=1)])
check("a film can be one too", film[0]["standout"] is True)

noted = group([row(id=5, episode=3, standout=1,
                   standout_note="Hal's astonishment. Sinestro as the reveal.")])
check("the note rides along with the entry",
      noted[0]["standout_note"].endswith("Sinestro as the reveal."))
check("...curled like the rest of the page, since it is prose someone typed",
      "Hal\u2019s" in noted[0]["standout_note"])

# A note on an episode nobody marked is not a note about anything.
check("an unmarked episode's note is not shown",
      group([row(id=6, standout=0, standout_note="left over")])[0]["standout_note"]
      is None)
check("a marked episode with nothing written reads as no note",
      group([row(id=7, standout=1)])[0]["standout_note"] is None)

both = group([row(id=8, episode=1, standout=1, standout_note="The box."),
              row(id=9, episode=2, standout=1, standout_note="Sinestro.")])
check("a night where both were the one lets both speak",
      both[0]["standout_note"] == "The box. \u00b7 Sinestro.")

mixed = group([row(id=10, episode=1, standout=1, standout_note="This one."),
               row(id=11, episode=2, standout=0, standout_note="Not this one.")])
check("...and only the marked ones",
      mixed[0]["standout_note"] == "This one.")


# --- typography --------------------------------------------------------------
# Plex sends straight apostrophes; Apple's metadata and anything typed on a Mac
# send curly ones. The display side settles it, without touching the keys that
# decide what counts as the same show.

from watchlog.grouping import curly_apostrophes, normalize    # noqa: E402

show = group([row(id=11, title="Bob's Burgers",
                  episode_title="Don't Stop Be-Leaf-ing")])
check("a straight apostrophe in a title is curled for display",
      show[0]["title"] == "Bob\u2019s Burgers")
check("...and in an episode title too",
      show[0]["episode_names"] == "Don\u2019t Stop Be-Leaf-ing")

film = group([row(id=12, title="If I Had Legs I'd Kick You", media_type="movie",
                  season=None, episode=None, episode_title=None)])
check("a film gets the same treatment",
      film[0]["title"] == "If I Had Legs I\u2019d Kick You")

check("an elision keeps its leading mark, curled as well",
      curly_apostrophes("'Salem's Lot") == "\u2019Salem\u2019s Lot")
check("a title with nothing to fix is left alone",
      curly_apostrophes("Reacher") == "Reacher")
check("None passes through, because episode_names can return it",
      curly_apostrophes(None) is None)

# Why this is safe to do at all: identity is computed from the raw row, and
# both forms normalise to the same key regardless.
check("both forms share one key, so dedup and title matching cannot notice",
      normalize("Bob's Burgers") == normalize("Bob\u2019s Burgers"))


print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
