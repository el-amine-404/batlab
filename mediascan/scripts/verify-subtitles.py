#!/usr/bin/env python3

"""Check that subtitle files are plain timed text and carry no active content."""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict

TEXT_SUFFIXES = frozenset((".srt", ".ass", ".ssa", ".vtt", ".sbv", ".smi", ".ttml"))
BINARY_SUFFIXES = frozenset((".sub", ".idx"))

MAX_TEXT_BYTES = 1_048_576
MAX_BINARY_BYTES = 26_214_400
MAX_CUES = 200_000

# Players render a small amount of markup in subtitles. Styling is expected;
# anything that can fetch or execute is not.
ACTIVE_CONTENT = re.compile(
    rb"<\s*(script|iframe|object|embed|applet|meta|link)\b"
    rb"|javascript\s*:"
    rb"|data\s*:\s*text/html"
    rb"|\bon(error|load|click|mouseover)\s*="
    rb"|<\?php",
    re.IGNORECASE,
)

# [Fonts] and [Graphics] let an ASS file carry UUEncoded binaries inline.
ASS_EMBEDDED = re.compile(rb"^\s*\[(Fonts|Graphics)\]", re.IGNORECASE | re.MULTILINE)

SRT_TIMING = re.compile(rb"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}")
VTT_TIMING = re.compile(rb"\d{1,2}:\d{2}[:.]\d{2}[.,]\d{1,3}\s*-->")
ASS_DIALOGUE = re.compile(rb"^\s*(Dialogue|Format)\s*:", re.IGNORECASE | re.MULTILINE)

HTML_DOCUMENT = re.compile(rb"^\s*(<!DOCTYPE\s+html|<html\b)", re.IGNORECASE)

ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


@dataclass
class Finding:
    path: str
    size: int
    problems: list = field(default_factory=list)
    encoding: str = ""
    cues: int = 0


def decode(data):
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None, ""


def verify(path):
    finding = Finding(path=path, size=os.path.getsize(path))
    suffix = os.path.splitext(path)[1].lower()

    if suffix in BINARY_SUFFIXES:
        if finding.size > MAX_BINARY_BYTES:
            finding.problems.append(f"OVERSIZE: {finding.size} bytes exceeds {MAX_BINARY_BYTES}")
        return finding

    if finding.size > MAX_TEXT_BYTES:
        finding.problems.append(f"OVERSIZE: {finding.size} bytes exceeds {MAX_TEXT_BYTES}")
        return finding

    with open(path, "rb") as handle:
        data = handle.read()

    if b"\x00" in data:
        finding.problems.append("BINARY: NUL byte in a file that should be text")
        return finding

    text, encoding = decode(data)
    finding.encoding = encoding
    if text is None:
        finding.problems.append("UNDECODABLE: not valid text in any expected encoding")
        return finding

    if HTML_DOCUMENT.match(data):
        finding.problems.append("NOT_SUBTITLE: file is an HTML document, likely a provider error page")

    match = ACTIVE_CONTENT.search(data)
    if match:
        finding.problems.append(f"ACTIVE_CONTENT: contains {match.group(0).decode('ascii', 'replace')!r}")

    if suffix in (".ass", ".ssa"):
        if ASS_EMBEDDED.search(data):
            finding.problems.append("EMBEDDED: [Fonts] or [Graphics] section carries inline binary data")
        finding.cues = len(ASS_DIALOGUE.findall(data))
    elif suffix == ".vtt":
        finding.cues = len(VTT_TIMING.findall(data))
    else:
        finding.cues = len(SRT_TIMING.findall(data))

    if finding.cues == 0:
        finding.problems.append("NO_CUES: no timing lines found, file is not timed text")
    elif finding.cues > MAX_CUES:
        finding.problems.append(f"CUES: {finding.cues} cues exceeds {MAX_CUES}")

    return finding


def walk(roots, excludes):
    suffixes = TEXT_SUFFIXES | BINARY_SUFFIXES
    for root in roots:
        # The watcher hands us one finished file at a time, not a tree.
        if os.path.isfile(root):
            if os.path.splitext(root)[1].lower() in suffixes:
                yield root
            continue
        for directory, subdirectories, names in os.walk(root):
            subdirectories[:] = [
                d for d in subdirectories if os.path.join(directory, d) not in excludes
            ]
            for name in names:
                if os.path.splitext(name)[1].lower() in suffixes:
                    yield os.path.join(directory, name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--report")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATH")
    parser.add_argument("--quiet", action="store_true")
    arguments = parser.parse_args()

    excludes = [os.path.normpath(e) for e in arguments.exclude]
    paths = sorted(walk(arguments.roots, excludes))
    if not paths:
        print("No subtitle files to inspect.")
        return 0

    findings = []
    for path in paths:
        try:
            findings.append(verify(path))
        except OSError as error:
            findings.append(Finding(path=path, size=0, problems=[f"UNREADABLE: {error}"]))

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
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
