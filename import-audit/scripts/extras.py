#!/usr/bin/env python3
"""Hardlink reviewed, completed torrent extras into Sonarr/Jellyfin's library.

Defaults to a dry run. The hourly import audit calls run() with --auto-import.
Only explicit entries in config/extras.json are eligible; no episode guessing.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import http.cookiejar
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

CONFIG = Path(__file__).resolve().parents[1] / "config/extras.json"


def contained(root: Path, relative: str) -> Path:
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts or not part.parts:
        raise ValueError(f"unsafe relative path: {relative}")
    result = root / part
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"path escapes root: {result}")
    # Do not link through symlinks, even ones currently pointing inside root.
    if any(p.is_symlink() for p in [result, *result.parents] if p != root and p.is_relative_to(root)):
        raise ValueError(f"symlink in path: {result}")
    return result


def load_config(path: Path) -> list[dict]:
    packs = json.loads(path.read_text())
    try:
        check_config(packs)
    except (KeyError, TypeError) as error:
        raise ValueError(f"{path.name}: incomplete entry ({error!r})") from error
    return packs


def check_config(packs: list[dict]):
    destinations = set()
    for pack in packs:
        contained(Path("/unused"), pack["source"])
        for entry in pack["files"]:
            contained(Path("/unused"), entry["source"])
            contained(Path("/unused"), entry["destination"])
            key = (pack["tvdb_id"], entry["destination"])
            if key in destinations:
                raise ValueError(f"duplicate destination: {key}")
            destinations.add(key)
            if Path(entry["destination"]).suffix.lower() != ".mkv":
                raise ValueError("only reviewed MKV files are supported")


class Qbit:
    def __init__(self, base: str, username: str, password: str):
        self.base = base.rstrip("/") + "/api/v2/"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        # Some installations explicitly allow the host's address without login.
        # Do not submit stale UI credentials when authenticated access works.
        try:
            if isinstance(self.get("torrents/info"), list):
                return
            raise ValueError("qBittorrent returned an invalid torrent list")
        except urllib.error.HTTPError as error:
            if error.code not in (401, 403):
                raise
        data = urllib.parse.urlencode({"username": username, "password": password}).encode()
        with self.opener.open(self.base + "auth/login", data, timeout=30) as r:
            # qBittorrent 5 answers an accepted login with 204; 4.x with "Ok.".
            if r.status != 204 and r.read().strip() != b"Ok.":
                raise ValueError("qBittorrent authentication failed")

    def get(self, endpoint: str, **query):
        url = self.base + endpoint + ("?" + urllib.parse.urlencode(query) if query else "")
        with self.opener.open(url, timeout=30) as r:
            return json.load(r)


def completed_files(qbit, pack: dict) -> dict[str, int]:
    """Require an exact torrent content path and individual file completion."""
    expected = "/data/torrents/tv/" + pack["source"]
    torrents = [t for t in qbit.get("torrents/info") if t.get("content_path", "").rstrip("/") == expected]
    if len(torrents) != 1:
        return {}
    torrent = torrents[0]
    if torrent.get("state") in {"checkingDL", "checkingUP", "checkingResumeData", "moving", "error", "missingFiles"}:
        return {}
    prefix = pack["source"] + "/"
    return {f["name"][len(prefix):]: f["size"]
            for f in qbit.get("torrents/files", hash=torrent["hash"])
            if f.get("progress") == 1 and f["name"].startswith(prefix)}


def nfo(entry: dict, show_title: str) -> bytes:
    root = ET.Element("episodedetails")
    for key, value in (("title", entry["title"]), ("showtitle", show_title),
                       ("season", 0), ("episode", entry["episode"]), ("lockdata", "true")):
        ET.SubElement(root, key).text = str(value)
    ET.SubElement(root, "uniqueid", type="tvdb", default="true").text = str(entry["episode_tvdb_id"])
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def publish_new(path: Path, content: bytes):
    """Publish a complete file without overwriting an existing one."""
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"existing metadata differs: {path}")
        return
    fd, temporary = tempfile.mkstemp(prefix=".extras-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def link_entry(source: Path, destination: Path, size: int, entry: dict, show_title: str, apply: bool) -> str:
    status = source.lstat()
    if not stat.S_ISREG(status.st_mode) or status.st_size != size:
        raise ValueError(f"source is not a completed regular file: {source}")
    if destination.exists():
        if not os.path.samefile(source, destination):
            raise ValueError(f"destination already contains another file: {destination}")
        if "episode" in entry and apply:
            publish_new(destination.with_suffix(".nfo"), nfo(entry, show_title))
        return "existing"
    if "episode" in entry:
        tag = re.compile(rf"S00E{entry['episode']:02d}(?!\d)", re.I)
        for sibling in destination.parent.glob("*.mkv"):
            if tag.search(sibling.name):
                raise ValueError(f"special already exists: {sibling}")
    if not apply:
        return "would link"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if "episode" in entry:
        publish_new(destination.with_suffix(".nfo"), nfo(entry, show_title))
    # os.link is atomic, fails across filesystems, and never replaces a file.
    os.link(source, destination, follow_symlinks=False)
    after = source.stat()
    if (status.st_ino, status.st_size, status.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        destination.unlink()
        raise ValueError(f"source changed while linking: {source}")
    return "linked"


def save_state(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def refresh_jellyfin(base: str, key: str, tvdb_ids: list[int]):
    def request(endpoint, method="GET"):
        req = urllib.request.Request(base.rstrip("/") + endpoint, method=method,
                                     headers={"X-Emby-Token": key})
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = response.read()
        return json.loads(payload) if payload else None
    for tvdb_id in tvdb_ids:
        query = urllib.parse.urlencode({"Recursive": "true", "IncludeItemTypes": "Series",
                                       "AnyProviderIdEquals": f"tvdb.{tvdb_id}"})
        items = request("/Items?" + query).get("Items", [])
        if len(items) != 1:
            raise ValueError(f"Jellyfin did not identify exactly one show for TVDB {tvdb_id}")
        request(f"/Items/{items[0]['Id']}/Refresh?Recursive=true&MetadataRefreshMode=Default"
                "&ImageRefreshMode=Default&ReplaceAllMetadata=false&ReplaceAllImages=false", "POST")


def run(sonarr, data_root: Path, env_file: Path, state_dir: Path, apply=False,
        config=CONFIG, qbit=None, jellyfin_refresh=None) -> int:
    from audit import env_value
    packs = load_config(config)
    if not (data_root / "media/tv").is_dir():
        raise ValueError("TV library is not mounted")
    if apply:
        state_dir.mkdir(parents=True, exist_ok=True)
    lock = (state_dir / "extras.lock").open("a") if apply else contextlib.nullcontext()
    with lock as handle:
        if apply:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("extras: another import is active")
                return 0
        qbit = qbit or Qbit(env_value(env_file, "QBIT_URL") or "http://qbittorrent.homelab.lan",
                           env_value(env_file, "QBIT_USER"), env_value(env_file, "QBIT_PASS"))
        shows = {s["tvdbId"]: s for s in sonarr.get("series")}
        state_path = state_dir / "extras.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {"sonarr": [], "jellyfin": []}
        errors, linked = 0, 0
        for pack in packs:
            show = shows.get(pack["tvdb_id"])
            if not show:
                print(f"extras: waiting for Sonarr series TVDB {pack['tvdb_id']}")
                continue
            relative = show["path"].removeprefix("/data/media/tv/")
            library = contained(data_root / "media/tv", relative)
            if not library.is_dir():
                raise ValueError(f"show library is missing: {library}")
            source_root = contained(data_root / "torrents/tv", pack["source"])
            complete = completed_files(qbit, pack)
            episodes = {e["episodeNumber"]: e for e in sonarr.get("episode", seriesId=show["id"])
                        if e["seasonNumber"] == 0}
            for entry in pack["files"]:
                try:
                    source = contained(source_root, entry["source"])
                    destination = contained(library, entry["destination"])
                    if "episode" in entry:
                        contained(library, str(Path(entry["destination"]).with_suffix(".nfo")))
                    if entry["source"] not in complete:
                        print(f"extras: waiting for completed torrent file: {source.name}")
                        continue
                    episode = episodes.get(entry.get("episode"))
                    if "episode" in entry:
                        if not episode or episode.get("tvdbId") != entry["episode_tvdb_id"]:
                            raise ValueError(f"Sonarr special identity changed: {entry['destination']}")
                        if episode.get("hasFile") and not destination.exists():
                            print(f"extras: Sonarr already has S00E{entry['episode']:02d}; kept existing file")
                            continue
                    if apply and (not destination.exists() or (episode and (
                            not episode.get("hasFile") or not destination.with_suffix(".nfo").exists()))):
                        # Persist pending refresh BEFORE publishing, including crash recovery.
                        state["jellyfin"] = sorted(set(state["jellyfin"]) | {pack["tvdb_id"]})
                        if episode:
                            state["sonarr"] = sorted(set(state["sonarr"]) | {show["id"]})
                        save_state(state_path, state)
                    result = link_entry(source, destination, complete[entry["source"]], entry, show["title"], apply)
                    linked += result == "linked"
                    print(f"extras: {result}: {destination.relative_to(data_root)}")
                except (OSError, ValueError) as error:
                    errors += 1
                    print(f"extras: refused: {error}")
        if apply:
            for series_id in list(state["sonarr"]):
                try:
                    sonarr.post("command", {"name": "RescanSeries", "seriesId": series_id})
                    state["sonarr"].remove(series_id)
                    save_state(state_path, state)
                except (OSError, ValueError) as error:
                    errors += 1
                    print(f"extras: Sonarr refresh pending: {error}")
            if state["jellyfin"]:
                try:
                    refresh = jellyfin_refresh or refresh_jellyfin
                    refresh(env_value(env_file, "JELLYFIN_URL") or "http://jellyfin.homelab.lan",
                            env_value(env_file, "JELLYFIN_API_KEY"), state["jellyfin"])
                    state["jellyfin"] = []
                    save_state(state_path, state)
                except (OSError, ValueError) as error:
                    errors += 1
                    print(f"extras: Jellyfin refresh pending (will retry): {error}")
        print(f"extras: {linked} linked, {errors} error(s)")
        return errors


def main():
    from audit import Arr, env_value
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--data-root", type=Path, default=Path("/mnt/storage/data"))
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[2] / "compose/.env")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/batlab-import-audit")
    parser.add_argument("--sonarr-url", default="http://172.19.10.11:8989")
    args = parser.parse_args()
    sonarr = Arr("Sonarr", args.sonarr_url, env_value(args.env_file, "SONARR_API_KEY"))
    try:
        return bool(run(sonarr, args.data_root, args.env_file, args.state_dir, args.apply))
    except (OSError, ValueError) as error:
        print(f"extras: could not run: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
