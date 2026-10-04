#!/usr/bin/env python3
"""Summarize the sweep's current JSONL reports for a bounded notification."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


CHECKS = {"types": "File types", "media": "Video", "subtitles": "Subtitles",
          "clamav": "ClamAV", "yara": "YARA", "virustotal": "VirusTotal"}
URGENT = {"MALWARE", "EXECUTABLE", "SCRIPT", "ARCHIVE", "ACTIVE_CONTENT", "EMBEDDED",
          "BINARY", "NOT_SUBTITLE", "ATTACHMENT", "NOT_VIDEO", "DANGEROUS_NAME"}


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


def summarize(report_dir, statuses, quarantine="report-only", quarantine_status=0, limit=1750):
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

    for problems in files.values():
        reasons.update({p.partition(":")[0] for p in problems})
    scopes = Counter(scope(path) for path in files)
    lines = [f"{len(files)} files flagged; {len(errors)} check/report errors."]
    if reasons:
        lines.append("Reasons (can overlap): " + "; ".join(f"{key} {n}" for key, n in reasons.most_common()))
        lines.append("Location: " + "; ".join(f"{key} {n}" for key, n in scopes.items()))
    if files and all(scope(path) == "torrent extras" and
                     all(p.partition(":")[0] in {"SHORT", "NO_AUDIO", "BITRATE"} for p in problems)
                     for path, problems in files.items()):
        lines.append("Extras only: these thresholds also flag legitimate menus, silent galleries and short clips.")
    if errors:
        lines.append("Errors: " + compact("; ".join(errors), 300))

    if quarantine_status:
        lines.append(f"Quarantine: failed (exit {quarantine_status}); inspect journal.")
    else:
        eligible = sum(any(p.partition(":")[0] in URGENT for p in problems) for problems in files.values())
        lines.append(f"Quarantine: {eligible} eligible; mode={quarantine}.")
    footer = "\nChecks: " + "; ".join(checks) + f"\nReports: {report_dir}/*.jsonl"
    # Prioritize security findings, then library files. Keep counts and report
    # location even when filenames are long or many files were flagged.
    ordered = sorted(files, key=lambda p: (not any(x.partition(":")[0] in URGENT for x in files[p]),
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
    args = parser.parse_args()
    text, failed = summarize(args.report_dir, dict(value.split("=", 1) for value in args.check),
                             args.quarantine, args.quarantine_status)
    print(text)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
