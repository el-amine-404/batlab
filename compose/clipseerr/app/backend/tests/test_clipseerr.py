"""python -m unittest discover -s tests (in the image, see docs/music.md)."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalog import match  # noqa: E402
from app.db import Database  # noqa: E402
from app.integrations import Integrations, Invalid  # noqa: E402
from app.services import TYPES, Update  # noqa: E402
from app.services.aurral import from_activity  # noqa: E402
from app.tracker.engine import Engine  # noqa: E402
from app.tracker.store import Requests  # noqa: E402


class Titles(unittest.TestCase):
    def test_upload_words_go(self):
        self.assertEqual(match.clean("Levitating (Official Music Video)", "Dua Lipa"), ("Levitating", None))
        self.assertEqual(match.clean("PIRATE [4K] | Stormy", "Stormy"), ("PIRATE", None))

    def test_artist_in_title_is_the_credit(self):
        self.assertEqual(match.clean("Bulin 47 X Kevin Wai - Dame Hombro (Video Oficial)", "Bulin 47 Oficial"),
                         ("Dame Hombro", "Bulin 47 X Kevin Wai"))

    def test_a_dash_inside_a_title_stays(self):
        self.assertEqual(match.clean("Love - Live at Wembley", "Some Band")[0], "Love - Live at Wembley")

    def test_channel_suffixes(self):
        self.assertEqual(match.channel_artist("DuaLipaVEVO"), "DuaLipa")
        self.assertEqual(match.channel_artist("Bulin 47 Oficial"), "Bulin 47")
        self.assertEqual(match.channel_artist("Stormy - Topic"), "Stormy")


class YouTubeLists(unittest.TestCase):
    def test_a_length_read_as_the_artist(self):
        from app.catalog import youtube

        video = youtube.to_video({"videoId": "1Iprh25owE8", "title": "Night Paradox",
                                  "artists": [{"name": "3 min 42 sec", "id": None}], "views": "Vaelissed"})
        self.assertEqual((video.artist, video.duration, video.views), ("Vaelissed", 222, None))


def _song(title, artist, seconds, video_id="x", kind="song"):
    return {"resultType": kind, "videoId": video_id, "title": title, "artists": [{"name": artist}],
            "duration_seconds": seconds}


class Matching(unittest.TestCase):
    def test_the_original_over_a_version(self):
        results = [_song("Patient Zero (Piano Version)", "Taylor Swift", 227, "piano"),
                   _song("Patient Zero", "Taylor Swift", 226, "original"),
                   _song("Patient Zero (Acoustic Version)", "Taylor Swift", 236, "acoustic")]
        self.assertEqual(match.best_audio("Patient Zero", "Taylor Swift", 312, results)["videoId"], "original")

    def test_another_artist_is_never_it(self):
        results = [_song("Patient Zero", "Luna Rae", 226)]
        self.assertIsNone(match.best_audio("Patient Zero", "Taylor Swift", 230, results))

    def test_a_short_film_holds_its_song(self):
        results = [_song("Thriller", "Michael Jackson", 357, "song")]
        self.assertEqual(match.best_audio("Thriller", "Michael Jackson", 822, results)["videoId"], "song")

    def test_a_song_longer_than_its_video_is_another(self):
        results = [_song("Intro", "Someone", 400)]
        self.assertIsNone(match.best_audio("Intro", "Someone", 120, results))

    def test_the_official_video_first(self):
        results = [{"videoId": "fan", "title": "Levitating", "artists": [{"name": "Dua Lipa"}],
                    "duration_seconds": 230, "videoType": "MUSIC_VIDEO_TYPE_UGC"},
                   {"videoId": "omv", "title": "Levitating (Official Video)", "artists": [{"name": "Dua Lipa"}],
                    "duration_seconds": 231, "videoType": "MUSIC_VIDEO_TYPE_OMV"}]
        self.assertEqual(match.best_video("Levitating", "Dua Lipa", 229, results)["videoId"], "omv")


def _video(title, source="youtube", released=None, explicit=False, youtube_id=None):
    from app.catalog.models import ArtistRef, Video

    return Video(id=f"{source[:2]}:{title}", source=source, title=title, artist="Shakira",
                 artists=[ArtistRef(name="Shakira")], released=released, explicit=explicit,
                 youtube_id=youtube_id)


class ArtistDates(unittest.TestCase):
    def test_apple_dates_the_same_take_only(self):
        from app.catalog.service import _with_apple

        youtube = [_video("Hips Don't Lie (feat. Wyclef Jean)"), _video("The Making of 'Dai Dai'"),
                   _video("Agua (Live from LMYNL World Tour)")]
        apple = [_video("Hips Don't Lie", "apple", "2006-02-01"), _video("Dai Dai", "apple", "2026-05-23"),
                 _video("AGUA", "apple", "2026-09-17"), _video("Hips Don't Lie", "apple", "2025-11-21")]
        merged = _with_apple(youtube, apple)
        self.assertEqual([v.released for v in merged[:3]], ["2006-02-01", None, None])
        self.assertEqual([v.title for v in merged[3:]], ["Dai Dai", "AGUA"])


class Versions(unittest.TestCase):
    def test_labels(self):
        from app.catalog.service import _label

        self.assertEqual(_label("Gnarly (Clean Edit)", False), "Clean")
        self.assertEqual(_label("Gnarly", True), "Explicit")
        self.assertEqual(_label("Gnarly (Ice Spice Remix)", True), "Remix")
        self.assertEqual(_label("Gnarly (Dance Practice)", False), "Dance Practice")
        self.assertEqual(_label("Gnarly", False), "Original")

    def test_same_song_same_artist_only(self):
        from app.catalog.models import VideoDetail
        from app.catalog.service import _versions

        detail = VideoDetail(id="yt:mv", source="youtube", youtube_id="mv", title="Gnarly (Clean Edit)",
                             artist="KATSEYE", artists=[])
        results = [_song("Gnarly", "KATSEYE", 138, "explicit"), _song("Gnarly (Clean Edit)", "KATSEYE", 138, "clean"),
                   _song("Gnarly", "Someone Else", 140, "cover"), _song("Gnarly Night", "KATSEYE", 140, "other")]
        results[0]["isExplicit"] = True
        found = _versions(results, "song", "Gnarly", detail)
        self.assertEqual([(v.youtube_id, v.label) for v in found], [("explicit", "Explicit"), ("clean", "Clean")])


class Pages(unittest.TestCase):
    def test_pages_count_what_is_shown(self):
        from app.catalog.models import Shelf

        # The routes module opens clipseerr's database: a throwaway one.
        os.environ.setdefault("CLIPSEERR_STATE", tempfile.mkdtemp())
        import importlib

        routes = importlib.import_module("app.routes.catalog")

        shelf = Shelf(id="s", title="S", source="youtube", items=[_video(f"v{i}", explicit=i % 2 == 0)
                                                                    for i in range(10)], total=10)
        with mock.patch.object(routes, "settings", lambda: {"hide_explicit": True}):
            page = asyncio.run(routes._page(shelf, limit=3, offset=3))
        self.assertEqual((page.total, [v.title for v in page.items]), (5, ["v7", "v9"]))


class Sorting(unittest.TestCase):
    def test_a_list_sorts_whole_before_its_pages(self):
        os.environ.setdefault("CLIPSEERR_STATE", tempfile.mkdtemp())
        import importlib

        routes = importlib.import_module("app.routes.catalog")
        videos = [_video("b"), _video("C"), _video("a")]
        for video, views in zip(videos, ("2.1K", "1.2M", None)):
            video.views = views
        self.assertEqual([v.title for v in asyncio.run(routes._sorted(videos, "title"))], ["a", "b", "C"])
        # Most viewed first; a count YouTube didn't give, last.
        self.assertEqual([v.title for v in asyncio.run(routes._sorted(videos, "views"))], ["C", "b", "a"])
        self.assertEqual([v.title for v in asyncio.run(routes._sorted(videos, "default"))], ["b", "C", "a"])


class Previews(unittest.TestCase):
    def test_clip_starts_a_third_in_without_running_short(self):
        from app.catalog import preview

        self.assertEqual(preview.start_of(None), 0.0)
        self.assertEqual(preview.start_of(18), 0.0)
        self.assertEqual(preview.start_of(180), 60.0)
        self.assertEqual(preview.start_of(30), 10.0)
        self.assertEqual(preview.start_of(400), 133.3)

    def test_ffmpeg_complaint_without_addresses(self):
        from app.catalog import preview

        said = b"[https @ 0x1] HTTP error 403 Forbidden\nhttps://rr1.googlevideo.com/videoplayback?x=1: Server returned 403\n"
        self.assertEqual(preview._quiet(said), "[https @ 0x1] HTTP error 403 Forbidden / <stream> Server returned 403")

    def test_least_recently_played_clips_pruned_first(self):
        from app.catalog import preview

        folder = Path(tempfile.mkdtemp())
        for i, name in enumerate(("old", "mid", "new")):
            clip = folder / f"{name}.mp4"
            clip.write_bytes(b"x" * 100)
            os.utime(clip, (1000 + i, 1000 + i))
        preview.prune(folder, 250)
        self.assertEqual(sorted(p.stem for p in folder.glob("*.mp4")), ["mid", "new"])

    def test_failure_not_retried_at_once(self):
        from app.catalog import preview

        clips = preview.Clips(Path(tempfile.mkdtemp()))
        calls = []

        def failing(youtube_id, out):
            calls.append(youtube_id)
            raise RuntimeError("refused")

        original = preview.cut
        preview.cut = failing
        try:
            for _ in range(2):
                with self.assertRaises(LookupError):
                    asyncio.run(clips.get("aaaaaaaaaaa"))
        finally:
            preview.cut = original
        self.assertEqual(calls, ["aaaaaaaaaaa"])


class PreviewRelay(unittest.TestCase):
    """ffmpeg's open reads, asked of a YouTube that refuses more than a piece at once."""

    def test_reads_served_whole_from_pieces(self):
        import http.server
        import re
        import threading
        import urllib.request

        from app.catalog import preview

        data = bytes(range(256)) * 12_000  # about 3 MB
        asked = []

        class YouTube(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                start, end = map(int, re.match(r"bytes=(\d+)-(\d+)", self.headers["Range"]).groups())
                asked.append(end - start + 1)
                if end - start + 1 > preview.PIECE:
                    self.send_error(403)
                    return
                end = min(end, len(data) - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                self.wfile.write(data[start:end + 1])

        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), YouTube)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        name = "test-stream"
        preview.STREAMS[name] = (f"http://127.0.0.1:{upstream.server_address[1]}/v", {})
        relay = f"http://127.0.0.1:{preview._relay_port()}/{name}"
        try:
            for rng, want in ((None, data), ("bytes=1500000-", data[1500000:]),
                              ("bytes=10-2500000", data[10:2500001])):
                headers = {"Range": rng} if rng else {}
                got = urllib.request.urlopen(urllib.request.Request(relay, headers=headers), timeout=10).read()
                self.assertEqual(got, want, rng)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(relay.replace(name, "unknown"), timeout=10)
        finally:
            preview.STREAMS.pop(name, None)
            upstream.shutdown()
        self.assertLessEqual(max(asked), preview.PIECE)


