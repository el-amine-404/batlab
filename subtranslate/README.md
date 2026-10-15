# Subtitle translator

Bazarr wants English, French and Arabic for every video, and some languages
exist on no subtitle site: in 2026-10, Arabic for most of *Attack on Titan* and
*Vinland Saga*. `scripts/subtranslate.py` fills those gaps with an AI
translation of the English subtitle, and keeps looking for a human one to
replace it. It runs every 6 hours.

## Who writes what, and when

subextract and this job take the same lock, so they never run at once, and
this job writes its files itself, in one step that never replaces a file.

```
subextract    every 15 min   embedded subtitles → sidecars, checked against the audio;
                             records each video it handled completely
subtranslate  every 6 h      a wanted language with no sidecar, on a video subextract
                             handled completely → Gemini → <video>.<lang>.srt
Bazarr        its own times  downloads human subtitles; a download replaces an AI one
```

A first design let Bazarr's own translator write the file. Bazarr translates in
the background and writes `<video>.<lang>.srt` over anything there a minute or
two later, so a subtitle subextract or Bazarr wrote in between would have been
lost. Now:

- a subtitle that appears while Gemini translates wins: the job's write fails
  and the translation is dropped;
- a subtitle Bazarr downloads later replaces the AI one, as it should.

## What it does

1. **Translate.** For each language on Bazarr's wanted list, at most 6 per
   run, once:
   - the video is 7 days old, so Bazarr's own searches go first;
   - [subextract](../subextract/README.md) has handled that very file
     completely (its state says so), so a language the release carries inside
     the video is already a sidecar and is never translated; a video with a
     track subextract refused or skipped is left alone;
   - no subtitle in that language is next to the video;
   - the English sidecar is one subextract checked against the audio and that
     hasn't changed since; a plain one rather than hearing-impaired (sound
     descriptions translate badly), and never a translation. Where a release
     has several English tracks, subextract already chose one (the release's
     default, then the least cluttered, then the most complete).

   Gemini gets the show's title, year and summary, the episode's title, the
   names that recur in the dialogue, and the dialogue itself, 400 lines per
   request (an episode in one, a film in four). It answers with the text of
   each line; the numbering and timing are copied from the English by code.
   An answer missing a line, doubling one or leaving one empty is refused and
   tried again a day later, three times in all, then reported once.

   **Reading speed.** The timing is the English's on purpose: a line shows
   while a character speaks, whatever the language, and subextract checked
   that timing against this very video. What a translation changes is the
   length: French runs 15 to 20% longer than English. A translated line over
   20 characters a second (Netflix's guideline for English) and longer than
   the English line goes back to Gemini once, with the most characters it may
   have, to be shortened with the same meaning; it comes back only shorter or
   not at all. A translation is held to the English, never made shorter than
   it: 17 a second, the usual figure for other languages, is broken by a
   quarter of the English lines of *Attack on Titan* S03E03. Estimated at
   French length, that pass sends 31 of 200 lines of *Vinland Saga* S02E01
   and 104 of 384 of *Attack on Titan* S03E03: one more request.
2. **Tag.** Every translation opens with `Translated by AI (<model>)` for its
   first seconds. That line marks the file for whoever watches and for this
   job, which tells Bazarr to rescan the show so it stops asking for it.
3. **Keep looking.** Each AI subtitle is searched for again every 7 days with
   Bazarr's manual search. A subtitle in the same language that reaches
   Bazarr's own minimum score (90 for episodes, 70 for films) replaces it.
   Bazarr refuses AI and machine-translated subtitles from OpenSubtitles and
   SubDL, so a replacement is a human one. A search costs nothing; a download
   counts against the site's daily limit.

A run that did something posts one message to `#downloads`: what was
translated, replaced, or given up on. Nothing is translated into English, and a
video with no English sidecar is left alone (most of *Courage the Cowardly
Dog*). Expect AI subtitles to get jokes, names and he/she wrong now and then;
they are a stopgap.

## Gemini

`GEMINI_API_KEY` and `GEMINI_MODEL` in `compose/.env`. A key from
[Google AI Studio](https://aistudio.google.com/apikey) on a project with no
billing account can't be charged: past the free quota Gemini refuses (429),
and the job stops translating until its next run. A Gemini app subscription
doesn't cover the API. On the free tier Google may use what it is sent, here
subtitle text, to improve its products. Without the two settings the job only
looks for human subtitles.

```bash
subtranslate/scripts/subtranslate.py --dry-run     # list what it would do; no request, no file
subtranslate/scripts/subtranslate.py --translate 1 --only "Vinland Saga 2x1 → fr"
python3 -m unittest discover -s subtranslate/scripts/tests
journalctl -u batlab-subtranslate.service --since today
```

## One-time installation on the server

```bash
sudo install -m 644 subtranslate/systemd/batlab-subtranslate.service \
  subtranslate/systemd/batlab-subtranslate.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-subtranslate.timer
```
