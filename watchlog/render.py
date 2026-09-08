"""Render the visible events into a self-contained HTML page and a JSON feed.

The page is what a person reads. The feed is the same log as data, for anything
that wants to ask questions of it rather than look at it -- which today means an
MCP server, and tomorrow whatever else. They are rendered from one call so the
two can never disagree about what was watched.
"""
import json
import logging
import math
import re
import unicodedata
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, db
from .grouping import group

log = logging.getLogger("watchlog.render")

TEMPLATES = config.ROOT / "templates"


def search_normalize(text):
    """Fold a string down to the form the on-page search compares.

    Lowercase, strip diacritics so "pokemon" finds "Pokemon", delete
    apostrophes rather than splitting on them so "bobs" finds "Bob's", and
    turn everything else non-alphanumeric into a single space.

    The JavaScript in the template does exactly this to the typed query. If
    one side changes the other has to change with it, or searches start
    missing things for no visible reason.
    """
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("\u2019", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _search_key(entry):
    """Everything the search matches on, for one entry.

    Title, the season/episode label, the episode titles and the service --
    but deliberately not the date or the year. A page this size has a lot of
    numbers on it, and typing "2019" to pull up one film would instead pull up
    every film released that year. Moving through time is the rail's job.
    """
    return search_normalize(" ".join(
        part for part in (entry["title"], entry["detail"],
                          entry["episode_names"], entry["service"])
        if part
    ))


def _date_label(iso):
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    return f"{moment.strftime('%B')} {moment.day}, {moment.year}"


def _entries():
    """The display entries, newest first -- what both outputs are built from."""
    return group(db.visible_events())[: config.PAGE_LIMIT]


def _json_entry(entry):
    """One display entry, as data.

    Deliberately the grouped entry rather than the raw event: a night of five
    episodes is one thing that happened, and that is the unit the page counts
    in too. `detail` is kept because it is exactly what the page prints, but
    `season` and `episode_count` sit beside it so nothing has to parse "S2
    E3-E6" back apart to answer a question.
    """
    moment = datetime.fromisoformat(
        entry["watched_at"].replace("Z", "+00:00")
    ).astimezone()
    return {
        # The local date, matching what the page prints. Filtering on the UTC
        # timestamp instead would put a late-night film in the wrong quarter.
        "date": moment.date().isoformat(),
        "watched_at": entry["watched_at"],
        "media_type": entry["media_type"],
        "title": entry["title"],
        "detail": entry["detail"],
        "season": entry["season"],
        # Null rather than 1 for a film: a film has no episodes, and saying
        # "1" invites something downstream to add them up.
        "episode_count": len(entry["ids"]) if entry["media_type"] == "episode" else None,
        "episode_titles": entry["episode_names"],
        "year": entry["year"],
        "service": entry["service"],
        "imdb_id": entry["imdb_id"],
    }


def build_json():
    """The whole log as one JSON document.

    Small enough to stay one document -- a year of watching is 58KB, 8.5KB
    gzipped -- so there is no paging, no query string, and nothing to keep in
    sync. A reader fetches it and has everything.
    """
    entries = _entries()
    payload = {
        # Bump this if a field changes meaning or leaves. Readers can then say
        # so instead of quietly answering from a shape they no longer understand.
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(entries),
        "entries": [_json_entry(entry) for entry in entries],
    }
    # ensure_ascii=False so "Naïve" stays readable rather than turning into
    # escapes; the file is served as UTF-8 either way.
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), len(entries)


def build_html():
    entries = _entries()

    # Everything the timeline rail needs: an anchor to jump to, and a flag on
    # the first entry of each month. Entries are newest first, so "first of the
    # month" means the topmost one as you read down.
    # Thin the fine ticks out once there are more entries than the rail can
    # draw distinctly. Month markers are exempt -- they are the navigation.
    step = max(1, math.ceil(len(entries) / config.RAIL_MAX_TICKS))

    previous_month = None
    for index, entry in enumerate(entries, start=1):
        moment = datetime.fromisoformat(
            entry["watched_at"].replace("Z", "+00:00")
        ).astimezone()
        entry["date_label"] = _date_label(entry["watched_at"])
        entry["anchor"] = f"e{index}"
        entry["q"] = _search_key(entry)
        month = (moment.year, moment.month)
        entry["month_start"] = month != previous_month
        entry["month_short"] = moment.strftime("%b")
        # A key the search can group by when it rebuilds the rail from
        # whatever survived the filter. "Feb" alone is not enough: filtered
        # results can put February 2026 directly above February 2025.
        entry["month_key"] = moment.strftime("%Y-%m")
        entry["rail_tick"] = entry["month_start"] or (index - 1) % step == 0
        previous_month = month

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("watchlog.html.j2")
    generated = datetime.now(timezone.utc).astimezone().strftime("%B %-d, %Y at %-I:%M %p")
    return template.render(entries=entries, generated_at=generated), len(entries)


def write_output():
    """Write both files. Every caller wants both, so neither is optional.

    Named for what it does rather than for the page alone: a `write_page` that
    quietly also wrote a JSON feed would be the kind of thing you only discover
    when the feed goes stale and nothing says why.
    """
    html, count = build_html()
    feed, _ = build_json()
    config.OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.OUT_PATH.write_text(html, encoding="utf-8")
    config.JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.JSON_PATH.write_text(feed, encoding="utf-8")
    log.info("rendered %d entries to %s and %s",
             count, config.OUT_PATH, config.JSON_PATH)
    return config.OUT_PATH


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init()
    print(write_output())
