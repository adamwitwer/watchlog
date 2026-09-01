#!/usr/bin/env python3
"""Phase 0 spike: dump everything the Apple TV reports while something plays.

The point is to find out what metadata each app actually provides. The Apple TV
app is expected to report cleanly; Netflix has historically been stingy about
series/season/episode structure. That answer decides whether Netflix is in v1.

    python tools/spike_listen.py [seconds]

Writes one JSON object per update to spike_log.jsonl and prints a summary.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyatv
from pyatv.const import Protocol
from pyatv.interface import PushListener

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
LOG = ROOT / "spike_log.jsonl"

# Every field worth knowing about on a Playing instance.
FIELDS = [
    "media_type", "device_state", "title", "artist", "album", "genre",
    "series_name", "season_number", "episode_number", "content_identifier",
    "total_time", "position", "shuffle", "repeat", "hash", "itunes_store_identifier",
]


def read_env(key, default=""):
    for line in ENV.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return default


def snapshot(playing, app):
    row = {"ts": datetime.now(timezone.utc).isoformat()}
    for field in FIELDS:
        try:
            value = getattr(playing, field, None)
        except Exception as exc:                      # some fields raise per-protocol
            value = f"<error: {exc}>"
        row[field] = str(value) if value is not None else None
    row["app_name"] = getattr(app, "name", None)
    row["app_identifier"] = getattr(app, "identifier", None)
    if playing.total_time and playing.position is not None and playing.total_time > 0:
        row["percent"] = round(100 * playing.position / playing.total_time, 1)
    else:
        row["percent"] = None
    return row


class Dumper(PushListener):
    def __init__(self, atv):
        self.atv = atv
        self.last = None
        self.count = 0

    def playstatus_update(self, updater, playstatus):
        try:
            app = self.atv.metadata.app
        except Exception:
            app = None
        row = snapshot(playstatus, app)
        self.count += 1

        with LOG.open("a") as handle:
            handle.write(json.dumps(row) + "\n")

        # Only print when something meaningful changed, to keep the log readable.
        key = (row["app_name"], row["title"], row["device_state"])
        if key != self.last:
            self.last = key
            print(
                f"\n--- update {self.count} @ {row['ts']}\n"
                f"    app     : {row['app_name']} ({row['app_identifier']})\n"
                f"    state   : {row['device_state']}   type: {row['media_type']}\n"
                f"    title   : {row['title']}\n"
                f"    series  : {row['series_name']}  S{row['season_number']}E{row['episode_number']}\n"
                f"    artist  : {row['artist']}   album: {row['album']}\n"
                f"    ident   : {row['content_identifier']}\n"
                f"    progress: {row['position']}/{row['total_time']} ({row['percent']}%)",
                flush=True,
            )
        else:
            print(".", end="", flush=True)

    def playstatus_error(self, updater, exception):
        print(f"\n[push error] {exception}", flush=True)


async def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    identifier = read_env("ATV_IDENTIFIER")

    loop = asyncio.get_event_loop()
    devices = await pyatv.scan(loop, identifier=identifier, timeout=10)
    if not devices:
        sys.exit("Apple TV not found. Is it awake and on the same subnet?")

    conf = devices[0]
    for protocol, key in ((Protocol.AirPlay, "ATV_AIRPLAY_CREDENTIALS"),
                          (Protocol.Companion, "ATV_COMPANION_CREDENTIALS")):
        creds = read_env(key)
        if creds:
            conf.set_credentials(protocol, creds)

    atv = await pyatv.connect(conf, loop)
    print(f"connected to {conf.name}; listening for {duration}s", flush=True)
    print(f"logging to {LOG}", flush=True)

    try:
        listener = Dumper(atv)
        atv.push_updater.listener = listener
        atv.push_updater.start()
        await asyncio.sleep(duration)
        print(f"\n\ndone: {listener.count} updates captured", flush=True)
    finally:
        atv.close()


if __name__ == "__main__":
    asyncio.run(main())
