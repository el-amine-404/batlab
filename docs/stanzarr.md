# Stanzarr: where it stood, and the plan

**Done in 1.0 (2026-10-22)**: steps 1 to 3 and 5 of the plan below. Its source
is in batlab (`compose/stanzarr/app`), it runs on batlab-common and batlab-ui
with the new look, sources are add-ons, `GET /api/v1/lyrics` answers other
apps, and Homepage has its card. Left: step 4 (clipseerr and videodrop asking
it), its docs site, appcheck covering it.

Stanzarr (stanzarr.homelab.lan) gives every song in the libraries its lyrics:
it finds what each song has, fetches what is missing from LRCLIB, turns plain
lyrics into synced ones with AI on a GPU worker, and has an editor to fix the
timings. Version 0.1.0, written before subfeed, videodrop, songdrop and
clipseerr settled on one way of being built; it has none of what they share.

## What it is today

| | |
| --- | --- |
| Code | Backend 1,800 lines (FastAPI, SQLite), page 4,000 (React, shadcn/ui), GPU worker 340 (Demucs, three aligners, a vote). |
| Where | **Only on lab2**, in `~/stanzarr`, a git repository with no remote. Built from there (`STANZARR_SOURCE`). A dead disk loses it. |
| Library | 399 songs (Navidrome counts 421). |
| Good, keep | The scanner (reads only what changed), the lyrics formats (.lrc, .elrc, .txt, .ttml, Navidrome's .yaml, embedded), LRCLIB's matching across credits, the worker protocol, the AI vote (86 % of lines within 1 s), the editor, "a person's file is never overwritten", backups. |

## Stanzarr and musicguard

They meet at the music folders, and do different jobs:

| | musicguard | Stanzarr |
| --- | --- | --- |
| Its question | May this file enter the library? | What are this song's lyrics, and are they in time? |
| When | Once, as a file arrives (inbox, Lidarr, Aurral) | All the time, for every song already there |
| Does | Identifies (Shazam), checks it decodes, cleans and completes tags, files it, quarantines the bad | Finds, fetches, syncs and edits lyrics; writes `.lrc` beside the song |
| Touches | The audio file and its place | Only the lyrics files beside it (and reads the audio) |
| A person | Never needed | The editor: a person makes the timings right |

musicguard fetched lyrics once; that is Stanzarr's now. musicguard still checks
lyrics files and starts Navidrome's scan when Stanzarr writes one.

## What it lacks, next to the others

| | The others | Stanzarr |
| --- | --- | --- |
| People | Accounts, permissions, API keys (batlab-common) | One admin from the environment; no keys |
| API | `/api/v1`, OpenAPI, Scalar at `/docs`, `{"detail"}` errors, the contract | `/api/…`, no version, no reference |
| Messages | Connections (Apprise, Discord, webhook), events | None |
| Page | batlab-ui: the sidebar, Settings with its own list, the Live dot | Its own shadcn sidebar, a generic look |
| Sources | — | LRCLIB written into the code (`lrclib.py`, a flag in settings) |
| Checks | appcheck, tests in the image build | Its own CI on a host it doesn't have |
| Homepage | Cards from `/system/status` | A dot (no key to read with) |

clipseerr (`catalog/lrclib.py`) and videodrop (`lyrics.py`) each have an LRCLIB
client of their own: three places that ask for lyrics.

## The plan

### 1. Lyrics are Stanzarr's, for everyone

One service owns lyrics; the others ask it.

- `GET /api/v1/lyrics?artist=&title=&album=&duration=`: the best lyrics
  for any song, in the library or not: synced first, with where they came from
  and whether a person or the AI synced them. It looks in the library, then
  asks the sources, and keeps what it found.
- clipseerr's lyrics for a video and videodrop's Lyrics subtitle track ask
  Stanzarr instead of LRCLIB; without Stanzarr, they have none (rule 3:
  usable alone, the extra is optional).
- Events (`lyrics.found`, `lyrics.synced`, `lyrics.missing`, `ai.to_check`)
  through connections, like the others.

### 2. Sources are add-ons

A source is a small module behind one interface, chosen in Settings, Sources,
not written into the code:

```python
class LyricsSource:
    key = "lrclib"; label = "LRCLIB"; fields = [...]          # its settings, like a connection's
    def search(self, song: SongQuery) -> list[Candidate]: ... # synced and/or plain, with a score
    def test(self, config) -> str: ...                        # "Settings, Sources, Test"
```

- Each has its own settings, an order (asked first to last), a switch, a
  rate limit and a Test button, as clipseerr's Services.
- Built in: **LRCLIB**, **the song's own tags**, **files beside it**. Others
  are one module each, added when wanted (NetEase, Musixmatch with a token,
  Genius for plain text), never needed.
- The AI sync is one too: a source that turns plain lyrics into synced ones,
  there while a worker with a GPU is online.

### 3. Built like the others

- On batlab-common (accounts, permissions, keys, body limit, threads, signed
  callbacks, `/api/v1`, Problem errors) and batlab-ui. **Where its source lives
  is to decide**: batlab is public on GitHub and Stanzarr isn't public yet.
  Either it moves into batlab as `compose/stanzarr/app` (public, built like
  the others, one place), or it stays a repository of its own (private, on
  GitHub) that takes batlab-common and batlab-ui as a git submodule.
- Permissions: read, edit lyrics, queue AI, settings, admin; a worker's key
  only takes and returns AI jobs.
- Its docs on GitHub Pages beside songdrop's (`/batlab/stanzarr/`).

### 4. The page

- The batlab look: `stanzarr` in lower case, its own colour (emerald: the
  others have orange, sky, violet and rose) and a new mark in the same rounded
  square. The sidebar: Library, To check, Queue; Settings with General,
  Sources, Workers, Connections, People, API keys, Status.
- **Library** opens with the coverage at the top (synced, AI to check, plain,
  missing), then the songs, filtered and searched on the server (FTS5),
  paged, a song's lyrics a click away in a side panel.
- **To check**: the AI lines it was unsure of, one after the other, play,
  nudge, accept: the work a person does, in one place.
- **The editor** stays, in the new frame; keyboard first (space, arrows,
  enter), on a phone too.

### 5. Homepage card

From `/api/v1/system/status`, with a key that only reads: **Synced**,
**To check**, **Plain only**, **Missing**.

### 6. Checked

appcheck covers it like the others (load, fuzzing, abuse); its tests run in
the image build.

## Order

1. Push the source somewhere safe (a private GitHub repository) before
   anything else, and decide where it lives (above).
2. The backend on batlab-common, `/api/v1`, sources as add-ons, the lyrics
   endpoint; the old page keeps working on the old routes meanwhile.
3. The page on batlab-ui, with its new look.
4. clipseerr and videodrop ask Stanzarr; their LRCLIB clients go.
5. Homepage card, docs site, appcheck.

## Done in 1.1

- **Find lyrics** is the home page: a song's name or a link (YouTube's
  oEmbed, Spotify's embed page, Deezer's and Apple's lookup APIs, other
  music sites' page title; never a link inside the network), the library
  first, then every source, each answer to copy or download. Odesli's
  public API, the first plan for links, closed in 2026.
- **Sources**: NetEase, Musixmatch and Genius beside LRCLIB; the ISRC from
  the tags matches Musixmatch exactly. The filter between them and the disk,
  with a quarantine.
- **Quality profile**: a cutoff on the ladder (plain, AI, AI checked, synced,
  words in time) and AI sync by itself for plain songs when a GPU is online.
- **Library**: a sortable table, filters (status, language, format, made by,
  artist, album) with counts, page numbers and sizes, grouped by album with
  the embedded covers, compact rows, j/k; the coverage bar or ring says each
  part on hover and narrows to it on click; the coverage day by day.
- **Song page**: every version in a tab, side by side, downloadable; who
  made each, its timings, its language, each a link to the songs like it;
  why an action can't run, said under it; back to the library as it was left.
- **Activity**: what runs now (the scan's and the search's progress, the AI
  jobs) and the log, with who asked and what failed.
- **Workers**: a key each, added, renamed and removed in Settings, Workers;
  the old shared key works until retired.
- **clipseerr and videodrop** ask stanzarr first, LRCLIB when it is down
  (`batlab_common/lyrics.py`).
