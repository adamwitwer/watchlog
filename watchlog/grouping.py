"""Turn raw watch events into the entries the page displays.

Movies are events in their own right and stay individual. Episodes collapse
into one entry per show per night, because six lines for one evening's
bingeing buries everything else on a page built around large type.
"""
import re
from datetime import datetime, timedelta

from .config import EPISODE_TITLES_MAX, NIGHT_ROLLOVER_HOUR


def curly_apostrophes(text):
    """Straight apostrophes to typographic ones, for anything displayed.

    Sources disagree: Plex sends "Bob's", while Apple's metadata and anything
    typed on a Mac send "Bob’s". On a page built around large type that
    reads as sloppiness, so the display side settles it one way.

    Only apostrophes. Directional quotation marks need to know whether each
    one opens or closes, and dashes need to know what they join; both are real
    parsers with real failure modes. An apostrophe is the case where replacing
    every straight mark is right essentially always -- including elisions like
    "'Salem's Lot", where the leading mark is an apostrophe too.

    Nothing downstream sees the difference: search_normalize and the template's
    fold() both delete either form before comparing, and normalize() below
    strips everything non-alphanumeric, so dedup keys are identical either way.
    """
    return text.replace("'", "’") if text else text


def normalize(title):
    """A loose key for matching the same show across sources and spellings."""
    text = (title or "").lower().strip()
    text = re.sub(r"^(the|a|an)\s+", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def night_of(watched_at):
    """The date an event belongs to, with the day rolling over at 4am."""
    moment = datetime.fromisoformat(watched_at.replace("Z", "+00:00"))
    return (moment - timedelta(hours=NIGHT_ROLLOVER_HOUR)).date()


def format_episodes(numbers):
    """[3,4,5,7] -> 'E3-E5, E7'. Contiguous runs collapse, gaps don't."""
    numbers = sorted({n for n in numbers if n is not None})
    if not numbers:
        return None

    runs = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        runs.append((start, previous))
        start = previous = number
    runs.append((start, previous))

    return ", ".join(
        f"E{a}" if a == b else f"E{a}-E{b}" for a, b in runs
    )


def single_season(rows):
    """The season number when a night sits in exactly one, else None.

    The label already says "S2" in its own string, but a machine reading the
    JSON feed should not have to parse English out of it. A night that spans a
    season boundary genuinely has no single answer, and says so.
    """
    seasons = {r["season"] for r in rows if r["season"] is not None}
    return seasons.pop() if len(seasons) == 1 else None


def episode_label(rows):
    """'S2 E3-E6' when we know the numbers, None when we don't.

    Apple TV+ entries have no season or episode data at all -- the device
    reports the series and nothing more -- so those simply carry no label.
    """
    episodes = [r["episode"] for r in rows if r["episode"] is not None]
    if not episodes:
        return None
    season = single_season(rows)
    if season is not None:
        return f"S{season} {format_episodes(episodes)}"
    return format_episodes(episodes)


# Plex stores "Episode 4" when it has no real title for an episode. Printing it
# under "S1 E3-E4" says nothing the label above hasn't already said. Named
# conventions like "Chapter 5" are left alone -- those are real titles.
PLACEHOLDER_TITLE = re.compile(r"^episode\s+\d+$", re.IGNORECASE)


def episode_names(rows, limit=EPISODE_TITLES_MAX):
    """The episode titles for one night, in episode order, or None.

    Withheld once a night runs past `limit` episodes: the page is built around
    one scannable line per entry, and six titles turns that line into a
    paragraph. The E-range still says what was watched.

    Apple TV entries have no episode title unless one was typed in by hand, so
    most of them return None here until they are edited.
    """
    named = [r for r in rows
             if (r["episode_title"] or "").strip()
             and not PLACEHOLDER_TITLE.match(r["episode_title"].strip())]
    if not named or len(named) > limit:
        return None

    ordered = sorted(
        named,
        key=lambda r: (r["episode"] is None, r["episode"] or 0, r["watched_at"]),
    )
    return " \u00b7 ".join(r["episode_title"].strip() for r in ordered)


def group(rows):
    """Rows (newest first) -> display entries (newest first)."""
    entries = []
    buckets = {}

    for row in rows:
        if row["media_type"] == "movie":
            entries.append({
                "ids": [row["id"]],
                "watched_at": row["watched_at"],
                "title": curly_apostrophes(row["title"]),
                "detail": None,
                "episode_names": None,
                "year": row["year"],
                "service": row["service"],
                "imdb_id": row["imdb_id"],
                "media_type": "movie",
                "season": None,
                "standout": bool(row["standout"]),
                "standout_note": curly_apostrophes(row["standout_note"])
                                 if row["standout"] else None,
            })
            continue

        key = (normalize(row["title"]), night_of(row["watched_at"]))
        buckets.setdefault(key, []).append(row)

    for rows_in_bucket in buckets.values():
        newest = rows_in_bucket[0]
        entries.append({
            "ids": [r["id"] for r in rows_in_bucket],
            "watched_at": newest["watched_at"],
            "title": curly_apostrophes(newest["title"]),
            "detail": episode_label(rows_in_bucket),
            "episode_names": curly_apostrophes(episode_names(rows_in_bucket)),
            "year": newest["year"],
            "service": newest["service"],
            "imdb_id": next((r["imdb_id"] for r in rows_in_bucket if r["imdb_id"]), None),
            "media_type": "episode",
            "season": single_season(rows_in_bucket),
            # A night is a standout if any episode in it was. Which one it was
            # is a question for the admin page, where the marking happens.
            "standout": any(r["standout"] for r in rows_in_bucket),
            # Only the marked episodes' notes, joined the way episode titles
            # are: on a night of two where both were the one, both get to speak.
            # Curled like everything else on the page: it is prose, and it is
            # the one line here someone typed by hand, so it is the most likely
            # source of a straight apostrophe.
            "standout_note": curly_apostrophes(" \u00b7 ".join(
                r["standout_note"] for r in rows_in_bucket
                if r["standout"] and (r["standout_note"] or "").strip()
            )) or None,
        })

    entries.sort(key=lambda e: e["watched_at"], reverse=True)
    return entries
