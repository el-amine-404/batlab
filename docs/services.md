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
   the notifier posting to Discord) is optional.
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

## Events

Each service will post what happened to one **notifier** service
(planned), which decides the channels (Discord today) and the wording:

```http
POST http://notifier:8900/api/v1/events
{"source": "songdrop", "event": "song.failed", "time": 1792008398.7,
 "subject": "Stormy, JRK 19 - EL MEJOR", "data": {"job_id": "…", "next_try_at": 1792009000}}
```

Until the notifier exists, songdrop, musicguard and camwatch post to their
Discord webhooks themselves. Event names are `<thing>.<what happened>`:

| Source | Events |
| --- | --- |
| songdrop | `job.finished`, `song.failed`, `song.recovered`, `song.gave_up`, `song.replaced` (another upload was the right song) |
| musicguard | `file.quarantined`, `file.held`, `album.ready` (playable in Navidrome), `song.deduplicated`, `song.upgraded` |
| camwatch | `camera.dark`, `camera.back` |

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
- **Owns**: `$VOLUMES_ROOT/songdrop/jobs` (jobs, settings, cookies).
- **Not its job**: naming and tagging songs, lyrics, filing into a library
  (musicguard and Stanzarr).

### musicguard: inbox → checked library

`compose/musicguard`. No page and no API yet.

- **Job today**: everything after the download. It checks that a file is safe
  and really audio, rebuilds it, identifies the song (Shazam, Apple Music,
  MusicBrainz), tags it, files it, removes duplicates, hands songs to Lidarr
  for better copies, keeps lyrics files tidy, and starts Navidrome's scans.
- **Alone**: point its inbox and library folders anywhere; it needs only ffmpeg
  and, for malware checks, clamd.
- **Plugs in through**: the hand-off folders, Lidarr's and Navidrome's APIs, its
  events (Discord today) and its reports (`reports/<month>.jsonl`).
- **Owns**: `$VOLUMES_ROOT/musicguard`, `media/music-extras`, the quarantine.
- **Not one job yet.** The planned split: **guard** (safe and really audio:
  checks and rebuild), **identify** (which song, and its tags), **librarian**
  (where it goes: filing, duplicates, upgrades). The audio-fingerprint
  comparison it shares with songdrop becomes one library both use.

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
- **Plugs in through**: its events (Discord today).

### tv-remote: TV remote

`compose/tv-remote`, `tv.homelab.lan`.

- **Job**: a web remote for the Samsung TVs on the network: find, pair, wake,
  send keys.
- **Alone**: yes; it talks only to the TVs.

## Where the services stand

| Service | One job | API v1 | Docs at `/docs` | Settings API | Events |
| --- | --- | --- | --- | --- | --- |
| songdrop | yes | yes | yes | yes | Discord directly |
| musicguard | no (split planned) | none | none | environment only | Discord directly |
| Stanzarr | yes | own (private project) | no | in its page | none |
| camwatch | yes | none (needs none) | none | environment only | Discord directly |
| tv-remote | yes | unversioned | no | environment only | none |

Next, in order: the notifier, and songdrop, musicguard and camwatch posting
events to it; the fingerprint library; musicguard's split.
