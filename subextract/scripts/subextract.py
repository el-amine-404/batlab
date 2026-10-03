#!/usr/bin/env python3
"""Give every library video one plain SRT per language, from its own subtitles.

Releases often carry their subtitles inside the file: six English ASS tracks in
Attack on Titan's Kira Blu-ray, one SRT in a film. Jellyfin shows embedded text
tracks, but most TV apps make the server burn an ASS track into the video, and
six tracks of one language are hard to choose from. For each language with an
embedded text track, this writes `<video>.<lang>.srt` next to the file, unless a
sidecar for that language is already there and fits the video's speech.

Choosing the track, per language:
  out        signs/songs-only, forced, commentary and karaoke tracks; tracks
             whose dialogue covers well under the others' (an incomplete one)
  then       the track the release marked default (its authors' choice), then
             the least cluttered once converted (fewest overlapping lines: a
             motion-tracked sign becomes hundreds of them), then the most complete
  honorifics variants only when no plain one is left

An existing sidecar is checked with ffsubsync, half by half, as the dub keeper
does; one that does not fit is moved to the recycle bin and replaced. A track
taken from the file is timed to it, so it is not checked.

Image subtitles (PGS, VobSub) need OCR and are not handled here.

Run with --dry-run to print decisions, --file VIDEO to handle one file, or
--all to go through the whole library once.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEXT_CODECS = {"ass": ".ass", "ssa": ".ass", "subrip": ".srt", "mov_text": ".srt", "webvtt": ".vtt"}
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".sup"}
NOT_DIALOGUE = re.compile(r"sign|song|forced|commentary|karaoke|lyrics|\bfx\b|\bcc\b only", re.I)
HONORIFICS = re.compile(r"honorific", re.I)
INCOMPLETE = 0.6          # a track covering less than this share of the best one's dialogue time
# ISO 639-2 to the two-letter codes Bazarr and Jellyfin name sidecars with.
TWO_LETTER = {"eng": "en", "enm": "en", "fre": "fr", "fra": "fr", "ara": "ar", "ita": "it", "spa": "es", "ger": "de",
              "deu": "de", "por": "pt", "jpn": "ja", "rus": "ru", "chi": "zh", "zho": "zh", "kor": "ko", "dut": "nl",
              "nld": "nl", "pol": "pl", "tur": "tr", "swe": "sv", "nor": "no", "dan": "da", "fin": "fi", "gre": "el",
              "ell": "el", "heb": "he", "hin": "hi", "ind": "id", "may": "ms", "msa": "ms", "tha": "th", "vie": "vi",
              "ukr": "uk", "cze": "cs", "ces": "cs", "hun": "hu", "rum": "ro", "ron": "ro", "per": "fa", "fas": "fa"}


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ass2srt = load("ass2srt", "ass2srt/scripts/ass2srt.py")
keeper = load("dubkeeper", "dubkeeper/scripts/dubkeeper.py")


def two_letter(language: str) -> str:
    return TWO_LETTER.get(language, language)


# --- choosing a track ------------------------------------------------------------

@dataclass
class Track:
    index: int
    codec: str
    language: str          # two-letter
    title: str
    default: bool
    srt: str = ""          # the track as SRT, once extracted
    lines: int = 0
    covered: float = 0.0   # seconds of the episode with a line on screen
    overlap: float = 0.0   # share of lines on screen together with another

    @property
    def honorifics(self) -> bool:
        return bool(HONORIFICS.search(self.title))


def text_tracks(info: dict) -> list[Track]:
    tracks = []
    for stream in info.get("streams", []):
        if stream.get("codec_type") != "subtitle" or stream.get("codec_name") not in TEXT_CODECS:
            continue
        tags, disposition = stream.get("tags") or {}, stream.get("disposition") or {}
        language = (tags.get("language") or "").lower()
        title = tags.get("title") or ""
        if language in ("", "und", "zxx", "mis") or disposition.get("forced") or NOT_DIALOGUE.search(title):
            continue
        tracks.append(Track(stream["index"], stream["codec_name"], two_letter(language), title, bool(disposition.get("default"))))
    return tracks


SRT_CUE = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{3})")


def measure(track: Track) -> None:
    spans = sorted(((int(a) * 3600 + int(b) * 60 + int(c)) * 1000 + int(d), (int(e) * 3600 + int(f) * 60 + int(g)) * 1000 + int(h))
                   for a, b, c, d, e, f, g, h in SRT_CUE.findall(track.srt))
    track.lines = len(spans)
    covered, reach, overlapping = 0, 0, set()
    for i, (start, end) in enumerate(spans):
        if start < reach:
            overlapping.add(i)
            overlapping.update(j for j in range(max(0, i - 50), i) if spans[j][1] > start)
        covered += max(0, end - max(start, reach))
        reach = max(reach, end)
    track.covered = covered / 1000
    track.overlap = len(overlapping) / len(spans) if spans else 1.0


def choose(tracks: list[Track]) -> Track | None:
    """The track to make a language's SRT from, of measured candidates."""
    usable = [t for t in tracks if t.lines]
    if not usable:
        return None
    best_cover = max(t.covered for t in usable)
    usable = [t for t in usable if t.covered >= INCOMPLETE * best_cover]
    plain = [t for t in usable if not t.honorifics] or usable
    return min(plain, key=lambda t: (not t.default, round(t.overlap, 2), -t.covered))


