"""A music video's lyrics as a subtitle track: its synced lyrics from LRCLIB
(lrclib.net, free, no key), turned into SubRip (SRT) for ffmpeg to embed.
Only when the job asks (options.lyrics); the song is the job's own (its
caller knows it) or guessed from the video's title.

Run by runner.py, in the download's process: the network calls stay there."""

import json
import re
from collections import Counter
import urllib.error
import urllib.parse
import urllib.request

API = "https://lrclib.net/api"
USER_AGENT = "videodrop (batlab, a home server)"
TIMEOUT = 15
# How far a recording may be from the video's length and still be its song.
DURATION_SLACK = 8
# Real lyrics: at least this many timed lines, the last one this far into the song.
MIN_LINES = 8
MIN_SPAN = 0.4

# What uploads add to a title that isn't the song's: "(Official Video)",
# "[4K]", "| Official Music Video", "(Lyrics)".
_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(?:official|video|audio|lyrics?|visuali[sz]er|4k|hd|hq|remaster(?:ed)?|clip|mv)\b[^\)\]]*[\)\]]"
    r"|\s*[|•].*$", re.I)
_LRC_LINE = re.compile(r"^\[(\d+):(\d{2})(?:[.:](\d{1,3}))?\](.*)$")


def guess(info: dict) -> tuple[str, str] | None:
    """(artist, title) of the song a video carries: what the site says
    (YouTube Music fills track and artist), else "Artist - Title", else the
    channel as the artist."""
    if info.get("track") and (info.get("artist") or info.get("creator")):
        artist = info.get("artist") or info.get("creator")
        return artist.split(",")[0].strip(), info["track"].strip()
    title = _NOISE.sub("", info.get("title") or "").strip()
    if " - " in title:
        artist, song = (part.strip() for part in title.split(" - ", 1))
        return (artist, song) if artist and song else None
    channel = re.sub(r"(?i)(?:\s*-\s*topic|vevo|\s+official)$", "", info.get("uploader") or info.get("channel") or "")
    return (channel.strip(), title) if channel.strip() and title else None


def _get(path: str, params: dict):
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            return json.load(answer)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def timed_lines(lrc: str, words_only: bool = True) -> list[tuple[float, str]]:
    """LRC's timed lines, in order; with words_only, only those with words
    (an empty one marks a pause)."""
    lines = []
    for raw in (lrc or "").splitlines():
        found = _LRC_LINE.match(raw.strip())
        if found:
            minutes, seconds, fraction, text = found.groups()
            at = int(minutes) * 60 + int(seconds) + (int(fraction.ljust(3, "0")) / 1000 if fraction else 0)
            if text.strip() or not words_only:
                lines.append((at, text.strip()))
    return sorted(lines, key=lambda line: line[0])


def plausible(lrc: str, duration: float | None) -> bool:
    """Real synced lyrics, not a placeholder ("[00:00.00]probe" is on
    LRCLIB): enough lines, spread over a fair part of the song, none after
    its end."""
    lines = timed_lines(lrc)
    if len(lines) < MIN_LINES:
        return False
    last = lines[-1][0]
    if duration:
        return duration * MIN_SPAN <= last <= duration + DURATION_SLACK
    return last >= 60


def choose(candidates: list[dict], duration: float | None) -> str | None:
    """Of LRCLIB's records, the synced lyrics to use: plausible ones of a
    recording as long as the video (DURATION_SLACK), then the text most of
    them agree on (uploaders differ by a line or two), then the closest
    length."""
    usable = []
    for candidate in candidates:
        lrc = candidate.get("syncedLyrics")
        if not lrc or candidate.get("instrumental") or not plausible(lrc, duration):
            continue
        gap = abs((candidate.get("duration") or duration or 0) - (duration or 0)) if duration else 0
        if gap <= DURATION_SLACK:
            usable.append((lrc, gap))
    if not usable:
        return None
    votes = Counter(" ".join(text.casefold() for _, text in timed_lines(lrc)) for lrc, _ in usable)
    return min(usable, key=lambda item: (-votes[" ".join(t.casefold() for _, t in timed_lines(item[0]))], item[1]))[0]


def find(artist: str, title: str, duration: float | None) -> str | None:
    """The synced lyrics (LRC) of the song, or None. Raises OSError when
    LRCLIB can't be reached."""
    params = {"artist_name": artist, "track_name": title}
    exact = _get("get", {**params, **({"duration": round(duration)} if duration else {})})
    found = _get("search", params) or []
    return choose(([exact] if exact else []) + list(found), duration)


def _stamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def to_srt(lrc: str, end: float | None) -> str:
    """LRC ("[01:02.50] words") as SRT: each line shown until the next one,
    the last one for 5 s (or until the video ends); an empty line ends the
    one before (a pause)."""
    lines = timed_lines(lrc, words_only=False)
    blocks = []
    for number, (at, text) in enumerate(lines):
        until = lines[number + 1][0] if number + 1 < len(lines) else min(at + 5, end or at + 5)
        if text and until > at:
            blocks.append(f"{len(blocks) + 1}\n{_stamp(at)} --> {_stamp(until)}\n{text}\n")
    return "\n".join(blocks)
