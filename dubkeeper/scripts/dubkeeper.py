#!/usr/bin/env python3
"""Carry audio tracks and sidecar subtitles across Sonarr upgrades.

Sonarr ranks anime by picture (release-group tier) and only then by dub, so an
upgrade can replace a dual-audio file with a better Japanese-only one. With
Sonarr's recycle bin on, the replaced file survives for a while; this script
finds it and, when both files are the same video, copies the audio languages
the new file lacks into it (stream copy, no re-encode). Sidecar subtitles the
old file had and the new one lacks are copied back under the new name.

"Same video" means video lengths within two seconds and a shared audio
language whose loudness lines up, with one consistent offset, at two points of
the episode. Anything less is reported for a manual look and left alone.

Run with --dry-run to print decisions without changing files, or
--check OLD NEW to analyse one pair of files.
"""

from __future__ import annotations

import argparse
import array
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

SIDECAR_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup"}
RATE, STEP = 8000, 80          # 8 kHz mono, 10 ms loudness bins
CLIP_SECONDS = 30
MAX_LAG_BINS = 300             # 3 s either way
MIN_CORRELATION = 0.9
MAX_OFFSET_SPREAD_MS = 40      # the two points must agree this closely
IGNORED_OFFSET_MS = 40         # below this a shift is not worth applying
MAX_LENGTH_DIFFERENCE = 2.0    # seconds


def env_value(env_file: Path, name: str) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def container_path(path: str, data_root: Path) -> Path:
    """Maps a path as the arr containers see it (/data/...) to this host."""
    return data_root / path.removeprefix("/data/") if path.startswith("/data/") else Path(path)


