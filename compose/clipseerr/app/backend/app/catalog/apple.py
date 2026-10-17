"""Apple's public feeds and search (no account, no key): the top music videos
of a storefront, overall and for about seventy genres, with release dates,
genres, cover art and a 30 s preview each. YouTube has the full video; an
Apple entry finds it when it is opened (service.py)."""

import httpx

from .models import ArtistRef, Category, Video

TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# Apple's music video genres (31 = Music Videos), as its feeds name them; the
# list the page offers, most asked for first. Checked on 2026-10-17.
GENRES: dict[int, str] = {
    1614: "Pop", 1618: "Hip-Hop/Rap", 1615: "R&B/Soul", 1620: "Alternative", 1621: "Rock", 1617: "Dance",
    1607: "Electronic", 1612: "Latin", 1686: "K-Pop", 1606: "Country", 1624: "Reggae", 1619: "World",
    1632: "French Pop", 1633: "German Pop", 1627: "J-Pop", 1629: "Anime", 1671: "Brazilian", 1673: "Baile Funk",
    1681: "Sertanejo", 1690: "Bollywood", 1694: "Punjabi Pop", 1655: "C-Pop", 1658: "Mandopop", 1688: "Heavy Metal",
    1611: "Jazz", 1602: "Blues", 1605: "Classical", 1610: "Singer/Songwriter", 1616: "Soundtrack",
    1622: "Christian", 1625: "Easy Listening", 1684: "Instrumental", 1604: "Children's Music", 1608: "Holiday",
}


def _label(entry: dict, key: str) -> str | None:
    value = entry.get(key)
    return value.get("label") if isinstance(value, dict) else None


def _artwork(url: str | None, width: int = 640) -> str | None:
    """Apple's artwork URLs end in <w>x<h>bb.jpg; any size can be asked for
    (16:9 here: a video's frame)."""
    if not url:
        return None
    head, _, tail = url.rpartition("/")
    return f"{head}/{width}x{width * 9 // 16}bb.jpg" if tail.endswith(("bb.jpg", "bb.png")) else url


def _from_feed(entry: dict) -> Video | None:
    track_id = ((entry.get("id") or {}).get("attributes") or {}).get("im:id")
    title = _label(entry, "im:name")
    if not track_id or not title:
        return None
    artist = _label(entry, "im:artist") or "Unknown artist"
    images = entry.get("im:image") or []
    links = entry.get("link") or []
    links = links if isinstance(links, list) else [links]
    preview = next((l["attributes"]["href"] for l in links
                    if (l.get("attributes") or {}).get("im:assetType") == "preview"), None)
    return Video(
        id=f"am:{track_id}",
        source="apple",
        title=title,
        artist=artist,
        artists=[ArtistRef(name=artist)],
        thumbnail=_artwork(images[-1]["label"] if images else None),
        released=(_label(entry, "im:releaseDate") or "")[:10] or None,
        genre=((entry.get("category") or {}).get("attributes") or {}).get("label"),
        preview_url=preview,
    )


def _from_search(item: dict) -> Video | None:
    if item.get("kind") != "music-video" or not item.get("trackId"):
        return None
    artist = item.get("artistName") or "Unknown artist"
    return Video(
        id=f"am:{item['trackId']}",
        source="apple",
        title=item.get("trackName") or "",
        artist=artist,
        artists=[ArtistRef(name=artist)],
        thumbnail=_artwork(item.get("artworkUrl100")),
        duration=int(item["trackTimeMillis"]) // 1000 if item.get("trackTimeMillis") else None,
        released=(item.get("releaseDate") or "")[:10] or None,
        genre=item.get("primaryGenreName"),
        explicit=item.get("trackExplicitness") == "explicit",
        preview_url=item.get("previewUrl"),
    )


class Apple:
    def __init__(self, client: httpx.AsyncClient):
        self._http = client

    async def _json(self, url: str, params: dict | None = None) -> dict:
        response = await self._http.get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()

    async def top(self, storefront: str, genre: int | None = None, limit: int = 100) -> list[Video]:
        path = f"limit={limit}/genre={genre}" if genre else f"limit={limit}"
        found = await self._json(f"https://itunes.apple.com/{storefront}/rss/topmusicvideos/{path}/json")
        entries = (found.get("feed") or {}).get("entry") or []
        entries = entries if isinstance(entries, list) else [entries]
        return [video for video in map(_from_feed, entries) if video]

    async def search(self, storefront: str, query: str, limit: int = 25) -> list[Video]:
        found = await self._json("https://itunes.apple.com/search",
                                 {"term": query, "entity": "musicVideo", "limit": limit, "country": storefront})
        return [video for video in map(_from_search, found.get("results") or []) if video]

    async def lookup(self, storefront: str, track_id: str) -> Video | None:
        found = await self._json("https://itunes.apple.com/lookup", {"id": track_id, "country": storefront})
        for item in found.get("results") or []:
            video = _from_search(item)
            if video:
                return video
        return None

    @staticmethod
    def categories() -> list[Category]:
        return [Category(id=f"am:{genre}", title=title, group="Apple Music", source="apple")
                for genre, title in GENRES.items()]
