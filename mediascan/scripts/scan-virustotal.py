#!/usr/bin/env python3

"""Check small carrier files against VirusTotal by hash, never by upload."""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict

API = "https://www.virustotal.com/api/v3/files/"

# Only the SHA-256 is sent. Uploading the file would share a private library
# with VirusTotal's partners, which is not an acceptable trade for a homelab.
#
# A 404 means nobody has ever submitted this hash. That is "unknown", not
# "clean", and is deliberately not treated as a pass.

# VirusTotal earns its keep on small carriers that slipped past the other
# checks. Hashing a 60 GB remux costs minutes and returns 404 every time,
# because nobody has ever submitted your rip.
DEFAULT_MAX_BYTES = 134_217_728

CARRIER_SUFFIXES = frozenset((
    ".exe", ".scr", ".bat", ".cmd", ".com", ".pif", ".msi", ".js", ".jse", ".vbs", ".vbe",
    ".ps1", ".psm1", ".jar", ".lnk", ".sh", ".run", ".dll", ".sys", ".reg", ".hta", ".wsf",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".bin", ".dat", ".apk",
    ".srt", ".ass", ".ssa", ".sub", ".vtt", ".nfo", ".pdf",
))


@dataclass
class Finding:
    path: str
    sha256: str = ""
    problems: list = field(default_factory=list)
    verdict: str = ""


def digest(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            hasher.update(block)
    return hasher.hexdigest()


def load_cache(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(path, cache):
    if not path:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cache, handle)


def lookup(sha256, api_key, timeout=25):
    request = urllib.request.Request(API + sha256, headers={"x-apikey": api_key})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {"status": "unknown"}
        return {"status": "error", "detail": f"HTTP {error.code}"}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
        return {"status": "error", "detail": str(error)}

    stats = payload.get("data", {}).get("attributes", {}).get("last_analysis_stats", {})
    return {
        "status": "known",
        "malicious": int(stats.get("malicious", 0)),
        "suspicious": int(stats.get("suspicious", 0)),
    }


def candidates(roots, excludes, max_bytes, all_files):
    for root in roots:
        for directory, subdirectories, names in os.walk(root):
            subdirectories[:] = [
                d for d in subdirectories if os.path.join(directory, d) not in excludes
            ]
            for name in names:
                path = os.path.join(directory, name)
                suffix = os.path.splitext(name)[1].lower()
                if not all_files and suffix not in CARRIER_SUFFIXES:
                    continue
                try:
                    if os.path.islink(path) or os.path.getsize(path) > max_bytes:
                        continue
                except OSError:
                    continue
                yield path


def load_paths_from_report(path):
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)["path"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*")
    parser.add_argument("--from-jsonl", metavar="FILE", help="take paths from an existing report")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATH")
    parser.add_argument("--report")
    parser.add_argument("--cache", metavar="FILE", help="remember verdicts by hash to save quota")
    parser.add_argument("--threshold", type=int, default=3,
                        help="flag when at least this many engines call it malicious")
    parser.add_argument("--max-lookups", type=int, default=400)
    parser.add_argument("--sleep", type=float, default=16.0,
                        help="seconds between lookups; the public API allows 4 per minute")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--all-files", action="store_true",
                        help="hash every file, not only likely carriers")
    parser.add_argument("--quiet", action="store_true")
    arguments = parser.parse_args()

    api_key = os.environ.get("VT_API_KEY", "")
    if not api_key:
        print("VT_API_KEY is not set; skipping VirusTotal.", file=sys.stderr)
        return 0

    excludes = [os.path.normpath(e) for e in arguments.exclude]
    if arguments.from_jsonl:
        paths = sorted(set(load_paths_from_report(arguments.from_jsonl)))
    else:
        paths = sorted(candidates(arguments.roots, excludes, arguments.max_bytes, arguments.all_files))

    if not paths:
        print("No candidate files for VirusTotal.")
        return 0

    cache = load_cache(arguments.cache)
    findings = []
    used = 0
    unknown = 0
    errors = 0

    print(f"{len(paths)} candidate file(s); up to {arguments.max_lookups} lookup(s) this run.")

    for path in paths:
        try:
            sha256 = digest(path)
        except OSError as error:
            findings.append(Finding(path=path, problems=[f"UNREADABLE: {error}"]))
            continue

        finding = Finding(path=path, sha256=sha256)
        result = cache.get(sha256)

        if result is None:
            if used >= arguments.max_lookups:
                finding.verdict = "skipped: lookup budget spent"
                findings.append(finding)
                continue
            if used > 0:
                time.sleep(arguments.sleep)
            result = lookup(sha256, api_key)
            used += 1
            if result["status"] != "error":
                cache[sha256] = result

        if result["status"] == "known":
            malicious = result.get("malicious", 0)
            finding.verdict = f"{malicious} engine(s) malicious"
            if malicious >= arguments.threshold:
                finding.problems.append(f"MALWARE: VirusTotal {malicious} engine(s) malicious")
        elif result["status"] == "unknown":
            unknown += 1
            finding.verdict = "unknown to VirusTotal"
        else:
            errors += 1
            finding.verdict = f"inconclusive: {result.get('detail', 'error')}"

        findings.append(finding)

    save_cache(arguments.cache, cache)
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
        print(f"{status} {finding.path}\n       {finding.verdict}")
        for problem in finding.problems:
            print(f"       {problem}")

    print(f"\n{len(findings)} checked, {used} lookup(s) used, {unknown} unknown to VirusTotal, "
          f"{errors} inconclusive, {len(flagged)} flagged.")
    if unknown:
        print("Unknown means nobody has submitted that hash. It is not a clean verdict.")

    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
