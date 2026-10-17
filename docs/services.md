# Services

The services built in this repository, what each one does, and how they
connect. Every service does one job, works on its own, and plugs into the
others only through the interfaces listed here. Third-party apps (Lidarr,
Navidrome, Frigate…) are described in [hosted_services.md](hosted_services.md).

## Rules every service follows

1. **One job.** If a feature needs a second sentence to explain why it belongs,
   it belongs to another service.
2. **Its own data.** A service writes only to its own state folder and to the
   output folder it is given. It reads another service's data only through that
   service's API or a hand-off folder (below).
3. **Usable alone.** Its page and its API work without any other service of this
   repository running. What another service adds (musicguard checking songs,
   Apprise delivering messages) is optional.
4. **An API before a page.** The page is a client of the API like any other app.
   Nothing the page does is out of reach of the API.
5. **Says what happened, not who to tell.** A service reports events; it does not
   decide where they are posted.

## The API contract

Every service with an HTTP API:

| | |
| --- | --- |
| Base path | `/api/v1/`. A change that breaks a caller gets `/api/v2/`; v1 keeps working until no caller uses it. |
| Description | `GET /api/v1/openapi.json` (OpenAPI 3). Human docs at `/docs`. |
| Health | `GET /api/v1/health` → `200 {"ok": true}` when the service can do its job. |
| Errors | A 4xx or 5xx status with `{"detail": "<sentence a person can read>"}`. |
| Formats | JSON in and out, UTF-8. Times are Unix seconds (`1792008398.7`). |
| Access | LAN only, through Caddy (`localSubnets`). No login inside the house network today; an API key per caller (`X-Api-Key`) comes before any service is reachable from outside it. |
| Long work | Returns at once with a job and its `id`. The caller polls `GET …/jobs/{id}` or passes a `callback_url`, which receives the job when it ends. |
| Settings | `GET` and `PATCH /api/v1/settings` (only the fields to change). The environment gives the first values; changes made through the API are kept in the service's state folder and win over the environment. Secrets are write-only: the API says whether one is set, never what it is. |

## Hand-off folders

When one service's output is another's input, the hand-off is a folder: the
producer writes a finished file there in one step (written beside, then moved
in), the consumer takes it and removes it. Neither knows the other exists.

| Folder (under `DATA_ROOT`) | Written by | Taken by |
| --- | --- | --- |
| `inbox/music/songdrop` | songdrop (its output folder setting) | musicguard |
| `inbox/music/metube` | MeTube | musicguard |
| `media/music-extras` | musicguard | Navidrome, Stanzarr (read and write lyrics beside songs) |

## Messages

batlab's messages go through **Apprise** (`compose/apprise`,
[apprise-api](https://github.com/caronc/apprise-api)): each sender posts to
its own address, `http://apprise:8000/notify/<sender>`, with a tag naming the
channel. Apprise delivers to that channel's destinations, which
`NOTIFY_<CHANNEL>` in `compose/.env` lists as Apprise URLs: Discord webhooks
here (they default to `DISCORD_WEBHOOK_<CHANNEL>`), Telegram, ntfy, e-mail or
any of about 100 services elsewhere. A service never knows where its messages
end up. The senders, and the name and avatar each shows on Discord, are in
`lib/batlab-common/batlab_common/senders.json`; a new sender is a line there.

| Channel (tag) | Who sends through Apprise | What |
| --- | --- | --- |
| `downloads` | musicguard, subtranslate | "Ready to play: Artist – Album", once Navidrome has it; subtitles translated by AI, or replaced by human ones |
| `download-issues` | musicguard, songdrop, vpnguard, dubkeeper | files quarantined, suspect, flagged or held; songs missing after a download, back, or given up; VPN reconnects; upgrades that lost audio |
| `requests` | clipseerr | a song or a music video asked for, got, or not found by any service |
| `cameras` | camwatch, carwatch | a camera dark, and back; the Clio 4 or the Ford left its spot, or is back, with a picture |
| `alerts` | Netdata, NetAlertX, watchdog (data disk, self-test, SMART), mediascan, updater (failures) | something is wrong with the machine or the network |
| `updates` | updater, Diun | a service updated, a manual update waiting, a new image |

Each message has a title, a Markdown body and a type (`info`, `success`,
`warning`, `failure`), sent with `batlab_common.notify`. Apprise's API listens
on `home_server` and, for the host's scripts, Netdata and NetAlertX (host
networking), on the loopback at `APPRISE_HTTP_PORT` (8011). Its configuration
can't be read or changed through it (`APPRISE_CONFIG_LOCK`), and it sends only
to configured URLs. It has no page; Homepage shows its health.

