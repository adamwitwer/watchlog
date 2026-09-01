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
