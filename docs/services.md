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
| Access | LAN only, through Caddy (`localSubnets`), and everyone known: a person signed in on the page (a session cookie), or an app with an API key of its own (`X-Api-Key`, or `Authorization: Bearer`). Only health, signing in and callbacks that just wake a service are open. Each person and key has permissions (`lib/batlab-common`: `accounts`, `web`). |
| Long work | Returns at once with a job and its `id`. The caller polls `GET …/jobs/{id}` or passes a `callback_url`, which receives the job when it ends. |
| Settings | `GET` and `PATCH /api/v1/settings` (only the fields to change). The environment gives the first values; changes made through the API are kept in the service's state folder and win over the environment. Secrets are write-only: the API says whether one is set, never what it is. |

## Who does what

```
 subfeed ──feeds──▶ clipseerr ──requests──▶ Aurral / songdrop ──files──▶ musicguard ──▶ Navidrome
 (sources as         (discover,              videodrop ─────────────────────────────▶ Jellyfin
  feeds)              ask, track)            (download only)            (check, tag, file)
```

| Service | Owns | Never |
| --- | --- | --- |
| subfeed | Turning a source (Reddit) into feeds any program reads | Downloads, or decides what is wanted |
| clipseerr | Discovery, requests, and following them across the services that download | Writes media files; it asks a service that downloads |
| songdrop | Getting audio from a link, and that it is the recording asked for | Names, tags or files songs into a library |
| videodrop | Getting video from a link | Library decisions |
| musicguard | The only way into the music library: the file is sound, tagged, with lyrics, filed | Downloads |

Where two could overlap, the line is drawn here:

- **"Is it the right song?" is asked twice, differently.** songdrop checks the
  download is the recording that was asked for (Spotify's preview, the
  title); musicguard checks the file is fit for the library (decodes, its
  tags, who it is by Shazam). Neither repeats the other's check.
- **clipseerr's previews.** The play-on-hover clips are cut by ffmpeg in
  clipseerr: read-only, a few at once (`catalog/preview.py`), never kept as
  media. Anything heavier is a download, and goes to videodrop.
- **Downloading services share one contract** (below), so clipseerr's
  adapters stay thin and a new one plugs in the same way.

## The download contract

songdrop and videodrop, and any service that turns a link into files:

