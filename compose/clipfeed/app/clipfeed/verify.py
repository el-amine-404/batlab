"""Whether a linked video is a real song, by YouTube Music (ytmusicapi, no
account): its kind (the artist's own video, the song itself, or someone's
upload), its title, artist, length and the day it came out.

The verdict:
- official: the artist's video or the song (YouTube Music's OMV or ATV), or
  an upload of a song YouTube Music has under the same title and artist
  (song_id is that song);
- likely: an upload YouTube Music doesn't know as a song (a small artist,
  a song not on streaming yet) whose title says it is the official video,
  and not a cover, a lyric video, a reaction…;
- unofficial: it plays, but nothing says it is a released song;
- unavailable: removed, private, or blocked.
"""

import asyncio
import re
import threading
from dataclasses import asdict, dataclass

from batlab_common import titles
from ytmusicapi import YTMusic

# Bumped when the rules below change: every video is checked again.
RULES = 2
KINDS = {"MUSIC_VIDEO_TYPE_OMV": "music_video", "MUSIC_VIDEO_TYPE_ATV": "song"}
_local = threading.local()

# What an upload's title adds to the song's: "(Official Video)", "[4K]", "| Prod. X".
_UPLOAD_WORDS = (r"official|oficial|officiel(?:le)?|video|vídeo|videoclip|clip|mv|m/v|visuali[sz]er|lyrics?|"
                 r"audio|hd|hq|4k|8k|uhd|1080p|premiere|exclusive|explicit|uncensored|prod\.?")
_BRACKET = re.compile(rf"(?i)\s*[\(\[【](?:[^\)\]】]*\b(?:{_UPLOAD_WORDS})\b[^\)\]】]*)[\)\]】]")
_TRAILING = re.compile(rf"(?i)\s*[|｜].*$|\s+[-–—]\s+(?:{_UPLOAD_WORDS})\b.*$"
                       r"|\s+(?:official|oficial|officiel(?:le)?)\s+(?:music\s+)?(?:video|vídeo|clip|mv)\b.*$")
_QUOTES = "\"'“”‘’«»「」"
# An upload's title that says it is the artist's video, and one that says it isn't.
_CLAIMS = re.compile(r"(?i)\b(?:official|oficial|officiel(?:le)?|ufficiale|offizielles?)\b|\bM/?V\b|videoclip")
_NOT_THE_SONG = re.compile(r"(?i)\b(?:cover|fan[ -]?made|lyrics?|letra|paroles|reaction|react|karaoke|tutorial|"
                           r"sped[ -]?up|slowed|nightcore|8d|teaser|trailer|behind the scenes|bts|making of|"
                           r"live|freestyle)\b")
_CHANNEL = re.compile(r"(?i)\s*(?:-\s*topic|vevo|official|oficial|music|tv)\s*$")


@dataclass
class Verdict:
    verdict: str
    kind: str | None = None
    title: str | None = None
    artist: str | None = None
    duration: int | None = None
    published: str | None = None
    song_id: str | None = None

    def row(self) -> dict:
        return asdict(self)


def _client() -> YTMusic:
    client = getattr(_local, "client", None)
    if client is None:
        client = _local.client = YTMusic(language="en")
    return client


def channel_artist(name: str) -> str:
    """ "DuaLipaVEVO" → "DuaLipa", "Stormy - Topic" → "Stormy"."""
    before, name = None, (name or "").strip()
    while before != name:
        before, name = name, _CHANNEL.sub("", name).strip()
    return name or before


def song_of(upload_title: str, channel: str, trusted: bool = False) -> tuple[str, str]:
    """(title, artist) an upload names: "Ellize - Mystika - Official Music
    Video" → ("Mystika", "Ellize"); without "Artist - ", the channel's. A
    trusted channel (the artist's) is the artist; its title loses "<artist> - "
    only when that names the artist."""
    text = _TRAILING.sub("", _BRACKET.sub("", upload_title or "")).strip()
    left, sep, right = text.partition(" - ")
    if not sep:
        left, sep, right = text.partition(" – ")
    left, right = left.strip(f" -–{_QUOTES}"), right.strip(f" -–{_QUOTES}")
    artist = channel_artist(channel)
    if trusted:
        if sep and right and titles.names(left) & titles.names(artist):
            return right, artist
        return text.strip(_QUOTES) or upload_title, artist
    if sep and right:
        return right, left
    return text.strip(_QUOTES), artist


def check(youtube_id: str) -> Verdict:
    """Asks YouTube Music; blocking (run it on a thread)."""
    client = _client()
    found = client.get_song(youtube_id)
    details = found.get("videoDetails") or {}
    playable = (found.get("playabilityStatus") or {}).get("status") == "OK"
    micro = (found.get("microformat") or {}).get("microformatDataRenderer") or {}
    seconds = str(details.get("lengthSeconds") or "")
    kind = KINDS.get(details.get("musicVideoType") or "", "upload")
    raw_title, channel = details.get("title") or "", details.get("author") or ""
    title, artist = song_of(raw_title, channel, trusted=kind != "upload")
    verdict = Verdict(
        verdict="official" if playable and kind != "upload" else "unofficial" if playable else "unavailable",
        kind=kind if details else None,
        title=title or None,
        artist=artist or None,
        duration=int(seconds) if seconds.isdigit() else None,
        published=(micro.get("publishDate") or micro.get("uploadDate") or "")[:10] or None,
    )
    if verdict.verdict == "unofficial" and title:
        verdict.song_id = _released_song(client, title, artist, channel_artist(channel))
        if verdict.song_id:
            verdict.verdict = "official"
        elif claims_official(raw_title):
            verdict.verdict = "likely"
    return verdict


def claims_official(upload_title: str) -> bool:
    return bool(_CLAIMS.search(upload_title or "")) and not _NOT_THE_SONG.search(upload_title or "")


def _released_song(client: YTMusic, title: str, artist: str, channel: str) -> str | None:
    """YouTube Music's song of that title by that artist, the same take. An
    upload's "A - B" is read both ways ("Mojito - KAMI H"), and the channel
    may be the artist."""
    pairs = list(dict.fromkeys([(title, artist), (artist, title), (title, channel)]))
    for want_title, want_artist in pairs:
        if not want_title or not want_artist:
            continue
        for result in client.search(f"{want_artist} {want_title}", filter="songs", limit=5):
            names = " & ".join(a.get("name", "") for a in result.get("artists") or [])
            if (result.get("videoId") and titles.agrees(want_title, want_artist, result.get("title") or "", names)
                    and titles.same_version(want_title, result.get("title") or "")):
                return result["videoId"]
    return None


async def check_async(youtube_id: str) -> Verdict:
    return await asyncio.to_thread(check, youtube_id)
