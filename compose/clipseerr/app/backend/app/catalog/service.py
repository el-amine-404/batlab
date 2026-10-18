"""The catalog the page browses: shelves (the newest videos, the charts),
categories, search, artists and each video's details, from YouTube Music and
Apple (youtube.py, apple.py), cached (cache.py) so that nothing is waited on
twice."""

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable

import httpx
from batlab_common import titles

from ..cache import Cache, tracking
from ..problems import Problem
from . import language, lrclib, match, youtube
from .apple import GENRES, Apple
from .models import (AlbumPage, ArtistPage, ArtistRef, AudioMatch, Category, CategoryGroup, Credits, Lyrics, SearchResults, Shelf,
                     Version, Versions, Video, VideoDetail)

log = logging.getLogger("clipseerr.catalog")

MIN = 60
HOUR = 3600
DAY = 86400
SHELF_PREVIEW = 24
# Apple's newest: released within this many days, from its top 200.
RECENT_DAYS = 45
# Latest releases: out within this many days, or coming out.
RELEASE_DAYS = 30
# Calls to one source at once, when a list needs many.
FAN_OUT = 6
# Seconds the genres wait for a list's pictures before answering without them.
COVER_WAIT = 1.5
# Videos a search asks YouTube Music for (it answers 20 a call), shown a page at a time.
SEARCH_VIDEOS = 100

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
        self._http = http
        self._apple = Apple(http)
        self._settings = settings
        self._gate = asyncio.Semaphore(FAN_OUT)
        # Hot's picks and artists (hotlist.py), when there is one.
        self.hotlist = None
        # Every video shown, for the quick search (known.py), when there is one.
        self.known = None
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

        async def fetch():
            async with self._gate:
                return await _dump(self._apple.top(storefront, genre, limit=200 if not genre else 100))

        return await self._get("apple", f"am:top:{storefront}:{genre or 0}", fetch, fresh=2 * HOUR)

    async def _published(self, youtube_id: str) -> str | None:
        """The day YouTube published a video; it never changes."""
        region = self.region

        async def fetch():
            async with self._gate:
                return (await youtube.song(region, youtube_id))["published"]

        return await self._get("youtube", f"yt:published:{youtube_id}", fetch, fresh=365 * DAY, stale=365 * DAY)

    async def _dated_new_videos(self) -> list[dict]:
        """YouTube's new videos with the day each came out."""
        items = await self._new_videos()
        days = await asyncio.gather(*(self._published(i["youtube_id"]) for i in items if not i.get("released")),
                                    return_exceptions=True)
        missing = iter(days)
        dated = []
        for item in items:
            released = item.get("released") or next(missing)
            dated.append({**item, "released": released if isinstance(released, str) else None})
        return dated

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
        elif shelf_id == "releases":
            shelf = await self.releases()
        elif shelf_id == "am-top":
            items = await self._apple_top()
            shelf = Shelf(id=shelf_id, title="Top on Apple Music", subtitle=f"Music videos · {self.storefront.upper()}",
                          source="apple", items=items, total=len(items))
        else:
            raise Problem(404, "No such shelf.")
        if limit:
            shelf.items = shelf.items[:limit]
        return shelf

    async def releases(self) -> Shelf:
        """What came out in the last RELEASE_DAYS days, and what is about to,
        newest first, from everything clipseerr reads: YouTube Music's new
        videos, Hot (its feeds and searches) and every Apple chart; one entry
        a song (YouTube's, which plays, before Apple's). No free source lists
        every release: a video none of them carries is missing."""
        region, storefront = self.region, self.storefront

        async def fetch():
            cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - RELEASE_DAYS * DAY))
            async def hot_items():
                return [v.model_dump() for v in (await self.hot()).items]

            found = await asyncio.gather(self._dated_new_videos(), hot_items(), self._apple_top(),
                                         *(self._apple_top(genre) for genre in GENRES), return_exceptions=True)
            lists = [r for r in found if isinstance(r, list)]
            if not lists:
                raise next(r for r in found if isinstance(r, Exception))
            seen: dict[tuple, dict] = {}
            for item in (i for found_list in lists for i in found_list):
                key = _song_key(item["title"], item["artist"])
                if key in seen:
                    if not seen[key].get("released"):
                        seen[key]["released"] = item.get("released")
                    continue
                seen[key] = dict(item)
            items = sorted((i for i in seen.values() if (i.get("released") or "") >= cutoff),
                           key=lambda i: i["released"], reverse=True)
            return Shelf(id="releases", title="Latest releases",
                         subtitle="Music videos out in the last 30 days, and the ones coming soon, newest first.",
                         source="apple", items=items, total=len(items)).model_dump()

        return Shelf.model_validate(await self._cache.get(f"releases:{region}:{storefront}", fetch,
                                                          fresh=30 * MIN, stale=DAY))

    async def hot(self) -> Shelf:
        """The Hot category, newest first: the videos of the feeds the
        settings name (clipfeed: what r/SexyMusicVideos and the like post,
        checked to be real songs) and the official videos of its YouTube
        searches, each dated by YouTube."""
        region, settings = self.region, self._settings()
        queries, feeds = settings["hot_queries"], settings.get("hot_feeds") or []
        picks = self.hotlist.picks() if self.hotlist else []
        key = f"hot:{region}:" + hashlib.sha1(json.dumps([queries, feeds, [p["id"] for p in picks]]).encode()
                                             ).hexdigest()[:12]

        async def search(query: str):
            async def fetch():
                async with self._gate:
                    return await _dump(youtube.search_videos(region, query, limit=40))

            return await self._get("youtube", f"yt:hot:{region}:{query.casefold()}", fetch, fresh=6 * HOUR,
                                   stale=7 * DAY)

        async def fetch():
            found = await asyncio.gather(*(self._feed(url) for url in feeds), *(search(q) for q in queries),
                                         return_exceptions=True)
            for url, failed in zip(feeds, found):
                if isinstance(failed, Exception):
                    log.warning("hot feed %s failed: %r", url, failed)
            lists = [r for r in found if isinstance(r, list)]
            if not lists and found:
                raise next(r for r in found if isinstance(r, Exception))
            # Picks first, then feeds: when two have a video, the first one's entry stays.
            lists.insert(0, [{k: v for k, v in p.items() if k != "picked_at"} for p in picks])
            seen, items = set(), []
            for item in (i for found_list in lists for i in found_list):
                if item["official"] and item["id"] not in seen:
                    seen.add(item["id"])
                    items.append(item)
            await self._date(items)
            items.sort(key=lambda i: i.get("released") or "", reverse=True)
            if self.hotlist:
                self.hotlist.saw(items)
            return Shelf(id="hot", title="Hot", subtitle="Sexy, twerk and uncensored music videos, newest first.",
                         source="youtube", items=items, total=len(items)).model_dump()

        return Shelf.model_validate(await self._cache.get(key, fetch, fresh=30 * MIN, stale=DAY))

    async def _feed(self, url: str) -> list[dict]:
        """A feed's videos: a clipfeed items URL (or any app answering
        {"items": [{"title", "video": {"youtube_id", "verdict", "title",
        "artist", "published", "duration"}}]}); the real songs only (official
        or likely), whatever else the feed holds."""
        response = await self._http.get(url, timeout=httpx.Timeout(15.0, connect=5.0))
        response.raise_for_status()
        videos = []
        for item in (response.json() or {}).get("items") or []:
            video = item.get("video") or {}
            youtube_id = video.get("youtube_id")
            if not youtube_id or not re.fullmatch(r"[\w-]{11}", youtube_id):
                continue
            if video.get("verdict") not in ("official", "likely"):
                continue
            artist = video.get("artist") or "Unknown artist"
            title, credit = match.clean(video.get("title") or item.get("title") or "", artist)
            videos.append(Video(id=f"yt:{youtube_id}", source="youtube", youtube_id=youtube_id, title=title,
                                artist=credit or artist, artists=[ArtistRef(name=n) for n in _credit_names(credit or artist)],
                                thumbnail=youtube.thumbnail(youtube_id), duration=video.get("duration"),
                                released=video.get("published"), official=True).model_dump())
        return videos

    async def _date(self, items: list[dict]) -> None:
        """Gives the undated ones the day YouTube published them."""
        undated = [i for i in items if not i.get("released") and i.get("youtube_id")]
        days = await asyncio.gather(*(self._published(i["youtube_id"]) for i in undated), return_exceptions=True)
        for item, published in zip(undated, days):
            if isinstance(published, str):
                item["released"] = published

    async def home(self) -> list[Shelf]:
        """Every shelf, with its first items; a source that fails leaves its
        shelves out rather than the page empty."""
        # Not "am-new": Apple's newest are on the page already, in the latest releases beside the shelves.
        ids = ["yt-new", *(i for i, _ in await self._chart_shelves()), "am-top"]
        async def one(shelf_id: str) -> Shelf:
            # Each in its own task, so each its own age.
            with tracking() as reads:
                found = await self.shelf(shelf_id, SHELF_PREVIEW)
            found.updated_at = min(reads, default=None)
            return found

        results = await asyncio.gather(*(one(i) for i in ids), return_exceptions=True)
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
        apple = Apple.categories()
        hot = Category(id="hot", title="Hot", group="Picks", source="youtube")
        # Apple's genres and Hot have no cover: their top videos' pictures stand
        # in. The page doesn't wait for a list not fetched yet: it gets its
        # pictures the next time, the fetch going on meanwhile.
        tasks = [asyncio.ensure_future(t) for t in (*(self._apple_top(int(c.id[3:])) for c in apple), self.hot())]
        for task in tasks:
            # One that finishes after the answer, failed, is logged by the source's health, not as "never retrieved".
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
        await asyncio.wait(tasks, timeout=COVER_WAIT)
        for category, task in zip([*apple, hot], tasks):
            if not task.done() or task.cancelled() or task.exception():
                continue
            items = task.result()
            if isinstance(items, Shelf):
                items = [v.model_dump() for v in items.items]
            category.images = [_small(i["thumbnail"]) for i in items if i.get("thumbnail")][:3]
        groups.append(CategoryGroup(title="Apple Music", source="apple", categories=apple))
        picks = CategoryGroup(title="Picks", source="youtube", categories=[hot])
        return [picks, *groups]

    async def category(self, category_id: str) -> Shelf:
        if category_id == "hot":
            return await self.hot()
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
            videos, artists, official = await asyncio.gather(
                youtube.search_videos(region, query, limit=SEARCH_VIDEOS), youtube.search_artists(region, query),
                # YouTube Music ranks a word like "twerk" by its own measure and
                # buries official videos past its first hundred: asked again
                # for official videos, their official ones come after.
                youtube.search_videos(region, f"{query} official video", limit=SEARCH_VIDEOS),
                return_exceptions=True)
            if isinstance(videos, Exception):
                raise videos
            artists = [] if isinstance(artists, Exception) else artists
            seen = {v.id for v in videos}
            if not isinstance(official, Exception):
                videos += [v for v in official if v.official and v.id not in seen]
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

        found = SearchResults.model_validate(await self._get("youtube", f"yt:search4:{region}:{key}", fetch,
                                                             fresh=30 * MIN, stale=DAY))
        found.total = len(found.videos)
        return found

    async def artist(self, channel_id: str) -> ArtistPage:
        if not re.fullmatch(r"UC[\w-]{20,30}", channel_id):
            raise Problem(404, "No such artist.")
        region, storefront = self.region, self.storefront

        async def fetch():
            try:
                page = await youtube.artist(region, channel_id)
            except LookupError as exc:
                raise Problem(404, "This channel has no page on YouTube Music.") from exc
            try:
                apple = await self._apple.artist_videos(storefront, page.name)
            except Exception as exc:
                log.info("no Apple videos for %s: %r", page.name, exc)
                apple = []
            page.videos = _with_apple(page.videos, apple)
            return page.model_dump()

        return ArtistPage.model_validate(await self._get("youtube", f"yt:artist:{region}:{channel_id}", fetch,
                                                         fresh=6 * HOUR))

    async def album(self, album_id: str) -> AlbumPage:
        if not re.fullmatch(r"MPRE[\w-]{6,60}", album_id):
            raise Problem(404, "No such album.")
        region = self.region

        async def fetch():
            return (await youtube.album(region, album_id)).model_dump()

        return AlbumPage.model_validate(await self._get("youtube", f"yt:album:{region}:{album_id}", fetch,
                                                        fresh=7 * DAY, stale=90 * DAY))

    def artist_empty(self, channel_id: str) -> bool:
        """Its page was read, and holds no video and no album (an empty Topic channel): a dead end to suggest."""
        page = self._cache.peek(f"yt:artist:{self.region}:{channel_id}")
        return bool(page) and not page.get("videos") and not page.get("albums")

    async def artist_by_name(self, name: str) -> dict | None:
        """{"id", "name"}: the YouTube Music artist named so, for Apple's
        credits and VEVO channels ("OliviaRodrigoVEVO")."""
        region = self.region

        async def fetch():
            found = await youtube.search_artists(region, name, limit=3)
            wanted = match.titles.simple(name)
            for artist in found:
                if match.titles.simple(artist.name) == wanted:
                    return {"id": artist.id, "name": artist.name, "thumbnail": artist.thumbnail}
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
                                                          # video4: the uploader named as an artist, and album tracks, are official.
                                                          f"video4:{self.region}:{video_id}", fetch,
                                                          # Apple's may be out on YouTube soon.
                                                          fresh=3 * DAY if kind == "yt" else 6 * HOUR,
                                                          stale=30 * DAY))

    async def versions(self, video_id: str) -> Versions:
        """The other takes of a video's song: its clean or explicit cut, the
        official video of a lyric or a performance one, remixes, live ones;
        as videos and as songs."""
        detail = await self.video(video_id)
        region = self.region

        async def fetch():
            base = _bare(detail.title)
            query = f"{_credit_names(detail.artist)[0] if detail.artist else ''} {base}".strip()
            videos, songs = await asyncio.gather(youtube.search_raw(region, query, "videos", limit=20),
                                                 youtube.search_raw(region, query, "songs", limit=20))
            return Versions(videos=_versions(videos, "video", base, detail), songs=_versions(songs, "song", base, detail)
                            ).model_dump()

        return Versions.model_validate(await self._get("youtube", f"yt:versions:{region}:{video_id}", fetch,
                                                       fresh=3 * DAY, stale=30 * DAY))

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
        # YouTube Music lists some videos under one of their artists only: the
        # upload's title ("Jayelle & bludnymph - …") or the song may name them all.
        full = _fuller_credit(video.artist, match.clean(song["title"], song["author"])[1],
                              audio.artist if audio else None)
        if full != video.artist:
            known = {titles.simple(ref.name): ref for ref in video.artists if ref.id}
            names = _credit_names(full)
            missing = [n for n in names if titles.simple(n) not in known]
            new = dict(zip(map(titles.simple, missing), await self._resolve_refs(missing)))
            video.artist = " & ".join(names)
            video.artists = [known.get(titles.simple(n)) or new.get(titles.simple(n)) or ArtistRef(name=n) for n in names]
        detail = VideoDetail(
            **video.model_dump(exclude={"youtube_id", "thumbnail", "duration", "views", "official"}),
            youtube_id=youtube_id,
            thumbnail=youtube.thumbnail(youtube_id),
            duration=song["seconds"] or video.duration,
            views=video.views or youtube.short_count(song["views"]),
            official=_official(song, video.artists),
            uploader=song["author"] or None,
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
            album_id=album.get("id") if isinstance(album, dict) else None,
            year=picked.get("year"),
            duration=picked.get("duration_seconds"),
            thumbnail=youtube._image(picked.get("thumbnails"), 544),
        )

    # ---- how many watched ---------------------------------------------------------------------

    async def views(self, youtube_ids: list[str]) -> dict[str, dict[str, str]]:
        """Each video's views ("1.2M"), as YouTube counts them today, and the
        day it came out: most lists (charts, Apple's, Hot, searches) carry
        neither. {"views": {id: …}, "released": {id: …}}; what YouTube didn't
        answer for is left out."""
        region = self.region

        async def one(youtube_id: str):
            async def fetch():
                async with self._gate:
                    song = await youtube.song(region, youtube_id)
                # The same answer has the day it came out, which never changes.
                if song["published"]:
                    self._cache.put(f"yt:published:{youtube_id}", song["published"])
                return youtube.short_count(song["views"])

            views = await self._get("youtube", f"yt:views:{youtube_id}", fetch, fresh=DAY, stale=30 * DAY)
            return views, await self._published(youtube_id)

        found = await asyncio.gather(*(one(i) for i in youtube_ids), return_exceptions=True)
        answer = {"views": {}, "released": {}}
        for youtube_id, facts in zip(youtube_ids, found):
            if isinstance(facts, tuple):
                if facts[0]:
                    answer["views"][youtube_id] = facts[0]
                if facts[1]:
                    answer["released"][youtube_id] = facts[1]
        return answer

    async def fill_released(self, videos: list[Video]) -> None:
        """The day each came out, where its list didn't say (YouTube's, cached a year): for sorting by newest."""
        missing = [v for v in videos if not v.released and v.youtube_id]
        days = await asyncio.gather(*(self._published(v.youtube_id) for v in missing), return_exceptions=True)
        for video, day in zip(missing, days):
            if isinstance(day, str):
                video.released = day

    def known_views(self, videos: list[Video]) -> None:
        """Fills in the views and days already looked up, without asking YouTube."""
        for video in videos:
            if video.youtube_id and not video.views:
                video.views = self._cache.peek(f"yt:views:{video.youtube_id}")
            if video.youtube_id and not video.released:
                video.released = self._cache.peek(f"yt:published:{video.youtube_id}")

    # ---- who made the song, and its words ---------------------------------------------------------

    async def credits(self, video_id: str) -> Credits:
        """The song's credits (its official audio's, else the video's)."""
        detail = await self.video(video_id)
        region = self.region
        ids = [i for i in ((detail.audio.youtube_id if detail.audio else None), detail.youtube_id) if i]

        async def fetch():
            for youtube_id in ids:
                found = await youtube.credits(region, youtube_id)
                if found["credits"]:
                    return found
            return {"credits": [], "provider": None}

        if not ids:
            return Credits(credits=[])
        found = Credits.model_validate(await self._get("youtube", f"yt:credits:{ids[0]}", fetch, fresh=30 * DAY,
                                                       stale=90 * DAY))
        # Each name to its YouTube Music page when it has one (a writer or a
        # producer often hasn't): the page links them.
        names = list(dict.fromkeys(n for credit in found.credits for n in credit.names))
        refs = dict(zip(names, await self._resolve_refs(names)))
        for credit in found.credits:
            credit.people = [refs.get(n) or ArtistRef(name=n) for n in credit.names]
        return found

    async def lyrics(self, video_id: str) -> Lyrics:
        """The song's words: LRCLIB's (timed when it has them), else YouTube
        Music's; none when neither has them."""
        detail = await self.video(video_id)
        region = self.region
        song = detail.audio
        title, artist = (song.title, song.artist) if song else (detail.title, detail.artist)
        seconds = (song.duration if song else None) or detail.duration
        first = (_credit_names(artist) or [artist])[0]

        async def fetch():
            found = None
            try:
                found = await lrclib.find(self._http, _bare(title), first, seconds)
            except Exception as exc:
                log.info("LRCLIB failed for %s - %s: %r", first, title, exc)
            if not found or not (found["synced"] or found["plain"]):
                youtube_id = song.youtube_id if song else detail.youtube_id
                try:
                    from_youtube = await youtube.lyrics(region, youtube_id) if youtube_id else None
                except Exception as exc:
                    log.info("YouTube Music lyrics failed for %s: %r", youtube_id, exc)
                    from_youtube = None
                found = from_youtube or found
            words = Lyrics.model_validate(found or {})
            text = words.plain or "\n".join(line.text for line in words.synced)
            words.language = language.detect(text)
            words.language_name = language.name(words.language)
            return words.model_dump()

        # lyrics2: with the language they are in.
        key = f"lyrics2:{titles.simple(first)}:{titles.simple(title)}:{seconds or 0}"
        found = Lyrics.model_validate(await self._cache.get(key, fetch, fresh=7 * DAY, stale=90 * DAY))
        if found.language and self.known:
            self.known.set_language(video_id, found.language)
        return found

    # ---- keeping the front page warm --------------------------------------------------------------

    async def _resolve_hot_artists(self, limit: int = 150) -> None:
        """Finds the YouTube Music page and picture of Hot's artists, some each
        round (about an hour for the first 600)."""
        if not self.hotlist:
            return

        async def look(name: str):
            async with self._gate:
                return await self.artist_by_name(name)

        names = self.hotlist.unresolved(limit)
        found = await asyncio.gather(*(look(name) for name in names), return_exceptions=True)
        for name, hit in zip(names, found):
            if isinstance(hit, Exception):
                continue
            self.hotlist.resolved(name, (hit or {}).get("id"), (hit or {}).get("thumbnail"))

    async def warm(self) -> None:
        try:
            shelves = [*await self.home()]
            await self.categories()
            shelves += [await self.hot(), await self.releases()]
            if self.known:
                for shelf in shelves:
                    self.known.saw(shelf.items, list_id=shelf.id)
            await self._resolve_hot_artists()
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


