# Live TV

Dispatcharr runs in the `arr` stack inside gluetun's network namespace and
fetches every stream through ProtonVPN, so the stream hosts see the VPN's
address, not ours. Two players use it:

```
iptv-org playlist ──> Dispatcharr (gluetun:9191) ──> NodeCast TV ──> browsers (PCs, phones)
epgshare01 guide  ──┘   Xtream Codes API      └───> Jellyfin Live TV (HDHomeRun + XMLTV)
```

Jellyfin's Live TV pages do not cope with 11k channels: its Channels page took
6 minutes in Firefox on 2026-10-11, while Dispatcharr's own list of the same
channels is instant. NodeCast is the player for PCs and phones.

- Web UI: http://dispatcharr.homelab.lan, user and password in `compose/.env`
  (`DISPATCHARR_USER`, `DISPATCHARR_PASS`).
- Swagger: http://dispatcharr.homelab.lan/swagger/

## Sources

| What | URL | Refresh |
| --- | --- | --- |
| Channels | `https://iptv-org.github.io/iptv/index.m3u`, everything (iptv-org has published no NSFW channels since January 2024) | every 24 h |
| Guide | `https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz` (~200 MB gz, ~28k channels) | every 24 h |

The playlist header's own guide (`worker-9dd4.onrender.com`) covered two
channels on 2026-10-11, so it is not used.

## How Dispatcharr is set up

Set once through the API, held in its database under
`${VOLUMES_ROOT}/dispatcharr`:

- M3U account `iptv-org`, Chrome user agent, stale streams dropped after 3 days.
- Every group has **auto channel sync** on, in its own number block sized for
  the group plus 30% (General 1-3480, Undefined 3481-5440, News 5441-6670, ...,
  up to 17670). New and removed iptv-org channels follow on each refresh.
- Proxy settings: **channel shutdown delay 10 s** (was 0). NodeCast probes a
  stream, closes it and opens it again to play; with no delay the second
  request found the channel tearing down and got 503 or a 188-byte
  `Error: Channel is stopping` packet.
- Default stream profile **Streamlink** (`best`). The stock FFmpeg profile
  passes `-map 0`, which pulls every HLS variant of a channel at once: five
  renditions of one channel through the VPN for one picture.
- Guide matching: Dispatcharr's fuzzy auto-match assigned 6776 channels, about
  half of them wrong (`Al Najah News` got `Al Qahera News`). Kept only the 3406
  whose names match after removing resolution and tags, or nearly match with
  the same country code; the others have no guide. Auto-match runs only when
  started by hand (Channels, Auto-Match), so the cleanup holds; running it
  again brings the wrong matches back.
- Changing guide assignments queues one parse task per channel, and each one
  reads the whole extracted guide (1.9 GB): 8 channels in 10 minutes, 4134
  queued after the cleanup, about 90 hours at two full cores. The source
  refresh parses every mapped channel in one pass instead (2 minutes for
  2731). After a bulk change, drop the queued tasks and refresh the source:

  ```bash
  docker exec dispatcharr redis-cli EVAL "local n=0 for _,v in ipairs(redis.call('lrange',KEYS[1],0,-1)) do if string.find(v,'parse_programs_for_tvg_id',1,true) then n=n+redis.call('lrem',KEYS[1],1,v) end end return n" 1 celery
  docker restart dispatcharr   # stops the ones already running
  ```

  then EPG, `epgshare01 all`, Refresh.

## NodeCast TV

http://nodecast.homelab.lan, stack `compose/nodecast`. Login `admin` and
`NODECAST_PASS` from `compose/.env`, asked twice: by Caddy, then by the app.

- Source: Xtream Codes, `http://gluetun:9191`, Dispatcharr user `nodecast`
  (`DISPATCHARR_XC_USER`/`_PASS`). That user is a Streamer (level 0, plays
  only); its Xtream password is the custom property `xc_password`. It sees all
  183 groups and 11172 channels; NodeCast syncs them and the guide (82k
  programmes) in under a minute, every 24 h.
- Settings, Transcoding: **Force Backend Proxy** on, stream format **TS**,
  encoder auto (VAAPI on lab2, `h264_vaapi`). Dispatcharr sends MPEG-TS, which
  browsers cannot play: NodeCast probes each stream and remuxes it to MP4
  (H.264/AAC, the usual case) or transcodes on the GPU.
- The settings live on the server, but the player falls back to its defaults
  (`m3u8`, no proxy) when it cannot read them, and saves the whole set on the
  next change, the volume included: that happened during a login loop. If
  channels stop starting, check Settings first.
- The image is built locally (`compose/nodecast/Dockerfile`) with two fixes to
  2.1.1; each `grep` fails the build if a new version changes the line:
  - Guide channels were saved under the same key as live channels. Dispatcharr's
    guide ids are channel numbers and its stream ids channel ids, so every
    channel name in the list pointed at another stream ("2M Monde +1", number
    13, played stream 13, a dead channel). After the fix, the old rows went
    with `DELETE FROM playlist_items WHERE type='epg_channel'` and a sync.
  - The remux, used for H.264/AAC in MPEG-TS (most channels), lacked
    `aac_adtstoasc`; ffmpeg stopped at the first audio packet.
