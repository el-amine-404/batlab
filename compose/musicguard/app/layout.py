"""Where a track lives: Album Artist/Album (Year)/NN - Title.ext

The same shape as Lidarr's naming (docs/music.md), so the library reads the
same whoever filed a track. Disc numbers prefix the track number only on
multi-disc releases: 2-05 - Title.flac.
"""

import os
import re
import unicodedata

REPLACE = {"/": "-", "\\": "-", ":": " -", "*": "-", "?": "", '"': "'", "<": "", ">": "", "|": "-"}
MAX_COMPONENT_BYTES = 180


def component(text, fallback):
    text = unicodedata.normalize("NFC", text or "")
    out = []
    for char in text:
        if char in REPLACE:
            out.append(REPLACE[char])
        elif unicodedata.category(char) in ("Cc", "Cf") and char not in "‌‍":
            continue
        else:
            out.append(char)
    text = re.sub(r"\s+", " ", "".join(out)).strip()
    # No hidden names, and nothing Windows (Samba) would refuse to show.
    text = text.lstrip(". ").rstrip(". ")
    while len(text.encode("utf-8")) > MAX_COMPONENT_BYTES:
        text = text[:-1].rstrip(". ")
    return text or fallback


def number(value):
    """(n, total) from "3" or "3/12"; (None, None) when absent."""
    if not value:
        return None, None
    parts = value.split("/")
    try:
        n = int(parts[0])
        total = int(parts[1]) if len(parts) > 1 and parts[1] else None
    except ValueError:
        return None, None
    return n, total


def first(tags, key):
    values = tags.get(key) or []
    return values[0] if values else ""


def relative(tags, extension):
    artist = component(first(tags, "albumartist") or first(tags, "artist"), "Unknown Artist")
    album = component(first(tags, "album"), "Unknown Album")
    year = (first(tags, "originaldate") or first(tags, "date"))[:4]
    album_dir = f"{album} ({year})" if year else album
    track, _ = number(first(tags, "tracknumber"))
    disc, discs = number(first(tags, "discnumber"))
    title = first(tags, "title") or "Unknown Title"
    if track:
        prefix = f"{disc}-{track:02d}" if disc and ((discs or 0) > 1 or disc > 1) else f"{track:02d}"
        name = f"{prefix} - {title}"
    else:
        name = title
    return os.path.join(artist, album_dir, component(name, "Unknown Title") + extension)


def free_path(path):
    """path, or path with " (2)", " (3)"... when something is already there."""
    if not os.path.lexists(path):
        return path
    stem, extension = os.path.splitext(path)
    for n in range(2, 1000):
        candidate = f"{stem} ({n}){extension}"
        if not os.path.lexists(candidate):
            return candidate
    raise FileExistsError(path)