**When Apprise is down**, the alerts still go out: the host's scripts
(`notify.deliver`, or `python3 -I lib/batlab-common/batlab_common/notify.py`
from a shell) and Netdata post straight to the channel's Discord webhook,
with the same name and avatar. An alert must not depend on the thing that
broke, and Apprise is down while the updater restarts it.

**Still posting to Discord themselves**, because Apprise would cost them
something:

| App | Why it stays |
| --- | --- |
| Sonarr, Radarr, Lidarr | Their Discord cards carry the poster, quality, size and links; their Apprise connection sends a title and plain text. |
| Seerr | Poster and requester in the card; it has no Apprise option. |
| Cleanuparr | Its Discord card has the item's fields and image. |
| Uptime Kuma | It watches every service, Apprise included, so it must not need one of them to speak. |
| frigate-notify | The snapshot sits inside the alert card; through Apprise it would come as a separate message, one hop later, for a person on the roof. |
| import-audit | Not changed yet (its script has unsaved local edits). |

A program that wants songdrop's events as data, not as a message, takes them
through a Webhook connection (Settings, Connect), as JSON:

```http
POST <your address>
{"source": "songdrop", "event": "song.gave_up", "time": 1792008398.7,
 "subject": "Stormy, JRK 19 - EL MEJOR", "data": {"job": {"id": "…"}, "song": "…", "tries": 4}}
```

Event names are `<thing>.<what happened>`:

| Source | Events |
| --- | --- |
| songdrop | `job.incomplete` (songs missing after a download), `song.recovered`, `song.gave_up`, `job.finished` |
| clipseerr | `request.created`, `request.completed`, `request.failed` (`data.request`: the request, as its API gives it) |

## Shared code

`lib/batlab-common` is the Python package every service of this repository
may use: whether two titles name one song and which version (`titles`:
remix, live…), audio fingerprints (`fingerprint`), Spotify's and Apple's
previews (`previews`), and Apprise (`notify`). Standard library only, so it
also runs in spotDL's environment. Images get it through compose's build
context `common` (`additional_contexts`) on `PYTHONPATH`; a mounted script
(camwatch) mounts it; the host's scripts (updater, watchdog, vpnguard,
dubkeeper, subtranslate, mediascan) load it from the checkout. Its tests: `python3 -m unittest discover -s
lib/batlab-common/tests`.

## The services

### songdrop: link → audio files

`compose/music-links/songdrop`, `songdrop.homelab.lan`. Docs: `/docs` on the
service itself.

- **Job**: takes a link (Spotify through spotDL, any site yt-dlp knows through
  yt-dlp) and turns it into audio files, the right song and complete: a Spotify
  download is compared with Spotify's preview and another upload is tried when
  it is another song; failed songs are tried again later.
- **Alone**: paste a link on its page, download the files from it.
- **Plugs in through**: its API (a music browsing app calls
  `POST /api/v1/jobs`); its output folder (a hand-off folder; musicguard's
  inbox in this stack); its events.
- **Owns**: `$VOLUMES_ROOT/songdrop/jobs` (jobs, settings, connections, cookies).
- **Not its job**: naming and tagging songs, lyrics, filing into a library
  (musicguard and Stanzarr).

### clipseerr: music videos → requests

`compose/clipseerr`, `clipseerr.homelab.lan`. API reference: `/api/docs` on
the service.

- **Job**: what there is (the newest music videos, charts, genres, search,
  artists), and what was asked for: a song or a video, handed to the
  services in order, followed, and announced.
