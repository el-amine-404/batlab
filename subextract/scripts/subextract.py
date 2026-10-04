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

Existing and newly extracted subtitles are checked against audio with ffsubsync,
half by half. Replacements are verified and written atomically, with backups.
This checks speech timing, not the meaning or accuracy of a translation.

A language with only a Blu-ray image (PGS) track is OCR'd with Tesseract
(subextract/ocr), and installed only when its word confidences are high enough.

Run with --dry-run to print decisions, --file VIDEO to handle one file, or
--all to go through the whole library once.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import wave
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEXT_CODECS = {"ass": ".ass", "ssa": ".ass", "subrip": ".srt", "mov_text": ".srt", "webvtt": ".vtt"}
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".sup"}
NOT_DIALOGUE = re.compile(r"\b(?:signs?|songs?|forced|commentary|karaoke|lyrics|fx)\b|\bcc\b only", re.I)
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


def non_dialogue(title: str) -> bool:
    if re.search(r"\b(?:forced|commentary|karaoke)\b", title, re.I):
        return True
    if re.search(r"\b(?:full|dialogue|dialog)\b", title, re.I):
        return False
    return bool(NOT_DIALOGUE.search(title))


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
        if language in ("", "und", "zxx", "mis") or disposition.get("forced") or non_dialogue(title):
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
        if path.name == video.stem + ".srt":
            found.setdefault("und", []).append(path)
            continue
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
    subprocess.run(command, check=True, capture_output=True, timeout=1800)
    for track, out in outputs:
        text = out.read_text(encoding="utf-8-sig", errors="replace")
        track.srt = keeper.ass_to_srt(text) if out.suffix == ".ass" else text
        measure(track)


