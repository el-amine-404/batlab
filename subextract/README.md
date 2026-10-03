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

1. Out: signs/songs-only, forced, commentary and karaoke tracks; tracks whose
   dialogue covers less than 60% of the best one's, which are incomplete. Image
   tracks (PGS) are a fallback, below.
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

## Blu-ray image subtitles (OCR)

A language with no text track but a Blu-ray PGS track is read with Tesseract,
in its own image (`ocr/`, built with `make -C subextract ocr-image`): Tesseract
5 with the `tessdata_best` models for English, French, Arabic, Italian,
Spanish, Portuguese and German. `ocr/pgsocr.py` parses the PGS stream itself:
each shown composition becomes a cue, timed from its presentation to the next;
each object is drawn as dark text on white from the palette's luminance and
alpha, keeping the most opaque palette of a fade, cropped and padded, doubled
when its lines are small. English gets the usual OCR repairs (`l'm`, `|`, `0`
inside words); every language gets straight quotes and tidy spacing, French
keeping its space before `! ? ; :`.

The track is the default one, else the one with the most pictures, never a
forced or signs track. An OCR'd SRT is installed only when Tesseract's word
confidences reach a mean of 85% with at most 5% of words under 60%, and it has
20 lines or more; otherwise nothing is written and the reason, with sample
lines, is posted. *Attack on Titan* S04E24, 2026-10-03:

| Track | Lines | Words | Mean confidence | Under 60% | Time (2 CPUs) |
| --- | --- | --- | --- | --- | --- |
| English BD PGS | 220 | 1,570 | 95.7% | 0.1% | 99 s |
| Italian BD PGS | 344 | 1,848 | 95.2% | 0.5% | 128 s |

Read by eye, the English was right throughout, names included (Zeke, Pyxis,
Ackermann). OCR uses two CPUs (`--ocr-cpus`) and no GPU: clean white-on-
transparent Blu-ray text is what Tesseract reads best, and the T1000 would add
nothing here. A text track is always preferred, since it is exact.

Every SRT written, from text or OCR, is cleaned: sorted, renumbered, without
empty or repeated cues, each at least 0.3 s on screen. A sidecar that does not
fit is moved aside only once its replacement is written; if nothing can replace
it, it stays and is reported.

## Notifications

One message per run, only when something happened: subtitles written (with
their source, track and OCR confidence) to `#downloads`; OCR results refused,
sidecars replaced for not fitting, and errors to `#download-issues`. The footer
gives the scope, the number of files and the duration.

## The whole library

The timer only handles new imports. To go through everything once, on lab2:

```bash
cd ~/batlab && nohup nice -n 19 ionice -c 3 \
  python3 subextract/scripts/subextract.py --all > ~/subextract-all.log 2>&1 &
tail -f ~/subextract-all.log      # one line per file it changed
```

It resumes where it stopped if interrupted: a file is recorded once handled and
skipped next time unless its size changed. Add `--languages en,fr,ar,it` for
more languages, or `--dry-run` first to see the decisions without writing.
`--all` posts one summary at the end.

```bash
subextract/scripts/subextract.py --dry-run --file VIDEO   # what it would do to one file
make -C subextract test
```

## One-time installation on the server

```bash
make -C subextract ocr-image
sudo install -m 644 subextract/systemd/batlab-subextract.service \
  subextract/systemd/batlab-subextract.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-subextract.timer
```
