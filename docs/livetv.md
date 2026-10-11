# Live TV

Jellyfin's Live TV gets its channels from Dispatcharr, which runs in the `arr`
stack inside gluetun's network namespace: every stream is fetched through
ProtonVPN, so the stream hosts see the VPN's address, not ours.

```
iptv-org playlist ──> Dispatcharr (gluetun:9191) ──> Jellyfin Live TV ──> TV app
epgshare01 guide  ──┘       HDHomeRun tuner + XMLTV
```

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
and Jellyfin's tuner and guide URLs at `dispatcharr:9191` instead of
`gluetun:9191`. Then `make up STACK=arr SERVICE=dispatcharr`.
