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

- **Login**: Frigate's own users; `admin`'s password is kept in
  `compose/.env` as `FRIGATE_ADMIN_PASS` (Frigate does not read it). Change
  it under Settings > Users. Lost it: set `auth.reset_admin_password: true`
  and restart, the log prints a new one.
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

### What it does

| Camera | Detection | Kept by Frigate |
| --- | --- | --- |
| `camera_1`, Rooftop | people, 5 fps on the sub stream, YOLOv9 on the iGPU | person on the roof (alert): 10 s before to 10 s after, 30 days, with a snapshot; person seen elsewhere (detection): 7 days; any motion: 3 days |
| `camera_2`, Street | none | nothing, live view only |

Clips are copied from the main stream, never re-encoded. The DVR still keeps
everything; Frigate is the alarm and the index.

- **Roof zone**: the floor and the wall tops. A person counts when the bottom
  of their box is inside it, so people on the neighbours' roofs are only
  detections. Edit it in Settings > Masks / Zones on the real picture.
- **Motion masks**: the DVR's clock and camera-name overlays (they change
  every second), the sky and buildings beyond the walls, the neighbour's
  plants on the right. Motion under a mask never starts detection.
- **A child playing** is a person like any other: it makes an alert and a
  clip. Frigate cannot tell them apart without training (Settings >
  Classification can learn `child` / `adult` from the snapshots this camera
  collects; it adds a tag, it does not silence the alert).

### Storage: a 20 GB cap

Frigate has no size limit of its own: it deletes its oldest recordings, of
any kind, when the filesystem under `/media/frigate` runs low. That
filesystem is a 20 GB ext4 image, `$VOLUMES_ROOT/frigate/media.img`,
loop-mounted at `$VOLUMES_ROOT/frigate/media` from `/etc/fstab`
(`setup-storage.sh`). The container mounts `media/frigate`, which exists only
inside the image: if the image is not mounted, Frigate fails to start instead
of filling the root SSD.

Motion is kept 3 days because a windy week could fill the 20 GB and take the
oldest alert clips with it. Check System > Storage after a few days; raise
`record.motion.days`, or the image (`resize2fs` after growing the file), if
there is room. restic skips the image and the recordings but backs up the
snapshots (`frigate/clips`).

### Model

YOLOv9 (`yolov9-s-320.onnx`), built once on the server by
`compose/frigate/build-model.sh`, which runs the build from Frigate's docs
and writes `$VOLUMES_ROOT/frigate/config/models`. A rebuild is only needed for
another size. The build runs on the host network: lab2 resolves through
AdGuard on `127.0.0.1`, which containers on Docker's default bridge cannot
reach.

### Install

```bash
# compose/.env: FRIGATE_DVR_HOST, FRIGATE_RTSP_USER, FRIGATE_RTSP_PASSWORD
compose/frigate/setup.sh
compose/frigate/build-model.sh s 320
sudo compose/frigate/setup-storage.sh 20G
make up STACK=frigate
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
make restart STACK=homepage
```

### Later

- **Notifications**: Frigate's own web push needs HTTPS; ntfy is the simpler
  route.
- **`record.enabled`** must stay `true` on `camera_1`: with it `false` in the
  config, turning recording on from the UI does nothing. `camera_2` cannot
  record until its config says so.
- The data disk, once reliable (`hosts/lab2/README.md`), could hold a bigger
  image.
