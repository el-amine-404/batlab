#!/usr/bin/env python3
"""Compare repaired media files with their originals and say exactly what changed.

For each pair it reports the durations, the video that was removed and where, whether the audio was
re-encoded and how close the new audio is to the old (loudness of each second), which stretches of the
original's audio are missing or never decoded, the decoder messages before and after, and whether the metadata
(creation date, rotation, tags) survived. Video is compared packet by packet (by checksum, leaving out
parameter sets), so removed stretches are found even when they sit in the middle of a clip.

  compare-media.py OLD NEW                          compare two files
  compare-media.py --old-root DIR --new-root DIR    every media file under NEW-ROOT against the file at
                                                    the same relative path under OLD-ROOT
  add --detail for the full report of every file, --json FILE to save all results,
  --no-similarity to skip the audio loudness comparison (faster)

Each file gets a verdict:
  IDENTICAL  nothing changed
  CHANGED    the differences below are all there is, and every check passed
  CHECK      a check failed: read the FAILED lines

Checks: same streams in the same order; rotation unchanged; no tag lost or altered; the new file decodes
with no decoder messages; no video packet was altered (removing packets is reported as a change, not a
failure); the new audio is not shorter than the old audio decoded to, has no stretch missing that the
old one had, and is within a few dB of it in every second. The encoder tag (the program that wrote the
file) is listed when it changes but is not a failure.

Needs ffmpeg and ffprobe. Exit status: 0 no CHECK, 1 bad arguments or tools missing, 2 some file needs a look.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

MEDIA_EXTENSIONS = {".mp4", ".mov", ".m4v", ".3gp", ".mkv", ".avi", ".webm"}
IGNORED_TAGS = {"major_brand", "minor_version", "compatible_brands"}
STREAM_KEYS = ("codec_name", "profile", "width", "height", "pix_fmt", "sample_rate", "channels", "r_frame_rate")
WRITER_TAGS = {"encoder"}   # names the program that wrote the file: listed when it changes, but not a loss of your data
AUDIO_TOLERANCE = 0.25   # seconds of audio that may differ before it counts as lost
VIDEO_CODECS = ("h264", "hevc", "mpeg4", "mpeg2video", "vp8", "vp9", "av1", "mjpeg", "prores")


class MediaError(RuntimeError):
    """A file could not be read as media."""


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True)


def probe(path: Path) -> dict:
    result = run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    try:
        data = json.loads(result.stdout)
    except ValueError:
        data = {}
    if not data.get("streams"):
        detail = (result.stderr.strip().splitlines() or ["no streams"])[-1]
        raise MediaError(f"{path} cannot be read as media ({detail})")
    return data


def rotation(stream: dict) -> int:
    for entry in stream.get("side_data_list", []):
        if "rotation" in entry:
            return int(entry["rotation"])
    return int((stream.get("tags") or {}).get("rotate", 0))


def stream_kinds(data: dict) -> list[str]:
    return [s.get("codec_type", "?") for s in data["streams"]]


def first_of(data: dict, kind: str) -> dict | None:
    return next((s for s in data["streams"] if s.get("codec_type") == kind), None)


@dataclass
class Packet:
    start: float
    length: float
    md5: str


# Tools that join or cut files may add parameter sets and SEI notes in front of key frames. They are
# not picture data, so they are left out of the checksum: only the coded slices are compared.
PARAMETER_UNITS = {"h264": "6|7|8", "hevc": "32|33|34|39|40"}


def packets(path: Path, kind: str, codec: str | None = None) -> tuple[list[Packet], bool]:
    """Every packet of the first stream of a kind with its checksum, without decoding anything.

    The flag says whether the unit filter ran without complaint. When it complains (a damaged packet
    is dropped by it, which would hide the damage), the caller compares the raw packets instead.
    """
    spec = {"video": "0:v:0", "audio": "0:a:0"}[kind]
    command = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", spec, "-c", "copy"]
    filtered = kind == "video" and codec in PARAMETER_UNITS
    if filtered:
        command += ["-bsf:v", f"filter_units=remove_types={PARAMETER_UNITS[codec]}"]
    result = run(command + ["-f", "framemd5", "-"])
    scale, found = 1.0, []
    for line in result.stdout.splitlines():
        match = re.match(r"#tb 0: (\d+)/(\d+)", line)
        if match:
            scale = int(match[1]) / int(match[2])
        elif line and line[0].isdigit():
            fields = [f.strip() for f in line.split(",")]
            found.append(Packet(int(fields[2]) * scale, int(fields[3]) * scale, fields[5]))
    return found, not (filtered and result.stderr.strip())


def video_packets(old_path: Path, new_path: Path, codec: str | None) -> tuple[list[Packet], list[Packet]]:
    """Both files' video packets, compared without parameter sets unless that filter had any trouble."""
    (old, old_ok), (new, new_ok) = packets(old_path, "video", codec), packets(new_path, "video", codec)
    if old_ok and new_ok:
        return old, new
    return packets(old_path, "video")[0], packets(new_path, "video")[0]


