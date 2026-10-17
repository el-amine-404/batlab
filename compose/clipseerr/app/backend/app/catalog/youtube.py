"""YouTube Music, without an account or a key (ytmusicapi): the newest music
videos, the charts of a country (with genre charts for some), artists and
their videos, search, and each video's details.

ytmusicapi is synchronous (requests); each call runs on a worker thread with
that thread's own client, so calls run side by side.
"""

import asyncio
import re
import threading

from ytmusicapi import YTMusic

from .match import channel_artist, clean
from .models import ArtistPage, ArtistRef, ArtistSummary, Video

LANGUAGE = "en"
OFFICIAL = "MUSIC_VIDEO_TYPE_OMV"
_local = threading.local()


def _client(region: str) -> YTMusic:
    clients = getattr(_local, "clients", None)
    if clients is None:
        clients = _local.clients = {}
    if region not in clients:
        clients[region] = YTMusic(language=LANGUAGE, location=region if region != "ZZ" else "")
    return clients[region]


async def _call(region: str, method: str, *args, **kwargs):
    def run():
        return getattr(_client(region), method)(*args, **kwargs)

    return await asyncio.to_thread(run)


# ---- turning ytmusicapi's answers into Videos ---------------------------------------

def thumbnail(video_id: str) -> str:
    """16:9 without black bars (YouTube's hqdefault is 4:3)."""
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


def _seconds(item: dict) -> int | None:
    if isinstance(item.get("duration_seconds"), (int, float)):
        return int(item["duration_seconds"])
    text = item.get("duration") or item.get("length")
    if isinstance(text, str) and re.fullmatch(r"\d+(?::\d{1,2}){1,2}", text):
        seconds = 0
        for part in text.split(":"):
            seconds = seconds * 60 + int(part)
        return seconds
    return None


def _image(thumbnails: list | None, size: int = 544) -> str | None:
    """The largest thumbnail, at size px when Google's URL says its size."""
    if not thumbnails:
        return None
    url = max(thumbnails, key=lambda t: t.get("width", 0) or 0).get("url")
    if url and re.search(r"=w\d+-h\d+", url):
        url = re.sub(r"=w\d+-h\d+", f"=w{size}-h{size}", url)
    elif url and re.search(r"=s\d+", url):
        url = re.sub(r"=s\d+", f"=s{size}", url)
    return url


_SPOKEN_LENGTH = re.compile(r"^(?:(\d+) hr\s*)?(?:(\d+) min\s*)?(?:(\d+) sec)?$")


def _shifted(item: dict) -> dict:
    """ytmusicapi reads some new videos one field off: the length ("3 min 42
    sec") as the artist, the artist as the views. Puts them back."""
    artists = item.get("artists") or []
    spoken = _SPOKEN_LENGTH.match(artists[0].get("name") or "") if len(artists) == 1 else None
    if not spoken or not any(spoken.groups()):
        return item
    hours, minutes, seconds = (int(part or 0) for part in spoken.groups())
    fixed = dict(item, duration_seconds=hours * 3600 + minutes * 60 + seconds, views=None)
    fixed["artists"] = [{"name": item["views"], "id": None}] if item.get("views") else []
    return fixed


def to_video(item: dict) -> Video | None:
    video_id = item.get("videoId")
    if not video_id or item.get("isAvailable") is False:
        return None
    item = _shifted(item)
    refs = [ArtistRef(name=a["name"], id=a.get("id")) for a in item.get("artists") or []
            if a.get("name") and not re.fullmatch(r"[\d.,]+[KMB]? views", a["name"])]
    channel = refs[0].name if refs else ""
    title, credit = clean(item.get("title") or "", channel)
    if credit:
        artist = credit
    elif refs:
        artist = " & ".join(channel_artist(ref.name) for ref in refs)
    else:
        artist = "Unknown artist"
    return Video(
        id=f"yt:{video_id}",
        source="youtube",
        youtube_id=video_id,
        title=title,
        original_title=item.get("title") if item.get("title") != title else None,
        artist=artist,
        artists=refs or [ArtistRef(name=artist)],
        thumbnail=thumbnail(video_id),
        duration=_seconds(item),
        views=(item.get("views") or None),
        explicit=bool(item.get("isExplicit")),
        official=item.get("videoType", OFFICIAL) == OFFICIAL,
    )


def to_videos(items: list[dict]) -> list[Video]:
    seen, videos = set(), []
    for item in items or []:
        video = to_video(item)
        if video and video.id not in seen:
            seen.add(video.id)
            videos.append(video)
    return videos


