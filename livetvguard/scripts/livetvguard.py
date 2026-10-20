#!/usr/bin/env python3
"""Close Jellyfin live TV streams that nobody watches.

Jellyfin records each live channel it plays into /cache/transcodes until it
closes the stream. On 2026-10-19 the TV's Tizen app stopped playing Arryadia
after 25 minutes without closing it, and Jellyfin went on recording for 26
hours: 37.8 GB, two hours from a full root disk.

Every run (a timer, every two minutes): a live TV buffer that grows on every
run for ORPHAN_MINUTES while no session plays it is closed through Jellyfin's
API. If it still grows, Jellyfin is restarted, which empties its transcode
folder; the restart waits while someone plays anything, unless that disk is
NEARLY_FULL. One card to #alerts per stream closed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib/batlab-common"))
from batlab_common import notify as apprise  # noqa: E402

ORPHAN_MINUTES = 10
NEARLY_FULL = 0.95

# Jellyfin's log, as its HDHomeRun and M3U tuners write it.
WHEN = r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d)"
BUFFER_LINE = re.compile(r'Beginning "SharedHttpStream" stream to "/cache/transcodes/(\w+)\.ts"')
OPENED_LINE = re.compile(WHEN + r'.*Live stream opened: .*?LiveStreamFiles/(\w+)/stream\.ts.*?LiveStreamId: "([^"]+)"')
STARTED_LINE = re.compile(r'started playback of \'"(.+?)"\' \("([^"]+)"')


def env_value(env_file: Path, name: str) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def run(*command: str, timeout: int = 60) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{' '.join(command[:4])}: {result.stderr.strip() or 'failed'}")
    return result.stdout


def jellyfin(path: str, key: str, method: str = "GET") -> str:
    """Jellyfin's API from inside its container: it publishes no port on the host."""
    return run("docker", "exec", "jellyfin", "curl", "-fsS", "-m", "20", "-X", method,
               "-H", f'Authorization: MediaBrowser Token="{key}"', f"http://localhost:8096{path}")


def transcodes_dir() -> Path:
    """Where /cache/transcodes is on the host: its own mount, or under /cache."""
    found = {m["Destination"]: m["Source"]
             for m in json.loads(run("docker", "inspect", "-f", "{{json .Mounts}}", "jellyfin"))}
    if "/cache/transcodes" in found:
        return Path(found["/cache/transcodes"])
    return Path(found["/cache"]) / "transcodes"


def live_streams(log_dir: Path) -> dict[str, dict]:
    """{buffer file: {"id", "opened", "channel", "client"}} from Jellyfin's last
    three daily logs: no API lists the live streams it holds open."""
    streams: dict[str, dict] = {}
    last = None
    for log in sorted(log_dir.glob("log_*.log"))[-3:]:
        with open(log, encoding="utf-8", errors="replace") as lines:
            for line in lines:
                if match := OPENED_LINE.search(line):
                    last = match.group(2)
                    streams.setdefault(last, {}).update(id=match.group(3), opened=match.group(1))
                elif last and (match := STARTED_LINE.search(line)):
                    streams[last].update(channel=match.group(1), client=match.group(2))
                    last = None
                elif match := BUFFER_LINE.search(line):
                    streams.setdefault(match.group(1), {})
    return streams


@dataclass
class Viewing:
    """Who plays what right now."""
    live_ids: set[str] = field(default_factory=set)
    live_unknown: bool = False      # a live channel playing without saying which stream
    anything: bool = False


def viewing(sessions: list[dict]) -> Viewing:
    seen = Viewing()
    for session in sessions:
        item = session.get("NowPlayingItem") or {}
        if not item:
            continue
        seen.anything = True
        live_id = (session.get("PlayState") or {}).get("LiveStreamId")
        if live_id:
            seen.live_ids.add(live_id)
        elif item.get("Type") == "TvChannel":
            seen.live_unknown = True
    return seen


@dataclass
class Buffer:
    name: str
    size: int
    stream: dict = field(default_factory=dict)


def orphans(buffers: list[Buffer], seen: Viewing, state: dict, now: float) -> list[Buffer]:
    """Buffers that grew on every run for ORPHAN_MINUTES while no session played
    them. Updates state: {name: {size, orphan_since}}."""
    found, kept = [], {}
    for buffer in buffers:
        before = state.get(buffer.name, {})
        grew = buffer.size > before.get("size", buffer.size)
        played = seen.live_unknown or buffer.stream.get("id") in seen.live_ids
        entry = {"size": buffer.size}
        if grew and not played:
            entry["orphan_since"] = before.get("orphan_since", now)
            if now - entry["orphan_since"] >= ORPHAN_MINUTES * 60:
                found.append(buffer)
        kept[buffer.name] = entry
    state.clear()
    state.update(kept)
    return found