# --- files ----------------------------------------------------------------------------

def sidecars_by_language(video: Path) -> dict[str, list[Path]]:
    """Existing sidecar subtitles, by the language code after the video's name
    (`.en.srt`, `.en.hi.srt`); forced-only ones do not count."""
    found: dict[str, list[Path]] = {}
    stem = video.stem + "."
    for path in video.parent.iterdir():
        if not path.name.startswith(stem) or path.suffix.lower() not in SUBTITLE_EXTENSIONS:
            continue
        parts = path.name[len(stem):].lower().split(".")[:-1]
        if not parts or "forced" in parts:
            continue
        found.setdefault(two_letter(parts[0]), []).append(path)
    return found


def extract(video: Path, tracks: list[Track], workdir: Path) -> None:
    """All wanted tracks in one read of the file: ASS as it is, then through
    ass2srt; SubRip, mov_text and WebVTT converted to SRT by ffmpeg."""
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(video)]
    outputs = []
    for track in tracks:
        if TEXT_CODECS[track.codec] == ".ass":
            out = workdir / f"track{track.index}.ass"
            command += ["-map", f"0:{track.index}", "-c", "copy", str(out)]
        else:
            out = workdir / f"track{track.index}.srt"
            command += ["-map", f"0:{track.index}", "-c:s", "srt", str(out)]
        outputs.append((track, out))
    subprocess.run(command, check=True, capture_output=True)
    for track, out in outputs:
        text = out.read_text(encoding="utf-8-sig", errors="replace")
        track.srt = keeper.ass_to_srt(text) if out.suffix == ".ass" else text
        measure(track)


class Fit:
    """Whether existing sidecars fit the video, with one reference audio
    extraction per video however many sidecars are checked."""

    def __init__(self, video: Path, info: dict, data_root: Path, workdir: Path, container: str) -> None:
        self.checker = keeper.SubtitleChecker(video, info, data_root, workdir, container)

    def __call__(self, sidecar: Path) -> tuple[bool, str]:
        shift, reason = self.checker.offset(sidecar)
        if shift is None:
            return False, reason
        if abs(shift) > 500:
            return False, f"it is {shift / 1000:+.2f} s off"
        return True, ""


@dataclass
class Outcome:
    written: list[str]
    replaced: list[str]
    kept: list[str]
    skipped: list[str]