class Arr:
    def __init__(self, base_url: str, key: str) -> None:
        self.base_url, self.key = base_url.rstrip("/"), key

    def request(self, method: str, endpoint: str, body: object = None, **query: object) -> object:
        url = f"{self.base_url}/api/v3/{endpoint}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method,
                                         headers={"X-Api-Key": self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
        return json.loads(payload) if payload else None

    def get(self, endpoint: str, **query: object) -> object:
        return self.request("GET", endpoint, **query)


# --- media inspection ---------------------------------------------------------

def language(stream: dict) -> str | None:
    value = (stream.get("tags") or {}).get("language", "").lower()
    return None if value in ("", "und", "unk", "mis", "zxx") else value


def probe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def video_length(info: dict) -> float:
    """Length of the first video stream, falling back to the container's."""
    for stream in info.get("streams", []):
        if stream.get("codec_type") != "video" or (stream.get("disposition") or {}).get("attached_pic"):
            continue
        if stream.get("duration"):
            return float(stream["duration"])
        tag = (stream.get("tags") or {}).get("DURATION")
        if tag:
            hours, minutes, seconds = tag.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        break
    return float(info.get("format", {}).get("duration") or 0)


def audio_streams(info: dict) -> list[dict]:
    return [stream for stream in info.get("streams", []) if stream.get("codec_type") == "audio"]


def missing_audio(old: dict, new: dict) -> list[dict]:
    """The first old audio stream of each language the new file has none of.
    Commentary is not worth carrying over on its own."""
    present = {language(stream) for stream in audio_streams(new)}
    chosen: dict[str, dict] = {}
    for stream in audio_streams(old):
        lang = language(stream)
        title = (stream.get("tags") or {}).get("title", "").lower()
        if lang and lang not in present and lang not in chosen and "commentary" not in title:
            chosen[lang] = stream
    return list(chosen.values())


def reference_language(old: dict, new: dict) -> str | None:
    """A language both files carry, preferring the new file's default track."""
    old_languages = {language(stream) for stream in audio_streams(old)} - {None}
    ordered = sorted(audio_streams(new), key=lambda s: not (s.get("disposition") or {}).get("default"))
    for stream in ordered:
        if language(stream) in old_languages:
            return language(stream)
    return None


def loudness(path: Path, stream_index: int, start: float) -> list[float]:
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", str(CLIP_SECONDS),
                          "-i", str(path), "-map", f"0:{stream_index}", "-ac", "1", "-ar", str(RATE),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    samples = array.array("h", raw[: len(raw) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    bins = [math.sqrt(sum(s * s for s in samples[i:i + STEP]) / STEP) for i in range(0, len(samples) - STEP, STEP)]
    mean = sum(bins) / len(bins) if bins else 0.0
    return [value - mean for value in bins]


def best_lag(a: list[float], b: list[float], limit: int = MAX_LAG_BINS) -> tuple[int, float]:
    """Lag in bins by which the same sound arrives later in b, and its correlation."""
    best = (0, -1.0)
    for lag in range(-limit, limit + 1):
        start, stop = max(0, -lag), min(len(a), len(b) - lag)
        if stop - start < len(a) // 2:
            continue
        num = sum(a[i] * b[i + lag] for i in range(start, stop))
        den = math.sqrt(sum(a[i] * a[i] for i in range(start, stop)) * sum(b[i + lag] ** 2 for i in range(start, stop)))
        if den and num / den > best[1]:
            best = (lag, num / den)
    return best


@dataclass
class Verdict:
    carry: list[dict] = field(default_factory=list)
    offset_ms: int = 0
    problem: str = ""
    notes: list[str] = field(default_factory=list)


def judge(old_path: Path, new_path: Path, old: dict, new: dict) -> Verdict:
    """Whether the old file's audio fits the new file: the same video, with the
    offset to apply. Subtitles are checked on their own, against the new
    file's audio (subtitle_offset)."""
    verdict = Verdict(carry=missing_audio(old, new))
    if not verdict.carry:
        return verdict
    lost = "+".join(language(s) or "?" for s in verdict.carry) + " audio"
    old_length, new_length = video_length(old), video_length(new)
    if not old_length or not new_length or abs(old_length - new_length) > MAX_LENGTH_DIFFERENCE:
        verdict.problem = f"{lost} lost; videos differ in length ({old_length:.1f}s vs {new_length:.1f}s)"
        return verdict
    shared = reference_language(old, new)
    if not shared:
        verdict.problem = f"{lost} lost; no audio language in both files to line them up by"
        return verdict
    old_ref = next(s for s in audio_streams(old) if language(s) == shared)
    new_ref = next(s for s in audio_streams(new) if language(s) == shared)
    offsets = []
    for fraction in (0.2, 0.65):
        start = new_length * fraction
        lag, correlation = best_lag(loudness(old_path, old_ref["index"], start),
                                    loudness(new_path, new_ref["index"], start))
        verdict.notes.append(f"{shared} at {start / 60:.0f} min: {lag * 10:+d} ms, r={correlation:.3f}")
        if correlation < MIN_CORRELATION:
            verdict.problem = f"{lost} lost; {shared} audio does not match (r={correlation:.2f})"
            return verdict
        offsets.append(lag * 10)
    if max(offsets) - min(offsets) > MAX_OFFSET_SPREAD_MS:
        verdict.problem = f"{lost} lost; timing drifts between the files ({offsets[0]:+d} vs {offsets[1]:+d} ms)"
        return verdict
    mean = round(sum(offsets) / len(offsets))
    verdict.offset_ms = mean if abs(mean) >= IGNORED_OFFSET_MS else 0
    return verdict


def merge(old_path: Path, new_path: Path, new: dict, verdict: Verdict, temporary: Path) -> None:
    """Writes new + the carried audio into temporary, then checks the result."""
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(new_path)]
    if verdict.offset_ms:
        # Positive: the new file hears it later, so the old track must start later.
        command += ["-itsoffset", f"{verdict.offset_ms / 1000:.3f}"]
    command += ["-i", str(old_path), "-map", "0"]
    for stream in verdict.carry:
        command += ["-map", f"1:{stream['index']}"]
    command += ["-c", "copy", "-map_metadata", "0", "-map_chapters", "0"]
    first = len(audio_streams(new))
    for offset, stream in enumerate(verdict.carry):
        command += [f"-disposition:a:{first + offset}", "0",
                    f"-metadata:s:a:{first + offset}", f"language={language(stream)}"]
    command += ["-f", "matroska", str(temporary)]
    subprocess.run(command, check=True, capture_output=True)

    result = probe(temporary)
    expected = len(new["streams"]) + len(verdict.carry)
    if len(result["streams"]) != expected:
        raise RuntimeError(f"merged file has {len(result['streams'])} streams, expected {expected}")
    if abs(video_length(result) - video_length(new)) > 1:
        raise RuntimeError("merged file's length differs from the new file's")
    have = {language(stream) for stream in audio_streams(result)}
    if any(language(stream) not in have for stream in verdict.carry):
        raise RuntimeError("merged file lacks a carried language")


def sidecars(old_path: Path) -> list[Path]:
    stem = old_path.stem + "."
    try:
        return sorted(p for p in old_path.parent.iterdir()
                      if p.name.startswith(stem) and p.suffix.lower() in SIDECAR_EXTENSIONS and p.is_file())
    except OSError:
        return []


def missing_sidecars(old_path: Path, new_path: Path) -> list[tuple[Path, Path, str]]:
    """Old sidecar subtitles the new file has no counterpart of, as (old file,
    name next to the new file, suffix such as ".en.srt")."""
    pairs = []
    for sidecar in sidecars(old_path):
        suffix = sidecar.name[len(old_path.stem):]
        target = new_path.with_name(new_path.stem + suffix)
        if not target.exists():
            pairs.append((sidecar, target, suffix))
    return pairs


SRT_TIME = re.compile(r"(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})")


def shift_srt(text: str, offset_ms: int) -> str:
    def shifted(match: re.Match) -> str:
        h, m, s, ms = (int(group) for group in match.groups())
        total = max(0, ((h * 60 + m) * 60 + s) * 1000 + ms + offset_ms)
        return f"{total // 3600000:02}:{total // 60000 % 60:02}:{total // 1000 % 60:02},{total % 1000:03}"
    return SRT_TIME.sub(shifted, text)


HALVES_AGREE_MS = 300      # both halves of a subtitle must want the same shift
MIN_CUES_PER_HALF = 15
FFSUBSYNC_OFFSET = re.compile(r"offset seconds:\s*(-?[0-9.]+)")


def srt_halves(text: str, middle_ms: float) -> tuple[str, str, int, int]:
    """The cues starting before and after the middle, as two SRT texts."""
    blocks = [block for block in re.split(r"\n\s*\n", text.replace("\r", "").strip()) if "-->" in block]
    first, second = [], []
    for block in blocks:
        h, m, sec, ms = (int(group) for group in SRT_TIME.search(block).groups())
        (first if ((h * 60 + m) * 60 + sec) * 1000 + ms < middle_ms else second).append(block)
    return "\n\n".join(first) + "\n", "\n\n".join(second) + "\n", len(first), len(second)


def halves_verdict(first_s: float, second_s: float) -> tuple[int | None, str]:
    """One shift for the whole file if both halves want it, else why not."""
    if abs(first_s - second_s) * 1000 > HALVES_AGREE_MS:
        return None, f"its halves need different shifts ({first_s:+.2f} s, {second_s:+.2f} s): the releases are cut differently"
    shift = round((first_s + second_s) / 2 * 1000)
    return (shift if abs(shift) >= IGNORED_OFFSET_MS else 0), ""


class SubtitleChecker:
    """Measures with ffsubsync, the tool Bazarr syncs with, how far a subtitle
    is from the new file's speech: once per half, so a cut that one shift
    cannot fix shows as two different answers. ffsubsync runs inside the
    Bazarr container, which has it; the reference audio is extracted once."""

    def __init__(self, new_path: Path, new: dict, data_root: Path, workdir: Path, container: str) -> None:
        self.new_path, self.new, self.data_root, self.container = new_path, new, data_root, container
        self.workdir, self.reference = workdir, None

    def in_container(self, path: Path) -> str:
        return "/data/" + str(path.relative_to(self.data_root))

    def reference_audio(self) -> Path:
        if self.reference is None:
            streams = audio_streams(self.new)
            main = next((s for s in streams if (s.get("disposition") or {}).get("default")), streams[0])
            self.reference = self.workdir / "reference.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(self.new_path), "-map", f"0:{main['index']}",
                            "-ac", "1", "-ar", "16000", str(self.reference)], check=True, capture_output=True)
        return self.reference

    def ffsubsync(self, subtitle: Path) -> float:
        output = self.workdir / "synced.srt"
        result = subprocess.run(["docker", "exec", "-e", "PYTHONPATH=/app/bazarr/bin/libs", self.container,
                                 "python3", "-m", "ffsubsync.ffsubsync", self.in_container(self.reference_audio()),
                                 "-i", self.in_container(subtitle), "-o", self.in_container(output), "--no-fix-framerate"],
                                capture_output=True, text=True, timeout=1800)
        match = FFSUBSYNC_OFFSET.search(result.stdout + result.stderr)
        if result.returncode or not match:
            raise RuntimeError(f"ffsubsync failed on {subtitle.name}: {(result.stderr or result.stdout)[-200:]}")
        return float(match.group(1))

    def offset(self, sidecar: Path) -> tuple[int | None, str]:
        """(shift in ms, "") to apply, or (None, reason) to leave it out."""
        if sidecar.suffix.lower() != ".srt":
            return None, "not SRT, so it cannot be checked here"
        text = sidecar.read_text(encoding="utf-8-sig", errors="replace")
        first, second, n_first, n_second = srt_halves(text, video_length(self.new) * 500)
        if min(n_first, n_second) < MIN_CUES_PER_HALF:
            return None, "too few lines to check its timing"
        measured = []
        for name, half in (("first.srt", first), ("second.srt", second)):
            path = self.workdir / name
            path.write_text(half, encoding="utf-8")
            measured.append(self.ffsubsync(path))
        return halves_verdict(*measured)


