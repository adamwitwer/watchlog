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
"""
import hmac
import logging

from flask import (Flask, make_response, redirect, render_template,
                   request, url_for)

from . import config, db, publish, render
from .grouping import group

log = logging.getLogger("watchlog.admin")
app = Flask(__name__, template_folder=str(config.ROOT / "templates"))

COOKIE = "watchlog_admin"


def _authorised():
    supplied = request.cookies.get(COOKIE) or request.args.get("token", "")
    return bool(config.ADMIN_TOKEN) and hmac.compare_digest(
        supplied, config.ADMIN_TOKEN
    )


def _date_label(iso):
    from datetime import datetime
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    return f"{moment.strftime('%b')} {moment.day}, {moment.year}"


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
                        error=request.args.get("error"))
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


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    db.init()
    app.run(host="0.0.0.0", port=config.ADMIN_PORT)


if __name__ == "__main__":
    main()
