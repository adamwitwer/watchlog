#!/usr/bin/env python3
"""Render the page from invented entries, to check the design without using
real viewing data. Writes to a path given as the first argument.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jinja2 import Environment, FileSystemLoader, select_autoescape

from watchlog.grouping import group

ROOT = Path(__file__).resolve().parent.parent


def iso(days_ago, hour=21):
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


# Invented, so the repo and any screenshots stay free of real viewing history.
DEMO = [
    dict(id=1, watched_at=iso(0), source="plex", service="Plex", media_type="episode",
         title="The Rehearsal", episode_title=None, year=2022, season=2, episode=6,
         imdb_id="tt13406094", tmdb_id=None, hidden=0),
    dict(id=2, watched_at=iso(0, 20), source="plex", service="Plex", media_type="episode",
         title="The Rehearsal", episode_title=None, year=2022, season=2, episode=5,
         imdb_id="tt13406094", tmdb_id=None, hidden=0),
    dict(id=3, watched_at=iso(0, 19), source="plex", service="Plex", media_type="episode",
         title="The Rehearsal", episode_title=None, year=2022, season=2, episode=4,
         imdb_id="tt13406094", tmdb_id=None, hidden=0),
    dict(id=4, watched_at=iso(1), source="appletv", service="Apple TV+",
         media_type="episode", title="Dark Matter", episode_title=None, year=2024,
         season=None, episode=None, imdb_id="tt13403238", tmdb_id=None, hidden=0),
    dict(id=5, watched_at=iso(2), source="plex", service="Plex", media_type="movie",
         title="The Third Man", episode_title=None, year=1949,
         season=None, episode=None, imdb_id="tt0041959", tmdb_id=None, hidden=0),
    dict(id=6, watched_at=iso(4), source="plex", service="Plex", media_type="episode",
         title="Slow Horses", episode_title=None, year=2022, season=3, episode=2,
         imdb_id="tt5875444", tmdb_id=None, hidden=0),
    dict(id=7, watched_at=iso(4, 20), source="plex", service="Plex", media_type="episode",
         title="Slow Horses", episode_title=None, year=2022, season=3, episode=1,
         imdb_id="tt5875444", tmdb_id=None, hidden=0),
    dict(id=8, watched_at=iso(9), source="plex", service="Plex", media_type="movie",
         title="Chungking Express", episode_title=None, year=1994,
         season=None, episode=None, imdb_id="tt0109424", tmdb_id=None, hidden=0),
]


def date_label(value):
    moment = datetime.fromisoformat(value).astimezone()
    return f"{moment.strftime('%B')} {moment.day}, {moment.year}"


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out" / "demo.html"
    entries = group(DEMO)
    for entry in entries:
        entry["date_label"] = date_label(entry["watched_at"])

    env = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    generated = datetime.now().strftime("%B %-d, %Y at %-I:%M %p")
    html = env.get_template("watchlog.html.j2").render(
        entries=entries, generated_at=generated)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"{len(entries)} entries -> {out}")
    for e in entries:
        print(f"  {e['date_label']:22} {e['title']:20} {e['detail'] or '':12} {e['service']}")


if __name__ == "__main__":
    main()
