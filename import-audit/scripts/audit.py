#!/usr/bin/env python3
"""Report downloads that Sonarr or Radarr imported wrongly or not at all.

Three checks, each against the current state rather than history:

  wrong episodes   a library episode file whose torrent source is named for
                   different episodes than Sonarr assigned to it
  left behind      a finished video in torrents/ that nothing in the library
                   links to, for an episode or movie that still has no file
  stuck in queue   a queue item Sonarr or Radarr flags as blocked or failing

Each problem is posted to Discord once, and again only after it went away and
came back. Run with --dry-run to print the findings without notifying.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".mov"}
EPISODE_TAG_RE = re.compile(r"(?<![A-Za-z0-9])S(?P<season>\d{1,2})(?P<episodes>(?:[ ._-]?E\d{1,3})+(?:-E?\d{1,3})?)(?![0-9])",
                            re.IGNORECASE)
EPISODE_NUMBER_RE = re.compile(r"E?(\d{1,3})", re.IGNORECASE)


@dataclass(frozen=True)
class Problem:
    kind: str
    key: str
    text: str


def episodes_in_name(name: str) -> tuple[int, list[int]] | None:
    """Season and episode numbers written in a release file name, or None."""
    match = EPISODE_TAG_RE.search(name)
    if not match:
        return None
    tag = match["episodes"]
    numbers = [int(number) for number in EPISODE_NUMBER_RE.findall(tag)]
    if "-" in tag and len(numbers) == 2 and numbers[0] < numbers[1]:
        numbers = list(range(numbers[0], numbers[1] + 1))
    return int(match["season"]), sorted(set(numbers))


def env_value(env_file: Path, name: str) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


class Arr:
    def __init__(self, name: str, base_url: str, key: str) -> None:
        self.name, self.base_url, self.key = name, base_url.rstrip("/"), key

    def get(self, endpoint: str, **query: object) -> object:
        url = f"{self.base_url}/api/v3/{endpoint}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={"X-Api-Key": self.key})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)


def finished_videos(root: Path, min_age_seconds: int, now: float) -> list[tuple[Path, os.stat_result]]:
    found = []
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if not name.startswith(".")]
        for name in files:
            path = Path(directory, name)
            if path.suffix.lower() not in VIDEO_EXTENSIONS or "sample" in name.lower():
                continue
            status = path.stat()
            if now - status.st_mtime >= min_age_seconds:
                found.append((path, status))
    return found


def container_path(path: str, data_root: Path) -> Path:
    """Maps a path as the arr containers see it (/data/...) to this host."""
    return data_root / path.removeprefix("/data/") if path.startswith("/data/") else Path(path)


def check_wrong_episodes(sonarr: Arr, data_root: Path, torrent_videos: list[tuple[Path, os.stat_result]]) -> list[Problem]:
    sources = {(status.st_dev, status.st_ino): path for path, status in torrent_videos if status.st_nlink > 1}
    problems = []
    for series in sonarr.get("series"):
        episode_files = {item["id"]: item for item in sonarr.get("episodefile", seriesId=series["id"])}
        if not episode_files:
            continue
        assigned: dict[int, list[int]] = {}
        for episode in sonarr.get("episode", seriesId=series["id"]):
            if episode.get("episodeFileId"):
                assigned.setdefault(episode["episodeFileId"], []).append(episode["episodeNumber"])
        for file_id, numbers in assigned.items():
            library_path = container_path(episode_files[file_id]["path"], data_root)
            try:
                status = library_path.stat()
            except OSError:
                continue
            source = sources.get((status.st_dev, status.st_ino))
            parsed = episodes_in_name(source.name) if source else None
            if parsed and parsed[1] != sorted(numbers):
                problems.append(Problem(
                    "wrong episodes", f"wrong:{source}:{sorted(numbers)}",
                    f"{series['title']}: `{source.name}` is named E{','.join(map(str, parsed[1]))} "
                    f"but Sonarr filed it as E{','.join(map(str, sorted(numbers)))}"))
    return problems


def check_left_behind(sonarr: Arr, radarr: Arr, torrents: Path, min_age: int, now: float) -> list[Problem]:
    problems = []
    episodes_by_series: dict[int, dict[tuple[int, int], bool]] = {}
    for path, status in finished_videos(torrents / "tv", min_age, now):
        if status.st_nlink > 1:
            continue
        # Sonarr's parser applies scene-numbering maps that can be wrong for a
        # release, so it only names the series; episodes come from the file name.
        parsed = sonarr.get("parse", title=path.name) or {}
        series = parsed.get("series") or {}
        numbers = episodes_in_name(path.name)
        if series.get("id") and numbers:
            if series["id"] not in episodes_by_series:
                episodes_by_series[series["id"]] = {(episode["seasonNumber"], episode["episodeNumber"]): episode["hasFile"]
                                                    for episode in sonarr.get("episode", seriesId=series["id"])}
            known = episodes_by_series[series["id"]]
            has_file = all(known.get((numbers[0], number), False) for number in numbers[1])
        else:
            has_file = bool(parsed.get("episodes")) and all(episode.get("hasFile") for episode in parsed["episodes"])
        if not has_file:
            problems.append(Problem("left behind", f"left:{path}",
                                    f"{series.get('title', 'unknown series')}: `{path.name}` was downloaded but is not in the library"))
    movie_folders: dict[Path, list[os.stat_result]] = {}
    movie_root = torrents / "movies"
    for path, status in finished_videos(movie_root, min_age, now):
        top = movie_root / path.relative_to(movie_root).parts[0]
        movie_folders.setdefault(top, []).append(status)
    movie_has_file: dict[int, bool] | None = None
    for folder, statuses in movie_folders.items():
        if any(status.st_nlink > 1 for status in statuses):
            continue
        movie = (radarr.get("parse", title=folder.name) or {}).get("movie") or {}
        # Radarr's parse endpoint returns hasFile as null however full the
        # library is, so a release an upgrade replaced would be reported for as
        # long as it seeds. The movie list is where the answer is real.
        if movie_has_file is None:
            movie_has_file = {item["id"]: bool(item.get("hasFile")) for item in radarr.get("movie")}
        if not movie_has_file.get(movie.get("id"), False):
            problems.append(Problem("left behind", f"left:{folder}",
                                    f"{movie.get('title', 'unknown movie')}: `{folder.name}` was downloaded but is not in the library"))
    return problems


def check_queues(apps: list[Arr], min_age: int, now: float) -> list[Problem]:
    problems = []
    for app in apps:
        for item in app.get("queue", pageSize=200).get("records", []):
            state, status = item.get("trackedDownloadState", ""), item.get("trackedDownloadStatus", "")
            added = item.get("added")
            age = now - dt.datetime.fromisoformat(added.replace("Z", "+00:00")).timestamp() if added else min_age
            if age < min_age or (status == "ok" and state not in ("importBlocked", "failedPending")):
                continue
            messages = [message for entry in item.get("statusMessages", []) for message in entry.get("messages", [])]
            reason = "; ".join(messages) or item.get("errorMessage") or state or status
            problems.append(Problem("stuck in queue", f"queue:{app.name}:{item.get('downloadId')}",
                                    f"{app.name}: `{item.get('title', '?')}`: {reason[:180]}"))
    return problems


def notify(webhook: str, problems: list[Problem]) -> None:
    fields = []
    for kind in ("wrong episodes", "left behind", "stuck in queue"):
        lines = [problem.text for problem in problems if problem.kind == kind]
        if not lines:
            continue
        value = ""
        for index, line in enumerate(lines):
            addition = ("\n" if value else "") + "• " + line
            if len(value) + len(addition) > 950:
                value += f"\n… and {len(lines) - index} more"
                break
            value += addition
        fields.append({"name": f"{kind} ({len(lines)})", "value": value, "inline": False})
    payload = {
        "username": "import audit",
        "avatar_url": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/sonarr.png",
        "embeds": [{
            "title": "🟠 Imports need a look",
            "description": "Fix them in Sonarr/Radarr → Wanted → Manual Import, mapping each file by its name.",
            "color": 15105570, "fields": fields,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        }],
    }
    request = urllib.request.Request(webhook, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "batlab-import-audit"})
    urllib.request.urlopen(request, timeout=15).close()


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print findings; no Discord post, no state change")
    parser.add_argument("--env-file", default=str(repo / "compose/.env"))
    parser.add_argument("--data-root", default="/mnt/storage/data")
    parser.add_argument("--sonarr-url", default="http://172.19.10.11:8989")
    parser.add_argument("--radarr-url", default="http://172.19.10.12:7878")
    parser.add_argument("--min-age-hours", type=float, default=3,
                        help="ignore downloads and queue items younger than this, which may still be importing")
    parser.add_argument("--state-dir", default=os.path.join(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"),
                                                            "batlab-import-audit"))
    args = parser.parse_args(argv)

    env_file, data_root = Path(args.env_file), Path(args.data_root)
    sonarr = Arr("Sonarr", args.sonarr_url, env_value(env_file, "SONARR_API_KEY"))
    radarr = Arr("Radarr", args.radarr_url, env_value(env_file, "RADARR_API_KEY"))
    now, min_age = time.time(), int(args.min_age_hours * 3600)
    if not (data_root / "torrents").is_dir():
        print(f"{data_root}/torrents is not there; is the data disk mounted?", file=sys.stderr)
        return 1

    try:
        torrent_videos = finished_videos(data_root / "torrents/tv", 0, now)
        problems = (check_wrong_episodes(sonarr, data_root, torrent_videos)
                    + check_left_behind(sonarr, radarr, data_root / "torrents", min_age, now)
                    + check_queues([sonarr, radarr], min_age, now))
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"audit could not run: {error}", file=sys.stderr)
        return 1

    for problem in problems:
        print(f"{problem.kind}: {problem.text}")
    print(f"{len(problems)} problem(s)")
    if args.dry_run:
        return 0

    state_path = Path(args.state_dir) / "reported.json"
    reported = set(json.loads(state_path.read_text())) if state_path.exists() else set()
    new = [problem for problem in problems if problem.key not in reported]
    webhook = env_value(env_file, "DISCORD_WEBHOOK_DOWNLOADS")
    if new and webhook.startswith("http"):
        notify(webhook, new)
        print(f"posted {len(new)} new problem(s) to Discord")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(sorted(problem.key for problem in problems)))
    temporary.replace(state_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
