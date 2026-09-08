"""Configuration, loaded from .env beside this package."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(key, default=None, required=False):
    value = os.getenv(key, default)
    if required and (not value or str(value).startswith("TODO")):
        raise RuntimeError(f"{key} is not set in {ROOT / '.env'}")
    return value


PLEX_WEBHOOK_SECRET = _get("PLEX_WEBHOOK_SECRET", "")
PLEX_ACCOUNT_TITLE = _get("PLEX_ACCOUNT_TITLE", "")
WEBHOOK_PORT = int(_get("WEBHOOK_PORT", "8420"))

ATV_IDENTIFIER = _get("ATV_IDENTIFIER", "")
ATV_AIRPLAY_CREDENTIALS = _get("ATV_AIRPLAY_CREDENTIALS", "")
ATV_COMPANION_CREDENTIALS = _get("ATV_COMPANION_CREDENTIALS", "")

TMDB_API_KEY = _get("TMDB_API_KEY", "")

NFSN_SSH_HOST = _get("NFSN_SSH_HOST", "")
NFSN_SSH_USER = _get("NFSN_SSH_USER", "")
NFSN_REMOTE_PATH = _get("NFSN_REMOTE_PATH", "/home/public/watchlog/")
NFSN_SSH_KEY = _get("NFSN_SSH_KEY", "")

ADMIN_PORT = int(_get("ADMIN_PORT", "8421"))
ADMIN_TOKEN = _get("ADMIN_TOKEN", "")

DB_PATH = Path(_get("WATCHLOG_DB", str(ROOT / "watchlog.db")))
OUT_PATH = Path(_get("WATCHLOG_OUT", str(ROOT / "out" / "watchlog.html")))
# The same log as data, published beside the page. Anything that wants to ask
# questions of the log reads this rather than scraping the HTML.
JSON_PATH = Path(_get("WATCHLOG_JSON", str(ROOT / "out" / "watchlog.json")))

# A "night" rolls over at 4am, so something started at 1am counts with the
# evening before rather than opening a new day with one stray episode.
NIGHT_ROLLOVER_HOUR = 4

# Matches Plex's own media.scrobble trigger, so both sensors agree.
WATCHED_THRESHOLD = 0.90

# --- reading the log back ----------------------------------------------------
# What the log can say about a show without anyone rating anything. These
# thresholds turn "when did he come back" into a label, and every one of them
# is a judgement call rather than a fact, so they live here where they can be
# argued with.

# How the pace of a show is described, from the median gap between the nights
# it was watched on. Below the first number he set the pace himself; inside the
# weekly band the release schedule set it, and keeping up is the signal.
DEVOURED_MAX_GAP_DAYS = 2
WEEKLY_GAP_DAYS = (5, 9)

# One night has no gaps to measure, but three episodes in one sitting is the
# most devoured thing in the log, not the least. Below this it is just a night.
BINGE_EPISODES = 3

# A season is called finished at this much of it, not at all of it: a stray
# unwatched episode is more often a recap or a special than a real gap.
FINISHED_AT = 0.95

# Quiet for this long with a season unfinished and it is no longer "paused".
# Deliberately generous -- a mid-season break is normal, and calling something
# abandoned that he is waiting on is the one wrong answer that would annoy.
ABANDONED_AFTER_DAYS = 60
WATCHING_WITHIN_DAYS = 21

# Season lengths come from TMDb and are cached. A running season's count can
# change as episodes are announced, so rows are re-read after this long.
SEASONS_REFRESH_DAYS = 7

# Plex records a second view when an episode is finished in a later session,
# so the same episode arrives twice, typically 8-23 hours apart. Four hours was
# too narrow to catch that. Genuine rewatches inside two days are rare; the
# artifact is not.
DEDUP_WINDOW_HOURS = 48

# The database keeps everything, and so does the page: None means no limit.
# Weighed against real numbers -- 150 entries render to 89KB, which the host
# serves as 9.3KB gzipped, because the markup is repetitive enough to compress
# almost 10:1. Even a decade of viewing stays trivial to serve. Set an integer
# here to cap it again; `entries[:None]` is the whole list, so nothing else
# needs to change.
PAGE_LIMIT = None

# The rail draws one hairline per entry into a fixed column about 650px tall.
# Past this many, ticks stop being distinguishable and the texture that makes
# the rail worth having turns into a solid bar -- so beyond it the fine ticks
# thin out and only every Nth entry gets one. Month markers are always kept.
RAIL_MAX_TICKS = 300

# How far back the hourly reconcile pass re-reads Plex's own history. PMS asks
# plex.tv for its webhook list only at startup; lose that request to a DNS race
# after a reboot and deliveries stop silently until the next restart. A week is
# comfortably longer than that goes unnoticed, and costs one page of history.
RECONCILE_DAYS = 7

# The library sweep reads every watched item in Plex, not just recent plays, so
# it is the only thing that can see an episode *marked* watched rather than
# played -- which creates no session and therefore never reaches the history
# endpoint reconcile reads. It costs a full library listing, and the gap it
# closes appears in ones and twos, so it runs daily rather than hourly.
SWEEP_EVERY_HOURS = 24

# How far back the sweep will reach. Left blank it stops at the log's own first
# event, which is the honest default: the log claims to cover from that day
# onwards, so filling gaps inside that era is repair, and importing what came
# before it is a different decision entirely.
#
# It matters more than it looks. Plex's play history is trimmed over time but
# its library remembers viewCount forever, so an unbounded sweep does not find
# "a few marked episodes" -- it finds every play Plex has since forgotten the
# session for, going back years.
SWEEP_SINCE = _get("WATCHLOG_SWEEP_SINCE", "")

# The timer runs hourly, so anything past this means it has missed a turn and
# the admin page should say so in red rather than stay quietly reassuring.
RECONCILE_STALE_AFTER_HOURS = 2

# Keys for the little meta table the health line reads. Defined here so the
# admin process can name them without importing the Apple TV listener, and with
# it pyatv.
META_RECONCILE_OK = "reconcile_ok_at"
META_RECONCILE_ERROR = "reconcile_error"
META_RECONCILE_ERROR_AT = "reconcile_error_at"
META_APPLETV_OK = "appletv_ok_at"

# Publishing is the last link in the chain and had no heartbeat: if the rsync to
# the web host started failing, everything on the Pi would still look perfect
# while the live page quietly went stale. Three of its four callers swallow the
# exception, so the record is written inside push() where every caller is
# covered. Age means nothing here -- no publish for three days is just three
# days of not watching anything -- so this line goes red on a failed attempt,
# never on silence.
META_PUBLISH_OK = "publish_ok_at"
META_PUBLISH_ERROR = "publish_error"
META_PUBLISH_ERROR_AT = "publish_error_at"

# The webhook is now covered by reconcile, which means it can die without any
# visible consequence at all -- the safety net just backfills it forever. The
# signal that it is rotting is reconcile having to recover anything: a play that
# the hourly pass finds is by definition a play the webhook did not deliver.
META_WEBHOOK_OK = "webhook_ok_at"
META_WEBHOOK_MISSED_AT = "webhook_missed_at"
META_WEBHOOK_MISSED = "webhook_missed_count"

META_SWEEP_OK = "sweep_ok_at"
META_SWEEP_FOUND = "sweep_found_count"
META_SWEEP_FOUND_AT = "sweep_found_at"

# Episode titles are listed for a night up to this many episodes, then withheld
# so a long binge doesn't turn one scannable line into a paragraph. Measured
# against real data: 229 of 231 entries are three episodes or fewer.
EPISODE_TITLES_MAX = 3

# Suggestions for the admin's "add an entry" service field, not a restriction --
# the field is free text. These are the platforms no sensor can reach: Netflix
# reports nothing from the Apple TV, and the rest were never wired up at all.
MANUAL_SERVICES = [
    "Netflix", "Prime Video", "Disney+", "Max", "Hulu",
    "Paramount+", "Peacock", "YouTube", "Theater",
]

PLEX_SERVER_URL = _get("PLEX_SERVER_URL", "")

# Push updates fire only on state change, so position has to be polled.
APPLETV_POLL_SECONDS = 30

# atv.metadata.playing() has no timeout of its own. A half-open connection --
# which is what a router reboot leaves behind -- makes it await forever, and the
# listener then sits there looking perfectly healthy: process up, socket still
# ESTABLISHED, not one line in the log. Measured on 2026-09-04, it had polled
# nothing for 11 hours. Bound the wait, and give up on the connection after a
# few in a row so the reconnect loop can do its job.
APPLETV_POLL_TIMEOUT = 15
APPLETV_MAX_POLL_FAILURES = 3

# How often the listener records that it is alive, and how long that record can
# go unrefreshed before the admin page calls it stale.
APPLETV_HEARTBEAT_SECONDS = 300
APPLETV_STALE_AFTER_MINUTES = 15

# An allowlist rather than a blocklist: only these apps are logged. This is also
# what keeps Plex-on-the-Apple-TV from being counted twice, since the Plex
# webhook already reports it regardless of which device played it.
#
# The bundle ids are real ones, read out of the listener's own journal: it logs
# "ignoring app <id> (<name>)" the first time it sees each app, so anything
# played on this device has already named itself there. Prime Video is the one
# exception below -- it has never appeared in that log, so its id is the
# published one rather than an observed one. If the guess is wrong nothing
# breaks quietly: the next Prime play writes an "ignoring app" line naming the
# real id, and that line is the fix.
#
# Known and deliberately left out: com.netflix.Netflix and
# com.google.ios.youtubeunplugged (YouTube TV) report nothing usable, and
# sport needs rules of its own before it belongs in a log built around
# seasons and episodes.
APPLETV_APPS = {
    "com.apple.TVWatchList": "Apple TV",
    "com.amazon.aiv.AIVApp": "Prime Video",
}
PLEX_TOKEN = _get("PLEX_TOKEN", "")
