# Subtitle extraction

Releases often carry their subtitles inside the file: *Attack on Titan*'s Kira
Blu-ray has six English ASS tracks per episode, *Azur et Asmar* one SRT.
Jellyfin shows embedded text tracks, but most TV apps make lab2 burn an ASS
track into the video (a full transcode), and six tracks of one language are hard
to choose from. `scripts/subextract.py` gives every new library video one plain
`<video>.<lang>.srt` per language it has a text track for, unless a sidecar for
that language is already there and fits.

It runs every 15 minutes, seven minutes after the [dub keeper](../dubkeeper/README.md),
on Sonarr and Radarr imports at least 30 minutes old: the dub keeper restores
an upgrade's lost subtitles first, so they are not replaced by extracted ones.

Only the languages of Bazarr's profile get an SRT, English, French and Arabic
(`--languages`): the library's films carry up to sixteen, and an SRT for each
would crowd every subtitle menu. Bazarr counts embedded text tracks as present
(`use_embedded_subs`, on since 2026-10-03, PGS and VobSub still ignored), so it
no longer downloads English for a file that has MTBB's built in.

## Choosing the track

Per language:

1. Out: signs/songs-only, forced, commentary and karaoke tracks; image tracks
   (PGS, VobSub), which need OCR; tracks whose dialogue covers less than 60% of
   the best one's, which are incomplete.
2. First: the track the release marked default, its authors' choice.
3. Then: the least cluttered once converted, the share of lines on screen
   together with another; a motion-tracked sign becomes hundreds of them.
4. Then: the most complete. Honorifics variants only when no plain one is left.

Translation quality cannot be measured; these rule out the tracks that would be
wrong as SRT. *Attack on Titan* S04E24, measured 2026-10-03:

| Track | Lines | On screen | Fit to speech |
| --- | --- | --- | --- |
| [MTBB] English, default | 294 | 14.3 min | 0.02 s |
| [MTBB] English (Honorifics) | 294 | 14.3 min | 0.02 s |
| [BlurayDesuYo] English | 1810 | 31.6 min | 0.02 s |
| [Crunchyroll modified] English | 1759 | 34.0 min | 0.22 s |
| [MTBB] Signs/Songs only | 24 | 1.7 min | 49 s (signs only) |
| [BlurayDesuYo] Signs/Songs only | 1540 | 19.0 min | -10 s (signs only) |

More time on screen than the 24-minute episode is the typesetting. MTBB is
chosen. The others stay in the file for players that render ASS.

ASS is converted with [ass2srt](../ass2srt/README.md); SubRip, mov_text and
WebVTT by ffmpeg. All tracks of a file come out in one read of it.

## Existing sidecars

A language whose sidecars are SRT is checked with ffsubsync, half by half, as
the dub keeper checks restored subtitles; one sidecar that fits keeps the
language as it is. When every one is off (halves disagree, or more than 0.5 s
out), they move to `/data/recycle/subextract/` and an SRT from the file replaces
them. A track from the file is timed to it, so it is not checked. A check costs
a decode of the audio: 4 s for an episode with nothing to check, 108 s for a
99-minute film whose `.en.srt` was checked.

Jellyfin is told about each file that got a subtitle (`/Library/Media/Updated`).

```bash
subextract/scripts/subextract.py --dry-run --file VIDEO   # what it would do to one file
subextract/scripts/subextract.py --all --dry-run          # the whole library
python3 -m unittest discover -s subextract/scripts/tests
```

## One-time installation on the server

```bash
sudo install -m 644 subextract/systemd/batlab-subextract.service \
  subextract/systemd/batlab-subextract.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-subextract.timer
```