class PreviewBudget(unittest.TestCase):
    def setUp(self):
        from app.catalog import preview

        self.preview = preview
        self.clips = preview.Clips(Path(tempfile.mkdtemp()))
        self.original = preview.cut

    def tearDown(self):
        self.preview.cut = self.original

    def test_new_clips_stop_at_the_budget_but_cut_ones_are_served(self):
        def ok(youtube_id, out):
            out.write_bytes(b"clip")

        self.preview.cut = ok
        self.clips.BUDGET = 2
        for vid in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
            asyncio.run(self.clips.get(vid))
        with self.assertRaises(self.preview.Busy):
            asyncio.run(self.clips.get("ccccccccccc"))
        self.assertTrue(asyncio.run(self.clips.get("aaaaaaaaaaa")).exists())

    def test_bot_check_pauses_every_cut(self):
        calls = []

        def refused(youtube_id, out):
            calls.append(youtube_id)
            raise RuntimeError("ERROR: [youtube] x: Sign in to confirm you\u2019re not a bot.")

        self.preview.cut = refused
        for vid in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
            with self.assertRaises(self.preview.Busy):
                asyncio.run(self.clips.get(vid))
        self.assertEqual(calls, ["aaaaaaaaaaa"])


class HotFeeds(unittest.TestCase):
    def test_a_clipfeed_feed_gives_its_real_songs(self):
        import httpx

        from app.cache import Cache
        from app.catalog.service import Catalog

        body = {"items": [
            {"title": "Ellize - Mystika - Official Music Video",
             "video": {"youtube_id": "fLCd0tXSLZU", "verdict": "likely", "title": "Mystika", "artist": "Ellize",
                       "published": "2026-10-16", "duration": 180}},
            {"title": "Crush", "video": {"youtube_id": "bbbbbbbbbbb", "verdict": "unofficial", "title": "Crush (Cover)",
                                         "artist": "Fan"}},
            {"title": "A clip", "video": None},
            {"title": "x", "video": {"youtube_id": "not-an-id", "verdict": "official"}}]}
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "t.db")

            async def run():
                async with httpx.AsyncClient(transport=transport) as http:
                    return await Catalog(Cache(db), http, lambda: {}).\
                        _feed("http://clipfeed:8830/api/v1/feeds/x/items")

            found = asyncio.run(run())
            db.close()
        self.assertEqual([(v["id"], v["title"], v["artist"], v["released"]) for v in found],
                         [("yt:fLCd0tXSLZU", "Mystika", "Ellize", "2026-10-16")])


