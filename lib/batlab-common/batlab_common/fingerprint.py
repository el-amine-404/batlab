"""Whether two recordings are one: Chromaprint fingerprints (the technique
behind AcoustID), the shorter slid along the longer, and the share of equal
bits at the best offset. The same recording scores about 0.99, another song
about 0.55 (chance is 0.5), whatever the codec or bitrate.

fpcalc and ffmpeg's chromaprint muxer give the same fingerprint (checked on
2026-10-15: 1.000 on three songs); fpcalc is about four times faster.
"""

import json
import shutil
import struct
import subprocess

SAME = 0.85   # this or more: one recording
OTHER = 0.70  # below this: another one


def fingerprint(path, seconds=None):
    """The raw fingerprint (32-bit integers, about 8 a second) of the first
    `seconds` of path, or all of it."""
    if shutil.which("fpcalc"):
        length = str(int(seconds)) if seconds else "0"
        out = subprocess.run(["fpcalc", "-raw", "-json", "-length", length, path], stdin=subprocess.DEVNULL,
                             capture_output=True, timeout=300, check=True).stdout
        return [value & 0xFFFFFFFF for value in json.loads(out)["fingerprint"]]
    limit = ["-t", str(int(seconds))] if seconds else []
    out = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", path, *limit, "-ac", "1",
                          "-f", "chromaprint", "-fp_format", "raw", "-"],
                         stdin=subprocess.DEVNULL, capture_output=True, timeout=300, check=True).stdout
    return list(struct.unpack(f"<{len(out) // 4}I", out[: len(out) // 4 * 4]))


def similarity(one, other):
    """The best share of equal bits, the shorter fingerprint slid along the
    longer: from 0.5 (unrelated) to 1.0."""
    if not one or not other:
        return 0.0
    short, long = (one, other) if len(one) <= len(other) else (other, one)
    best = 0
    for offset in range(len(long) - len(short) + 1):
        same = sum(32 - (a ^ b).bit_count() for a, b in zip(short, long[offset:]))
        best = max(best, same)
    return best / (32 * len(short))


def same_recording(path, other_path):
    return similarity(fingerprint(path), fingerprint(other_path)) >= SAME
