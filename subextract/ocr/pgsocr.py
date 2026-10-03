#!/usr/bin/env python3
"""Turn a PGS (Blu-ray bitmap) subtitle stream into a clean SRT with Tesseract.

  pgsocr.py INPUT.sup OUTPUT.srt --lang eng [--report REPORT.json]

PGS is a sequence of display sets: a composition (which objects show, where),
palettes, run-length-encoded objects. Each shown composition becomes one cue,
from its presentation time to the next composition's. Every object is OCR'd on
its own, as dark text on white: the palette's luminance times its alpha gives
the white fill on black that Blu-ray subtitles are, inverted and padded,
doubled when its lines are small (720p, DVD sizes).

Tesseract reports a confidence for each word. The report gives the mean and the
share of words under 60; the caller decides whether that is good enough. The
SRT is written either way, for inspection.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

PCS, WDS, PDS, ODS, END = 0x16, 0x17, 0x14, 0x15, 0x80
LOW_CONFIDENCE = 60
SMALL_LINE = 32           # px per line under which an image is doubled


# --- parsing -------------------------------------------------------------------------

@dataclass
class Composition:
    pts: int                                   # 90 kHz ticks
    objects: list[tuple[int, int, int, bool]]  # object id, x, y, forced
    palette_only: bool


@dataclass
class Bitmap:
    width: int
    height: int
    pixels: bytes                              # palette indices, row-major


def segments(data: bytes):
    offset = 0
    while offset + 13 <= len(data):
        if data[offset:offset + 2] != b"PG":
            raise ValueError(f"not a PGS segment at byte {offset}")
        pts, _dts, kind, size = struct.unpack(">IIBH", data[offset + 2:offset + 13])
        yield pts, kind, data[offset + 13:offset + 13 + size]
        offset += 13 + size


def decode_rle(data: bytes, width: int, height: int) -> bytes:
    """PGS run-length coding: a nonzero byte is one pixel; 0x00 then a flag byte
    gives a run (long length if bit 6, explicit colour if bit 7) or, as 0x00 0x00,
    the end of a line."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        byte = data[i]
        i += 1
        if byte:
            out.append(byte)
            continue
        if i >= n:
            break
        flag = data[i]
        i += 1
        if flag == 0:
            row = len(out) % width if width else 0
            if row:
                out.extend(b"\0" * (width - row))
            continue
        length = flag & 0x3F
        if flag & 0x40:
            length = (length << 8) | data[i]
            i += 1
        colour = 0
        if flag & 0x80:
            colour = data[i]
            i += 1
        out.extend(bytes([colour]) * length)
    size = width * height
    return bytes(out[:size]) + b"\0" * max(0, size - len(out))


@dataclass
class Event:
    start: int
    end: int
    images: list[tuple[int, int, Bitmap, dict]] = field(default_factory=list)   # x, y, bitmap, palette


def parse(data: bytes) -> list[Event]:
    """Display periods with the bitmaps on screen during each."""
    palettes: dict[int, dict[int, tuple[int, int]]] = {}    # id -> index -> (luma, alpha)
    pending: dict[int, list] = {}                           # object id -> [width, height, rle bytes]
    bitmaps: dict[int, Bitmap] = {}
    events: list[Event] = []
    current: Event | None = None
    composition: Composition | None = None
    palette_id = 0
    for pts, kind, payload in segments(data):
        if kind == PCS:
            count = payload[10]
            palette_only, palette_id = payload[8] == 0x80, payload[9]
            objects, at = [], 11
            for _ in range(count):
                object_id, _window, flags, x, y = struct.unpack(">HBBHH", payload[at:at + 8])
                at += 16 if flags & 0x80 else 8
                objects.append((object_id, x, y, bool(flags & 0x40)))
            composition = Composition(pts, objects, palette_only)
        elif kind == PDS:
            entries = palettes.setdefault(payload[0], {})
            for at in range(2, len(payload) - 4, 5):
                index, luma, _cr, _cb, alpha = payload[at:at + 5]
                entries[index] = (luma, alpha)
        elif kind == ODS:
            object_id, _version, sequence = struct.unpack(">HBB", payload[:4])
            if sequence & 0x80:
                _length = int.from_bytes(payload[4:7], "big")
                width, height = struct.unpack(">HH", payload[7:11])
                pending[object_id] = [width, height, bytearray(payload[11:])]
            elif object_id in pending:
                pending[object_id][2].extend(payload[4:])
            if sequence & 0x40 and object_id in pending:
                width, height, rle = pending.pop(object_id)
                bitmaps[object_id] = Bitmap(width, height, decode_rle(bytes(rle), width, height))
        elif kind == END and composition is not None:
            if composition.palette_only and current is not None:
                # A fade: same objects, new palette. Keep each colour at its
                # most opaque, or a fade-in would be read at its first, faint step.
                for _x, _y, _bitmap, palette in current.images:
                    for index, (luma, alpha) in palettes.get(palette_id, {}).items():
                        if alpha >= palette.get(index, (0, -1))[1]:
                            palette[index] = (luma, alpha)
                composition = None
                continue
            if current is not None:
                current.end = composition.pts
                events.append(current)
                current = None
            shown = [(x, y, bitmaps[o]) for o, x, y, _forced in composition.objects if o in bitmaps]
            if shown:
                palette = dict(palettes.get(palette_id, {}))
                current = Event(composition.pts, composition.pts,
                                [(x, y, bitmap, palette) for x, y, bitmap in shown])
            composition = None
    if current is not None:
        current.end = current.start + 5 * 90000       # a last cue never cleared: give it 5 s
        events.append(current)
    return [e for e in events if e.end > e.start]


