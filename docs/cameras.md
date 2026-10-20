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
  OpenVINO, OpenCV and ffmpeg) watches the two spots, not the cars: any car
  in the Clio 4's or the Ford's spot counts. Every 2 s it looks at Frigate's
  latest frame. Each spot (its zone) is a patch of it; once nothing has moved
  there for 6 s (`CARWATCH_SETTLE`) it is compared with its last settled
  look. A car coming or going changes it by 19 to 81 grey levels (a person,
  a door or a shadow, under 10; `CARWATCH_CHANGE` 18) and replaces its
  texture: likeness to the old look 0.37 at most on 2026-10-20, against 0.87
  when the car beside the Clio left and its shadow and overhang moved off
  the Clio's spot (more than 0.5 is not a car, day or night). When it stays
  changed for 20 s (`CARWATCH_CONFIRM`), it is told only with proof that a
  car did it:
  - by day, the path of a car Frigate followed from the spot to 130 px away
    ("left") or from there into it ("arrived"). A car driving past starts
    and ends away from the spot, so it never counts. Frigate's tracking
    alone is not enough: from above it loses parked cars and finds them
    again, and hands their identity to cars driving past, so it "drives
    out" of a spot every few minutes (the cause of 460 false messages on
    2026-10-19). A change with no path waits 5 min (`CARWATCH_PENDING`) for
    one, or for the model to read the spot the other way on 90% of its
    looks for 60 s (`CARWATCH_WAIT`): a car that pulls out and waits in the
    street is followed only when it goes.
  - by day also, Frigate's YOLO model on 9 squares around the spots (a car
    covering 40% of a spot, `CARWATCH_FILL`). A look finding fewer than 4
    cars (`CARWATCH_MIN_CARS`; a row of them is always parked there) missed
    them and is ignored. The model overrules the rest (no "left" while it
    sees the car; two looks since the spot settled are enough, in the sun
    it misses most), stands in for a path Frigate missed, and tells a change
    nobody saw (the camera off, a white car at night) once it reads the
    spot the other way for 5 min (`CARWATCH_DRIFT`): "seen 08:05, not seen
    leaving", with the clip from then. Only its looks since the spot last
    changed count: on 2026-10-20 the looks from before each departure made a
    false "arrived" right after it, and a false "left" after each arrival.
  - at night (the camera in infrared, frames without colour) Frigate
    follows almost no car and the model sees none. A change is told when
    the spot's shape changed (headlights change a parked car's light, not
    its shape), a car-sized patch moved around it, and it got brighter
    (left: the cars there are darker than the paving in infrared) or darker
    (arrived).
  When the whole picture changes at once (the infrared switching at dusk
  and dawn, a cloud), that is the light, not a car. What each spot holds is
  kept in `${VOLUMES_ROOT}/frigate/carwatch/spots.json`: after a restart at
  night it is still known, and the night rules need it. Tested on
  2026-10-19's recordings, one frame every 2 s: the Ford's three departures
  and arrivals that were recorded whole are told, with the way, and nothing
  else, with every threshold moved up or down; on 2026-10-20's, its five
  recorded whole, and nothing for the Clio (the version before told the
  car beside it leaving as "Clio 4 spot · car left"). `test_carwatch.py`
  holds each fault seen live as a test (the command at its top). The night rules are checked on that
  night's frames (grain 1.6, a car against the paving 30 to 72, headlights
  on a parked car under 14) but no car came or went at night in the data.
  The message ("Ford spot · car left · went left", with how long it was
  taken or empty) comes once the car has settled: 8 s after by day, when
  Frigate followed the car and the model sees the spot taken (or empty)
  too (`CARWATCH_QUICK` 2 s after the 6 s still, instead of 20 s: 15 and 18 s
  after the Ford began to leave on 2026-10-19, against 33 and 36); 30 s
  after otherwise (at dusk the model sees too few cars, and one of Frigate's
  tracks can jump from a parked car to the street in a second), with a
  close-up from the 4K stream, the street and the clip from 10 s before it
  began, at most 60 s. carwatch records the clips itself: ffmpeg copies
  go2rtc's sub stream (H.264 960x576, about 600 kbit/s, which Discord plays
  as it is) into 5 s pieces in its /tmp, the last 15 min kept. Frigate
  records the street only around its detections, so its clips were often
  missing. A message's files must fit in 9 MB (Discord takes 10 MiB a
  message): a longer clip is encoded again at the bitrate that fits what
  the pictures leave. Apprise takes 16 MB of JSON
  (`APPRISE_UPLOAD_MAX_MEMORY_SIZE`); at its default 3 MB, every message
  with a clip over about 1.5 MB was refused ("JSON Payload provided is too
  large", 431) and lost: four of the six real ones on 2026-10-20. A message
  without a clip says nothing of one. A dark camera
  (Frigate's `camera_fps` 0) changes nothing. Its times follow
  `CARWATCH_TZ` in `compose/.env` (`Etc/GMT-1` on lab2: the zone data there
  puts Casablanca at UTC+0, the phones and the DVR at UTC+1).
  `docker logs carwatch` shows each spot's first state, each change it did
  not tell and why, and each message.
- **The Sandero** parks anywhere, so it has no zone: Frigate's object
  classifier tells it from the other cars. Each car Frigate follows is
  cropped from the frame and given up to 16 guesses; when 60% agree (at least
  3) above the threshold, the car gets the sub label `sandero`. carwatch
  posts "Sandero parked" when a car with that label stops after driving in,
  wherever it is, and "Sandero left" when it drives a car length away, with
  the same pictures, way and clip. Set up in Settings > Classification > new
  object model: name it `cars`, objects `car`, type sub label, classes
  `sandero` and `none` (exact names: `CARWATCH_FOLLOW` matches the class).
  The wizard offers 24 street car snapshots to sort; train, then keep
  sorting the guesses under Recent classifications, by day and by night,
  until it is right. Snapshots are kept 7 days for this.
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
