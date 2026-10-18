"""Every video and artist clipseerr has shown (a list, a search, an artist's
page), in the database with a full-text index: search answers from it at once,
while YouTube Music's own answer takes a second.

A list is seen again and again: only what is new or changed is written."""

import hashlib
import json
import re
import threading
import time

from batlab_common import titles

from .catalog.models import ArtistSummary, Video
from .tags import tags_of
from .db import Database

_WORD = re.compile(r"\w+")
# A word of the query matches the start of a word of the title or the artist ("shak" → "Shakira").
_SHORTEST = 2


def _query(text: str, joined: str = " ") -> str | None:
    """FTS5 query: every word (or any, joined by " OR "), as a prefix (quoted, so no word is an operator)."""
    words = [w for w in _WORD.findall(text.casefold()) if len(w) >= _SHORTEST or w.isdigit()]
    return joined.join(f'"{w}"*' for w in words[:8]) or None


def _count(text: str | None) -> float:
    """ "52.1M subscribers" → 52100000."""
    found = re.match(r"([\d.]+)\s*([KMB]?)", text or "")
    if not found:
        return 0
    return float(found.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[found.group(2)]


def _sign(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


class Known:
    def __init__(self, db: Database):
        self._db = db
        self._lock = threading.Lock()
        # What is written already: id → signature, so a list seen again costs no write.
        self._signed = {row["id"]: row["sign"] for row in db.all("SELECT id, sign FROM known_videos")}
        self._signed.update({f"artist:{row['id']}": row["sign"] for row in db.all("SELECT id, sign FROM known_artists")})
        # A long list (Hot: hundreds) read a page at a time: looked through once, until its videos change.
        self._lists: dict[str, int] = {}

    # ---- seeing -----------------------------------------------------------------------

    def saw(self, videos: list[Video], artists: list[ArtistSummary] = (), list_id: str | None = None) -> None:
        """Keeps these, and every artist of theirs with a page."""
        if list_id:
            ids = hash(tuple(v.id for v in videos))
            if self._lists.get(list_id) == ids:
                return
            self._lists[list_id] = ids
        now = time.time()
        writes = []
        with self._lock:
            for video in videos:
                kept = video.model_dump(exclude={"library", "views"})
                sign = _sign(kept)
                if self._signed.get(video.id) != sign:
                    self._signed[video.id] = sign
                    writes.append(("INSERT INTO known_videos (id, title, artist, video, sign, seen_at) "
                                   "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET title = excluded.title, "
                                   "artist = excluded.artist, video = excluded.video, sign = excluded.sign, "
                                   "seen_at = excluded.seen_at",
                                   (video.id, video.original_title or video.title, video.artist,
                                    json.dumps(kept, ensure_ascii=False), sign, now)))
            # A fan upload's "artist" is its channel: not an artist to suggest.
            refs = [(ArtistSummary(id=ref.id, name=ref.name), True)
                    for v in videos if v.official for ref in v.artists if ref.id]
            for artist, credited in [*((a, False) for a in artists), *refs]:
                sign = _sign(artist.name, artist.thumbnail, artist.subscribers)
                old = self._signed.get(f"artist:{artist.id}")
                # A video's credit names the artist only: it doesn't take their picture away.
                if old == sign or (old and credited):
                    continue
                self._signed[f"artist:{artist.id}"] = sign
                writes.append(("INSERT INTO known_artists (id, name, thumbnail, subscribers, sign, seen_at) "
                               "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET name = excluded.name, "
                               "thumbnail = coalesce(excluded.thumbnail, thumbnail), "
                               "subscribers = coalesce(excluded.subscribers, subscribers), "
                               "sign = excluded.sign, seen_at = excluded.seen_at",
                               (artist.id, artist.name, artist.thumbnail, artist.subscribers, sign, now)))
        if writes:
            self._db.many(writes)

    # ---- finding ------------------------------------------------------------------------

    def search(self, text: str, limit: int = 24) -> tuple[list[ArtistSummary], list[Video]]:
        """The best matches: videos with every word in their title or artist (the
        artist's name counting most); artists with any word in their name ("shakira
        soltera" finds Shakira)."""
        query = _query(text)
        if not query:
            return [], []
        rows = self._db.all(
            "SELECT v.video FROM known_search s JOIN known_videos v ON v.rowid = s.rowid "
            "WHERE known_search MATCH ? ORDER BY bm25(known_search, 1.0, 2.0) LIMIT ?", (query, limit * 2))
        # The same song from YouTube and from Apple: once, in the best one's place, as YouTube's (it plays).
        at: dict[tuple[str, str], int] = {}
        videos: list[Video] = []
        for video in (Video.model_validate_json(row["video"]) for row in rows):
            key = (titles.simple(video.title), titles.simple(video.artist))
            if key not in at:
                at[key] = len(videos)
                videos.append(video)
            elif video.source == "youtube" and videos[at[key]].source != "youtube":
                videos[at[key]] = video
        # The artists' own videos before the covers, karaoke and tutorials of the same words.
        videos = sorted(videos, key=lambda v: not v.official)[:limit]
        artists = self._db.all(
            "SELECT a.id, a.name, a.thumbnail, a.subscribers FROM known_artist_search s "
            "JOIN known_artists a ON a.rowid = s.rowid WHERE known_artist_search MATCH ? "
            "ORDER BY bm25(known_artist_search) LIMIT 24", (_query(text, " OR "),))
        wanted = titles.simple(text)
        # The artist named exactly first, then the most credited (on the videos known), the most
        # followed: "shak" is Shakira before the channels named after her.
        ranked = sorted(artists, key=lambda a: (titles.simple(a["name"]) != wanted, -self._credited(a["name"]),
                                                -_count(a["subscribers"]), not a["thumbnail"]))
        found = [ArtistSummary(**dict(row)) for row in ranked[:8]]
        for artist in found:
            if artist.thumbnail and "/ytm/images/artist_avatar" in artist.thumbnail:
                artist.thumbnail = None
        return found, videos

    def _credited(self, name: str) -> int:
        phrase = " ".join(_WORD.findall(name.casefold()))
        if not phrase:
            return 0
        row = self._db.one("SELECT COUNT(*) AS n FROM known_search WHERE known_search MATCH ?",
                           (f'artist : "{phrase}"',))
        return row["n"] if row else 0

    # ---- tags -----------------------------------------------------------------------------

    def set_language(self, video_id: str, code: str) -> None:
        self._db.run("INSERT INTO known_languages (video_id, language) VALUES (?, ?) "
                     "ON CONFLICT (video_id) DO UPDATE SET language = excluded.language", (video_id, code))

    def tagged(self) -> list[tuple[Video, set[str]]]:
        """Every video known, with its tags (tags.py), the newest first."""
        languages = {row["video_id"]: row["language"] for row in self._db.all("SELECT * FROM known_languages")}
        found = []
        for row in self._db.all("SELECT id, video FROM known_videos ORDER BY seen_at DESC"):
            video = Video.model_validate_json(row["video"])
            found.append((video, tags_of(video, languages.get(row["id"]))))
        return found

    def count(self) -> dict:
        videos = self._db.one("SELECT COUNT(*) AS n FROM known_videos")
        artists = self._db.one("SELECT COUNT(*) AS n FROM known_artists")
        return {"videos": videos["n"] if videos else 0, "artists": artists["n"] if artists else 0}
