# Music

How a song gets from a request or a pasted link into Navidrome, and what checks
it on the way. Set up on 2026-10-11.

```
 request (Aurral) ─┬─ album ──▶ Lidarr ──▶ Prowlarr ──▶ qBittorrent ─┐
                   │                  └─▶ Soularr ──▶ slskd ────────┤ (both through gluetun)
                   └─ track, playlist ──▶ Aurral: slskd, else yt-dlp ─┤
 pasted link ──▶ songdrop / MeTube / spotDL ─────────▶ inbox ───────┤
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
| `inbox/music/{songdrop,metube,spotdl}` | link downloads waiting to be checked | songdrop, MeTube, spotDL | never |
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
| lyrics from LRCLIB when none are beside the track; retried after 30 days, and so is a track with only plain lyrics (`.txt`), since players scroll only synced ones (`.lrc`, written beside the `.txt`, which is kept). Every LRCLIB entry with the same title, an artist in common and the same length (±3 s) is weighed, synced first: LRCLIB held KATANA ten times, under "Manal", "Manal, Stormy", "Manal & Stormy"…, and only the "Manal" one had timings. `guard.py lyrics` asks again now for every track without synced lyrics | — |

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
- **Songs from links get Apple Music's album data.** Shazam's answer names the
  song's Apple Music track; Apple's public lookup API (no account) gives the
  album without Apple's " - EP"/" - Single" suffix, the release type, track
  and disc numbers, the release date, the genre and a 1200 px cover, which
  replaces the video thumbnail. So a song files as
  `Stormy/OMEGA (2025)/05 - PIRATE.mp3`, not as a single of its own. When
  Shazam hears a different song than the link said, every old tag goes,
  genre included.
- **A song already in a library is not filed twice**: same title, an artist
  in common and the same length within 5 s (so a remix or live take still
  files) and the new download is dropped (`duplicate ALREADY_IN_LIBRARY`).
- **Songs from links are upgraded** (`app/upgrade.py`), after each library
  pass: musicguard looks the song up on MusicBrainz (by ISRC, else title and
  artist), picks the release it belongs to (the full album, else the EP, else
  the single; never a compilation, live or remix release), and has Lidarr
  monitor and search that album, adding the artist with nothing else
  monitored if it is new. Once Lidarr's file for that recording is in `music`
  and musicguard has verified it, the YouTube copy, its lyrics and its cover
  are removed from `music-extras`. A song MusicBrainz does not know (much
  Moroccan music, e.g. Stormy's "PIRATE", is only on streaming services) is
  reported once as `NOT_IN_MUSICBRAINZ`, keeps its YouTube copy, and is looked
  up again weekly: Lidarr's catalogue is MusicBrainz, so it cannot fetch it.
  **Singles in Lidarr's library** (files tagged `musicbrainz_albumtype`
  `single`) get the same: the album their recording is on (MusicBrainz by
  recording ID, then title and artist) is monitored and searched, and once its
  copy of the song is in the library and verified, the single is deleted
  through Lidarr (to its recycle bin) and unmonitored, so the song is there
  once. A single on no album (many Moroccan releases) is looked at again
  monthly. On 2026-10-11 this requested *i am > i was* (21 Savage), *SMITHEREENS*
  (Joji) and *The Dichotomy* (David Kushner); the other eight singles are on
  no album.
  `docker exec musicguard python /app/guard.py upgrade` runs one pass
  (`MUSICGUARD_DRY_RUN=1` to only list what it would request).
- **Programs and archives in a library** (`.exe`, `.zip`, `.lnk`, `.html`…, or
  anything that starts like one, a "cover.jpg" that is an ELF binary included)
  are quarantined; other odd files are reported once and left.

**Identification (Shazam).** musicguard asks Shazam what a recording is,
through SongRec (an open-source Shazam client, compiled into the image): only
a fingerprint leaves the server, never audio. Shazam knows far more Moroccan
and streaming-only music than MusicBrainz's AcoustID.

- A pasted link takes Shazam's artist, title and album over the video's title
  whenever they disagree, and loses the IDs it came with.
- A file new to a library that Shazam hears as a different song, with neither
  title nor artist in common, is quarantined as `WRONG_SONG`; a monitored album
  then makes Lidarr fetch another copy. A remix, a feature or a translated title
  shares one of them and is not called wrong. No match is never a verdict.
  Before anything moves, the file is compared with **the real song**
  (`app/reference.py`): Apple Music's 30 s preview of the song its tags name,
  Chromaprint fingerprints of both (AcoustID's technique), the preview slid
  along the file. 0.85 or more of the fingerprint bits alike: it is that song,
  kept (`SHAZAM_OVERRULED`). Below 0.70: it is not, `WRONG_SONG`. With no
  preview (the song is not on Apple), Shazam decides only when its answer is
  a real release, with an ISRC or an Apple Music ID; an entry with neither
  moves nothing (`SHAZAM_UNSURE`, reported). Shazam's catalogue has re-uploads
  of hits under other names: on 2026-10-11 it heard 21 Savage's "4L" as
  "Thedjnino - Just TEN" (no ISRC, not on Apple) at every point of the song,
  and the real track was quarantined; compared with Apple's "4L" it scored
  0.99, and with another song 0.58.
- `guard.py audit` compares every track with Shazam and only reports; the
  files it saw count as checked, so it is safe on an old library.

On 2026-10-11 Shazam named the two files beets had mis-tagged: "Georgie Fame -
Stormy" was Stormy - PIRATE, "Ombladon - Made in Romania" was Ionuț Cercel -
Made In Romania.

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
FLAC made from them is not caught. `POSSIBLE_LOSSY_SOURCE` applies to lossless
files only: a cut at 20 kHz is how every genuine MP3 320 is made, and until
2026-10-11 every 320k album (Post Malone, UTOPIA) was reported for it. An MP3
cut below 19 kHz is still `LOSSY_SOURCE`: a 320k file made from a 128k one.

## Navidrome

Navidrome has one library per folder (Settings > Libraries, as an admin):
`/music` (Lidarr's, the original library), `/music-extras` (pasted links) and
`/data/media/music-aurral`, which Aurral creates through the API once it is
connected. On 2026-10-11 Aurral sent its own path for that library, which
Navidrome refused (400) while the folder was mounted at `/music-aurral`, so the
mount now uses Aurral's path.
Give every user access to all three. Lyrics need no setting.

**Navidrome never scans on its own** (`ND_SCANNER_ENABLED=false`). With its
scanner on, a song Lidarr imported was playable minutes before musicguard had
checked it. musicguard asks for a scan (Subsonic `startScan`, account
`NAVIDROME_MUSICGUARD_USER`, an admin because only admins may scan) after a
library pass that changed something, and only when no audio file in any
library is unchecked; a held or failed file keeps new music out of Navidrome
until it is resolved, rather than letting it in unchecked. New music shows up
2 to 5 minutes after it lands. Every other way to Navidrome goes through Caddy
(`compose/caddy/conf/navidrome.caddy`), which answers `startScan` itself with
"ok": an admin's rescan button and Aurral, which scans after its own downloads,
cannot index unchecked files. Aurral needs an admin account (as a non-admin its
playlist library setup fails with 403), so it has its own, `aurral`, and
reaches Navidrome at `http://caddy:4534`, a Caddy listener that is not
published on the host. musicguard alone calls `navidrome:4533` directly.