- A channel dead at the source fails the probe; the player then shows
  "Transcoding (Video)" and spins forever. Try another channel.
- `JWT_SECRET` must be set: without it the image signs logins with a key
  published in its source.
- **Basic auth at Caddy is not optional.** NodeCast checks its login only in
  the browser: on 2.1.1, `/api/sources` (with the Dispatcharr password),
  `/api/settings` and `/api/proxy/stream?url=` (fetches any URL from inside
  `home_server`, gluetun's control server included) all answered 200 without
  one. Only user management checks the token. The Caddy password hash is
  `NODECAST_PASS_HASH`, single-quoted in `compose/.env`. After login the app
  sends its own token in `Authorization`, which replaces the browser's basic
  credentials; Caddy lets those requests through only if NodeCast's
  `/api/auth/me` accepts the token (`forward_auth`), else the app loops back
  to its login page.
- The browser gets the Dispatcharr stream URL, `nodecast` user and Xtream
  password included; that user can only play.
- First start: the first `POST /api/auth/setup` creates the admin, with no
  login, until a user exists. Create it right after the container starts.
- Pinned to `2.1.1`. The project's last commit was 2026-03-02; if it breaks
  or stays abandoned, IPTVnator (very active, but configured per browser) is
  the fallback.

## How Jellyfin is set up

Dashboard, Live TV:

- Tuner: HDHomeRun, `http://gluetun:9191/hdhr`, 10 tuners, hardware
  transcoding allowed, stream sharing on.
- Guide: XMLTV, `http://gluetun:9191/output/epg?days=1`. Jellyfin reads the
  whole file once per channel, and the full guide is 179 MB; one day is 62 MB.
  With 11k channels Jellyfin's automatic guide length is one day anyway.

Use `gluetun`, not its IP: the address in `home_server` is not pinned.

Jellyfin's Refresh Guide task goes through all 11k channels, reads the guide
file for each and fetches each logo through Dispatcharr, many from hosts that
no longer answer: with the full guide it ran at 20% an hour. Cancelling it
waits for the logo downloads in flight, several minutes. Jellyfin keeps the XMLTV file in `/cache/xmltv`; after changing the
guide in Dispatcharr, delete it before refreshing or Jellyfin reuses the old one.

Jellyfin records each live channel it plays into a file in
`/cache/transcodes` until it closes the stream, and a client that stops
without closing it leaves it recording: on 2026-10-19 the TV's Tizen app left
*Arryadia* running for 26 hours, 37.8 GB, and the root disk reached 98%. The
live TV guard (`livetvguard/`) closes a stream that grows for 10 minutes with no
one playing it, and the transcode folder is a 30 GB filesystem of its own
(`compose/jellyfin/setup-storage.sh`).

## What goes through the VPN

Streams, the playlist, the guide download and channel logos (Dispatcharr's
logo cache): Dispatcharr's address on 2026-10-11 was ProtonVPN's in Lisbon,
lab2's own was not. One thing does not: Jellyfin pre-caches **programme
artwork** itself, from the hosts the guide names (`dtil.tmsimg.com`,
`jiotv.catchup.cdn.jio.com`, `metadata-static.plex.tv`, ...; half the
programmes carry one). Those hosts see our address fetching TV-guide pictures,
no stream. Dispatcharr has no switch to leave them out of its XMLTV.

## What to expect

On 2026-10-11, 19 of 29 sampled channels played (Al Jazeera, DW, 2M Monde +1,
regional channels). The rest failed at the source, the same from our own IP
and through the VPN: 403 and 404 answers, `[Not 24/7]` channels off air, hosts
that no longer answer. iptv-org lists public streams; it does not check that
they work.

Some channels are `[Geo-blocked]`; whether they play depends on the country of
the VPN server (`GLUETUN_SERVER_COUNTRIES`).

## Rebuilding from an empty volume

The admin login comes from `compose/.env`, created inside the container (its
environment holds the Django secret key, hence `bash -lc`):

```bash
. <(grep -E '^DISPATCHARR_(USER|PASS)=' compose/.env)
docker exec -e DJANGO_SUPERUSER_PASSWORD="$DISPATCHARR_PASS" dispatcharr \
  bash -lc "cd /app && /dispatcharrpy/bin/python manage.py createsuperuser \
  --noinput --username $DISPATCHARR_USER --email admin@homelab.lan"
```

Then redo the settings above in the web UI: the M3U account with auto sync on
every group, the EPG source, Streamlink as default profile (Settings, Stream
Settings), and the guide matching with a check of what it matched. The token
endpoint (`/api/accounts/token/`) is rate limited: log in once and reuse the
access token.

## Turning the VPN off

If live TV stutters or the VPN slows downloads, take Dispatcharr out of
gluetun's namespace: in `compose/arr/docker-compose.yml` replace
`network_mode: service:gluetun` and the `depends_on` block with
`networks: [home_server]`, then point `dispatcharr.caddy`, the homepage tile
Jellyfin's tuner and guide URLs and NodeCast's source (Settings, Content
Sources) at `dispatcharr:9191` instead of `gluetun:9191`. Then `make up STACK=arr SERVICE=dispatcharr`.
