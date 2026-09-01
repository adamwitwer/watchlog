#!/usr/bin/env python3
"""Pair with the Apple TV. Phase 0 spike tool.

tvOS shows the PIN on the television, so pairing cannot be done headlessly.
This begins pairing, then waits for the PIN to appear in a drop file, so the
person reading the screen and the process doing the pairing don't have to be
the same terminal.

    python tools/pair_atv.py companion
    echo 1234 > /tmp/watchlog_pin_companion

Credentials are written back into .env on success.
"""
import asyncio
import sys
import time
from pathlib import Path

import pyatv
from pyatv.const import Protocol

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

PROTOCOLS = {
    "companion": (Protocol.Companion, "ATV_COMPANION_CREDENTIALS"),
    "airplay": (Protocol.AirPlay, "ATV_AIRPLAY_CREDENTIALS"),
}


def read_env(key, default=""):
    for line in ENV.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return default


def write_env(key, value):
    lines = ENV.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n")


def wait_for_pin(path, timeout=300):
    """Poll for the PIN read off the television."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            pin = path.read_text().strip()
            if pin:
                path.unlink()
                return pin
        time.sleep(1)
    return None


async def main():
    if len(sys.argv) < 2 or sys.argv[1] not in PROTOCOLS:
        sys.exit(f"usage: pair_atv.py [{'|'.join(PROTOCOLS)}]")

    name = sys.argv[1]
    protocol, env_key = PROTOCOLS[name]
    pin_file = Path(f"/tmp/watchlog_pin_{name}")
    identifier = read_env("ATV_IDENTIFIER")

    loop = asyncio.get_event_loop()
    print(f"scanning for {identifier} ...", flush=True)
    devices = await pyatv.scan(loop, identifier=identifier, timeout=10)
    if not devices:
        sys.exit("Apple TV not found. Is it awake and on the same subnet?")

    conf = devices[0]
    print(f"found {conf.name} ({conf.device_info})", flush=True)

    pairing = await pyatv.pair(conf, protocol, loop)
    await pairing.begin()

    if not pairing.device_provides_pin:
        sys.exit("expected the device to display a PIN; it did not")

    print(f"PIN_READY: a {name} PIN is now on the television", flush=True)
    print(f"waiting for {pin_file} ...", flush=True)

    pin = wait_for_pin(pin_file)
    if pin is None:
        await pairing.close()
        sys.exit("timed out waiting for the PIN")

    print(f"got PIN, submitting for {name} ...", flush=True)
    pairing.pin(pin)
    await pairing.finish()

    if not pairing.has_paired:
        await pairing.close()
        sys.exit(f"{name} pairing failed -- wrong PIN?")

    credentials = pairing.service.credentials
    await pairing.close()

    write_env(env_key, credentials)
    print(f"PAIRED: {name} credentials written to .env as {env_key}", flush=True)
    print(f"credential length: {len(credentials)} chars", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