def _small(image: str) -> str:
    """Apple's picture at 320×180, a quarter of the 640×360 its lists give: for a tile."""
    return re.sub(r"/\d+x\d+bb\.jpg$", "/320x180bb.jpg", image)


def _official(song: dict, artists: list[ArtistRef]) -> bool:
    """The artist's own video: YouTube's official music video type (a label's
    upload), or uploaded by one of its artists' own channels, which YouTube
    types as a user upload (aggelina's "KALSON SKISMENA" on her channel)."""
    if song["kind"] in (None, *youtube.OFFICIAL_KINDS):
        return True
    channel = song.get("channel_id")
    if channel and any(ref.id == channel for ref in artists):
        return True
    # An artist may have two channels (aggelina's artist page and the channel
    # she uploads on): the uploader named as one of the artists, or as their
    # "VEVO" or "- Topic" channel, is still the artist.
    uploader = titles.simple(match.channel_artist(song.get("author") or ""))
    return bool(uploader) and any(titles.simple(ref.name) == uploader for ref in artists)


def _fuller_credit(current: str, *others: str | None) -> str:
    """The credit naming the most artists, among the current one and others
    that share an artist with it (another credit is another song)."""
    have = titles.names(current)
    best, count = current, len(have)
    for other in others:
        names = titles.names(other or "")
        if names & have and len(names) > count:
            best, count = other, len(names)
    return best


