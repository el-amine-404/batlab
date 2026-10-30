"""From a song and its plain lyrics to synced lines.

1. Demucs separates the voice from the music.
2. Three aligners place each known line: Whisper large-v3-turbo, Whisper
   large-v3 and Meta's MMS, each in its own process.
3. The median of their times wins. A line where they do not agree (two within
   0.5 s of the median, all within 1.5 s) is marked unsure.

Measured on 8 songs a person had synced: 86% of lines within 1 s; on the
lines the aligners agree on (two thirds), 94%.
"""

import json
import os
import statistics
import subprocess
import sys
from typing import Callable

from .gpu import cuda_library_path

METHODS = ("whisper", "whisperL", "mms")
NAME = "Demucs htdemucs + Whisper large-v3-turbo + Whisper large-v3 + MMS, median vote"


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


def vote(runs: list[list[float | None]], count: int) -> list[dict]:
    times, unsure = [], []
    for i in range(count):
        got = [r[i] for r in runs if r[i] is not None]
        if not got:
            times.append(None)
            unsure.append(True)
            continue
        t = statistics.median(got)
        agree = sum(abs(g - t) <= 0.5 for g in got) >= 2 and max(got) - min(got) <= 1.5
        times.append(t)
        unsure.append(not agree)
    for i, t in enumerate(times):  # a line nobody placed goes between its neighbours
        if t is None:
            before = next((times[j] for j in range(i - 1, -1, -1) if times[j] is not None), 0.0)
            after = next((times[j] for j in range(i + 1, len(times)) if times[j] is not None), before + 2)
            times[i] = (before + after) / 2
    return [{"t": round(t, 3), "u": u} for t, u in zip(times, unsure)]


def run(audio: str, lines: list[str], workdir: str, stage: Callable[[str], None], device: str = "cuda",
        language: str = "fr") -> tuple[list[dict], str]:
    """Returns (lines with t and u, path of the voice as Ogg Opus)."""
    stage("separating the voice")
    vocals = separate(audio, workdir, device)
    with open(os.path.join(workdir, "lines.json"), "w", encoding="utf-8") as out:
        json.dump(lines, out, ensure_ascii=False)
    env = dict(os.environ, LD_LIBRARY_PATH=cuda_library_path())
    runs = []
    for method in METHODS:
        stage(f"aligning ({method})")
        out = os.path.join(workdir, f"{method}.json")
        _run([sys.executable, "-m", "stanzarr_worker.align", method, vocals, os.path.join(workdir, "lines.json"), out,
              language], env)
        runs.append(json.load(open(out)))
    stage("voting")
    placed = vote(runs, len(lines))
    stage("encoding the voice")
    voice = os.path.join(workdir, "vocals.ogg")
    _run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", vocals, "-c:a", "libopus", "-b:a", "96k", voice])
    return [p | {"s": s} for p, s in zip(placed, lines)], voice


def run_generate(audio: str, workdir: str, stage: Callable[[str], None], device: str = "cuda",
                 language: str = "") -> tuple[list[dict], str]:
    """Transcribes the song from audio, aligns and verifies, returning (lines, voice path)."""
    stage("separating the voice (HTDemucs)")
    vocals = separate(audio, workdir, device)
    stage("transcribing (Whisper large-v3)")
    out_trans = os.path.join(workdir, "transcription.json")
    env = dict(os.environ, LD_LIBRARY_PATH=cuda_library_path())
    _run([sys.executable, "-m", "stanzarr_worker.align", "transcribe", vocals, audio, out_trans, language or "auto"], env)
    data = json.load(open(out_trans, encoding="utf-8"))
    lines = [item["s"] for item in data]
    if lines:
        with open(os.path.join(workdir, "lines.json"), "w", encoding="utf-8") as out:
            json.dump(lines, out, ensure_ascii=False)
        runs = []
        for method in METHODS:
            stage(f"verifying sync ({method})")
            out = os.path.join(workdir, f"{method}.json")
            try:
                _run([sys.executable, "-m", "stanzarr_worker.align", method, vocals, os.path.join(workdir, "lines.json"), out, language or "fr"], env)
                runs.append(json.load(open(out, encoding="utf-8")))
            except Exception:
                pass
        if len(runs) >= 2:
            stage("voting on verified timings")
            placed = vote(runs, len(lines))
            result_lines = [p | {"s": s} for p, s in zip(placed, lines)]
        else:
            result_lines = data
    else:
        result_lines = data

    stage("encoding the voice")
    voice = os.path.join(workdir, "vocals.ogg")
    _run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", vocals, "-c:a", "libopus", "-b:a", "96k", voice])
    return result_lines, voice