class Views(unittest.TestCase):
    def test_views_are_looked_up_once_and_filled_in(self):
        import httpx

        from app.cache import Cache
        from app.catalog import youtube
        from app.catalog.service import Catalog

        asked = []

        async def song(region, youtube_id):
            asked.append(youtube_id)
            if youtube_id == "broken00000":
                raise RuntimeError("no answer")
            return {"views": 1_234_567, "published": "2026-10-01"}

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(youtube, "song", song):
            db = Database(Path(folder) / "t.db")

            async def run():
                async with httpx.AsyncClient() as http:
                    catalog = Catalog(Cache(db), http, lambda: {"region": "US"})
                    first = await catalog.views(["aaaaaaaaaaa", "broken00000"])
                    again = await catalog.views(["aaaaaaaaaaa"])
                    listed = [_video("A", youtube_id="aaaaaaaaaaa"), _video("B", youtube_id="bbbbbbbbbbb")]
                    catalog.known_views(listed)
                    return first, again, [(v.views, v.released) for v in listed]

            first, again, listed = asyncio.run(run())
            db.close()
        both = {"views": {"aaaaaaaaaaa": "1.2M"}, "released": {"aaaaaaaaaaa": "2026-10-01"}}
        self.assertEqual((first, again, listed), (both, both, [("1.2M", "2026-10-01"), (None, None)]))
        self.assertEqual(asked, ["aaaaaaaaaaa", "broken00000"])


