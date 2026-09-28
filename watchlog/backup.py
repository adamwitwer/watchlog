"""Keep a copy of the log somewhere the Pi is not.

Everything else in this project can be rebuilt. The page and the feed are
rendered output, the code is in git, and Plex remembers its own history. The
database cannot: the Apple TV keeps no history at all, hand-typed entries exist
nowhere else, and the record of what was *deleted* is by definition not
published anywhere. It sits on an SD card, which is a wear-out part.

So once a night it goes to another machine in the house. That covers the
failure this is actually for -- the card dying -- and not fire or theft, which
would want somewhere further away.

Three things separate this from hoping:

  * The copy is taken with SQLite's online backup API, not `cp`. Both sensors
    write whenever something is watched, and copying the file out from under a
    writer can capture a half-finished transaction.
  * Every copy is checked with PRAGMA integrity_check before it is sent, and a
    failed check throws it away rather than shipping it.
  * The transferred file's checksum is compared against the local one, so a
    truncated transfer is noticed here rather than on the day it is needed.
"""
import logging
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import config, db

log = logging.getLogger("watchlog.backup")

TIMEOUT = 120


def _now():
    return datetime.now(timezone.utc).isoformat()


def _record(key, value):
    """Bookkeeping must never be the reason a backup fails."""
    try:
        db.set_meta(key, value)
    except Exception:
        log.exception("could not record backup outcome")


def _ssh_command():
    key = f"-i {config.BACKUP_SSH_KEY} " if config.BACKUP_SSH_KEY else ""
    return f"ssh {key}-o BatchMode=yes -o StrictHostKeyChecking=accept-new"


def snapshot(destination):
    """A consistent copy of the live database, taken while it is in use.

    Opened read-only, copied through sqlite3's backup API so a writer mid
    transaction cannot produce a torn file, then checked. A copy that fails its
    own integrity check is deleted rather than kept: a backup nobody can
    restore is worse than an obvious absence, because it stops you looking.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)

    source = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            verdict = target.execute("PRAGMA integrity_check").fetchone()[0]
            events = target.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        finally:
            target.close()
    finally:
        source.close()

    if verdict != "ok":
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"the copy failed its integrity check: {verdict}")

    log.info("snapshot of %d events -> %s (%d bytes)",
             events, destination, destination.stat().st_size)
    return destination


def _sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _remote(command):
    """Run one command on the backup host. Returns stdout."""
    result = subprocess.run(
        ["ssh", *(["-i", config.BACKUP_SSH_KEY] if config.BACKUP_SSH_KEY else []),
         "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
         config.BACKUP_HOST, command],
        capture_output=True, text=True, timeout=TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"remote command failed: {result.stderr.strip()}")
    return result.stdout.strip()


def send(local):
    """Copy one snapshot to the backup host and prove it arrived intact."""
    local = Path(local)
    remote_dir = config.BACKUP_PATH.rstrip("/")
    _remote(f"mkdir -p {remote_dir}")

    result = subprocess.run(
        ["rsync", "-a", "--no-perms", "--no-times",
         "-e", _ssh_command(), str(local),
         f"{config.BACKUP_HOST}:{remote_dir}/{local.name}"],
        capture_output=True, text=True, timeout=TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"rsync failed: {result.stderr.strip()}")

    # macOS ships shasum, Linux sha256sum; the host here is a Mac, but ask for
    # either so this keeps working if the copy ever moves somewhere else.
    there = _remote(
        f"cd {remote_dir} && (shasum -a 256 {local.name} 2>/dev/null "
        f"|| sha256sum {local.name}) | cut -d' ' -f1"
    )
    here = _sha256(local)
    if there != here:
        raise RuntimeError(
            f"the copy that arrived is not the one that was sent "
            f"({there or 'nothing'} != {here})")
    log.info("sent %s, checksum matches", local.name)
    return f"{remote_dir}/{local.name}"


def rotate(keep=None):
    """Drop the oldest copies, keeping the newest `keep`.

    Scoped to this project's own filenames in its own directory, and listed
    newest-first before trimming, so nothing else on that machine is at risk
    from a bad glob.
    """
    keep = keep or config.BACKUP_KEEP
    remote_dir = config.BACKUP_PATH.rstrip("/")
    removed = _remote(
        f"cd {remote_dir} && ls -1t watchlog-*.db 2>/dev/null "
        f"| tail -n +{keep + 1} | while read -r old; do rm -f \"$old\" && echo \"$old\"; done"
    )
    names = [line for line in removed.splitlines() if line]
    if names:
        log.info("rotated out %d old cop%s: %s",
                 len(names), "y" if len(names) == 1 else "ies", ", ".join(names))
    return names


def run(keep=None):
    """Take a copy, send it, trim the old ones, and say so either way.

    Named for the day it matters: if this has been failing, the admin page says
    so in red, because a backup that stopped running six weeks ago and told
    nobody is the standard way this goes wrong.
    """
    if not config.BACKUP_HOST:
        log.info("BACKUP_HOST is not set; skipping")
        return None

    staging = Path(config.OUT_PATH).parent / "backup"
    name = f"watchlog-{datetime.now().astimezone().date().isoformat()}.db"
    local = staging / name
    try:
        snapshot(local)
        remote = send(local)
        rotate(keep)
    except Exception as exc:
        _record(config.META_BACKUP_ERROR, f"{type(exc).__name__}: {exc}"[:400])
        _record(config.META_BACKUP_ERROR_AT, _now())
        raise
    finally:
        # The staging copy is the same bytes as the one now on the other
        # machine; keeping it would only fill the card this exists to survive.
        shutil.rmtree(staging, ignore_errors=True)

    _record(config.META_BACKUP_OK, _now())
    _record(config.META_BACKUP_ERROR, "")
    log.info("backup complete: %s", remote)
    return remote


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db.init()
    print(run())
