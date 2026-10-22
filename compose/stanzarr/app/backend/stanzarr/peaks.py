"""Loudness of a track every 5 ms, for the editor's waveform.

Decoded by ffmpeg at 8 kHz, a rate that is an exact multiple of 200 points a
second: an inexact one drifts the drawing late through the song.
"""

import json
import os
import subprocess

import numpy as np

RATE = 200
DECODE_RATE = 8000


def compute(path: str) -> dict:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-ac", "1", "-ar", str(DECODE_RATE),
                          "-f", "f32le", "-"], capture_output=True, check=True, timeout=600).stdout
    audio = np.abs(np.frombuffer(raw, np.float32))
    step = DECODE_RATE // RATE
    usable = len(audio) // step * step
    values = audio[:usable].reshape(-1, step).max(axis=1) if usable else np.zeros(0, np.float32)
    top = float(values.max()) if len(values) else 0.0
    if top > 0:
        values = values / top
    return {"rate": RATE, "peaks": [round(float(v), 3) for v in values]}


def cached(cache_dir: str, key: str, path: str) -> bytes:
    target = os.path.join(cache_dir, f"peaks-{key}.json")
    if os.path.exists(target):
        with open(target, "rb") as handle:
            return handle.read()
    data = json.dumps(compute(path), separators=(",", ":")).encode()
    with open(target + ".tmp", "wb") as out:
        out.write(data)
    os.replace(target + ".tmp", target)
    return data