def process(video: Path, data_root: Path, container: str, apply: bool,
            languages: set[str] | None = None) -> Outcome:
    outcome = Outcome([], [], [], [])
    info = keeper.probe(video)
    by_language: dict[str, list[Track]] = {}
    for track in text_tracks(info):
        if languages is None or track.language in languages:
            by_language.setdefault(track.language, []).append(track)
    if not by_language:
        return outcome
    existing = sidecars_by_language(video)
    workdir = data_root / "recycle" / ".subextract" / video.stem[:80]
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        wanted: dict[str, list[Track]] = {}
        fit = Fit(video, info, data_root, workdir, container)
        for language, tracks in by_language.items():
            srts = [p for p in existing.get(language, []) if p.suffix.lower() == ".srt"]
            if not srts:
                wanted[language] = tracks
                continue
            bad = []
            for sidecar in srts:
                fits, reason = fit(sidecar)
                if fits:
                    outcome.kept.append(sidecar.name[len(video.stem):])
                else:
                    bad.append((sidecar, reason))
            # One sidecar that fits is enough; only a language whose every
            # sidecar is off gets an SRT from the file instead.
            if len(bad) == len(srts):
                wanted[language] = tracks
                for sidecar, reason in bad:
                    outcome.replaced.append(f"{sidecar.name[len(video.stem):]} ({reason})")
                    if apply:
                        aside = data_root / "recycle" / "subextract" / sidecar.parent.relative_to(data_root) / sidecar.name
                        aside.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(sidecar), aside)
        if wanted:
            extract(video, [t for tracks in wanted.values() for t in tracks], workdir)
        for language, tracks in wanted.items():
            chosen = choose(tracks)
            if not chosen:
                outcome.skipped.append(f".{language}: no usable track")
                continue
            target = video.with_name(f"{video.stem}.{language}.srt")
            if apply:
                target.write_text(chosen.srt, encoding="utf-8")
            outcome.written.append(f".{language}.srt from {chosen.title or f'track {chosen.index}'} "
                                   f"({chosen.lines} lines)")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return outcome


# --- what to look at -------------------------------------------------------------

class Arr:
    def __init__(self, base_url: str, key: str) -> None:
        self.base_url, self.key = base_url.rstrip("/"), key

    def get(self, endpoint: str, **query: object) -> object:
        url = f"{self.base_url}/api/v3/{endpoint}" + ("?" + urllib.parse.urlencode(query) if query else "")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"X-Api-Key": self.key}), timeout=60) as response:
            return json.load(response)


def recent_imports(app: Arr, kind: str, now: dt.datetime, days: float, settle_minutes: float) -> set[str]:
    """Current paths of files imported in the window: the import's own path
    may since have been renamed or upgraded, so the item's file is asked for."""
    records = app.get("history", pageSize=500, sortKey="date", sortDirection="descending", eventType=3).get("records", [])
    paths = set()
    for record in records:
        when = dt.datetime.fromisoformat(record["date"].replace("Z", "+00:00"))
        if not (now - dt.timedelta(days=days) <= when <= now - dt.timedelta(minutes=settle_minutes)):
            continue
        try:
            if kind == "episode":
                file_id = app.get(f"episode/{record['episodeId']}").get("episodeFileId")
                if file_id:
                    paths.add(app.get(f"episodefile/{file_id}")["path"])
            else:
                movie_file = app.get(f"movie/{record['movieId']}").get("movieFile")
                if movie_file:
                    paths.add(movie_file["path"])
        except urllib.error.HTTPError:
            continue
    return paths


def library_videos(data_root: Path) -> list[Path]:
    found = []
    for root in ("media/tv", "media/movies"):
        for directory, _, files in os.walk(data_root / root):
            found += [Path(directory, f) for f in files if Path(f).suffix.lower() in VIDEO_EXTENSIONS]
    return sorted(found)


def jellyfin_url() -> str:
    """Jellyfin has no fixed address on the Docker network, so ask Docker."""
    address = subprocess.run(["docker", "inspect", "jellyfin", "--format",
                              "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"],
                             capture_output=True, text=True).stdout.split()
    return f"http://{address[0]}:8096" if address else ""


