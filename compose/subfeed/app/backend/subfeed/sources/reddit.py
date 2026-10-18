"""Reddit, without an account or a key.

- Reddit's own Atom feed of a subreddit's newest posts (/r/<name>/new/.rss):
  fresh to the minute, the last 100 posts. Reddit refuses its JSON without a
  login, and answers 429 to an address that asks too often: one call at a
  time, at least SPACING seconds apart.
- PullPush (api.pullpush.io), an archive of Reddit: the history, page by
  page, a few days behind. A new feed is filled from it, and it stands in
  when Reddit refuses.
"""

import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from ..domain import Post
from ..links import videos_in
from .base import BadAnswer, Source
from .fetch import Fetcher

SPACING = 10.0
ATOM = "{http://www.w3.org/2005/Atom}"
# In a post's Atom content, "[link]" is what the post links to.
_ANCHOR = re.compile(r'<a href="([^"]+)">\[link\]</a>')


def _iso(value: str) -> float:
    # "2026-10-17T08:48:16+00:00"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def parse_atom(body: str) -> list[Post]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise BadAnswer(f"Reddit's feed isn't readable ({exc})") from exc
    posts = []
    for entry in root.iter(f"{ATOM}entry"):
        post_id = (entry.findtext(f"{ATOM}id") or "").removeprefix("t3_")
        link = entry.find(f"{ATOM}link")
        published = entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated")
        if not post_id or link is None or not published:
            continue
        author = entry.findtext(f"{ATOM}author/{ATOM}name")
        content = entry.findtext(f"{ATOM}content") or ""
        permalink = link.get("href", "")
        # A text post's [link] is the post itself.
        target = _ANCHOR.search(content)
        target = html.unescape(target.group(1)) if target else None
        posts.append(Post(id=post_id, title=html.unescape(entry.findtext(f"{ATOM}title") or "").strip(),
                          url=permalink, author=(author or "").removeprefix("/u/") or None,
                          posted_at=_iso(published), videos=videos_in(content),
                          link=None if not target or target.rstrip("/") == permalink.rstrip("/") else target))
    return posts


def parse_pullpush(data: dict) -> list[Post]:
    if not isinstance(data, dict):
        raise BadAnswer("PullPush's answer isn't what it sends")
    posts = []
    for item in data.get("data") or []:
        if not isinstance(item, dict) or not item.get("id") or not item.get("created_utc"):
            continue
        text = " ".join(str(item.get(key) or "") for key in ("url", "url_overridden_by_dest", "selftext"))
        permalink = item.get("permalink") or f"/comments/{item['id']}/"
        target = item.get("url_overridden_by_dest") or item.get("url")
        posts.append(Post(id=str(item["id"]), title=html.unescape(item.get("title") or "").strip(),
                          url=f"https://www.reddit.com{permalink}", author=item.get("author"),
                          posted_at=float(item["created_utc"]), videos=videos_in(text),
                          link=None if item.get("is_self") or not target or "/comments/" in target else target))
    return posts


class Reddit(Source):
    key = "reddit"
    label = "Reddit"
    target_label = "Subreddit"
    target_help = "2 to 21 letters, digits or _, without r/ (SexyMusicVideos)."
    target_pattern = r"^[A-Za-z0-9_]{2,21}$"
    archive_label = "PullPush"

    def __init__(self, http: httpx.AsyncClient, user_agent: str, spacing: float = SPACING):
        self._fetch = Fetcher(http, user_agent, spacing)

    def normalize(self, raw: str) -> str:
        # "r/Name", "/r/Name/", "https://www.reddit.com/r/Name/" → "Name"
        value = re.sub(r"^(?:https?://(?:www\.|old\.)?reddit\.com)?/?r/", "", str(raw or "").strip()).strip("/")
        return super().normalize(value)

    def home(self, target: str) -> str:
        return f"https://www.reddit.com/r/{target}/"

    def title(self, target: str) -> str:
        return f"r/{target}"

    async def newest(self, target: str) -> list[Post]:
        response = await self._fetch.get(f"https://www.reddit.com/r/{target}/new/.rss", {"limit": 100})
        return parse_atom(response.text)

    async def history(self, target: str, before: float | None = None) -> list[Post]:
        params = {"subreddit": target, "size": self.page_size, "sort": "desc", "sort_type": "created_utc"}
        if before:
            params["before"] = int(before)
        response = await self._fetch.get("https://api.pullpush.io/reddit/search/submission/", params)
        try:
            return parse_pullpush(response.json())
        except json.JSONDecodeError as exc:
            raise BadAnswer("PullPush's answer isn't JSON") from exc