# --- images --------------------------------------------------------------------------

def to_pgm(bitmap: Bitmap, palette: dict[int, tuple[int, int]]) -> bytes:
    """Dark text on white, cropped to the ink, padded, scaled towards the line
    height Tesseract reads best: luminance times alpha is the white-on-black
    subtitle, then inverted."""
    lut = bytes(255 - (palette.get(i, (0, 0))[0] * palette.get(i, (0, 0))[1] // 255) for i in range(256))
    gray = bitmap.pixels.translate(lut)
    w, h = bitmap.width, bitmap.height
    rows = [gray[r * w:(r + 1) * w] for r in range(h)]
    ink = [r for r in range(h) if min(rows[r], default=255) < 160]
    if not ink:
        return b""
    cols = [c for c in range(w) if any(rows[r][c] < 160 for r in ink)]
    top, bottom, left, right = ink[0], ink[-1] + 1, cols[0], cols[-1] + 1
    rows = [row[left:right] for row in rows[top:bottom]]
    w, h = right - left, bottom - top
    # A 1080p Blu-ray line is 45-60 px, which Tesseract reads well as it is;
    # 720p and DVD-sized lines are doubled.
    lines = max(1, round(h / 55))
    scale = 2 if h / lines < SMALL_LINE else 1
    if scale != 1:
        new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
        rows = [bytes(rows[min(h - 1, int(y / scale))][min(w - 1, int(x / scale))] for x in range(new_w))
                for y in range(new_h)]
        w, h = new_w, new_h
    pad = 20
    blank = b"\xff" * (w + 2 * pad)
    body = b"".join(b"\xff" * pad + row + b"\xff" * pad for row in rows)
    return f"P5 {w + 2 * pad} {h + 2 * pad} 255\n".encode() + blank * pad + body + blank * pad


@dataclass
class Text:
    lines: list[str]
    confidences: list[float]


def tesseract(image: bytes, language: str, workdir: Path, name: str) -> Text:
    path = workdir / f"{name}.pgm"
    path.write_bytes(image)
    # tessedit_create_tsv rather than the "tsv" config name: the config files
    # live beside Debian's models, not in TESSDATA_PREFIX.
    result = subprocess.run(["tesseract", str(path), "stdout", "-l", language, "--oem", "1", "--psm", "6",
                             "-c", "tessedit_create_tsv=1"], capture_output=True, text=True, timeout=120)
    path.unlink(missing_ok=True)
    if result.returncode or not result.stdout.startswith("level"):
        raise RuntimeError(f"tesseract failed: {result.stderr.strip()[-300:] or 'no TSV output'}")
    lines: dict[tuple[int, int, int], list[str]] = {}
    confidences = []
    for row in result.stdout.splitlines()[1:]:
        fields = row.split("\t")
        if len(fields) < 12 or fields[0] != "5" or not fields[11].strip():
            continue
        block, paragraph, line = int(fields[2]), int(fields[3]), int(fields[4])
        lines.setdefault((block, paragraph, line), []).append(fields[11])
        confidences.append(float(fields[10]))
    return Text([" ".join(words) for _, words in sorted(lines.items())], confidences)


# --- cleaning ------------------------------------------------------------------------

ENGLISH_FIXES = [
    (re.compile(r"(?<![A-Za-z])\|(?![A-Za-z])"), "I"),
    (re.compile(r"\|"), "l"),
    (re.compile(r"\bl(?=('m|'ll|'ve|'d)\b)"), "I"),
    (re.compile(r"(?<![\w'])l(?=\s|[,.!?]|$)"), "I"),
    (re.compile(r"\b(?:l)(?=(f|n|t|s)\b)"), "I"),
    (re.compile(r"(?<=[a-z])0(?=[a-z])"), "o"),
    (re.compile(r"(?<=[A-Z])0(?=[A-Z])"), "O"),
]
# French sets a space before ! ? ; : so only the comma and full stop are tightened there.
TIGHTEN = re.compile(r"\s+(\.\.\.|[,.!?;:])(?=\s|$)")
TIGHTEN_FRENCH = re.compile(r"\s+(\.\.\.|[,.])(?=\s|$)")
ELLIPSIS = re.compile(r"\.\s*\.\s*\.")
COMMON = [
    (re.compile(r"[‘’`´]"), "'"),
    (re.compile(r"[“”„]|''"), '"'),
    (re.compile(r"^[-–—]\s*"), "- "),
    (re.compile(r"\s{2,}"), " "),
]


def clean_line(line: str, language: str) -> str:
    line = line.strip()
    if language == "eng":
        for pattern, replacement in ENGLISH_FIXES:
            line = pattern.sub(replacement, line)
    line = ELLIPSIS.sub("...", line)
    line = (TIGHTEN_FRENCH if language == "fra" else TIGHTEN).sub(r"\1", line)
    for pattern, replacement in COMMON:
        line = pattern.sub(replacement, line)
    return line.strip()


def srt_time(ticks: int) -> str:
    ms = ticks // 90
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--lang", default="eng", help="Tesseract language, eng, fra, ara, ita...")
    parser.add_argument("--report")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args(argv)

    events = parse(Path(args.input).read_bytes())
    with tempfile.TemporaryDirectory() as temp:
        workdir = Path(temp)
        jobs = []
        for e_index, event in enumerate(events):
            # Objects top to bottom: two-line subtitles are often two objects.
            for o_index, (x, y, bitmap, palette) in enumerate(sorted(event.images, key=lambda item: (item[1], item[0]))):
                jobs.append((e_index, o_index, to_pgm(bitmap, palette)))
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            texts = list(pool.map(lambda job: tesseract(job[2], args.lang, workdir, f"{job[0]}_{job[1]}") if job[2]
                                  else Text([], []), jobs))
    by_event: dict[int, list[Text]] = {}
    for (e_index, _o, _img), text in zip(jobs, texts):
        by_event.setdefault(e_index, []).append(text)

    cues, confidences, low_lines = [], [], []
    for e_index, event in enumerate(events):
        lines, words = [], []
        for text in by_event.get(e_index, []):
            lines += [clean_line(line, args.lang) for line in text.lines]
            words += text.confidences
        lines = [line for line in lines if line]
        if not lines:
            continue
        confidences += words
        if words and sum(words) / len(words) < LOW_CONFIDENCE:
            low_lines.append(" / ".join(lines))
        if cues and cues[-1][2] == lines and event.start - cues[-1][1] <= 9000:
            cues[-1][1] = event.end          # the same text split by a fade or a cut
        else:
            cues.append([event.start, event.end, lines])
    with open(args.output, "w", encoding="utf-8") as out:
        for n, (start, end, lines) in enumerate(cues, 1):
            end = max(end, start + 300 * 90)  # at least 0.3 s on screen
            out.write(f"{n}\n{srt_time(start)} --> {srt_time(end)}\n" + "\n".join(lines) + "\n\n")
    report = {
        "cues": len(cues),
        "words": len(confidences),
        "mean_confidence": round(sum(confidences) / len(confidences), 1) if confidences else 0.0,
        "low_confidence_share": round(sum(c < LOW_CONFIDENCE for c in confidences) / len(confidences), 3) if confidences else 1.0,
        "low_confidence_samples": low_lines[:5],
    }
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
