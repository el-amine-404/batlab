"""Whether a video a feed carries is a real song, by YouTube Music: its kind
(the artist's own video, the song itself, or someone's upload), its title,
artist, length and the day it came out. The Hot category shows only real
songs; the feeds (subfeed, any JSON Feed) only say which videos a community
posted.

The verdict:
- official: the artist's video or the song (YouTube Music's OMV or ATV), or
  an upload of a song YouTube Music has under the same title and artist
  (song_id is that song);
- likely: an upload YouTube Music doesn't know as a song (a small artist,
  a song not on streaming yet) whose title says it is the official video,
  and not a cover, a lyric video, a reaction…;
- unofficial: it plays, but nothing says it is a released song;
- unavailable: removed, private, or blocked.

Verdicts are kept in the cache (cache.py), so asking is instant: a video not
judged yet, or judged long ago, is judged in the background, a few at a time
(YouTube answers one address only so fast), and on_judged() is told when a
round judged some, so that Hot shows them."""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass

from batlab_common import titles

from ..cache import Cache
from . import youtube
from .match import channel_artist

log = logging.getLogger("clipseerr.verdicts")

# Bumped when the rules below change: every video is judged again.
RULES = 2
# YouTube Music without a country: the same answer everywhere.
REGION = "ZZ"
KINDS = {"MUSIC_VIDEO_TYPE_OMV": "music_video", "MUSIC_VIDEO_TYPE_ATV": "song"}
REAL = ("official", "likely")
# Verdicts change: a song reaches YouTube Music, a video is taken down.
RECHECK_DAYS = {"official": 30, "other": 7}
# Videos judged at once, and per round (the rest wait for the next round).
AT_ONCE = 3
PER_ROUND = 300

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


@dataclass
class Verdict:
    verdict: str
    kind: str | None = None
    title: str | None = None
    artist: str | None = None
    duration: int | None = None
    published: str | None = None
    song_id: str | None = None
    checked_at: float = 0.0

    @property
    def real(self) -> bool:
        return self.verdict in REAL


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


def claims_official(upload_title: str) -> bool:
    return bool(_CLAIMS.search(upload_title or "")) and not _NOT_THE_SONG.search(upload_title or "")


async def judge(youtube_id: str) -> Verdict:
    """Asks YouTube Music; raises when it doesn't answer."""
    found = await youtube.song(REGION, youtube_id)
    known = bool(found["title"] or found["author"])
    kind = KINDS.get(found["kind"] or "", "upload")
    title, artist = song_of(found["title"], found["author"], trusted=kind != "upload")
    playable = found["playable"] and known
    verdict = Verdict(
        verdict="official" if playable and kind != "upload" else "unofficial" if playable else "unavailable",
        kind=kind if known else None,
        title=title or None,
        artist=artist or None,
        duration=found["seconds"],
        published=found["published"],
        checked_at=time.time(),
    )
    if verdict.verdict == "unofficial" and title:
        verdict.song_id = await _released_song(title, artist, channel_artist(found["author"]))
        if verdict.song_id:
            verdict.verdict = "official"
        elif claims_official(found["title"]):
            verdict.verdict = "likely"
    return verdict


async def _released_song(title: str, artist: str, channel: str) -> str | None:
    """YouTube Music's song of that title by that artist, the same take. An
    upload's "A - B" is read both ways ("Mojito - KAMI H"), and the channel
    may be the artist."""
    for want_title, want_artist in dict.fromkeys([(title, artist), (artist, title), (title, channel)]):
        if not want_title or not want_artist:
            continue
        for result in await youtube.search_raw(REGION, f"{want_artist} {want_title}", "songs", 5):
            names = " & ".join(a.get("name", "") for a in result.get("artists") or [])
            if (result.get("videoId") and titles.agrees(want_title, want_artist, result.get("title") or "", names)
                    and titles.same_version(want_title, result.get("title") or "")):
                return result["videoId"]
    return None


def _key(youtube_id: str) -> str:
    return f"verdict:{RULES}:{youtube_id}"


class Verdicts:
    def __init__(self, cache: Cache, on_judged: Callable[[], None] = lambda: None,
                 judge: Callable[[str], Awaitable[Verdict]] = judge):
        self._cache = cache
        self._on_judged = on_judged
        self._judge = judge
        # Waiting to be judged, first asked first.
        self._waiting: dict[str, None] = {}
        self._task: asyncio.Task | None = None

    def known(self, youtube_id: str) -> Verdict | None:
        """The verdict kept, at once (even an old one); a video without one,
        or with an old one, is judged in the background."""
        saved = self._cache.peek(_key(youtube_id))
        verdict = Verdict(**saved) if saved else None
        if verdict is None or self._stale(verdict):
            self._waiting.setdefault(youtube_id)
            self._start()
        return verdict

    @staticmethod
    def _stale(verdict: Verdict) -> bool:
        days = RECHECK_DAYS["official" if verdict.verdict == "official" else "other"]
        return time.time() - verdict.checked_at > days * 86400

    def keep(self, youtube_id: str, verdict: Verdict) -> None:
        self._cache.put(_key(youtube_id), asdict(verdict))

    def _start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run(), name="verdicts")

    async def _run(self) -> None:
        gate = asyncio.Semaphore(AT_ONCE)

        async def one(youtube_id: str) -> bool:
            async with gate:
                try:
                    self.keep(youtube_id, await self._judge(youtube_id))
                    return True
                except Exception as exc:
                    # YouTube didn't answer: asked again the next time Hot reads the feed.
                    log.info("judging %s failed: %r", youtube_id, exc)
                    return False

        while self._waiting:
            batch = list(self._waiting)[:PER_ROUND]
            for youtube_id in batch:
                del self._waiting[youtube_id]
            judged = sum(await asyncio.gather(*(one(i) for i in batch)))
            log.info("judged %d of %d videos", judged, len(batch))
            if judged:
                self._on_judged()

    async def drain(self) -> None:
        """For tests and shutdown: until nothing waits."""
        while self._task and not self._task.done():
            await asyncio.shield(self._task)