def growing(path: Path, seconds: int = 15) -> bool:
    try:
        before = path.stat().st_size
        time.sleep(seconds)
        return path.stat().st_size > before
    except FileNotFoundError:
        return False


def duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes < 90 else f"{minutes // 60} h {minutes % 60:02d} min"


def card(buffer: Buffer, unwatched: float, how: str) -> tuple[str, str]:
    """Title and body of the #alerts card for one closed stream."""
    stream = buffer.stream
    opened = stream.get("opened", "")
    if opened:
        opened += " UTC" + (f" on {stream['client']}" if stream.get("client") else "")
    return ("Closed a live TV stream nobody was watching",
            apprise.fields_text([("Channel", stream.get("channel") or "unknown, opened without playback"),
                                 ("Opened", opened or "unknown"),
                                 ("Unwatched for", duration(unwatched)),
                                 ("Recorded", f"{buffer.size / 1e9:.1f} GB, deleted"),
                                 ("Done", how)]))


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print each buffer and the decision; change and post nothing")
    parser.add_argument("--env-file", default=str(repo / "compose/.env"))
    parser.add_argument("--state-dir", type=Path,
                        default=Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "batlab-livetvguard")
    args = parser.parse_args(argv)
    env = Path(args.env_file)
    key = env_value(env, "JELLYFIN_API_KEY")
    volumes = Path(env_value(env, "VOLUMES_ROOT") or "/mnt/docker-volumes")
    state_path = args.state_dir / "state.json"
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}

    try:
        if run("docker", "inspect", "-f", "{{.State.Running}}", "jellyfin").strip() != "true":
            return 0
        folder = transcodes_dir()
        seen = viewing(json.loads(jellyfin("/Sessions?activeWithinSeconds=300", key)))
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"jellyfin did not answer: {error}", file=sys.stderr)
        return 1

    known = live_streams(volumes / "jellyfin/log")
    buffers = [Buffer(path.stem, path.stat().st_size, known[path.stem])
               for path in folder.glob("*.ts") if path.stem in known]
    now = time.time()
    found = orphans(buffers, seen, state, now)
    for buffer in buffers:
        print(f"{buffer.name}: {buffer.size / 1e9:.1f} GB, {buffer.stream.get('channel', 'channel unknown')}, "
              + ("unwatched" if buffer in found else f"watched, stopped, or unwatched under {ORPHAN_MINUTES} min"))
    if args.dry_run:
        return 0

    restarted = ""
    for buffer in found:
        path = folder / f"{buffer.name}.ts"
        unwatched = now - state[buffer.name]["orphan_since"]
        how = restarted     # a restart closes every stream at once
        if buffer.stream.get("id") and not how:
            try:
                jellyfin(f"/LiveStreams/Close?liveStreamId={buffer.stream['id']}", key, "POST")
                if not growing(path):
                    how = "closed through Jellyfin's API"
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                print(f"close failed: {error}", file=sys.stderr)
        if not how:
            usage = shutil.disk_usage(folder)
            if seen.anything and 1 - usage.free / usage.total < NEARLY_FULL:
                print(f"{buffer.name}: still growing, but someone is playing; the restart waits")
                continue
            try:
                run("docker", "restart", "jellyfin", timeout=180)
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                print(f"restart failed: {error}", file=sys.stderr)
                continue
            how = restarted = ("restarted Jellyfin during playback: the API close did not stop it and the disk "
                               "was nearly full" if seen.anything else
                               "restarted Jellyfin: the API close did not stop it")
        if path.exists() and not growing(path, 5):
            path.unlink(missing_ok=True)
        state.pop(buffer.name, None)
        print(f"{buffer.name}: {how}")
        title, body = card(buffer, unwatched, how)
        webhook = env_value(env, "DISCORD_WEBHOOK_ALERTS")
        try:
            apprise.deliver("livetvguard", "alerts", title, body, "warning",
                            fallback=webhook if webhook.startswith("http") else None)
        except apprise.NotSent as error:
            print(f"message not sent: {error}", file=sys.stderr)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=1))
    temporary.replace(state_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
