"""The delete surface.

Deliberately small and deliberately private: it binds to the LAN and the
Tailnet, never to the internet, and is the only interaction Watchlog asks for.

Deleting hides rather than destroys, so a mistaken delete can be undone. An
entry on the page may be several events -- a night of episodes -- so deleting
one hides the whole group.

It also accepts season, episode and episode title by hand, because the Apple TV
reports none of them: the device gives a series name and nothing else. Typing is
the only way those entries get to parity with the Plex ones, and it is offered
only for entries backed by a single event, which every Apple TV entry is.

And it accepts whole entries by hand, for the platforms no sensor reaches --
Netflix above all, which reports nothing at all from the Apple TV. This is a
departure from "logged automatically", but not from the rule that mattered:
nothing stands between watching and publishing, and an entry typed after the
fact adds no confirmation step to the automatic path.
"""
import hmac
import logging
from datetime import datetime, time, timezone

from flask import (Flask, make_response, redirect, render_template,
                   request, url_for)

from . import config, db, enrich, publish, render
from .grouping import group, normalize

log = logging.getLogger("watchlog.admin")
app = Flask(__name__, template_folder=str(config.ROOT / "templates"))

COOKIE = "watchlog_admin"


def _authorised():
    supplied = request.cookies.get(COOKIE) or request.args.get("token", "")
    return bool(config.ADMIN_TOKEN) and hmac.compare_digest(
        supplied, config.ADMIN_TOKEN
    )


def _date_label(iso):
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    return f"{moment.strftime('%b')} {moment.day}, {moment.year}"


def _ago(iso):
    """'12 minutes ago', roughly. Precision past the useful point is noise."""
    delta = datetime.now(timezone.utc) - datetime.fromisoformat(iso)
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return f"{hours // 24} days ago"


def _beat(label, key, stale_after_seconds, cadence, error_key=None,
          error_at_key=None):
    """One line of 'is this thing actually running'.

    Both sensors are silent when healthy and idle, so silence proves nothing.
    A heartbeat that has stopped being refreshed is the only difference between
    working and wedged, and stale counts as broken.
    """
    ok_at = db.get_meta(key)
    error = db.get_meta(error_key) if error_key else None
    error_at = db.get_meta(error_at_key) if error_at_key else None

    if not ok_at:
        return {"label": label, "state": "unknown",
                "text": f"{label} has not reported yet.", "error": error or None,
                "error_ago": None}

    age = (datetime.now(timezone.utc) - datetime.fromisoformat(ok_at)).total_seconds()
    stale = age > stale_after_seconds
    text = f"{label} {_ago(ok_at)}"
    if stale:
        text += f" — {cadence}, so something is wrong"
    return {
        "label": label,
        "state": "stale" if stale else "ok",
        "text": text,
        # Only worth showing while it is still the current story.
        "error": (error or None) if stale else None,
        "error_ago": _ago(error_at) if error and error_at and stale else None,
    }


def _health():
    return [
        _beat("Last reconcile", config.META_RECONCILE_OK,
              config.RECONCILE_STALE_AFTER_HOURS * 3600, "it runs hourly",
              config.META_RECONCILE_ERROR, config.META_RECONCILE_ERROR_AT),
        _beat("Apple TV listener polled", config.META_APPLETV_OK,
              config.APPLETV_STALE_AFTER_MINUTES * 60, "it polls constantly"),
    ]


def _decorate(entries, by_id=None):
    for entry in entries:
        entry["date_label"] = _date_label(entry["watched_at"])
        entry["id_list"] = ",".join(str(i) for i in entry["ids"])
        # Editing writes to one row, so it is offered only where the entry is
        # one row. A multi-episode night has no single season/episode to set.
        entry["editable"] = None
        if by_id and len(entry["ids"]) == 1 and entry["media_type"] == "episode":
            entry["editable"] = by_id.get(entry["ids"][0])
    return entries


@app.get("/")
def index():
    if not _authorised():
        return "Not found", 404

    rows = db.recent_events(limit=400)
    by_id = {r["id"]: r for r in rows}
    visible = _decorate(group([r for r in rows if not r["hidden"]]), by_id)
    hidden = _decorate(group([r for r in rows if r["hidden"]]))

    response = make_response(
        render_template("admin.html.j2", visible=visible, hidden=hidden,
                        published=config.PAGE_LIMIT,
                        services=config.MANUAL_SERVICES,
                        health=_health(),
                        today=datetime.now().astimezone().date().isoformat(),
                        error=request.args.get("error"),
                        notice=request.args.get("notice"))
    )
    # Accept the token once in the URL, then keep it in a cookie so the page
    # can be bookmarked without the secret sitting in browser history.
    if request.args.get("token"):
        response.set_cookie(COOKIE, config.ADMIN_TOKEN, max_age=60 * 60 * 24 * 365,
                            httponly=True, samesite="Lax")
    return response


