#!/usr/bin/env python3
"""Tracker rules, exercised without an Apple TV.

Run: python -m tests.test_tracker
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyatv.const import DeviceState

from watchlog.appletv import Tracker

TV = "com.apple.TVWatchList"
PLEX = "com.plexapp.plex"
NETFLIX = "com.netflix.Netflix"

failures = []


def check(name, condition):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition:
        failures.append(name)


def observe(tracker, app=TV, title="Dark Matter", pos=0, total=2917,
            state=DeviceState.Playing):
    return tracker.observe(app, "TV", title, pos, total, state)


print("Tracker")

t = Tracker()
check("below threshold logs nothing", observe(t, pos=1000) is None)
check("at 89% logs nothing", observe(t, pos=int(2917 * 0.89)) is None)

event = observe(t, pos=int(2917 * 0.90))
check("at 90% produces an event", event is not None)
check("event is attributed to Apple TV", event and event["service"] == "Apple TV")
check("event carries no episode numbers", event and event["season"] is None)
check("event groups as an episode", event and event["media_type"] == "episode")

check("does not log the same session twice", observe(t, pos=2900) is None)

t2 = Tracker()
check("Plex is ignored (its webhook covers it)",
      observe(t2, app=PLEX, pos=2900) is None)

t3 = Tracker()
check("Netflix is ignored (reports nothing usable)",
      observe(t3, app=NETFLIX, title=None, pos=None, total=None) is None)

t4 = Tracker()
observe(t4, title="Show A", pos=2900)
event_b = observe(t4, title="Show B", pos=2900)
check("a new title starts a new session", event_b is not None)
check("the new event is the new title", event_b and event_b["title"] == "Show B")

t5 = Tracker()
observe(t5, pos=2900)
observe(t5, state=DeviceState.Idle)
check("idle resets, so a rewatch can log again", observe(t5, pos=2900) is not None)

t6 = Tracker()
check("missing duration logs nothing (Netflix's shape)",
      observe(t6, total=None, pos=None) is None)

t7 = Tracker()
a = observe(t7, title="Dark Matter", pos=2900)
t7.reset()
b = observe(t7, title="Dark Matter", pos=2900)
check("same title, same night, shares a dedup key",
      a and b and a["dedup_key"] == b["dedup_key"])
check("dedup key includes the night, so consecutive nights differ",
      a and "appletv" in a["dedup_key"] and len(a["dedup_key"].split("|")) == 3)

print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
