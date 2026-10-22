# Music

How a song gets from a request or a pasted link into Navidrome, and what checks
it on the way. Set up on 2026-10-11.

```
 request (Aurral) ─┬─ album ──▶ Lidarr ──▶ Prowlarr ──▶ qBittorrent ─┐
                   │                  └─▶ Soularr ──▶ slskd ────────┤ (both through gluetun)
                   └─ track, playlist ──▶ Aurral: slskd, else yt-dlp ─┤
 pasted link ──▶ songdrop (spotDL, yt-dlp) ─────────▶ inbox ───────┤
                                                                      ▼
                                     musicguard: check, strip, tag, lyrics, file
                                                                      ▼
                                                  Navidrome (three libraries)

 music video (clipseerr) ─┬─ the song ──▶ Aurral (track), else songdrop ──▶ (as above)
                          └─ the video ──▶ videodrop ──▶ media/music-videos ──▶ Jellyfin
```

## Where everything lives

| Path under `DATA_ROOT` | What | Who writes it | Navidrome sees it at |
| --- | --- | --- | --- |
| `media/music` | albums Lidarr manages, and the music that was here before | Lidarr | `/music` |
| `media/music-aurral` | single tracks, playlists and flows requested in Aurral | Aurral | `/data/media/music-aurral` |
| `media/music-extras` | songs from pasted links | musicguard | `/music-extras` |
| `inbox/music/songdrop` | link downloads waiting to be checked | songdrop | never |
| `media/music-videos` | video files asked for in clipseerr, `Artist/Title [id].mp4` | videodrop (destination `music-videos`) | never (Jellyfin's Music Videos library) |
| `soulseek/{incomplete,complete}` | slskd downloads before Lidarr or Aurral import them | slskd | never |
| `.quarantine/musicguard/<date>/` | everything musicguard refused or retired, with a `.why.json` beside it; deleted 30 days after that date (`MUSICGUARD_QUARANTINE_DAYS`, 0 keeps it) | musicguard | never |

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
  in common, the same length within 5 s and the same version (a remix, a live
  or sped-up take still files: `identify.version` reads it from the title's
  brackets or what follows " - ") and the new download is dropped (`duplicate ALREADY_IN_LIBRARY`),
  unless it is better audio than a copy musicguard filed itself (FLAC over
  MP3, say): then it is filed and the next pass retires the old one.
- **The same song filed twice keeps its best copy** (`app/dedup.py`), after
  each library pass. One song: the same ISRC or MusicBrainz recording ID, or
  the title and an artist in common with lengths within 5 s, or within 30 s
  when their fingerprints agree (`reference.SAME`): a video's copy carries an
  intro or an outro, a remix sounds different. A title naming another version
  ("PIRATE (Club Remix)", "PIRATE - Live") is never the original's copy, unless
  the ISRC or recording ID says so. Such a pair, and one title whose copies
  turn out to be two recordings, stays and is flagged once to the
  download-issues channel (`flagged VERSION` or `flagged SAME_TITLE`), so a
  remix filed as its original gets seen. On 2026-10-14 a YouTube
  "Mghayer (Prod. By Ysos) #CAMÉLÉON" (220 s) and Spotify's "Mghayer" (214 s)
  were both filed. The best audio stays: Lidarr's or Aurral's copy always (they
  own theirs; nothing of theirs is moved), then lossless, then the copy
  without extra seconds, then the bitrate (Opus and AAC worth 1.5 and 1.3 times
  MP3's). It gets the best tags: a MusicBrainz ID, then a clean title (no
  hashtag, brackets, "Official Video"), then the most complete set, completed
  from the other copies (a genre only one had), and is moved to the path those
  tags give. The other `music-extras` copies go to quarantine as `DUPLICATE`,
  with their lyrics, after handing over what the kept copy lacks (below). `docker exec musicguard python /app/guard.py dedup`
  runs one pass; `MUSICGUARD_DEDUP=0` turns it off.
- **Songs from links are upgraded** (`app/upgrade.py`), after each library
  pass: musicguard looks the song up on MusicBrainz (by ISRC, else title and
  artist), picks the release it belongs to (the full album, else the EP, else
  the single; never a compilation, live or remix release), and has Lidarr
  monitor and search that album, adding the artist with nothing else
  monitored if it is new (`albumsToMonitor`: with "monitor none" alone, Lidarr
  unmonitored the asked album too, seconds after adding the artist; Lomepal's
  Jeannine, 2026-10-15). From the next pass musicguard checks that the album
  and its artist are still monitored, and wants them again if not. A remix or
  live take is never replaced by the album's original. Once Lidarr's file for that recording is in `music`
  and musicguard has verified it, the YouTube copy and its lyrics go to
  quarantine as `UPGRADED` (not announced on Discord), and its folder and
  cover leave `music-extras`. First it hands over what Lidarr's copy lacks
  (`app/carry.py`): synced lyrics from LRCLIB, a person or the AI sync, when
  both copies last the same (±1.5 s), and a person's over LRCLIB's or AI ones
  Lidarr's copy already got; synced lyrics of unknown origin, or timed for a
  copy of another length, go over as plain text, since Stanzarr stops asking
  LRCLIB once a song has a synced file not flagged AI. The cover goes over
  only into a folder without one, between copies of the same album. Lidarr
  writes its own `cover.jpg` into each album folder too (Settings → Metadata →
  Kodi/Emby, only "Album Images" on, since 2026-10-14). A song MusicBrainz does not know (much
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
- **Lyrics left behind by a rename** (Lidarr renames a track after import,
  "01 - " to "1-01 - ", or an album folder): after an hour without a track
  of their name they follow the one track of their title in the folder, else
  in the artist's other album folders, and replace that track's own lyrics
  only when better (a person's or LRCLIB's over AI or unknown ones). Lyrics
  with no such track, or not better, go to quarantine (`ORPHANED_LYRICS`,
  not announced); none are deleted outright.
- **Programs and archives in a library** (`.exe`, `.zip`, `.lnk`, `.html`…, or
  anything that starts like one, a "cover.jpg" that is an ELF binary included)
  are quarantined; other odd files are reported once and left.

**Identification (Shazam).** musicguard asks Shazam what a recording is,
through SongRec (an open-source Shazam client, compiled into the image): only
a fingerprint leaves the server, never audio. Shazam knows far more Moroccan
and streaming-only music than MusicBrainz's AcoustID.

- A pasted link takes Shazam's artist, title and album over the video's title
  whenever they disagree, and loses the IDs it came with.
  Not a Spotify download (spotDL leaves the track's URL in `WOAS`; its ISRC
  is empty): Spotify named that song, so when Shazam hears something with
  neither title nor artist in common, and Apple's preview does not vouch for
  the file, it is a wrong download, quarantined as `WRONG_SONG`. Its album,
  date and track numbers are Spotify's: Apple only fills what it lacks (Apple
  dates Caméléon 2020, Spotify 2021, and "Halla halla" was filed apart in
  "Caméléon (2020)"), and a Shazam answer sharing a title or an artist leaves
  Spotify's name (it heard Caméléon's "Intro" as "Haj Bellaïd - Intro"). On 2026-10-14
  spotDL's YouTube pick for ElGrandeToto's "La Paille" was a Bhojpuri song,
  and was filed as "Chhote Raja - Pala Lagat Rahe Ho Lahnga".