class KnownSearch(unittest.TestCase):
    def test_what_was_shown_is_found_by_the_start_of_its_words(self):
        from app.catalog.models import ArtistRef, ArtistSummary, Video
        from app.known import Known

        def video(vid, title, artist, artist_id=None):
            return Video(id=f"yt:{vid}", source="youtube", youtube_id=vid, title=title, artist=artist,
                         artists=[ArtistRef(name=artist, id=artist_id)])

        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "t.db")
            known = Known(db)
            known.saw([video("a", "Soltera", "Shakira", "UCshak"), video("b", "Café con Ron", "Bad Bunny")],
                      [ArtistSummary(id="UCshak", name="Shakira", thumbnail="pic")], list_id="hot")
            # Seen again in a list, the credit alone doesn't take the picture away.
            known.saw([video("a", "Soltera", "Shakira", "UCshak")])
            artists, videos = known.search("shak sol")
            self.assertEqual(([(a.name, a.thumbnail) for a in artists], [v.title for v in videos]),
                             ([("Shakira", "pic")], ["Soltera"]))
            self.assertEqual([v.title for v in known.search("cafe")[1]], ["Café con Ron"])
            self.assertEqual(known.search('"OR*'), ([], []))
            # A title changed: the index follows.
            known.saw([video("b", "Cafe con Ron (Remix)", "Bad Bunny")])
            self.assertEqual([v.title for v in known.search("remix")[1]], ["Cafe con Ron (Remix)"])
            # The same song from Apple: once, as YouTube's; a fan upload's channel isn't an artist.
            known.saw([Video(id="am:1", source="apple", title="Soltera", artist="Shakira", artists=[]),
                       video("c", "Soltera (cover)", "Fan Channel", "UCfan").model_copy(update={"official": False})])
            self.assertEqual([v.id for v in known.search("soltera shakira")[1]], ["yt:a"])
            self.assertEqual(known.search("fan")[0], [])
            self.assertEqual(Known(db).count(), {"videos": 4, "artists": 1})
            db.close()


