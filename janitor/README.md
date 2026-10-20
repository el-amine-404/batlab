# Janitor

On 2026-10-20 at 21:41 Netdata reported lab2's root SSD 98% full. The Samsung
TV had opened the live channel *Arryadia* through Jellyfin at 19:31 the evening
before and stopped watching at 19:56, but Jellyfin never closed the stream: it
kept recording the channel into `/cache/transcodes` for 26 hours, one 37.8 GB
file growing at about 400 KB/s, two hours from a full disk. Behind it sat old
images from earlier upgrades (46 GB) and build cache (17 GB) that nothing
used.

`scripts/janitor.py` has two jobs:

| Job | When | Does |
| --- | --- | --- |
| `livetv` | every 2 minutes (`batlab-livetv-guard.timer`) | a live TV buffer that grows for 10 minutes while no Jellyfin session plays it is closed through Jellyfin's API (`/LiveStreams/Close`); if it still grows, Jellyfin is restarted, which empties its transcode folder. The restart waits while someone plays anything, unless that disk is 95% full. One card to `#alerts` per stream |
| `clean` | nightly at 08:30 (`batlab-janitor.timer`), after the updater | removes old images, build cache above 10 GB, exited containers no compose stack owns (a day after they stopped), unused anonymous volumes. One card to `#updates` when it freed 1 GB or more |

## Which images stay

Per repository (`ollama/ollama`, `ghcr.io/immich-app/immich-server`, ...):

- every image a container uses, running or stopped;
- every image this host's compose stacks would run, up or down
  (`docker compose config --images`), the `IMAGE_`/`TAG_` pins in
  `compose/versions.env`, and `conf/keep-images.txt`, for images scripts start
  with `docker run` (subextract's OCR worker);
- anything newer than those: pulled with `make pull` and not deployed yet;
- **the newest older one: the previous version to roll back to.** Set the old
  tag back in `compose/versions.env` and `make up STACK=<stack>`; nothing is
  downloaded.

Everything older goes. An image no container, stack or pin uses goes a week
after it arrived (a `docker run` test, a removed service); an untagged one a
day after. A locally built app's previous build is the updater's
`local/batlab-rollback:<service>`, kept until the next rebuild moves the tag;
one rebuilt by hand with `make up` is untagged, so it goes after a day: roll
back with `git checkout` of the earlier code and `make up`. The updater no
longer removes the image it replaced: that is the previous version. The
clean waits for the updater's lock, so it never removes an image the updater
holds for a rollback, and a stack whose compose file does not render stops
the image part of the clean rather than guessing.

The disk image under Jellyfin's transcode folder is a second wall: a stream
the guard misses fills that 30 GB filesystem, not the root disk
(`compose/jellyfin/setup-storage.sh`).

## Live TV streams

Jellyfin has no API that lists the live streams it holds open, so the guard
reads its log (`$VOLUMES_ROOT/jellyfin/log`, the last three days): each
`Beginning "SharedHttpStream" stream to /cache/transcodes/<file>.ts` and the
`LiveStreamId` of the `Live stream opened` line after it. A buffer counts as
watched when a session's `PlayState.LiveStreamId` names it, or when any session
plays a live channel without naming its stream. Two pieces of evidence, then:
the file grew on every run for 10 minutes, and no session played it.

## Installation on the server

```bash
cd ~/batlab
make down STACK=jellyfin
sudo compose/jellyfin/setup-storage.sh
make up STACK=jellyfin
sudo install -m 644 janitor/systemd/*.service janitor/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-livetv-guard.timer batlab-janitor.timer
make restart STACK=apprise   # the new sender, "janitor on <host>"
```

The unit paths assume the repository is `/home/potato/batlab`. Jellyfin's API
key is `JELLYFIN_API_KEY` in `compose/.env`.

## Running by hand

```bash
janitor/scripts/janitor.py clean --dry-run     # list what would go, change nothing
janitor/scripts/janitor.py livetv --dry-run    # the buffers and what the guard thinks of them
python3 -m unittest discover -s janitor/scripts/tests
journalctl -u batlab-janitor.service -u batlab-livetv-guard.service -n 50 --no-pager
```
