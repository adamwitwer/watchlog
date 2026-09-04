# Watchlog

A running list of what I watch, published automatically to
**[adamwitwer.com/watchlog](https://adamwitwer.com/watchlog)**.

No confirmation step, no manual entry, nothing to maintain. Things I watch on Plex and
on the Apple TV show up on a page. If I don't want one there, I delete it.

Design and decisions: [`miniPRD.txt`](miniPRD.txt)

## Status

**Phase 0 — complete.** The Apple TV is paired over Companion and AirPlay, and a push
listener captured what each app reports. The finding that mattered: **Netflix reports no
metadata at all** — while it is actively playing, the Apple TV exposes only the bundle
identifier, with no title, position, or duration. Netflix is therefore out of v1. The
Apple TV app works, at series level.

**Phase 1 — live** at [adamwitwer.com/watchlog](https://adamwitwer.com/watchlog).
Plex webhook receiver, SQLite storage, night-grouping, the rendered page, and the rsync
publisher, running as `watchlog-webhook.service` on the Pi. Backfilled from Plex's own
watch history, so the page started populated rather than empty.

**Phase 2 — running.** `watchlog-appletv.service` holds a `pyatv` connection to the
Apple TV, using push updates for session boundaries and polling for position. Apps are
allowlisted, which is also what keeps Plex-on-the-Apple-TV from being counted twice.

**Phase 3 — running.** TMDb resolves Apple TV titles to IMDb ids and years, cached, with
a `locked` flag for correcting a bad match by hand.

**Phase 4 — running.** A small delete UI on the Pi (`watchlog-admin.service`), bound to
the LAN and Tailnet only. Deleting hides rather than destroys and republishes
immediately; removed entries can be restored.

**v1 is complete.** Netflix is Phase 5, and only if the gap proves annoying.

**Episode titles and manual details — running.** The page shows the episode title
under each entry, for nights of up to `EPISODE_TITLES_MAX` episodes (a long binge keeps
the range and drops the titles, rather than turning one line into a paragraph). Plex's
"Episode 4" placeholders are treated as absent, since they only restate the label above
them. The admin page accepts season, episode and episode title by hand for any entry
backed by a single event — which is every Apple TV entry, because the device reports
none of the three.

**Reconcile — running.** `watchlog-reconcile.timer` re-reads the last week of Plex's own
history every hour and imports anything the webhook missed. It exists because the webhook
turned out to have a silent failure mode: PMS asks plex.tv for its hook list *once, at
startup*, and if that request loses a race with DNS after a reboot it delivers to zero
hooks until someone restarts it. The reconcile pass writes the same dedup key the webhook
does, so live-delivered plays are recognised and skipped, and it only republishes when
something actually landed.

## How it works

Two sensors feed one SQLite database on a Raspberry Pi, which renders a single static
HTML file and rsyncs it to the web host.

- **Plex** — Plex Pass fires a webhook at 90% watched. Device-independent, so this
  covers Plex on the TV, in a browser, and on a phone. Plex supplies IMDb IDs directly,
  so those entries need no lookup.
- **Apple TV** — a `pyatv` push listener on the Pi holds a connection to the Apple TV 4K
  and reports the playing title, position, and *which app* is playing. Push updates fire
  only on state change, so the collector also polls position on an interval. Covers
  Apple TV+ at series level: we learn the show and the progress, but not the episode.
- **Dedup** — Plex played on the Apple TV would otherwise be counted twice, so pyatv
  events reporting the Plex app are ignored.
- **Enrichment** — TMDb resolves Apple TV titles to IMDb IDs and years.
- **Publish** — Jinja2 renders one self-contained HTML file; rsync over SSH puts it on
  NearlyFreeSpeech, alongside an `.htaccess` that stops the host's edge cache serving a
  stale page for a quarter of an hour after every publish.
- **Delete** — a private admin page on the Pi lists what's published, with a button per
  entry. Hiding is reversible.

Everything with moving parts runs on the Pi and is never exposed to the internet. The
only public surface is a flat HTML file.

### Known gaps

**Netflix isn't captured at all.** Not a design choice so much as a measured limit: the
Apple TV reports no metadata whatsoever for Netflix, and a browser is invisible to
`pyatv` anyway. The only route left is scraping Netflix's viewing activity page, which
is deferred rather than allowed to hold up the parts that work. A Roku on the same
network is likewise invisible.

## Backfill

Plex keeps its own watch history, so the log didn't have to start from zero:

```
python -m watchlog.plex_history --dry-run   # show what would be imported
python -m watchlog.plex_history             # import it
```

Apple TV has no equivalent — `pyatv` only reports what is playing right now, so
Apple TV+ entries can only accumulate from the moment the listener starts running.

## Configuration

Copy [`.env.example`](.env.example) to `.env` and fill it in. Every value the app needs
is documented there.

```
cp .env.example .env
```

## A note on what's public

The code is public. The data is not, and the split is deliberate:

- **`watchlog.db` is gitignored.** It holds every watch event, including ones deleted
  from the published page. Committing it would republish exactly what a delete was meant
  to remove.
- **The rendered `watchlog.html` is gitignored too.** It's generated output, and its git
  history would become a permanent record of every entry ever deleted.
- **Apple TV pairing credentials and the NFSN SSH key never enter the repo.** Keys live
  in `~/.ssh` on the Pi and are referenced by path.

The published page is the only thing anyone is meant to see, and deleting from it should
actually mean something.