# ---- what the catalog asks ------------------------------------------------------------

async def new_videos(region: str) -> list[Video]:
    explore = await _call(region, "get_explore")
    return to_videos(explore.get("new_videos") or [])


async def charts(region: str) -> dict:
    """{"videos": [{title, playlistId}], "genres": [{title, playlistId, image}]}:
    the music video charts of a country; genre charts exist for a few."""
    found = await _call(region, "get_charts", region if region != "ZZ" else "ZZ")
    videos = [{"title": c["title"], "playlistId": c["playlistId"]} for c in found.get("videos") or []
              if c.get("playlistId")]
    genres = [{"title": c["title"], "playlistId": c["playlistId"], "image": _image(c.get("thumbnails"), 400)}
              for c in found.get("genres") or [] if c.get("playlistId")]
    return {"videos": videos, "genres": genres}


async def playlist(region: str, playlist_id: str, limit: int = 100) -> tuple[str, list[Video]]:
    found = await _call(region, "get_playlist", playlist_id, limit=limit)
    return found.get("title") or "", to_videos(found.get("tracks") or [])


async def search_videos(region: str, query: str, limit: int = 40) -> list[Video]:
    results = await _call(region, "search", query, filter="videos", limit=limit)
    videos = to_videos(results)
    # The artists' own videos before covers and fan uploads.
    return sorted(videos, key=lambda v: not v.official)


async def search_artists(region: str, query: str, limit: int = 8) -> list[ArtistSummary]:
    results = await _call(region, "search", query, filter="artists", limit=limit)
    return [ArtistSummary(id=r["browseId"], name=r.get("artist") or r.get("title") or "",
                          thumbnail=_image(r.get("thumbnails"), 400), subscribers=r.get("subscribers"))
            for r in results if r.get("browseId") and r.get("browseId", "").startswith("UC")][:limit]


async def search_raw(region: str, query: str, kind: str, limit: int = 10) -> list[dict]:
    """Search results as ytmusicapi gives them, for matching (match.py)."""
    return await _call(region, "search", query, filter=kind, limit=limit)


async def artist(region: str, channel_id: str) -> ArtistPage:
    found = await _call(region, "get_artist", channel_id)
    section = found.get("videos") or {}
    videos = to_videos(section.get("results") or [])
    browse = section.get("browseId") or ""
    if browse:
        # The section shows a few; its playlist has them all.
        playlist_id = browse[2:] if browse.startswith("VL") else browse
        try:
            _title, every = await playlist(region, playlist_id, limit=200)
            if len(every) >= len(videos):
                videos = every
        except Exception:
            pass
    related = [ArtistSummary(id=r["browseId"], name=r.get("title") or "", thumbnail=_image(r.get("thumbnails"), 300),
                             subscribers=r.get("subscribers"))
               for r in (found.get("related") or {}).get("results") or [] if r.get("browseId")]
    return ArtistPage(
        id=channel_id,
        name=found.get("name") or "",
        thumbnail=_image(found.get("thumbnails"), 600),
        subscribers=found.get("subscribers"),
        description=found.get("description"),
        views=found.get("views"),
        videos=videos,
        related=related[:12],
    )


async def song(region: str, video_id: str) -> dict:
    """A video's details: {title, author, channel_id, seconds, views,
    published, embeddable, playable, kind, description}."""
    found = await _call(region, "get_song", video_id)
    details = found.get("videoDetails") or {}
    playability = found.get("playabilityStatus") or {}
    micro = (found.get("microformat") or {}).get("microformatDataRenderer") or {}
    seconds = details.get("lengthSeconds")
    return {
        "title": details.get("title") or "",
        "author": details.get("author") or "",
        "channel_id": details.get("channelId"),
        "seconds": int(seconds) if str(seconds or "").isdigit() else None,
        "views": int(details["viewCount"]) if str(details.get("viewCount") or "").isdigit() else None,
        "published": (micro.get("publishDate") or micro.get("uploadDate") or "")[:10] or None,
        "embeddable": playability.get("playableInEmbed", True) is not False,
        "playable": playability.get("status", "OK") == "OK",
        "kind": details.get("musicVideoType"),
        "description": (micro.get("description") or "")[:2000] or None,
    }


def short_count(value: int | None) -> str | None:
    """1171065826 → "1.2B"."""
    if value is None:
        return None
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= limit:
            text = f"{value / limit:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return str(value)

