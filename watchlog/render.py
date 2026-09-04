"""Render the visible events into one self-contained HTML file."""
import logging
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, db
from .grouping import group

log = logging.getLogger("watchlog.render")

TEMPLATES = config.ROOT / "templates"


def _date_label(iso):
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    return f"{moment.strftime('%B')} {moment.day}, {moment.year}"


def build_html():
    entries = group(db.visible_events())[: config.PAGE_LIMIT]

    # Everything the timeline rail needs: an anchor to jump to, and a flag on
    # the first entry of each month. Entries are newest first, so "first of the
    # month" means the topmost one as you read down.
    previous_month = None
    for index, entry in enumerate(entries, start=1):
        moment = datetime.fromisoformat(
            entry["watched_at"].replace("Z", "+00:00")
        ).astimezone()
        entry["date_label"] = _date_label(entry["watched_at"])
        entry["anchor"] = f"e{index}"
        month = (moment.year, moment.month)
        entry["month_start"] = month != previous_month
        entry["month_short"] = moment.strftime("%b")
        previous_month = month

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("watchlog.html.j2")
    generated = datetime.now(timezone.utc).astimezone().strftime("%B %-d, %Y at %-I:%M %p")
    return template.render(entries=entries, generated_at=generated), len(entries)


def write_page():
    html, count = build_html()
    config.OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.OUT_PATH.write_text(html, encoding="utf-8")
    log.info("rendered %d entries to %s", count, config.OUT_PATH)
    return config.OUT_PATH


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init()
    print(write_page())
