# Watchlog

A running list of what I watch, published automatically to
**[adamwitwer.com/watchlog](https://adamwitwer.com/watchlog)**.

No confirmation step, no manual entry, nothing to maintain. Things I watch on Plex and
on the Apple TV show up on a page. If I don't want one there, I delete it.

Design and decisions: [`miniPRD.txt`](miniPRD.txt)

## Status

**Phase 0 — spike.** `pyatv` is installed on the Pi and discovers the Apple TV. What
remains is pairing (needs physical access to the TV, since tvOS shows the PIN on screen)
and a metadata dump to find out what Netflix and the Apple TV app actually report.

Phases 1–4 — Plex end to end, the Apple TV listener, TMDb enrichment, and the delete
UI — are described in the miniPRD.

## How it works

Two sensors feed one SQLite database on a Raspberry Pi, which renders a single static
HTML file and rsyncs it to the web host.

- **Plex** — Plex Pass fires a webhook at 90% watched. Device-independent, so this
  covers Plex on the TV, in a browser, and on a phone. Plex supplies IMDb IDs directly,
  so those entries need no lookup.
- **Apple TV** — a `pyatv` push listener on the Pi holds a connection to the Apple TV 4K
  and reports the playing title, position, and *which app* is playing. That last part
  gives service attribution for free, and covers Apple TV+ and Netflix on the box.
- **Dedup** — Plex played on the Apple TV would otherwise be counted twice, so pyatv
  events reporting the Plex app are ignored.
- **Enrichment** — TMDb resolves Apple TV titles to IMDb IDs and years.
- **Publish** — Jinja2 renders one self-contained HTML file; rsync over SSH puts it on
  NearlyFreeSpeech.

Everything with moving parts runs on the Pi and is never exposed to the internet. The
only public surface is a flat HTML file.

### Known gaps

Netflix watched in a browser isn't captured — only Netflix on the Apple TV. That's a
deliberate v1 tradeoff to avoid maintaining a scraper. A Roku on the same network is
also invisible to both sensors.

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