def tell_jellyfin(env_file: Path, url: str, paths: list[Path], data_root: Path) -> None:
    key = keeper.env_value(env_file, "JELLYFIN_API_KEY")
    url = url or jellyfin_url()
    if not key or not url or not paths:
        return
    body = {"Updates": [{"Path": "/data/" + str(p.relative_to(data_root)), "UpdateType": "Modified"} for p in paths]}
    request = urllib.request.Request(f"{url}/Library/Media/Updated", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f'MediaBrowser Token="{key}"'})
    urllib.request.urlopen(request, timeout=30).close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print decisions; write and move nothing")
    parser.add_argument("--file", help="handle this one video")
    parser.add_argument("--all", action="store_true", help="go through the whole library")
    parser.add_argument("--env-file", default=str(REPO / "compose/.env"))
    parser.add_argument("--data-root", default="/mnt/storage/data")
    parser.add_argument("--sonarr-url", default="http://172.19.10.11:8989")
    parser.add_argument("--radarr-url", default="http://172.19.10.12:7878")
    parser.add_argument("--jellyfin-url", default="", help="default: the jellyfin container's address")
    parser.add_argument("--ffsubsync-container", default="bazarr")
    parser.add_argument("--languages", default="en,fr,ar",
                        help="two-letter codes to give an SRT, comma-separated (Bazarr's profile); 'all' for every one")
    parser.add_argument("--days", type=float, default=3, help="how far back to look for imports")
    parser.add_argument("--settle-minutes", type=float, default=30,
                        help="wait this long after an import, so the dub keeper and Bazarr go first")
    parser.add_argument("--state-dir", default=os.path.join(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"),
                                                            "batlab-subextract"))
    args = parser.parse_args(argv)
    env_file, data_root = Path(args.env_file), Path(args.data_root)

    if args.file:
        videos = [Path(args.file)]
    elif args.all:
        videos = library_videos(data_root)
    else:
        now = dt.datetime.now(dt.timezone.utc)
        try:
            imported = (recent_imports(Arr(args.sonarr_url, keeper.env_value(env_file, "SONARR_API_KEY")), "episode",
                                       now, args.days, args.settle_minutes)
                        | recent_imports(Arr(args.radarr_url, keeper.env_value(env_file, "RADARR_API_KEY")), "movie",
                                         now, args.days, args.settle_minutes))
        except (urllib.error.URLError, OSError, ValueError) as error:
            print(f"could not read Sonarr or Radarr: {error}", file=sys.stderr)
            return 1
        videos = sorted(keeper.container_path(p, data_root) for p in imported)

    state_path = Path(args.state_dir) / "handled.json"
    handled: dict[str, int] = json.loads(state_path.read_text()) if state_path.exists() else {}
    changed, failed = [], 0
    for video in videos:
        try:
            size = video.stat().st_size
        except OSError:
            continue
        if handled.get(str(video)) == size and not args.file:
            continue
        try:
            languages = None if args.languages == "all" else {code.strip() for code in args.languages.split(",")}
            outcome = process(video, data_root, args.ffsubsync_container, apply=not args.dry_run, languages=languages)
        except (subprocess.CalledProcessError, RuntimeError, OSError, ValueError) as error:
            detail = error.stderr[-300:] if isinstance(error, subprocess.CalledProcessError) and error.stderr else error
            print(f"{video.name}: {detail}", file=sys.stderr)
            failed += 1
            continue
        parts = [f"wrote {', '.join(outcome.written)}"] if outcome.written else []
        parts += [f"replaced {', '.join(outcome.replaced)}"] if outcome.replaced else []
        parts += [f"kept {', '.join(outcome.kept)}"] if outcome.kept else []
        parts += [f"skipped {', '.join(outcome.skipped)}"] if outcome.skipped else []
        if parts:
            print(f"{video.name}: {'; '.join(parts)}")
        if outcome.written:
            changed.append(video)
        if not args.dry_run:
            handled[str(video)] = size

    if args.dry_run:
        return 1 if failed else 0
    try:
        tell_jellyfin(env_file, args.jellyfin_url, changed, data_root)
    except (urllib.error.URLError, OSError) as error:
        print(f"Jellyfin was not told about new subtitles: {error}", file=sys.stderr)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(handled, indent=1, sort_keys=True))
    temporary.replace(state_path)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
