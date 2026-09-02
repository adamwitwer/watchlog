#!/usr/bin/env python3
"""Collapse repeat rows that the old, narrower dedup window let through.

Keeps the earliest row of each cluster. These are redundant records of one
viewing, not separate viewings, and everything here can be re-imported from
Plex's history if it is ever wanted back.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchlog import config, db


def main(dry_run=True):
    with db.connect() as conn:
        doomed = conn.execute(
            """SELECT b.id, b.title, b.season, b.episode, b.watched_at,
                      ROUND((julianday(b.watched_at)-julianday(a.watched_at))*24,1) AS hours
               FROM events a JOIN events b
                 ON a.dedup_key = b.dedup_key AND a.id < b.id
               WHERE (julianday(b.watched_at)-julianday(a.watched_at))*24 < ?
                 AND b.hidden = 0""",
            (config.DEDUP_WINDOW_HOURS,),
        ).fetchall()

        print(f"{len(doomed)} redundant rows (window {config.DEDUP_WINDOW_HOURS}h)")
        for row in doomed:
            episode = f"S{row['season']}E{row['episode']}" if row["season"] else ""
            print(f"  {row['hours']:6.1f}h  {row['title']} {episode}")

        if dry_run:
            print("\ndry run; nothing removed")
            return

        conn.executemany("DELETE FROM events WHERE id = ?", [(r["id"],) for r in doomed])
        print(f"\nremoved {len(doomed)} rows")


if __name__ == "__main__":
    main(dry_run="--apply" not in sys.argv)