def restore_sidecars(pairs: list[tuple[Path, Path, str]], checker: SubtitleChecker | None,
                     apply: bool) -> tuple[list[str], list[str]]:
    """Restores each sidecar ffsubsync finds fits the new file, shifted when
    needed. Returns (restored, refused) descriptions."""
    restored, refused = [], []
    for sidecar, target, label in pairs:
        if checker is None:
            refused.append(f"{label} (Bazarr's ffsubsync is not reachable)")
            continue
        shift, reason = checker.offset(sidecar)
        if shift is None:
            refused.append(f"{label}: {reason}")
            continue
        if apply:
            if shift:
                text = sidecar.read_text(encoding="utf-8-sig", errors="replace")
                target.write_text(shift_srt(text, shift), encoding="utf-8")
                shutil.copystat(sidecar, target)
            else:
                shutil.copy2(sidecar, target)
        restored.append(label + (f" shifted {shift / 1000:+.2f} s" if shift else ""))
    return restored, refused


# --- Sonarr history -----------------------------------------------------------

@dataclass
class Upgrade:
    old: str                       # path as Sonarr saw it before deletion
    series_id: int
    series_title: str
    episode_ids: set[int]
    date: str


def recent_upgrades(sonarr: Arr, now: dt.datetime, days: float, settle_minutes: float) -> list[Upgrade]:
    records = sonarr.get("history", pageSize=500, sortKey="date", sortDirection="descending",
                         eventType=5, includeSeries="true").get("records", [])
    upgrades: dict[str, Upgrade] = {}
    for record in records:
        if (record.get("data") or {}).get("reason") != "Upgrade":
            continue
        when = dt.datetime.fromisoformat(record["date"].replace("Z", "+00:00"))
        if not (now - dt.timedelta(days=days) <= when <= now - dt.timedelta(minutes=settle_minutes)):
            continue
        upgrade = upgrades.setdefault(record["sourceTitle"], Upgrade(
            record["sourceTitle"], record["seriesId"], (record.get("series") or {}).get("title", "?"), set(), record["date"]))
        upgrade.episode_ids.add(record["episodeId"])
    return list(upgrades.values())