class AudioChecker(keeper.SubtitleChecker):
    """Use audio only, disable *both* framerate heuristics, check API success.

    ffsubsync's CLI can exit zero even when alignment failed. Its structured
    result is required; an offset in the log alone is not evidence of success.
    """

    def reference_audio(self) -> Path:
        if self.reference is None:
            streams = [s for s in keeper.audio_streams(self.new)
                       if not (s.get("disposition") or {}).get("comment")
                       and not re.search(r"commentary|audio description", (s.get("tags") or {}).get("title", ""), re.I)]
            if not streams:
                raise ValueError("no dialogue audio to verify against")
            main = next((s for s in streams if (s.get("disposition") or {}).get("default")), streams[0])
            self.reference = self.workdir / "reference.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(self.new_path),
                            "-map", f"0:{main['index']}", "-threads", "1", "-ac", "1", "-ar", "16000",
                            str(self.reference)], check=True, capture_output=True, timeout=1800)
        return self.reference

    def ffsubsync(self, subtitle: Path) -> float:
        reference = getattr(self, "segment_reference", None) or self.reference_audio()
        speech = reference.with_suffix(".npz")
        options = [] if speech.exists() else ["--serialize-speech"]
        reference = speech if speech.exists() else reference
        driver = ("import json,sys; from ffsubsync.ffsubsync import run,make_parser; "
                  "r=run(make_parser().parse_args(sys.argv[1:])); "
                  "print('SUBEXTRACT_RESULT='+json.dumps(r))")
        result = subprocess.run(["docker", "exec", "-e", "PYTHONPATH=/app/bazarr/bin/libs",
                                 "-e", "OMP_NUM_THREADS=1", "-e", "OPENBLAS_NUM_THREADS=1", self.container,
                                 "python3", "-c", driver, self.in_container(reference),
                                 "-i", self.in_container(subtitle), "-o", self.in_container(self.workdir / "synced.srt"),
                                 "--vad", "webrtc", "--no-fix-framerate", "--skip-infer-framerate-ratio",
                                 "--encoding", "utf-8", *options], capture_output=True, text=True, timeout=1800)
        marker = next((line.partition("=")[2] for line in result.stdout.splitlines()
                       if line.startswith("SUBEXTRACT_RESULT=")), "{}")
        report = json.loads(marker)
        if result.returncode or "sync_was_successful" not in report:
            raise RuntimeError(f"ffsubsync failed: {(result.stderr or result.stdout)[-300:]}")
        offset = report.get("offset_seconds")
        if not report["sync_was_successful"] or offset is None or not math.isfinite(offset):
            raise ValueError("audio alignment was unsuccessful")
        if abs(report.get("framerate_scale_factor", 0) - 1) > 0.001:
            raise ValueError("audio alignment required a framerate change")
        return offset

    def offset(self, sidecar: Path) -> tuple[int | None, str]:
        """Compare each subtitle half to the matching half of the audio.

        Leaving the omitted half as subtitle silence corrupts the alignment
        score, especially for the second half with a long silent prefix.
        Rebase both audio and subtitles to zero for each comparison instead.
        """
        text = sidecar.read_text(encoding="utf-8-sig", errors="strict")
        duration_ms = round(keeper.video_length(self.new) * 1000)
        middle_ms = duration_ms // 2
        windows = [(0, middle_ms), (middle_ms, duration_ms)]
        halves = [srt_window(text, start, end) for start, end in windows]
        if min(len(SRT_CUE.findall(half)) for half in halves) < keeper.MIN_CUES_PER_HALF:
            return None, "too few lines to check its timing"
        reference = self.reference_audio()
        offsets = []
        try:
            for name, (start, end), half in zip(("first", "second"), windows, halves):
                audio = self.workdir / f"{name}-audio.wav"
                if not audio.exists():
                    with wave.open(str(reference), "rb") as source:
                        rate = source.getframerate()
                        first_frame = round(start * rate / 1000)
                        last_frame = min(source.getnframes(), round(end * rate / 1000))
                        if first_frame >= last_frame:
                            return None, "reference audio does not cover both video halves"
                        source.setpos(first_frame)
                        with wave.open(str(audio), "wb") as output:
                            output.setparams(source.getparams())
                            output.writeframes(source.readframes(last_frame - first_frame))
                subtitle = self.workdir / f"{name}.srt"
                subtitle.write_text(half, encoding="utf-8")
                self.segment_reference = audio
                offsets.append(self.ffsubsync(subtitle))
        finally:
            self.segment_reference = None
        shift, reason = keeper.halves_verdict(*offsets)
        return shift, reason.replace("the releases are cut differently", "timing verification is inconclusive")


class Fit:
    """Whether existing sidecars fit the video, with one reference audio
    extraction per video however many sidecars are checked."""

    def __init__(self, video: Path, info: dict, data_root: Path, workdir: Path, container: str) -> None:
        self.checker = AudioChecker(video, info, data_root, workdir, container)

    def __call__(self, sidecar: Path) -> tuple[bool, str]:
        text = sidecar.read_text(encoding="utf-8-sig", errors="strict")
        reason = srt_problem(text, keeper.video_length(self.checker.new))
        if reason:
            return False, reason
        try:
            shift, reason = self.checker.offset(sidecar)
        except ValueError as error:
            return False, str(error)
        if shift is None:
            return False, reason
        if abs(shift) > 500:
            return False, f"it is {shift / 1000:+.2f} s off"
        return True, f"audio timing OK ({shift / 1000:+.2f} s; halves agree within 0.3 s)"


IMAGE_CODECS = {"hdmv_pgs_subtitle"}
OCR_IMAGE = "batlab/pgsocr:1"
OCR_LANGUAGES = {"en": "eng", "fr": "fra", "ar": "ara", "it": "ita", "es": "spa", "pt": "por", "de": "deu"}
# What an OCR'd subtitle must reach to be installed; see the README's measurements.
OCR_MIN_CONFIDENCE = 85.0
OCR_MAX_LOW_SHARE = 0.05
OCR_MIN_CUES = 20


@dataclass
class ImageTrack:
    index: int
    language: str
    title: str
    default: bool
    frames: int


