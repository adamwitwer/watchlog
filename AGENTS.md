# AGENTS.md

Guidance for AI agents (Claude Code and others) working in this repo. The design and
its decisions are in `miniPRD.txt`; how it runs, and how each part has failed, is in
`README.md`. This file holds what is open right now and the conventions that are easy
to break.

## Open: confirm the first Plex scrobble after the LAN gate (deployed 2026-10-04)

`watchlog/lan.py` (commit `954afcd`) makes the webhook and the admin page 404 any
request from outside loopback, the private ranges, or Tailscale's 100.64.0.0/10. It was
deployed to the Pi on 2026-10-04 with every suite passing, and a request from a Mac on
the LAN reached both apps. **No real Plex delivery had arrived through it yet.** The last
one before the deploy was about 16 hours earlier.

The risk is narrow but would be silent: if PMS ever reaches the Pi from an address the
gate does not count as local, every scrobble gets a 404, and the hourly reconcile backfills
the entries, so nothing looks wrong on the page. To check, after something finishes on
Plex:

```
journalctl -u watchlog-webhook --since today | grep -E "recorded|refused"
```

`recorded …` means it worked: delete this section. `refused POST /plex/… from <addr>` means
the gate is blocking Plex. Find out why that address is not on the LAN before widening
`is_local`, rather than adding the address as an exception.

The admin page's "Plex webhook delivered …" health line tells you the same thing without
the log: if it keeps ageing while reconcile keeps importing, deliveries are being dropped.

## Conventions

- **Deploy with `./deploy.sh` on the Pi** (`ssh adam@raspberrypi`, repo at
  `~/Projects/watchlog`). It runs every suite before restarting anything, and the restart is
  the step that fails silently if it is skipped (README, "Staying alive").
- **`deploy.sh` does not `pip install`.** A new entry in `requirements.txt` needs an install
  step added to the script first. Otherwise the services fail at import on their next
  restart, after the suites have passed. This is why the webhook and admin page still run
  on Flask's development server rather than waitress.
- **Test "outside" with real public addresses.** Python's `ipaddress` counts the
  documentation ranges (203.0.113.0/24 and friends) as private, so a test that uses one as
  an internet caller passes for the wrong reason. `tests/test_lan.py` caught exactly that.
- **This repo is public; the watch history is not.** The database holds entries deleted
  from the page. Never commit it, a rendered page, or real titles from it in fixtures
  (`.gitignore` has the list).
