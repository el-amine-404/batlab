"""Credits sources (Settings, Credits): where clipseerr looks for who made a
song, in their order; the first with credits wins, and the panel says which
were asked. A new kind is one CreditsType subclass, listed in TYPES; its form
is drawn from its fields."""

import re

import httpx
from batlab_common import titles

from ..catalog import genius, musicbrainz, youtube
from ..integrations import IntegrationType
from ..integrations.base import Field

TIMEOUT = httpx.Timeout(10.0, connect=5.0)
# A song every source should know, for Test.
PROBE = {"artist": "Daft Punk", "title": "Get Lucky", "seconds": 248, "youtube_ids": ["5NV6Rdv1a3I"], "region": "US"}


class CreditsType(IntegrationType):
    async def find(self, http: httpx.AsyncClient, song: dict, config: dict) -> list[dict]:
        """[{"role", "names"}] for song ({"artist", "title", "seconds", "youtube_ids", "region"}); [] for none."""
        raise NotImplementedError

    async def test(self, http: httpx.AsyncClient, config: dict) -> str:
        found = await self.find(http, PROBE, config)
        if not found:
            return f"{self.label} answered, without credits for Daft Punk – Get Lucky."
        return f"{self.label} answered: {sum(len(c['names']) for c in found)} credits for Daft Punk – Get Lucky."


class YouTubeMusic(CreditsType):
    key = "youtube_music"
    label = "YouTube Music"
    description = "The credits its song pages show, from the labels. No account."
    homepage = "https://music.youtube.com"
    fields = ()

    async def find(self, http, song, config):
        for youtube_id in song.get("youtube_ids") or []:
            found = await youtube.credits(song.get("region") or "US", youtube_id)
            if found["credits"]:
                return found["credits"]
        return []


class Genius(CreditsType):
    key = "genius"
    label = "Genius"
    description = "Writers, producers, engineers: new songs within days. No account."
    homepage = "https://genius.com"
    fields = ()

    async def find(self, http, song, config):
        return (await genius.credits(http, song["artist"], song["title"]))["credits"]


class MusicBrainz(CreditsType):
    key = "musicbrainz"
    label = "MusicBrainz"
    description = "The open music database: performers, writers, producers, engineers. No account."
    homepage = "https://musicbrainz.org"
    fields = ()

    async def find(self, http, song, config):
        return (await musicbrainz.credits(http, song["artist"], song["title"], song.get("seconds")))["credits"]


# Discogs' credit roles, as the panel words them; any other stays as Discogs says it.
_DISCOGS_ROLES = (
    (re.compile(r"written|lyrics by|music by|songwriter|composed", re.I), "Written by"),
    (re.compile(r"^producer|produced", re.I), "Produced by"),
    (re.compile(r"mixed|^mix", re.I), "Mixed by"),
    (re.compile(r"master", re.I), "Mastered by"),
    (re.compile(r"engineer|recorded", re.I), "Engineered by"),
    (re.compile(r"featuring|vocals", re.I), "Performed by"),
)


def discogs_credits(release: dict, title: str) -> list[dict]:
    """A Discogs release's credits for one of its tracks: the track's own, and
    the release's that name that track's position or no track."""
    track = next((t for t in release.get("tracklist") or [] if titles.same_title(t.get("title") or "", title)), None)
    if not track:
        return []
    position = (track.get("position") or "").strip()
    people = list(track.get("extraartists") or [])
    for credit in release.get("extraartists") or []:
        tracks = [p.strip() for p in re.split(r"[,&]| to ", credit.get("tracks") or "") if p.strip()]
        if not tracks or position in tracks:
            people.append(credit)
    out: dict[str, list[str]] = {}
    for person in people:
        name = re.sub(r"\s*\(\d+\)$", "", person.get("anv") or person.get("name") or "").strip()
        for role in re.split(r",\s*", person.get("role") or ""):
            worded = next((w for pattern, w in _DISCOGS_ROLES if pattern.search(role)), role.strip())
            if name and worded and name not in out.setdefault(worded, []):
                out[worded].append(name)
    return [{"role": role, "names": names} for role, names in out.items() if names]


class Discogs(CreditsType):
    key = "discogs"
    label = "Discogs"
    description = "Each release's full credits, track by track: strong for albums, older music, vinyl. Needs a free token."
    homepage = "https://www.discogs.com/settings/developers"
    fields = (Field("token", "Personal access token", "secret",
                    help="discogs.com, Settings, Developers: \"Generate new token\"."),)
    API = "https://api.discogs.com"

    async def find(self, http, song, config):
        headers = {"Authorization": f"Discogs token={config.get('token') or ''}",
                   "User-Agent": "clipseerr/1 +https://github.com/el-amine-404/batlab"}
        response = await http.get(f"{self.API}/database/search", headers=headers, timeout=TIMEOUT,
                                  params={"artist": song["artist"], "track": song["title"], "type": "release", "per_page": 5})
        response.raise_for_status()
        for result in (response.json().get("results") or [])[:3]:
            release = await http.get(f"{self.API}/releases/{result['id']}", headers=headers, timeout=TIMEOUT)
            if release.status_code != 200:
                continue
            found = discogs_credits(release.json(), song["title"])
            if any(c["role"] != "Performed by" for c in found):
                return found
        return []


class Deezer(CreditsType):
    key = "deezer"
    label = "Deezer"
    description = "Who performs a song (main and featured artists), for when no other has credits. No account."
    homepage = "https://www.deezer.com"
    fields = ()

    async def find(self, http, song, config):
        found = await http.get("https://api.deezer.com/search", timeout=TIMEOUT,
                               params={"q": f'artist:"{song["artist"]}" track:"{song["title"]}"', "limit": 5})
        for track in (found.json() or {}).get("data") or []:
            if not titles.agrees(song["title"], song["artist"], track.get("title_short") or track.get("title") or "",
                                 (track.get("artist") or {}).get("name") or ""):
                continue
            detail = (await http.get(f"https://api.deezer.com/track/{track['id']}", timeout=TIMEOUT)).json() or {}
            main = [c["name"] for c in detail.get("contributors") or [] if c.get("role") != "Featured"]
            featured = [c["name"] for c in detail.get("contributors") or [] if c.get("role") == "Featured"]
            return [c for c in ({"role": "Performed by", "names": main}, {"role": "Featuring", "names": featured})
                    if c["names"]]
        return []


TYPES: dict[str, CreditsType] = {kind.key: kind for kind in (YouTubeMusic(), Genius(), MusicBrainz(), Discogs(), Deezer())}
# Made on the first start of 1.24: those that need nothing set up.
FIRST = ("youtube_music", "genius", "musicbrainz", "deezer")

__all__ = ["FIRST", "TYPES", "CreditsType", "discogs_credits"]
