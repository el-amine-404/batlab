"""Whether two titles and artists name one song, and which version of it.

"PIRATE (feat. X) [Official Video]" and "PIRATE" are one title; "Manal &
Stormy" and "Stormy" share an artist. "PIRATE (Club Remix)" is the same
title too, but another version (version()): a remix, a live or sped-up take
is never the original's copy.
"""

import re
import unicodedata

# Brackets that say nothing about which song it is: features, producers, edits.
NOISE = re.compile(r"(?i)\s*[\(\[][^\)\]]*\b(?:feat|ft|with|prod|remaster|version|edit|mix|explicit|clean)\b[^\)\]]*[\)\]]")
FEAT = re.compile(r"(?i)\s+(?:feat\.?|ft\.?|featuring|with|x|&|,|et|و)\s+")


def simple(text):
    """Lower case, no accents, brackets of noise or punctuation: "Mômes (feat. X)" → "momes"."""
    text = unicodedata.normalize("NFKD", NOISE.sub("", text or "")).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", "", text)


def names(artist):
    """The separate artists in a credit: "Manal & Stormy" -> {"manal", "stormy"}."""
    return {simple(part) for part in FEAT.split(artist or "") if simple(part)}


def same_title(one, other):
    a, b = simple(one), simple(other)
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a)))


def agrees(title, artist, other_title, other_artist):
    """The same title and an artist in common."""
    return same_title(title, other_title) and bool(names(artist) & names(other_artist))


def contradicts(title, artist, other_title, other_artist):
    """Surely another song: neither the title nor any artist in common. A
    remix, a feature or a translated title still shares one of them."""
    return not same_title(title, other_title) and not (names(artist) & names(other_artist))


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
