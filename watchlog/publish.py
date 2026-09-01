"""Push the rendered page to the web host.

The Pi holds everything with moving parts. The only thing that leaves the house
is one flat HTML file.
"""
import logging
import subprocess

from . import config

log = logging.getLogger("watchlog.publish")


def push(local_path=None):
    local = local_path or config.OUT_PATH
    if not local.exists():
        raise FileNotFoundError(f"nothing rendered at {local}")

    for name in ("NFSN_SSH_HOST", "NFSN_SSH_USER"):
        value = getattr(config, name)
        if not value or value.startswith("TODO"):
            raise RuntimeError(f"{name} is not set in .env; cannot publish")

    destination = (
        f"{config.NFSN_SSH_USER}@{config.NFSN_SSH_HOST}:{config.NFSN_REMOTE_PATH}"
    )
    command = [
        "rsync", "-az", "--no-perms", "--no-times", "--checksum",
        "-e", f"ssh -i {config.NFSN_SSH_KEY} -o StrictHostKeyChecking=accept-new",
        str(local),
        f"{destination.rstrip('/')}/index.html",
    ]

    log.info("pushing %s -> %s", local.name, destination)
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        log.error("rsync failed (%s): %s", result.returncode, result.stderr.strip())
        raise RuntimeError(f"rsync failed: {result.stderr.strip()}")

    log.info("published")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    push()
