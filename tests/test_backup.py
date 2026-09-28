#!/usr/bin/env python3
"""The nightly copy: that it is taken safely, checked, and admits failure.

The database is the only thing in this project that cannot be rebuilt, so the
things worth testing are the ones that would leave a backup that exists but
cannot be restored, or a backup that stopped running and told nobody.

Nothing here touches the network or the real database.

Run: python -m tests.test_backup
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchlog import config

_tmp = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmp.name) / "test.db"
config.OUT_PATH = Path(_tmp.name) / "out" / "watchlog.html"
config.BACKUP_HOST = "nobody@nowhere.invalid"
config.BACKUP_PATH = "Backups/watchlog"
config.BACKUP_SSH_KEY = ""

from watchlog import backup, db                     # noqa: E402

failures = []


def check(name, condition):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition:
        failures.append(name)


db.init()
for episode in range(1, 6):
    db.insert_event({
        "watched_at": f"2026-09-0{episode}T20:00:00+00:00", "source": "test",
        "service": "Plex", "media_type": "episode", "title": "Backed Up",
        "episode_title": f"E{episode}", "year": 2026, "season": 1,
        "episode": episode, "dedup_key": f"backed up|episode|1|{episode}",
    })


# --- the copy itself ---------------------------------------------------------

print("\ntaking a copy")

copy = backup.snapshot(Path(_tmp.name) / "staging" / "watchlog-2026-09-27.db")
check("the copy is written where it was asked for", copy.exists())
check("its parent directory is created if missing", copy.parent.is_dir())

with sqlite3.connect(copy) as conn:
    rows = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
    titles = {r[0] for r in conn.execute("SELECT DISTINCT title FROM events")}
check("every event came across", rows == 5)
check("the copy is a sound database, not just a file of the right size",
      verdict == "ok")
check("...with the actual contents", titles == {"Backed Up"})

# Taken through sqlite's own API rather than a file copy, so a writer mid
# transaction cannot produce a torn file. Prove the source is opened read-only
# by backing up while a transaction is open against it.
live = sqlite3.connect(config.DB_PATH)
live.execute("BEGIN")
live.execute("INSERT INTO meta (key, value) VALUES ('half', 'written')")
second = backup.snapshot(Path(_tmp.name) / "staging" / "during-a-write.db")
live.rollback()
live.close()
with sqlite3.connect(second) as conn:
    check("a copy taken mid-write is still sound",
          conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    check("...and does not contain the transaction that was rolled back",
          conn.execute("SELECT COUNT(*) FROM meta WHERE key='half'").fetchone()[0] == 0)


# --- what gets sent, and proving it arrived ---------------------------------

print("\nsending it")

sent = {}


def fake_remote(command):
    sent.setdefault("commands", []).append(command)
    if "shasum" in command:
        return sent.get("digest", backup._sha256(copy))
    return ""


class FakeRun:
    def __init__(self, returncode=0, stderr=""):
        self.returncode, self.stderr, self.stdout = returncode, stderr, ""


backup._remote = fake_remote
backup.subprocess.run = lambda *a, **k: FakeRun()

result = backup.send(copy)
check("it lands under the configured path",
      result == "Backups/watchlog/watchlog-2026-09-27.db")
check("the directory is created on the far side first",
      any(c.startswith("mkdir -p") for c in sent["commands"]))
check("the arriving file's checksum is compared, not assumed",
      any("shasum" in c for c in sent["commands"]))

sent["digest"] = "0" * 64
try:
    backup.send(copy)
    check("a copy that arrives changed is rejected", False)
except RuntimeError as exc:
    check("a copy that arrives changed is rejected", "is not the one" in str(exc))
sent.pop("digest")


# --- rotation ----------------------------------------------------------------

print("\nrotating")

sent["commands"] = []
backup.rotate(keep=3)
command = sent["commands"][0]
check("it trims to the newest few", "tail -n +4" in command)
check("...listed newest first", "ls -1t" in command)
check("...scoped to this project's own filenames",
      "watchlog-*.db" in command and "rm -f" in command)
check("...inside the backup directory only",
      command.startswith("cd Backups/watchlog"))


# --- saying so, either way ---------------------------------------------------

print("\nadmitting failure")

with db.connect() as conn:
    conn.execute("DELETE FROM meta")

backup.send = lambda local: "sent"
backup.rotate = lambda keep=None: []
backup.run()
check("a successful run records a heartbeat",
      db.get_meta(config.META_BACKUP_OK) is not None)
check("...and clears any old error", db.get_meta(config.META_BACKUP_ERROR) == "")
check("the staging copy is not left behind on the card this exists to survive",
      not (config.OUT_PATH.parent / "backup").exists())


def explode(local):
    raise RuntimeError("the other machine is asleep")


backup.send = explode
try:
    backup.run()
    check("a failed run raises, so systemd marks it failed", False)
except RuntimeError:
    check("a failed run raises, so systemd marks it failed", True)
check("...and records why, for the admin page to show",
      "asleep" in (db.get_meta(config.META_BACKUP_ERROR) or ""))
check("...and does not move the heartbeat forward",
      db.get_meta(config.META_BACKUP_ERROR_AT) is not None)
check("...and still clears up after itself",
      not (config.OUT_PATH.parent / "backup").exists())

config.BACKUP_HOST = ""
with db.connect() as conn:
    conn.execute("DELETE FROM meta")
check("with no host configured it does nothing at all", backup.run() is None)
check("...and claims no heartbeat it did not earn",
      db.get_meta(config.META_BACKUP_OK) is None)


print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