def image_tracks(info: dict) -> list[ImageTrack]:
    tracks = []
    for stream in info.get("streams", []):
        if stream.get("codec_type") != "subtitle" or stream.get("codec_name") not in IMAGE_CODECS:
            continue
        tags, disposition = stream.get("tags") or {}, stream.get("disposition") or {}
        language = (tags.get("language") or "").lower()
        title = tags.get("title") or ""
        if language in ("", "und", "zxx", "mis") or disposition.get("forced") or non_dialogue(title):
            continue
        frames = int(tags.get("NUMBER_OF_FRAMES") or tags.get("NUMBER_OF_FRAMES-eng") or 0)
        tracks.append(ImageTrack(stream["index"], two_letter(language), title, bool(disposition.get("default")), frames))
    return tracks


def choose_image(tracks: list[ImageTrack]) -> ImageTrack | None:
    """The default track, else the one with the most pictures: a forced or
    signs track has few, and was already ruled out by its flags or title."""
    return min(tracks, key=lambda t: (not t.default, -t.frames)) if tracks else None


def ocr_verdict(report: dict) -> str:
    """Why an OCR result is not good enough, or "" when it is."""
    if report.get("cues", 0) < OCR_MIN_CUES:
        return f"only {report.get('cues', 0)} lines read"
    if report.get("unreadable_share", 0) > 0.05:
        return f"{report['unreadable_share']:.0%} of displayed subtitle images were unreadable"
    if not all(math.isfinite(report.get(k, float("nan"))) for k in ("mean_confidence", "low_confidence_share")):
        return "missing or non-finite OCR confidence"
    if report.get("mean_confidence", 0) < OCR_MIN_CONFIDENCE:
        return f"mean confidence {report['mean_confidence']:.0f}%, under {OCR_MIN_CONFIDENCE:.0f}%"
    if report.get("low_confidence_share", 1) > OCR_MAX_LOW_SHARE:
        return f"{report['low_confidence_share']:.0%} of words read with under 60% confidence"
    return ""


class Ocr:
    """Runs subextract/ocr (Tesseract) in its image, on one extracted track."""

    def __init__(self, image: str = OCR_IMAGE, cpus: int = 2) -> None:
        self.image, self.cpus = image, cpus

    def available(self) -> bool:
        return subprocess.run(["docker", "image", "inspect", self.image], capture_output=True, timeout=30).returncode == 0

    def run(self, video: Path, track: ImageTrack, language: str, workdir: Path, data_root: Path) -> tuple[str, dict]:
        sup, srt, report = (workdir / f"image{track.index}.{suffix}" for suffix in ("sup", "srt", "json"))
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(video), "-map", f"0:{track.index}",
                        "-c", "copy", str(sup)], check=True, capture_output=True, timeout=1800)

        name = "subextract-ocr-" + uuid.uuid4().hex
        try:
            subprocess.run(["docker", "run", "--rm", "--name", name, "--network", "none",
                            "--cpus", str(self.cpus), "--memory", "2g", "-e", "OMP_THREAD_LIMIT=1",
                            "--user", f"{os.getuid()}:{os.getgid()}",
                            "-v", f"{workdir}:/work", self.image, f"/work/{sup.name}", f"/work/{srt.name}",
                            "--lang", language, "--report", f"/work/{report.name}", "--jobs", str(self.cpus)],
                           check=True, capture_output=True, timeout=3600)
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        return srt.read_text(encoding="utf-8"), json.loads(report.read_text(encoding="utf-8"))


