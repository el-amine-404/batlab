#!/usr/bin/env python3
"""Convert a fansub ASS track to a readable SRT.

Keeps dialogue, signs and song lyrics; drops what only makes sense as
typesetting: Kara Effector output (one event per animated letter), styles made
of karaoke syllables, vector drawings, and the layer and frame-by-frame copies
of a single line. Song
styles (OP/ED/Song) come out in italics so lyrics read apart from dialogue.

Usage: ass2srt.py INPUT.ass [OUTPUT.srt]   (stdout without OUTPUT)
"""
import re
import sys

# Letters on neither side, so OP2 and "ED Song" match but "Default" does not.
SONG = re.compile(r"(?<![a-z])(op|ed|opening|ending|song|insert)(?![a-z])", re.I)


def ass_time(value: str) -> int:
    h, m, s = value.strip().split(":")
    return round((int(h) * 3600 + int(m) * 60 + float(s)) * 1000)


def srt_time(ms: int) -> str:
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def events(text: str):
    fields = None
    in_events = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_events = line.lower() == "[events]"
            continue
        if not in_events:
            continue
        if line.startswith("Format:"):
            fields = [f.strip().lower() for f in line[7:].split(",")]
        elif line.startswith("Dialogue:") and fields:
            values = line[9:].split(",", len(fields) - 1)
            yield dict(zip(fields, (v.strip() if k != "text" else v for k, v in zip(fields, values))))


def plain_text(raw: str) -> str:
    return re.sub(r"\{[^}]*\}", "", raw).replace("\\N", " ").replace("\\n", " ").replace("\\h", " ").strip()


def syllable_styles(all_events: list[dict]) -> set[str]:
    """Styles whose events are karaoke syllables ("te", "tsu", "no"): many
    events, most of them three characters or fewer. Attack on Titan S04's MTBB
    ending had 4,151 such events, unmarked as effects."""
    lengths: dict[str, list[int]] = {}
    for event in all_events:
        text = plain_text(event.get("text", ""))
        if text:
            lengths.setdefault(event.get("style", ""), []).append(len(text))
    return {style for style, values in lengths.items()
            if len(values) >= 40 and sorted(values)[len(values) // 2] <= 3}


def convert(text: str) -> list[tuple[int, int, str]]:
    cues = set()
    order: dict[str, int] = {}
    all_events = list(events(text))
    syllables = syllable_styles(all_events)
    for index, event in enumerate(all_events):
        raw = event.get("text", "")
        if "fx" in event.get("effect", "").lower() or re.search(r"\\p[1-9]", raw) or event.get("style", "") in syllables:
            continue
        body = re.sub(r"\{[^}]*\}", "", raw).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
        body = "\n".join(part.strip() for part in body.splitlines() if part.strip())
        if not body:
            continue
        if SONG.search(event.get("style", "").replace("Kara", "")):
            body = "\n".join(f"<i>{part}</i>" for part in body.splitlines())
        cues.add((ass_time(event["start"]), ass_time(event["end"]), body))
        order.setdefault(body, index)

    # A sign built from layers can leave a fragment ("Child of") on screen
    # for exactly as long as the full line ("Child of a Hero"); keep the full one.
    spans: dict[tuple[int, int], list[str]] = {}
    for start, end, body in cues:
        spans.setdefault((start, end), []).append(body)
    cues = {(start, end, body) for start, end, body in cues
            if not any(body != other and body in other for other in spans[(start, end)])}

    merged: list[list] = []
    for start, end, body in sorted(cues, key=lambda cue: (cue[0], order[cue[2]], cue[1])):
        # A sign tracked frame by frame, or a line split at a cut, arrives as
        # back-to-back copies of the same text; join them into one cue.
        for cue in reversed(merged[-8:]):
            if cue[2] == body and start <= cue[1] + 50:
                cue[1] = max(cue[1], end)
                break
        else:
            merged.append([start, end, body])

    # Lines sharing one span go in one cue: some TV players show a single cue
    # at a time, and stacking order is otherwise up to the player.
    joined: dict[tuple[int, int], list[str]] = {}
    for start, end, body in merged:
        if end > start:
            joined.setdefault((start, end), []).append(body)
    return sorted((start, end, "\n".join(bodies)) for (start, end), bodies in joined.items())


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    source = open(sys.argv[1], encoding="utf-8-sig").read()
    out = sys.stdout if len(sys.argv) < 3 else open(sys.argv[2], "w", encoding="utf-8")
    for index, (start, end, body) in enumerate(convert(source), 1):
        out.write(f"{index}\n{srt_time(start)} --> {srt_time(end)}\n{body}\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
