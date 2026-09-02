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
    for entry in entries:
        entry["date_label"] = _date_label(entry["watched_at"])

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