def clean_srt(text: str) -> str:
    """Sorted, renumbered, without empty or repeated cues, each at least 0.3 s."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "").strip()):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            match = SRT_CUE.search(line)
            if match:
                a, b, c, d, e, f, g, h = (int(x) for x in match.groups())
                start, end = ((a * 60 + b) * 60 + c) * 1000 + d, ((e * 60 + f) * 60 + g) * 1000 + h
                body = "\n".join(part.rstrip() for part in lines[i + 1:] if part.strip())
                if body:
                    cues.append((start, max(end, start + 300), body))
                break
    cues = sorted(set(cues))
    out = []
    for n, (start, end, body) in enumerate(cues, 1):
        out.append(f"{n}\n{keeper_time(start)} --> {keeper_time(end)}\n{body}\n")
    return "\n".join(out)


def keeper_time(ms: int) -> str:
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def srt_window(text: str, start_ms: int, end_ms: int) -> str:
    """Clip cues to an audio window and rebase its timestamps to zero."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "").strip()):
        lines = block.splitlines()
        match = SRT_CUE.fullmatch(lines[1].strip()) if len(lines) >= 3 else None
        if not match:
            raise ValueError("malformed SRT cue")
        a, b, c, d, e, f, g, h = map(int, match.groups())
        start = max(((a * 60 + b) * 60 + c) * 1000 + d, start_ms)
        end = min(((e * 60 + f) * 60 + g) * 1000 + h, end_ms)
        if end > start:
            cues.append(f"{len(cues) + 1}\n{keeper_time(start - start_ms)} --> {keeper_time(end - start_ms)}\n"
                        + "\n".join(lines[2:]) + "\n")
    return "\n".join(cues)


def srt_problem(text: str, duration: float) -> str:
    """Reject malformed, empty, corrupted or out-of-range subtitles."""
    if not math.isfinite(duration) or duration <= 0:
        return "video duration is unavailable"
    blocks = re.split(r"\n\s*\n", text.replace("\r", "").strip())
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            return "malformed or empty SRT cue"
        match = SRT_CUE.fullmatch(lines[1].strip())
        if not match:
            return "malformed SRT timestamp"
        a, b, c, d, e, f, g, h = map(int, match.groups())
        start, end = a * 3600 + b * 60 + c + d / 1000, e * 3600 + f * 60 + g + h / 1000
        if max(b, c, f, g) >= 60 or end <= start or end > duration + 2:
            return "invalid cue duration or cue beyond the video"
        body = re.sub(r"<[^>]*>", "", "\n".join(lines[2:])).strip()
        if not body or "\ufffd" in body or "\x00" in body:
            return "empty or corrupt subtitle text"
    return ""


def file_stamp(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ino]


def atomic_text(path: Path, text: str) -> None:
    """Stage on the destination filesystem; never truncate an existing file."""
    fd, name = tempfile.mkstemp(prefix=".subextract-", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text)
            output.flush()
            os.fchmod(output.fileno(), 0o644)
            os.fsync(output.fileno())
        temp.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


@dataclass
class Outcome:
    written: list[str]
    replaced: list[str]
    kept: list[str]
    skipped: list[str]
    refused: list[str] = None

    def __post_init__(self) -> None:
        self.refused = self.refused or []