class Official(unittest.TestCase):
    def test_the_artists_own_channel_is_official(self):
        from app.catalog.models import ArtistRef
        from app.catalog.service import _official

        artists = [ArtistRef(name="aggelina", id="UCanZ2RWpbgoz7VoDBlr6z1w")]
        own = {"kind": "MUSIC_VIDEO_TYPE_UGC", "channel_id": "UCanZ2RWpbgoz7VoDBlr6z1w"}
        fan = {"kind": "MUSIC_VIDEO_TYPE_UGC", "channel_id": "UCsomeoneelse0000000000"}
        label = {"kind": "MUSIC_VIDEO_TYPE_OMV", "channel_id": "UCvevo000000000000000000"}
        self.assertEqual([_official(s, artists) for s in (own, fan, label)], [True, False, True])
        # Her other channel, a VEVO one, an album's track: the artist's own too. A fan's is not.
        other = {"kind": "MUSIC_VIDEO_TYPE_UGC", "channel_id": "UCother00000000000000000", "author": "aggelina"}
        vevo = {"kind": "MUSIC_VIDEO_TYPE_UGC", "channel_id": "UCvevo", "author": "aggelinaVEVO"}
        track = {"kind": "MUSIC_VIDEO_TYPE_ATV", "channel_id": "UCtopic"}
        fan["author"] = "Greek Music Fan"
        self.assertEqual([_official(s, artists) for s in (other, vevo, track, fan)], [True, True, True, False])


class Tags(unittest.TestCase):
    def test_a_video_carries_what_its_badges_say(self):
        from app import tags

        video = _video("Soltera", source="apple", released="2999-01-01", explicit=True)
        video.official, video.genre = False, "Latin"
        self.assertEqual(tags.tags_of(video, "es"),
                         {"explicit", "fan-upload", "apple", "soon", "genre:Latin", "lang:es"})
        self.assertEqual(tags.describe("lang:el")["label"], "Greek")
        self.assertIsNone(tags.describe("lang:xx"))
        self.assertIsNone(tags.describe("nonsense"))


class Language(unittest.TestCase):
    def test_lyrics_say_their_language_when_sure(self):
        from app.catalog import language

        greek = ("Θέλω να φορέσω κάτι που να μη σ' αρέσει, να μη σου αρέσω, να φύγεις μακριά. "
                 "Κάθε βράδυ περιμένω στο παράθυρο, η πόλη κοιμάται και εγώ ακόμα σε σκέφτομαι, "
                 "δεν ξέρω πώς να σε ξεχάσω, πες μου τι να κάνω τώρα.")
        self.assertEqual(language.detect(greek), "el")
        self.assertEqual(language.name("el"), "Greek")
        self.assertIsNone(language.detect("Too short to tell"))


