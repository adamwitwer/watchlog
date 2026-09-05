"""Push the rendered page to the web host.

The Pi holds everything with moving parts. The only thing that leaves the house
is one flat HTML file.
"""
import logging
import subprocess
from datetime import datetime, timezone

from . import config, db

log = logging.getLogger("watchlog.publish")


def _record(key, value):
    """Bookkeeping must never be the reason a publish fails."""
    try:
        db.set_meta(key, value)
    except Exception:
        log.exception("could not record publish outcome")


def push(local_path=None):
    """Publish, and leave a record either way.

    The record lives here rather than in the callers because two of the three
    -- the webhook's debounced timer and the admin's republish -- catch the
    exception and carry on. From outside, a web host that has stopped accepting
    the file looks exactly like one that is up to date.
    """
    try:
        result = _push(local_path)
    except Exception as exc:
        _record(config.META_PUBLISH_ERROR, f"{type(exc).__name__}: {exc}"[:400])
        _record(config.META_PUBLISH_ERROR_AT, _now())
        raise
    _record(config.META_PUBLISH_OK, _now())
    _record(config.META_PUBLISH_ERROR, "")
    return result


def _now():
    return datetime.now(timezone.utc).isoformat()


def _push(local_path=None):
    local = local_path or config.OUT_PATH
    if not local.exists():
        raise FileNotFoundError(f"nothing rendered at {local}")

    for name in ("NFSN_SSH_HOST", "NFSN_SSH_USER"):
        value = getattr(config, name)
        if not value or value.startswith("TODO"):
            raise RuntimeError(f"{name} is not set in .env; cannot publish")

    destination = (
        f"{config.NFSN_SSH_USER}@{config.NFSN_SSH_HOST}:"
        f"{config.NFSN_REMOTE_PATH.rstrip('/')}"
    )
    ssh_cmd = f"ssh -i {config.NFSN_SSH_KEY} -o StrictHostKeyChecking=accept-new"

    # The page, and the .htaccess that keeps the host's edge cache from
    # serving a stale copy for a quarter of an hour after every publish.
    transfers = [(local, "index.html")]
    htaccess = config.ROOT / "web" / ".htaccess"
    if htaccess.exists():
        transfers.append((htaccess, ".htaccess"))

    for source, remote_name in transfers:
        command = [
            "rsync", "-az", "--no-perms", "--no-times", "--checksum",
            "-e", ssh_cmd, str(source), f"{destination}/{remote_name}",
        ]
        log.info("pushing %s -> %s/%s", source.name, destination, remote_name)
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            log.error("rsync failed (%s): %s", result.returncode, result.stderr.strip())
            raise RuntimeError(f"rsync failed: {result.stderr.strip()}")

    log.info("published")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    push()
