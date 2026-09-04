#!/usr/bin/env python3

"""Validate that every video file is structurally a video and matches its name."""

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict

VIDEO_SUFFIXES = frozenset(
    (".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".m2ts", ".wmv", ".flv", ".webm", ".mpg", ".mpeg")
)

# Matroska attachments are normally fonts or cover art. Anything else is a file
# riding along inside the container where nothing would think to look for it.
ATTACHMENT_ALLOWED = re.compile(r"^(font/|image/|application/x-truetype-font|application/vnd\.ms-opentype)")
ATTACHMENT_DENIED_SUFFIXES = frozenset(
    (".exe", ".scr", ".bat", ".cmd", ".com", ".lnk", ".vbs", ".js", ".ps1", ".sh", ".jar", ".msi", ".dll", ".apk")
)

# Resolution is judged on width. A 2.40:1 scope film is 1920x800 and still a
# genuine 1080p release, while an anamorphic 1440x1080 is one too, so the
# comparison uses whichever of the two dimensions implies the larger frame.
RESOLUTION_CLAIMS = ((("2160p", "4k", "uhd"), 3840), (("1080p", "fhd"), 1920), (("720p",), 1280), (("480p",), 854))
RESOLUTION_TOLERANCE = 0.10

CODEC_CLAIMS = (
    (("x265", "h265", "h.265", "hevc"), frozenset(("hevc",))),
    (("x264", "h264", "h.264", "avc"), frozenset(("h264",))),
    (("xvid", "divx"), frozenset(("mpeg4", "msmpeg4v3"))),
    (("av1",), frozenset(("av1",))),
)

# A 1080p release under this rate is not the release it claims to be, whatever
# the container header says.
MIN_BITRATE_BY_WIDTH = ((3840, 4_000_000), (1920, 1_000_000), (1280, 500_000), (0, 150_000))

MIN_DURATION_SECONDS = 60.0


@dataclass
class Finding:
    path: str
    size: int
    problems: list = field(default_factory=list)
    duration: float = 0.0
    width: int = 0
    height: int = 0
    video_codec: str = ""
    audio_codecs: list = field(default_factory=list)
    attachments: list = field(default_factory=list)


def ffprobe(path, timeout):
    command = (
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "-i", path,
    )
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, f"ffprobe timed out after {timeout}s"
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        return None, detail[-1] if detail else f"ffprobe exited {completed.returncode}"
    try:
        return json.loads(completed.stdout), None
    except json.JSONDecodeError as error:
        return None, f"ffprobe emitted invalid JSON: {error}"


def claimed_width(name):
    lowered = name.lower()
    for tokens, width in RESOLUTION_CLAIMS:
        if any(token in lowered for token in tokens):
            return width
    return None


def effective_width(width, height):
    return max(width, round(height * 16 / 9))


def claimed_codecs(name):
    lowered = name.lower()
    for tokens, codecs in CODEC_CLAIMS:
        if any(token in lowered for token in tokens):
            return codecs
    return None


def minimum_bitrate(width):
    for threshold, rate in MIN_BITRATE_BY_WIDTH:
        if width >= threshold:
            return rate
    return 0


def inspect_attachments(streams):
    suspicious = []
    for stream in streams:
        if stream.get("codec_type") != "attachment":
            continue
        tags = stream.get("tags") or {}
        filename = tags.get("filename", "")
        mimetype = tags.get("mimetype", "")
        suffix = os.path.splitext(filename)[1].lower()
        if suffix in ATTACHMENT_DENIED_SUFFIXES or not ATTACHMENT_ALLOWED.match(mimetype):
            suspicious.append({"filename": filename, "mimetype": mimetype})
    return suspicious


def verify(path, timeout):
    finding = Finding(path=path, size=os.path.getsize(path))

    probe, error = ffprobe(path, timeout)
    if probe is None:
        finding.problems.append(f"UNREADABLE: {error}")
        return finding

    streams = probe.get("streams") or []
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    finding.audio_codecs = sorted({s.get("codec_name", "?") for s in audio_streams})
    finding.attachments = inspect_attachments(streams)
    if finding.attachments:
        names = ", ".join(a["filename"] or a["mimetype"] or "?" for a in finding.attachments)
        finding.problems.append(f"ATTACHMENT: non-font attachment inside container ({names})")

    if not video_streams:
        finding.problems.append("NOT_VIDEO: container holds no video stream")
        return finding

    # Cover art is a video stream too; prefer one that actually has duration.
    primary = max(video_streams, key=lambda s: int(s.get("width") or 0) * int(s.get("height") or 0))
    finding.width = int(primary.get("width") or 0)
    finding.height = int(primary.get("height") or 0)
    finding.video_codec = primary.get("codec_name", "")

    if not audio_streams:
        finding.problems.append("NO_AUDIO: container holds no audio stream")

    duration = probe.get("format", {}).get("duration")
    finding.duration = float(duration) if duration else 0.0
    if finding.duration < MIN_DURATION_SECONDS:
        finding.problems.append(f"SHORT: duration {finding.duration:.0f}s is below {MIN_DURATION_SECONDS:.0f}s")

    name = os.path.basename(path)

    expected = claimed_width(name)
    actual = effective_width(finding.width, finding.height)
    if expected and actual:
        if actual < expected * (1 - RESOLUTION_TOLERANCE):
            finding.problems.append(
                f"RESOLUTION: name claims {expected}px wide but frame is {finding.width}x{finding.height}"
            )

    expected_codecs = claimed_codecs(name)
    if expected_codecs and finding.video_codec and finding.video_codec not in expected_codecs:
        finding.problems.append(
            f"CODEC: name claims {'/'.join(sorted(expected_codecs))} but stream is {finding.video_codec}"
        )

    if finding.duration >= MIN_DURATION_SECONDS and actual:
        bitrate = finding.size * 8 / finding.duration
        floor = minimum_bitrate(actual)
        if bitrate < floor:
            finding.problems.append(
                f"BITRATE: {bitrate / 1_000_000:.2f} Mbps at {finding.width}x{finding.height}"
                f" is below {floor / 1_000_000:.2f} Mbps"
            )

    return finding


def walk(roots, excludes, newer_than):
    for root in roots:
        for directory, subdirectories, names in os.walk(root):
            subdirectories[:] = [
                d for d in subdirectories if os.path.join(directory, d) not in excludes
            ]
            for name in names:
                if os.path.splitext(name)[1].lower() not in VIDEO_SUFFIXES:
                    continue
                path = os.path.join(directory, name)
                try:
                    if newer_than and os.path.getmtime(path) <= newer_than:
                        continue
                    if os.path.getsize(path) == 0:
                        continue
                except OSError:
                    continue
                yield path


def marker_time(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--report", help="write one JSON object per inspected file to this path")
    parser.add_argument("--marker", help="only inspect files newer than this file, then touch it")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATH",
                        help="skip this directory subtree (repeatable)")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--quiet", action="store_true", help="only print files with problems")
    arguments = parser.parse_args()

    newer_than = marker_time(arguments.marker) if arguments.marker else 0.0
    excludes = [os.path.normpath(e) for e in arguments.exclude]
    paths = sorted(walk(arguments.roots, excludes, newer_than))
    if not paths:
        print("No video files to inspect.")
        return 0

    with ThreadPoolExecutor(max_workers=arguments.jobs) as pool:
        findings = list(pool.map(lambda p: verify(p, arguments.timeout), paths))

    flagged = [f for f in findings if f.problems]

    if arguments.report:
        os.makedirs(os.path.dirname(arguments.report) or ".", exist_ok=True)
        with open(arguments.report, "w", encoding="utf-8") as handle:
            for finding in findings:
                handle.write(json.dumps(asdict(finding)) + "\n")

    for finding in findings:
        if arguments.quiet and not finding.problems:
            continue
        status = "FLAG" if finding.problems else "ok  "
        print(f"{status} {finding.path}")
        for problem in finding.problems:
            print(f"       {problem}")

    print(f"\nInspected {len(findings)} file(s); {len(flagged)} flagged.")

    if arguments.marker:
        os.makedirs(os.path.dirname(arguments.marker) or ".", exist_ok=True)
        with open(arguments.marker, "w", encoding="utf-8") as handle:
            handle.write("")

    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