- A file new to a library that Shazam hears as a different song, with neither
  title nor artist in common, is quarantined as `WRONG_SONG`; a monitored album
  then makes Lidarr fetch another copy. A remix, a feature or a translated title
  shares one of them and is not called wrong. No match is never a verdict.
  Before anything moves, the file is compared with **the real song**
  (`app/reference.py`): Apple Music's 30 s preview of the song its tags name,
  Chromaprint fingerprints of both (AcoustID's technique), the preview slid
  along the file (`batlab_common.previews` and `.fingerprint`, shared with
  songdrop: `lib/batlab-common`). 0.85 or more of the fingerprint bits alike: it is that song,
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
inbox and `#download-issues` hears about it once. Findings go through Apprise
(`compose/apprise`) to its `download-issues` channel, grouped by kind
(quarantined, suspect, flagged, held, errors), and every event to
`${VOLUMES_ROOT}/musicguard/reports/<month>.jsonl`.

The code is one module per job (`compose/musicguard/app`): `guard.py` starts
the passes and puts the parts together; `parts/` holds them (`checks`, the
safety and identity checks; `filing`, one file into its library; `passes`,
the inbox and library passes; `duplicates`; `upgrades`, Lidarr; `lyrics_files`;
`quarantine`; `announce`, Navidrome's scan and "Ready to play"; `reporting`;
`state`); `settings.py` is what the environment can set. The modules beside
them (`dedup`, `upgrade`, `tags`, `layout`, `lyrics`…) are the rules and the
clients, with no state of their own, and what songdrop also needs lives in
`lib/batlab-common`.

```bash
python3 -m unittest discover -s compose/musicguard/tests     # needs ffmpeg and mutagen
python3 -m unittest discover -s lib/batlab-common/tests      # the shared code
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
`#downloads` ("Ready to play in Navidrome", `MUSICGUARD_READY_WEBHOOK`): one
card per album ("Artist – Album"), listing its new songs with track number,
length and quality, and whether they came from Lidarr or a link.
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
  (`compose/songdrop/app`) to replace the two below. A Spotify track,
  album, playlist or artist link goes through spotDL; any other link yt-dlp
  supports (YouTube, YouTube Music, SoundCloud, Bandcamp…) through yt-dlp, as
  audio. Each finished song is copied into `inbox/music/songdrop`, where
  musicguard takes it like the others, and stays downloadable from the page
  for a day (a zip for several). The options panel offers the engine flags
  that make sense here (format, bitrate, audio source, SponsorBlock…), each
  shown with its flag; lyrics, file names and folders are left to musicguard
  and Stanzarr.
- **MeTube** was removed on 2026-10-18, and with it `metube.homelab.lan` and
  `inbox/music/metube`: songdrop takes every link MeTube took, and videos are
  videodrop's (`compose/videodrop`, docs/services.md). Following a channel
  for new uploads, the one thing MeTube did beyond them, was never used.
- **spotDL's own page** (`spotdl.homelab.lan`, single track links only) was
  removed on 2026-10-14: songdrop takes the same links and albums, playlists
  and artists too, with the same options, and checks each song against
  Spotify's preview. Its address no longer exists. spotDL itself
  still runs inside songdrop; Spotify playlists can also be imported in Aurral.

spotDL's image carries the yt-dlp it was released with, and YouTube answers an
old yt-dlp with 403 Forbidden: v4.5.2 (yt-dlp 2026.07.04) failed every download
on 2026-10-11 while MeTube's 2026.8.19 (then running here) worked on the same link. So songdrop is
built locally on the pinned spotDL release with the yt-dlp from
`VERSION_YTDLP` in `compose/versions.env`. Both stay current on their own:
Renovate merges new yt-dlp (a day after release) and spotDL minor releases
straight into `main`, and the nightly updater redeploys them, rebuilding
songdrop and videodrop because their image tags name the pins. A spotDL major (v5)
still arrives as a pull request. When spotDL starts failing with 403, check
`#updates` and `#alerts` for a yt-dlp bump that did not land.

### songdrop

songdrop's own docs, for using it, for its API (`/api/v1`) and for working on
it, are a site of their own: https://el-amine-404.github.io/batlab/songdrop/
(VitePress, sources in `compose/songdrop/app/docs/`, published on
GitHub Pages by `.github/workflows/docs.yml`). The service itself has the API
reference, made from the running API's OpenAPI description, at
http://songdrop.homelab.lan/docs. The sidebar, the same in
batlab's four apps (`lib/batlab-ui`), has Add (the link box), Queue, History
and Missing, then Settings, whose own list is on its page (Your account,
General, Downloads, Retries, Connect, Spotify, Other sites, YouTube, People,
API keys, Status). Settings are kept in
`$VOLUMES_ROOT/songdrop/jobs/settings.json` over the environment's values,
connections (where events go) in `connections.json` beside it, both mode 600.
What it does and does not do is in [services.md](services.md).

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
- **Right song.** spotDL picks a YouTube upload by title and length; a
  re-upload can carry both over other audio. Each download is fingerprinted
  against Spotify's own 30 s preview (from the track's embed page,
  `runners/verify.py` with `batlab_common.previews`): the same song scores about 0.9,
  another about 0.55. Below 0.70 the file is deleted and the next YouTube
  uploads of the same length (within 7 s) are tried, up to four, until one
  sounds right; none does, and the song fails with that reason. No preview:
  kept, and musicguard still checks it. `docker logs songdrop` shows each
  score.