| | |
| --- | --- |
| Ask | `POST /api/v1/jobs` with `url`, `options` (the service's own), `callback_url`. Answers `201` with the job at once. |
| Follow | `GET /api/v1/jobs/{id}`; `GET /api/v1/jobs` lists them, newest first. |
| The job | `id`, `url`, `site`, `status`, `title`, `progress`, `files`, `asked_by`, `created_at`, `started_at`, `finished_at`. |
| Status | `queued`, `running`, then one of `finished`, `failed`, `cancelled` (final). A service may have a final status of its own besides, said in its API (songdrop's `partial`: some songs missing). |
| Act | `POST …/jobs/{id}/cancel`; `DELETE …/jobs/{id}` removes a finished job and its files. |
| Files | `GET …/jobs/{id}/files/{file}`, while the service keeps them. |
| Told | `callback_url` gets the job (POST, JSON) when it ends, signed with `callback_secret` when one is given: `X-<App>-Signature` is `sha256=` and the HMAC-SHA256 of `<X-<App>-Timestamp>.<body>` (`batlab_common.callbacks`). A caller that only wakes up on a call and then asks the service itself (clipseerr) needn't check it: a forged call changes nothing. |

Where they still differ, each the service's own and within the contract:

| | songdrop | videodrop |
| --- | --- | --- |
| `progress` | 0 to 1 | an object (bytes, speed, stage) |
| Live changes | polling | `GET /api/v1/jobs/events` (SSE) |

## Hand-off folders

When one service's output is another's input, the hand-off is a folder: the
producer writes a finished file there in one step (written beside, then moved
in), the consumer takes it and removes it. Neither knows the other exists.

| Folder (under `DATA_ROOT`) | Written by | Taken by |
| --- | --- | --- |
| `inbox/music/songdrop` | songdrop (its output folder setting) | musicguard |
| `media/music-extras` | musicguard | Navidrome, Stanzarr (read and write lyrics beside songs) |
| `media/music-videos` | videodrop (its `music-videos` destination) | Jellyfin; clipseerr reads it (what is there already) |

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
| `downloads` | musicguard, subtranslate, videodrop | "Ready to play: Artist – Album", once Navidrome has it; subtitles translated by AI, or replaced by human ones; "Downloaded: <title>", each video finished (a person's or an app's) |
| `download-issues` | musicguard, songdrop, videodrop, vpnguard, dubkeeper | files quarantined, suspect, flagged or held; songs missing after a download, back, or given up; videos that failed, the media disk nearly full; VPN reconnects; upgrades that lost audio |
| `requests` | clipseerr | a song or a music video asked for, got, or not found by any service |
| `cameras` | camwatch, carwatch | a camera dark, and back; the Clio 4 or the Ford left its spot, or is back, with a close-up, the street, the way it went and the clip |
| `alerts` | Netdata, NetAlertX, watchdog (data disk, self-test, SMART), mediascan, updater (failures), subfeed | something is wrong with the machine or the network; a feed failing, and working again |
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
| subfeed | `feed.failing`, `feed.recovered`, `posts.new` (`data.feed`: the feed's slug; `data.posts`: the new posts, as its API gives them) |
| videodrop | `job.failed`, `job.finished` (`data.job`: the job, as its API gives it), `disk.low` |

## Shared code

`lib/batlab-common` is the Python package every service of this repository
may use: whether two titles name one song and which version (`titles`:
remix, live…), audio fingerprints (`fingerprint`), Spotify's and Apple's
previews (`previews`), Apprise (`notify`), each web app's people and API
keys (`accounts`; `web`, the FastAPI side: who asks, the sign-in, people
and key routes; `admin`, the shell commands), what an address is called
on the network (`netnames`) and the egress proxy (`egress`). Standard
library only, `web` aside (FastAPI, only the web servers import it), so it
also runs in spotDL's environment. The web apps' image builds run its
accounts and egress tests. Images get it through compose's build
context `common` (`additional_contexts`) on `PYTHONPATH`; a mounted script
(camwatch) mounts it; the host's scripts (updater, watchdog, vpnguard,
dubkeeper, subtranslate, mediascan) load it from the checkout. Its tests: `python3 -m unittest discover -s
lib/batlab-common/tests`.

`lib/batlab-ui` is the same for the web apps' pages (subfeed, videodrop,
songdrop, clipseerr): the frame, the sidebar and the settings' own list, so
the four look and work alike. TypeScript source built with each app's page,
through the build context `ui`; an app takes only the pieces it uses
([its README](../lib/batlab-ui/README.md)).

## The services

### songdrop: link → audio files

`compose/songdrop/app`, `songdrop.homelab.lan`. Docs: `/docs` on the
service itself.

- **Job**: takes a link (Spotify through spotDL, any site yt-dlp knows through
  yt-dlp) and turns it into audio files, the right song and complete: a Spotify
  download is compared with Spotify's preview and another upload is tried when
  it is another song; failed songs are tried again later.
- **Alone**: paste a link on its page, download the files from it.
- **Plugs in through**: its API (a music browsing app calls
  `POST /api/v1/jobs`); its output folder (a hand-off folder; musicguard's
  inbox in this stack); its events.
- **Who may use it** (`app/accounts.py, app/auth.py`, the shared `accounts` and `web`): nobody
  without an account, as videodrop. People sign in on the page; apps send
  an API key (Settings → API keys, shown once, kept as a hash). Permissions:
  ask for downloads, see everyone's downloads (else only one's own), change settings, admin (people and keys). Downloads and the notifications name who asked ("Asked by …"); only a key may ask for a `callback_url`. Before anyone exists, the page makes the first admin with a
  setup code from its log or `docker exec songdrop admin
  setup-code`; the same tool resets a password and makes or removes keys.
  clipseerr's key is `SONGDROP_CLIPSEERR_KEY`.
