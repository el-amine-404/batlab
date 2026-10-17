"""python -m unittest discover -s tests (in the image, see docs/music.md)."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

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
