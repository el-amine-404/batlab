"""Is a downloaded file the Spotify song it was downloaded for?

spotDL picks a YouTube upload by title and length; a re-upload can carry the
right title and length over other audio (on 2026-10-14 "ElGrandeToto - la
paille (Ft Farid Bang)" was a Bhojpuri song). The file is compared with
Spotify's own 30 s preview: Chromaprint fingerprints of both (ffmpeg's
chromaprint muxer), the preview slid along the file, the share of equal bits
at the best offset. The same song scores about 0.9, another about 0.55, as in
musicguard's reference.py.

Standard library only: this runs in spotDL's virtualenv.
"""

import re
import struct
import subprocess
import tempfile
import urllib.request

OTHER = 0.70  # below: another recording
MAX_PREVIEW_BYTES = 2_000_000
PREVIEW = re.compile(r"https://p\.scdn\.co/mp3-preview/[0-9a-f]+")
AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


def _get(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read(limit + 1)


def preview_url(track_id: str) -> str | None:
    """The preview's URL from the track's embed page; Spotify's API no longer
    gives one to new apps."""
    page = _get(f"https://open.spotify.com/embed/track/{track_id}", 3_000_000).decode(errors="replace")
    found = PREVIEW.search(page)
    return found.group(0) if found else None


def fingerprint(path: str) -> list[int]:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-t", "600", "-ac", "1",
         "-f", "chromaprint", "-fp_format", "raw", "-"],
        capture_output=True, timeout=180, check=True).stdout
    return list(struct.unpack(f"<{len(out) // 4}I", out[: len(out) // 4 * 4]))


def similarity(short: list[int], long: list[int]) -> float:
    """Best share of equal bits with short slid along long."""
    if not short or not long:
        return 0.0
    if len(short) > len(long):
        short, long = long, short
    best = 0
    for offset in range(len(long) - len(short) + 1):
        same = sum(32 - (a ^ b).bit_count() for a, b in zip(short, long[offset:]))
        best = max(best, same)
    return best / (32 * len(short))


def score(path: str, track_id: str) -> float | None:
    """How much the file sounds like Spotify's preview, or None when there is
    no preview to compare with (or Spotify could not be reached)."""
    try:
        url = preview_url(track_id)
        if not url:
            return None
        data = _get(url, MAX_PREVIEW_BYTES)
    except Exception:
        return None
    if len(data) > MAX_PREVIEW_BYTES:
        return None
    with tempfile.NamedTemporaryFile(suffix=".mp3") as preview:
        preview.write(data)
        preview.flush()
        return similarity(fingerprint(preview.name), fingerprint(path))


def candidates(song, tried: set[str], limit: int = 4) -> list[str]:
    """Other YouTube uploads of the song, of its length within 7 s."""
    import yt_dlp  # spotDL's own, installed with it

    artists = ", ".join(song.artists or []) or song.artist or ""
    query = f"ytsearch15:{artists} - {song.name}"
    options = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        entries = (ydl.extract_info(query, download=False) or {}).get("entries") or []
    found = []
    for entry in entries:
        url = f"https://www.youtube.com/watch?v={entry.get('id')}"
        duration = entry.get("duration")
        if not entry.get("id") or url in tried or duration is None:
            continue
        if abs(float(duration) - float(song.duration)) <= 7:
            found.append(url)
        if len(found) >= limit:
            break
    return found
