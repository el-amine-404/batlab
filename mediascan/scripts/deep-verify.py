#!/usr/bin/env python3

"""Decode samples of new files and demux a rotating slice of the whole library."""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict

VIDEO_SUFFIXES = frozenset(
    (".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".m2ts", ".wmv", ".flv", ".webm", ".mpg", ".mpeg")
)

# Sample points as a fraction of duration. The tail matters most: a truncated
# download parses correctly at the head and fails only at the end.
SAMPLE_POINTS = (0.05, 0.50, 0.95)


@dataclass
class Finding:
    path: str
    checks: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    seconds: float = 0.0


def duration_of(path):
    completed = subprocess.run(
        ("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", "-i", path),
        capture_output=True, check=False,
    )
    try:
        return float(completed.stdout.decode().strip())
    except ValueError:
        return 0.0


def run_ffmpeg(arguments, timeout):
    try:
        completed = subprocess.run(
            ("ffmpeg", "-nostdin", "-v", "error", *arguments, "-f", "null", "-"),
            capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s"
    message = completed.stderr.decode("utf-8", "replace").strip()
    if completed.returncode != 0 and not message:
        message = f"ffmpeg exited {completed.returncode}"
    return message or None


def sample_decode(path, sample_seconds, timeout):
    problems = []
    duration = duration_of(path)
    if duration <= 0:
        return ["SAMPLE: could not read duration"]

    for point in SAMPLE_POINTS:
        offset = max(0.0, min(duration - sample_seconds, duration * point))
        # 0:V:0 is the first video stream that is not an attached picture.
        # Decoding cover art instead of the feature passes in a fraction of a
        # second and proves nothing about the film.
        error = run_ffmpeg(
            ("-ss", f"{offset:.2f}", "-i", path, "-t", str(sample_seconds), "-map", "0:V:0"),
            timeout,
        )
        if error:
            problems.append(f"SAMPLE: decode failed at {offset:.0f}s of {duration:.0f}s: {error.splitlines()[-1]}")
    return problems


def full_demux(path, timeout):
    error = run_ffmpeg(("-i", path, "-c", "copy"), timeout)
    return [f"DEMUX: {error.splitlines()[-1]}"] if error else []


def walk(roots, excludes):
    for root in roots:
        for directory, subdirectories, names in os.walk(root):
            subdirectories[:] = [
                d for d in subdirectories if os.path.join(directory, d) not in excludes
            ]
            for name in names:
                if os.path.splitext(name)[1].lower() in VIDEO_SUFFIXES:
                    yield os.path.join(directory, name)


def marker_time(path):
    try:
        return os.path.getmtime(path)
    except (OSError, TypeError):
        return 0.0


def cpu_temperature(sensor):
    for hwmon in sorted(os.listdir("/sys/class/hwmon")):
        base = os.path.join("/sys/class/hwmon", hwmon)
        try:
            with open(os.path.join(base, "name"), encoding="utf-8") as handle:
                if handle.read().strip() != sensor:
                    continue
            with open(os.path.join(base, "temp1_input"), encoding="utf-8") as handle:
                return int(handle.read().strip()) / 1000
        except (OSError, ValueError):
            continue
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATH")
    parser.add_argument("--report")
    parser.add_argument("--marker", help="files newer than this get the sample decode; touched on success")
    parser.add_argument("--sample-seconds", type=int, default=10)
    parser.add_argument("--fraction", type=int, default=4,
                        help="demux 1/N of the library per run, so a full rotation takes N runs")
    parser.add_argument("--slice", type=int, default=-1,
                        help="which 1/N slice to demux; defaults to the ISO week number")
    parser.add_argument("--no-demux", action="store_true")
    parser.add_argument("--no-samples", action="store_true")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--time-budget", type=int, default=0,
                        help="stop starting new files after this many seconds (0 means no limit)")
    parser.add_argument("--max-cpu-temp", type=float, default=0,
                        help="stop starting new files once this sensor reads above this many C (0 means off)")
    parser.add_argument("--cpu-temp-sensor", default="",
                        help="hwmon name whose temp1_input is compared against --max-cpu-temp")
    arguments = parser.parse_args()

    if arguments.max_cpu_temp and cpu_temperature(arguments.cpu_temp_sensor) is None:
        print(f"No readable hwmon sensor named {arguments.cpu_temp_sensor!r}; refusing to run without the temperature guard.")
        return 2

    excludes = [os.path.normpath(e) for e in arguments.exclude]
    paths = sorted(walk(arguments.roots, excludes))
    if not paths:
        print("No video files found.")
        return 0

    newer_than = marker_time(arguments.marker) if arguments.marker else 0.0
    sample_set = [] if arguments.no_samples else [
        p for p in paths if not newer_than or os.path.getmtime(p) > newer_than
    ]

    if arguments.no_demux:
        demux_set = []
    else:
        fraction = max(1, arguments.fraction)
        index = arguments.slice if arguments.slice >= 0 else int(time.strftime("%V"))
        demux_set = [p for n, p in enumerate(paths) if n % fraction == index % fraction]

    print(f"{len(paths)} video file(s): {len(sample_set)} to sample-decode, {len(demux_set)} to demux.")

    started = time.monotonic()
    findings = {}
    budget_hit = False
    too_hot = False
    last_reading = None

    for path, kind in [(p, "sample") for p in sample_set] + [(p, "demux") for p in demux_set]:
        if arguments.time_budget and time.monotonic() - started > arguments.time_budget:
            budget_hit = True
            break
        if arguments.max_cpu_temp:
            last_reading = cpu_temperature(arguments.cpu_temp_sensor)
            if last_reading is None or last_reading > arguments.max_cpu_temp:
                too_hot = True
                break

        finding = findings.setdefault(path, Finding(path=path))
        began = time.monotonic()
        if kind == "sample":
            finding.problems.extend(sample_decode(path, arguments.sample_seconds, arguments.timeout))
        else:
            finding.problems.extend(full_demux(path, arguments.timeout))
        finding.checks.append(kind)
        finding.seconds += time.monotonic() - began

        status = "FLAG" if finding.problems else "ok  "
        print(f"{status} [{kind}] {path} ({finding.seconds:.0f}s)")
        for problem in finding.problems:
            print(f"       {problem}")

    results = list(findings.values())
    flagged = [f for f in results if f.problems]

    if arguments.report:
        os.makedirs(os.path.dirname(arguments.report) or ".", exist_ok=True)
        with open(arguments.report, "w", encoding="utf-8") as handle:
            for finding in results:
                handle.write(json.dumps(asdict(finding)) + "\n")

    elapsed = time.monotonic() - started
    print(f"\nChecked {len(results)} file(s) in {elapsed / 60:.1f} min; {len(flagged)} flagged.")
    if budget_hit:
        print(f"Stopped early: time budget of {arguments.time_budget}s reached.")
    if too_hot:
        reading = "unreadable" if last_reading is None else f"{last_reading:.1f} C"
        print(f"Stopped early: CPU {reading}, limit {arguments.max_cpu_temp:g} C.")

    stopped_early = budget_hit or too_hot
    if arguments.marker and not stopped_early and not arguments.no_samples:
        os.makedirs(os.path.dirname(arguments.marker) or ".", exist_ok=True)
        with open(arguments.marker, "w", encoding="utf-8") as handle:
            handle.write("")

    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
