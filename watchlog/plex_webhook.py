"""Receives Plex webhooks and records what was watched.

Plex Pass fires media.scrobble at 90% watched. Webhooks cannot carry custom
headers or basic auth, so the endpoint is protected by an unguessable path
segment instead. It binds to the LAN and is never forwarded.
"""
import hmac
import json
import logging
import re
import threading
from datetime import datetime, timezone

from flask import Flask, request

from . import config, db, publish, render
from .grouping import normalize

log = logging.getLogger("watchlog.plex")
app = Flask(__name__)

# Rendering and pushing on every event would mean a round trip to the web host
# per episode during a binge. Collect for a minute, then publish once.
PUBLISH_DEBOUNCE_SECONDS = 60
_timer = None
_timer_lock = threading.Lock()


def _extract_guid(metadata, scheme):
    """Plex supplies GUIDs like 'imdb://tt1234567'. Pull one out by scheme."""
    for entry in metadata.get("Guid") or []:
        value = entry.get("id", "")
        if value.startswith(f"{scheme}://"):
            return value.split("://", 1)[1]
    return None


def parse_scrobble(payload):
    """Plex payload -> an events row, or None if we don't care about it."""
    if payload.get("event") != "media.scrobble":
        return None

    account = (payload.get("Account") or {}).get("title", "")
    if config.PLEX_ACCOUNT_TITLE and account != config.PLEX_ACCOUNT_TITLE:
        log.info("ignoring scrobble for other account: %s", account)
        return None

    metadata = payload.get("Metadata") or {}
    kind = metadata.get("type")
    if kind not in ("movie", "episode"):
        log.info("ignoring media type: %s", kind)
        return None

    now = datetime.now(timezone.utc).isoformat()

    if kind == "movie":
        title = metadata.get("title") or "Unknown"
        season = episode = None
        episode_title = None
    else:
        title = metadata.get("grandparentTitle") or metadata.get("title") or "Unknown"
        season = metadata.get("parentIndex")
        episode = metadata.get("index")
        episode_title = metadata.get("title")

    return {
        "watched_at": now,
        "source": "plex",
        "service": "Plex",
        "media_type": kind,
        "title": title,
        "episode_title": episode_title,
        "year": metadata.get("year"),
        "season": season,
        "episode": episode,
        "imdb_id": _extract_guid(metadata, "imdb"),
        "tmdb_id": _extract_guid(metadata, "tmdb"),
        "dedup_key": f"{normalize(title)}|{kind}|{season}|{episode}",
        "hidden": 0,
        "raw": json.dumps(payload)[:8000],
    }


def schedule_publish():
    global _timer
    with _timer_lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(PUBLISH_DEBOUNCE_SECONDS, _publish_now)
        _timer.daemon = True
        _timer.start()


def _publish_now():
    try:
        render.write_page()
        publish.push()
    except Exception:
        log.exception("publish failed")


@app.post("/plex/<secret>")
def plex_hook(secret):
    if not config.PLEX_WEBHOOK_SECRET or not hmac.compare_digest(
        secret, config.PLEX_WEBHOOK_SECRET
    ):
        return "", 404

    raw = request.form.get("payload")
    if not raw:
        return "", 400

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "", 400

    event = parse_scrobble(payload)
    if event is None:
        return "", 204

    row_id = db.insert_event(event)
    if row_id is None:
        log.info("duplicate, ignored: %s", event["dedup_key"])
        return "", 204

    log.info("recorded %s: %s", event["media_type"], event["title"])
    # Proof of life. Nothing else can supply it: this listener is silent when
    # healthy, and reconcile now backfills whatever it drops, so a webhook that
    # died would otherwise have no visible consequence at all.
    try:
        db.set_meta(config.META_WEBHOOK_OK,
                    datetime.now(timezone.utc).isoformat())
    except Exception:
        log.exception("could not record webhook delivery")
    schedule_publish()
    return "", 204


@app.get("/health")
def health():
    return {"ok": True}


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    db.init()
    app.run(host="0.0.0.0", port=config.WEBHOOK_PORT)


if __name__ == "__main__":
    main()