def _republish():
    try:
        render.write_page()
        publish.push()
    except Exception:
        log.exception("republish after change failed")


def _apply(hidden):
    ids = [int(i) for i in request.form.get("ids", "").split(",") if i.strip()]
    if not ids:
        return
    db.set_hidden(ids, hidden=hidden)
    log.info("%s %d event(s)", "hid" if hidden else "restored", len(ids))
    _republish()


@app.post("/hide")
def hide():
    if not _authorised():
        return "Not found", 404
    _apply(True)
    return redirect(url_for("index"))


@app.post("/restore")
def restore():
    if not _authorised():
        return "Not found", 404
    _apply(False)
    return redirect(url_for("index"))


class _BadField(Exception):
    pass


def _optional_int(name):
    """A whole number, or None for a field left blank."""
    raw = (request.form.get(name) or "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise _BadField(f"{name} must be a whole number")
    return int(raw)


@app.post("/edit")
def edit():
    if not _authorised():
        return "Not found", 404

    try:
        event_id = int(request.form.get("id", ""))
        season = _optional_int("season")
        episode = _optional_int("episode")
    except (ValueError, _BadField) as exc:
        # Say so rather than silently discarding what was typed.
        log.warning("rejected edit: %s", exc)
        return redirect(url_for("index", error=str(exc)))

    episode_title = (request.form.get("episode_title") or "").strip()[:200] or None
    db.update_details(event_id, season, episode, episode_title)
    log.info("edited event %s: S%s E%s %r", event_id, season, episode, episode_title)
    _republish()
    return redirect(url_for("index"))


# Manual entries are anchored at 9pm local on the chosen date. Any evening hour
# would do; what matters is landing unambiguously inside that date's night, clear
# of both midnight and the 4am rollover, so the entry files under the day typed in.
MANUAL_HOUR = 21


def _watched_at(date_text):
    """A chosen calendar date -> the UTC timestamp of that night."""
    try:
        day = datetime.strptime(date_text.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        raise _BadField("date must be a real date")
    local = datetime.combine(day, time(MANUAL_HOUR)).astimezone()
    return local.astimezone(timezone.utc).isoformat()


@app.post("/add")
def add():
    if not _authorised():
        return "Not found", 404

    try:
        title = (request.form.get("title") or "").strip()[:200]
        if not title:
            raise _BadField("a title is required")
        service = (request.form.get("service") or "").strip()[:40]
        if not service:
            raise _BadField("a service is required")

        watched_at = _watched_at(request.form.get("date"))
        kind = "movie" if request.form.get("media_type") == "movie" else "episode"
        season = _optional_int("season")
        episode = _optional_int("episode")
        year = _optional_int("year")
    except _BadField as exc:
        log.warning("rejected new entry: %s", exc)
        return redirect(url_for("index", error=str(exc)))

    episode_title = (request.form.get("episode_title") or "").strip()[:200] or None
    if kind == "movie":
        # Seasons and episode titles belong to episodes; drop them rather than
        # storing fields that would render as nonsense on a film.
        season = episode = None
        episode_title = None

    event = {
        "watched_at": watched_at,
        "source": "manual",
        "service": service,
        "media_type": kind,
        "title": title,
        "episode_title": episode_title,
        "year": year,
        "season": season,
        "episode": episode,
        "imdb_id": None,
        "tmdb_id": None,
        # The same shape the sensors write, so a hand-typed entry and a
        # scrobble for the same episode still recognise each other.
        "dedup_key": f"{normalize(title)}|{kind}|{season}|{episode}",
        "hidden": 0,
        "raw": None,
    }

    if db.insert_event(event) is None:
        # Refusing silently would look like the form did nothing.
        log.info("duplicate manual entry ignored: %s", event["dedup_key"])
        return redirect(url_for(
            "index",
            error=f"{title} is already logged within "
                  f"{config.DEDUP_WINDOW_HOURS} hours of that date",
        ))

    log.info("added %s: %s (%s) on %s", kind, title, service, watched_at[:10])
    try:
        enrich.enrich_pending()
    except Exception:
        log.exception("enrichment failed; publishing anyway")
    _republish()
    return redirect(url_for("index", notice=f"Added {title}."))


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    db.init()
    app.run(host="0.0.0.0", port=config.ADMIN_PORT)


if __name__ == "__main__":
    main()
