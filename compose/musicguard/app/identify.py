"""What a recording really is, asked of Shazam through SongRec.

SongRec computes Shazam's fingerprint locally (frequency peaks, not audio) and
sends only that. Shazam knows far more Moroccan and streaming-only music than
MusicBrainz's AcoustID, which is why it identifies here and MusicBrainz only
supplies IDs and credits afterwards.

A match is used only when it is unambiguous; "no match" is never a verdict,
only an absence of one.
"""

import json
import re
import subprocess
import time
import unicodedata

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

NOISE = re.compile(r"(?i)\s*[\(\[][^\)\]]*\b(?:feat|ft|with|prod|remaster|version|edit|mix|explicit|clean)\b[^\)\]]*[\)\]]")
FEAT = re.compile(r"(?i)\s+(?:feat\.?|ft\.?|featuring|with|x|&|,|et|و)\s+")


def simple(text):
    text = unicodedata.normalize("NFKD", NOISE.sub("", text or "")).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", "", text)


def names(artist):
    """The separate artists in a credit: "Manal & Stormy" -> {"manal", "stormy"}."""
    return {simple(part) for part in FEAT.split(artist or "") if simple(part)}


def same_title(one, other):
    a, b = simple(one), simple(other)
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a)))


def agrees(match, title, artist):
    """True when the file's title and artists are the recording Shazam heard."""
    return same_title(match.title, title) and bool(names(match.artist) & names(artist))


def contradicts(match, title, artist):
    """True only when Shazam is sure of something else: neither the title nor
    any artist overlaps. A remix, a feature or a translated title still shares
    one of them and is not called wrong."""
    return not same_title(match.title, title) and not (names(match.artist) & names(artist))


# --- versions of a song ----------------------------------------------------------

# A remix, a live take, a sped-up upload…: another recording of the song, named
# in brackets or after " - " ("PIRATE (Stormy Remix)", "PIRATE - Live").
# Edits, remasters and "explicit" are the same recording and are not listed.
VERSIONS = {
    "remix": r"re-?mix|rmx|(?<!original )(?<!album )(?<!radio )\bmix",
    "live": r"live|en concert",
    "acoustic": r"acoustic|unplugged|acoustique",
    "instrumental": r"instrumental|karaok[eé]",
    "a cappella": r"a ?cap+el+a",
    "extended": r"extended",
    "sped up": r"sped[ -]?up|speed[ -]?up|nightcore",
    "slowed": r"slowed",
    "rework": r"rework|bootleg|vip|flip",
    "cover": r"cover|reprise",
    "demo": r"demo",
}
_VERSION_PARTS = re.compile(r"[\(\[]([^\)\]]*)[\)\]]|\s[-–—]\s(.+)$")
_VERSION_WORDS = {name: re.compile(rf"(?i)\b(?:{pattern})\b") for name, pattern in VERSIONS.items()}


def version(title):
    """What sets this take apart from the song's original recording, as a
    sorted tuple ("live",), ("remix",); () for the original."""
    parts = " ".join(a or b for a, b in _VERSION_PARTS.findall(title or ""))
    return tuple(sorted(name for name, words in _VERSION_WORDS.items() if words.search(parts)))


def same_version(one, other):
    return version(one) == version(other)
