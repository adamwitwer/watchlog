"""Watch the Apple TV and record what gets played to the end.

Two mechanisms, because neither is sufficient alone:

* Push updates tell us when something starts, stops, pauses, or changes. They
  fire on state change only -- fifty-five seconds of steady playback produced
  exactly one update -- so they can never tell us when a title passes 90%.
* Polling metadata.playing() on an interval supplies the position.

And a third, because the second proves less than it looks like it does:
playing() never touches the network. pyatv answers it from a local cache fed by
push updates, extrapolating position from the last known playback rate, so it
returns in a tenth of a millisecond whether the Apple TV is there or not. A
connection the device has abandoned -- which is what a reboot leaves behind --
keeps answering it indefinitely with the last state it heard. Whether the
device is actually listening is asked separately, with a request it has to
answer.

The Apple TV reports no season or episode numbers, only the series name, so
entries here group by show and night and carry no episode label.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

import pyatv
from pyatv.const import DeviceState, FeatureName, FeatureState, Protocol
from pyatv.interface import DeviceListener, PushListener

from . import config, db, enrich, publish, render
from .grouping import night_of, normalize

log = logging.getLogger("watchlog.appletv")

RECONNECT_MIN = 10
RECONNECT_MAX = 300
PUBLISH_DEBOUNCE_SECONDS = 60


class Tracker:
    """Decides when a title has been watched.

    One title is logged at most once per viewing session; the session resets
    when the title or app changes, or playback goes idle.
    """

    def __init__(self):
        self.key = None
        self.logged = False
        self.peak = 0.0
        # Diagnostic only, and deliberately not cleared by reset(): one line per
        # unrecognised app for the life of the process, not one every poll.
        self.unknown_apps = set()

    def reset(self):
        # A session that ends without being logged is the interesting failure:
        # it says the title was seen and how close it got, which separates "never
        # noticed it" from "noticed it, fell short of the threshold".
        if self.key and not self.logged and self.peak > 0:
            log.info("stopped: %s at %.0f%% (threshold %.0f%%)",
                     self.key[1], self.peak * 100, config.WATCHED_THRESHOLD * 100)
        self.key = None
        self.logged = False
        self.peak = 0.0

    def observe(self, app_id, app_name, title, position, total_time, state):
        service = config.APPLETV_APPS.get(app_id)
        if service is None:
            # Not an app we log -- Plex included, since its webhook covers it.
            # Say so once, though: an app that plays but is missing from the
            # allowlist is otherwise indistinguishable from one that reports
            # nothing, and the difference is a one-line fix vs. a dead end.
            if app_id and app_id not in self.unknown_apps:
                self.unknown_apps.add(app_id)
                log.info("ignoring app %s (%s), playing %r -- not in APPLETV_APPS",
                         app_id, app_name, title)
            self.reset()
            return None

        if state in (DeviceState.Idle, DeviceState.Loading) or not title:
            self.reset()
            return None

        key = (app_id, title)
        if key != self.key:
            self.reset()
            self.key = key
            log.info("now playing: %s (%s)", title, app_name)

        if self.logged or not total_time or position is None:
            return None

        percent = position / total_time
        self.peak = max(self.peak, percent)
        if percent < config.WATCHED_THRESHOLD:
            return None

        self.logged = True
        log.info("watched: %s at %.0f%%", title, percent * 100)
        return self._event(title, service)

    @staticmethod
    def _event(title, service):
        watched_at = datetime.now(timezone.utc).isoformat()
        # The Apple TV gives no episode numbers, so an episode-level dedup key is
        # impossible. Including the night keeps two plays on one evening together
        # while still letting the same show appear on consecutive nights.
        night = night_of(watched_at)
        return {
            "watched_at": watched_at,
            "source": "appletv",
            "service": service,
            # Treated as an episode so it groups by show and night. We cannot tell
            # a film from an episode here, and night-grouping is right either way.
            "media_type": "episode",
            "title": title,
            "episode_title": None,
            "year": None,
            "season": None,
            "episode": None,
            "imdb_id": None,
            "tmdb_id": None,
            "dedup_key": f"{normalize(title)}|appletv|{night}",
            "hidden": 0,
            "raw": None,
        }


class _Disconnected(Exception):
    pass


class _DeviceWatcher(DeviceListener):
    def __init__(self, event):
        self.event = event

    def connection_lost(self, exception):
        log.warning("connection lost: %s", exception)
        self.event.set()

    def connection_closed(self):
        log.info("connection closed")
        self.event.set()


class _Push(PushListener):
    """Push updates mark session boundaries; position comes from polling."""

    def __init__(self, on_update):
        self.on_update = on_update

    def playstatus_update(self, updater, playstatus):
        self.on_update(playstatus)

    def playstatus_error(self, updater, exception):
        log.warning("push error: %s", exception)


class Collector:
    def __init__(self):
        self.tracker = Tracker()
        self.publish_timer = None

    def _heartbeat(self):
        """Record that the Apple TV answered.

        Called only after a request the device itself had to reply to. It used
        to be called after every successful poll, and a poll cannot fail: it is
        served from pyatv's cache. So on 2026-09-08 a power cut rebooted the
        Apple TV, the connection died without the Pi being told, and this line
        read green on the admin page for fifty hours while the listener heard
        nothing. A heartbeat that can be earned without the device is not one.
        """
        try:
            db.set_meta(config.META_APPLETV_OK,
                        datetime.now(timezone.utc).isoformat())
        except Exception:
            log.exception("could not record heartbeat")

    @staticmethod
    def _can_probe(atv):
        """Whether this connection can ask the device a real question.

        Listing apps needs the Companion protocol. Without it there is no cheap
        request that has to reach the Apple TV, and the listener can only fall
        back to the old, weaker behaviour -- which it says so about, loudly.
        """
        try:
            return atv.features.in_state(FeatureState.Available, FeatureName.AppList)
        except Exception:
            return False

    @staticmethod
    async def _probe(atv):
        """Ask the Apple TV something it has to answer. True if it did.

        The app list: read-only, changes nothing on the screen, and about 30ms
        on a live connection. On a dead one it either fails outright -- a
        rebooted device resets the connection it no longer recognises -- or
        waits until the timeout, if the device is not there at all.
        """
        try:
            await asyncio.wait_for(atv.apps.app_list(), config.APPLETV_POLL_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("Apple TV did not answer a liveness probe")
            return False
        except Exception as exc:
            log.warning("liveness probe failed: %s", exc)
            return False
        return True

    def _schedule_publish(self):
        loop = asyncio.get_running_loop()
        if self.publish_timer is not None:
            self.publish_timer.cancel()
        self.publish_timer = loop.call_later(
            PUBLISH_DEBOUNCE_SECONDS,
            lambda: loop.run_in_executor(None, self._publish_now),
        )

    @staticmethod
    def _publish_now():
        try:
            # Apple TV entries arrive with only a title, so they are resolved
            # to an IMDb id and year before the page is built.
            enrich.enrich_pending()
            render.write_output()
            publish.push()
        except Exception:
            log.exception("publish failed")

    def _handle(self, playing, app):
        event = self.tracker.observe(
            getattr(app, "identifier", None),
            getattr(app, "name", None),
            playing.title,
            playing.position,
            playing.total_time,
            playing.device_state,
        )
        if event is None:
            return
        if db.insert_event(event) is None:
            log.info("duplicate, ignored: %s", event["dedup_key"])
            return
        log.info("recorded: %s", event["title"])
        self._schedule_publish()

    async def session(self):
        loop = asyncio.get_running_loop()
        devices = await pyatv.scan(loop, identifier=config.ATV_IDENTIFIER, timeout=10)
        if not devices:
            raise _Disconnected("Apple TV not found on the network")

        conf = devices[0]
        for protocol, credentials in (
            (Protocol.AirPlay, config.ATV_AIRPLAY_CREDENTIALS),
            (Protocol.Companion, config.ATV_COMPANION_CREDENTIALS),
        ):
            if credentials:
                conf.set_credentials(protocol, credentials)

        atv = await pyatv.connect(conf, loop)
        lost = asyncio.Event()
        atv.listener = _DeviceWatcher(lost)

        def on_push(playstatus):
            try:
                self._handle(playstatus, atv.metadata.app)
            except Exception:
                log.exception("push handling failed")

        atv.push_updater.listener = _Push(on_push)
        atv.push_updater.start()
        log.info("connected to %s, polling every %ss",
                 conf.name, config.APPLETV_POLL_SECONDS)

        can_probe = self._can_probe(atv)
        if not can_probe:
            log.warning("no Companion app list on this connection: the heartbeat "
                        "will record polls, which cannot tell a live device from "
                        "a dead connection")

        # Two counters, because they measure different things and one must not
        # be able to cancel the other. A poll succeeds from cache on a dead
        # connection; if its success reset the count the probe was building,
        # the two would take turns every thirty seconds and the listener would
        # never reconnect -- which is the bug this exists to close.
        poll_failures = probe_failures = 0
        next_probe = 0.0                      # ask at once on connect
        try:
            while not lost.is_set():
                try:
                    await asyncio.wait_for(lost.wait(), config.APPLETV_POLL_SECONDS)
                    break
                except asyncio.TimeoutError:
                    pass

                # Position. Bounded, because on some half-open connections
                # playing() waits forever instead of answering from cache.
                try:
                    playing = await asyncio.wait_for(
                        atv.metadata.playing(), config.APPLETV_POLL_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    poll_failures += 1
                    log.warning("poll timed out (%d in a row)", poll_failures)
                except Exception as exc:
                    poll_failures += 1
                    log.warning("poll failed (%d in a row): %s", poll_failures, exc)
                else:
                    poll_failures = 0
                    if not can_probe:
                        self._heartbeat()
                    try:
                        self._handle(playing, atv.metadata.app)
                    except Exception:
                        log.exception("poll handling failed")

                # Liveness. Every few minutes when all is well; on the very next
                # tick after a miss, so a dead connection is given up on within
                # a minute or two rather than after three full heartbeat periods.
                if can_probe and time.monotonic() >= next_probe:
                    if await self._probe(atv):
                        probe_failures = 0
                        self._heartbeat()
                        next_probe = time.monotonic() + config.APPLETV_HEARTBEAT_SECONDS
                    else:
                        probe_failures += 1
                        next_probe = time.monotonic()

                if poll_failures >= config.APPLETV_MAX_POLL_FAILURES:
                    raise _Disconnected(
                        f"{poll_failures} polls in a row went unanswered")
                if probe_failures >= config.APPLETV_MAX_POLL_FAILURES:
                    raise _Disconnected(
                        f"{probe_failures} liveness probes in a row went "
                        "unanswered; the connection is open but nobody is on it")
        finally:
            atv.close()
        raise _Disconnected("device connection ended")

    async def run(self):
        backoff = RECONNECT_MIN
        while True:
            try:
                await self.session()
            except _Disconnected as exc:
                log.info("%s; reconnecting in %ss", exc, backoff)
            except Exception:
                log.exception("session failed; reconnecting in %ss", backoff)
            else:
                backoff = RECONNECT_MIN
            self.tracker.reset()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    db.init()
    asyncio.run(Collector().run())


if __name__ == "__main__":
    main()
