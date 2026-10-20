#!/usr/bin/env python3
"""Keep the root SSD from filling up.

On 2026-10-20 lab2's root SSD reached 98%. The Samsung TV had opened a live
channel through Jellyfin the evening before and stopped watching after 25
minutes, but Jellyfin never closed the stream: it kept recording the channel
into /cache/transcodes for 26 hours, 37.8 GB, and was two hours from filling
the disk. Behind it sat 46 GB of images left by earlier upgrades and 17 GB of
build cache that nothing used.

  janitor.py livetv   (every 2 minutes)
      A live-TV buffer that keeps growing while no session plays it for
      ORPHAN_MINUTES is closed through Jellyfin's API; if it still grows,
      Jellyfin is restarted, which clears its transcode folder. A restart waits
      while someone plays anything, unless the disk is nearly full. One
      message to #alerts per stream.

  janitor.py clean    (nightly, after the updater)
      Images: per repository, every image a container or compose/versions.env
      uses, anything newer (pulled, not yet deployed), and the newest older one
      as the previous version to roll back to; older ones go. An image no
      container or pin uses goes after UNUSED_IMAGE_DAYS, an untagged one
      after a day. Build cache above BUILD_CACHE_MAX. Exited containers that no
      compose stack owns, a day after they stopped, and anonymous volumes
      nothing uses. One message to #updates when it freed at least 1 GB.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
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
NEARLY_FULL = 0.95          # restart Jellyfin even during playback above this
UNUSED_IMAGE_DAYS = 7
UNTAGGED_IMAGE_HOURS = 24
STRAY_CONTAINER_HOURS = 24
BUILD_CACHE_MAX = "10GB"
REPORT_FREED_BYTES = 1 << 30
GB = 1e9

# Jellyfin's log, as written by its HDHomeRun and M3U tuners.
BUFFER_LINE = re.compile(r'Beginning "SharedHttpStream" stream to "/cache/transcodes/(\w+)\.ts"')
OPENED_LINE = re.compile(r'Live stream opened: .*?LiveStreamFiles/(\w+)/stream\.ts.*?LiveStreamId: "([^"]+)"')
STARTED_LINE = re.compile(r'started playback of \'"(.+?)"\'')


def env_value(env_file: Path, name: str) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def run(*command: str, timeout: int = 600) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{' '.join(command[:4])}: {result.stderr.strip() or 'failed'}")
    return result.stdout


def gb(size: float) -> str:
    return f"{size / GB:.1f} GB"


def notify(channel: str, webhook: str, title: str, text: str, kind: str, dry_run: bool) -> None:
    """Through Apprise as "janitor on <host>"; straight to the webhook when Apprise is down."""
    if dry_run:
        print(f"would post to #{channel}: {title}\n{text}")
        return
    try:
        apprise.deliver("janitor", channel, title, text, kind,
                        fallback=webhook if webhook.startswith("http") else None)
    except apprise.NotSent as error:
        print(f"message not sent: {error}", file=sys.stderr)


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=1))
    temporary.replace(path)


# --- live TV ---------------------------------------------------------------

def jellyfin(path: str, key: str, method: str = "GET") -> str:
    """Jellyfin's API from inside its container: it publishes no port on the host."""
    return run("docker", "exec", "jellyfin", "curl", "-fsS", "-m", "20", "-X", method,
               "-H", f'Authorization: MediaBrowser Token="{key}"', f"http://localhost:8096{path}", timeout=60)


def transcodes_dir() -> Path:
    """Where /cache/transcodes is on the host: its own mount, or under /cache."""
    mounts = json.loads(run("docker", "inspect", "-f", "{{json .Mounts}}", "jellyfin"))
    found = {m["Destination"]: m["Source"] for m in mounts}
    if "/cache/transcodes" in found:
        return Path(found["/cache/transcodes"])
    return Path(found["/cache"]) / "transcodes"


def live_streams(log_dir: Path) -> dict[str, dict]:
    """{buffer file id: {"id": LiveStreamId, "channel": name}} from Jellyfin's
    last three daily logs: no API lists the streams it holds open."""
    streams: dict[str, dict] = {}
    last = None
    for log in sorted(log_dir.glob("log_*.log"))[-3:]:
        with open(log, encoding="utf-8", errors="replace") as lines:
            for line in lines:
                if match := OPENED_LINE.search(line):
                    last = match.group(1)
                    streams.setdefault(last, {})["id"] = match.group(2)
                elif last and (match := STARTED_LINE.search(line)):
                    streams[last].setdefault("channel", match.group(1))
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
    stream_id: str = ""
    channel: str = ""