def message_kind(line: str) -> str:
    source = re.match(r"\[([^\]@]+?) @", line.strip())
    name = source[1] if source else ""
    if name.startswith(VIDEO_CODECS):
        return "video"
    if name.startswith(("aac", "mp3", "ac3", "opus", "vorbis", "pcm", "aist")):
        return "audio"
    return "other"


def meaningful(lines: list[str]) -> list[str]:
    """Decoder messages that count: ffmpeg's harmless timestamp warning and its repeat lines are dropped."""
    kept, after_ignored = [], False
    for line in lines:
        if not line.strip():
            continue
        if ("[null @" in line and "non monotonically increasing dts" in line) or \
                (line.strip().startswith("Last message repeated") and after_ignored):
            after_ignored = True
        else:
            kept.append(line.strip())
            after_ignored = False
    return kept


@dataclass
class Decode:
    frames: int
    seconds: float
    messages: list[str]


def decode(path: Path, kind: str) -> Decode:
    spec = {"video": "0:v:0", "audio": "0:a:0"}[kind]
    result = run(["ffmpeg", "-nostdin", "-v", "error", "-progress", "pipe:1", "-nostats", "-i", str(path),
                  "-map", spec, "-f", "null", "-"])
    frames = seconds = 0
    for line in result.stdout.splitlines():
        if line.startswith("frame="):
            frames = int(line.split("=")[1] or 0)
        elif line.startswith("out_time_us="):
            value = line.split("=")[1]
            seconds = int(value) / 1e6 if value.lstrip("-").isdigit() else seconds
    wanted = ("video", "other") if kind == "video" else ("audio", "other")
    return Decode(frames, max(seconds, 0.0), [m for m in meaningful(result.stderr.splitlines()) if message_kind(m) in wanted])


LEVEL_FILTER = ("aresample=async=1:first_pts=0,aresample=16000,asetnsamples=n=16000:p=0,astats=metadata=1:reset=1,"
                "ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-")
SILENCE_DB = -60.0      # seconds quieter than this in both files say nothing about similarity
LEVEL_TOLERANCE = 3.0   # dB a second may differ before the audio counts as altered


