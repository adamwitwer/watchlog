# AGENTS.md

Guidance for AI agents (Claude Code and others) working in this repo. The design and
its decisions are in `miniPRD.txt`; how it runs, and how each part has failed, is in
`README.md`. This file holds what is open right now and the conventions that are easy
to break.

## The LAN gate and Plex

`watchlog/lan.py` returns 404 for any request from outside loopback, the private ranges and
Tailscale's 100.64.0.0/10. The first real scrobble arrived through it on 2026-10-04 at
22:59, with no refusals. If deliveries ever stop, check
`journalctl -u watchlog-webhook | grep refused`. Reconcile backfills dropped scrobbles,
so the page won't show the problem. Find out why the Plex host's address isn't local
before widening `is_local`.

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