def orphans(buffers: list[Buffer], seen: Viewing, state: dict, now: float) -> list[Buffer]:
    """Buffers that grew since the last run while no session played them, for
    ORPHAN_MINUTES in a row. Updates state: {name: {size, orphan_since}}."""
    found, kept = [], {}
    for buffer in buffers:
        before = state.get(buffer.name, {})
        growing = buffer.size > before.get("size", buffer.size)
        played = seen.live_unknown or (buffer.stream_id and buffer.stream_id in seen.live_ids)
        entry = {"size": buffer.size}
        if growing and not played:
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


def livetv(args: argparse.Namespace, env: Path, volumes: Path) -> int:
    key = env_value(env, "JELLYFIN_API_KEY")
    state_path = args.state_dir / "livetv.json"
    state = load_state(state_path)
    try:
        if run("docker", "inspect", "-f", "{{.State.Running}}", "jellyfin").strip() != "true":
            return 0
        folder = transcodes_dir()
        seen = viewing(json.loads(jellyfin("/Sessions?activeWithinSeconds=300", key)))
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"jellyfin did not answer: {error}", file=sys.stderr)
        return 1

    known = live_streams(volumes / "jellyfin/log")
    buffers = []
    for path in folder.glob("*.ts"):
        if path.stem in known:
            buffers.append(Buffer(path.stem, path.stat().st_size, known[path.stem].get("id", ""),
                                  known[path.stem].get("channel", "")))
    now = time.time()
    found = orphans(buffers, seen, state, now)
    for buffer in buffers:
        print(f"{buffer.name}: {gb(buffer.size)}, {buffer.channel or 'channel unknown'}, "
              f"{'left open' if buffer in found else 'watched, stopped, or not yet ' + str(ORPHAN_MINUTES) + ' minutes'}")
    if args.dry_run:
        return 0

    restarted = ""
    for buffer in found:
        path = folder / f"{buffer.name}.ts"
        since = state[buffer.name]["orphan_since"]
        how = restarted     # a restart closes every stream at once
        if buffer.stream_id and not how:
            try:
                jellyfin(f"/LiveStreams/Close?liveStreamId={buffer.stream_id}", key, "POST")
                if not growing(path):
                    how = "closed through Jellyfin's API"
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                print(f"close failed: {error}", file=sys.stderr)
        if not how:
            usage = shutil.disk_usage(folder)
            full = 1 - usage.free / usage.total
            if seen.anything and full < NEARLY_FULL:
                print(f"{buffer.name}: still growing, but someone is playing; restart waits")
                continue
            try:
                run("docker", "restart", "jellyfin", timeout=180)
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                print(f"restart failed: {error}", file=sys.stderr)
                continue
            how = restarted = "Jellyfin restarted" + (" during playback: the disk was nearly full"
                                                     if seen.anything else "")
        if path.exists() and not growing(path, 5):
            path.unlink(missing_ok=True)
        state.pop(buffer.name, None)
        print(f"{buffer.name}: {how}")
        notify("alerts", env_value(env, "DISCORD_WEBHOOK_ALERTS"), "📺 Live TV stream left open, now closed",
               f"**{buffer.channel or 'A live channel'}** was still being recorded into Jellyfin's transcode "
               f"folder with no one watching it: the buffer grew for "
               f"{(now - since) / 60:.0f} minutes while no session played it, and had reached "
               f"{gb(buffer.size)}. A TV app that stops playback without closing the stream leaves it running "
               f"until the disk is full.\n\n**Done:** {how}, buffer deleted.",
               "warning", False)
    save_state(state_path, state)
    return 0


# --- nightly clean -----------------------------------------------------------

@dataclass
class Image:
    id: str
    refs: list[str]
    arrived: float              # pulled, built or tagged here (Metadata.LastTagTime)
    size: int


def repository(ref: str) -> str:
    """docker.io/library/x:1, ghcr.io/a/b:2, host:5000/c:3 → the part before the tag.
    The updater's local/batlab-rollback:<service> holds one image per service,
    so each tag is its own repository."""
    slash = ref.rfind("/")
    colon = ref.rfind(":")
    name = ref[:colon] if colon > slash else ref
    return ref if name == "local/batlab-rollback" else name


