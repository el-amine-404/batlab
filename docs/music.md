# Music

How a song gets from a request or a pasted link into Navidrome, and what checks
it on the way. Set up on 2026-10-11.

```
 request (Aurral) ─┬─ album ──▶ Lidarr ──▶ Prowlarr ──▶ qBittorrent ─┐
                   │                  └─▶ Soularr ──▶ slskd ────────┤ (both through gluetun)
                   └─ track, playlist ──▶ Aurral: slskd, else yt-dlp ─┤
 pasted link ──▶ MeTube (YouTube) / spotDL (Spotify) ──▶ inbox ───────┤
                                                                      ▼
                                     musicguard: check, strip, tag, lyrics, file
                                                                      ▼
                                                  Navidrome (three libraries)
```

## Where everything lives

| Path under `DATA_ROOT` | What | Who writes it | Navidrome sees it at |
| --- | --- | --- | --- |
| `media/music` | albums Lidarr manages, and the music that was here before | Lidarr | `/music` |
| `media/music-aurral` | single tracks, playlists and flows requested in Aurral | Aurral | `/data/media/music-aurral` |
| `media/music-extras` | songs from pasted links | musicguard | `/music-extras` |
| `inbox/music/{metube,spotdl}` | link downloads waiting to be checked | MeTube, spotDL | never |
| `soulseek/{incomplete,complete}` | slskd downloads before Lidarr or Aurral import them | slskd | never |
| `.quarantine/musicguard/<date>/` | everything musicguard refused, with a `.why.json` beside it | musicguard | never |

All three libraries use the same layout, Lidarr's:

```
Album Artist/Album (Year)/NN - Title.ext      01 - Airbag.flac
                                              2-05 - Title.flac   (disc 2 of a multi-disc release)
```

A single is an album of one track: `Stormy/KATANA (2026)/01 - KATANA.mp3`.
Synced lyrics sit beside a track as `NN - Title.lrc`, plain ones as `.txt`;
Navidrome reads both (its default `LyricsPriority`).

## musicguard

`compose/musicguard`, a container built here. It never deletes anything: a file
it refuses is moved to quarantine with the reason, and its permissions are
removed (`chmod 000`), as mediascan does.

What every file goes through, in order:

