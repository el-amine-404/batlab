"""The same song filed twice: which copy stays, and with which tags.

Two files are one song when they share an ISRC or a MusicBrainz recording ID,
or when their titles and an artist agree (identify.agrees) and their lengths
are within 5 s. Within 30 s they are one song only if they sound alike
(reference.SAME): a video's copy often carries an intro or an outro (on
2026-10-14, a YouTube "Mghayer (Prod. By Ysos) #CAMÉLÉON" ran 220 s, Spotify's
"Mghayer" 214 s), while a remix or a live take sounds different.

The best audio stays: a library musicguard does not own (Lidarr's, Aurral's)
always wins; then lossless; then the copy without extra seconds; then the
higher bitrate, Opus and AAC counted as worth more per kbit than MP3. The best
tags go on it: a MusicBrainz ID, then a clean title (no hashtags, brackets or
"Official Video"), then the most complete set; whatever it lacks is taken
from the others.
"""

import re

import identify

SAME_LENGTH = 5      # seconds: one song
ALIKE_LENGTH = 30    # seconds: one song if the fingerprints agree
PADDED = 3           # seconds longer than the shortest copy: an intro or outro
EFFICIENCY = {"opus": 1.5, "aac": 1.3, "vorbis": 1.3, "mp3": 1.0}
DIRTY_TITLE = re.compile(r"(?i)#|\b(official|video|clip|lyrics?|audio|visuali[sz]er|prod\.?|hd|4k)\b|[\[(]")
COMPLETENESS = ("album", "albumartist", "date", "tracknumber", "isrc", "genre")


class Track:
    def __init__(self, path, tags, seconds, codec="", bitrate=0, owned=True):
        self.path = path
        self.tags = tags            # canonical key -> list of values (tags.collect)
        self.seconds = seconds
        self.codec = codec
        self.bitrate = bitrate      # bits per second
        self.owned = owned          # in music-extras: musicguard may move or remove it

    def first(self, key):
        values = self.tags.get(key) or []
        return values[0] if values else ""

    def __repr__(self):
        return f"Track({self.path!r})"


def same_song(a, b, alike):
    """alike(a, b) -> bool compares fingerprints; called only when needed."""
    for key in ("isrc", "musicbrainz_trackid"):
        if a.first(key) and a.first(key) == b.first(key):
            return True
    if not identify.agrees(identify.Match(a.first("title"), a.first("artist")),
                           b.first("title"), b.first("artist")):
        return False
    gap = abs(a.seconds - b.seconds)
    if gap <= SAME_LENGTH:
        return True
    return gap <= ALIKE_LENGTH and alike(a, b)


def groups(tracks, alike):
    """Lists of two or more tracks that are one song, each with an owned one.
    Only tracks sharing an artist name are compared."""
    by_artist = {}
    for track in tracks:
        for name in identify.names(track.first("artist")) or {""}:
            by_artist.setdefault(name, []).append(track)
    parent = {id(t): t for t in tracks}

    def root(track):
        while parent[id(track)] is not track:
            track = parent[id(track)]
        return track

    for bucket in by_artist.values():
        for i, a in enumerate(bucket):
            for b in bucket[i + 1:]:
                if root(a) is not root(b) and (a.owned or b.owned) and same_song(a, b, alike):
                    parent[id(root(b))] = root(a)
    found = {}
    for track in tracks:
        found.setdefault(id(root(track)), []).append(track)
    return [g for g in found.values() if len(g) > 1 and any(t.owned for t in g)]


def best_audio(group):
    shortest = min(t.seconds for t in group)

    def rank(t):
        return (not t.owned, t.codec in ("flac", "alac"), t.seconds - shortest <= PADDED,
                t.bitrate * EFFICIENCY.get(t.codec, 1.0))
    return max(group, key=rank)


def best_tags(group):
    """The merged tags: the best copy's, completed from the others."""
    def rank(t):
        title = t.first("title")
        return (bool(t.first("musicbrainz_trackid")), not DIRTY_TITLE.search(title),
                sum(bool(t.first(k)) for k in COMPLETENESS), -len(title))
    ordered = sorted(group, key=rank, reverse=True)
    merged = {key: list(values) for key, values in ordered[0].tags.items()}
    for other in ordered[1:]:
        for key, values in other.tags.items():
            if key not in merged and values:
                merged[key] = list(values)
    return merged