def normalise(ref: str) -> str:
    """versions.env says jellyfin/jellyfin:12.2, Docker says the same; strip
    docker.io/library/ the way Docker shows it."""
    for prefix in ("docker.io/library/", "docker.io/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def pinned(repo: Path, env: Path) -> set[str]:
    """Every image this host's compose stacks would run, whether up or down, the
    IMAGE_/TAG_ pins of compose/versions.env (base images a local build starts
    from), and conf/keep-images.txt: images scripts run outside compose."""
    found = set()
    versions = repo / "compose/versions.env"
    found |= pinned_from(dict(line.split("=", 1) for line in versions.read_text().splitlines()
                              if "=" in line and not line.startswith("#")))
    keep = repo / "janitor/conf/keep-images.txt"
    found |= {normalise(line.split("#")[0].strip()) for line in keep.read_text().splitlines()
              if line.split("#")[0].strip()}
    profile = env_value(env, "HOST_PROFILE")
    stacks_file = repo / f"hosts/{profile}/stacks.txt"
    stacks = sorted(path.parent.name for path in (repo / "compose").glob("*/docker-compose.yml"))
    if stacks_file.exists():
        listed = {line.split("#")[0].strip() for line in stacks_file.read_text().splitlines()}
        stacks = [stack for stack in stacks if stack in listed]
    env_args = ["--env-file", str(versions), "--env-file", str(env),
                "--env-file", str(repo / f"hosts/{profile}/compose.env")]
    for stack in stacks:
        try:
            images = run("docker", "compose", *env_args, "-f", str(repo / f"compose/{stack}/docker-compose.yml"),
                         "config", "--images", timeout=60)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            # A stack that cannot render cannot say what it needs: keep everything.
            raise RuntimeError(f"compose/{stack} does not render, not cleaning images: {error}") from error
        found |= {normalise(ref if ":" in ref.rsplit("/", 1)[-1] else ref + ":latest") for ref in images.split()}
    return found


def pinned_from(values: dict[str, str]) -> set[str]:
    return {normalise(f"{values[name]}:{values['TAG_' + name[6:]]}")
            for name in values if name.startswith("IMAGE_") and "TAG_" + name[6:] in values}


def removable(images: list[Image], used_ids: set[str], pins: set[str], now: float) -> list[tuple[Image, str]]:
    """(image, why) for every image that can go, keeping one previous version per repository."""
    groups: dict[str, list[Image]] = {}
    for image in images:
        for ref in image.refs:
            groups.setdefault(repository(ref), []).append(image)

    def in_use(image: Image) -> bool:
        # A rollback tag is a built service's previous version: one per service.
        return (image.id in used_ids or any(normalise(ref) in pins for ref in image.refs)
                or any(ref.startswith("local/batlab-rollback:") for ref in image.refs))

    keep: set[str] = {image.id for image in images if in_use(image)}
    for members in groups.values():
        members = sorted({image.id: image for image in members}.values(), key=lambda image: image.arrived, reverse=True)
        current = [image for image in members if in_use(image)]
        if not current:
            keep.update(image.id for image in members if now - image.arrived < UNUSED_IMAGE_DAYS * 86400)
            continue
        newest_used = max(image.arrived for image in current)
        older = [image for image in members if not in_use(image) and image.arrived < newest_used]
        keep.update(image.id for image in members if image.arrived >= newest_used)
        if older:
            keep.add(older[0].id)

    found = []
    for image in images:
        if image.id in keep:
            continue
        if not image.refs:
            if now - image.arrived >= UNTAGGED_IMAGE_HOURS * 3600:
                found.append((image, "untagged"))
        elif any(repository(ref) in groups and any(in_use(other) for other in groups[repository(ref)])
                 for ref in image.refs):
            found.append((image, "older than the previous version"))
        else:
            found.append((image, f"unused for {UNUSED_IMAGE_DAYS} days"))
    return found


def timestamp(value: str) -> float:
    """Docker's times, nanoseconds and all: 2026-10-01 09:21:52.597526262 +0000 UTC
    or 2026-09-24T03:55:15.625472039Z."""
    value = value.replace(" +0000 UTC", "Z").replace(" ", "T")
    value = re.sub(r"(\.\d{6})\d+", r"\1", value).replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def docker_images() -> list[Image]:
    ids = sorted(set(run("docker", "image", "ls", "-q", "--no-trunc").split()))
    if not ids:
        return []
    images = []
    for item in json.loads(run("docker", "image", "inspect", *ids)):
        arrived = timestamp((item.get("Metadata") or {}).get("LastTagTime") or "") or timestamp(item["Created"])
        images.append(Image(item["Id"], item.get("RepoTags") or [], arrived, item.get("Size", 0)))
    return images


def used_image_ids() -> set[str]:
    containers = run("docker", "ps", "-aq").split()
    if not containers:
        return set()
    return set(run("docker", "inspect", "-f", "{{.Image}}", *containers).split())


def stray_containers(now: float) -> list[str]:
    """Exited one-off containers (docker run without --rm) a day after they stopped."""
    names = run("docker", "ps", "-aq", "--filter", "status=exited", "--filter", "status=created").split()
    found = []
    for item in json.loads(run("docker", "inspect", *names)) if names else []:
        if "com.docker.compose.project" in (item["Config"].get("Labels") or {}):
            continue
        ended = timestamp(item["State"].get("FinishedAt") or "") or timestamp(item["Created"])
        if now - ended >= STRAY_CONTAINER_HOURS * 3600:
            found.append(item["Name"].lstrip("/"))
    return found


def clean(args: argparse.Namespace, env: Path, repo: Path) -> int:
    # Never while the updater swaps images: it holds the old one to roll back to.
    lock_path = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "batlab-updater/lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = open(lock_path, "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("the updater is running; cleaning tomorrow")
        return 0

    now = time.time()
    before = shutil.disk_usage("/").free
    done, failed = [], []

    for name in stray_containers(now):
        print(f"container {name}: exited, no compose stack")
        if not args.dry_run:
            try:
                run("docker", "rm", name)
                done.append(f"container {name}")
            except RuntimeError as error:
                failed.append(str(error))

    images = docker_images()
    try:
        pins = pinned(repo, env)
    except RuntimeError as error:
        failed.append(str(error))
        images, pins = [], set()
    for image, why in removable(images, used_image_ids(), pins, now):
        label = ", ".join(image.refs) or image.id[7:19]
        print(f"image {label} ({gb(image.size)}): {why}")
        if args.dry_run:
            continue
        try:
            # By name: an id with several tags is refused without --force.
            for ref in image.refs or [image.id]:
                run("docker", "image", "rm", ref)
            done.append(f"{label} ({gb(image.size)})")
        except RuntimeError as error:
            failed.append(str(error))

    if not args.dry_run:
        for command in (("docker", "builder", "prune", "-f", "--max-used-space", BUILD_CACHE_MAX),
                        ("docker", "volume", "prune", "-f")):
            try:
                run(*command)
            except RuntimeError as error:
                failed.append(str(error))

    freed = shutil.disk_usage("/").free - before
    usage = shutil.disk_usage("/")
    print(f"freed {gb(max(freed, 0))}; root disk {100 * (1 - usage.free / usage.total):.0f}% used"
          + (f"; {len(failed)} failed" if failed else ""))
    for error in failed:
        print(error, file=sys.stderr)
    if freed >= REPORT_FREED_BYTES:
        removed = "\n".join(f"- {item}" for item in done[:25]) + (f"\n- and {len(done) - 25} more"
                                                                  if len(done) > 25 else "")
        notify("updates", env_value(env, "DISCORD_WEBHOOK_UPDATES"), f"🧹 Freed {gb(freed)} on the root disk",
               f"Old images and build cache, keeping each image's previous version to roll back to. "
               f"The root disk is now {100 * (1 - usage.free / usage.total):.0f}% used.\n\n{removed}",
               "success", args.dry_run)
    return 0


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("job", choices=("livetv", "clean"))
    parser.add_argument("--dry-run", action="store_true", help="print what would be done; change and post nothing")
    parser.add_argument("--env-file", default=str(repo / "compose/.env"))
    parser.add_argument("--state-dir", type=Path,
                        default=Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "batlab-janitor")
    args = parser.parse_args(argv)
    env = Path(args.env_file)
    if args.job == "livetv":
        return livetv(args, env, Path(env_value(env, "VOLUMES_ROOT") or "/mnt/docker-volumes"))
    return clean(args, env, repo)


if __name__ == "__main__":
    sys.exit(main())