class LibraryFiles(unittest.TestCase):
    def test_files_are_found_and_only_those_handed_over(self):
        from app.libraries.base import FileRef
        from app.libraries.folder import Folder, scan

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Tate McRae - Housewife [DPEodkHzH-s].mp4").write_bytes(b"video" * 10)
            found = scan(root, "videos", library="lib1")
            file = found.files[("yt", "DPEodkHzH-s")][0]
            self.assertEqual((file.library, file.kind, file.size), ("lib1", "video", 50))
            self.assertIn(file, found.files[("video", "housewife", "tatemcrae")])

            async def read(ref):
                chunks, size = await Folder().open(None, {"path": folder}, ref)
                return b"".join([c async for c in chunks]), size

            self.assertEqual(asyncio.run(read(file)), (b"video" * 10, 50))
            outside = FileRef(library="lib1", kind="video", ref="../../etc/passwd", name="passwd")
            with self.assertRaises(OSError):
                asyncio.run(read(outside))


class ListAge(unittest.TestCase):
    def test_a_list_is_as_old_as_its_oldest_part(self):
        from app.cache import Cache, tracking

        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "t.db")
            cache = Cache(db)

            async def value():
                return "x"

            async def run():
                with tracking() as reads:
                    await cache.get("list", value, fresh=600)
                    await cache.get("release-day", value, fresh=365 * 86400)
                return reads

            reads = asyncio.run(run())
            db.close()
        # The year-long release day doesn't count; the list does.
        self.assertEqual(len(reads), 1)


class Credit(unittest.TestCase):
    def test_the_fullest_credit_of_the_same_artists(self):
        from app.catalog.service import _fuller_credit

        self.assertEqual(_fuller_credit("bludnymph", "Jayelle & bludnymph", "Jayelle & bludnymph"),
                         "Jayelle & bludnymph")
        # Another song's credit, however long, is not this one's.
        self.assertEqual(_fuller_credit("Tate McRae", None, "Luna Rae & Someone"), "Tate McRae")


class Libraries(unittest.TestCase):
    def test_a_folder_of_music_videos(self):
        from app.libraries.folder import scan

        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "Tate McRae"
            folder.mkdir()
            (folder / "Tate McRae - Housewife (Official Video) [DPEodkHzH-s].mp4").touch()
            (folder / "Blue Moon.mkv").touch()
            (folder / "cover.jpg").touch()
            found = scan(Path(root), "videos")
        self.assertEqual(found.videos, {"DPEodkHzH-s"})
        self.assertEqual(found.video_songs, {("housewife", "tatemcrae"), ("bluemoon", "tatemcrae")})
        self.assertEqual(found.songs, set())

    def test_marks(self):
        from app.libraries import LibraryIndex, Snapshot

        index = LibraryIndex(None, None)
        snapshot = Snapshot()
        snapshot.add_song("Gnarly", "KATSEYE")
        snapshot.add_video("Housewife", "Tate McRae", "DPEodkHzH-s")
        index._snapshot = snapshot
        self.assertEqual(index.held("x", "Gnarly (Clean Edit)", "KATSEYE & Ice Spice"), ["audio"])
        self.assertEqual(index.held("DPEodkHzH-s", "Housewife (Live)", "Someone"), ["video"])
        self.assertEqual(index.held("other", "Housewife", "Tate McRae"), ["video"])
        self.assertEqual(index.held("other", "Gnarly Night", "KATSEYE"), [])


class LyricsChoice(unittest.TestCase):
    def test_the_right_take_timed_first(self):
        from app.catalog import lrclib

        entries = [
            {"trackName": "Gnarly", "artistName": "KATSEYE", "duration": 137.3, "instrumental": True},
            {"trackName": "Gnarly", "artistName": "KATSEYE", "duration": 2.0, "syncedLyrics": "[00:01.00] x"},
            {"trackName": "Gnarly", "artistName": "KATSEYE", "duration": 142.0, "plainLyrics": "plain"},
            {"trackName": "Gnarly", "artistName": "KATSEYE", "duration": 139.0, "syncedLyrics": "[00:01.00] y"},
            {"trackName": "Gnarly (Ice Spice Remix)", "artistName": "KATSEYE", "duration": 138.0,
             "syncedLyrics": "[00:01.00] z"}]
        self.assertEqual(lrclib.best(entries, "Gnarly", "KATSEYE", 138)["syncedLyrics"], "[00:01.00] y")

    def test_lrc(self):
        from app.catalog import lrclib

        self.assertEqual(lrclib.parse_lrc("[ar:x]\n[00:12.50]Hello\n[01:02.00][00:05.00]Twice\n"),
                         [{"start": 5.0, "text": "Twice"}, {"start": 12.5, "text": "Hello"},
                          {"start": 62.0, "text": "Twice"}])


