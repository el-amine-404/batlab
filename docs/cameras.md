# cameras

The home cameras hang off a Hikvision DVR (`HIK Media Server V4.25.001`, MAC
`24:28:fd:12:90:11`). It records around the clock on its own disk. Frigate
(`compose/frigate`, on lab2) sits in front of it for a browser view at
http://frigate.homelab.lan.

| DVR channel | Camera | Main stream (`x01`) | Sub stream (`x02`) |
| --- | --- | --- | --- |
| 1 | Camera 1 | H.265 2960x1664 | H.264 960x576, 12 fps |
| 2 | Camera 2 | H.265 3840x2160 | H.264 960x576, 12 fps |
| 3, 4 | none plugged in ("NO VIDEO") | | |

RTSP: `rtsp://<user>:<password>@<dvr>:554/Streaming/Channels/<channel><01|02>`.
From a laptop, `cam-home` (dotfiles) opens them in mpv; `cam-home --sub` uses
the sub streams, `cam-home --find` prints the DVR's address.

## The DVR's address

It used to be `.2`, a static lease on lab1's AdGuard. Since lab1 went off
(2026-10-10) lab2's AdGuard serves DHCP, has no such lease, and gave the DVR
`192.168.1.101` from its `.101`-`.200` range. Until a lease pins it again, the
address can move: Frigate then shows the cameras as offline. Fix it in one
place, `FRIGATE_DVR_HOST` in `compose/.env`, then
`make restart STACK=frigate`. `cam-home --find` (or an RTSP scan of port 554)
tells the new address.

## Frigate

- **Login**: Frigate's own users. The first start prints an `admin` password
  in the log (`docker logs frigate 2>&1 | grep -A3 -i password`); change it
  under Settings > Users. Lost it: set `auth.reset_admin_password: true` and
  restart, the log prints a new one.
- **Config**: the live file is `$VOLUMES_ROOT/frigate/config/config.yml`,
  edited from Frigate's Settings UI, which also migrates it on upgrades.
  `compose/frigate/config.yml` is only the seed `compose/frigate/setup.sh`
  copies when there is none. The DVR's address and login stay in
  `compose/.env` (`FRIGATE_DVR_HOST`, `FRIGATE_RTSP_USER`,
  `FRIGATE_RTSP_PASSWORD`, URL-encoded) and reach the config as
  `{FRIGATE_*}` placeholders, so the config holds no secret.
- **Streams**: Frigate's go2rtc opens each DVR stream once and shares it.
  The Live grid plays the H.264 sub streams, which every browser plays; a
  camera's own page has a dropdown for the main stream, H.265, which plays
  in Chrome, Edge and Safari but not Firefox.
- **Ports**: 8971 (login) only through Caddy; 5000 (no login) only on the
  `home_server` network, for the Homepage widget. Nothing is published on
  the host.

### Install

```bash
# compose/.env: FRIGATE_DVR_HOST, FRIGATE_RTSP_USER, FRIGATE_RTSP_PASSWORD
compose/frigate/setup.sh
make up STACK=frigate
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
make restart STACK=homepage
```

### Step 1 (now): live view

Detection, recording and snapshots are off, so Frigate only decodes the two
sub streams at 5 fps on the iGPU (VAAPI).

### Step 2 (later): detection and alerts

Turn on person and car detection (the OpenVINO detector on the UHD 620 is
already configured, idle), record only
events from the main stream, and push alerts. Before that:

- **Disk**: recordings must not land on the 256 GB root SSD. Point
  `/media/frigate` at the data disk once it is reliable again
  (`hosts/lab2/README.md`) and set retention in days.
- **`record.enabled`** must be `true` in the config: with it `false`, turning
  recording on from the UI does nothing.
- Raise `FRIGATE_CPUS` / `FRIGATE_MEM_LIMIT` in `hosts/lab2/compose.env` and
  check Netdata for a week.
