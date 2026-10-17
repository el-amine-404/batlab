"""The catalog the page browses: shelves (the newest videos, the charts),
categories, search, artists and each video's details, from YouTube Music and
Apple (youtube.py, apple.py), cached (cache.py) so that nothing is waited on
twice."""

import asyncio
import logging
import re
import time
from collections.abc import Callable

import httpx

from ..cache import Cache
from ..problems import Problem
from . import match, youtube
from .apple import GENRES, Apple
from .models import (ArtistPage, ArtistRef, AudioMatch, Category, CategoryGroup, SearchResults, Shelf, Video,
                     VideoDetail)

log = logging.getLogger("clipseerr.catalog")

MIN = 60
HOUR = 3600
DAY = 86400
SHELF_PREVIEW = 24
# Apple's newest: released within this many days, from its top 200.
RECENT_DAYS = 45

_ID = re.compile(r"^(yt|am):([\w-]{1,64})$")
_CHART_KINDS = (
    # (shelf title, what YouTube's chart title starts with), in the page's order.
    ("Trending now", "Trending 20"),
    ("Daily top music videos", "Daily Top Music Videos"),
    ("Top 100 music videos", "Top 100 Music Videos"),
)


def _source_failed(name: str, exc: Exception) -> Problem:
    log.warning("%s failed: %r", name, exc)
    return Problem(502, f"{name} didn't answer. Try again in a moment.")


