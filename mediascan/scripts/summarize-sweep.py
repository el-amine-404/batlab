#!/usr/bin/env python3
"""Summarize the sweep's current JSONL reports for a bounded notification.

Every verdict has a class: a threat (the file has no business in the library;
sweep.sh quarantines it), an integrity problem (the file is damaged or is not
what it claims), or a quality note (a short clip, a silent track, a low
bitrate). conf/accepted.txt names the places where a quality note is expected,
a disc's menus in a release's Extras, and those stop failing the sweep. They
are still in the reports and still counted here. A threat or an integrity
verdict can never be accepted: the file says so and the sweep refuses it.
"""

import argparse
from collections import Counter, defaultdict
import fnmatch
import json
from pathlib import Path


CHECKS = {"types": "File types", "media": "Video", "subtitles": "Subtitles",
          "clamav": "ClamAV", "yara": "YARA", "virustotal": "VirusTotal"}
URGENT = {"MALWARE", "EXECUTABLE", "SCRIPT", "ARCHIVE", "ACTIVE_CONTENT", "EMBEDDED",
          "BINARY", "NOT_SUBTITLE", "ATTACHMENT", "NOT_VIDEO", "DANGEROUS_NAME"}
# What a file can be and still be what it claims: the only verdicts accepted.txt may name.
QUALITY = {"SHORT", "NO_AUDIO", "BITRATE", "RESOLUTION", "CODEC", "COVER_ART_ONLY"}


def severity(code):
    return "threat" if code in URGENT else "quality" if code in QUALITY else "integrity"


def load_accepted(path):
    """[(glob, verdicts)] from accepted.txt: "<glob> <VERDICT>..." per line,
    matched against the whole path, case ignored. A line naming anything but a
    quality verdict is refused, and the sweep fails until it is fixed."""
    rules = []
    if not path:
        return rules
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        glob, *codes = line.split()
        refused = [c for c in codes if c not in QUALITY]
        if not codes or refused or not glob.startswith("/"):
            raise ValueError(f"accepted.txt line {number}: only an absolute glob and quality verdicts "
                             f"({', '.join(sorted(QUALITY))}) may be accepted, not {' '.join(refused) or 'nothing'}")
        rules.append((glob.lower(), frozenset(codes)))
    return rules


def accepted_codes(path, rules):
    lowered = path.lower()
    return frozenset().union(*(codes for glob, codes in rules if fnmatch.fnmatchcase(lowered, glob)))


def compact(text, limit):
    text = " ".join(str(text).split()).replace("`", "'").replace("@", "＠")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def scope(path):
    parts = Path(path).parts
    if "torrents" in parts and any(p.lower() == "extras" for p in parts):
        return "torrent extras"
    if "media" in parts:
        return "library"
    return "other/downloads"


def short_path(path):
    parts = Path(path).parts
    # Keep the release/season and the actual basename, not just a clipped prefix.
    for anchor in ("tv", "movies"):
        if anchor in parts:
            at = parts.index(anchor)
            if at + 2 < len(parts):
                middle = "/…/" if at + 2 < len(parts) - 1 else "/"
                return compact(parts[at + 1], 62) + middle + compact(parts[-1], 100)
    return compact(str(path), 165)


