"""What a copy that goes hands to the copy that stays: its lyrics, and its
album's cover, when the one that stays lacks them.

Lyrics go over only as good as they are known to be. Stanzarr stops asking
LRCLIB about a song once it has a synced .lrc not flagged AI-generated, so a
synced file of unknown origin (a downloader's, an old tool's) would keep a
better one out for good: it goes over as plain text instead, which never stops
Stanzarr. Synced lyrics also go over only when both copies last the same
(within SYNC_GAP): a video's copy with an intro is timed seconds off the
album's. A person's lyrics replace LRCLIB's or AI ones the new copy already
got; nothing else is ever overwritten. The going copy's own files follow it to
quarantine, so whatever is not handed over can still be found there.

A cover goes over only between copies of one album: a single's or a video's
picture is not the album's cover.
"""

import os
import re

SYNC_GAP = 1.5  # seconds
# Stanzarr's origins (its lyricsfiles.origin), worst to best. "unknown" is
# never handed over synced; an AI file stays replaceable by Stanzarr.
RANK = {"ai": 1, "lrclib": 2, "person": 3}
INFO_TAG = re.compile(r"^\s*\[([a-z#]+):(.*)\]\s*$", re.I)
TIMESTAMP = re.compile(r"\[\d+:\d+(?:[.:]\d+)?\]|<\d+:\d+(?:[.:]\d+)?>")
COVER_NAMES = ("cover", "folder", "front", "album")
COVER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def origin(text):
    """lrclib, person, ai or unknown, as Stanzarr reads it."""
    tags = {}
    for line in text.splitlines()[:40]:
        found = INFO_TAG.match(line)
        if found:
            tags.setdefault(found.group(1).lower(), found.group(2).strip())
    by, re_ = tags.get("by", ""), tags.get("re", "").lower()
    if by.startswith("AI-generated"):
        return "ai"
    if "lrclib" in re_:
        return "lrclib"
    if "stanzarr" in re_ or "a person" in by:
        return "person"
    return "unknown"


def plain(text):
    """Synced lyrics as plain text: no timings, no info tags."""
    lines = []
    for line in text.splitlines():
        if INFO_TAG.match(line):
            continue
        lines.append(TIMESTAMP.sub("", line).strip())
    return "\n".join(lines).strip() + "\n"


def lyrics(old_audio, new_audio, old_seconds, new_seconds):
    """[(path to write, text, what it is)] for the copy that stays."""
    old, new = os.path.splitext(old_audio)[0], os.path.splitext(new_audio)[0]
    if old == new:
        return []  # one name: the lyrics are already the staying copy's
    synced, text = read(old + ".lrc"), read(old + ".txt")
    has_lrc, has_txt = read(new + ".lrc"), os.path.exists(new + ".txt")
    plan = []
    if synced and synced.strip():
        kind = origin(synced)
        timed = abs(old_seconds - new_seconds) <= SYNC_GAP
        better = kind in RANK and (has_lrc is None or RANK.get(origin(has_lrc), 99) < RANK[kind])
        if better and timed:
            plan.append((new + ".lrc", synced, f"synced lyrics ({kind})"))
        elif has_lrc is None and not has_txt and not text:
            why = "of unknown origin" if kind not in RANK else "timed for a copy of another length"
            plan.append((new + ".txt", plain(synced), f"plain lyrics (the synced ones were {why})"))
            has_txt = True
    if text and text.strip() and not has_txt:
        plan.append((new + ".txt", text, "plain lyrics"))
    return plan


def cover_in(directory):
    for name in COVER_NAMES:
        for extension in COVER_EXTENSIONS:
            path = os.path.join(directory, name + extension)
            if os.path.isfile(path) and not os.path.islink(path):
                return path
    return None


def album_key(tags):
    album = (tags.get("album") or [""])[0]
    return re.sub(r"\W+", "", re.sub(r"[\[(].*?[\])]", "", album).casefold())


def cover(old_audio, new_audio, old_tags, new_tags):
    """(source, target) of a cover for the staying copy's folder, or None."""
    old_dir, new_dir = os.path.dirname(old_audio), os.path.dirname(new_audio)
    if old_dir == new_dir or cover_in(new_dir):
        return None
    key = album_key(old_tags)
    if not key or key != album_key(new_tags):
        return None
    source = cover_in(old_dir)
    if not source:
        return None
    return source, os.path.join(new_dir, "cover" + os.path.splitext(source)[1].lower())


# "01 - ", "1-01 - ", "2-05 - ": Lidarr's track and disc numbers before a title.
TRACK_PREFIX = re.compile(r"^(?:\d+-)?\d+\s*-\s*")


def title_of(stem):
    return TRACK_PREFIX.sub("", os.path.basename(stem)).casefold().strip()


def renamed_track(lyrics_path, audio_extensions):
    """The track a lyrics file was left behind by, after a rename: the one
    audio file of its title in its folder, else in its artist's other album
    folders (an album folder renamed). None when there is none or several."""
    title = title_of(os.path.splitext(lyrics_path)[0])
    if not title:
        return None
    folder = os.path.dirname(lyrics_path)
    artist = os.path.dirname(folder)
    places = [folder] + sorted(os.path.join(artist, d) for d in _listdir(artist)
                               if os.path.join(artist, d) != folder)
    for place in places:
        found = [os.path.join(place, n) for n in _listdir(place)
                 if os.path.splitext(n)[1].lower() in audio_extensions
                 and not n.startswith(".") and title_of(os.path.splitext(n)[0]) == title]
        if len(found) == 1:
            return found[0]
        if found:
            return None  # several of that title: not a guess to make
    return None


def better_lyrics(new_text, old_text):
    """Whether new_text should replace old_text: a person's or LRCLIB's
    synced lyrics over AI or unknown ones, a person's over LRCLIB's."""
    rank = {"unknown": 0, **RANK}
    return rank[origin(new_text)] > rank[origin(old_text)] and origin(new_text) in ("person", "lrclib")


def _listdir(path):
    try:
        return os.listdir(path) if os.path.isdir(path) else []
    except OSError:
        return []

