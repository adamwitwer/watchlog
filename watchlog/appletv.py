"""Watch the Apple TV and record what gets played to the end.

Two mechanisms, because neither is sufficient alone:

* Push updates tell us when something starts, stops, pauses, or changes. They
  fire on state change only -- fifty-five seconds of steady playback produced
  exactly one update -- so they can never tell us when a title passes 90%.
* Polling metadata.playing() on an interval supplies the position.

The Apple TV reports no season or episode numbers, only the series name, so
entries here group by show and night and carry no episode label.
"""
import asyncio
import logging
from datetime import datetime, timezone

import pyatv
from pyatv.const import DeviceState, Protocol
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
            render.write_page()
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

        try:
            while not lost.is_set():
                try:
                    await asyncio.wait_for(lost.wait(), config.APPLETV_POLL_SECONDS)
                    break
                except asyncio.TimeoutError:
                    pass
                try:
                    playing = await atv.metadata.playing()
                    self._handle(playing, atv.metadata.app)
                except Exception as exc:
                    log.warning("poll failed: %s", exc)
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