def audio_levels(path: Path) -> list[float]:
    """Loudness (RMS, dB) of each whole second of the first audio stream, placed on the file's own timeline.

    The last window is left out: it holds whatever remains, often a few milliseconds of encoder padding.
    """
    result = run(["ffmpeg", "-nostdin", "-v", "quiet", "-i", str(path), "-map", "0:a:0", "-af", LEVEL_FILTER, "-f", "null", "-"])
    return [float(v) for v in re.findall(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+|-?inf)", result.stdout)][:-1]


def audio_similarity(old: Path, new: Path) -> dict | None:
    """How far the loudness of each second of the new audio is from the old, in dB (None if it cannot be told).

    Loudness is used instead of a sample-by-sample difference: re-encoding shifts the signal by a few
    samples, which ruins a sample comparison even though nothing audible changed.
    """
    pairs = [(x, y) for x, y in zip(audio_levels(old), audio_levels(new)) if max(x, y) > SILENCE_DB]
    if not pairs:
        return None
    gaps = [abs(x - y) for x, y in pairs]
    return {"seconds": len(pairs), "max_db": max(gaps), "mean_db": sum(gaps) / len(gaps)}


GAP_MIN = 0.1   # seconds of missing audio that count as lost; shorter holes are ordinary timestamp jitter


def audio_gaps(path: Path, declared_end: float) -> list[tuple[float, float]]:
    """Stretches of the first audio stream that do not decode, found as holes between decoded frames.

    declared_end is where the file says the audio runs to; whatever is missing after the last decoded frame counts too.
    """
    result = run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-map", "0:a:0", "-af", "ashowinfo", "-f", "null", "-"])
    frames = []
    for line in result.stderr.splitlines():
        if "Parsed_ashowinfo" in line and " n:" in line:
            start, count, rate = (re.search(pattern, line) for pattern in (r"pts_time:(-?[\d.]+)", r"nb_samples:(\d+)", r"rate:(\d+)"))
            if start and count and rate:
                frames.append((float(start[1]), int(count[1]) / int(rate[1])))
    holes = [(t + length, following) for (t, length), (following, _) in zip(frames, frames[1:]) if following - (t + length) >= GAP_MIN]
    if frames and declared_end - (frames[-1][0] + frames[-1][1]) >= GAP_MIN:
        holes.append((frames[-1][0] + frames[-1][1], declared_end))
    return holes


def gap_text(holes: list[tuple[float, float]], limit: int = 6) -> str:
    shown = ", ".join(f"{a:.2f}-{b:.2f} s" for a, b in holes[:limit]) + (f", and {len(holes) - limit} more" if len(holes) > limit else "")
    return f"{sum(b - a for a, b in holes):.2f} s in {len(holes)} place(s): {shown}"


@dataclass
class Removed:
    start: float
    end: float
    packets: int

    @property
    def seconds(self) -> float:
        return self.end - self.start


def compare_packets(old: list[Packet], new: list[Packet]) -> tuple[list[Removed], int, int]:
    """Which old packets are missing from the new file, as time ranges; how many new packets are not from the old."""
    matcher = difflib.SequenceMatcher(None, [p.md5 for p in old], [p.md5 for p in new], autojunk=False)
    removed, added = [], 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            if i2 > i1:
                span = old[i1:i2]
                removed.append(Removed(min(p.start for p in span), max(p.start + p.length for p in span), i2 - i1))
            added += j2 - j1
    return removed, added, sum(i2 - i1 for tag, i1, i2, _, _ in matcher.get_opcodes() if tag == "equal")


def tags_of(item: dict) -> dict:
    return {k: v for k, v in (item.get("tags") or {}).items() if k not in IGNORED_TAGS}


def tag_changes(old: dict, new: dict) -> dict:
    return {"lost": sorted(k for k in old if k not in new), "added": sorted(k for k in new if k not in old),
            "changed": sorted(k for k in old if k in new and old[k] != new[k])}


@dataclass
class Result:
    name: str
    old_size: int
    new_size: int
    changes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        if self.failures:
            return "CHECK"
        return "CHANGED" if self.changes else "IDENTICAL"


def seconds_text(value: float) -> str:
    return f"{value:.2f} s"


def compare(old_path: Path, new_path: Path, *, similarity: bool = True, name: str | None = None) -> Result:
    old, new = probe(old_path), probe(new_path)
    result = Result(name or new_path.name, old_path.stat().st_size, new_path.stat().st_size)
    facts, changes, failures = result.facts, result.changes, result.failures

    # Streams and their properties
    if stream_kinds(old) != stream_kinds(new):
        failures.append(f"the streams differ: {stream_kinds(old)} became {stream_kinds(new)}")
    for kind in ("video", "audio"):
        a, b = first_of(old, kind), first_of(new, kind)
        if a is None or b is None:
            continue
        for key in STREAM_KEYS:
            if a.get(key) != b.get(key):
                changes.append(f"{kind} {key}: {a.get(key)} -> {b.get(key)}")
        if kind == "audio" and a.get("bit_rate") != b.get("bit_rate") and a.get("bit_rate") and b.get("bit_rate"):
            changes.append(f"audio bit rate: {int(a['bit_rate']) // 1000} -> {int(b['bit_rate']) // 1000} kbps")
        if rotation(a) != rotation(b):
            failures.append(f"{kind} rotation changed: {rotation(a)} -> {rotation(b)}")
    video_old, video_new = first_of(old, "video"), first_of(new, "video")
    facts["rotation"] = rotation(video_new) if video_new else None

    # Metadata: nothing may be lost or altered; anything added is reported
    pairs = [("file", tags_of(old["format"]), tags_of(new["format"]))]
    for index, (a, b) in enumerate(zip(old["streams"], new["streams"])):
        pairs.append((f"{a.get('codec_type', 'stream')} {index}", tags_of(a), tags_of(b)))
    facts["tags"] = {}
    for where, a, b in pairs:
        delta = tag_changes(a, b)
        facts["tags"][where] = delta
        for key in delta["lost"]:
            if key in WRITER_TAGS:
                changes.append(f"{where} writer tag dropped: {key} ({a[key]})")
            else:
                failures.append(f"{where} tag lost: {key} ({a[key]})")
        for key in delta["changed"]:
            if key in WRITER_TAGS:
                changes.append(f"{where} writer tag changed: {key}: {a[key]} -> {b[key]}")
            else:
                failures.append(f"{where} tag changed: {key}: {a[key]} -> {b[key]}")
        for key in delta["added"]:
            changes.append(f"{where} tag added: {key} = {b[key]}")

    # Video: packet by packet
    if video_old and video_new:
        codec = video_old.get("codec_name")
        pv_old, pv_new = video_packets(old_path, new_path, codec)
        removed, added, same = compare_packets(pv_old, pv_new)
        facts["video_packets"] = {"old": len(pv_old), "new": len(pv_new), "identical": same}
        facts["removed"] = [{"start": r.start, "end": r.end, "seconds": r.seconds, "packets": r.packets} for r in removed]
        if added:
            failures.append(f"{added} video packet(s) in the new file are not in the old one: the picture was altered")
        for piece in removed:
            changes.append(f"video removed from {seconds_text(piece.start)} to {seconds_text(piece.end)} "
                           f"({seconds_text(piece.seconds)}, {piece.packets} frames)")
        d_old, d_new = decode(old_path, "video"), decode(new_path, "video")
        facts["video"] = {"frames_old": d_old.frames, "frames_new": d_new.frames, "seconds_old": d_old.seconds,
                          "seconds_new": d_new.seconds, "messages_old": len(d_old.messages), "messages_new": len(d_new.messages)}
        if d_new.messages:
            failures.append(f"the new video still has {len(d_new.messages)} decoder message(s): {d_new.messages[0][:90]}")
        if len(d_old.messages) != len(d_new.messages):
            changes.append(f"video decoder messages: {len(d_old.messages)} -> {len(d_new.messages)}")

    # Audio: identical, or re-encoded and how close, and where it ends
    audio_old, audio_new = first_of(old, "audio"), first_of(new, "audio")
    if audio_old and audio_new:
        pa_old, pa_new = packets(old_path, "audio")[0], packets(new_path, "audio")[0]
        removed_audio, added_audio, _ = compare_packets(pa_old, pa_new)
        facts["audio_identical"] = not removed_audio and not added_audio
        # Re-encoded audio replaces every packet, so "removed" ranges only mean something when nothing was added.
        facts["audio_removed"] = [] if added_audio else [{"start": r.start, "end": r.end, "seconds": r.seconds} for r in removed_audio]
        a_old, a_new = decode(old_path, "audio"), decode(new_path, "audio")
        facts["audio"] = {"seconds_old": a_old.seconds, "seconds_new": a_new.seconds,
                          "messages_old": len(a_old.messages), "messages_new": len(a_new.messages)}
        shift = a_new.seconds - a_old.seconds
        picture_end = facts.get("video", {}).get("seconds_new") or 0.0
        old_end = pa_old[-1].start + pa_old[-1].length if pa_old else 0.0
        new_end = pa_new[-1].start + pa_new[-1].length if pa_new else 0.0
        holes_old, holes_new = audio_gaps(old_path, old_end), audio_gaps(new_path, new_end)
        facts["audio_gaps"] = {"old": holes_old, "new": holes_new}
        if holes_old:
            changes.append(f"audio missing in the old file (nothing recorded there, or it does not decode), so it is missing in the new one too: {gap_text(holes_old)}")
        lost_now = [h for h in holes_new if not any(h[0] < o[1] and o[0] < h[1] for o in holes_old)]
        if lost_now:
            failures.append(f"audio the old file still had is missing in the new one: {gap_text(lost_now)}")
        if added_audio:
            # New audio packets that are not in the old file: the audio was encoded again.
            changes.append("audio re-encoded" + (f" ({audio_old.get('codec_name')} -> {audio_new.get('codec_name')})"
                                                  if audio_old.get("codec_name") != audio_new.get("codec_name") else ""))
            if similarity:
                levels = audio_similarity(old_path, new_path)
                facts["audio_levels"] = levels
                if levels:
                    changes.append(f"audio loudness per second within {levels['max_db']:.1f} dB of the old "
                                   f"(average {levels['mean_db']:.2f} dB, {levels['seconds']} s compared)")
                    if levels["max_db"] > LEVEL_TOLERANCE:
                        failures.append(f"the new audio is up to {levels['max_db']:.1f} dB louder or quieter than the old in some seconds")
            # Audio that runs on after the last picture may be trimmed away; audio under the picture may not.
            expected = min(a_old.seconds, picture_end) if picture_end else a_old.seconds
            for start, end in holes_old:   # the old audio had nothing from start on, so the new one owes nothing there
                if start < expected <= end:
                    expected = start
            if a_new.seconds < expected - AUDIO_TOLERANCE:
                failures.append(f"the new audio is {expected - a_new.seconds:.2f} s shorter than the old audio decoded to "
                                f"(ends at {a_new.seconds:.2f} s instead of {expected:.2f} s)")
            elif shift < -AUDIO_TOLERANCE:
                changes.append(f"audio after the end of the picture removed: {seconds_text(a_new.seconds)} -> {seconds_text(a_old.seconds)} "
                               f"({abs(shift):.2f} s; the picture ends at {seconds_text(picture_end)})")
            elif abs(shift) > 0.02:
                changes.append(f"audio length {seconds_text(a_old.seconds)} -> {seconds_text(a_new.seconds)} ({shift:+.2f} s)")
        else:
            for piece in removed_audio:
                what = "audio after the end of the picture removed" if picture_end and piece.start >= picture_end - AUDIO_TOLERANCE \
                    else "audio removed"
                changes.append(f"{what} from {seconds_text(piece.start)} to {seconds_text(piece.end)} ({seconds_text(piece.seconds)})")
        if a_new.messages:
            failures.append(f"the new audio still has {len(a_new.messages)} decoder message(s): {a_new.messages[0][:90]}")
        if len(a_old.messages) != len(a_new.messages):
            changes.append(f"audio decoder messages: {len(a_old.messages)} -> {len(a_new.messages)}")
        video_seconds = facts.get("video", {}).get("seconds_new") or 0
        if video_seconds and a_new.seconds < video_seconds - AUDIO_TOLERANCE:
            facts["audio_ends_before_video"] = {"audio": a_new.seconds, "video": video_seconds}

    # Whole file
    d_old_total, d_new_total = float(old["format"].get("duration", 0)), float(new["format"].get("duration", 0))
    facts["duration"] = {"old": d_old_total, "new": d_new_total}
    if abs(d_new_total - d_old_total) > 0.02:
        changes.insert(0, f"duration {seconds_text(d_old_total)} -> {seconds_text(d_new_total)} ({d_new_total - d_old_total:+.2f} s)")
    if result.new_size != result.old_size:
        changes.append(f"size {result.old_size / 1e6:.1f} MB -> {result.new_size / 1e6:.1f} MB "
                       f"({(result.new_size - result.old_size) / max(result.old_size, 1) * 100:+.1f}%)")
    return result


def summary_line(result: Result) -> str:
    facts = result.facts
    duration = facts.get("duration", {})
    parts = [f"{duration.get('old', 0):.1f}s -> {duration.get('new', 0):.1f}s"]
    removed = sum(r["seconds"] for r in facts.get("removed", []))
    if removed:
        parts.append(f"video -{removed:.1f}s")
    audio = facts.get("audio")
    if audio and not facts.get("audio_removed") and abs(audio["seconds_new"] - audio["seconds_old"]) > 0.02:
        parts.append(f"audio {audio['seconds_new'] - audio['seconds_old']:+.2f}s")
    if audio and not facts.get("audio_identical", True) and not facts.get("audio_removed"):
        parts.append("audio re-encoded")
    if facts.get("audio_removed"):
        parts.append(f"audio -{sum(r['seconds'] for r in facts['audio_removed']):.1f}s")
    holes = facts.get("audio_gaps", {}).get("old", [])
    if holes:
        parts.append(f"audio already lost in the original {sum(b - a for a, b in holes):.1f}s")
    messages_old = facts.get("video", {}).get("messages_old", 0) + facts.get("audio", {}).get("messages_old", 0)
    messages_new = facts.get("video", {}).get("messages_new", 0) + facts.get("audio", {}).get("messages_new", 0)
    if messages_old or messages_new:
        parts.append(f"messages {messages_old}->{messages_new}")
    return ", ".join(parts)


def render_detail(result: Result) -> list[str]:
    lines = [f"{result.name}: {result.verdict}", f"    {summary_line(result)}"]
    lines += [f"    changed: {c}" for c in result.changes] or ["    nothing changed"]
    lines += [f"    FAILED:  {f}" for f in result.failures]
    facts = result.facts
    if "audio_ends_before_video" in facts:
        gap = facts["audio_ends_before_video"]
        lines.append(f"    note:    the audio ends at {gap['audio']:.2f} s but the video runs to {gap['video']:.2f} s "
                     f"(silence from {gap['audio']:.2f} s to {gap['video']:.2f} s)")
    if "video_packets" in facts:
        packets_ = facts["video_packets"]
        lines.append(f"    video:   {packets_['identical']} of {packets_['old']} packets identical, {packets_['new']} in the new file")
    lines.append(f"    metadata: {'all kept' if not any(d['lost'] or d['changed'] for d in facts.get('tags', {}).values()) else 'SEE ABOVE'}"
                 f", rotation {facts.get('rotation')}")
    return lines


def media_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS and not p.name.startswith("."))


