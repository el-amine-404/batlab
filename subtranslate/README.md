# Subtitle translator

Bazarr wants English, French and Arabic for every video, and some languages
exist on no subtitle site: in 2026-10, Arabic for most of *Attack on Titan* and
*Vinland Saga*. `scripts/subtranslate.py` fills those gaps with an AI
translation of the English subtitle, and keeps looking for a human one to
replace it. It runs every 6 hours:

1. **Translate.** For each language on Bazarr's wanted list, it asks Bazarr to
   translate the English sidecar with Gemini, at most 6 per run, once:
   - the video is 7 days old, so Bazarr's own searches go first;
   - [subextract](../subextract/README.md) has handled that very file
     completely (its state says so), so a language the release carries inside
     the video is already a sidecar and is never translated; a video with a
     track subextract refused or skipped is left alone;
   - no subtitle in that language is next to the video, whatever Bazarr's list
     says: Bazarr writes `<video>.<lang>.srt`, over anything there;
   - the English sidecar is one subextract checked against the audio and that
     hasn't changed since; a plain one rather than hearing-impaired (sound
     descriptions translate badly), and never a translation.

   A translation that doesn't appear is tried again a day later, three times
   in all, then reported once.

   Gemini gets the show's title, year and summary from Bazarr, and the
   dialogue 300 lines at a time, so most of an episode in one request.
2. **Tag.** Bazarr opens every translation with the line `# Subtitles
   translated with <model> #`, shown for the first seconds of the video. That
   line marks the file as AI-made, for whoever watches and for this job.
3. **Keep looking.** Each AI subtitle is searched for again every 7 days with
   Bazarr's manual search. A subtitle in the same language that reaches
   Bazarr's own minimum score (90 for episodes, 70 for films) replaces it.
   Bazarr's upgrades give up after 30 days at most; this doesn't.

Bazarr refuses AI and machine-translated subtitles from OpenSubtitles, so a
replacement is always a human one. A run that did something posts one message
to `#downloads`: what was translated, replaced, or given up on.

Nothing is translated into English, and a video with no English sidecar is
left alone (most of *Courage the Cowardly Dog*). Where a release has several
English tracks, subextract already chose one (the release's default, then the
least cluttered, then the most complete). Expect AI subtitles to get jokes, names and he/she
wrong now and then; they are a stopgap.

## Setting up Gemini

In Bazarr → Settings → Subtitles → Translating: translator **Gemini**, an API
key from [Google AI Studio](https://aistudio.google.com/apikey), and a current
Flash model (the default, `gemini-2.0-flash`, is old). Until the translator is
Gemini, the job only searches for replacements. The key stays in Bazarr.

```bash
subtranslate/scripts/subtranslate.py --dry-run     # list what it would do
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
