# ass2srt

Fansub ASS tracks carry styling, signs and karaoke that SRT cannot, and a plain
conversion (ffmpeg, or the one behind *Vinland Saga*'s old `.en.srt`) shows
each lyric three times, once per ASS layer, and turns a frame-by-frame sign into
hundreds of 0.12 s cues. `scripts/ass2srt.py` keeps what a reader needs:

- dialogue and signs, as plain text;
- opening, ending and insert songs in italics, by style name (`OP`, `OP2`,
  `ED Song`, `Opening`), so lyrics read apart from dialogue;
- one cue per line: layer copies merged, frame-by-frame repeats joined, a layer
  fragment ("Child of") dropped for the full line ("Child of a Hero"), and lines
  sharing a span joined into one cue in script order (some TV players show one
  cue at a time).

It drops Kara Effector output (one event per animated letter, marked
`Effector [fx]`) and vector drawings (`\p1`). On *Vinland Saga* S1 that removed
6,000 of the Arabic track's 6,140 events and kept its 300-odd lines of dialogue
and the translated lyrics.

```bash
# extract a track from a release, then convert
ffmpeg -v error -i episode.mkv -map 0:s:m:language:ara -c copy track.ass
ass2srt/scripts/ass2srt.py track.ass "Show - S01E01 - Title.ar.srt"
python3 -m unittest discover -s ass2srt/scripts/tests
```

## Timing

A track is timed to the video it came with. Before using it with a different
release, check the two are the same video: the dub keeper's
`dubkeeper/scripts/dubkeeper.py --check OLD NEW` lines up their audio and
prints the offset. On 2026-10-03 UQW's and iAHD's *Vinland Saga* differed by
20 ms; a TV or WEB release can differ from the Blu-ray by seconds.

## Why not keep the ASS

Styled ASS renders in the Jellyfin web and desktop players but makes most TV
apps burn it into the video, a full transcode on lab2. As a sidecar file it also
loses the fonts attached to the release, so the typesetting falls back to a
default font.