After each scan musicguard posts the albums that just became playable to
`#downloads` ("Ready to play in Navidrome", `MUSICGUARD_READY_WEBHOOK`).
Lidarr's own Discord connection for `#downloads` has *On Release Import* and
*On Upgrade* off since 2026-10-11: it announced music minutes before anyone
could play it.

A scan forgets files that are gone
(`ND_SCANNER_PURGEMISSING=always`): an upgrade that replaces an MP3 with a FLAC
or a Lidarr rename would otherwise leave the old album greyed out next to the
new one. Navidrome matches moved files before purging, so plays and stars
follow a move. The purge has no guard against an empty mount of its own; the
data guard (watchdog/README.md) is that guard, stopping Navidrome while the disk
is unmounted.

## Stanzarr (lyrics)

Since 2026-10-12 lyrics are managed by **Stanzarr** (`stanzarr.homelab.lan`,
`compose/stanzarr`, its own project, kept private for now: built from the source copy in `STANZARR_SOURCE`, `~/stanzarr` on lab2). It
tracks every song's lyrics files (format, language, origin), fetches missing
ones from LRCLIB (synced first, again every 30 days for songs without lyrics
synced by a person), writes `.lrc` / `.txt` beside the songs, and hosts the
lyrics editor. AI sync runs on a GPU worker: the laptop, with
`~/ai-lyrics/dvenv/bin/stanzarr-worker --server http://stanzarr.homelab.lan
--key <STANZARR_WORKER_KEY>`; without a worker online, AI sync is disabled in
the interface. The account and the worker key are in `compose/.env`
(`STANZARR_USERNAME`, `STANZARR_PASSWORD`, `STANZARR_WORKER_KEY`).

