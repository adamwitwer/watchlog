"""Turn raw watch events into the entries the page displays.

Movies are events in their own right and stay individual. Episodes collapse
into one entry per show per night, because six lines for one evening's
bingeing buries everything else on a page built around large type.
"""
import re
from datetime import datetime, timedelta

from .config import NIGHT_ROLLOVER_HOUR


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


def episode_label(rows):
    """'S2 E3-E6' when we know the numbers, None when we don't.

    Apple TV+ entries have no season or episode data at all -- the device
    reports the series and nothing more -- so those simply carry no label.
    """
    seasons = {r["season"] for r in rows if r["season"] is not None}
    episodes = [r["episode"] for r in rows if r["episode"] is not None]
    if not episodes:
        return None
    if len(seasons) == 1:
        return f"S{seasons.pop()} {format_episodes(episodes)}"
    return format_episodes(episodes)


def group(rows):
    """Rows (newest first) -> display entries (newest first)."""
    entries = []
    buckets = {}

    for row in rows:
        if row["media_type"] == "movie":
            entries.append({
                "ids": [row["id"]],
                "watched_at": row["watched_at"],
                "title": row["title"],
                "detail": None,
                "year": row["year"],
                "service": row["service"],
                "imdb_id": row["imdb_id"],
                "media_type": "movie",
            })
            continue

        key = (normalize(row["title"]), night_of(row["watched_at"]))
        buckets.setdefault(key, []).append(row)

    for rows_in_bucket in buckets.values():
        newest = rows_in_bucket[0]
        entries.append({
            "ids": [r["id"] for r in rows_in_bucket],
            "watched_at": newest["watched_at"],
            "title": newest["title"],
            "detail": episode_label(rows_in_bucket),
            "year": newest["year"],
            "service": newest["service"],
            "imdb_id": next((r["imdb_id"] for r in rows_in_bucket if r["imdb_id"]), None),
            "media_type": "episode",
        })

    entries.sort(key=lambda e: e["watched_at"], reverse=True)
    return entries
