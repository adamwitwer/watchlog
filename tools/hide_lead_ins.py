#!/usr/bin/env python3
"""Hide the lead-ins already in the log: episodes re-played for their ending.

New ones are hidden as they arrive (see db.lead_ins); this is for the rows
recorded before that rule existed. Hiding, not deleting -- any of them can be
brought back from the admin page.

Run: python tools/hide_lead_ins.py            (dry run)
     python tools/hide_lead_ins.py --apply
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchlog import db


def main(dry_run=True):
    with db.connect() as conn:
        found = {}
        for row in conn.execute(
                "SELECT title, watched_at FROM events WHERE media_type = 'episode'"
                "  AND hidden = 0 ORDER BY watched_at"):
            for i in db.lead_ins(conn, row["title"], row["watched_at"]):
                found[i] = None
        rows = [conn.execute("SELECT * FROM events WHERE id = ?", (i,)).fetchone()
                for i in found]

        print(f"{len(rows)} lead-ins")
        for r in rows:
            print(f"  #{r['id']:<5} {r['watched_at'][:10]}  "
                  f"{r['title']} S{r['season']}E{r['episode']}")

        if dry_run:
            print("\ndry run; nothing hidden")
            return
        db.set_hidden(list(found))
        print(f"\nhid {len(rows)} rows")


if __name__ == "__main__":
    main(dry_run="--apply" not in sys.argv)
