# Docker prune

Removes old Docker images, build cache and stray containers every night,
keeping one previous version of each image to roll back to.

On 2026-10-20 lab2's root disk reached 98%: besides a live TV stream left
open (`livetvguard/`), 46 GB were images from earlier upgrades and 17 GB build
cache nothing used.

`scripts/dockerprune.py`, nightly at 08:30 (`batlab-dockerprune.timer`), after
the updater; it waits for the updater's lock, so it never removes an image the
updater holds for a rollback.

| What | Kept | Removed |
| --- | --- | --- |
| images, per repository | what a container uses, running or stopped; what this host's compose stacks would run, up or down; the `IMAGE_`/`TAG_` pins of `compose/versions.env`; `conf/keep-images.txt`; anything newer (pulled, not deployed yet); **the newest older one, the previous version** | older versions |
| images nothing uses (a `docker run` test, a removed service) | for 7 days after they arrived | after that |
| untagged images | for a day | after that |
| build cache | 10 GB, most recently used first | the rest |
| exited containers no compose stack owns | for a day after they stopped | after that |
| anonymous volumes | in use | unused |

To roll back, put the previous tag back in `compose/versions.env` and
`make up STACK=<stack>`: nothing is downloaded. A locally built app's previous
build is the updater's `local/batlab-rollback:<service>`; one rebuilt by hand
with `make up` leaves its old build untagged, gone a day later, so roll that
back with `git checkout` of the earlier code. A stack whose compose file does
not render stops the image part rather than guessing what it needs.

When it freed 1 GB or more it posts to `#updates`, as "docker prune on lab2":

```
Freed 17.7 GB of old Docker images and build cache
Root disk             75% used, 59.0 GB free
Images removed (8)    songrec-test:latest  2.5 GB
                      linuxserver/ffmpeg:latest  1.1 GB
                      ...
Build cache           13.6 GB
```

## Installation on the server

```bash
sudo install -m 644 dockerprune/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-dockerprune.timer
```

```bash
dockerprune/scripts/dockerprune.py --dry-run    # what would go, and why
python3 -m unittest discover -s dockerprune/scripts/tests
journalctl -u batlab-dockerprune.service -n 30 --no-pager
```
