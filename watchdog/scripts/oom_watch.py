#!/usr/bin/env python3
"""Notify when the kernel kills a process for a host or container OOM."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
NOTIFY = ROOT / "lib" / "batlab-common" / "batlab_common" / "notify.py"
STATE = Path("/var/lib/batlab-watchdog/oom-cursor.json")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")

INVOKED = re.compile(r"\binvoked oom-killer:")
CGROUP = re.compile(r"(?:docker-|cri-containerd-)([0-9a-f]{64})\.(?:scope|slice)")
MEMORY = re.compile(r"^memory: usage (\d+)kB, limit (\d+)kB")
SWAP = re.compile(r"^swap: usage (\d+)kB, limit (\d+)kB")
KILLED = re.compile(r"Memory cgroup out of memory: Killed process (\d+) \(([^)]+)\)")
HOST_KILLED = re.compile(r"Out of memory: Killed process (\d+) \(([^)]+)\)")


@dataclass
class OomEvent:
    timestamp: datetime
    container_id: str | None
    pid: int
    process: str
    memory: tuple[int, int] | None
    swap: tuple[int, int] | None


def parse_events(entries: list[tuple[datetime, str]]) -> list[OomEvent]:
    """Turn the kernel's multi-line OOM reports into one event per kill."""
    container_id = None
    memory = None
    swap = None
    events = []
    for timestamp, message in entries:
        if INVOKED.search(message):
            container_id = None
            memory = None
            swap = None
        if match := CGROUP.search(message):
            container_id = match.group(1)
        if match := MEMORY.match(message):
            memory = (int(match.group(1)), int(match.group(2)))
        if match := SWAP.match(message):
            swap = (int(match.group(1)), int(match.group(2)))
        match = KILLED.search(message) or HOST_KILLED.search(message)
        if match:
            events.append(OomEvent(timestamp, container_id, int(match.group(1)), match.group(2), memory, swap))
    return events


def journal_entries(cursor: str | None) -> tuple[list[tuple[datetime, str]], str]:
    command = ["journalctl", "--boot", "-k", "--output=json", "--show-cursor", "--no-pager"]
    if cursor:
        command.append(f"--after-cursor={cursor}")
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    entries = []
    newest = cursor
    for line in result.stdout.splitlines():
        if line.startswith("-- cursor: "):
            newest = line.removeprefix("-- cursor: ")
            continue
        record = json.loads(line)
        timestamp = datetime.fromtimestamp(int(record["__REALTIME_TIMESTAMP"]) / 1_000_000, timezone.utc)
        entries.append((timestamp, str(record.get("MESSAGE", ""))))
        newest = record.get("__CURSOR", newest)
    if not newest:
        raise RuntimeError("journalctl returned no cursor")
    return entries, newest


def latest_cursor() -> str:
    result = subprocess.run(
        ["journalctl", "--boot", "-k", "-n", "0", "--show-cursor", "--no-pager"],
        text=True,
        capture_output=True,
        check=True,
    )
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("-- cursor: "):
            return line.removeprefix("-- cursor: ")
    raise RuntimeError("journalctl returned no cursor")


def container_name(container_id: str | None) -> str:
    if not container_id:
        return "the host"
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.Name}}", container_id],
        text=True,
        capture_output=True,
    )
    return result.stdout.strip().removeprefix("/") if result.returncode == 0 else container_id[:12]


def size(pair: tuple[int, int] | None) -> str:
    if not pair:
        return "not reported"
    used, limit = pair
    return f"{used / 1024:.0f} MiB of {limit / 1024:.0f} MiB"


def notify(events: list[OomEvent], test: bool = False) -> None:
    if test:
        groups = {"test": []}
    else:
        groups: dict[str, list[OomEvent]] = {}
        for event in events:
            groups.setdefault(container_name(event.container_id), []).append(event)

    for name, found in groups.items():
        if test:
            kind = "info"
            title = "OOM monitoring test"
            fields = ["Host", socket.gethostname(), "Result", "Kernel OOM notifications are active"]
        else:
            last = found[-1]
            kind = "failure"
            title = f"Out of memory: {name}"
            processes = ", ".join(f"{event.process} (PID {event.pid})" for event in found[-6:])
            fields = [
                "Container", name,
                "Killed", processes,
                "RAM", size(last.memory),
                "Swap", size(last.swap),
                "When", last.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
                "Occurrences", str(len(found)),
                "Inspect", f"journalctl -k --since '{last.timestamp.isoformat()}'",
            ]
        command = [sys.executable, "-I", str(NOTIFY), "watchdog", "alerts", kind, title]
        for field_name, value in zip(fields[::2], fields[1::2]):
            command.extend(["--field", field_name, value])
        environment = os.environ.copy()
        environment["BATLAB_NOTIFY_FALLBACK"] = environment.get("WATCHDOG_DISCORD_WEBHOOK", "")
        subprocess.run(command, env=environment, check=True)


def load_state(path: Path) -> dict[str, str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("boot_id"), str) or not isinstance(data.get("cursor"), str):
            raise ValueError("missing boot_id or cursor")
        return data
    except FileNotFoundError:
        return None


def save_state(path: Path, boot_id: str, cursor: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps({"boot_id": boot_id, "cursor": cursor}) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(state_path: Path = STATE, boot_path: Path = BOOT_ID) -> None:
    boot_id = boot_path.read_text(encoding="ascii").strip()
    state = load_state(state_path)
    if state is None:
        save_state(state_path, boot_id, latest_cursor())
        print("OOM monitor initialized at the current journal cursor")
        return
    cursor = state["cursor"] if state["boot_id"] == boot_id else None
    entries, newest = journal_entries(cursor)
    events = parse_events(entries)
    if events:
        notify(events)
        print(f"sent {len(events)} OOM event(s)")
    save_state(state_path, boot_id, newest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-notification", action="store_true")
    args = parser.parse_args()
    try:
        if args.test_notification:
            notify([], test=True)
        else:
            run()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"oom-watch: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