def find_recycled(recycle: Path, old: str) -> Path | None:
    """Sonarr recycles to <bin>/<series folder>/<season folder>/<name>; match on
    the name, preferring a copy under the same series folder."""
    name, parts = Path(old).name, set(Path(old).parts)
    matches = [Path(directory, name) for directory, _, files in os.walk(recycle) if name in files]
    matches.sort(key=lambda path: -len(parts & set(path.parts)))
    return matches[0] if matches else None


def current_files(sonarr: Arr, episode_ids: set[int]) -> set[str]:
    paths = set()
    for episode_id in episode_ids:
        file_id = (sonarr.get(f"episode/{episode_id}") or {}).get("episodeFileId")
        if file_id:
            paths.add(sonarr.get(f"episodefile/{file_id}")["path"])
    return paths


# --- reporting ----------------------------------------------------------------

def notify(webhook: str, kept: list[str], manual: list[str]) -> None:
    fields = []
    for name, lines in (("kept after upgrade", kept), ("needs a look", manual)):
        if lines:
            value = ""
            for index, line in enumerate(lines):
                addition = ("\n" if value else "") + "• " + line
                if len(value) + len(addition) > 950:
                    value += f"\n… and {len(lines) - index} more"
                    break
                value += addition
            fields.append({"name": f"{name} ({len(lines)})", "value": value, "inline": False})
    payload = {
        "username": "dub keeper",
        "avatar_url": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/sonarr.png",
        "embeds": [{
            "title": "🔊 Upgrades checked for lost audio and subtitles" if not manual else "🟠 An upgrade lost audio",
            "description": "A lost track is in the recycle bin for 14 days after the upgrade: "
                           "copy it in by hand, or restore the old file from `/data/recycle`.",
            "color": 15105570 if manual else 3066993, "fields": fields,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        }],
    }
    request = urllib.request.Request(webhook, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "batlab-dubkeeper"})
    urllib.request.urlopen(request, timeout=15).close()


