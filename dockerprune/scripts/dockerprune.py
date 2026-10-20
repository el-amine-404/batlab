#!/usr/bin/env python3
"""Remove old Docker images, build cache and stray containers, keeping one
previous version of each image to roll back to.

On 2026-10-20 lab2's root SSD reached 98%. Besides a live TV stream left open
(livetvguard/), 46 GB of it was images earlier upgrades had left behind and 17
GB build cache nothing used.

Every night, after the updater (it waits for the updater's lock):

  images        per repository, everything a container uses (running or not),
                everything this host's compose stacks would run, the IMAGE_/TAG_
                pins of compose/versions.env, conf/keep-images.txt, anything
                newer than those (pulled, not yet deployed), and the newest
                older one: the previous version. Older ones go. An image
                nothing uses goes after UNUSED_IMAGE_DAYS, an untagged one
                after UNTAGGED_IMAGE_HOURS.
  build cache   kept to BUILD_CACHE_MAX, the most recently used first.
  containers    exited ones no compose stack owns, STRAY_CONTAINER_HOURS
                after they stopped (a docker run without --rm).
  volumes       anonymous ones nothing uses.

One card to #updates when it freed REPORT_FREED_BYTES or more.
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

UNUSED_IMAGE_DAYS = 7
UNTAGGED_IMAGE_HOURS = 24
STRAY_CONTAINER_HOURS = 24
BUILD_CACHE_MAX = "10GB"
REPORT_FREED_BYTES = 1_000_000_000


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
    return f"{size / 1e9:.1f} GB"


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
    keep = repo / "dockerprune/conf/keep-images.txt"
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


def reclaimed(output: str) -> float:
    """Bytes from the "Total: 13.6GB" or "Total reclaimed space: 13.6GB" line
    docker builder prune prints."""
    match = re.search(r"Total(?: reclaimed space)?:\s*([\d.]+)\s*([kMGT]?B)", output)
    if not match:
        return 0.0
    return float(match.group(1)) * {"B": 1, "kB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}[match.group(2)]


@dataclass
class Report:
    freed: float = 0.0
    used_percent: float = 0.0
    free: float = 0.0
    images: list[tuple[str, int]] = field(default_factory=list)
    build_cache: float = 0.0
    containers: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def card(report: Report) -> tuple[str, str]:
    """Title and body of the #updates card."""
    fields = [("Root disk", f"{report.used_percent:.0f}% used, {gb(report.free)} free")]
    if report.images:
        shown = [f"{name}  {gb(size)}" for name, size in sorted(report.images, key=lambda item: -item[1])[:10]]
        if len(report.images) > 10:
            shown.append(f"and {len(report.images) - 10} more")
        fields.append((f"Images removed ({len(report.images)})", "\n".join(shown)))
    if report.build_cache >= 0.05e9:
        fields.append(("Build cache", gb(report.build_cache)))
    if report.containers:
        fields.append(("Containers removed", ", ".join(report.containers)))
    if report.failed:
        fields.append(("Not done", "\n".join(report.failed[:5])))
    return f"Freed {gb(report.freed)} of old Docker images and build cache", apprise.fields_text(fields)


def prune(args: argparse.Namespace, env: Path, repo: Path) -> int:
    # Never while the updater swaps images: it holds the old one to roll back to.
    lock_path = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "batlab-updater/lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = open(lock_path, "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("the updater is running; pruning tomorrow")
        return 0

    now = time.time()
    before = shutil.disk_usage("/").free
    report = Report()

    for name in stray_containers(now):
        print(f"container {name}: exited, no compose stack")
        if not args.dry_run:
            try:
                run("docker", "rm", name)
                report.containers.append(name)
            except RuntimeError as error:
                report.failed.append(str(error))

    images = docker_images()
    try:
        pins = pinned(repo, env)
    except RuntimeError as error:
        report.failed.append(str(error))
        images, pins = [], set()
    for image, why in removable(images, used_image_ids(), pins, now):
        label = ", ".join(image.refs) or f"untagged {image.id[7:19]}"
        print(f"image {label} ({gb(image.size)}): {why}")
        if args.dry_run:
            continue
        try:
            # By name: an id with several tags is refused without --force.
            for ref in image.refs or [image.id]:
                run("docker", "image", "rm", ref)
            report.images.append((label, image.size))
        except RuntimeError as error:
            report.failed.append(str(error))

    if not args.dry_run:
        try:
            report.build_cache = reclaimed(run("docker", "builder", "prune", "-f", "--max-used-space", BUILD_CACHE_MAX))
            run("docker", "volume", "prune", "-f")
        except RuntimeError as error:
            report.failed.append(str(error))

    usage = shutil.disk_usage("/")
    report.freed, report.free = max(usage.free - before, 0), usage.free
    report.used_percent = 100 * (1 - usage.free / usage.total)
    title, body = card(report)
    print(title + "\n" + body)
    if report.freed >= REPORT_FREED_BYTES and not args.dry_run:
        webhook = env_value(env, "DISCORD_WEBHOOK_UPDATES")
        try:
            apprise.deliver("dockerprune", "updates", title, body, "success",
                            fallback=webhook if webhook.startswith("http") else None)
        except apprise.NotSent as error:
            print(f"message not sent: {error}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="list what would go; change and post nothing")
    parser.add_argument("--env-file", default=str(repo / "compose/.env"))
    args = parser.parse_args(argv)
    return prune(args, Path(args.env_file), repo)


if __name__ == "__main__":
    sys.exit(main())
