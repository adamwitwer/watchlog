#!/usr/bin/env python3
"""The listeners answer the house and the Tailnet, and nobody else.

Both bind 0.0.0.0, so this check is the thing that keeps a port forward or a
UPnP mapping from putting the admin page on the internet. Nothing here touches
the network or the real database.

Run: python -m tests.test_lan
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchlog import config

_tmp = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmp.name) / "test.db"
config.ADMIN_TOKEN = "test-token"
config.PLEX_WEBHOOK_SECRET = "test-secret"
config.OUT_PATH = Path(_tmp.name) / "out" / "watchlog.html"

from watchlog import admin, db, plex_webhook   # noqa: E402
from watchlog.lan import is_local               # noqa: E402

db.init()

failures = []


def check(name, condition):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition:
        failures.append(name)


print("\nwhich addresses count as local")
for addr in ("127.0.0.1", "::1", "192.168.1.20", "10.0.0.5", "172.16.4.4",
             "100.101.102.103", "fd7a:115c:a1e0::1", "::ffff:192.168.1.20"):
    check(f"{addr} is local", is_local(addr))
# Real public addresses, not the 203.0.113.0/24 documentation range: Python
# counts the documentation ranges as private, which is harmless for real traffic
# (nothing routes them) but would make a test of "outside" quietly pass as local.
for addr in ("8.8.8.8", "93.184.215.14", "2001:4860:4860::8888", "::ffff:8.8.8.8",
             "100.128.0.1", "", None, "not-an-address"):
    check(f"{addr!r} is not", not is_local(addr))


def get(app, path, addr, **kw):
    return app.test_client().get(path, environ_base={"REMOTE_ADDR": addr}, **kw)


def post(app, path, addr, **kw):
    return app.test_client().post(path, environ_base={"REMOTE_ADDR": addr}, **kw)


print("\nthe admin page")
check("answers the LAN with the right token",
      get(admin.app, "/?token=test-token", "192.168.1.20").status_code == 200)
check("answers the Tailnet with the right token",
      get(admin.app, "/?token=test-token", "100.90.1.2").status_code == 200)
outside = get(admin.app, "/?token=test-token", "93.184.215.14")
check("refuses the internet even with the right token", outside.status_code == 404)
check("...and sets no cookie doing it", "Set-Cookie" not in outside.headers)
check("refuses a POST from outside before it reaches the handler",
      post(admin.app, "/hide?token=test-token", "93.184.215.14",
           data={"ids": "1"}).status_code == 404)
check("refuses a body far larger than any form",
      post(admin.app, "/hide?token=test-token", "192.168.1.20",
           data={"ids": "1" * (2 * 1024 * 1024)}).status_code == 413)

print("\nthe Plex webhook")
check("health answers the LAN",
      get(plex_webhook.app, "/health", "192.168.1.30").status_code == 200)
check("health refuses the internet",
      get(plex_webhook.app, "/health", "93.184.215.14").status_code == 404)
check("a scrobble from outside is refused even with the secret",
      post(plex_webhook.app, "/plex/test-secret", "93.184.215.14",
           data={"payload": "{}"}).status_code == 404)
check("a scrobble from the LAN still reaches the handler",
      post(plex_webhook.app, "/plex/test-secret", "192.168.1.30",
           data={"payload": "{}"}).status_code == 204)

print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
