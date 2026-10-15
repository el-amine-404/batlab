"""What a recording really is, asked of Shazam through SongRec.

SongRec computes Shazam's fingerprint locally (frequency peaks, not audio) and
sends only that. Shazam knows far more Moroccan and streaming-only music than
MusicBrainz's AcoustID, which is why it identifies here and MusicBrainz only
supplies IDs and credits afterwards.

A match is used only when it is unambiguous; "no match" is never a verdict,
only an absence of one.
"""

import json
import subprocess
import time

from batlab_common import titles

SONGREC = "songrec"
TIMEOUT = 90
PAUSE = 3.0  # between requests: Shazam throttles bursts

_last_request = 0.0


class Match:
    def __init__(self, title, artist, album="", year="", isrc="", apple_id="", genre=""):
        self.title = title
        self.artist = artist
        self.album = album
        self.year = year
        self.isrc = isrc
        self.apple_id = apple_id  # the Apple Music track, for apple.py
        self.genre = genre

    def __repr__(self):
        return f"{self.artist} - {self.title}" + (f" [{self.album}]" if self.album else "")


def parse(answer):
    """A Match from Shazam's JSON, or None when it recognised nothing."""
    track = (answer or {}).get("track") or {}
    title, artist = (track.get("title") or "").strip(), (track.get("subtitle") or "").strip()
    if not title or not artist:
        return None
    album = year = ""
    for section in track.get("sections") or []:
        for item in section.get("metadata") or []:
            if item.get("title") == "Album":
                album = (item.get("text") or "").strip()
            elif item.get("title") == "Released":
                year = (item.get("text") or "").strip()[:4]
    apple_id = ""
    for action in (track.get("hub") or {}).get("actions") or []:
        if action.get("type") == "applemusicplay" and str(action.get("id") or "").isdigit():
            apple_id = str(action["id"])
    genre = ((track.get("genres") or {}).get("primary") or "").strip()
    return Match(title, artist, album, year if year.isdigit() else "", (track.get("isrc") or "").strip(),
                 apple_id, genre)


def recognise(path):
    """Ask Shazam about the middle of the file. Match, or None."""
    global _last_request
    wait = PAUSE - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    result = subprocess.run([SONGREC, "audio-file-to-recognized-song", path], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=TIMEOUT, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip()[-300:] or "songrec failed")
    try:
        return parse(json.loads(result.stdout))
    except ValueError as error:
        raise RuntimeError(f"unreadable Shazam answer: {error}") from error


# --- comparing a match with what a file says it is ------------------------------

def agrees(match, title, artist):
    """True when the file's title and artists are the recording Shazam heard."""
    return titles.agrees(match.title, match.artist, title, artist)


def contradicts(match, title, artist):
    """True only when Shazam is sure of something else: neither the title nor
    any artist overlaps. A remix, a feature or a translated title still shares
    one of them and is not called wrong."""
    return titles.contradicts(match.title, match.artist, title, artist)
