#!/usr/bin/env python3
"""The on-page filter: what it matches, and the Python/JavaScript seam.

The page folds a query in the browser and compares it against a data-q that
Python folded at build time. Two implementations of one rule is the risk here,
so the last section runs the template's own JavaScript against the Python and
insists they agree. That part needs node; without it the rest still runs.

Runs against a throwaway database, never the real one.

Run: python -m tests.test_search
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchlog import config

_tmp = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmp.name) / "test.db"

from watchlog import db, render                    # noqa: E402
from watchlog.render import _search_key, search_normalize   # noqa: E402

failures = []


def check(name, condition):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition:
        failures.append(name)


# --- folding ---------------------------------------------------------------

print("\nfolding")

check("case is flattened", search_normalize("Star TREK") == "star trek")
check("diacritics come off so 'pokemon' can find 'Pokémon'",
      search_normalize("Pokémon") == "pokemon")
check("apostrophes are deleted, not split on, so 'bobs' finds \"Bob's\"",
      search_normalize("Bob's Burgers") == "bobs burgers")
check("a curly apostrophe folds the same way",
      search_normalize("Bob’s") == search_normalize("Bob's") == "bobs")
check("punctuation becomes a space",
      search_normalize("Star Trek: Strange New Worlds")
      == "star trek strange new worlds")
check("runs of punctuation and space collapse to one",
      search_normalize("X-Men  --  '97") == "x men 97")
check("the edges are trimmed", search_normalize("  Silo!  ") == "silo")
check("digits survive", search_normalize("Se7en 2049") == "se7en 2049")
check("nothing in, nothing out",
      search_normalize("") == "" and search_normalize(None) == "")


# --- what a row offers the filter ------------------------------------------

print("\nsearch keys")


def entry(**over):
    base = {"title": "Severance", "detail": "S2 E1-E3",
            "episode_names": "Hello, Ms. Cobel", "service": "Apple TV",
            "year": 2022}
    base.update(over)
    return base


key = _search_key(entry())
check("the title is in there", "severance" in key)
check("so is the season and episode label", "s2 e1 e3" in key)
check("so are the episode titles", "hello ms cobel" in key)
check("so is the service", "apple tv" in key)
check("the year is deliberately not, or every 2022 film would match",
      "2022" not in key)

check("a movie with no detail or episode names still folds cleanly",
      _search_key(entry(detail=None, episode_names=None, title="Heat",
                        service="Plex")) == "heat plex")


# --- the rendered page -----------------------------------------------------

print("\nrendered page")

db.init()
# Mid-month and mid-day, so the month a row lands in is the same whatever
# time zone the machine running the tests is set to.
SEED = [
    ("2026-03-16T20:00:00+00:00", "episode", "Severance", "Hello, Ms. Cobel",
     2022, 2, 1),
    ("2026-03-15T20:00:00+00:00", "episode", "Severance", "Goodbye, Mrs. Selvig",
     2022, 2, 2),
    ("2026-01-20T20:00:00+00:00", "movie", "Heat", None, 1995, None, None),
]
for watched_at, kind, title, episode_title, year, season, episode in SEED:
    db.insert_event({
        "watched_at": watched_at, "source": "test", "service": "Plex",
        "media_type": kind, "title": title, "episode_title": episode_title,
        "year": year, "season": season, "episode": episode,
        "dedup_key": f"{title}|{season}|{episode}",
    })

html, count = render.build_html()
check("all three entries rendered", count == 3)

rows = re.findall(r'<li id="(e\d+)" data-q="([^"]*)"\s+'
                  r'data-mkey="([^"]*)" data-mon="([^"]*)">', html)
check("every entry carries what the filter reads", len(rows) == 3)
check("the two Severance nights collapsed into one month key, and Heat is its own",
      [r[2] for r in rows] == ["2026-03", "2026-03", "2026-01"])
check("the month abbreviation is there for the rebuilt rail",
      [r[3] for r in rows] == ["Mar", "Mar", "Jan"])
check("a data-q holds the folded row",
      rows[0][1] == "severance s2 e1 hello ms cobel plex")
check("the movie's data-q has no season label",
      rows[2][1] == "heat plex")

check("the box ships hidden, so no-JavaScript never sees a dead input",
      '<div class="search" hidden>' in html)
check("the script is inline, like everything else on this page",
      "<script>" in html and "src=" not in html.split("<script>")[1][:200])


# --- the two folds have to agree -------------------------------------------

print("\nPython/JavaScript parity")

CORPUS = ["Star Trek: Strange New Worlds", "Bob's Burgers", "Bob’s",
          "Pokémon", "X-Men  --  '97", "  Silo!  ", "Se7en 2049",
          "The Gentlemen S2 E1", "Naïve Café", "", "don't die", "A—B"]

node = shutil.which("node")
if not node:
    print("  SKIP  node is not installed; cannot run the template's fold()")
else:
    source = (config.ROOT / "templates" / "watchlog.html.j2").read_text()
    fold = re.search(r"function fold\(text\) \{.*?\n  \}", source, re.S)
    check("the template still has a fold() to compare against", bool(fold))
    if fold:
        script = (fold.group(0) + "\n"
                  + "console.log(JSON.stringify("
                  + json.dumps(CORPUS) + ".map(fold)));")
        js = json.loads(subprocess.run([node, "-e", script],
                                       capture_output=True, text=True,
                                       check=True).stdout)
        py = [search_normalize(text) for text in CORPUS]
        for text, a, b in zip(CORPUS, py, js):
            check(f"both folds agree on {text!r} -> {a!r}", a == b)

print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
