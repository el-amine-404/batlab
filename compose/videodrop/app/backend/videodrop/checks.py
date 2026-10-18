"""Whether a finished file is the video it should be, read by ffprobe: a
container that matches its extension, a video stream, a sound stream, and
the length the site said (a shorter file was cut off). A file that fails
is not kept.

A video file holds no program to run; what could be wrong with one is that
it is broken, cut short or not a video at all, which this sees."""

import json
import subprocess
from pathlib import Path

# What ffprobe calls each container (format_name), by extension.
CONTAINERS = {".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".mkv": "matroska", ".webm": "webm"}
AUDIO_NAMES = {"aac": "AAC", "opus": "Opus", "mp3": "MP3", "vorbis": "Vorbis", "flac": "FLAC", "ac3": "AC-3", "eac3": "E-AC-3"}
# A file this much shorter than the video is cut off: seconds, or a share.
SHORT_BY = (3.0, 0.02)


def probe(path: Path) -> dict:
    """ffprobe's reading of the file; raises ValueError when it can't read it."""
    try:
        answer = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=format_name,duration,size:stream=codec_type,codec_name,width,height,disposition",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"ffprobe couldn't read it ({exc.__class__.__name__})") from exc
    if answer.returncode != 0:
        raise ValueError(f"ffprobe couldn't read it: {answer.stderr.strip()[:200] or 'no reason given'}")
    return json.loads(answer.stdout or "{}")


def check(path: Path, expected_duration: float | None) -> dict:
    """{"ok", "problems": [...], "summary": "AV1 1920×1080 · Opus · 3:45", "duration"}."""
    problems: list[str] = []
    try:
        found = probe(path)
    except ValueError as exc:
        return {"ok": False, "problems": [str(exc)], "summary": None, "duration": None}
    streams = found.get("streams") or []
    fmt = found.get("format") or {}
    video = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    expected = CONTAINERS.get(path.suffix.lower())
    if expected and expected not in (fmt.get("format_name") or ""):
        problems.append(f"It is {fmt.get('format_name') or 'not a video'}, not the {path.suffix} its name says.")
    if not video:
        problems.append("It has no video stream.")
    if not audio:
        problems.append("It has no sound.")
    try:
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError):
        duration = None
    if expected_duration and duration is not None:
        short = expected_duration - duration
        if short > max(SHORT_BY[0], expected_duration * SHORT_BY[1]):
            problems.append(f"It is {short:.0f} s shorter than the video: the download was cut off.")
    parts = []
    if video:
        v = video[0]
        parts.append(f"{(v.get('codec_name') or '?').upper()} {v.get('width')}×{v.get('height')}")
    if audio:
        name = audio[0].get("codec_name") or "?"
        parts.append(AUDIO_NAMES.get(name, name.upper()))
    if duration:
        parts.append(f"{int(duration // 60)}:{int(duration % 60):02d}")
    # Sound missing is said, not refused: some videos have none.
    fatal = [p for p in problems if p != "It has no sound."]
    return {"ok": not fatal, "problems": problems, "summary": " · ".join(parts) or None, "duration": duration}


def embed_subtitles(video: Path, srt: Path, title: str) -> None:
    """Adds srt to video as one more subtitle track named title, in place;
    raises ValueError when ffmpeg fails (the video is left as it was)."""
    codec = "mov_text" if video.suffix.lower() in (".mp4", ".m4v", ".mov") else "srt"
    out = video.with_name(f".{video.stem}.lyrics{video.suffix}")
    existing = sum(1 for s in probe(video).get("streams") or [] if s.get("codec_type") == "subtitle")
    command = ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(srt), "-map", "0", "-map", "1",
               "-c", "copy", f"-c:s:{existing}", codec, f"-metadata:s:s:{existing}", f"title={title}",
               f"-metadata:s:s:{existing}", "language=und", str(out)]
    answer = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    if answer.returncode != 0 or not out.is_file():
        out.unlink(missing_ok=True)
        raise ValueError(f"ffmpeg couldn't add them: {answer.stderr.strip()[:200]}")
    out.replace(video)