def process(video: Path, data_root: Path, container: str, apply: bool,
            languages: set[str] | None = None, ocr: "Ocr | None" = None) -> Outcome:
    outcome = Outcome([], [], [], [])
    original_video = file_stamp(video)
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
                            capture_output=True, text=True, check=True, timeout=120)
    info = json.loads(result.stdout)
    by_language: dict[str, list[Track]] = {}
    for track in text_tracks(info):
        if languages is None or track.language in languages:
            by_language.setdefault(track.language, []).append(track)
    images: dict[str, list[ImageTrack]] = {}
    for track in image_tracks(info):
        if languages is None or track.language in languages:
            images.setdefault(track.language, []).append(track)
    for stream in info.get("streams", []):
        language = two_letter((stream.get("tags") or {}).get("language", "").lower())
        if (stream.get("codec_type") == "subtitle" and stream.get("codec_name") not in {*TEXT_CODECS, *IMAGE_CODECS}
                and (languages is None or language in languages) and language not in by_language and language not in images):
            outcome.skipped.append(f".{language}: unsupported subtitle codec {stream.get('codec_name')}")
    existing = sidecars_by_language(video)
    audit_languages = {lang for lang, paths in existing.items()
                       if (languages is None or lang in languages or lang == "und")
                       and any(p.suffix.lower() == ".srt" for p in paths)}
    if not by_language and not images and not audit_languages:
        return outcome
    workroot = data_root / "recycle" / ".subextract"
    workroot.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="work-", dir=workroot))
    # Bazarr may run as a different UID; it must read and write checker files.
    workdir.chmod(0o777)
    try:
        fit = Fit(video, info, data_root, workdir, container)
        replace: dict[str, list[tuple[Path, str]]] = {}   # language -> sidecars that do not fit
        checked: dict[str, bool] = {}
        snapshots = {p: file_stamp(p) for paths in existing.values() for p in paths}

        def needed(language: str) -> bool:
            if language in checked:
                return checked[language]
            srts = [p for p in existing.get(language, []) if p.suffix.lower() == ".srt"]
            if not srts:
                checked[language] = True
                return True
            bad = []
            for sidecar in srts:
                fits, reason = fit(sidecar)
                if fits:
                    outcome.kept.append(f"{sidecar.name[len(video.stem):]} ({reason})")
                else:
                    bad.append((sidecar, reason))
            # One sidecar that fits is enough for its language.
            if len(bad) == len(srts):
                replace[language] = bad
                checked[language] = True
                return True
            checked[language] = False
            outcome.refused += [f"{sidecar.name[len(video.stem):]} failed verification ({reason}); "
                                "left in place because another SRT for this language fits"
                                for sidecar, reason in bad]
            return False

        def install(language: str, srt: str, source: str) -> bool:
            problem = srt_problem(srt, keeper.video_length(info))
            if problem:
                outcome.refused.append(f".{language} {source}: {problem}")
                return False
            srt = clean_srt(srt)
            candidate = workdir / f"candidate.{language}.srt"
            candidate.write_text(srt, encoding="utf-8")
            fits, reason = fit(candidate)
            if not fits:
                outcome.refused.append(f".{language} {source}: {reason}")
                return False
            target = video.with_name(f"{video.stem}.{language}.srt")
            bad = replace.get(language, [])
            if apply:
                if file_stamp(video) != original_video:
                    raise RuntimeError("video changed during extraction; retry after the import finishes")
                current = sidecars_by_language(video).get(language, [])
                if set(current) != set(existing.get(language, [])) or any(file_stamp(p) != snapshots[p] for p in current):
                    raise RuntimeError("sidecars changed during verification; retry")
                # Copy originals before installing; keep them in place on write failure.
                backup_root = data_root / "recycle" / "subextract" / uuid.uuid4().hex
                for sidecar, _reason in bad:
                    aside = backup_root / sidecar.relative_to(data_root)
                    aside.parent.mkdir(parents=True, exist_ok=True)
                    with sidecar.open("rb") as src, aside.open("xb") as dest:
                        shutil.copyfileobj(src, dest)
                        dest.flush()
                        os.fsync(dest.fileno())
                atomic_text(target, srt)
                for sidecar, _reason in bad:
                    if sidecar != target:
                        sidecar.unlink()
            outcome.replaced += [f"{sidecar.name[len(video.stem):]} ({why})" for sidecar, why in bad]
            # An image fallback can resolve a text candidate's failed check.
            outcome.refused = [item for item in outcome.refused if not item.startswith(f".{language} ")]
            outcome.written.append(f".{language}.srt {source}; {reason}")
            return True

        for language in sorted(audit_languages):
            needed(language)
        text_wanted = {language: tracks for language, tracks in by_language.items() if needed(language)}
        if text_wanted:
            extract(video, [t for tracks in text_wanted.values() for t in tracks], workdir)
        installed = set()
        for language, tracks in text_wanted.items():
            chosen = choose(tracks)
            if chosen:
                if install(language, chosen.srt, f"from {chosen.title or f'track {chosen.index}'} ({chosen.lines} lines)"):
                    installed.add(language)
            else:
                if language not in images:
                    outcome.refused.append(f".{language}: no usable text track")

        for language, tracks in images.items():
            if language in installed or not needed(language):
                continue
            chosen = choose_image(tracks)
            if ocr is None:
                outcome.skipped.append(f".{language}: only a Blu-ray image track, and OCR is off")
                continue
            if chosen is None or language not in OCR_LANGUAGES:
                outcome.skipped.append(f".{language}: no image track OCR can read")
                continue
            srt, report = ocr.run(video, chosen, OCR_LANGUAGES[language], workdir, data_root)
            verdict = ocr_verdict(report)
            if verdict:
                outcome.refused.append(f".{language} OCR of {chosen.title or f'track {chosen.index}'}: {verdict}"
                                       + "".join(f"\n  › {line}" for line in report.get("low_confidence_samples", [])[:2]))
                continue
            install(language, srt, f"by OCR of {chosen.title or f'track {chosen.index}'} "
                                   f"({report['cues']} lines, {report['mean_confidence']:.0f}% confidence)")
        for language, bad in replace.items():
            if not any(w.startswith(f".{language}.srt") for w in outcome.written):
                outcome.refused += [f"{sidecar.name[len(video.stem):]} failed verification ({reason}) and nothing could replace it"
                                    for sidecar, reason in bad]
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
    def walk_error(error: OSError) -> None:
        raise error
    for root in ("media/tv", "media/movies"):
        library = data_root / root
        if not library.is_dir():
            raise ValueError(f"library directory is missing: {library}")
        for directory, _, files in os.walk(library, onerror=walk_error):
            found += [Path(directory, f) for f in files if Path(f).suffix.lower() in VIDEO_EXTENSIONS]
    return sorted(found)


