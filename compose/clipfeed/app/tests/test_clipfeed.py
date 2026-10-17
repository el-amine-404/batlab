"""python -m unittest discover -s tests (from compose/clipfeed/app, with
lib/batlab-common on PYTHONPATH; see docs/music.md)."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipfeed import verify  # noqa: E402
from clipfeed.db import Database  # noqa: E402
from clipfeed.feeds import FeedIn, Feeds  # noqa: E402
from clipfeed.sources import Post, Refused, parse_atom, parse_pullpush, youtube_ids  # noqa: E402

ATOM = """<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><author><name>/u/someone</name></author><content type="html">&lt;a href=&quot;https://youtu.be/fLCd0tXSLZU?si=x&quot;&gt;[link]&lt;/a&gt;</content>
<id>t3_abc</id><link href="https://www.reddit.com/r/X/comments/abc/t/"/><published>2026-10-17T07:00:00+00:00</published>
<title>Ellize - Mystika - Official Music Video</title></entry>
<entry><content type="html">&lt;a href=&quot;https://v.redd.it/x&quot;&gt;[link]&lt;/a&gt;</content><id>t3_def</id>
<link href="https://www.reddit.com/r/X/comments/def/t/"/><published>2026-10-17T06:00:00+00:00</published><title>M</title></entry>
</feed>"""


class Links(unittest.TestCase):
    def test_every_form_once(self):
        text = ("https://www.youtube.com/watch?v=14z6nlqcCpQ https://youtu.be/14z6nlqcCpQ "
                "https://m.youtube.com/watch?feature=share&amp;v=fPA28AfG-JY https://music.youtube.com/watch?v=R2-yomhYAj4")
        self.assertEqual(youtube_ids(text), ["14z6nlqcCpQ", "fPA28AfG-JY", "R2-yomhYAj4"])

    def test_shorts_and_others_are_not_videos(self):
        self.assertEqual(youtube_ids("https://youtube.com/shorts/14z6nlqcCpQ https://v.redd.it/eexv933zksuh1"), [])


class Sources(unittest.TestCase):
    def test_reddit_atom(self):
        posts = parse_atom(ATOM)
        self.assertEqual([(p.id, p.author, p.videos) for p in posts],
                         [("abc", "someone", ["fLCd0tXSLZU"]), ("def", None, [])])

    def test_pullpush(self):
        posts = parse_pullpush({"data": [{"id": "abc", "title": "A &amp; B", "url": "https://youtu.be/fLCd0tXSLZU",
                                          "created_utc": 1792000000, "permalink": "/r/X/comments/abc/t/"}]})
        self.assertEqual((posts[0].title, posts[0].videos, posts[0].url),
                         ("A & B", ["fLCd0tXSLZU"], "https://www.reddit.com/r/X/comments/abc/t/"))


class Titles(unittest.TestCase):
    def test_uploads(self):
        self.assertEqual(verify.song_of("Ellize - Mystika - Official Music Video", "Utopia Records"), ("Mystika", "Ellize"))
        self.assertEqual(verify.song_of('Madeline Monet - "PQTG" Official Music Video', "x"), ("PQTG", "Madeline Monet"))
        self.assertEqual(verify.song_of("Frozen (Official Video)", "Suzy Clue VEVO"), ("Frozen", "Suzy Clue"))

    def test_the_artists_own_channel(self):
        self.assertEqual(verify.song_of("Tate McRae - Housewife (Official Video)", "Tate McRae", trusted=True),
                         ("Housewife", "Tate McRae"))
        self.assertEqual(verify.song_of("Love - Live at Wembley", "Some Band", trusted=True),
                         ("Love - Live at Wembley", "Some Band"))

    def test_claims(self):
        self.assertTrue(verify.claims_official("Orel - SABRA (video oficial)"))
        self.assertFalse(verify.claims_official("Lulu Liz Cohen - Crush (Cover)"))
        self.assertFalse(verify.claims_official("X - Song (Official Lyric Video)"))
        self.assertFalse(verify.claims_official("Megan Thee Stallion - Tina Tuesday Freestyle"))


class FakeReddit:
    def __init__(self):
        self.refuse = False
        self.newest_posts: list[Post] = []
        self.pages: list[list[Post]] = []

    async def newest(self, subreddit):
        if self.refuse:
            raise Refused("www.reddit.com answered 429")
        return self.newest_posts

    async def archive(self, subreddit, before=None):
        return self.pages.pop(0) if self.pages else []


def _post(post_id, posted_at, *videos):
    return Post(id=post_id, title=post_id, url=f"https://www.reddit.com/{post_id}", author=None,
                posted_at=posted_at, videos=list(videos))


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.dir.name) / "t.db")
        self.reddit = FakeReddit()
        self.feeds = Feeds(self.db, self.reddit)
        self.feeds.create(FeedIn(title="Sexy music videos", target="r/SexyMusicVideos"))

    def tearDown(self):
        self.db.close()
        self.dir.cleanup()

    def poll(self):
        asyncio.run(self.feeds.poll(self.db.one("SELECT * FROM feeds")))

    def test_history_then_news_one_row_a_video(self):
        self.reddit.pages = [[_post("old", 100, "aaaaaaaaaaa"), _post("older", 50, "bbbbbbbbbbb")]]
        self.reddit.newest_posts = [_post("new", 200, "ccccccccccc"), _post("repost", 300, "aaaaaaaaaaa")]
        self.poll()
        items, total = self.feeds.items(["sexy-music-videos"], [], "posted", 10, 0)
        self.assertEqual(total, 3)
        # The first post that linked a video is the one kept.
        self.assertEqual([(i["youtube_id"], i["post"]["id"]) for i in items],
                         [("ccccccccccc", "new"), ("aaaaaaaaaaa", "old"), ("bbbbbbbbbbb", "older")])
        self.assertEqual(self.feeds.get("sexy-music-videos")["target"], "SexyMusicVideos")

    def test_pullpush_stands_in_when_reddit_refuses(self):
        self.reddit.refuse = True
        self.reddit.pages = [[], [_post("p", 100, "ddddddddddd")]]
        self.poll()
        feed = self.feeds.get("sexy-music-videos")
        self.assertTrue(feed["last"]["ok"])
        self.assertIn("Reddit refused", feed["last"]["message"])
        self.assertEqual(feed["items"], 1)

    def test_verdicts_and_filters(self):
        self.reddit.newest_posts = [_post("a", 200, "aaaaaaaaaaa"), _post("b", 100, "bbbbbbbbbbb")]
        self.poll()
        answers = {"aaaaaaaaaaa": verify.Verdict("official", "music_video", "Song", "Artist", 200, "2026-10-01"),
                   "bbbbbbbbbbb": verify.Verdict("unofficial", "upload", "Cover", "Fan")}
        with mock.patch.object(verify, "check", side_effect=lambda youtube_id: answers[youtube_id]):
            self.assertEqual(asyncio.run(self.feeds.check_videos()), 2)
            # Checked under today's rules: nothing to do.
            self.assertEqual(asyncio.run(self.feeds.check_videos()), 0)
        items, total = self.feeds.items([], ["official"], "published", 10, 0)
        self.assertEqual((total, items[0]["artist"], items[0]["published"]), (1, "Artist", "2026-10-01"))


if __name__ == "__main__":
    unittest.main()