| Step | Refuses with |
| --- | --- |
| starts like a program, archive, script or web page (`MZ`, `ELF`, `PK`, `#!`, `<html`…) | `EXECUTABLE` |
| clamd (the host's, over its socket) recognises it; files above clamd's 25 MB stream limit are not sent, and say so | `MALWARE` |
| ffprobe cannot read it, or its content is not the format its name says (a polyglot or a disguise) | `NOT_AUDIO`, `MISMATCH` |
| no audio, a codec outside MP3/FLAC/AAC/ALAC/Opus/Vorbis/PCM, or shorter than 5 s | `NOT_AUDIO`, `UNSUPPORTED_CODEC`, `TOO_SHORT` |
| the whole audio stream is decoded; it must finish, with at most 20 decoder complaints | `CORRUPT` |
| only the audio stream is copied, unchanged, into a new container; WAV becomes FLAC losslessly | `REBUILD_FAILED` |
| tags: only an allow-list is written back, each value checked (below) | — |
| cover art is decoded and encoded again as a plain JPEG, at most 1200 px; covers from links are cropped square | — |
| a lossless or 256k+ file whose top band is empty is reported as `LOSSY_SOURCE` (made from a lossy file) | reported, never refused |
| lyrics from LRCLIB when none are beside the track; retried after 30 days | — |

Rebuilding is what makes the rest safe. Whatever a file carried besides the
audio (private frames, extra streams, chapters, bytes appended after the audio
or after a picture's end marker) is simply not copied.

The tag allow-list is title, artist(s), album, album artist, dates, track and
disc numbers, genre, composer, ISRC, barcode, media, compilation, sort names,
MusicBrainz and AcoustID IDs, and Aurral's `AURRAL_IDS` marker (dropping it
would make Aurral lose track of its own files). Every value is normalised;
control and text-direction characters are removed; URLs are cut out; a value
holding markup or template syntax (`<script`, `{{`, `${`, `javascript:`…) is
dropped. IDs must be real UUIDs, dates real dates, ISRCs valid. Comments,
descriptions, URLs, encoder notes and embedded lyrics never pass.

It runs in three ways:

- **Inbox**, every minute: a file is taken once its size has held still for two
  minutes. Link downloads carry a video's title and a channel's name, so
  "Artist - Song (Official Video)" is split and cleaned, "Artist - Topic"
  loses " - Topic" (also "VEVO", " Archive"), "Artist: Song" is split when
  the channel is the artist, and a song without an album becomes a single.
  When the channel names neither side of "X - Y" (a re-upload channel), the
  MusicBrainz recording search decides which side is the artist; if it knows
  neither order, the title is left whole rather than guessed. Filed into
  `music-extras`, then removed from the inbox with its thumbnail. A link from
  YouTube Music (`music.youtube.com`) or an "Artist - Topic" upload carries real
  album data and needs none of this.
- **Libraries**, every ten minutes: a new or changed file is checked where it
  is. In `music-aurral` and `music-extras` it is also rebuilt in place, under
  the name Aurral or musicguard gave it. In Lidarr's `music` it is **verified
  only** (`MUSICGUARD_VERIFY_ONLY`): Lidarr scrubs every download's tags and
  writes MusicBrainz's, and checks them on refresh, so rewriting its files
  would start a retag war, and would break the hardlink to the seeding torrent.
  Every check above still runs there; what a rebuild would have removed is
  reported instead (`SUSPICIOUS_TAG`, `EXTRA_STREAM`, `APPENDED_DATA` after a
  cover's end marker, `NOT_AN_IMAGE`). A file that must go is still
  quarantined. A codec musicguard cannot rebuild (WMA, APE) is reported and
  left.
- **Programs and archives in a library** (`.exe`, `.zip`, `.lnk`, `.html`…, or
  anything that starts like one, a "cover.jpg" that is an ELF binary included)
  are quarantined; other odd files are reported once and left.

When clamd is down, inbox files are **held**, not passed: they stay in the
inbox and `#download-issues` hears about it once. Findings go to Discord
`#download-issues` (`DISCORD_WEBHOOK_DOWNLOAD_ISSUES`) and every event to
`${VOLUMES_ROOT}/musicguard/reports/<month>.jsonl`.

```bash
python3 -m unittest discover -s compose/musicguard/tests     # needs ffmpeg and mutagen
docker logs -f musicguard
docker exec musicguard python /app/guard.py check /data/media/music-extras/<file>   # dry run
```

`LOSSY_SOURCE` is a measurement, not a verdict: it compares the level above
12, 16, 19 and 20.5 kHz on 30 s from the middle of the track, and reports a fall
of 25 dB or more between two bands that ends 50 dB below the whole signal. A
128k MP3 cuts at about 16-17 kHz, a 320k one at 20 kHz (`POSSIBLE_LOSSY_SOURCE`,
because some old masters stop there too). AAC and Opus keep the top band, so a
FLAC made from them is not caught.

## Navidrome

Navidrome has one library per folder (Settings > Libraries, as an admin):
`/music` (Lidarr's, the original library), `/music-extras` (pasted links) and
`/data/media/music-aurral`, which Aurral creates through the API once it is
connected. On 2026-10-11 Aurral sent its own path for that library, which
Navidrome refused (400) while the folder was mounted at `/music-aurral`, so the
mount now uses Aurral's path.
Give every user access to all three. Lyrics need no setting.

## Pasted links

- **MeTube** (`metube.homelab.lan`): any YouTube link (or one of the other sites
  yt-dlp supports). Choose **Audio**; the default format and "with cover" are
  right. A video download works too: musicguard keeps only its audio.
- **spotDL** (`spotdl.homelab.lan`): a Spotify track link. spotDL reads the
  song's details from Spotify without an account (its default SpotipyFree
  client since v4.5.0) and downloads the matching YouTube Music audio as Opus,
  without re-encoding. Its web UI takes single songs; for a whole Spotify
  playlist, import it in Aurral.

spotDL's image carries the yt-dlp it was released with, and YouTube answers an
old yt-dlp with 403 Forbidden: v4.5.2 (yt-dlp 2026.07.04) failed every download
on 2026-10-11 while MeTube's 2026.8.19 worked on the same link. So spotDL is
built locally (`compose/music-links/spotdl`) on the pinned release with the
yt-dlp from `VERSION_YTDLP` in `compose/versions.env`, which Renovate bumps
(the `PYPI_`/`VERSION_` manager in `renovate.json`). When spotDL starts failing
with 403, that pin is the first thing to raise.

Both are LAN-only (Caddy `localSubnets`) and have no login, like Seerr's
request page. YouTube audio is lossy (about 130-160 kbps Opus); for an album
you care about, request it in Aurral instead, which goes through Lidarr.

## Aurral

`aurral.homelab.lan`, the Seerr of the stack: search, request an album (sent to
Lidarr), discover, and import playlists from Spotify (owned or collaborative
playlists, through Lidarr's shared Spotify login, no developer app) or public
YouTube Music playlists. Tracks Aurral downloads itself land in `music-aurral`.

Settings that live in Aurral's database, not here. On first run, create the
admin account, then:

| Setting | Value |
| --- | --- |
| Lidarr | `http://lidarr:8686`, `LIDARR_API_KEY`; root folder `/data/media/music`, quality profile `Lossless or 320` |
| Downloads Folder | `/data/media/music-aurral` |
| slskd | `http://gluetun:5030`, `SLSKD_API_KEY` |
| yt-dlp | on (built in), lowest priority |
| Navidrome | `http://navidrome:4533`, an admin account |
| Remote path mappings | Navidrome `/music` → `/data/media/music` (Aurral's own folder has the same path in both, so it needs none) |

## Lidarr

Configured through its API on 2026-10-11:

- Root folder `/data/media/music` (new artists: monitor nothing until an album
  is requested); download clients qBittorrent (category `music`) and, through
  Soularr, slskd. Prowlarr syncs its indexers (only 1337x and Knaben passed
  Lidarr's test search for music; the other public sites returned nothing in
  the music categories).
- Metadata profile `Standard` with Album, EP and Single.
- Quality profile `Lossless or 320`: FLAC (and 24-bit) preferred, MP3-320 accepted,
  nothing below. Upgrades on until lossless.
- Naming on: artist folder `{Artist Name}`, tracks
  `{Album Title} ({Release Year})/{track:00} - {Track Title}`, multi-disc
  `{Album Title} ({Release Year})/{medium:0}-{track:00} - {Track Title}`.
- Tags written for every file from MusicBrainz, existing tags scrubbed, cover
  embedded; fingerprinting (AcoustID) for all files. Replaced files go to the
  recycle bin `recycle/lidarr` for 14 days.
- Discord: imports and upgrades to `#downloads`, health and failed imports to
  `#download-issues`, like Sonarr.
- Release profile rejecting names with `.exe`, `.scr`, `.lnk`, `.zip`, `.rar`,
  `password`, `codec`, `keygen`.

qBittorrent's excluded file names and Cleanuparr's malware blocker already
stop programs inside torrents (hosts/lab1/cleanuparr.md). Cleanuparr keeps its
settings in its own database, so Lidarr is added there by hand: Lidarr
instance `http://lidarr:8686` with `LIDARR_API_KEY`, Malware Blocker on for it,
and `music` added to the Download Cleaner's categories.

**Monitoring decides what gets downloaded.** A monitored album with missing
tracks is "wanted", and Lidarr (torrents) and Soularr (Soulseek) both fetch
wanted albums on their own, and upgrade monitored ones towards FLAC. Monitor an
album only when the whole album is wanted: request it in Aurral, or tick it in
Lidarr.

## Soulseek

slskd shares gluetun's network, like qBittorrent, and connects to Soulseek only
while gluetun reports the tunnel up. gluetun's single forwarded port belongs to
qBittorrent, so slskd has no inbound port; it downloads from every peer that
accepts connections, which is most of them. It shares the Lidarr library
read-only, because many users refuse peers who share nothing.

Homepage's slskd widget uses its own read-only key, `SLSKD_HOMEPAGE_KEY`,
which slskd learns from `${VOLUMES_ROOT}/slskd/slskd.yml` (the environment
holds only the admin key Soularr and Aurral use):

```yaml
web:
  authentication:
    api_keys:
      homepage:
        key: <SLSKD_HOMEPAGE_KEY>
        role: readonly
        cidr: 172.19.10.30/32
```

slskd's read-only role only keeps a key out of the admin settings: searches
and downloads are open to any key (`AuthPolicy.Any` in slskd's controllers).
So the key is also bound to Homepage's fixed address, 172.19.10.30.

The account is `SLSKD_SLSK_USERNAME`/`SLSKD_SLSK_PASSWORD` in `compose/.env`
(Soulseek has no sign-up: the first login creates it). The web UI login is
`SLSKD_WEB_USER`/`SLSKD_WEB_PASS`.

Soularr reads `${VOLUMES_ROOT}/soularr/config.ini`, made from
`compose/arr/conf/soularr/config.ini.example`. Departures from its defaults: no
region filter (it would skip most Moroccan and French releases), no MP3 below
320, and only audio, covers and `.lrc` are downloaded from a folder.

## The old library

The 125 MP3s that were in `media/music` (beets' `~ A ~/Artist/[Year] Album`
layout) went through musicguard's `migrate` on 2026-10-11 into the layout
above; the originals are in `recycle/music-before-migration-2026-10-11`. They
carried MusicBrainz IDs, so Lidarr adopted their 28 artists by itself when its
root folder was added.

Adoption first monitored every album with a file in it. Most were a few tracks
picked from an album, so Lidarr counted 15 albums as wanted, and before they
were unmonitored Soularr fetched four whole albums in FLAC (Future *High Off
Life*, Machine Gun Kelly *Hotel Diablo*, Cardi B *Invasion of Privacy*,
Ombladon *Cel mai prost din curtea școlii*), replacing the one MP3 of each
that was there; those four MP3s went to `recycle/lidarr`. The albums were kept.
All adopted albums are now unmonitored.

```bash
docker compose ... -f compose/musicguard/docker-compose.yml run --rm \
  -v <old library>:/migrate:ro musicguard migrate /migrate /data/media/music
```