def _credit_names(credit: str) -> list[str]:
    """ "Olivia Rodrigo & Chappell Roan" → ["Olivia Rodrigo", "Chappell Roan"]."""
    return [part.strip() for part in match.titles.FEAT.split(credit or "") if part.strip()][:4]


def _genre_name(title: str) -> str:
    """ "Top 50 Hip Hop Music Videos United States" → "Hip Hop"."""
    name = re.sub(r"(?i)^top\s+\d+\s+", "", title or "")
    name = re.split(r"(?i)\s+music videos\b", name)[0]
    return name.strip() or title


def _song_key(title: str, artist: str) -> tuple[str, str]:
    """One song whoever lists it: its simple title and first artist."""
    first = (_credit_names(match.channel_artist(artist)) or [artist])[0]
    return titles.simple(_bare(title)), titles.simple(first)


def _with_apple(videos: list[Video], apple: list[Video]) -> list[Video]:
    """YouTube's videos, in its order, with Apple's release dates where Apple
    has the same take; then the videos only Apple lists."""
    left = list(apple)
    for exact in (True, False):
        for video in videos:
            if video.released:
                continue
            # Exact first; then without brackets ("Bésame (Video Oficial)"),
            # never a title inside another ("The Making of 'Dai Dai'").
            hit = next((a for a in left if (titles.simple(a.title) == titles.simple(video.title) if exact
                                            else titles.simple(_bare(a.title)) == titles.simple(_bare(video.title)))
                        and titles.same_version(a.title, video.title)), None)
            if hit:
                video.released = hit.released
                video.genre = video.genre or hit.genre
                video.explicit = video.explicit or hit.explicit
                left.remove(hit)
    # Apple often lists a take twice (a re-release): one YouTube has is enough.
    seen = {(titles.simple(_bare(v.title)), titles.version(v.title)) for v in videos}
    only_apple = []
    for video in left:
        key = (titles.simple(_bare(video.title)), titles.version(video.title))
        if key not in seen:
            seen.add(key)
            only_apple.append(video)
    return [*videos, *only_apple]


