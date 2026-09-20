#!/usr/bin/env python3

"""Judge what a container carries inside it, not what it says it carries.

The other checks ask whether a file is the kind of file its name claims. They
say nothing about its contents. A Matroska container holds fonts, cover art and
subtitle tracks, and every one of those is later parsed by something — freetype,
an image decoder, libass — that has never been told to distrust it.

`verify-media.py` does look at attachments, but it reads the `mimetype` tag,
which is a string the file supplies about itself. Declaring `font/ttf` over
arbitrary bytes is enough to pass it. Cover art carried as an attached picture
is not even reachable from that check, because it arrives as a video stream
rather than an attachment.

This extracts the embedded parts and judges them on their bytes. Extraction
reads the container, so this belongs in `deep.sh`, which reads it anyway, and in
`watch.sh`, which sees one new file at a time. It is not part of the nightly
sweep.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CONTAINER_SUFFIXES = frozenset((".mkv", ".mp4", ".mov", ".m4v", ".webm", ".ts", ".m2ts"))

# Text subtitle codecs worth reading. Image subtitles (PGS, VOBSUB) are bitmaps
# and carry no markup for the renderer to act on.
TEXT_SUBTITLE_CODECS = frozenset(("ass", "ssa", "subrip", "srt", "webvtt", "text", "mov_text"))
SUBTITLE_SUFFIX = {"ass": ".ass", "ssa": ".ssa", "subrip": ".srt", "srt": ".srt",
                   "webvtt": ".vtt", "text": ".srt", "mov_text": ".srt"}

FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"ttcf", b"wOFF", b"wOF2", b"true", b"typ1")

# An attachment nothing should be carrying, whatever it calls itself.
DENIED_SUFFIXES = frozenset(
    (".exe", ".scr", ".bat", ".cmd", ".com", ".pif", ".msi", ".js", ".vbs", ".ps1",
     ".jar", ".lnk", ".sh", ".dll", ".sys", ".reg", ".hta", ".apk", ".elf", ".so")
)

MAX_EXTRACT_BYTES = 33_554_432

EICAR = r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@dataclass
class Finding:
    path: str
    problems: list = field(default_factory=list)
    attachments: int = 0
    cover_art: int = 0
    subtitles: int = 0


def load_subtitle_checks():
    """Reuse verify-subtitles.py rather than keeping a second copy of its patterns."""
    source = os.path.join(SCRIPT_DIR, "verify-subtitles.py")
    spec = importlib.util.spec_from_file_location("verify_subtitles", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ffprobe_streams(path, timeout):
    completed = subprocess.run(
        ("ffprobe", "-v", "error", "-show_streams", "-print_format", "json", "-i", path),
        capture_output=True, timeout=timeout, check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        return json.loads(completed.stdout).get("streams") or []
    except json.JSONDecodeError:
        return None


def run_ffmpeg(arguments, timeout):
    try:
        completed = subprocess.run(("ffmpeg", "-nostdin", "-v", "error", *arguments),
                                   capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False
    return completed.returncode == 0


def content_type(path):
    completed = subprocess.run(("file", "--mime-type", "--brief", "--dereference", "--", path),
                               capture_output=True, check=False)
    return completed.stdout.decode("utf-8", "replace").strip()


def trailing_bytes(data):
    """Bytes after the end of the image, where a second payload is usually hidden."""
    if data[:2] == b"\xff\xd8":
        end = data.rfind(b"\xff\xd9")
        return len(data) - end - 2 if end >= 0 else 0
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        end = data.rfind(b"IEND\xaeB`\x82")
        return len(data) - end - 8 if end >= 0 else 0
    return 0


class Clamav:
    """clamd, with the same precondition scan-clamav.sh uses.

    An engine that cannot read what it is handed calls everything clean, which
    is indistinguishable from a container with nothing in it.
    """

    def __init__(self, enabled):
        self.usable = False
        self.reason = "not requested"
        if not enabled:
            return
        if not shutil.which("clamdscan"):
            self.reason = "clamdscan is not installed"
            return
        with tempfile.TemporaryDirectory() as directory:
            probe = os.path.join(directory, "eicar.com")
            with open(probe, "w", encoding="ascii") as handle:
                handle.write(EICAR)
            os.chmod(probe, 0o644)
            if "FOUND" in self.scan_raw(probe):
                self.usable = True
                self.reason = ""
            else:
                self.reason = "clamd did not detect the EICAR probe"

    @staticmethod
    def scan_raw(path):
        completed = subprocess.run(("clamdscan", "--fdpass", "--no-summary", "--infected", path),
                                   capture_output=True, check=False)
        return completed.stdout.decode("utf-8", "replace")

    def signature(self, path):
        if not self.usable:
            return None
        output = self.scan_raw(path)
        if "FOUND" not in output:
            return None
        return output.rsplit(": ", 1)[-1].replace(" FOUND", "").strip()


def inspect_blob(path, declared, kind, clamav, finding):
    """Judge one extracted file on its bytes."""
    actual = content_type(path)
    label = f"{kind} {os.path.basename(path)!r}"

    signature = clamav.signature(path)
    if signature:
        finding.problems.append(f"MALWARE: {label} matches {signature}")

    if declared and actual and not actual.startswith(declared.split("/", 1)[0] + "/"):
        finding.problems.append(
            f"MIME_LIE: {label} declares {declared} but its content is {actual}")

    suffix = os.path.splitext(path)[1].lower()
    if suffix in DENIED_SUFFIXES:
        finding.problems.append(f"DANGEROUS_ATTACHMENT: {label} has no business inside a container")

    with open(path, "rb") as handle:
        head = handle.read(8)
        handle.seek(0)
        data = handle.read(MAX_EXTRACT_BYTES)

    if declared.startswith("font/") or "font" in declared or suffix in (".ttf", ".otf", ".ttc"):
        if not head.startswith(FONT_MAGIC):
            finding.problems.append(
                f"NOT_A_FONT: {label} is declared a font but carries no font header")

    if kind == "cover art":
        if not actual.startswith("image/"):
            finding.problems.append(f"NOT_AN_IMAGE: {label} is {actual or 'unidentifiable'}")
        extra = trailing_bytes(data)
        if extra > 0:
            finding.problems.append(
                f"APPENDED_DATA: {label} carries {extra} byte(s) after the end of the image")


def extract_attachments(path, streams, directory, timeout):
    """ffmpeg indexes -dump_attachment by position among attachment streams."""
    extracted = []
    attachments = [s for s in streams if s.get("codec_type") == "attachment"]
    for index, stream in enumerate(attachments):
        tags = stream.get("tags") or {}
        name = os.path.basename(tags.get("filename") or f"attachment-{index}")
        target = os.path.join(directory, f"att-{index}-{name}")
        if run_ffmpeg((f"-dump_attachment:t:{index}", target, "-i", path), timeout) or os.path.exists(target):
            if os.path.exists(target):
                extracted.append((target, tags.get("mimetype", ""), "attachment"))
    return extracted


def extract_streams(path, streams, directory, timeout):
    extracted = []
    for stream in streams:
        index = stream.get("index")
        codec_type = stream.get("codec_type")
        codec = (stream.get("codec_name") or "").lower()
        attached = (stream.get("disposition") or {}).get("attached_pic")

        if codec_type == "video" and attached:
            tags = stream.get("tags") or {}
            suffix = ".png" if codec == "png" else ".jpg"
            target = os.path.join(directory, f"cover-{index}{suffix}")
            if run_ffmpeg(("-i", path, "-map", f"0:{index}", "-c", "copy",
                           "-frames:v", "1", "-y", target), timeout) and os.path.exists(target):
                extracted.append((target, tags.get("mimetype", "image/"), "cover art"))

        elif codec_type == "subtitle" and codec in TEXT_SUBTITLE_CODECS:
            target = os.path.join(directory, f"subtitle-{index}{SUBTITLE_SUFFIX.get(codec, '.srt')}")
            if run_ffmpeg(("-i", path, "-map", f"0:{index}", "-c", "copy", "-y", target), timeout) \
                    and os.path.exists(target):
                extracted.append((target, "text/plain", "subtitle track"))
    return extracted


def verify(path, clamav, subtitle_checks, timeout):
    finding = Finding(path=path)
    streams = ffprobe_streams(path, timeout)
    if streams is None:
        finding.problems.append("UNREADABLE: ffprobe could not list the streams")
        return finding

    finding.attachments = sum(1 for s in streams if s.get("codec_type") == "attachment")
    finding.cover_art = sum(1 for s in streams
                            if s.get("codec_type") == "video" and (s.get("disposition") or {}).get("attached_pic"))
    finding.subtitles = sum(1 for s in streams if s.get("codec_type") == "subtitle"
                            and (s.get("codec_name") or "").lower() in TEXT_SUBTITLE_CODECS)

    if not (finding.attachments or finding.cover_art or finding.subtitles):
        return finding

    with tempfile.TemporaryDirectory(prefix="mediascan-embedded-") as directory:
        extracted = extract_attachments(path, streams, directory, timeout)
        extracted += extract_streams(path, streams, directory, timeout)

        for target, declared, kind in extracted:
            if os.path.getsize(target) > MAX_EXTRACT_BYTES:
                finding.problems.append(f"OVERSIZE: {kind} is larger than {MAX_EXTRACT_BYTES} bytes")
                continue
            if kind == "subtitle track":
                inner = subtitle_checks.verify(target)
                for problem in inner.problems:
                    # NO_CUES on a demuxed track means the demux produced nothing,
                    # not that a player is being handed something odd.
                    if not problem.startswith("NO_CUES"):
                        finding.problems.append(f"{problem} (embedded track)")
                signature = clamav.signature(target)
                if signature:
                    finding.problems.append(f"MALWARE: subtitle track matches {signature}")
            else:
                inspect_blob(target, declared, kind, clamav, finding)

    return finding


def walk(roots, excludes):
    for root in roots:
        if os.path.isfile(root):
            if os.path.splitext(root)[1].lower() in CONTAINER_SUFFIXES:
                yield root
            continue
        for directory, subdirectories, names in os.walk(root):
            subdirectories[:] = [d for d in subdirectories
                                 if os.path.join(directory, d) not in excludes]
            for name in names:
                if os.path.splitext(name)[1].lower() in CONTAINER_SUFFIXES:
                    yield os.path.join(directory, name)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--report")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATH")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--time-budget", type=int, default=0,
                        help="stop starting new containers after this many seconds (0 means no limit)")
    parser.add_argument("--no-clamav", action="store_true",
                        help="skip the malware scan of extracted parts")
    parser.add_argument("--quiet", action="store_true")
    arguments = parser.parse_args()

    for command in ("ffprobe", "ffmpeg", "file"):
        if not shutil.which(command):
            print(f"Required command not found: {command}", file=sys.stderr)
            return 2

    clamav = Clamav(not arguments.no_clamav)
    if not arguments.no_clamav and not clamav.usable:
        print(f"Refusing to report embedded content clean: {clamav.reason}.", file=sys.stderr)
        return 2

    subtitle_checks = load_subtitle_checks()
    excludes = [os.path.normpath(e) for e in arguments.exclude]
    paths = sorted(walk(arguments.roots, excludes))
    if not paths:
        print("No containers to inspect.")
        return 0

    findings = []
    started = time.monotonic()
    budget_hit = False
    for path in paths:
        if arguments.time_budget and time.monotonic() - started > arguments.time_budget:
            budget_hit = True
            break
        try:
            findings.append(verify(path, clamav, subtitle_checks, arguments.timeout))
        except (OSError, subprocess.TimeoutExpired) as error:
            findings.append(Finding(path=path, problems=[f"UNREADABLE: {error}"]))

    flagged = [f for f in findings if f.problems]

    if arguments.report:
        os.makedirs(os.path.dirname(arguments.report) or ".", exist_ok=True)
        with open(arguments.report, "w", encoding="utf-8") as handle:
            for finding in findings:
                handle.write(json.dumps(asdict(finding)) + "\n")

    carried = sum(f.attachments + f.cover_art + f.subtitles for f in findings)
    for finding in findings:
        if arguments.quiet and not finding.problems:
            continue
        status = "FLAG" if finding.problems else "ok  "
        print(f"{status} {finding.path}")
        for problem in finding.problems:
            print(f"       {problem}")

    print(f"\nInspected {len(findings)} container(s) carrying {carried} embedded part(s); "
          f"{len(flagged)} flagged.")
    if budget_hit:
        print(f"Stopped early: time budget of {arguments.time_budget}s reached "
              f"with {len(paths) - len(findings)} container(s) unread.")
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
