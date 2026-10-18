"""python -m unittest discover -s tests (in the image, see docs/music.md)."""

import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from subfeed import config as config_module  # noqa: E402
from subfeed import formats  # noqa: E402
from subfeed.api.app import create_app  # noqa: E402
from subfeed.db import Database  # noqa: E402
from subfeed.domain import Invalid, Post, Video  # noqa: E402
from subfeed.feeds import FeedIn  # noqa: E402
from subfeed.links import kind_of, videos_in  # noqa: E402
from subfeed.posts import PostQuery  # noqa: E402
from subfeed.services import build  # noqa: E402
from subfeed.settings import SettingsPatch  # noqa: E402
from subfeed.sources import BadAnswer, Refused, Unreachable  # noqa: E402
from subfeed.sources.reddit import Reddit, parse_atom, parse_pullpush  # noqa: E402

ATOM = """<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><author><name>/u/someone</name></author><content type="html">&lt;a href=&quot;https://youtu.be/fLCd0tXSLZU?si=x&quot;&gt;[link]&lt;/a&gt;</content>
<id>t3_abc</id><link href="https://www.reddit.com/r/X/comments/abc/t/"/><published>2026-10-17T07:00:00+00:00</published>
<title>Ellize - Mystika - Official Music Video</title></entry>
<entry><content type="html">&lt;a href=&quot;https://v.redd.it/x&quot;&gt;[link]&lt;/a&gt;</content><id>t3_def</id>
<link href="https://www.reddit.com/r/X/comments/def/t/"/><published>2026-10-17T06:00:00+00:00</published><title>M</title></entry>
<entry><content type="html">&lt;a href=&quot;https://www.reddit.com/r/X/comments/ghi/t/&quot;&gt;[link]&lt;/a&gt;</content><id>t3_ghi</id>
<link href="https://www.reddit.com/r/X/comments/ghi/t/"/><published>2026-10-17T05:00:00+00:00</published><title>Talk</title></entry>
</feed>"""


def yt(*ids: str) -> list[Video]:
    return [Video("youtube", i) for i in ids]


class Links(unittest.TestCase):
    def test_every_form_once(self):
        text = ("https://www.youtube.com/watch?v=14z6nlqcCpQ https://youtu.be/14z6nlqcCpQ "
                "https://m.youtube.com/watch?feature=share&amp;v=fPA28AfG-JY https://music.youtube.com/watch?v=R2-yomhYAj4")
        self.assertEqual(videos_in(text), yt("14z6nlqcCpQ", "fPA28AfG-JY", "R2-yomhYAj4"))

    def test_shorts_and_others_are_not_videos(self):
        self.assertEqual(videos_in("https://youtube.com/shorts/14z6nlqcCpQ https://v.redd.it/eexv933zksuh1"), [])

    def test_kinds(self):
        self.assertEqual([kind_of(url, []) for url in ("https://i.redd.it/a.jpeg", "https://www.reddit.com/gallery/x",
                                                       "https://example.com/a.png", "https://soundcloud.com/x",
                                                       "https://v.redd.it/x", None)],
                         ["image", "gallery", "image", "other", "reddit_video", "text"])
        self.assertEqual(kind_of("https://youtu.be/x", yt("fLCd0tXSLZU")), "video")


class RedditSource(unittest.TestCase):
    def test_atom(self):
        posts = parse_atom(ATOM)
        self.assertEqual([(p.id, p.author, p.videos, kind_of(p.link, p.videos)) for p in posts],
                         [("abc", "someone", yt("fLCd0tXSLZU"), "video"), ("def", None, [], "reddit_video"),
                          ("ghi", None, [], "text")])

    def test_pullpush(self):
        posts = parse_pullpush({"data": [{"id": "abc", "title": "A &amp; B", "url": "https://youtu.be/fLCd0tXSLZU",
                                          "created_utc": 1792000000, "permalink": "/r/X/comments/abc/t/"},
                                         "junk", {"id": "no-date"}]})
        self.assertEqual([(p.title, p.videos, p.url) for p in posts],
                         [("A & B", yt("fLCd0tXSLZU"), "https://www.reddit.com/r/X/comments/abc/t/")])

    def test_bad_answers_say_so(self):
        with self.assertRaises(BadAnswer):
            parse_atom("<html>Blocked</html")
        with self.assertRaises(BadAnswer):
            parse_pullpush(["not", "a", "dict"])

    def test_targets(self):
        reddit = Reddit(None, "test")
        for raw in ("SexyMusicVideos", "r/SexyMusicVideos", "/r/SexyMusicVideos/",
                    "https://www.reddit.com/r/SexyMusicVideos/"):
            self.assertEqual(reddit.normalize(raw), "SexyMusicVideos")
        for raw in ("a", "no spaces", "../etc", "x" * 22):
            with self.assertRaises(Invalid):
                reddit.normalize(raw)


