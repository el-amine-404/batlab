#!/usr/bin/env python3

"""Flag files whose real content is not the kind of file the library expects."""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, asdict

BATCH = 400

# Extensions that have no business in a media tree. Checked by name as well as
# by content, because `file` cannot identify a truncated or empty carrier and
# reports it as data.
DANGEROUS_SUFFIXES = frozenset((
    ".exe", ".scr", ".bat", ".cmd", ".com", ".pif", ".msi", ".js", ".jse", ".vbs", ".vbe",
    ".ps1", ".psm1", ".jar", ".lnk", ".sh", ".run", ".app", ".dll", ".sys", ".reg",
    ".hta", ".wsf", ".cpl", ".gadget", ".sct",
))

# Incomplete downloads are renamed once they finish, so judging them is noise.
PARTIAL_SUFFIXES = frozenset((".part", ".!qb", ".!ut", ".tmp", ".crdownload", ".partial", ".parts"))

EXECUTABLE_MIMES = frozenset((
    "application/x-dosexec",
    "application/vnd.microsoft.portable-executable",
    "application/x-msdownload",
    "application/x-executable",
    "application/x-pie-executable",
    "application/x-sharedlib",
    "application/x-mach-binary",
    "application/x-elf",
    "application/java-archive",
    "application/vnd.android.package-archive",
    "application/x-msi",
))

SCRIPT_MIMES = frozenset((
    "text/x-shellscript",
    "text/x-msdos-batch",
    "application/x-shellscript",
    "text/x-perl",
    "text/x-python",
    "application/x-powershell",
))

ARCHIVE_MIMES = frozenset((
    "application/zip",
    "application/x-rar",
    "application/vnd.rar",
    "application/x-7z-compressed",
    "application/x-iso9660-image",
    "application/x-tar",
))

# What each family of extensions must actually turn out to be.
EXPECTED_PREFIX = {
    "video": ("video/", "application/mp4", "application/x-matroska"),
    "audio": ("audio/", "application/ogg"),
    "image": ("image/",),
    "text": ("text/", "application/json", "application/xml", "application/x-subrip"),
    "document": ("application/pdf", "text/", "application/epub+zip", "application/x-mobipocket-ebook"),
}

EXTENSION_FAMILY = {}
for family, suffixes in (
    ("video", (".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".m2ts", ".wmv", ".flv", ".webm", ".mpg", ".mpeg")),
    ("audio", (".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma", ".m4b")),
    ("image", (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".dng", ".tif", ".tiff", ".bmp", ".avif")),
    ("text", (".srt", ".ass", ".ssa", ".vtt", ".nfo", ".txt", ".md", ".json", ".xml", ".yml", ".yaml", ".log", ".cue")),
    ("document", (".pdf", ".epub", ".mobi", ".azw3")),
):
    for suffix in suffixes:
        EXTENSION_FAMILY[suffix] = family


@dataclass
class Finding:
    path: str
    size: int
    suffix: str
    mime: str
    problems: list = field(default_factory=list)


def mime_types(paths):
    results = []
    for start in range(0, len(paths), BATCH):
        chunk = paths[start:start + BATCH]
        completed = subprocess.run(
            ["file", "--mime-type", "--brief", "--dereference", "--separator", "", "--", *chunk],
            capture_output=True, check=False,
        )
        lines = completed.stdout.decode("utf-8", "replace").splitlines()
        if len(lines) != len(chunk):
            lines = ["application/octet-stream"] * len(chunk)
        results.extend(line.strip() for line in lines)
    return results


def classify(path, size, mime, ignored_suffixes):
    suffix = os.path.splitext(path)[1].lower()
    finding = Finding(path=path, size=size, suffix=suffix, mime=mime)

    if suffix in ignored_suffixes or suffix in PARTIAL_SUFFIXES:
        return finding

    if suffix in DANGEROUS_SUFFIXES:
        finding.problems.append(f"DANGEROUS_NAME: {suffix} does not belong in a media tree")

    if mime in EXECUTABLE_MIMES:
        finding.problems.append(f"EXECUTABLE: content is {mime}")
    elif mime in SCRIPT_MIMES:
        finding.problems.append(f"SCRIPT: content is {mime}")
    elif mime in ARCHIVE_MIMES:
        finding.problems.append(f"ARCHIVE: content is {mime}")

    family = EXTENSION_FAMILY.get(suffix)
    if family and not mime.startswith(EXPECTED_PREFIX[family]):
        finding.problems.append(f"MISMATCH: {suffix} should be {family} but content is {mime}")
    elif not family and not finding.problems:
        finding.problems.append(f"UNEXPECTED: {suffix or 'no extension'} is not an expected library file type")

    return finding


def walk(roots, excludes):
    for root in roots:
        # The watcher hands us one finished file at a time, not a tree.
        if os.path.isfile(root):
            try:
                yield root, os.path.getsize(root)
            except OSError:
                pass
            continue
        for directory, subdirectories, names in os.walk(root):
            subdirectories[:] = [
                d for d in subdirectories if os.path.join(directory, d) not in excludes
            ]
            for name in names:
                path = os.path.join(directory, name)
                try:
                    if os.path.islink(path):
                        continue
                    yield path, os.path.getsize(path)
                except OSError:
                    continue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--report")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATH")
    parser.add_argument("--ignore-suffix", action="append", default=[], metavar=".EXT",
                        help="treat this extension as expected without checking content (repeatable)")
    parser.add_argument("--quiet", action="store_true")
    arguments = parser.parse_args()

    excludes = [os.path.normpath(e) for e in arguments.exclude]
    ignored = frozenset(s.lower() if s.startswith(".") else "." + s.lower() for s in arguments.ignore_suffix)

    entries = sorted(walk(arguments.roots, excludes))
    if not entries:
        print("No files to inspect.")
        return 0

    paths = [path for path, _ in entries]
    findings = [
        classify(path, size, mime, ignored)
        for (path, size), mime in zip(entries, mime_types(paths))
    ]

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
