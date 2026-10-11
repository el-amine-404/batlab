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

## How Jellyfin is set up

Dashboard, Live TV:

- Tuner: HDHomeRun, `http://gluetun:9191/hdhr`, 10 tuners, hardware
  transcoding allowed, stream sharing on.
- Guide: XMLTV, `http://gluetun:9191/output/epg`. Dispatcharr builds it on
  request in about a minute (33 MB).

Use `gluetun`, not its IP: the address in `home_server` is not pinned.

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