def describe(verdict: Verdict) -> str:
    langs = "+".join(language(s) or "?" for s in verdict.carry)
    shift = f", shifted {verdict.offset_ms:+d} ms" if verdict.offset_ms else ""
    return f"{langs} audio carried over{shift}"


# --- main ---------------------------------------------------------------------

def process(upgrade: Upgrade, sonarr: Arr, data_root: Path, apply: bool,
            ffsubsync_container: str = "bazarr") -> tuple[str, str]:
    """Returns (status, message); status is done, kept, manual, or gone."""
    recycle = data_root / "recycle"
    old_path = find_recycled(recycle, upgrade.old)
    if not old_path:
        return "gone", f"{Path(upgrade.old).name} is not in the recycle bin"
    targets = current_files(sonarr, upgrade.episode_ids)
    label = f"{upgrade.series_title}: `{Path(upgrade.old).name}`"
    if len(targets) != 1:
        return "manual", f"{label} was replaced by {len(targets)} files; compare them by hand"
    new_path = container_path(targets.pop(), data_root)
    if not new_path.is_file():
        return "retry", f"{new_path} is not there yet"

    old, new = probe(old_path), probe(new_path)
    pairs = missing_sidecars(old_path, new_path)
    verdict = judge(old_path, new_path, old, new)
    changes, refused = [], []
    until = (dt.datetime.fromisoformat(upgrade.date.replace("Z", "+00:00")) + dt.timedelta(days=14)).date()
    if verdict.problem:
        refused.append(verdict.problem)
    elif verdict.carry and new_path.suffix.lower() != ".mkv":
        refused.append(f"{describe(verdict).replace('carried over', 'lost')}; the new file is not MKV")
    elif verdict.carry:
        if apply:
            temporary = recycle / ".dubkeeper" / new_path.name
            temporary.parent.mkdir(parents=True, exist_ok=True)
            try:
                merge(old_path, new_path, new, verdict, temporary)
                shutil.copymode(new_path, temporary)
                os.replace(temporary, new_path)
            finally:
                temporary.unlink(missing_ok=True)
        changes.append(describe(verdict))
    if pairs:
        workdir = recycle / ".dubkeeper" / "subtitles"
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            checker = SubtitleChecker(new_path, new, data_root, workdir, ffsubsync_container)
            restored, refused_subtitles = restore_sidecars(pairs, checker, apply)
            refused += [f"subtitles {reason}" for reason in refused_subtitles]
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        if restored:
            changes.append("subtitles restored: " + ", ".join(restored))
    if refused and not changes:
        return "manual", f"{label}: {'; '.join(refused)}. The old file stays in the recycle bin until {until}"
    if refused:
        changes.append("not kept: " + "; ".join(refused))
    if not changes:
        return "done", f"{label}: nothing lost"
    if apply:
        sonarr.request("POST", "command", {"name": "RescanSeries", "seriesId": upgrade.series_id})
    detail = f" ({'; '.join(verdict.notes)})" if verdict.notes else ""
    return "kept", f"{upgrade.series_title}: `{new_path.name}`: {'; '.join(changes)}{detail}"


