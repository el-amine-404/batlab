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

A language whose sidecars are SRT is checked with ffsubsync, half by half;
one sidecar that fits keeps the language as it is. **Newly extracted and OCR'd
SRTs must pass the same check before installation.** Both halves must agree
within 0.3 s and the overall offset must be within 0.5 s. Both framerate
heuristics are disabled; ffsubsync must explicitly report a successful
alignment. The default non-commentary audio track is used. Its audio and
speech detection are cached for the video's remaining checks.

Existing full-dialogue SRTs in the requested languages are checked even if the
video has no embedded subtitles. Untagged `<video>.srt` files are also checked,
without guessing their language. Failed variants are reported even when another
SRT for that language passes. If no verified replacement is available, existing
files are preserved. Forced-only sidecars are excluded from dialogue checks.

Malformed SRTs, corrupt text, invalid timestamps, cues beyond the video, and
subtitles with fewer than 15 cues in either half are refused. An inconclusive
check leaves existing sidecars in place. These checks establish structural
validity and speech timing, **not translation accuracy or semantic agreement
with spoken words**. Sparse dialogue and different dub timings can cause
conservative refusals; the log gives the reason.

When all existing SRTs for a language fail and a replacement passes, originals
are copied into a unique directory under `/data/recycle/subextract/`. The new
SRT is flushed and atomically installed before redundant bad sidecars are
removed. Failed writes preserve the original. Changes by an import or Bazarr
during verification abort installation so a later run can retry.

Jellyfin is told about each file that got a subtitle (`/Library/Media/Updated`).

## Blu-ray image subtitles (OCR)

A language with no usable text track but a Blu-ray PGS track is read with Tesseract,
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
20 lines or more, with at most 5% of displayed events unreadable; it must also
pass the audio check above. Otherwise nothing is written and the reason, with sample
lines, is posted. *Attack on Titan* S04E24, 2026-10-03:

| Track | Lines | Words | Mean confidence | Under 60% | Time (2 CPUs) |
| --- | --- | --- | --- | --- | --- |
| English BD PGS | 220 | 1,570 | 95.7% | 0.1% | 99 s |
| Italian BD PGS | 344 | 1,848 | 95.2% | 0.5% | 128 s |

Read by eye, the English was right throughout, names included (Zeke, Pyxis,
Ackermann). OCR uses two CPUs (`--ocr-cpus`) and no GPU: clean white-on-
transparent Blu-ray text is what Tesseract reads best, and the T1000 would add
nothing here. A usable text track is preferred. The PGS parser rejects
truncated data and honors object cropping. VobSub/DVD, DVB and other image
codecs are currently unsupported and explicitly logged as skipped.

Every SRT written, from text or OCR, is cleaned: sorted, renumbered, without
empty or repeated cues, each at least 0.3 s on screen. A sidecar that does not
fit is removed only once its replacement is written; if nothing can replace
it, it stays and is reported.

## Notifications

One message per run, only when something happened: subtitles written (with
their source, track and OCR confidence) to `#downloads`; OCR results refused,
sidecars replaced for not fitting, and errors to `#download-issues`. The footer
gives the scope, the number of files and the duration.

## The whole library

The timer only handles new imports. To process the TV and movie library once,
run this from the laptop (the job runs on lab2 and survives SSH disconnection):

```bash
ssh lab2 'nohup nice -n 19 ionice -c 3 python3 -u /home/potato/batlab/subextract/scripts/subextract.py --all --languages en,fr,ar --quiet > /home/potato/subextract-all.log 2>&1 < /dev/null &'
ssh lab2 'tail -f /home/potato/subextract-all.log'
```

`--all` recursively scans `/mnt/storage/data/media/tv` and `media/movies`,
excluding downloads and recycle directories. It handles one video at a time;
OCR is capped at two CPUs and 2 GiB. A lock shared with the timer prevents
overlapping runs. `--quiet` suppresses Discord posts; Jellyfin is refreshed
after successful writes.

Progress is checkpointed atomically after every completed file. Rerunning the
command resumes; changed videos, sidecars, languages, scripts, or OCR images
invalidate the cache. Old size-only cache entries are rechecked. Refusals,
skips and errors are retried. The log prints each file before processing and
a final summary; errors or refusals produce exit status 1.

Use `--languages en,fr,ar,it` to include Italian, or `--languages all` for all
tagged text languages and supported OCR languages. `--dry-run` performs the
same extraction/OCR/verification using temporary files but leaves library
subtitles and resume state unchanged. Missing OCR/Bazarr dependencies stop the
run; `--no-ocr` explicitly opts out of OCR. Refused results need review; they
are never silently installed.

```bash
subextract/scripts/subextract.py --dry-run --file VIDEO   # what it would do to one file
make -C subextract test
```

## Validation on lab2, 2026-10-04

The safety review follows git commit `c422fe2`. Tests use isolated hard links
under the recycle directory, without changing the original library sidecars.

| S04E24 path | Cues | OCR confidence | Audio check |
| --- | --- | --- | --- |
| MTBB ASS → English SRT | 273 | n/a | passed, 0.00 s |
| English BD PGS → English SRT | 220 | about 96% | passed, +0.16 s |
| Italian BD PGS → Italian SRT | 344 | about 95% | passed, 0.00 s |
| English SRT deliberately shifted by 30 s | — | n/a | refused, −29.96 s |

The 71 tests across subextract, PGS OCR, ass2srt and dubkeeper pass. Regression
coverage includes failed writes preserving originals, concurrent sidecar
changes, interrupted-run checkpoints, overlapping runs, malformed/cropped PGS,
unreadable OCR events, and ffsubsync reporting failure with a zero exit code.

## One-time installation on the server

```bash
make -C subextract ocr-image
sudo install -m 644 subextract/systemd/batlab-subextract.service \
  subextract/systemd/batlab-subextract.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-subextract.timer
```