class FakeSource(Reddit):
    """Reddit as subfeed sees it (its targets, names and pages), without the network."""

    page_size = 2

    def __init__(self):
        self.newest_posts: list[Post] = []
        self.pages: list[list[Post]] = []
        self.fail: Exception | None = None
        self.refuse = False

    async def newest(self, target):
        if self.fail:
            raise self.fail
        if self.refuse:
            raise Refused("www.reddit.com answered 429", retry_after=7200)
        return self.newest_posts

    async def history(self, target, before=None):
        if self.fail:
            raise self.fail
        return self.pages.pop(0) if self.pages else []


def post(post_id, posted_at, *videos, link=None):
    return Post(id=post_id, title=post_id, url=f"https://www.reddit.com/{post_id}", author=None,
                posted_at=posted_at, videos=yt(*videos),
                link=link or (f"https://youtu.be/{videos[0]}" if videos else None))


class Harness(unittest.TestCase):
    """A subfeed of its own: a temporary database, a fake Reddit, the events it emits."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.config = config_module.load({"SUBFEED_STATE": self.dir.name, "SUBFEED_STATIC": self.dir.name,
                                          "SUBFEED_PUBLIC_URL": "http://subfeed.lan"})
        self.source = FakeSource()
        self.ctx = build(self.config, sources={"reddit": self.source})
        self.events = []
        self.ctx.poller._emit = self.events.append
        self.ctx.settings.update(SettingsPatch(backfill_posts=4))

    def tearDown(self):
        self.ctx.db.close()
        self.dir.cleanup()

    def make(self, target="SexyMusicVideos", **fields):
        return self.ctx.feeds.create(FeedIn(target=target, **fields), 30)

    def poll(self, slug="sexymusicvideos"):
        asyncio.run(self.ctx.poller.poll(self.ctx.feeds.row(slug)))
        return self.ctx.feeds.public(self.ctx.feeds.row(slug), self.ctx.posts.counts())

    def ids(self, **query):
        posts, total = self.ctx.posts.query(PostQuery(**query))
        self.assertEqual(total, len(posts))
        return [p["post_id"] for p in posts]


class Polling(Harness):
    def test_everything_unless_filtered(self):
        self.make()
        self.source.pages = [[post("old", 100, "aaaaaaaaaaa"), post("clip", 60, link="https://v.redd.it/x")],
                             [post("talk", 50)]]
        self.source.newest_posts = [post("new", 200, "ccccccccccc"), post("repost", 300, "aaaaaaaaaaa")]
        feed = self.poll()
        self.assertTrue(feed["last"]["ok"])
        self.assertEqual((feed["posts"], feed["videos"]), (5, 3))
        self.assertEqual(self.ids(), ["repost", "new", "old", "clip", "talk"])
        self.assertEqual(self.ids(kinds=["reddit_video", "text"]), ["clip", "talk"])
        self.assertEqual(self.ids(domains=["v.redd.it"]), ["clip"])
        self.assertEqual(self.ids(providers=["youtube"]), ["repost", "new", "old"])
        # One post a video: the first that carried it.
        self.assertEqual(self.ids(providers=["youtube"], unique=True), ["new", "old"])
        self.assertEqual(self.ids(words="talk"), ["talk"])
        self.assertEqual(self.ids(since=100, until=300), ["new", "old"])
        # Read again: nothing new, nothing doubled.
        self.assertEqual(self.poll()["posts"], 5)

    def test_the_archive_stands_in_when_reddit_refuses(self):
        self.make()
        self.source.refuse = True
        self.source.pages = [[], [post("p", 100, "ddddddddddd")]]
        feed = self.poll()
        self.assertTrue(feed["last"]["ok"])
        self.assertIn("Reddit refused", feed["last"]["message"])
        self.assertEqual(feed["posts"], 1)
        # Not before Reddit said to come back (2 h), whatever poll_minutes says.
        self.assertGreater(feed["next_at"] - feed["last"]["at"], 0.9 * 7200)

    def test_failing_is_told_once_then_recovered(self):
        self.make()
        self.source.fail = Unreachable("www.reddit.com didn't answer in time")
        for _ in range(4):
            feed = self.poll()
        self.assertFalse(feed["last"]["ok"])
        self.assertEqual(feed["failures"], 4)
        self.assertIn("didn't answer in time", feed["last"]["message"])
        self.assertEqual([e.name for e in self.events], ["feed.failing"])
        self.assertEqual(self.events[0].url, "http://subfeed.lan/feeds/sexymusicvideos")
        # Each failure waits longer: 30 min, 1 h, 2 h… up to 6 h.
        self.assertGreater(feed["next_at"] - feed["last"]["at"], 4 * 3600)
        self.source.fail = None
        feed = self.poll()
        self.assertEqual((feed["failures"], [e.name for e in self.events]), (0, ["feed.failing", "feed.recovered"]))

    def test_new_posts_are_told_after_the_history(self):
        self.make()
        self.source.newest_posts = [post("a", 100)]
        self.poll()
        self.assertEqual(self.events, [])
        self.source.newest_posts = [post("b", 200, "bbbbbbbbbbb"), post("a", 100)]
        self.poll()
        self.assertEqual([(e.name, [p["post_id"] for p in e.data["posts"]]) for e in self.events],
                         [("posts.new", ["b"])])

    def test_another_community_starts_afresh(self):
        self.make()
        self.source.newest_posts = [post("a", 100)]
        self.poll()
        from subfeed.feeds import FeedPatch

        feed = self.ctx.feeds.update("sexymusicvideos", FeedPatch(target="r/Other"))
        self.assertEqual((feed["target"], feed["backfilled"], self.ctx.posts.total()), ("Other", 0, 0))

    def test_old_posts_are_forgotten(self):
        import time

        self.make()
        self.source.newest_posts = [post("old", time.time() - 400 * 86400), post("new", time.time())]
        self.poll()
        # Kept by default.
        self.ctx.poller._forget()
        self.assertEqual(self.ids(), ["new", "old"])
        self.ctx.settings.update(SettingsPatch(keep_days=365))
        self.ctx.poller._forget()
        self.assertEqual(self.ids(), ["new"])


class Formats(Harness):
    def setUp(self):
        super().setUp()
        self.make()
        self.source.newest_posts = [Post(id="abc", title="Ellize - Mystika <3", url="https://www.reddit.com/abc",
                                         author="someone", posted_at=1792000000.0, videos=yt("fLCd0tXSLZU"),
                                         link="https://youtu.be/fLCd0tXSLZU"),
                                    post("talk", 1791000000.0)]
        self.poll()
        self.feed = self.ctx.feeds.public(self.ctx.feeds.row("sexymusicvideos"))
        self.posts, _ = self.ctx.posts.query(PostQuery())

    def test_json_feed(self):
        body = formats.json_feed(self.feed, self.posts, feed_url="http://x/feed.json")
        self.assertEqual(body["version"], "https://jsonfeed.org/version/1.1")
        first = body["items"][0]
        self.assertEqual((first["id"], first["external_url"], first["authors"], first["date_published"]),
                         ("subfeed:sexymusicvideos:abc", "https://youtu.be/fLCd0tXSLZU", [{"name": "someone"}],
                          "2026-10-14T17:46:40+00:00"))
        self.assertEqual(first["_subfeed"]["videos"],
                         [{"provider": "youtube", "id": "fLCd0tXSLZU",
                           "url": "https://www.youtube.com/watch?v=fLCd0tXSLZU"}])
        self.assertNotIn("external_url", body["items"][1])

    def test_atom_is_well_formed(self):
        root = ET.fromstring(formats.atom(self.feed, self.posts, self_url="http://x/feed.atom?a=1&b=2"))
        ns = "{http://www.w3.org/2005/Atom}"
        entries = root.findall(f"{ns}entry")
        self.assertEqual(entries[0].find(f"{ns}title").text, "Ellize - Mystika <3")
        self.assertEqual(entries[0].find(f"{ns}link").get("href"), "https://www.youtube.com/watch?v=fLCd0tXSLZU")
        self.assertEqual(root.find(f"{ns}link").get("href"), "http://x/feed.atom?a=1&b=2")


class Api(Harness):
    def setUp(self):
        super().setUp()
        self.client = TestClient(create_app(self.ctx))

    def test_feeds_and_errors(self):
        made = self.client.post("/api/v1/feeds", json={"target": "https://www.reddit.com/r/SexyMusicVideos/"})
        self.assertEqual(made.status_code, 201)
        self.assertEqual((made.json()["slug"], made.json()["title"], made.json()["poll_minutes"]),
                         ("sexymusicvideos", "r/SexyMusicVideos", 30))
        again = self.client.post("/api/v1/feeds", json={"target": "SexyMusicVideos"})
        self.assertEqual(again.status_code, 409)
        self.assertIn("already", again.json()["detail"])
        bad = self.client.post("/api/v1/feeds", json={"target": "no spaces here"})
        self.assertEqual((bad.status_code, bad.json()["detail"][:20]), (422, "That isn't a subredd"))
        extra = self.client.post("/api/v1/feeds", json={"target": "Abc", "verdict": "x"})
        self.assertEqual(extra.json()["detail"], "Verdict isn't something subfeed knows.")
        self.assertEqual(self.client.get("/api/v1/feeds/nope").status_code, 404)
        off = self.client.patch("/api/v1/feeds/sexymusicvideos", json={"enabled": False})
        self.assertFalse(off.json()["enabled"])
        self.assertEqual(self.client.get("/api/v1/nothing").json()["detail"][:9], "Not found")
        self.assertEqual(self.client.delete("/api/v1/feeds/sexymusicvideos").status_code, 204)

    def test_reads_answer_304_until_the_feed_changes(self):
        self.make()
        self.source.newest_posts = [post("a", 100, "aaaaaaaaaaa")]
        self.poll()
        url = "/api/v1/feeds/sexymusicvideos/feed.json?video=youtube"
        first = self.client.get(url)
        self.assertEqual((first.status_code, first.headers["content-type"]), (200, "application/feed+json"))
        self.assertEqual(first.json()["items"][0]["_subfeed"]["videos"][0]["id"], "aaaaaaaaaaa")
        tag = first.headers["etag"]
        self.assertEqual(self.client.get(url, headers={"If-None-Match": tag}).status_code, 304)
        self.source.newest_posts = [post("b", 200, "bbbbbbbbbbb")]
        self.ctx.feeds._db.run("UPDATE feeds SET changed_at = changed_at - 1")
        self.poll()
        self.assertEqual(self.client.get(url, headers={"If-None-Match": tag}).status_code, 200)
        page = self.client.get("/api/v1/posts?limit=1").json()
        self.assertEqual((page["total"], len(page["posts"])), (2, 1))
        atom = self.client.get("/api/v1/feeds/sexymusicvideos/feed.atom")
        self.assertEqual(atom.headers["content-type"], "application/atom+xml")

    def test_settings(self):
        self.assertEqual(self.client.patch("/api/v1/settings", json={"keep_days": 30}).json()["keep_days"], 30)
        bad = self.client.patch("/api/v1/settings", json={"public_url": "ftp://x"})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.client.get("/api/v1/settings").json()["keep_days"], 30)

    def test_notifications_keep_their_secrets(self):
        types = self.client.get("/api/v1/notifications/types").json()
        self.assertEqual({t["type"] for t in types["types"]}, {"apprise", "discord", "webhook"})
        made = self.client.post("/api/v1/notifications", json={
            "name": "Hook", "type": "webhook", "config": {"url": "http://app:8080/in", "token": "s3cret"}}).json()
        self.assertEqual((made["config"], made["secrets_set"], made["events"]),
                         ({}, ["token", "url"], ["feed.failing", "feed.recovered"]))
        kept = self.client.put(f"/api/v1/notifications/{made['id']}", json={
            "name": "Hook", "type": "webhook", "events": ["posts.new"], "config": {"url": "", "token": ""}}).json()
        self.assertEqual((kept["secrets_set"], kept["events"]), (["token", "url"], ["posts.new"]))
        self.assertEqual(self.ctx.notifications.find(made["id"])["config"]["token"], "s3cret")

    def test_health_status_and_the_contract(self):
        self.assertEqual(self.client.get("/api/v1/health").json()["ok"], True)
        self.assertEqual(self.client.get("/api/v1/status").json()["feeds"]["total"], 0)
        spec = self.client.get("/api/v1/openapi.json").json()
        self.assertIn("/api/v1/feeds/{slug}/feed.json", spec["paths"])
        page = self.client.get("/feeds/x")
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])


class Migration(unittest.TestCase):
    def test_clipfeeds_database_is_taken_as_it_is(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy = sqlite3.connect(Path(folder) / "clipfeed.db")
            legacy.executescript("""
                CREATE TABLE feeds (slug TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                    target TEXT NOT NULL, enabled INTEGER NOT NULL, poll_minutes INTEGER NOT NULL,
                    created_at REAL NOT NULL, backfilled INTEGER NOT NULL DEFAULT 0, last_at REAL, last_ok INTEGER,
                    last_message TEXT, next_at REAL NOT NULL DEFAULT 0);
                CREATE TABLE entries (feed TEXT NOT NULL, post_id TEXT NOT NULL, title TEXT NOT NULL,
                    url TEXT NOT NULL, author TEXT, posted_at REAL NOT NULL, link TEXT, kind TEXT NOT NULL,
                    domain TEXT, youtube_id TEXT, youtube_ids TEXT NOT NULL DEFAULT '[]', found_at REAL NOT NULL,
                    PRIMARY KEY (feed, post_id));
                CREATE TABLE videos (youtube_id TEXT PRIMARY KEY, verdict TEXT NOT NULL, checked_at REAL NOT NULL);
                INSERT INTO feeds VALUES ('smv', 'r/SMV', 'reddit', 'SexyMusicVideos', 1, 30, 1, 1, NULL, NULL,
                                          NULL, 0);
                INSERT INTO entries VALUES ('smv', 'a', 'A', 'https://r/a', 'x', 100, 'https://youtu.be/aaaaaaaaaaa',
                                            'youtube', 'youtu.be', 'aaaaaaaaaaa', '["aaaaaaaaaaa","bbbbbbbbbbb"]', 1);
                INSERT INTO entries VALUES ('smv', 'b', 'B', 'https://r/b', NULL, 90, NULL, 'text', NULL, NULL, '[]', 1);
                INSERT INTO videos VALUES ('aaaaaaaaaaa', 'official', 1);
            """)
            legacy.close()
            db = Database(Path(folder) / "subfeed.db")
            tables = {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type = 'table'")}
            rows = db.all("SELECT post_id, kind, videos, first_video FROM posts ORDER BY post_id")
            version = db.one("PRAGMA user_version")[0]
            db.close()
            self.assertFalse((Path(folder) / "clipfeed.db").exists())
            # Opened again: nothing runs twice.
            Database(Path(folder) / "subfeed.db").close()
        self.assertEqual((version, "entries" in tables, "videos" in tables), (1, False, False))
        self.assertEqual([(r["post_id"], r["kind"], json.loads(r["videos"]), r["first_video"]) for r in rows],
                         [("a", "video", [{"provider": "youtube", "id": "aaaaaaaaaaa"},
                                          {"provider": "youtube", "id": "bbbbbbbbbbb"}], "youtube:aaaaaaaaaaa"),
                          ("b", "text", [], None)])


class Config(unittest.TestCase):
    def test_seed_and_urls(self):
        conf = config_module.load({"SUBFEED_FEEDS": "smv=reddit:SexyMusicVideos, x=reddit:Other",
                                   "SUBFEED_PUBLIC_URL": "http://subfeed.lan/"})
        self.assertEqual([(s.slug, s.target) for s in conf.seed], [("smv", "SexyMusicVideos"), ("x", "Other")])
        self.assertEqual(conf.public_url, "http://subfeed.lan")
        with self.assertRaises(SystemExit):
            config_module.load({"SUBFEED_FEEDS": "smv=SexyMusicVideos"})
        with self.assertRaises(SystemExit):
            config_module.load({"SUBFEED_PUBLIC_URL": "subfeed.lan"})


if __name__ == "__main__":
    unittest.main()