def check_pair(old_path: Path, new_path: Path, data_root: Path, container: str) -> int:
    old, new = probe(old_path), probe(new_path)
    print("old audio:", [language(s) for s in audio_streams(old)], f"video {video_length(old):.3f}s")
    print("new audio:", [language(s) for s in audio_streams(new)], f"video {video_length(new):.3f}s")
    verdict = judge(old_path, new_path, old, new)
    for note in verdict.notes:
        print("  " + note)
    print("audio:", verdict.problem or (describe(verdict) if verdict.carry else "nothing lost"))
    pairs = missing_sidecars(old_path, new_path)
    if pairs:
        workdir = data_root / "recycle" / ".dubkeeper" / "check"
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            restored, refused = restore_sidecars(
                pairs, SubtitleChecker(new_path, new, data_root, workdir, container), apply=False)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        for item in restored:
            print("would restore subtitles:", item)
        for item in refused:
            print("would not restore:", item)
    return 0


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print decisions; change no file, post nothing")
    parser.add_argument("--check", nargs=2, metavar=("OLD", "NEW"), help="analyse one pair of files and exit")
    parser.add_argument("--env-file", default=str(repo / "compose/.env"))
    parser.add_argument("--data-root", default="/mnt/storage/data")
    parser.add_argument("--sonarr-url", default="http://172.19.10.11:8989")
    parser.add_argument("--ffsubsync-container", default="bazarr",
                        help="container whose ffsubsync checks subtitle timing (Bazarr ships it)")
    parser.add_argument("--days", type=float, default=14, help="how far back to look; match the recycle bin's cleanup")
    parser.add_argument("--state-dir", default=os.path.join(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"),
                                                            "batlab-dubkeeper"))
    args = parser.parse_args(argv)
    if args.check:
        return check_pair(Path(args.check[0]), Path(args.check[1]), Path(args.data_root), args.ffsubsync_container)

    env_file, data_root = Path(args.env_file), Path(args.data_root)
    if not (data_root / "recycle").is_dir():
        print(f"{data_root}/recycle is not there; is the data disk mounted and Sonarr's recycle bin set?", file=sys.stderr)
        return 1
    sonarr = Arr(args.sonarr_url, env_value(env_file, "SONARR_API_KEY"))
    state_path = Path(args.state_dir) / "handled.json"
    handled: dict[str, str] = json.loads(state_path.read_text()) if state_path.exists() else {}

    try:
        upgrades = recent_upgrades(sonarr, dt.datetime.now(dt.timezone.utc), args.days, settle_minutes=10)
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"dub keeper could not read Sonarr: {error}", file=sys.stderr)
        return 1

    kept, manual, failed = [], [], 0
    for upgrade in upgrades:
        if upgrade.old in handled:
            continue
        try:
            status, message = process(upgrade, sonarr, data_root, apply=not args.dry_run,
                                      ffsubsync_container=args.ffsubsync_container)
        except (subprocess.CalledProcessError, RuntimeError, OSError, urllib.error.URLError, ValueError) as error:
            detail = error.stderr.decode(errors="replace")[-300:] if isinstance(error, subprocess.CalledProcessError) and error.stderr else error
            print(f"{upgrade.old}: {detail}", file=sys.stderr)
            failed += 1
            continue
        print(f"{status}: {message}")
        if status == "retry":
            continue
        if status == "kept":
            kept.append(message)
        elif status == "manual":
            manual.append(message)
        handled[upgrade.old] = status

    if args.dry_run:
        return 1 if failed else 0
    webhook = env_value(env_file, "DISCORD_WEBHOOK_DOWNLOAD_ISSUES")
    if (kept or manual) and webhook.startswith("http"):
        notify(webhook, kept, manual)
    # Entries older than the look-back window can never come back; drop them.
    recent = {upgrade.old for upgrade in upgrades}
    handled = {path: status for path, status in handled.items() if path in recent}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(handled, indent=1, sort_keys=True))
    temporary.replace(state_path)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