def summarize(report_dir, statuses, quarantine="report-only", quarantine_status=0, limit=1750, accepted=None):
    files = defaultdict(set)
    reasons = Counter()
    checks, errors = [], []
    for key, label in CHECKS.items():
        status = statuses.get(key, "missing")
        if status == "skipped":
            checks.append(f"{label}: skipped")
            continue
        if status == "missing":
            errors.append(f"{label}: no run status")
            continue
        count, flagged, unknown = 0, set(), 0
        errors_before = len(errors)
        try:
            with (Path(report_dir) / f"{key}.jsonl").open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    path, problems = entry["path"], entry.get("problems", [])
                    if not isinstance(path, str) or not isinstance(problems, list) or not all(isinstance(p, str) for p in problems):
                        raise ValueError("invalid report row")
                    count += 1
                    unknown += entry.get("verdict") == "unknown to VirusTotal"
                    if problems:
                        flagged.add(path)
                        files[path].update(problems)
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f"{label}: report unreadable/invalid ({compact(error, 80)})")
        if status not in ("0", "1") or (status == "1" and not flagged):
            errors.append(f"{label}: exited {status}; check journal")
        if len(errors) > errors_before:
            checks.append(f"{label}: FAILED/incomplete")
            continue
        if key in ("clamav", "yara"):
            checks.append(f"{label}: {len(flagged)} detections")
        else:
            suffix = f", {unknown} unknown" if unknown else ""
            checks.append(f"{label}: {len(flagged)}/{count} flagged{suffix}")

    try:
        rules = load_accepted(accepted)
    except (OSError, ValueError) as error:
        rules = []
        errors.append(f"accepted list: {compact(error, 200)}")
    # A file whose every verdict is accepted where it lies is counted, not raised.
    waved = {path for path, problems in files.items()
             if {p.partition(":")[0] for p in problems} <= accepted_codes(path, rules)}
    for path in waved:
        del files[path]

    for problems in files.values():
        reasons.update({p.partition(":")[0] for p in problems})
    classes = Counter(max((severity(p.partition(":")[0]) for p in problems),
                          key=("quality", "integrity", "threat").index) for problems in files.values())
    scopes = Counter(scope(path) for path in files)
    lines = [f"{len(files)} files flagged; {len(errors)} check/report errors."]
    lines.append(f"Threats {classes['threat']}; integrity {classes['integrity']}; quality {classes['quality']}; "
                 f"accepted {len(waved)} (conf/accepted.txt).")
    if reasons:
        lines.append("Reasons (can overlap): " + "; ".join(f"{key} {n}" for key, n in reasons.most_common()))
        lines.append("Location: " + "; ".join(f"{key} {n}" for key, n in scopes.items()))
    if errors:
        lines.append("Errors: " + compact("; ".join(errors), 300))

    if quarantine_status:
        lines.append(f"Quarantine: failed (exit {quarantine_status}); inspect journal.")
    else:
        eligible = sum(any(p.partition(":")[0] in URGENT for p in problems) for problems in files.values())
        lines.append(f"Quarantine: {eligible} eligible; mode={quarantine}.")
    footer = "\nChecks: " + "; ".join(checks) + f"\nReports: {report_dir}/*.jsonl"
    # Threats first, then damage, then quality notes; library files before downloads. Keep counts and report
    # location even when filenames are long or many files were flagged.
    rank = {"threat": 0, "integrity": 1, "quality": 2}
    ordered = sorted(files, key=lambda p: (min(rank[severity(x.partition(":")[0])] for x in files[p]),
                                          scope(p) != "library", p))
    shown = 0
    for path in ordered:
        example = f"- {short_path(path)}: {compact('; '.join(sorted(files[path])), 140)}"
        if len("\n".join(lines + [example])) + len(footer) + 55 > limit or shown == 3:
            break
        lines.append(example)
        shown += 1
    if len(files) > shown:
        lines.append(f"… {len(files) - shown} more files in reports.")
    text = "\n".join(lines) + footer
    if len(text) > limit:
        text = text[:limit - len(footer) - 2] + "…\n" + footer
    return text, bool(files or errors or quarantine_status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--check", action="append", default=[], metavar="NAME=EXIT_OR_SKIPPED")
    parser.add_argument("--quarantine", choices=("enabled", "report-only"), default="report-only")
    parser.add_argument("--quarantine-status", type=int, default=0)
    parser.add_argument("--accepted", help="accepted.txt: quality verdicts expected in given places")
    args = parser.parse_args()
    text, failed = summarize(args.report_dir, dict(value.split("=", 1) for value in args.check),
                             args.quarantine, args.quarantine_status, accepted=args.accepted)
    print(text)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
