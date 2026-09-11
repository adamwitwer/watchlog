#!/usr/bin/env python3
"""The listener's connection: telling a live Apple TV from a dead socket.

On 2026-09-08 a power cut rebooted the Apple TV. The Pi was never told, so its
connection stayed open to nothing, and the listener's poll -- which pyatv
answers from a local cache -- kept succeeding instantly. The heartbeat was
recorded on every poll, so the admin page read green for fifty hours while the
listener heard nothing at all.

These tests rebuild that connection out of fakes: a playing() that always
answers from cache, and a device that does or does not reply when asked
something real. Nothing touches the network or the real database.

Needs pyatv, so it runs on the Pi. Run: python -m tests.test_listener
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyatv.const import DeviceState                   # noqa: E402

from watchlog import config

_tmp = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmp.name) / "test.db"

# Everything shrunk from minutes to milliseconds, keeping the proportions: the
# poll ticks far more often than the device is probed.
config.APPLETV_POLL_SECONDS = 0.01
config.APPLETV_POLL_TIMEOUT = 0.05
config.APPLETV_HEARTBEAT_SECONDS = 0.2

from watchlog import appletv, db                      # noqa: E402

failures = []


def check(name, condition):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition:
        failures.append(name)


db.init()


# --- a fake Apple TV ---------------------------------------------------------

class Playing:
    """What the cache says: nothing, idle -- the last thing it ever heard."""
    title = position = total_time = None
    device_state = DeviceState.Idle


class Metadata:
    app = None

    async def playing(self):
        return Playing()                  # instantly, whatever the device is doing


class Apps:
    """answers: a list consumed one per request -- "yes", "reset" or "hang"."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    async def app_list(self):
        self.calls += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if answer == "yes":
            return ["com.apple.TVWatchList"]
        if answer == "reset":
            raise ConnectionResetError("connection reset by peer")
        await asyncio.sleep(3600)         # a device that is simply not there


class Features:
    def __init__(self, companion):
        self.companion = companion

    def in_state(self, state, name):
        return self.companion


class Push:
    listener = None

    def start(self):
        pass


class Atv:
    def __init__(self, answers, companion=True):
        self.metadata = Metadata()
        self.apps = Apps(answers)
        self.features = Features(companion)
        self.push_updater = Push()
        self.listener = None
        self.closed = False

    def close(self):
        self.closed = True


class Conf:
    name = "Fake Room"

    def set_credentials(self, protocol, credentials):
        pass


def connect_to(atv):
    async def scan(loop, identifier=None, timeout=None):
        return [Conf()]

    async def connect(conf, loop):
        return atv

    appletv.pyatv.scan = scan
    appletv.pyatv.connect = connect


def heartbeat():
    return db.get_meta(config.META_APPLETV_OK)


def clear():
    with db.connect() as conn:
        conn.execute("DELETE FROM meta")


async def run(atv, seconds):
    """Run one session. Returns the reason it ended, or None if still going."""
    connect_to(atv)
    try:
        await asyncio.wait_for(appletv.Collector().session(), seconds)
    except appletv._Disconnected as exc:
        return str(exc)
    except asyncio.TimeoutError:
        return None


# --- the bug -----------------------------------------------------------------

print("\na connection with nobody on the other end")

clear()
atv = Atv(["hang"])
reason = asyncio.run(run(atv, 5))
check("the listener gives up on it, rather than polling the cache forever",
      reason is not None and "liveness" in reason)
check("...even though every single poll succeeded -- a cached answer must not "
      "cancel out a device that never replies", atv.apps.calls >= 3)
check("the heartbeat was never recorded, so the admin page goes red",
      heartbeat() is None)
check("the dead connection is closed before reconnecting", atv.closed)

clear()
atv = Atv(["reset"])
reason = asyncio.run(run(atv, 5))
check("a rebooted device that resets the connection is given up on too",
      reason is not None and "liveness" in reason)
check("...again without a heartbeat", heartbeat() is None)


# --- the normal case ---------------------------------------------------------

print("\na device that answers")

clear()
atv = Atv(["yes"])
reason = asyncio.run(run(atv, 0.7))
check("a live connection is kept", reason is None)
check("the heartbeat is recorded, because the device itself replied",
      heartbeat() is not None)
check("the device is asked every heartbeat period, not every poll",
      2 <= atv.apps.calls <= 6)


# --- one miss is not a dead connection ---------------------------------------

print("\na single dropped answer")

clear()
atv = Atv(["hang", "yes"])
reason = asyncio.run(run(atv, 0.7))
check("one unanswered probe does not tear the connection down", reason is None)
check("the retry comes on the next tick, not a whole heartbeat period later",
      atv.apps.calls >= 2)
check("and the heartbeat is recorded once the device answers",
      heartbeat() is not None)


# --- the fallback ------------------------------------------------------------

print("\nno way to ask")

clear()
atv = Atv(["yes"], companion=False)
reason = asyncio.run(run(atv, 0.3))
check("without Companion the device is never probed", atv.apps.calls == 0)
check("...and the old behaviour stands in, rather than a permanent false alarm",
      reason is None and heartbeat() is not None)


print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