def jellyfin_url() -> str:
    """Jellyfin has no fixed address on the Docker network, so ask Docker."""
    address = subprocess.run(["docker", "inspect", "jellyfin", "--format",
                              "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"],
                             capture_output=True, text=True).stdout.split()
    return f"http://{address[0]}:8096" if address else ""


def tell_jellyfin(env_file: Path, url: str, paths: list[Path], data_root: Path) -> None:
    if not paths:
        return
    key = keeper.env_value(env_file, "JELLYFIN_API_KEY")
    url = url or jellyfin_url()
    if not key or not url or not paths:
        return
    body = {"Updates": [{"Path": "/data/" + str(p.relative_to(data_root)), "UpdateType": "Modified"} for p in paths]}
    request = urllib.request.Request(f"{url}/Library/Media/Updated", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f'MediaBrowser Token="{key}"'})
    urllib.request.urlopen(request, timeout=30).close()


def post(webhook: str, title: str, colour: int, sections: list[tuple[str, list[str]]], footer: str) -> None:
    fields = []
    for name, lines in sections:
        if not lines:
            continue
        value = ""
        for index, line in enumerate(lines):
            addition = ("\n" if value else "") + "• " + line
            if len(value) + len(addition) > 1000:
                value += f"\n… and {len(lines) - index} more"
                break
            value += addition
        fields.append({"name": f"{name} ({len(lines)})", "value": value, "inline": False})
    if not fields or not webhook.startswith("http"):
        return
    payload = {"username": "subtitles", "embeds": [{"title": title, "color": colour, "fields": fields[:10],
                                                     "footer": {"text": footer},
                                                     "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}]}
    request = urllib.request.Request(webhook, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "batlab-subextract"})
    urllib.request.urlopen(request, timeout=15).close()


def notify(env_file: Path, written: list[str], refused: list[str], replaced: list[str], errors: list[str],
           footer: str) -> None:
    """New subtitles to #downloads, anything to look at to #download-issues."""
    try:
        post(keeper.env_value(env_file, "DISCORD_WEBHOOK_DOWNLOADS"), "📝 Subtitles added", 3066993,
             [("written", written)], footer)
        post(keeper.env_value(env_file, "DISCORD_WEBHOOK_DOWNLOAD_ISSUES"), "🟠 Subtitles need a look", 15105570,
             [("refused", refused), ("replaced after failed verification", replaced), ("errors", errors)], footer)
    except (urllib.error.URLError, OSError) as error:
        print(f"Discord was not told: {error}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="verify using temporary files; leave library subtitles and state unchanged")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--file", help="handle this one video")
    scope.add_argument("--all", action="store_true", help="go through media/tv and media/movies recursively")
    parser.add_argument("--env-file", default=str(REPO / "compose/.env"))
    parser.add_argument("--data-root", default="/mnt/storage/data")
    parser.add_argument("--sonarr-url", default="http://172.19.10.11:8989")
    parser.add_argument("--radarr-url", default="http://172.19.10.12:7878")
    parser.add_argument("--jellyfin-url", default="", help="default: the jellyfin container's address")
    parser.add_argument("--ffsubsync-container", default="bazarr")
    parser.add_argument("--no-ocr", action="store_true", help="never OCR Blu-ray image subtitles")
    parser.add_argument("--ocr-image", default=OCR_IMAGE, help="Docker image containing the PGS OCR worker")
    parser.add_argument("--ocr-cpus", type=int, choices=range(1, 9), default=2, help="CPUs for one OCR container")
    parser.add_argument("--quiet", action="store_true", help="post nothing to Discord")
    parser.add_argument("--languages", default="en,fr,ar",
                        help="two-letter codes to give an SRT, comma-separated (Bazarr's profile); 'all' for every one")
    parser.add_argument("--days", type=float, default=3, help="how far back to look for imports")
    parser.add_argument("--settle-minutes", type=float, default=30,
                        help="wait this long after an import, so the dub keeper and Bazarr go first")
    parser.add_argument("--state-dir", default=os.path.join(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"),
                                                            "batlab-subextract"))
    args = parser.parse_args(argv)
    data_root = Path(args.data_root).resolve()
    if not data_root.is_dir():
        parser.error(f"data root is not mounted or does not exist: {data_root}")
    args.data_root = str(data_root)
    if args.file:
        args.file = str(Path(args.file).resolve())
        if not Path(args.file).is_relative_to(data_root):
            parser.error("--file must be inside --data-root")
    # Lock by data root, not state directory: timer/manual runs must not overlap.
    lock_root = data_root / "recycle" / ".subextract"
    lock_root.mkdir(parents=True, exist_ok=True)
    with (lock_root / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if args.all:
                print("another subextract run is active; waiting to start the library batch", flush=True)
                fcntl.flock(lock, fcntl.LOCK_EX)
            else:
                print("another subextract run is active; leaving it to finish", flush=True)
                return 0
        try:
            return run_batch(args)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            print(f"subextract: {error}", file=sys.stderr, flush=True)
            return 1


def fingerprint(video: Path, settings: dict) -> dict:
    return {"video": file_stamp(video), "settings": settings,
            "sidecars": {str(p): file_stamp(p) for paths in sidecars_by_language(video).values() for p in paths}}


def run_batch(args: argparse.Namespace) -> int:
    env_file, data_root = Path(args.env_file), Path(args.data_root)
    for binary in ("ffmpeg", "ffprobe", "docker"):
        if not shutil.which(binary):
            raise RuntimeError(f"required command is missing: {binary}")

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

    ocr = None
    subprocess.run(["docker", "exec", "-e", "PYTHONPATH=/app/bazarr/bin/libs", args.ffsubsync_container,
                    "python3", "-c", "import ffsubsync.ffsubsync, webrtcvad"], check=True,
                   capture_output=True, timeout=30)
    if not args.no_ocr:
        ocr = Ocr(image=args.ocr_image, cpus=args.ocr_cpus)
        if not ocr.available():
            raise RuntimeError(f"{args.ocr_image} is unavailable; build with make -C subextract ocr-image, or explicitly use --no-ocr")
    languages = None if args.languages.lower() == "all" else {two_letter(code.strip().lower()) for code in args.languages.split(",")}
    if languages is not None and any(not re.fullmatch(r"[a-z]{2,3}", code) for code in languages):
        raise ValueError("--languages must contain comma-separated language codes")
    written, refused, replaced, errors, skipped = [], [], [], [], []
    started = dt.datetime.now()

    state_path = Path(args.state_dir) / "handled.json"
    handled = json.loads(state_path.read_text()) if state_path.exists() else {}
    if not isinstance(handled, dict):
        raise ValueError(f"invalid state file: {state_path}")
    digest = hashlib.sha256()
    for source in (Path(__file__), REPO / "ass2srt/scripts/ass2srt.py", REPO / "dubkeeper/scripts/dubkeeper.py",
                   REPO / "subextract/ocr/pgsocr.py"):
        digest.update(source.read_bytes())
    image_id = "off"
    if ocr:
        image_id = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", ocr.image],
                                  capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    settings = {"code": digest.hexdigest(), "languages": sorted(languages) if languages is not None else "all",
                "ocr": image_id, "checker": args.ffsubsync_container}
    changed, failed, cached, processed = [], 0, 0, 0
    print(f"Starting: {len(videos)} videos; languages={args.languages}; OCR={'on' if ocr else 'off'}; dry_run={args.dry_run}", flush=True)
    for number, video in enumerate(videos, 1):
        try:
            before = fingerprint(video, settings)
            if handled.get(str(video)) == before and not args.file:
                cached += 1
                continue
            print(f"[{number}/{len(videos)}] checking {video}", flush=True)
            processed += 1
            outcome = process(video, data_root, args.ffsubsync_container, apply=not args.dry_run,
                              languages=languages, ocr=ocr)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError, OSError, ValueError) as error:
            detail = error.stderr[-300:] if isinstance(error, subprocess.CalledProcessError) and error.stderr else error
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            print(f"{video.name}: {detail}", file=sys.stderr, flush=True)
            errors.append(f"`{video.name}`: {str(detail).strip()[-200:]}")
            failed += 1
            continue
        written += [f"`{video.name}`: {item}" for item in outcome.written]
        refused += [f"`{video.name}`: {item}" for item in outcome.refused]
        replaced += [f"`{video.name}`: {item}" for item in outcome.replaced]
        skipped += [f"`{video.name}`: {item}" for item in outcome.skipped]
        parts = [f"{'would write' if args.dry_run else 'wrote'} {', '.join(outcome.written)}"] if outcome.written else []
        parts += [f"replaced {', '.join(outcome.replaced)}"] if outcome.replaced else []
        parts += [f"kept {', '.join(outcome.kept)}"] if outcome.kept else []
        parts += [f"skipped {', '.join(outcome.skipped)}"] if outcome.skipped else []
        parts += [f"refused {'; '.join(outcome.refused)}"] if outcome.refused else []
        print(f"{video.name}: {'; '.join(parts) if parts else 'no eligible embedded subtitles'}", flush=True)
        if outcome.written:
            changed.append(video)
        if not args.dry_run:
            if not outcome.refused and not outcome.skipped and file_stamp(video) == before["video"]:
                handled[str(video)] = fingerprint(video, settings)
            else:
                handled.pop(str(video), None)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_text(state_path, json.dumps(handled, indent=1, sort_keys=True))
    print(f"Finished: checked={processed}, cached={cached}, written={len(written)}, refused={len(refused)}, skipped={len(skipped)}, errors={failed}", flush=True)

    if args.dry_run:
        return 1 if failed or refused else 0
    if not args.quiet:
        minutes = (dt.datetime.now() - started).total_seconds() / 60
        scope = "whole library" if args.all else (Path(args.file).name if args.file else "new imports")
        notify(env_file, written, refused, replaced, errors, f"{scope} · {len(videos)} files · {minutes:.0f} min")
    try:
        tell_jellyfin(env_file, args.jellyfin_url, changed, data_root)
    except (urllib.error.URLError, OSError) as error:
        print(f"Jellyfin was not told about new subtitles: {error}", file=sys.stderr)
    return 1 if failed or refused else 0


if __name__ == "__main__":
    sys.exit(main())