def check_tools() -> str | None:
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    return f"needs {' and '.join(missing)} on the PATH" if missing else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="OLD NEW")
    parser.add_argument("--old-root", type=Path)
    parser.add_argument("--new-root", type=Path)
    parser.add_argument("--detail", action="store_true", help="full report for every file")
    parser.add_argument("--json", type=Path, metavar="FILE", help="also write every result to FILE")
    parser.add_argument("--no-similarity", action="store_true", help="skip the audio loudness comparison")
    args = parser.parse_args(argv)
    roots = args.old_root is not None or args.new_root is not None
    if roots and (args.old_root is None or args.new_root is None or args.files):
        parser.error("use either OLD NEW or both --old-root and --new-root")
    if not roots and len(args.files) != 2:
        parser.error("give the old file and the new file, or --old-root and --new-root")
    problem = check_tools()
    if problem:
        print(f"Error: {problem}", file=sys.stderr)
        return 1
    pairs: list[tuple[str, Path, Path]] = []
    if roots:
        if not args.new_root.is_dir() or not args.old_root.is_dir():
            print("Error: both roots must be existing folders", file=sys.stderr)
            return 1
        pairs = [(str(new.relative_to(args.new_root)), args.old_root / new.relative_to(args.new_root), new)
                 for new in media_files(args.new_root)]
        if not pairs:
            print(f"Error: no media files under {args.new_root}", file=sys.stderr)
            return 1
    else:
        pairs = [(Path(args.files[1]).name, Path(args.files[0]), Path(args.files[1]))]
    results, unreadable = [], []
    for number, (name, old, new) in enumerate(pairs, 1):
        print(f"[{number}/{len(pairs)}] {name}", file=sys.stderr, flush=True)
        try:
            if not old.is_file():
                raise MediaError(f"no original at {old}")
            results.append(compare(old, new, similarity=not args.no_similarity, name=name))
        except MediaError as error:
            unreadable.append((name, str(error)))
    detail = args.detail or len(pairs) == 1
    print()
    for result in results:
        if detail:
            print("\n".join(render_detail(result)))
        else:
            print(f"{result.verdict:9} {result.name}\n          {summary_line(result)}"
                  + "".join(f"\n          FAILED: {f}" for f in result.failures))
    for name, error in unreadable:
        print(f"UNREADABLE {name}: {error}")
    counts = {v: sum(r.verdict == v for r in results) for v in ("IDENTICAL", "CHANGED", "CHECK")}
    lost_video = sum(r["seconds"] for res in results for r in res.facts.get("removed", []))
    print(f"\n{len(results)} compared: {counts['IDENTICAL']} identical, {counts['CHANGED']} changed, {counts['CHECK']} need a check"
          + (f", {len(unreadable)} unreadable" if unreadable else "") + f". Video removed in total: {lost_video:.1f} s.")
    if args.json:
        args.json.write_text(json.dumps({"results": [{"name": r.name, "verdict": r.verdict, "changes": r.changes,
                                                       "failures": r.failures, "facts": r.facts} for r in results],
                                         "unreadable": [{"name": n, "error": e} for n, e in unreadable]},
                                        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 2 if counts["CHECK"] or unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
