#!/usr/bin/env python3

"""Move flagged files out of the library, following every hardlink to them."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time

HASH_LIMIT_BYTES = 67_108_864
MANIFEST_NAME = "manifest.jsonl"


def build_link_index(roots, minimum_links=2):
    """Map inode to every path under roots that shares it."""
    index = {}
    for root in roots:
        for directory, _, names in os.walk(root):
            for name in names:
                path = os.path.join(directory, name)
                try:
                    info = os.lstat(path)
                except OSError:
                    continue
                if info.st_nlink < minimum_links:
                    continue
                index.setdefault((info.st_dev, info.st_ino), []).append(path)
    return index


def digest(path, size):
    if size > HASH_LIMIT_BYTES:
        return ""
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            hasher.update(block)
    return hasher.hexdigest()


def destination_for(path, root, quarantine, stamp):
    relative = os.path.relpath(path, root)
    return os.path.join(quarantine, stamp, relative)


def move(path, target, apply_changes):
    if not apply_changes:
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    # Same filesystem, so this is a rename and the inode is preserved along with
    # every remaining link to it.
    shutil.move(path, target)
    try:
        os.chmod(target, 0o000)
    except OSError:
        pass


def quarantine_one(path, root, quarantine, stamp, index, reason, apply_changes):
    try:
        info = os.lstat(path)
    except OSError as error:
        return {"path": path, "error": str(error)}

    links = sorted(set(index.get((info.st_dev, info.st_ino), [])) | {path})
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "reason": reason,
        "inode": info.st_ino,
        "size": info.st_size,
        "links": links,
        "sha256": digest(path, info.st_size) if apply_changes or info.st_size <= HASH_LIMIT_BYTES else "",
        "moved": [],
    }

    for link in links:
        target = destination_for(link, root, quarantine, stamp)
        try:
            move(link, target, apply_changes)
        except OSError as error:
            record.setdefault("errors", []).append(f"{link}: {error}")
            continue
        record["moved"].append({"from": link, "to": target})

    return record


def load_jsonl(path, problem_prefixes):
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        problems = entry.get("problems") or []
        if not problems:
            continue
        if problem_prefixes and not any(p.startswith(problem_prefixes) for p in problems):
            continue
        yield entry["path"], "; ".join(problems)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--root", default="/mnt/storage/data",
                        help="library root that quarantined paths are made relative to")
    parser.add_argument("--quarantine", default="/mnt/storage/data/.quarantine")
    parser.add_argument("--scan-root", action="append", default=[], metavar="PATH",
                        help="tree to search for hardlinks (repeatable, defaults to --root)")
    parser.add_argument("--from-jsonl", metavar="FILE",
                        help="read flagged paths from a verify-media or verify-subtitles report")
    parser.add_argument("--problem", action="append", default=[], metavar="PREFIX",
                        help="only act on report entries whose problem starts with this (repeatable)")
    parser.add_argument("--reason", default="flagged by mediascan")
    parser.add_argument("--apply", action="store_true",
                        help="actually move files; without it nothing is changed")
    arguments = parser.parse_args()

    root = os.path.normpath(arguments.root)
    quarantine = os.path.normpath(arguments.quarantine)

    targets = [(os.path.normpath(p), arguments.reason) for p in arguments.paths]
    if arguments.from_jsonl:
        prefixes = tuple(arguments.problem)
        targets += [(os.path.normpath(p), r) for p, r in load_jsonl(arguments.from_jsonl, prefixes)]

    targets = [(p, r) for p, r in targets if not p.startswith(quarantine + os.sep)]

    # Several engines can flag the same file; keep one entry carrying every
    # reason rather than one entry per engine.
    merged = {}
    for path, reason in targets:
        if path in merged:
            if reason not in merged[path]:
                merged[path] += f"; {reason}"
        else:
            merged[path] = reason
    targets = list(merged.items())
    if not targets:
        print("Nothing to quarantine.")
        return 0

    scan_roots = [os.path.normpath(p) for p in arguments.scan_root] or [root]
    index = build_link_index(scan_roots)

    stamp = time.strftime("%Y-%m-%d")
    records = []
    handled = set()
    for path, reason in targets:
        # A scanner reports every hardlink it finds, but the first move already
        # took all of them, so the rest are done rather than missing.
        if path in handled:
            print(f"ALREADY {path}\n       moved with an earlier hardlink in this run")
            continue

        record = quarantine_one(path, root, quarantine, stamp, index, reason, arguments.apply)
        handled.update(record.get("links", []))
        records.append(record)

        if record.get("error"):
            print(f"SKIP {path}\n       {record['error']}")
            continue
        verb = "MOVED" if arguments.apply else "WOULD MOVE"
        extra = f" ({len(record['links'])} hardlinks)" if len(record["links"]) > 1 else ""
        print(f"{verb} {path}{extra}\n       {reason}")
        for moved in record["moved"] if arguments.apply else []:
            print(f"       -> {moved['to']}")
        for error in record.get("errors", []):
            print(f"       ERROR {error}")

    if arguments.apply:
        manifest = os.path.join(quarantine, stamp, MANIFEST_NAME)
        os.makedirs(os.path.dirname(manifest), exist_ok=True)
        with open(manifest, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        print(f"\nManifest: {manifest}")
    else:
        print(f"\n{len(records)} file(s) would be quarantined. Re-run with --apply to move them.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
