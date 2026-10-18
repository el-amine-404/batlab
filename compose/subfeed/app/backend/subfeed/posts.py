"""Every post the feeds found, and the questions readers ask of them: which
feeds, what they link to, which words, when. Everything comes out unless a
reader narrows it (PostQuery)."""

import json
import re
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from .db import Database
from .domain import Post, Video
from .links import domain_of, kind_of, video_url


@dataclass
class PostQuery:
    """What GET …/posts narrows to; empty lists mean all."""

    feeds: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    # Only posts carrying a video of these providers.
    providers: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    words: str = ""
    author: str = ""
    since: float | None = None
    until: float | None = None
    # One post a video: the first in its feed that carried it (posts without a video stay).
    unique: bool = False
    limit: int = 100
    offset: int = 0

    def where(self) -> tuple[str, list]:
        clauses, params = [], []

        def any_of(column: str, values: list[str]):
            clauses.append(f"{column} IN ({','.join('?' * len(values))})")
            params.extend(values)

        if self.feeds:
            any_of("p.feed", self.feeds)
        if self.kinds:
            any_of("p.kind", self.kinds)
        if self.providers:
            clauses.append(f"EXISTS (SELECT 1 FROM json_each(p.videos) WHERE json_extract(value, '$.provider') IN "
                           f"({','.join('?' * len(self.providers))}))")
            params.extend(self.providers)
        if self.domains:
            any_of("p.domain", [d.casefold().removeprefix("www.") for d in self.domains])
        for word in self.words.split():
            clauses.append("p.title LIKE ? ESCAPE '\\'")
            params.append("%" + re.sub(r"([%_\\])", r"\\\1", word) + "%")
        if self.author:
            clauses.append("p.author = ? COLLATE NOCASE")
            params.append(self.author)
        if self.since is not None:
            clauses.append("p.posted_at >= ?")
            params.append(self.since)
        if self.until is not None:
            clauses.append("p.posted_at < ?")
            params.append(self.until)
        if self.unique:
            clauses.append("(p.first_video IS NULL OR p.posted_at = (SELECT MIN(f.posted_at) FROM posts f "
                           "WHERE f.feed = p.feed AND f.first_video = p.first_video))")
        return (f" WHERE {' AND '.join(clauses)}" if clauses else ""), params


def _videos(raw: str) -> list[dict]:
    return [{"provider": v["provider"], "id": v["id"], "url": video_url(Video(v["provider"], v["id"]))}
            for v in json.loads(raw)]


def _public(row: sqlite3.Row) -> dict:
    return {
        "id": f"{row['feed']}:{row['post_id']}",
        "feed": row["feed"],
        "post_id": row["post_id"],
        "title": row["title"],
        "url": row["url"],
        "author": row["author"],
        "posted_at": row["posted_at"],
        "found_at": row["found_at"],
        "link": {"url": row["link"], "kind": row["kind"], "domain": row["domain"]},
        "videos": _videos(row["videos"]),
    }


class PostStore:
    def __init__(self, db: Database):
        self._db = db

    def add(self, feed: str, posts: Iterable[Post]) -> int:
        """Keeps the posts not kept yet, in one transaction; how many were new."""
        now = time.time()
        rows = [(feed, post.id, post.title[:300], post.url, post.author, post.posted_at, post.link,
                 kind_of(post.link, post.videos), domain_of(post.link),
                 json.dumps([{"provider": v.provider, "id": v.id} for v in post.videos]),
                 post.videos[0].key if post.videos else None, now)
                for post in posts]
        if not rows:
            return 0
        return self._db.many("INSERT OR IGNORE INTO posts (feed, post_id, title, url, author, posted_at, link, kind, "
                             "domain, videos, first_video, found_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             rows)

    def query(self, query: PostQuery) -> tuple[list[dict], int]:
        """A page of matching posts, newest first, and how many match in all."""
        clause, params = query.where()
        total = self._db.one(f"SELECT COUNT(*) AS n FROM posts p{clause}", tuple(params))["n"]
        rows = self._db.all(f"SELECT p.* FROM posts p{clause} ORDER BY p.posted_at DESC, p.post_id LIMIT ? OFFSET ?",
                            (*params, query.limit, query.offset))
        return [_public(r) for r in rows], total

    def newest(self, feed: str, since_found: float) -> list[dict]:
        """What a feed found at or after since_found, newest first."""
        rows = self._db.all("SELECT p.* FROM posts p WHERE p.feed = ? AND p.found_at >= ? ORDER BY p.posted_at DESC",
                            (feed, since_found))
        return [_public(r) for r in rows]

    def counts(self) -> dict[str, dict]:
        """Each feed's posts, and of them those carrying a video."""
        return {r["feed"]: {"posts": r["n"], "videos": r["videos"]}
                for r in self._db.all("SELECT feed, COUNT(*) AS n, COUNT(first_video) AS videos FROM posts "
                                      "GROUP BY feed")}

    def total(self) -> int:
        return self._db.one("SELECT COUNT(*) AS n FROM posts")["n"]

    def forget(self, before: float) -> list[str]:
        """Drops posts posted before then; the feeds that lost some."""
        feeds = [r["feed"] for r in self._db.all("SELECT DISTINCT feed FROM posts WHERE posted_at < ?", (before,))]
        if feeds:
            self._db.run("DELETE FROM posts WHERE posted_at < ?", (before,))
        return feeds