class HotListTests(unittest.TestCase):
    def test_artists_stay_and_resolve(self):
        from app.hotlist import HotList

        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "t.db")
            hot = HotList(db)
            hot.saw([{"artists": [{"name": "Jayelle", "id": None}, {"name": "bludnymph", "id": None}]},
                     {"artists": [{"name": "TateMcRaeVEVO", "id": "UCvevo"}]},
                     {"artists": [{"name": "Jayelle"}]}])
            hot.saw([])
            names = [(a["name"], a["videos"], a["id"]) for a in hot.artists()]
            self.assertEqual(names[0], ("Jayelle", 2, None))
            # A VEVO channel is not the artist's page.
            self.assertIn(("TateMcRae", 1, None), names)
            hot.resolved("Jayelle", "UCjayelle", None)
            self.assertNotIn("Jayelle", hot.unresolved(10))
            hot.saw([{"artists": [{"name": "Jayelle"}]}])
            self.assertEqual(hot.artists()[0]["id"], "UCjayelle")
            hot.pick({"id": "yt:a", "title": "A"})
            hot.pick({"id": "yt:b", "title": "B"})
            hot.pick({"id": "yt:a", "title": "A"})
            self.assertEqual(hot.pick_ids(), ["yt:a", "yt:b"])
            db.close()


class AurralFeed(unittest.TestCase):
    def entry(self, status, label, **more):
        return {"kind": "track_download", "jobId": "j", "status": status, "statusLabel": label, "source": "slskd",
                **more}

    def test_states(self):
        self.assertEqual(from_activity(self.entry("pending", "Queued")).state, "sent")
        self.assertEqual(from_activity(self.entry("processing", "Searching")).message, "Searching Soulseek.")
        self.assertEqual(from_activity(self.entry("processing", "Downloading")).state, "downloading")
        self.assertEqual(from_activity(self.entry("blocked", "Review")).state, "sent")
        failed = from_activity(self.entry("failed", "Failed", subtitle="No match on Soulseek"))
        self.assertEqual((failed.state, failed.message), ("failed", "No match on Soulseek"))

    def test_quality_from_the_file(self):
        done = from_activity(self.entry("completed", "Downloaded", sourceFilename="Artist/01 - Song.flac"))
        self.assertEqual((done.state, done.quality), ("completed", "FLAC"))


# ---- the engine, with services that answer as told ------------------------------------------