_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_CLEAN_WORDS = re.compile(r"(?i)\b(?:clean|censored|radio edit)\b")
_EXPLICIT_WORDS = re.compile(r"(?i)\b(?:explicit|uncensored|dirty)\b")


def _bare(title: str) -> str:
    """The title without any bracket: "Gnarly (Clean Edit)" → "Gnarly"."""
    return _BRACKETS.sub("", title or "").strip() or (title or "").strip()


def _label(title: str, explicit: bool) -> str:
    inside = " ".join(re.findall(r"[\(\[]([^\)\]]*)[\)\]]", title or ""))
    if _CLEAN_WORDS.search(inside) and not re.search(r"(?i)uncensored", inside):
        return "Clean"
    taken = titles.version(title)
    if taken:
        return " · ".join(name.capitalize() for name in taken)
    if explicit or _EXPLICIT_WORDS.search(inside):
        return "Explicit"
    inside = re.sub(r"(?i)\b(?:feat\.?|ft\.?|with)\b.*", "", inside).strip(" -")
    return inside[:28].strip().title() if inside else "Original"


def _versions(results: list[dict], kind: str, base: str, detail: VideoDetail) -> list[Version]:
    wanted = titles.names(detail.artist) | titles.names(match.channel_artist(detail.artist))
    found, seen = [], {detail.youtube_id}
    for item in results:
        video = youtube.to_video(item)
        if not video or video.youtube_id in seen:
            continue
        if kind == "video" and not video.official:
            continue
        names = set().union(*(titles.names(match.channel_artist(a.name)) for a in video.artists)) | \
            titles.names(video.artist)
        if wanted and not names & wanted:
            continue
        if titles.simple(_bare(video.title)) != titles.simple(base):
            continue
        seen.add(video.youtube_id)
        if kind == "song":
            video.official = True
            video.thumbnail = youtube._image(item.get("thumbnails"), 544) or video.thumbnail
        found.append(Version(**video.model_dump(), kind=kind, label=_label(video.title, video.explicit)))
    return found[:12]