class Catalog:
    def __init__(self, cache: Cache, http: httpx.AsyncClient, settings: Callable[[], dict]):
        self._cache = cache
        self._apple = Apple(http)
        self._settings = settings
        # The last answer or failure of each source, for the status screen.
        self.health: dict[str, dict] = {}

    @property
    def region(self) -> str:
        return self._settings()["region"]

    @property
    def storefront(self) -> str:
        return self._settings()["storefront"]

    async def _get(self, source: str, key: str, fetch, fresh: float, stale: float | None = None):
        async def tracked():
            started = time.monotonic()
            try:
                value = await fetch()
            except Exception as exc:
                self.health[source] = {"ok": False, "at": time.time(), "error": str(exc)[:300]}
                raise
            self.health[source] = {"ok": True, "at": time.time(), "error": None,
                                   "ms": round((time.monotonic() - started) * 1000)}
            return value

        try:
            return await self._cache.get(key, tracked, fresh, stale)
        except Problem:
            raise
        except Exception as exc:
            raise _source_failed("YouTube Music" if source == "youtube" else "Apple Music", exc) from exc

    # ---- raw lists, cached --------------------------------------------------------------

    async def _new_videos(self) -> list[dict]:
        region = self.region
        return await self._get("youtube", f"yt:new:{region}",
                               lambda: _dump(youtube.new_videos(region)), fresh=30 * MIN)

    async def _charts(self) -> dict:
        region = self.region
        return await self._get("youtube", f"yt:charts:{region}", lambda: youtube.charts(region), fresh=12 * HOUR)

    async def _playlist(self, playlist_id: str) -> dict:
        region = self.region

        async def fetch():
            title, videos = await youtube.playlist(region, playlist_id, limit=100)
            return {"title": title, "items": [v.model_dump() for v in videos]}

        return await self._get("youtube", f"yt:playlist:{region}:{playlist_id}", fetch, fresh=2 * HOUR)

    async def _apple_top(self, genre: int | None = None) -> list[dict]:
        storefront = self.storefront
        return await self._get("apple", f"am:top:{storefront}:{genre or 0}",
                               lambda: _dump(self._apple.top(storefront, genre, limit=200 if not genre else 100)),
                               fresh=2 * HOUR)

    # ---- shelves ----------------------------------------------------------------------------

    async def _chart_shelves(self) -> list[tuple[str, str]]:
        """(shelf id, title) of the region's video charts, in _CHART_KINDS' order."""
        try:
            charts = await self._charts()
        except Problem:
            return []
        found = []
        for title, prefix in _CHART_KINDS:
            chart = next((c for c in charts["videos"] if c["title"].startswith(prefix)), None)
            if chart:
                where = chart["title"].removeprefix(prefix).strip(" -–")
                found.append((f"yt-chart:{chart['playlistId']}", f"{title} · {where}" if where else title))
        return found

    async def shelf(self, shelf_id: str, limit: int | None = None) -> Shelf:
        if shelf_id == "yt-new":
            items = await self._new_videos()
            shelf = Shelf(id=shelf_id, title="New music videos", subtitle="Just out on YouTube", source="youtube",
                          items=items, total=len(items))
        elif shelf_id.startswith("yt-chart:"):
            playlist_id = shelf_id.split(":", 1)[1]
            if not re.fullmatch(r"[\w-]{10,64}", playlist_id):
                raise Problem(404, "No such shelf.")
            found = await self._playlist(playlist_id)
            title = next((t for i, t in await self._chart_shelves() if i == shelf_id), found["title"])
            shelf = Shelf(id=shelf_id, title=title, subtitle="YouTube charts", source="youtube",
                          items=found["items"], total=len(found["items"]))
        elif shelf_id == "am-new":
            cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - RECENT_DAYS * DAY))
            items = sorted((i for i in await self._apple_top() if (i.get("released") or "") >= cutoff),
                           key=lambda i: i["released"], reverse=True)
            shelf = Shelf(id=shelf_id, title="Just released", subtitle="Charting on Apple Music, newest first",
                          source="apple", items=items, total=len(items))
        elif shelf_id == "am-top":
            items = await self._apple_top()
            shelf = Shelf(id=shelf_id, title="Top on Apple Music", subtitle=f"Music videos · {self.storefront.upper()}",
                          source="apple", items=items, total=len(items))
        else:
            raise Problem(404, "No such shelf.")
        if limit:
            shelf.items = shelf.items[:limit]
        return shelf

    async def home(self) -> list[Shelf]:
        """Every shelf, with its first items; a source that fails leaves its
        shelves out rather than the page empty."""
        ids = ["yt-new", *(i for i, _ in await self._chart_shelves()), "am-new", "am-top"]
        results = await asyncio.gather(*(self.shelf(i, SHELF_PREVIEW) for i in ids), return_exceptions=True)
        shelves = [r for r in results if isinstance(r, Shelf) and r.items]
        if not shelves and results and all(isinstance(r, Exception) for r in results):
            raise results[0]
        return shelves

    # ---- categories ---------------------------------------------------------------------------

    async def categories(self) -> list[CategoryGroup]:
        groups = []
        try:
            charts = await self._charts()
        except Problem:
            charts = {"genres": []}
        if charts["genres"]:
            items = [Category(id=f"yt:{g['playlistId']}", title=_genre_name(g["title"]), group="YouTube charts",
                              source="youtube", image=g.get("image")) for g in charts["genres"]]
            groups.append(CategoryGroup(title="YouTube charts", source="youtube",
                                        categories=sorted(items, key=lambda c: c.title)))
        groups.append(CategoryGroup(title="Apple Music", source="apple", categories=Apple.categories()))
        return groups

    async def category(self, category_id: str) -> Shelf:
        kind, _, value = category_id.partition(":")
        if kind == "am" and value.isdigit() and int(value) in GENRES:
            items = await self._apple_top(int(value))
            return Shelf(id=category_id, title=GENRES[int(value)], subtitle=f"Top music videos on Apple Music · "
                         f"{self.storefront.upper()}", source="apple", items=items, total=len(items))
        if kind == "yt" and re.fullmatch(r"[\w-]{10,64}", value):
            found = await self._playlist(value)
            return Shelf(id=category_id, title=_genre_name(found["title"]), subtitle=found["title"], source="youtube",
                         items=found["items"], total=len(found["items"]))
        raise Problem(404, "No such category.")

    # ---- search and artists --------------------------------------------------------------------

    async def search(self, query: str) -> SearchResults:
        query = " ".join(query.split())[:120]
        if not query:
            return SearchResults(query="", artists=[], videos=[])
        region = self.region
        key = query.casefold()

        async def fetch():
            videos, artists = await asyncio.gather(youtube.search_videos(region, query),
                                                   youtube.search_artists(region, query))
            # A search for an artist's name: their own videos first (YouTube's
            # video search favours fan uploads and live clips).
            named = next((a for a in artists[:2] if match.titles.simple(a.name) == match.titles.simple(query)), None)
            if named:
                try:
                    own = (await self.artist(named.id)).videos[:16]
                except Problem:
                    own = []
                ids = {v.id for v in own}
                videos = [*own, *(v for v in videos if v.id not in ids)]
            return {"query": query, "videos": [v.model_dump() for v in videos],
                    "artists": [a.model_dump() for a in artists]}

        return SearchResults.model_validate(await self._get("youtube", f"yt:search:{region}:{key}", fetch,
                                                            fresh=30 * MIN, stale=DAY))

    async def artist(self, channel_id: str) -> ArtistPage:
        if not re.fullmatch(r"UC[\w-]{20,30}", channel_id):
            raise Problem(404, "No such artist.")
        region = self.region

        async def fetch():
            return (await youtube.artist(region, channel_id)).model_dump()

        return ArtistPage.model_validate(await self._get("youtube", f"yt:artist:{region}:{channel_id}", fetch,
                                                         fresh=6 * HOUR))

    async def artist_by_name(self, name: str) -> dict | None:
        """{"id", "name"}: the YouTube Music artist named so, for Apple's
        credits and VEVO channels ("OliviaRodrigoVEVO")."""
        region = self.region

        async def fetch():
            found = await youtube.search_artists(region, name, limit=3)
            wanted = match.titles.simple(name)
            for artist in found:
                if match.titles.simple(artist.name) == wanted:
                    return {"id": artist.id, "name": artist.name}
            return None

        return await self._get("youtube", f"yt:artist-name:{region}:{name.casefold()}", fetch, fresh=30 * DAY)

    async def _resolve_refs(self, names: list[str]) -> list[ArtistRef]:
        found = await asyncio.gather(*(self.artist_by_name(name) for name in names), return_exceptions=True)
        return [ArtistRef(name=hit["name"], id=hit["id"]) if isinstance(hit, dict) else ArtistRef(name=name)
                for name, hit in zip(names, found)]

    async def _artists_of(self, video: Video, listed: Video | None) -> list[ArtistRef]:
        """The artists with their YouTube Music pages: Apple's credit split in
        names, or YouTube's own list with channels that aren't an artist's
        page (VEVO) looked up by name."""
        if video.source == "apple":
            return await self._resolve_refs(_credit_names(video.artist))
        refs = (listed or video).artists
        odd = [ref for ref in refs if not ref.id or match.channel_artist(ref.name) != ref.name]
        if not odd:
            return refs
        fixed = await self._resolve_refs([match.channel_artist(ref.name) for ref in odd])
        swap = {id(ref): new for ref, new in zip(odd, fixed)}
        return [swap.get(id(ref), ref) for ref in refs]

    # ---- one video ---------------------------------------------------------------------------------

    async def video(self, video_id: str) -> VideoDetail:
        found = _ID.match(video_id or "")
        if not found:
            raise Problem(404, "No such video.")
        kind, value = found.groups()
        fetch = (lambda: self._youtube_detail(value)) if kind == "yt" else (lambda: self._apple_detail(value))
        return VideoDetail.model_validate(await self._get("youtube" if kind == "yt" else "apple",
                                                          f"video:{self.region}:{video_id}", fetch, fresh=3 * DAY,
                                                          stale=30 * DAY))

    async def _youtube_detail(self, youtube_id: str, base: Video | None = None) -> dict:
        region = self.region
        song, watch = await asyncio.gather(youtube.song(region, youtube_id), _watch_track(region, youtube_id))
        if not song["playable"] and not watch:
            raise Problem(404, "YouTube doesn't have this video (any more).")
        listed = youtube.to_video(watch) if watch else None
        video = base or listed or Video(id=f"yt:{youtube_id}", source="youtube", youtube_id=youtube_id,
                                        title=match.clean(song["title"], song["author"])[0],
                                        artist=match.channel_artist(song["author"]),
                                        artists=[ArtistRef(name=match.channel_artist(song["author"]))])
        video.artists = await self._artists_of(video, listed)
        audio = await self._audio_for(video.title, video.artist, song["seconds"])
        detail = VideoDetail(
            **video.model_dump(exclude={"youtube_id", "thumbnail", "duration", "views", "official"}),
            youtube_id=youtube_id,
            thumbnail=youtube.thumbnail(youtube_id),
            duration=song["seconds"] or video.duration,
            views=video.views or youtube.short_count(song["views"]),
            official=song["kind"] in (None, youtube.OFFICIAL),
            embeddable=song["embeddable"],
            audio=audio,
            description=song["description"],
            links={"youtube": f"https://www.youtube.com/watch?v={youtube_id}",
                   "youtube_music": f"https://music.youtube.com/watch?v={youtube_id}"},
        )
        if not detail.released and song["published"]:
            detail.released = song["published"]
        if video.source == "apple":
            detail.links["apple"] = f"https://music.apple.com/{self.storefront}/music-video/{video.id[3:]}"
        return detail.model_dump()

    async def _apple_detail(self, track_id: str) -> dict:
        storefront = self.storefront
        apple = await self._apple.lookup(storefront, track_id)
        if not apple:
            raise Problem(404, "Apple Music doesn't have this video (any more).")
        results = await youtube.search_raw(self.region, f"{apple.artist} {apple.title}", "videos", limit=10)
        picked = match.best_video(apple.title, apple.artist, apple.duration, results)
        if not picked:
            # Watchable as Apple's preview only; still requestable as audio, by name.
            detail = VideoDetail(**apple.model_dump(), embeddable=False,
                                 audio=await self._audio_for(apple.title, apple.artist, apple.duration),
                                 links={"apple": f"https://music.apple.com/{storefront}/music-video/{track_id}"})
            detail.artists = await self._artists_of(apple, None)
            return detail.model_dump()
        return await self._youtube_detail(picked["videoId"], base=apple)

    async def _audio_for(self, title: str, artist: str, seconds: int | None) -> AudioMatch | None:
        try:
            results = await youtube.search_raw(self.region, f"{artist} {title}", "songs", limit=10)
        except Exception as exc:
            log.warning("no audio match for %s - %s: %r", artist, title, exc)
            return None
        picked = match.best_audio(title, artist, seconds, results)
        if not picked:
            return None
        album = picked.get("album") or {}
        return AudioMatch(
            youtube_id=picked["videoId"],
            title=picked.get("title") or title,
            artist=" & ".join(a["name"] for a in picked.get("artists") or [] if a.get("name")) or artist,
            album=album.get("name") if isinstance(album, dict) else None,
            year=picked.get("year"),
            duration=picked.get("duration_seconds"),
            thumbnail=youtube._image(picked.get("thumbnails"), 544),
        )

    # ---- keeping the front page warm --------------------------------------------------------------

    async def warm(self) -> None:
        try:
            await self.home()
            await self.categories()
        except Exception as exc:
            log.warning("warming the catalog failed: %r", exc)


async def _dump(videos_coroutine) -> list[dict]:
    return [v.model_dump() for v in await videos_coroutine]


async def _watch_track(region: str, youtube_id: str) -> dict | None:
    """The video as YouTube Music lists it, with its artists' channels."""
    try:
        watch = await youtube._call(region, "get_watch_playlist", videoId=youtube_id, limit=1)
    except Exception as exc:
        log.info("no watch entry for %s: %r", youtube_id, exc)
        return None
    tracks = watch.get("tracks") or []
    track = next((t for t in tracks if t.get("videoId") == youtube_id), None)
    if track and "duration_seconds" not in track:
        track["duration"] = track.get("length")
    return track


def _credit_names(credit: str) -> list[str]:
    """ "Olivia Rodrigo & Chappell Roan" → ["Olivia Rodrigo", "Chappell Roan"]."""
    return [part.strip() for part in match.titles.FEAT.split(credit or "") if part.strip()][:4]


def _genre_name(title: str) -> str:
    """ "Top 50 Hip Hop Music Videos United States" → "Hip Hop"."""
    name = re.sub(r"(?i)^top\s+\d+\s+", "", title or "")
    name = re.split(r"(?i)\s+music videos\b", name)[0]
    return name.strip() or title
