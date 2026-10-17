"""Titles as people read them, and which upload is which song.

An upload's title carries what a song's name doesn't: "(Official Video)",
"[4K]", "Artist - Title" when the channel isn't the artist's. clean() takes
those away; best_audio() and best_video() pick, among YouTube Music's search
results, the official audio of a video, or the video of an Apple Music entry
(there is no link between them without a signed-in account).
"""

import re

from batlab_common import titles

# Brackets that say what kind of upload it is, not which song: any language
# YouTube shows on music channels.
_UPLOAD_WORDS = (
    r"official|oficial|officiel(?:le)?|ufficiale|offizielles?|"
    r"video|vídeo|vidéo|videoclip|clip|mv|m/v|visuali[sz]er|lyrics?|lyric video|paroles|letra|"
    r"audio|hd|hq|4k|8k|uhd|1080p|720p|remastered in 4k|premiere|exclusive"
)
_UPLOAD_BRACKET = re.compile(rf"(?i)\s*[\(\[【「](?:[^\)\]】」]*\b(?:{_UPLOAD_WORDS})\b[^\)\]】」]*)[\)\]】」]")
_TRAILING = re.compile(rf"(?i)\s*[|｜]\s*.*$|\s+[-–—]\s+(?:{_UPLOAD_WORDS})(?:\s+(?:{_UPLOAD_WORDS}))*\s*$")
_ARTIST_SEP = re.compile(r"\s+[-–—]\s+")
# What a channel adds to an artist's name.
_CHANNEL_SUFFIX = re.compile(r"(?i)\s*(?:-\s*topic|vevo|official|oficial|officiel|music|tv|channel)\s*$")


def channel_artist(name: str) -> str:
    """ "DuaLipaVEVO" → "DuaLipa", "Bulin 47 Oficial" → "Bulin 47"."""
    before = None
    name = (name or "").strip()
    while before != name:
        before = name
        name = _CHANNEL_SUFFIX.sub("", name).strip()
    return name or before


def clean(title: str, artist: str = "") -> tuple[str, str | None]:
    """(the song's title, the artist credit the title names, if any).

    "Bulin 47 X Kevin Wai - Dame Hombro (Video Oficial)", "Bulin 47 Oficial"
    → ("Dame Hombro", "Bulin 47 X Kevin Wai")."""
    text = _UPLOAD_BRACKET.sub("", title or "").strip()
    text = _TRAILING.sub("", text).strip()
    credit = None
    parts = _ARTIST_SEP.split(text, maxsplit=1)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        known = titles.names(channel_artist(artist)) | titles.names(artist)
        # "Artist - Title" when the left part names the channel's artist, or
        # when the channel is a label's: the left part is still a credit.
        if titles.names(left) & known or not known or _looks_like_credit(left):
            credit, text = left, right
    text = re.sub(r"\s{2,}", " ", text).strip(" -–—")
    return (text or (title or "").strip()), credit


def _looks_like_credit(text: str) -> bool:
    return bool(re.search(r"(?i)\s(?:x|&|feat\.?|ft\.?|featuring|vs\.?)\s", f" {text} "))


# ---- picking among search results --------------------------------------------------

def _seconds(item: dict) -> int | None:
    value = item.get("duration_seconds")
    return int(value) if isinstance(value, (int, float)) else None


def _artists(item: dict) -> str:
    return " & ".join(a.get("name", "") for a in item.get("artists") or [] if a.get("name"))


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", (text or "").casefold()) if len(w) > 1}


def score(want_title: str, want_artist: str, want_seconds: int | None,
          title: str, artist: str, seconds: int | None, shorter: int, longer: int) -> float | None:
    """How surely (title, artist, seconds) is the song wanted: None when it
    surely isn't, else higher is better (about 1 at most). seconds may be up
    to shorter seconds below want_seconds, or longer above it."""
    clean_title, credit = clean(title, artist)
    names = titles.names(artist) | titles.names(channel_artist(artist)) | titles.names(credit or "")
    wanted = titles.names(want_artist) | titles.names(channel_artist(want_artist))
    if not titles.same_title(want_title, clean_title):
        return None
    if not titles.same_version(want_title, clean_title):
        return None
    shared = names & wanted
    if wanted and not shared:
        return None
    value = 0.6 + 0.2 * (len(shared) / max(1, len(wanted)))
    if titles.simple(want_title) == titles.simple(clean_title):
        value += 0.1
    # "(Piano Version)", "(Sped Up)": words the wanted title doesn't have.
    extra = _words(clean_title) - _words(want_title) - _words(want_artist)
    value -= 0.03 * len(extra)
    if want_seconds and seconds:
        gap = seconds - want_seconds
        if gap < -shorter or gap > longer:
            return None
        value += 0.1 * (1 - abs(gap) / (shorter if gap < 0 else longer))
    return round(value, 4)


def best_audio(title: str, artist: str, seconds: int | None, results: list[dict]) -> dict | None:
    """The official audio (YouTube Music "song") of a video. A video often runs
    longer than its song (an intro, a skit, a short film around it): the song
    may be up to 15 min shorter, and 20 s longer."""
    picked, best = None, 0.0
    for item in results:
        if item.get("resultType") not in (None, "song") or not item.get("videoId"):
            continue
        value = score(title, artist, seconds, item.get("title", ""), _artists(item), _seconds(item), shorter=900, longer=20)
        if value is not None and value > best:
            picked, best = item, value
    return picked


def best_video(title: str, artist: str, seconds: int | None, results: list[dict]) -> dict | None:
    """The music video of a song: the official one (OMV) before any other. It
    may run 3 min longer than the song, and 20 s shorter."""
    picked, best = None, 0.0
    for item in results:
        if not item.get("videoId"):
            continue
        value = score(title, artist, seconds, item.get("title", ""), _artists(item), _seconds(item), shorter=20, longer=180)
        if value is None:
            continue
        if item.get("videoType") == "MUSIC_VIDEO_TYPE_OMV":
            value += 0.5
        if value > best:
            picked, best = item, value
    return picked
