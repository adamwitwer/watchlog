# Watchlog

A running list of what I watch, published automatically to
**[adamwitwer.com/watchlog](https://adamwitwer.com/watchlog)**.

Nothing stands between watching something and it appearing on the page — no confirmation
step, no queue to review. What the sensors can't see, I can type in afterwards. Anything
I'd rather not have up there, I delete.

Design and decisions: [`miniPRD.txt`](miniPRD.txt)

## How it works

Everything with moving parts runs on a Raspberry Pi. Sensors write watch events to one
SQLite database; the Pi renders a single self-contained HTML file and rsyncs it to the
web host. The only public surface is that flat file.

### Where entries come from

- **Plex** — Plex Pass fires a webhook at 90% watched. This is server-side, so it covers
  Plex on the TV, in a browser, and on a phone equally. Plex supplies IMDb ids directly,
  so those entries need no lookup.
- **Apple TV** — a `pyatv` listener holds a connection to the Apple TV 4K and reports the
  playing title, position, and *which app* is playing. Push updates fire only on state
  change, so position is polled as well. Apps are allowlisted, which is also what stops
  Plex-on-the-Apple-TV being counted twice.
- **By hand** — the admin page can create a whole entry for anything no sensor reaches.
  Netflix above all: it reports no metadata at all from the Apple TV, so it is typed or it
  is nothing. Manual entries are stored with `source = manual` and the same dedup key
  shape the sensors write, so a typed entry and a scrobble for the same episode recognise
  each other rather than both landing on the page.

### What happens to them

- **Grouping** — episodes collapse into one entry per show per night, with the day rolling
  over at `NIGHT_ROLLOVER_HOUR`. Six lines for one evening's bingeing would bury
  everything else on a page built around large type. Movies stay individual.
- **Enrichment** — TMDb resolves titles that arrive without ids into IMDb ids and years,
  cached per title. A `locked` flag pins a hand-corrected match so the enricher leaves it
  alone.
- **Publish** — Jinja2 renders one file; rsync over SSH puts it on NearlyFreeSpeech,
  alongside an `.htaccess` that stops the host's edge cache serving a stale page for a
  quarter of an hour after every publish.

### The page

Newest first, large type, no images. Each entry carries the title, the season and episode
where they're known, the episode title, the date, the service, and a link to IMDb.

- **Every entry is shown.** `PAGE_LIMIT` is `None`. The page is repetitive enough to
  compress about 10:1 — 233 entries are 164KB of HTML and 18KB over the wire — so there is
  little reason to cap it.
- **Episode titles** appear for nights of up to `EPISODE_TITLES_MAX` episodes. A longer
  binge keeps the episode range and drops the titles rather than turning one scannable
  line into a paragraph. Plex's `Episode 4` placeholders are treated as absent, since they
  only restate the label above them.
- **A timeline rail** runs down the right edge, one hairline per entry, so a year of
  viewing reads as texture. Hovering expands a tick and labels it with its date; clicking
  jumps to that entry. On touch it becomes a labelled month index instead, since hover
  cannot reveal anything and a 4px tick is not a tap target. Past `RAIL_MAX_TICKS` the
  fine ticks thin out so the rail stays legible however long the log gets.
- **Filtering** narrows the list as you type — `/` to focus, Escape to clear. Every entry
  is already in the page, so this is one substring test per row per keystroke: about
  0.2ms for the whole log, with no index and nothing to fetch. It matches the title, the
  season and episode label, the episode titles and the service, but not the year: on a
  page this full of numbers, typing `2019` to find one film would return everything
  released that year. While a filter is on, the rail rebuilds itself from the survivors,
  one tick per month, and disappears entirely when fewer than two months are left.

The one piece of JavaScript on the page is that filter, about 90 lines, inline. The
search box ships `hidden` and the script reveals it, so a browser without JavaScript is
never shown an input that cannot do anything — and everything else here, the rail
included, is anchors and CSS that works regardless.

Each row carries a `data-q` attribute holding its searchable text, folded by
`render.search_normalize` — lowercased, diacritics stripped, apostrophes deleted so
`bobs` finds `Bob's`. The browser folds the typed query the same way. Those two folds
have to agree, so `tests/test_search.py` extracts the template's `fold()` and runs it
under node against the Python over the same corpus.

### The admin page

Private to the LAN and the Tailnet, 404s without its token, never exposed to the
internet. It can:

- **delete** an entry — hiding rather than destroying, so it can be restored, and
  republishing immediately
- **edit** season, episode and episode title on any entry backed by a single event, which
  is every Apple TV entry, because the device reports none of the three
- **add** an entry outright, resolving the IMDb link and year from the title
- **fix a bad match** — TMDb search takes the most popular result, which for an ambiguous
  title ("Dark Matter" is a 2024 Apple TV+ series and a 2015 Syfy one) is sometimes the
  wrong show, and the result is a wrong IMDb link on a public page. Typing the right id
  repoints every entry for that show — including other spellings that normalise to the
  same key — and pins it in the `titles` cache with `locked=1`, which is the first thing
  that has ever set the flag the schema always had. The stale `tmdb_id` is dropped rather
  than kept, since it was resolved alongside the id that turned out to be wrong.
- **report health** — see below

## Staying alive

Every component here has a healthy state that looks exactly like a dead one: they are all
silent when idle. Most of them have failed silently at least once.

- **The Plex webhook** stops without warning. PMS asks plex.tv for its hook list *once, at
  startup*; if that request loses a race with DNS after a reboot, it delivers to zero
  hooks until someone restarts it, and reports nothing.
- **`watchlog-reconcile.timer`** covers that by re-reading the last `RECONCILE_DAYS` of
  Plex's own history every hour and importing whatever is missing. Plex's history is the
  server's own record and is never wrong.
- **The Apple TV listener** can wedge. `atv.metadata.playing()` has no timeout of its own,
  so the half-open connection a router reboot leaves behind made it await forever — with
  the process up, the socket still `ESTABLISHED`, and nothing in the log. The poll is now
  bounded, and a few unanswered polls in a row force a reconnect.
- **Publishing** can fail on its own. Three of `push()`'s four callers catch the
  exception and carry on — only reconcile lets it propagate — so a web host that had
  stopped accepting the file would leave everything on the Pi looking perfect while the
  live page quietly went stale.

So each of them records what happened, and the admin page reads it back:

```
● Plex webhook delivered 2 hours ago
● Apple TV listener polled just now
● Last reconcile 18 minutes ago
● Last publish 18 minutes ago
```

A line goes red two ways, and they are not the same thing:

- **Stale** — a heartbeat that has stopped being refreshed. Applies only to something with
  a cadence to miss: reconcile runs hourly, the listener polls constantly. Publishing has
  no cadence, so a quiet week there is a quiet week, not a fault.
- **Failed** — the last attempt raised, whatever its age. Age alone was not enough:
  reconcile could error at 10:00, still be holding a 09:00 success, and read green for
  another hour.

The webhook needs a third test, because reconcile made it *more* invisible rather than
less: with the safety net backfilling whatever it drops, a dead webhook has no visible
consequence at all — the log stays correct while the sensor rots. What gives it away is
reconcile finding anything, since every row it recovers is one the webhook should have
delivered. A recovery more recent than the last live delivery turns the line red; a
delivery after the last recovery means it came back.

**Anything added here should carry a heartbeat from the start.**

## Running it

Four systemd units on the Pi, all enabled at boot:

| unit | what it does |
| --- | --- |
| `watchlog-webhook.service` | receives Plex scrobbles |
| `watchlog-appletv.service` | holds the `pyatv` connection |
| `watchlog-admin.service` | the private admin page |
| `watchlog-reconcile.timer` | hourly catch-up against Plex history |

```
python -m watchlog.plex_history --reconcile --dry-run   # what the timer would import
python -m watchlog.render                               # render without publishing
python -m tests.test_grouping                           # and test_tracker, test_admin
```

## Backfill

Plex keeps its own watch history, so the log didn't start from zero:

```
python -m watchlog.plex_history --dry-run   # show what would be imported
python -m watchlog.plex_history             # import it
```

Apple TV has no equivalent. `pyatv` only reports what is playing right now, so those
entries accumulate only from the moment the listener starts running.

## Configuration

Copy [`.env.example`](.env.example) to `.env` and fill it in. Every value is documented
there.

```
cp .env.example .env
```

One thing worth repeating from that file: **address the Plex host by a name, not by a
DHCP lease.** A hardcoded `192.168.x.y` works right up until the router reboots and hands
it a different one, at which point reconcile fails hourly and nothing gets logged. Watch
out for hosts with more than one active interface, too — a Mac with Ethernet and Wi-Fi
both up holds two addresses, and the wired one can be an order of magnitude faster.

## Known limits

- **Netflix has no automatic path.** Not a design choice: the Apple TV reports no metadata
  whatsoever for Netflix, and a browser is invisible to `pyatv` anyway. Scraping the
  viewing activity page was always the most fragile thing in the design, and typing an
  entry takes seconds, so it stays unbuilt.
- **Apple TV+ entries arrive without season or episode.** The device reports a series name
  and nothing more. They can be filled in by hand.
- **The Apple TV listener only sees the living-room box.** Plex is observed at the server
  and so covers every client; the Apple TV is observed at one device. Watching Apple TV+
  on a laptop or a phone is invisible.
- **There is no UI for correcting a bad TMDb match.** The `titles.locked` column exists
  for it, but nothing exposes it yet.

## A note on what's public

The code is public. The data is not, and the split is deliberate:

- **`watchlog.db` is gitignored.** It holds every watch event, including ones deleted from
  the published page. Committing it would republish exactly what a delete was meant to
  remove.
- **The rendered `watchlog.html` is gitignored too.** It's generated output, and its git
  history would become a permanent record of every entry ever deleted.
- **Apple TV pairing credentials and the NFSN SSH key never enter the repo.** Keys live in
  `~/.ssh` on the Pi and are referenced by path.

The published page is the only thing anyone is meant to see, and deleting from it should
actually mean something.
