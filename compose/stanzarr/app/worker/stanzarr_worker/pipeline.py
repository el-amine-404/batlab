"""From a song and its plain lyrics to synced lines.

1. Demucs separates the voice from the music.
2. For generation: Whisper large-v3-turbo transcribes with musical prompting,
   word-level timestamps, smart phrase splitting, and backing vocal detection.
3. Multi-aligner verification: Whisper large-v3-turbo, Whisper large-v3 and
   Meta's MMS cross-verify timings.
4. The median of their times wins.
"""

import json
import os
import statistics
import subprocess
import sys
from typing import Callable

from .gpu import cuda_library_path

METHODS = ("whisper", "whisperL", "mms")
NAME = "Demucs htdemucs + Whisper large-v3-turbo + MMS, verified sync"


def _run(args: list[str], env: dict | None = None) -> None:
    result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"{os.path.basename(args[2]) if len(args) > 2 else args[0]} failed: "
                           + (result.stderr.strip().splitlines() or ["?"])[-1][:300])


def separate(audio: str, workdir: str, device: str) -> str:
    try:
        _run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "--segment", "7", "-d", device,
              "-o", os.path.join(workdir, "sep"), audio])
    except RuntimeError as e:
        if device != "cpu" and ("out of memory" in str(e).lower() or "cuda" in str(e).lower()):
            _run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "--segment", "7", "-d", "cpu",
                  "-o", os.path.join(workdir, "sep"), audio])
        else:
            raise
    stem = os.path.splitext(os.path.basename(audio))[0]
    return os.path.join(workdir, "sep", "htdemucs", stem, "vocals.wav")


def vote(runs: list[list[float | None]], count: int, fallback_times: list[float] | None = None) -> list[dict]:
    times, unsure = [], []
    for i in range(count):
        got = [r[i] for r in runs if r[i] is not None]
        if not got:
            fb = fallback_times[i] if fallback_times and i < len(fallback_times) else None
            times.append(fb)
            unsure.append(True)
            continue
        t = statistics.median(got)
        agree = sum(abs(g - t) <= 0.6 for g in got) >= 2 and max(got) - min(got) <= 1.8
        times.append(t)
        unsure.append(not agree)
    for i, t in enumerate(times):
        if t is None:
            before = next((times[j] for j in range(i - 1, -1, -1) if times[j] is not None), 0.0)
            after = next((times[j] for j in range(i + 1, len(times)) if times[j] is not None), before + 2)
            times[i] = (before + after) / 2
    return [{"t": round(t, 3), "u": u} for t, u in zip(times, unsure)]


def detect_text_language(lines: list[str]) -> str:
    sample = " ".join(lines[:15]).lower()
    # Check common English markers
    if any(w in sample for w in (" the ", " and ", " you ", " me ", " to ", " in ", " my ", " is ")):
        return "en"
    # Check French markers
    if any(w in sample for w in (" le ", " la ", " les ", " et ", " des ", " dans ", " pour ", " que ")):
        return "fr"
    # Check Spanish markers
    if any(w in sample for w in (" el ", " la ", " y ", " que ", " en ", " de ", " por ", " un ")):
        return "es"
    return "en"


def run(audio: str, lines: list[str], workdir: str, stage: Callable[[str], None], device: str = "cuda",
        language: str = "") -> tuple[list[dict], str]:
    """Returns (lines with t and u, path of the voice as Ogg Opus)."""
    stage("separating the voice (HTDemucs)")
    vocals = separate(audio, workdir, device)
    with open(os.path.join(workdir, "lines.json"), "w", encoding="utf-8") as out:
        json.dump(lines, out, ensure_ascii=False)
    env = dict(os.environ, LD_LIBRARY_PATH=cuda_library_path())
    lang = language or detect_text_language(lines)
    runs = []
    for method in METHODS:
        stage(f"aligning ({method})")
        out = os.path.join(workdir, f"{method}.json")
        try:
            _run([sys.executable, "-m", "stanzarr_worker.align", method, vocals, os.path.join(workdir, "lines.json"), out,
                  lang], env)
            runs.append(json.load(open(out)))
        except Exception:
            pass
    stage("voting")
    placed = vote(runs, len(lines)) if runs else [{"t": 0.0, "u": True} for _ in lines]
    stage("encoding the voice")
    voice = os.path.join(workdir, "vocals.ogg")
    _run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", vocals, "-c:a", "libopus", "-b:a", "96k", voice])
    return [p | {"s": s} for p, s in zip(placed, lines)], voice


def run_generate(audio: str, workdir: str, stage: Callable[[str], None], device: str = "cuda",
                 song: dict | None = None, lines_input: list[str] | None = None) -> tuple[list[dict], str]:
    """Transcribes the song with musical prompting, aligns and verifies, returning (lines, voice path)."""
    song = song or {}
    stage("separating the voice (HTDemucs)")
    vocals = separate(audio, workdir, device)
    stage("transcribing (Whisper large-v3-turbo)")
    meta_path = os.path.join(workdir, "meta.json")
    meta_payload = {
        "artist": song.get("artist", ""),
        "title": song.get("title", ""),
        "album": song.get("album", ""),
        "language": song.get("language", ""),
        "lines_ref": lines_input or [],
    }
    with open(meta_path, "w", encoding="utf-8") as mf:
        json.dump(meta_payload, mf, ensure_ascii=False)

    out_trans = os.path.join(workdir, "transcription.json")
    env = dict(os.environ, LD_LIBRARY_PATH=cuda_library_path())
    _run([sys.executable, "-m", "stanzarr_worker.align", "transcribe", vocals, meta_path, out_trans, device], env)
    trans_data = json.load(open(out_trans, encoding="utf-8"))
    trans_lines = trans_data.get("lines", [])
    detected_lang = trans_data.get("detected_language") or song.get("language") or "en"
    lines_text = [item["s"] for item in trans_lines]
    orig_times = [item["t"] for item in trans_lines]

    if lines_text:
        with open(os.path.join(workdir, "lines.json"), "w", encoding="utf-8") as out:
            json.dump(lines_text, out, ensure_ascii=False)
        runs = []
        for method in METHODS:
            stage(f"verifying sync ({method})")
            out = os.path.join(workdir, f"{method}.json")
            try:
                _run([sys.executable, "-m", "stanzarr_worker.align", method, vocals, os.path.join(workdir, "lines.json"), out, detected_lang], env)
                runs.append(json.load(open(out, encoding="utf-8")))
            except Exception:
                pass
        if len(runs) >= 2:
            stage("voting on verified timings")
            placed = vote(runs, len(lines_text), fallback_times=orig_times)
            result_lines = [p | {"s": s} for p, s in zip(placed, lines_text)]
        else:
            result_lines = trans_lines
    else:
        result_lines = trans_lines

    stage("encoding the voice")
    voice = os.path.join(workdir, "vocals.ogg")
    _run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", vocals, "-c:a", "libopus", "-b:a", "96k", voice])
    return result_lines, voice