musicguard no longer fetches lyrics (`MUSICGUARD_LYRICS=0`). It still checks
lyrics files (size, orphans), and a new or changed one makes it start
Navidrome's scan once all audio is checked, so Stanzarr's lyrics show up
without ever letting an unchecked song in. Stanzarr follows the same flags as
below: `[by:AI-generated…]` files stay replaceable by a person-synced version.

## AI-synced lyrics

Songs whose lyrics exist only as plain text (most often Moroccan ones nobody
has synced on LRCLIB) can be synced by AI on a workstation with a GPU: the
voice is separated (Demucs), three aligners place each known line (Whisper
large-v3-turbo, Whisper large-v3, Meta's MMS) and the median time wins. On 8
songs a person had synced, this put 86% of lines within 1 s of the person's
timing; on the lines where the aligners agree (two thirds), 94%.

Every such file says so in its first lines, as LRC info tags players ignore:

```
[by:AI-generated, not checked by a person]
[re:batlab ai-sync 2026-10-11: Demucs htdemucs + Whisper large-v3-turbo + Whisper large-v3 + MMS, median vote]
[#:uncertain lines (the methods disagree): 3, 12, 19]
```

musicguard treats a file with that `[by:` line as not synced by a person: it
keeps asking LRCLIB monthly, and replaces it with a human-synced one when
there is one. Lyrics musicguard writes also lose look-alike letters inside
Latin words (a Cyrillic `е` in "ri7tеk"), which read the same but defeat
search; Russian and Greek words are left alone.

## Feishin

`feishin.homelab.lan` is a better player than Navidrome's own web page: synced
lyrics, a real queue, playlists. It is Feishin's web build (`compose/feishin`),
locked to `http://navidrome.homelab.lan`; it holds nothing itself and logs in
with each user's own Navidrome account, so favourites, playlists and plays are
shared with every other app. It reaches Navidrome through Caddy like the
browser does, so it cannot start a scan either. Its analytics are off
(`ANALYTICS_DISABLED=true`). On phones, a Subsonic app does the same job:
Symfonium or Tempo (Android), Amperfy or play:Sub (iPhone).

## Pasted links

- **songdrop** (`songdrop.homelab.lan`): one page for every link, built here
  (`compose/music-links/songdrop`) to replace the two below. A Spotify track,
  album, playlist or artist link goes through spotDL; any other link yt-dlp
  supports (YouTube, YouTube Music, SoundCloud, Bandcamp…) through yt-dlp, as
  audio. Each finished song is copied into `inbox/music/songdrop`, where
  musicguard takes it like the others, and stays downloadable from the page
  for a day (a zip for several). The options panel offers the engine flags
  that make sense here (format, bitrate, audio source, SponsorBlock…), each
  shown with its flag; lyrics, file names and folders are left to musicguard
  and Stanzarr.
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
yt-dlp from `VERSION_YTDLP` in `compose/versions.env`. Both stay current on
their own: Renovate merges new yt-dlp (a day after release), MeTube and spotDL
minor releases straight into `main`, and the nightly updater redeploys them,
rebuilding spotDL because its image tag names both pins. A spotDL major (v5)
still arrives as a pull request. When spotDL starts failing with 403, check
`#updates` and `#alerts` for a yt-dlp bump that did not land.

spotDL's web page takes track links only: an album or playlist link leaves it
on "LOADING" (the container log says `Invalid URL`). songdrop takes them.

### songdrop

FastAPI with a React page (shadcn/ui, Tailwind), in one container on spotDL's
image. Each link is a job; one runs at a time (`SONGDROP_CONCURRENT_JOBS`),
and spotDL fetches three of its songs at once. The engines run as child
processes (`backend/app/runners`) that report progress as JSON on their own
pipe, so a crash or a cancel never takes the server down. Jobs survive a
restart (`$VOLUMES_ROOT/songdrop/jobs/<id>/job.json`); after 24 h the job and
its download copies are removed, never the library's.

- **Metadata.** spotDL writes Spotify's title, artists, album, album artist,
  track and disc numbers, date and cover. Its account-free Spotify client gets
  no ISRC, so musicguard's upgrade looks those songs up by title and artist. A
  yt-dlp download carries what the site gives (YouTube Music: title, artist,
  album, date) and its thumbnail; musicguard adds the rest from Shazam and
  Apple Music.
- **Retries.** YouTube answers some audio requests with 403 at random (4 of an
  album's 15 on 2026-10-14). A failed song is tried twice more, one at a time;
  what still fails is listed on the job with the reason, and the engine's
  output goes to `docker logs songdrop`.
- **Links it refuses**: sites yt-dlp only knows through its generic page
  scraper, Spotify podcasts, and `spotify.link` short links. A link with more
  than 200 songs (`SONGDROP_MAX_ITEMS`) is refused rather than half done.

```bash
# tests, in the image
docker run --rm --entrypoint /opt/songdrop/.venv/bin/python \
  -v "$PWD/compose/music-links/songdrop/backend/tests:/opt/songdrop/tests:ro" \
  -w /opt/songdrop local/batlab-songdrop:<tag> -m unittest discover -s tests
# after changing requirements.in
docker run --rm -v "$PWD/compose/music-links/songdrop/backend:/w" -w /w \
  ghcr.io/astral-sh/uv:0.13.0-python3.14-trixie-slim \
  uv pip compile requirements.in --generate-hashes --python-version 3.14 --no-header -o requirements.txt
```

The server's own packages (FastAPI, uvicorn) and the page's npm packages are
pinned in `requirements.txt` and `package-lock.json` and are not followed by
Renovate; yt-dlp, spotDL and Node are, through `compose/versions.env`.

All three are LAN-only (Caddy `localSubnets`) and have no login, like Seerr's
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
| Navidrome | `http://caddy:4534` (Caddy, which drops scan requests), user `aurral` (`NAVIDROME_AURRAL_PASSWORD`), an admin |
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
- Tags written from MusicBrainz for each new import (`newFiles`), existing
  tags scrubbed, cover embedded; fingerprinting (AcoustID) for all files.
  Lidarr writes only its one artist name ("Manal" for "Manal & Stormy"), so
  musicguard then writes MusicBrainz's full credit into artist, album artist
  and ARTISTS, in place. Lidarr does not retag existing files, so the two never
  undo each other. Replaced files go to the
  recycle bin `recycle/lidarr` for 14 days.
- Discord: imports and upgrades to `#downloads`, health and failed imports to
  `#download-issues`, like Sonarr.
- Release profile rejecting names with `.exe`, `.scr`, `.lnk`, `.zip`, `.rar`,
  `password`, `codec`, `keygen`.

qBittorrent's excluded file names and Cleanuparr's malware blocker already
stop programs inside torrents (hosts/lab1/cleanuparr.md). Cleanuparr keeps its
settings in its own database; since 2026-10-11 it has a Lidarr instance
(`http://lidarr:8686`), the Malware Blocker on for Lidarr as a **whitelist**
(`/blocklists/lidarr-whitelist`, from `compose/arr/conf/cleanuparr/`: audio,
covers, lyrics, cue/log/nfo/checksums, booklet PDFs; anything else and the
whole torrent is removed), and `music` in the unlinked categories and the
`unlinked` seeding rule. Not Cleanuparr's official blacklist: it is written
for video and lists `*.flac`, `*.mp3`, `*.m4a`, `*.jpg` and `*.cue`, and
Blacklist Sync puts it into qBittorrent's excluded file names, which apply to
every category. qBittorrent skipped every file of every music torrent and
Cleanuparr removed it as `AllFilesSkippedByQBit`. Sonarr, Radarr and the sync
therefore use `/blocklists/blacklist`: the official list without the entries
of the music whitelist. Seeker is deliberately off for Lidarr: it
would search upgrades for every monitored album.

**Monitoring decides what gets downloaded.** A monitored album with missing
tracks is "wanted", and Lidarr (torrents) and Soularr (Soulseek,
`search_source = all`) both fetch wanted albums on their own, and upgrade
monitored ones until they reach FLAC. Since 2026-10-11 every album with a
track in the library is monitored, so albums are completed and upgraded
(14 to complete and 24 to upgrade that day); an album only partly wanted
should be unmonitored in Lidarr.

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