- **Retries.** YouTube answers some audio requests with 403 at random, in
  bursts (4 of an album's 15 on 2026-10-14; EL MEJOR failed three times in
  13 s, and downloaded fine an hour later). A failed song is first tried
  twice more within seconds, then with other YouTube uploads of its length
  that sound like Spotify's preview (spotDL found nothing for Caméléon's
  "Intro"; the next upload scored 0.93). What still fails waits for later
  tries on its own: after 10 min, 1 h and 6 h, one song at a time through the
  same queue, then songdrop gives up. The page lists missing songs openly
  with the next try, a "Retry now" button (also after giving up) and a link to
  the song on Spotify; a song a later try brings back gets its download
  button and goes to the library. A job is not removed while a try is
  pending.
- **Connect** (Settings): songdrop reports events (`job.incomplete`,
  `song.recovered`, `song.gave_up`, `job.finished`) to connections, each with
  its type (Apprise, Discord, or a Webhook that gets the event as JSON) and
  the events it wants. Here it is one Apprise connection to the
  `download-issues` channel (`SONGDROP_APPRISE_URL` and `_TAG` make it on a
  first start), where musicguard's findings go too.
- **Long videos, playlists and sign-in** (yt-dlp links, in Options):
  *One song per chapter* splits a video at its chapters (a whole album or a
  mix uploaded as one); each part is tagged with the chapter's title, without
  its "01." or "[03:15]", with its artist when the chapter reads "Artist -
  Song", the video as its album and the chapter's number, and the whole video
  is dropped; a video without chapters stays one song and the page says so.
  *Clip from / to* (1:23 or 1:02:03) keeps only part of a video, one song of
  a live set say; not together with chapters. *How much of the playlist*
  keeps the first 10 to 100 items. **YouTube cookies** (Settings, YouTube):
  a cookies.txt exported from a signed-in browser lets every
  download (spotDL's too) fetch videos YouTube shows only to a signed-in
  viewer. It is kept in `$VOLUMES_ROOT/songdrop/jobs/cookies.txt` (mode 600),
  never sent back to the page, which shows only when it was saved and when the
  YouTube cookies run out; each job gets its own copy, removed when it ends.
- **Links it refuses**: sites yt-dlp only knows through its generic page
  scraper, Spotify podcasts, and `spotify.link` short links. A link with more
  than 200 songs (`SONGDROP_MAX_ITEMS`) is refused rather than half done.

```bash
# tests, in the image
docker run --rm --network none --entrypoint /opt/songdrop/.venv/bin/python \
  -v "$PWD/compose/songdrop/app/backend:/w" -w /w \
  local/batlab-songdrop:<tag> -m unittest discover -s tests
# after changing requirements.in
docker run --rm -v "$PWD/compose/songdrop/app/backend:/w" -w /w \
  ghcr.io/astral-sh/uv:0.13.0-python3.14-trixie-slim \
  uv pip compile requirements.in --generate-hashes --python-version 3.14 --no-header -o requirements.txt
```

The server's own packages (FastAPI, uvicorn) and the page's and docs' npm
packages are pinned in `requirements.txt` and the two `package-lock.json` and
are not followed by Renovate; yt-dlp, spotDL and Node are, through `compose/versions.env`.

Both are LAN-only (Caddy `localSubnets`) and have no login, like Seerr's
request page. YouTube audio is lossy (about 130-160 kbps Opus); for an album
you care about, request it in Aurral instead, which goes through Lidarr.

## clipseerr (music videos)

`clipseerr.homelab.lan`, built here (`compose/clipseerr`): the newest music
videos to browse, watch and ask for, the way Seerr does for films. It only
asks and follows; the apps below download, musicguard checks, Navidrome and
Jellyfin play. Its API reference is at `/api/docs` on the service.

- **Browse.** Discover shows YouTube Music's new videos and, for the country
  in Settings, General, its Trending 20, daily top and top 100 video charts,
  and Apple Music's top music videos (newest first in "Just released").
  Beside them, *Latest releases* (also its own page, Releases): the last 30
  days and what is about to come out, by day, from everything clipseerr
  reads: YouTube Music's "New music videos" page (about 90 a country), Hot
  (below) and every Apple chart, each video dated. No free source lists every
  release, so a video none of them carries is missing. Dates read "16 Oct 2026 ·
  yesterday". Genres are YouTube's genre charts (only some countries have
  them: the US has eight), about 35 of Apple's, and *Hot* (sexy, twerk and
  uncensored music videos), newest first:
  the real songs among what r/SexyMusicVideos posts (subfeed's JSON Feed,
  below; clipseerr judges each video, see *Hot's songs*) and the official
  videos of YouTube searches (twerk, sexy, uncensored… by default), both set
  in Settings, General. Search finds videos and artists. An artist's page
  joins YouTube Music's list (which stops at 150) with Apple's (dated), newest
  first. A video's page lists its other versions: the clean or explicit cut,
  the official video, remixes, live takes, as videos and as songs, each
  requestable. An Apple video YouTube doesn't have yet says when it comes out
  and links to Apple Music. Long lists (a shelf, a genre, Hot, Releases)
  come 48 at a time (`limit`, `offset`, `total` on the API): the next page
  loads about two screens before the end, and Back finds the list where it
  was left. An artist's videos come at once and are drawn 48 at a time. No account and no key: ytmusicapi
  (pinned in `compose/versions.env`, followed by Renovate) and Apple's public
  feeds. Answers are kept in its database and refreshed in the background
  (the front page every 20 min), so a page opens from the cache in a few ms.
- **What you have.** Settings, Libraries: where songs and music videos
  already are. Navidrome (its songs, read through Caddy's :4534 with
  Homepage's token, so a scan asked there never reaches it) and the music
  videos folder (`media/music-videos`, mounted read only; a YouTube id in a
  file's name matches exactly, else artist and title). They are read every
  10 min; every video clipseerr shows gets a green check when its song or its
  file is there, "Requested" while a request is under way, and its page says
  "In your library" (with "Get it again").
- **Hot's songs.** A feed (any JSON Feed) says only what a community posted;
  clipseerr asks YouTube Music about each post's first YouTube video
  (`catalog/verdicts.py`): *official* when it is the artist's video or the
  song, or an upload of a song YouTube Music has under the same title and
  artist; *likely* when YouTube Music doesn't know the song (a small artist,
  not on streaming yet) but the title says it is the official video and not
  a cover, lyric video, reaction, live take or freestyle; *unofficial*
  otherwise; *unavailable* when gone. Hot shows official and likely ones.
  Verdicts are kept in the cache and judged in the background, three at a
  time: a new video joins Hot a minute or so after the feed has it. They are
  judged again after 7 days (30 for official ones), and all of them when the
  rules change (`verdicts.RULES`).
- **Hot's own.** "Add to Hot" on a video's page puts a video no feed or
  search found into Hot. Hot keeps every artist it has shown (their pages
  and pictures are found in the background), listed above its videos.
- **The song.** A video's page shows the song's credits (performed, written,
  produced by, and who gave them: YouTube Music's "Song credits") and its
  lyrics: LRCLIB's, timed when it has them, else YouTube Music's
  (LyricFind's), in a box that follows the song while "Song" plays (a click
  on a line plays from it), with `.lrc` and `.txt` downloads; "Lyrics not
  available" when neither has them.
- **Watch.** The video, or the song alone (its official audio on YouTube
  Music, found by title, artist and length: a video often runs longer than
  its song), or Apple's 30 s preview. Nothing loads from YouTube until play
  is pressed. Some videos refuse to play on other pages; they link to YouTube.
- **Ask.** *The song*: to the first service in Settings, Services that takes
  audio, then the next when it fails or takes longer than the attempt timeout
  (12 h): **Aurral** (a track request: Soulseek first, so lossless when
  someone shares it, then its own yt-dlp), then **songdrop** (YouTube Music's
  audio, about 130-160 kbps Opus). Both deliver into folders musicguard
  checks. Aurral answering "already in the library" ends the request at once.
  *The video*: to **videodrop** (`compose/videodrop`), its `music-videos`
  destination: MP4s at the best quality into `media/music-videos`, named
  `Artist/Title [id].mp4`, tags and cover embedded, never into musicguard's
  inbox. videodrop calls back when the download ends. Asking twice for the same thing
  answers the first request.
- **Follow.** Every 15 s (Settings, General) it asks each service where its
  requests are, once per service a round; songdrop and videodrop also call back. The
  Requests screen shows each request, the services it went through and the
  format it arrived in (FLAC, OPUS, MP4). Retry asks every
  service again from the first.
- **Tell.** Each request made, done and failed goes to Discord's
  `#requests` through Apprise (sender `clipseerr`), with a link to it.
- **Plug in.** Services and notifications are types with a form (Settings);
  a new app is one module in `app/services/` or `app/notifications/`. Any app
  without a type takes requests through a *Webhook* service: the request as
  JSON, and its progress back through `POST /api/v1/requests/{id}/report`.
  Settings, services and notifications are all in the API too.

Set up once:

| Setting | Where | Value |
| --- | --- | --- |
| Aurral's API key | `compose/.env`, `AURRAL_API_KEY` (first start), else Settings, Services, Aurral | Aurral: Settings, General, API key |
| Jellyfin's Music Videos library | Jellyfin, Dashboard, Libraries | type Music Videos, folder `/data/media/music-videos` |
| Country and Apple store | Settings, General | YouTube `US`, Apple `us` by default (`CLIPSEERR_REGION`, `CLIPSEERR_STOREFRONT`) |

What it keeps: `$VOLUMES_ROOT/clipseerr/clipseerr.db` (SQLite, mode 600:
requests, services with Aurral's key, notifications, settings, the catalog
cache). The environment's services and notification are made on the first
start only; after that the Settings screens own them.

```bash
# tests, in the image
docker run --rm --network none --entrypoint /opt/clipseerr/.venv/bin/python \
  -v "$PWD/compose/clipseerr/app/backend/tests:/opt/clipseerr/tests:ro" -w /opt/clipseerr \
  -e CLIPSEERR_STATE=/tmp/s local/batlab-clipseerr:ytmusicapi-<version> -m unittest discover -s tests
# after changing requirements.in
docker run --rm -v "$PWD/compose/clipseerr/app/backend:/w" -w /w \
  ghcr.io/astral-sh/uv:0.13.0-python3.14-trixie-slim \
  uv pip compile requirements.in --generate-hashes --python-version 3.13 \
  --python-platform x86_64-unknown-linux-gnu --no-header -o requirements.txt
# after changing app/schemas.py: the page's types, from a running clipseerr
cd compose/clipseerr/app/frontend && CLIPSEERR_OPENAPI=http://clipseerr.homelab.lan/api/v1/openapi.json npm run gen:api
```

When YouTube changes and the lists come back empty, Settings, Status says
which source fails; a newer ytmusicapi (Renovate) is usually the fix.

### subfeed (subreddits as feeds)

`compose/subfeed`, `subfeed.homelab.lan` (its page; the API reference at
`/docs`). It turns a community's posts into feeds a program can read: every
post of a subreddit, newest first, with what it links to (`link`: video,
reddit_video, image, gallery, text, other) and the videos it carries. It
keeps and answers everything, and judges nothing; its readers choose with
parameters. clipseerr's Hot reads r/SexyMusicVideos' posts with a YouTube
video from it, and keeps the real songs.

- **Reading.** Every feed is read every 30 min (`poll_minutes`; Settings,
  General for new feeds): Reddit's own Atom feed of the newest 100 posts
  (Reddit refuses its JSON without a login, and answers 429 to an address
  that asks too often, so calls are 10 s apart and a `Retry-After` is
  honoured), and PullPush's archive when Reddit refuses. A new feed first
  takes its last 500 posts from PullPush (a few days behind Reddit). A read
  that fails waits longer before the next one (doubling, up to 6 h); after
  3 in a row the feed is *failing*: one message to `#alerts`, and another
  when it works again. Posts are kept (Settings, General can forget the
  old ones).
- **Reading it.** `GET /api/v1/feeds/{slug}/feed.json` (JSON Feed 1.1, its
  extra fields in `_subfeed`), `…/feed.atom` (any feed reader) and `…/posts`
  (subfeed's JSON) take the same parameters, repeated for several values:
  `link` (the kinds above), `video=youtube` (posts carrying a YouTube video),
  `domain` (`youtu.be`, `v.redd.it`), `q` (words of the title), `author`,
  `since` and `until` (Unix seconds), `unique=true` (one post a video: its
  first), `limit`, `offset`. Hot reads
  `…/feed.json?video=youtube&unique=true&limit=500`. Each answer carries an
  `ETag`; sent back as `If-None-Match`, an unchanged feed answers 304 with no
  body. The feed's page shows these addresses, filters included.
- **Changing feeds.** On its page, or `POST /api/v1/feeds {"target":
  "Name"}`, `PATCH` and `DELETE /api/v1/feeds/{slug}`, `POST …/refresh` to
  read now. `SUBFEED_FEEDS` (`slug=reddit:Subreddit,…`) makes them on the
  first start only.
- **Telling.** Settings, Notifications: Apprise (`#alerts` here), Discord or
  a Webhook, each with the events it wants: `feed.failing`,
  `feed.recovered`, and `posts.new` for programs.

It took clipfeed's database as it was (its posts and feeds; clipfeed's
YouTube Music verdicts went, clipseerr keeps them now).

```bash
# another subreddit
curl -X POST http://subfeed.homelab.lan/api/v1/feeds -H 'Content-Type: application/json' \
  -d '{"target": "MusicVideos"}'
# tests, in the image
docker run --rm --network none --entrypoint /opt/subfeed/.venv/bin/python \
  -v "$PWD/compose/subfeed/app/backend/tests:/opt/subfeed/tests:ro" -w /opt/subfeed \
  local/batlab-subfeed -m unittest discover -s tests
# after changing requirements.in: as clipseerr's, in compose/subfeed/app/backend
# after changing api/schemas.py: the page's types, from a running subfeed
cd compose/subfeed/app/frontend && SUBFEED_OPENAPI=http://subfeed.homelab.lan/api/v1/openapi.json npm run gen:api
```

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

Soularr runs every 6 hours (`SCRIPT_INTERVAL`): each run searches Soulseek
for every wanted album, and every 10 minutes meant 985 searches a day on
2026-10-14, mostly for Moroccan albums nobody shares. A newly requested album
goes to the torrent indexers at once and to Soulseek within 6 hours.

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
