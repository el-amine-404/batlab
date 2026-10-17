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
(2026-10-10) lab2's AdGuard serves DHCP and gave the DVR `192.168.1.101` from
its `.101`-`.200` range. On 2026-10-14 lab2's AdGuard got a static lease again
(MAC `24:28:fd:12:90:11`, hostname `dvr`, `192.168.1.2`); the DVR took it at
16:44 while Frigate still dialed `.101`, and both cameras were dark for three
and a half hours without a word.

Two things keep that from happening again:

- **Frigate dials a name, not an address**: `FRIGATE_DVR_HOST=dvr.lan` in
  `compose/.env`. AdGuard answers `dvr.lan` from the lease, so if the lease
  moves, Frigate follows on its next reconnect. The name depends on that
  static lease (AdGuard > Settings > DHCP settings > Static leases): keep it.
  After changing `FRIGATE_DVR_HOST`, `make up STACK=frigate` (a restart keeps
  the old value).
- **camwatch** (`compose/frigate/camwatch`) reads Frigate's stats every
  minute. A camera without frames for 5 minutes, or a Frigate that does not
  answer, goes to Apprise's `cameras` channel (the cameras Discord channel
  here) with the reason (the name does not resolve; the DVR does not answer
  at its address; or it answers but Frigate gets no frames), again every 6
  hours while it lasts, and a message when it is back. `docker logs camwatch` shows the same.

`cam-home --find` (or an RTSP scan of port 554) tells the DVR's address.

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
| `camera_2`, Street | cars and people, 5 fps on the sub stream | every car and person Frigate sees (detections, no alerts): clips of them moving, 1 day; snapshots, 7 days (the car classifier learns from them) |

Clips are copied from the main stream, never re-encoded. The DVR still keeps
everything; Frigate is the alarm and the index.

- **Roof zone**: the floor and the wall tops. A person counts when the bottom
  of their box is inside it, so people on the neighbours' roofs are only
  detections. Edit it in Settings > Masks / Zones on the real picture.
- **Motion masks**: the DVR's clock and camera-name overlays (they change
  every second), the sky and buildings beyond the walls, the neighbour's
  plants on the right. Motion under a mask never starts detection.
- **Parking spots** (`camera_2`): zones `spot_1` (the Clio 4) and `spot_2`
  (the Ford) over the places they park, wide enough for a car parked a
  little off. A car is in a spot when the bottom middle of its box is
  inside. Move them in Settings > Masks / Zones if a car is parked where its
  zone does not reach.
- **carwatch** (`compose/frigate/carwatch`, in Frigate's image for its
  OpenCV and ffmpeg) reads Frigate's events on the broker and posts to
  Apprise's `cameras` channel the moment Frigate sees a car arrive or leave:
  "Clio 4 left" or "Clio 4 is back", how long it was parked or away, a
  close-up of the car cut from the 4K stream and Frigate's frame of the
  street. About 45 s later a second message says which way it went ("went
  left/right", the side of the picture it was last seen on; "came from" for
  one arriving) with the clip, 5 s before to 15 s after, re-encoded to
  1280 px H.264 for Discord. A car that drives into an empty spot has
  arrived; one parked there that drives out has left; either must move a
  car length (`CARWATCH_MOVE`), so a box that jumps on a parked car says
  nothing. A car that pulls out and parks again is told as both. Frigate
  looks for cars only where something moves, so after a restart a parked
  car can stay unseen until someone walks past it: such a car is not
  "back", and it is told when it leaves. Another car parked in a spot is
  taken for its usual car. `docker logs carwatch` shows each move.
- **Someone at the cars**: the street camera also detects people. The zone
  `near_cars` (around both spots) counts a person only after 30 s there
  (`loitering_time`); carwatch then posts "Someone at the cars" with a
  close-up and the street, and the clip from when they came. At most one in
  10 minutes (`CARWATCH_PEOPLE_QUIET`), so loading the boot is told once.
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

### Discord alerts

Each alert (a person in the `roof` zone) is posted to `DISCORD_WEBHOOK_CAMERAS`
with its snapshot and a link to the clip, which opens only at home. Frigate
publishes to `mosquitto`, a broker on the stack's internal `mqtt` network that
no other container joins; `frigate-notify` (`compose/frigate/conf/notify.yml`)
subscribes and posts. One message per alert, when it starts; an alert that
starts while `frigate-notify` is restarting is not sent.

To test the whole chain, create a manual alert (it says "person: test"):

```bash
docker exec frigate curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"sub_label":"test","duration":10}' \
  http://127.0.0.1:5000/api/events/camera_1/person/create
```

### Install

```bash
# compose/.env: FRIGATE_DVR_HOST, FRIGATE_RTSP_USER, FRIGATE_RTSP_PASSWORD,
# DISCORD_WEBHOOK_CAMERAS (frigate-notify's alerts, and camwatch's through
# Apprise's "cameras" channel: compose/apprise)
compose/frigate/setup.sh
compose/frigate/build-model.sh s 320
sudo compose/frigate/setup-storage.sh 20G
make up STACK=frigate
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
make restart STACK=homepage
```

### Later

- **`record.enabled`** must stay `true` on `camera_1`: with it `false` in the
  config, turning recording on from the UI does nothing; the same goes for
  `camera_2`.
- The data disk, once reliable (`hosts/lab2/README.md`), could hold a bigger
  image.