- **Alone**: browse and watch with no service at all; requests wait for one.
- **Plugs in through**: its services (Aurral, songdrop, MeTube, or any app
  through a Webhook), its notifications (Apprise, Discord, Webhook), its
  libraries (Navidrome, a folder: what is there already), its API
  (`/api/v1`: requests, the catalog, settings).
- **Owns**: `$VOLUMES_ROOT/clipseerr` (one SQLite database).
- **Not its job**: downloading, checking or filing anything.

### clipfeed: communities → checked video feeds

`compose/clipfeed`, `clipfeed.homelab.lan`. API reference: `/api/docs`.

- **Job**: what subreddits post, as feeds: every post, newest first, with
  what it links to; YouTube videos checked to be real songs (YouTube Music).
- **Alone**: needs nothing else; Reddit, PullPush and YouTube Music without
  a key.
- **Plugs in through**: its API (`/api/v1`: items as JSON, feeds to make and
  change) and Atom feeds. clipseerr's Hot category reads it.
- **Owns**: `$VOLUMES_ROOT/clipfeed` (one SQLite database).
- **Not its job**: downloading, or deciding what to show: it answers
  everything, and its readers filter (link kind, verdict, domain, words…).

### musicguard: inbox → checked library

`compose/musicguard`. No page and no API yet.

- **Job today**: everything after the download. It checks that a file is safe
  and really audio, rebuilds it, identifies the song (Shazam, Apple Music,
  MusicBrainz), tags it, files it, removes duplicates, hands songs to Lidarr
  for better copies, keeps lyrics files tidy, and starts Navidrome's scans.
- **Alone**: point its inbox and library folders anywhere; it needs only ffmpeg
  and, for malware checks, clamd.
- **Plugs in through**: the hand-off folders, Lidarr's and Navidrome's APIs, its
  messages (Apprise) and its reports (`reports/<month>.jsonl`).
- **Owns**: `$VOLUMES_ROOT/musicguard`, `media/music-extras`, the quarantine.
- **Several jobs, one service.** Its code is one module per job
  (`app/parts/`: checks, filing, duplicates, upgrades, lyrics, quarantine,
  announcements; docs/music.md). They stay one service on purpose: each file
  goes through them in order, on one copy, under one lock; three services
  would hand every file over twice through folders and agree on its state
  through APIs, for no gain while one machine runs them. A part becomes a
  service when something else needs it alone.

### Stanzarr: lyrics

`compose/stanzarr`, `stanzarr.homelab.lan`; its own private project.

- **Job**: every song's lyrics: tracks them, fetches them from LRCLIB, syncs
  plain lyrics by AI on a GPU worker, and hosts the lyrics editor.
- **Alone**: point it at any music folder.
- **Plugs in through**: the library folders (it writes `.lrc` and `.txt` beside
  songs), its worker API.

### camwatch: camera health

`compose/frigate/camwatch`. No page.

- **Job**: says when a Frigate camera gives no picture, why, and when it is back.
- **Alone**: needs only Frigate's API.
- **Plugs in through**: its messages (Apprise, `cameras`).

### carwatch: who parked, who left

`compose/frigate/carwatch`. No page.

- **Job**: says when a car leaves its parking spot on the street camera, and
  when it is back, with a picture of the street.
- **Alone**: needs Frigate's events on MQTT and a zone per spot.
- **Plugs in through**: its messages (Apprise, `cameras`).

### tv-remote: TV remote

`compose/tv-remote`, `tv.homelab.lan`.

- **Job**: a web remote for the Samsung TVs on the network: find, pair, wake,
  send keys.
- **Alone**: yes; it talks only to the TVs.

## Where the services stand

| Service | One job | API v1 | Docs at `/docs` | Settings API | Events |
| --- | --- | --- | --- | --- | --- |
| songdrop | yes | yes | yes | yes | its connections (Apprise here; Discord, Webhook) |
| musicguard | several, one module each | none | none | environment only | Apprise |
| Stanzarr | yes | own (private project) | no | in its page | none |
| camwatch | yes | none (needs none) | none | environment only | Apprise |
| carwatch | yes | none (needs none) | none | environment only | Apprise |
| tv-remote | yes | unversioned | no | environment only | none |

Next: installing batlab elsewhere (published images, a short `.env.example`).