- **Its way out**: yt-dlp's downloads go through the egress proxy
  (`batlab_common/egress.py`, as videodrop's): the internet, never the home
  network. spotDL's don't: it only ever gets Spotify's links, and reaches
  Spotify, YouTube Music and the lyrics sites.
- **Tests**: the image's build runs them; a failing one fails `make up`.
- **Owns**: `$VOLUMES_ROOT/songdrop/jobs` (jobs, settings, connections, cookies, and
  `accounts.db`, the people and keys).
- **Not its job**: naming and tagging songs, lyrics, filing into a library
  (musicguard and Stanzarr).

### clipseerr: music videos → requests

`compose/clipseerr`, `clipseerr.homelab.lan`. API reference: `/api/docs` on
the service.

- **Job**: what there is (the newest music videos, charts, genres, search,
  artists, Hot), and what was asked for: a song or a video, handed to the
  services in order, followed, and announced. It judges which videos are
  real songs (YouTube Music, `catalog/verdicts.py`): the feeds Hot reads
  only say what a community posted. Hot is made again by the first 20-min
  warming once it is 30 min old (so about every 40 min); its page says when it was checked,
  whether that changed it (or for how long nothing has), when it is checked
  next, and, on hover, when subfeed last read Reddit and reads it next.
- **Alone**: browse and watch with no service at all; requests wait for one.
- **Plugs in through**: its services (Aurral, songdrop, videodrop, or any app
  through a Webhook), its notifications (Apprise, Discord, Webhook), its
  libraries (Navidrome, a folder: what is there already), Hot's feeds (any
  JSON Feed: subfeed's here), its API (`/api/v1`: requests, the catalog,
  settings).
