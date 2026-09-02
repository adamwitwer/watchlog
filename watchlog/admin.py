"""The delete surface.

Deliberately small and deliberately private: it binds to the LAN and the
Tailnet, never to the internet, and is the only interaction Watchlog asks for.

Deleting hides rather than destroys, so a mistaken delete can be undone. An
entry on the page may be several events -- a night of episodes -- so deleting
one hides the whole group.
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


def _decorate(entries):
    for entry in entries:
        entry["date_label"] = _date_label(entry["watched_at"])
        entry["id_list"] = ",".join(str(i) for i in entry["ids"])
    return entries


@app.get("/")
def index():
    if not _authorised():
        return "Not found", 404

    rows = db.recent_events(limit=400)
    visible = _decorate(group([r for r in rows if not r["hidden"]]))
    hidden = _decorate(group([r for r in rows if r["hidden"]]))

    response = make_response(
        render_template("admin.html.j2", visible=visible, hidden=hidden,
                        published=config.PAGE_LIMIT)
    )
    # Accept the token once in the URL, then keep it in a cookie so the page
    # can be bookmarked without the secret sitting in browser history.
    if request.args.get("token"):
        response.set_cookie(COOKIE, config.ADMIN_TOKEN, max_age=60 * 60 * 24 * 365,
                            httponly=True, samesite="Lax")
    return response


def _apply(hidden):
    ids = [int(i) for i in request.form.get("ids", "").split(",") if i.strip()]
    if not ids:
        return
    db.set_hidden(ids, hidden=hidden)
    log.info("%s %d event(s)", "hid" if hidden else "restored", len(ids))
    try:
        render.write_page()
        publish.push()
    except Exception:
        log.exception("republish after change failed")


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


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    db.init()
    app.run(host="0.0.0.0", port=config.ADMIN_PORT)


if __name__ == "__main__":
    main()
