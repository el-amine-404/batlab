# Live TV guard

Closes Jellyfin live TV streams that nobody is watching.

Jellyfin records each live channel it plays into its transcode folder until it
closes the stream. On 2026-10-19 the TV's Tizen app stopped playing *Arryadia*
after 25 minutes without closing it, and Jellyfin went on recording for 26
hours: one 37.8 GB file, and lab2's root disk at 98%.

`scripts/livetvguard.py`, every 2 minutes (`batlab-livetvguard.timer`):

| Sees | Does |
| --- | --- |
| a live TV buffer that grew on every run for 10 minutes while no Jellyfin session played it | closes the stream through Jellyfin's API (`POST /LiveStreams/Close`) and deletes the buffer |
| the buffer still grows after the close | restarts Jellyfin, which empties its transcode folder; waits while someone plays anything, unless that disk is 95% full |
| a session playing a live channel without naming its stream | nothing, for every buffer: it may be that one |

Jellyfin has no API that lists the live streams it holds open, so the guard
reads its log (`$VOLUMES_ROOT/jellyfin/log`, last three days): the buffer file,
`LiveStreamId`, channel and app of each stream opened.

Each stream closed posts one card to `#alerts`, as "live TV guard":

```
Closed a live TV stream nobody was watching
Channel        Arryadia (720p)
Opened         2026-10-19 19:31 UTC on Jellyfin for Tizen
Unwatched for  26 h 00 min
Recorded       37.8 GB, deleted
Done           closed through Jellyfin's API
```

Jellyfin's transcode folder is also a 30 GB filesystem of its own
(`compose/jellyfin/setup-storage.sh`): a stream the guard misses fills that,
not the root disk.

## Installation on the server

```bash
cd ~/batlab
make down STACK=jellyfin
sudo compose/jellyfin/setup-storage.sh
make up STACK=jellyfin
sudo install -m 644 livetvguard/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-livetvguard.timer
```

It needs `JELLYFIN_API_KEY` in `compose/.env`.

```bash
livetvguard/scripts/livetvguard.py --dry-run    # each buffer and what the guard thinks of it
python3 -m unittest discover -s livetvguard/scripts/tests
journalctl -u batlab-livetvguard.service -n 20 --no-pager
```