- **Who may use it** (`app/accounts.py, app/auth.py`, the shared `accounts` and `web`): nobody
  without an account, as videodrop. People sign in on the page; apps send
  an API key (Settings → API keys, shown once, kept as a hash). Permissions:
  watching needs none; ask for songs and videos, see everyone's requests (else only one's own), change settings (services, notifications, libraries, Hot's picks), admin. Requests, their page and the notifications name who asked. The callbacks (`/hooks/…`) stay open: they only wake the engine, which asks the app itself. Before anyone exists, the page makes the first admin with a
  setup code from its log or `docker exec clipseerr admin
  setup-code`; the same tool resets a password and makes or removes keys.
  It calls songdrop and videodrop with their keys, and reads Hot's feed
  with subfeed's (`SUBFEED_CLIPSEERR_KEY`).
- **Tests**: the image's build runs them; a failing one fails `make up`.
- **Owns**: `$VOLUMES_ROOT/clipseerr` (one SQLite database).
- **Not its job**: downloading, checking or filing anything.

### subfeed: communities → feeds

`compose/subfeed`, `subfeed.homelab.lan`. API reference: `/docs`.

- **Job**: what communities post, as feeds a program can read: every post
  of a subreddit, newest first, with what it links to and the videos it
  carries, as [JSON Feed 1.1](https://www.jsonfeed.org/version/1.1/), Atom
  and its own JSON. It judges nothing: whether a video is a song, or worth
  showing, is for its readers.
- **Alone**: needs nothing else; Reddit's public feed and PullPush's archive,
  without a key. Its page makes and changes the feeds and shows their posts.
- **Plugs in through**: its feeds (`/api/v1/feeds/{slug}/feed.json` and
  `…/feed.atom`, with the same filters as `…/posts`; an `ETag` answers an
  unchanged feed with a bodyless 304), its API (`/api/v1`: feeds, posts,
  settings, notifications) and its events (Apprise, Discord, Webhook).
  clipseerr's Hot reads r/SexyMusicVideos' JSON Feed.
- **Who may use it** (`subfeed/accounts.py, subfeed/api/auth.py`, the shared `accounts` and `web`): nobody
  without an account, as videodrop. People sign in on the page; apps send
  an API key (Settings → API keys, shown once, kept as a hash). Permissions:
  reading (feeds, posts, JSON Feed, Atom) needs none; manage feeds, change settings (and notifications), admin. A feed reader that can't send a header puts its key in the feed's address (`…/feed.json?key=…`); the addresses a feed writes into itself leave it out. Before anyone exists, the page makes the first admin with a
  setup code from its log or `docker exec subfeed admin
  setup-code`; the same tool resets a password and makes or removes keys.
  clipseerr's key is `SUBFEED_CLIPSEERR_KEY`, Homepage's
  `SUBFEED_HOMEPAGE_KEY`; both only read.
- **Tests**: the image's build runs them; a failing one fails `make up`.
- **Owns**: `$VOLUMES_ROOT/subfeed` (one SQLite database, mode 600).
- **Not its job**: deciding what a post is worth, or checking what it links
  to; downloading anything.
- **The `_subfeed` extension.** What JSON Feed has no field for is in each
  item's `_subfeed`: `feed`, `post_id`, `link_kind` (video, reddit_video,
  image, gallery, text, other), `domain` and `videos` (`[{"provider":
  "youtube", "id", "url"}]`, every video it carries, in order). A reader
  that knows nothing of it still has the post (`url`), what it links to
  (`external_url`) and its text.
- **A new source** (Lemmy, a forum) is one module in `subfeed/sources/`
  (a `Source`: its newest posts, its history); a new video provider is one
  line in `subfeed/links.py`.

### videodrop: link → video files

`compose/videodrop`, `videodrop.homelab.lan`. API reference: `/docs`.

- **Job**: getting videos, for whoever asks: a link, the options it wants
  (quality, codec, container, subtitles, playlist) and a destination;
  videodrop downloads it with yt-dlp, a few at once, and gives the file
  back. What a video is for (a music video, a clip) is its caller's
  business; songs are songdrop's.
- **Alone**: paste a link on its page; with no destination the files are
  held for a day (Settings, `hold_hours`; a job's own `hold_hours`), to
  download from the page.
- **A held file's life**: each file says when it goes (`expires_at`; the
  page counts down). Asked for after that, its `download_url` answers
  `410 Gone` with `{detail, again, removed_at}`; `POST again` (`…/retry`)
  downloads it once more as the same job, so the same link works again.
- **Plugs in through**: its API (`POST /api/v1/jobs`: asking twice for the
  same download while it runs answers the same job), three ways to follow a
  job (`GET /api/v1/jobs/{id}`; its live stream, `…/events`, Server-Sent
  Events; a `callback_url`, signed with `callback_secret`: `X-Videodrop-
  Signature: sha256=<HMAC of "<timestamp>.<body>">`), each file's
  `download_url` (Range requests work), its destinations (named folders
  under `media`, with a file-name template and options of their own;
  callers name one, never a path) and its events (Apprise, Discord,
  Webhook). clipseerr sends "get the video" to the `music-videos`
  destination.
- **Who may use it** (`accounts.py`, `api/auth.py`; the shared `accounts` and `web`): nobody without an
  account. People sign in on the page (scrypt-hashed passwords, an HttpOnly
  SameSite session cookie that ends after 30 days unused; wrong passwords
  slow down after five); apps send an API key (`X-Api-Key`, made in
  Settings → API keys, shown once, kept as a hash). Each has permissions: ask
  for downloads, save into destinations, see everyone's downloads (else
  only one's own), change settings, admin (people and keys). Downloads,
  their logs and the notifications name who asked. Only a key may ask for a
  callback. Before anyone exists, the page makes the first admin with a
  setup code from videodrop's log or `docker exec videodrop admin
  setup-code`; the same tool resets a password and makes
  keys. clipseerr's key is `VIDEODROP_CLIPSEERR_KEY`, Homepage's (read only,
  everyone's counts) `VIDEODROP_HOMEPAGE_KEY`.
- **Its way out** (`batlab_common/egress.py`): the downloads and link checks reach the
  internet only, through a proxy on the loopback that resolves each host
  itself, refuses any that points inside (a private, loopback, link-local
  or CGNAT address, even once), and connects to the address it checked, so
  a name can't point elsewhere by the time it is fetched. yt-dlp and ffmpeg
  go through it (ffmpeg over the web only), and the download process can't
  open any other connection. A link pointing home is refused when asked.
- **Tests**: the image's build runs them (`backend/tests`); a failing one
  fails `make up`.
- **Owns**: `$VOLUMES_ROOT/videodrop` (one SQLite database, mode 600, and
  the cookies) and `media/.videodrop` (downloads in progress, held files).
- **Not its job**: deciding what to download, or what a video is; songs.
- **How it runs downloads**: each one is a process of its own
  (`videodrop/runner.py`), so a download is stopped by stopping it and a
  crash ends only it. At most `max_parallel` at once, `per_site_parallel`
  from one site (every app here shares one address), the highest priority
  first. A failure that may pass (the network, the site busy) is tried again
  after 1, 5, 25… minutes; nothing starts while the media disk has less than
  `min_free_gb` free. A finished file is moved into place by a rename and
  never replaces one already there.
- **Before and after a download**: a link is checked as it is typed
  (`GET /api/v1/links/check`, without asking the site), then read for a
  preview (`POST /api/v1/probe`: the picture, title and source, the format the
  options pick with its bitrate and size, the subtitles there are, where the
  file would land; two at a time, kept 10 minutes). After a download each
  video is read by ffprobe (`checks.py`): a real video, of the length the
  site said, in the container its name says; one that fails isn't kept. A
  video file holds no program, so there is no virus scan; what can be wrong
  with one is that it is broken or cut short, which this sees.
- **The same file twice**: before downloading a video, its *fingerprint* is
  read from the site (`ytdlp.fingerprint`: site, id, length, and the exact
  streams the options pick: their ids, size, codecs, bitrate). When an
  earlier download had the same fingerprint, the same options and the same
  place, and its files are still there, they are reused and nothing is
  downloaded (`reused_from`); a re-encoded or trimmed video, another quality
  or a new stream changes the fingerprint. `again: true` downloads anyway; the
  preview says so first (`target.earlier`).
- **A part of a video** (`section: {start, end}`, seconds): cut by ffmpeg at
  the keyframes around it, without re-encoding (it may start a few seconds
  early); its times end the file's name (`… [1m30s-2m45s].mp4`). A section is
  a download of its own: it is in the dedupe and reuse keys.
- **File names** (`options.safe_path`): yt-dlp's look-alikes for characters a
  name can't hold (`：`, `⧸`…) become plain ones (`Song: Live` → `Song -
  Live`, `AC/DC` → `AC-DC`), the rest of `: | / \ " ? * < >`, control and
  invisible characters go. Letters of every language stay.
- **Seeing a file** (`previews.py`, `GET …/files/{n}/details`): ffprobe's
  reading of it (codecs, bitrates, tracks, chapters), a gallery (a picture
  at each chapter's start when there are 3 to 24, else 4 to 24 across it by
  its length),
  a storyboard (a picture every few seconds in one JPEG, for the player's
  seek bar) and its text subtitle tracks as WebVTT. ffmpeg decodes keyframes
  only (a 4K film's storyboard in about 15 s), right after the download, in
  the background; kept in `$VOLUMES_ROOT/videodrop/previews/<file identity>`
  and dropped once no job has that file. The page's player
  (`/jobs/{id}/play`) is the browser's `<video>` with these.
- **Lyrics** (`options.lyrics`, on for `music-videos`): a music video's
  synced lyrics from LRCLIB, as a subtitle track named Lyrics. The song is
  the job's `song` (clipseerr sends the one it matched), else guessed from
  the title. Subtitles are a video's business; which song a video is, its
  caller's.
- **Sites** (`sites.py`, `GET /api/v1/sites`, the page's Sites): the sites
  yt-dlp downloads from, one entry a site (its extractors' test links give
  its domain), in categories; known sites named and described by hand, the
  others sorted by the words in their names. Read in a process of its own
  once for each yt-dlp version and kept in `$VOLUMES_ROOT/videodrop/sites`;
  their icons are asked of DuckDuckGo's icon service by videodrop (never by
  the browser), a listed site's only, and kept there too.
- **Logs** (`journal.py`, `logs.py`, `redact.py`): each download has its
  own log (`GET /api/v1/jobs/{id}/log`, `…/log.txt`; on its page): who
  asked, each try, what yt-dlp said and what its process printed, the
  fingerprint and the reuse decision, each step and its time, where the
  files went, each callback, the held files' removal; its newest 3000
  lines, kept until the job is forgotten. videodrop's own log goes to the
  container and its newest 2000 lines to Settings → Log (`GET
  /api/v1/log`): every change asked through the API and every error, with
  the request's `X-Request-ID` (a 500 names it). Every line is cleaned
  first: passwords in addresses, tokens and signatures in queries, the
  `ip=` of signed video addresses, cookies and authorisation headers,
  notification addresses are hidden; a line can't forge another (line
  breaks, terminal escapes and control characters go).
- **Issues** (`GET /api/v1/issues`, the page's Issues, counted in the
  sidebar): what needs a person: yt-dlp behind PyPI's newest release for
  more than two days (the update chain stopped: Renovate, then the nightly
  updater, which rebuilds videodrop when `VERSION_YTDLP` changes), YouTube's
  bot check, failed downloads, the disk, cookies running out, notifications
  or callbacks failing.

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

### stanzarr: lyrics

`compose/stanzarr/app`, `stanzarr.homelab.lan`. Docs: `/docs` on the service
itself, and [its README](../compose/stanzarr/app/README.md).

- **Job**: every song's lyrics: tracks them, asks its sources for what is
  missing until they reach the quality cutoff (Settings, General), syncs
  plain lyrics by AI on a GPU worker, and hosts the lyrics editor. Its home
  page finds lyrics for any song by its name or a link (YouTube, Spotify,
  Deezer, Apple Music, SoundCloud, Bandcamp, Tidal, Amazon Music).
- **Alone**: point it at any music folder.
- **Plugs in through**: the library folders (it writes `.lrc` and `.txt` beside
  songs); `GET /api/v1/lyrics?artist=&title=&duration=`, any song's lyrics,
  the library's when the song is there, else the sources'; its events
  (`ai.synced`, `ai.failed`, `lyrics.found`); the workers' protocol
  (`/api/v1/worker/…`, a key per worker, Settings, Workers).
- **Others don't depend on it**: clipseerr and videodrop ask it first when
  given its address and a key (`STANZARR_CLIPSEERR_KEY`,
  `STANZARR_VIDEODROP_KEY`), and ask LRCLIB themselves when it doesn't answer
  in 6 s; after a failure they leave it alone 5 minutes
  (`batlab_common/lyrics.py`). videodrop asks it before the download starts:
  the download's process reaches only the VPN proxy.
- **Sources are add-ons** (`backend/stanzarr/sources/`): one `LyricsSource`
  module each, chosen, ordered and tested in Settings, Sources: LRCLIB,
  NetEase Cloud Music (no account), Musixmatch (one's own user token; its
  terms don't allow this use), Genius (words only, a free token),
  lyrics.ovh (words only, free). Every
  answer goes through a filter first (hidden and direction-turning
  characters, HTML, site credits and links, times past the song's end);
  what it refuses is kept aside in `quarantine/` with why, and said in
  Activity.
- **Who did what**: Activity says what runs now and logs every search, write,
  AI job and refusal with who asked (a person, an app's key, a worker).
- **Who may use it**: accounts and API keys as the other apps. Reading needs
  only to be someone; editing lyrics (`edit`), queuing AI syncs (`ai`) and
  changing settings need the permission. Homepage's key only reads
  (`STANZARR_HOMEPAGE_KEY`).
- **Owns**: `$VOLUMES_ROOT/stanzarr` (the database, the voices the workers
  separated, the backups of replaced lyrics); the `.lrc` and `.txt` files
  beside the songs.
- **Not its job**: whether a file may enter the library, its tags and place
  (musicguard).

### camwatch: camera health

`compose/frigate/camwatch`. No page.

- **Job**: says when a Frigate camera gives no picture, why, and when it is back.
- **Alone**: needs only Frigate's API.
- **Plugs in through**: its messages (Apprise, `cameras`).

### carwatch: who parked, who left

`compose/frigate/carwatch`. No page.

- **Job**: says when a car leaves its parking spot on the street camera, and
  when it is back, with a close-up, the street, which way it went and the
  clip.
- **Alone**: needs Frigate's events on MQTT, its API and go2rtc, and a zone per spot.
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
| subfeed | yes | yes | yes | yes | its notifications (Apprise here; Discord, Webhook) |
| videodrop | yes | yes | yes | yes | its notifications (Apprise here; Discord, Webhook), and each job's callback |
| musicguard | several, one module each | none | none | environment only | Apprise |
| Stanzarr | yes | own (private project) | no | in its page | none |
| camwatch | yes | none (needs none) | none | environment only | Apprise |
| carwatch | yes | none (needs none) | none | environment only | Apprise |
| tv-remote | yes | unversioned | no | environment only | none |

Next: installing batlab elsewhere (published images, a short `.env.example`).
