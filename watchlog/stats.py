"""What the log says about a show, without anyone having rated anything.

The log records what was watched and when. That turns out to be enough to rank
shows, but not in the obvious way: episode count is almost useless here, because
most of what gets watched is weekly-release television and the count therefore
measures how much has *aired*. Nine shows sitting on eight episodes is a release
schedule, not a preference.

Two things the schedule does not control, and this module measures both:

  - **The gap between nights.** Coming back the next night is a choice; coming
    back in seven days is the calendar. So a short median gap says "devoured",
    and inside a weekly show the *largest* gap says whether it was appointment
    viewing or something drifted away from and caught up on later.
  - **Whether a season was finished.** This needs TMDb's episode counts, which
    `enrich.refresh_seasons` fetches. Without them "eight episodes then nothing"
    is unreadable -- a complete season of one show and a walkout halfway through
    another are the same number.

Nothing here decides what a favourite is. It produces the evidence -- pace,
completion, how long ago -- and leaves the ranking to whoever asked, because
"favourite" means different things in the two cases above and any single score
would quietly pick one.
"""
import statistics
from collections import Counter, defaultdict

from . import config, db
from .grouping import night_of, normalize


def _pace(gaps, episodes):
    """What the rhythm of the nights says about who set it.

    One night has no gaps to measure, which would make a sitting of five
    episodes look like the emptiest signal in the log rather than the loudest.
    So the single-night case is decided on volume instead.
    """
    if not gaps:
        return "devoured" if episodes >= config.BINGE_EPISODES else "single"
    median = statistics.median(gaps)
    if median <= config.DEVOURED_MAX_GAP_DAYS:
        return "devoured"
    low, high = config.WEEKLY_GAP_DAYS
    if low <= median <= high:
        return "weekly"
    return "sporadic"


def _status(completion, quiet_days, airing):
    """Finished, still going, waiting on the show, or given up on.

    Completion alone cannot say: a season half-watched is "in progress" on
    Tuesday and "abandoned" a year later, and it is the same number both times.

    Nor is completion plus silence enough. A season still airing has an episode
    count that includes episodes nobody could have watched yet, so "7 of 12,
    quiet for two months" describes both walking out and being perfectly up to
    date. `airing` is what separates them, and getting this wrong is the one
    error here that would actually offend -- being told you abandoned a show
    you are waiting on.
    """
    if completion is not None and completion >= config.FINISHED_AT:
        return "finished"
    if quiet_days <= config.WATCHING_WITHIN_DAYS:
        return "watching"
    if airing:
        return "waiting"
    if quiet_days >= config.ABANDONED_AFTER_DAYS:
        return "abandoned"
    return "paused"


def show_seasons(events=None, today=None):
    """One record per show per season, newest activity first.

    Seasons rather than shows: a season is the unit that gets finished or
    abandoned, and someone who loved series four and never started series five
    has not abandoned anything.
    """
    rows = events if events is not None else db.visible_events()
    lengths = db.season_lengths()
    airing = db.airing_seasons()

    buckets = defaultdict(lambda: {
        "names": Counter(), "imdb": Counter(), "nights": set(),
        "episodes": set(), "unnumbered": 0,
    })
    for row in rows:
        if row["media_type"] != "episode":
            continue
        key = (normalize(row["title"]), row["season"])
        bucket = buckets[key]
        bucket["names"][row["title"]] += 1
        if row["imdb_id"]:
            bucket["imdb"][row["imdb_id"]] += 1
        bucket["nights"].add(night_of(row["watched_at"]))
        if row["episode"] is not None:
            bucket["episodes"].add(row["episode"])
        else:
            # Apple TV reports the series and nothing else, so these cannot be
            # deduplicated by number. Counting each one is the best available
            # guess and is never an overcount of *nights*.
            bucket["unnumbered"] += 1

    today = today or max(
        (night_of(r["watched_at"]) for r in rows if r["media_type"] == "episode"),
        default=None,
    )

    records = []
    for (_, season), bucket in buckets.items():
        nights = sorted(bucket["nights"])
        gaps = [(nights[i + 1] - nights[i]).days for i in range(len(nights) - 1)]
        imdb_id = bucket["imdb"].most_common(1)[0][0] if bucket["imdb"] else None
        watched = len(bucket["episodes"]) + bucket["unnumbered"]
        total = lengths.get((imdb_id, season)) if imdb_id and season else None
        # Capped at 1.0: a rewatch, or a season TMDb has since shortened, can
        # otherwise report someone as 110% finished.
        completion = min(watched / total, 1.0) if total else None
        quiet = (today - nights[-1]).days if today else 0

        records.append({
            "title": bucket["names"].most_common(1)[0][0],
            "imdb_id": imdb_id,
            "season": season,
            "episodes_watched": watched,
            "episodes_in_season": total,
            "completion": round(completion, 2) if completion is not None else None,
            "nights": len(nights),
            "first": nights[0].isoformat(),
            "last": nights[-1].isoformat(),
            "days_since": quiet,
            "median_gap_days": statistics.median(gaps) if gaps else None,
            # The tell for a weekly show: a max gap equal to the cadence means
            # never missing one, and anything above it means falling behind and
            # catching up, which is a weaker signal than the medians suggest.
            "max_gap_days": max(gaps) if gaps else None,
            "pace": _pace(gaps, watched),
            # Reported as well as used, so a reader can see why something is
            # "waiting" rather than having to trust the label.
            "still_airing": (imdb_id, season) in airing,
            "status": _status(completion, quiet, (imdb_id, season) in airing),
        })

    records.sort(key=lambda r: r["last"], reverse=True)
    return records
