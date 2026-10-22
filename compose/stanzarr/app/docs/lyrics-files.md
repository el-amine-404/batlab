# Lyrics files

Stanzarr reads every lyrics file players know, and writes standard LRC.

## What it reads

| File beside `Song.flac` | Format |
|---|---|
| `Song.lrc` | LRC: `[mm:ss.xx] words`, line-synced (or plain lines without stamps) |
| `Song.elrc` | Enhanced LRC: word stamps `<mm:ss.xx>` inside lines |
| `Song.txt` | Plain text |
| `Song.ttml` | TTML (Apple's timed XML) |
| `Song.yaml`, `Song.yml` | Navidrome's Lyricsfile |
| inside the audio file | ID3 `USLT`/`SYLT`, Vorbis `LYRICS`/`UNSYNCEDLYRICS`, MP4 `©lyr` |

## What it writes

Only `Song.lrc` (synced) and `Song.txt` (plain, when LRCLIB has nothing
better). It never deletes a file; an `.lrc` it replaces is copied to
`/config/backups/` first.

## Marks

LRC info tags carry where a file came from. Players ignore them.

| Tag | Meaning |
|---|---|
| `[by:AI-generated, not checked by a person]` | AI sync; nobody has looked |
| `[by:AI-generated, checked by a person]` | AI sync, approved as it is |
| `[by:AI-generated, corrected by a person]` | AI sync, fixed in the editor |
| `[by:a person]` | Synced by hand in the editor |
| `[re:LRCLIB via Stanzarr]` | From LRCLIB, synced by a person there |
| `[#:uncertain lines (…): 3, 12]` | Lines where the AI's aligners disagreed |
| `[#:checked lines: 1, 2, 3]` | Lines a person checked in the editor |
| `[la:fr]` | The language (read if present) |

A file with no marks counts as made by a person.

## Rules

1. A synced `.lrc` made by a person is never overwritten automatically.
2. Anything marked `[by:AI-generated…]` stays replaceable: when LRCLIB has a
   person-synced version, it replaces the AI one (after a backup). Editing an
   AI file in Stanzarr keeps the mark, so this still holds.
3. Songs without person-synced lyrics are looked up on LRCLIB again every
   `STANZARR_RETRY_DAYS`.
4. Read-only mode (`STANZARR_READ_ONLY=1`) writes nothing at all.

## Statuses

| Status | The best lyrics the song has |
|---|---|
| Synced | Synced, made by a person (LRCLIB, by hand, or unmarked) |
| AI, corrected | Synced by AI, fixed by a person |
| AI, checked | Synced by AI, approved by a person |
| AI, to check | Synced by AI, not looked at yet |
| Plain only | Words without timings |
| Instrumental | LRCLIB says it has no words |
| Missing | Nothing |
