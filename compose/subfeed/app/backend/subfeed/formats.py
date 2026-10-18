"""A feed's posts in the formats readers know: JSON Feed 1.1
(https://www.jsonfeed.org/version/1.1/), for programs, and Atom (RFC 4287),
for feed readers. What JSON Feed has no field for is in each item's
"_subfeed" extension: the post's id, what it links to, the videos it
carries. Pure functions of the posts, as the API gives them."""

from datetime import datetime, timezone
from xml.sax.saxutils import escape, quoteattr

JSON_FEED_VERSION = "https://jsonfeed.org/version/1.1"
EXTENSION_DOCS = "https://github.com/el-amine-404/batlab/blob/main/docs/services.md#subfeed-communities--feeds"


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")


def _text(post: dict) -> str:
    link = post["link"]["url"]
    return f"{post['title']}\n{link}" if link else post["title"]


def json_feed(feed: dict, posts: list[dict], *, feed_url: str, next_url: str | None = None) -> dict:
    """feed: as GET /api/v1/feeds/{slug} gives it."""
    items = []
    for post in posts:
        item = {
            "id": f"subfeed:{post['id']}",
            "url": post["url"],
            "title": post["title"],
            "content_text": _text(post),
            "date_published": _iso(post["posted_at"]),
            "_subfeed": {"feed": post["feed"], "post_id": post["post_id"], "link_kind": post["link"]["kind"],
                         "domain": post["link"]["domain"], "videos": post["videos"]},
        }
        if post["link"]["url"]:
            item["external_url"] = post["link"]["url"]
        if post["author"]:
            item["authors"] = [{"name": post["author"]}]
        items.append(item)
    body = {
        "version": JSON_FEED_VERSION,
        "title": feed["title"],
        "description": f"What {feed['title']} posts, newest first (subfeed).",
        "feed_url": feed_url,
        "_subfeed": {"about": EXTENSION_DOCS, "feed": feed["slug"], "source": feed["source"],
                     "target": feed["target"]},
        "items": items,
    }
    if feed.get("home_url"):
        body["home_page_url"] = feed["home_url"]
    if next_url:
        body["next_url"] = next_url
    return body


def atom(feed: dict, posts: list[dict], *, self_url: str) -> str:
    updated = max((p["posted_at"] for p in posts), default=feed.get("changed_at") or feed["created_at"])
    entries = []
    for post in posts:
        href = (post["videos"][0]["url"] if post["videos"] else None) or post["link"]["url"] or post["url"]
        author = f"<author><name>{escape(post['author'])}</name></author>" if post["author"] else ""
        entries.append(
            f"<entry><id>urn:subfeed:{escape(post['id'])}</id><title>{escape(post['title'])}</title>"
            f"<link href={quoteattr(href)}/><link rel=\"via\" href={quoteattr(post['url'])}/>"
            f"<updated>{_iso(post['posted_at'])}</updated>{author}"
            f"<summary>{escape(_text(post))}</summary></entry>")
    home = f"<link rel=\"alternate\" href={quoteattr(feed['home_url'])}/>" if feed.get("home_url") else ""
    return (f'<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
            f"<id>urn:subfeed:{escape(feed['slug'])}</id><title>{escape(feed['title'])}</title>"
            f"<link rel=\"self\" href={quoteattr(self_url)}/>{home}<updated>{_iso(updated)}</updated>"
            # Atom wants an author for every entry: the feed's stands in for posts without one.
            f"<author><name>{escape(feed['title'])}</name></author>"
            f"<generator>subfeed</generator>{''.join(entries)}</feed>")
