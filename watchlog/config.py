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

# A "night" rolls over at 4am, so something started at 1am counts with the
# evening before rather than opening a new day with one stray episode.
NIGHT_ROLLOVER_HOUR = 4

# Matches Plex's own media.scrobble trigger, so both sensors agree.
WATCHED_THRESHOLD = 0.90

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
APPLETV_APPS = {
    "com.apple.TVWatchList": "Apple TV",
}
PLEX_TOKEN = _get("PLEX_TOKEN", "")