class Scripted:
    """A service type whose submit and poll answer from a script."""

    key = "scripted"
    label = "Scripted"
    description = ""
    fields = ()

    def __init__(self):
        self.submits = []
        self.answers: dict[str, list] = {}

    def kinds(self, config):
        return tuple(config.get("kinds", "audio").split(","))

    def check(self, config):
        return dict(config)

    def summary(self, config):
        return ""

    def _next(self, config, step):
        script = self.answers[config["name"]]
        answer = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def submit(self, ctx, request, config):
        self.submits.append(config["name"])
        return self._next(config, "submit")

    async def poll(self, ctx, request, attempt, config):
        return self._next(config, "poll")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.dir.name) / "t.db")
        self.scripted = Scripted()
        TYPES["scripted"] = self.scripted
        self.services = Integrations(self.db, "service", TYPES)
        self.requests = Requests(self.db)
        self.events = []
        self.engine = Engine(self.requests, self.services, self.events.append,
                             lambda: {"poll_seconds": 15, "attempt_timeout_hours": 12}, None, "http://c/api/v1", "")
        self.media = {"video_key": "yt:abc", "video_id": "abc", "audio_id": "def", "title": "Song",
                      "artist": "Artist", "album": None, "year": None, "duration": 200, "thumbnail": None,
                      "video_title": "Song", "artist_id": None, "links": {}}

    def tearDown(self):
        TYPES.pop("scripted", None)
        self.db.close()
        self.dir.cleanup()

    def add(self, name, *answers, kinds="audio"):
        self.services.create({"name": name, "type": "scripted", "config": {"name": name, "kinds": kinds}})
        self.scripted.answers[name] = list(answers)

    def run_round(self):
        asyncio.run(self.engine.run_round())

    def test_falls_back_to_the_next_service(self):
        from app.outbound import SendFailed

        self.add("first", SendFailed("down"))
        self.add("second", Update("sent", external_id="1"), Update("completed", quality="OPUS"))
        request = self.requests.create("audio", self.media)
        self.run_round()
        self.assertEqual(self.scripted.submits, ["first", "second"])
        self.assertEqual(self.requests.get(request["id"])["status"], "sent")
        self.run_round()
        done = self.requests.get(request["id"])
        self.assertEqual((done["status"], done["quality"]), ("completed", "OPUS"))
        self.assertEqual([e.name for e in self.events], ["request.completed"])

    def test_a_failure_while_downloading_moves_on(self):
        self.add("first", Update("sent", external_id="1"), Update("failed", "not found"))
        self.add("second", Update("completed", "had it", already=True))
        request = self.requests.create("audio", self.media)
        self.run_round()
        self.run_round()
        self.run_round()
        self.assertEqual(self.requests.get(request["id"])["status"], "completed")
        self.assertEqual(self.scripted.submits, ["first", "second"])

    def test_every_service_failed(self):
        self.add("only", Update("failed", "nothing on Soulseek"))
        request = self.requests.create("audio", self.media)
        self.run_round()
        failed = self.requests.get(request["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("nothing on Soulseek", failed["message"])
        self.assertEqual(self.events[-1].name, "request.failed")

    def test_only_services_of_the_kind(self):
        self.add("audio only", Update("completed"))
        request = self.requests.create("video", self.media)
        self.run_round()
        self.assertEqual(self.scripted.submits, [])
        self.assertIn("No enabled service takes video", self.requests.get(request["id"])["message"])

    def test_retry_asks_again_from_the_first(self):
        self.add("only", Update("failed", "no"), Update("completed"))
        request = self.requests.create("audio", self.media)
        self.run_round()
        self.engine.retry(self.requests.get(request["id"]))
        self.run_round()
        self.assertEqual(self.requests.get(request["id"])["status"], "completed")
        self.assertEqual(self.scripted.submits, ["only", "only"])

    def test_order_decides(self):
        self.add("a", Update("completed"))
        self.add("b", Update("completed"))
        ids = [item["id"] for item in self.services.list()]
        self.services.reorder(list(reversed(ids)))
        self.requests.create("audio", self.media)
        self.run_round()
        self.assertEqual(self.scripted.submits, ["b"])


class Store(unittest.TestCase):
    def test_secrets_stay_and_never_show(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "t.db")
            self.assertEqual(oct(os.stat(Path(folder) / "t.db").st_mode & 0o777), "0o600")
            store = Integrations(db, "service", TYPES)
            item = store.create({"name": "Aurral", "type": "aurral",
                                 "config": {"base_url": "http://aurral:3001/", "api_key": "k" * 64}})
            self.assertNotIn("api_key", item["config"])
            self.assertEqual(item["secrets_set"], ["api_key"])
            self.assertEqual(item["config"]["base_url"], "http://aurral:3001")
            store.update(item["id"], {"name": "Aurral", "type": "aurral",
                                      "config": {"base_url": "http://aurral:3001", "api_key": ""}})
            self.assertEqual(store.find(item["id"])["config"]["api_key"], "k" * 64)
            with self.assertRaises(Invalid):
                store.create({"name": "x", "type": "aurral", "config": {"base_url": "aurral"}})
            db.close()


if __name__ == "__main__":
    unittest.main()
