"""python -m unittest discover -s tests (in the image, see docs/services.md).

The API tests run videodrop whole, its scheduler and download processes
included, with fake_runner.py in place of yt-dlp: no network."""

import hashlib
import hmac
import json
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from videodrop import config as config_module  # noqa: E402
from videodrop import links, placement  # noqa: E402
from videodrop.api.app import create_app  # noqa: E402
from videodrop.callbacks import sign  # noqa: E402
from videodrop.domain import Invalid  # noqa: E402
from videodrop.options import DEFAULTS, check_template, resolve, to_output_template  # noqa: E402
from videodrop.scheduler import TRANSIENT, backoff  # noqa: E402
from videodrop.services import build  # noqa: E402
from videodrop.ytdlp import params  # noqa: E402

FAKE = str(Path(__file__).with_name("fake_runner.py"))


def watch(video: str) -> str:
    return f"https://www.youtube.com/watch?v={video}"


class Options(unittest.TestCase):
    def test_layers_first_wins(self):
        options = resolve({"quality": "720"}, {"quality": "1080", "container": "mkv"})
        self.assertEqual((options.quality, options.container, options.codec), ("720", "mkv", "any"))
        self.assertEqual(resolve(None, None), DEFAULTS)

    def test_templates(self):
        self.assertEqual(check_template(" {artist}/{title} [{id}] "), "{artist}/{title} [{id}]")
        for bad in ("{title}", "{artist}/../{id}", "{nope} {id}", "a//{id}", "{id}:x", ""):
            with self.subTest(bad), self.assertRaises(ValueError):
                check_template(bad)
        self.assertEqual(to_output_template("100% {title} [{id}]"), "100%% %(title).150B [%(id)s].%(ext)s")

    def test_ytdlp_params(self):
        options = resolve({"quality": "1080", "codec": "h264", "container": "mp4", "subtitles": ["en"]})
        made = params(options, "%(id)s.%(ext)s", Path("/w"), rate_limit=1024)
        self.assertEqual(made["format_sort"], ["res:1080", "vcodec:h264", "ext:mp4:m4a"])
        self.assertEqual(made["merge_output_format"], "mp4")
        self.assertEqual(made["allowed_extractors"], ["default", "-generic"])
        self.assertEqual(made["ratelimit"], 1024)
        keys = [p["key"] for p in made["postprocessors"]]
        self.assertEqual(keys, ["FFmpegEmbedSubtitle", "FFmpegMetadata", "FFmpegThumbnailsConvertor", "EmbedThumbnail"])
        webm = params(resolve({"container": "webm"}), "x", Path("/w"))
        self.assertFalse(webm["writethumbnail"])
        self.assertNotIn("merge_output_format", params(resolve({"container": "original"}), "x", Path("/w")))


class Links(unittest.TestCase):
    def test_canonical(self):
        for url in ("https://youtu.be/DPEodkHzH-s?si=abc", "https://m.youtube.com/watch?v=DPEodkHzH-s&feature=share",
                    "https://music.youtube.com/watch?v=DPEodkHzH-s", "https://www.youtube.com/shorts/DPEodkHzH-s"):
            self.assertEqual(links.canonical(url), "https://www.youtube.com/watch?v=DPEodkHzH-s")
        self.assertEqual(links.site_of("https://youtu.be/x"), "youtube.com")

    def test_refused(self):
        for url in ("ftp://example.com/a", "http://192.168.1.1/video", "http://apprise:8000/x",
                    "http://router.lan/a.mp4", "https://user:pw@www.youtube.com/watch?v=DPEodkHzH-s",
                    "https://example.com/some/page"):
            with self.subTest(url), self.assertRaises(Invalid):
                links.check(url)
        self.assertEqual(links.check(" https://vimeo.com/76979871 "), "https://vimeo.com/76979871")

    def test_transient(self):
        self.assertTrue(TRANSIENT.search("HTTP Error 503: Service Unavailable"))
        self.assertTrue(TRANSIENT.search("Sign in to confirm you're not a bot"))
        self.assertFalse(TRANSIENT.search("Video unavailable"))
        self.assertEqual([backoff(n) for n in (1, 2, 3)], [60, 300, 1500])


class Placement(unittest.TestCase):
    def test_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, media = Path(tmp, "work"), Path(tmp, "media")
            (work / "A").mkdir(parents=True)
            (work / "A" / "t [x].mp4").write_bytes(b"new")
            (media / "videos" / "A").mkdir(parents=True)
            (media / "videos" / "A" / "t [x].mp4").write_bytes(b"old")
            (work / "B.mp4").write_bytes(b"b")
            placed = placement.place(work, ["A/t [x].mp4", "B.mp4", "../escape.mp4"],
                                     placement.Target("destination", media, "videos"))
            self.assertEqual([(p["path"], p["existed"]) for p in placed],
                             [("videos/A/t [x].mp4", True), ("videos/B.mp4", False)])
            self.assertEqual((media / "videos" / "A" / "t [x].mp4").read_bytes(), b"old")
            self.assertFalse((work / "A" / "t [x].mp4").exists())


class Api(unittest.TestCase):
    """videodrop whole, against fake_runner.py."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "media").mkdir()
        config = config_module.load({"VIDEODROP_STATE": str(root / "state"), "VIDEODROP_MEDIA": str(root / "media"),
                                     "VIDEODROP_STATIC": str(root / "static"),
                                     "VIDEODROP_DESTINATIONS": "music-videos=music-videos"})
        self.config = replace(config, runner=(sys.executable, FAKE))
        self.services = build(self.config)
        self.client = TestClient(create_app(self.services))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def wait(self, job_id: str, *statuses: str, timeout: float = 15) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.client.get(f"/api/v1/jobs/{job_id}").json()
            if job["status"] in statuses:
                return job
            time.sleep(0.05)
        self.fail(f"job {job_id} stayed {job['status']}")

    def ask(self, video: str, **body) -> dict:
        answer = self.client.post("/api/v1/jobs", json={"url": watch(video), **body})
        self.assertIn(answer.status_code, (200, 201), answer.text)
        return answer.json()

    def test_download_into_a_destination_and_fetch_it(self):
        job = self.ask("okokokokok1", destination="music-videos", client="clipseerr")
        self.assertEqual(job["status"], "queued")
        done = self.wait(job["id"], "finished")
        self.assertEqual((done["title"], done["uploader"], done["site"]), ("Song 1", "Fake Artist", "youtube.com"))
        [file] = done["files"]
        self.assertEqual(file["path"], "music-videos/Fake Artist/Song 1 [okokokokok1].mp4")
        self.assertTrue(Path(self.config.media_root, file["path"]).is_file())
        self.assertTrue(file["download_url"].startswith("http://testserver/api/v1/jobs/"))
        whole = self.client.get(file["download_url"])
        self.assertEqual((whole.status_code, len(whole.content)), (200, 1000))
        self.assertIn("attachment", whole.headers["content-disposition"])
        part = self.client.get(file["download_url"], headers={"Range": "bytes=10-19"})
        self.assertEqual((part.status_code, part.content), (206, b"0123456789"))
        self.assertFalse(list(self.config.work_dir.iterdir()), "the work folder is emptied")

    def test_asked_twice_is_one_job(self):
        first = self.ask("slowslowslo")
        again = self.client.post("/api/v1/jobs", json={"url": "https://youtu.be/slowslowslo?si=x"})
        self.assertEqual((again.status_code, again.json()["id"]), (200, first["id"]))
        other = self.ask("slowslowslo", options={"quality": "720"})
        self.assertNotEqual(other["id"], first["id"])
        for job in (first, other):
            self.client.post(f"/api/v1/jobs/{job['id']}/cancel")

    def test_cancel_stops_the_process(self):
        job = self.ask("slowslowslo")
        self.wait(job["id"], "running")
        time.sleep(0.5)
        self.assertIsNotNone(self.client.get(f"/api/v1/jobs/{job['id']}").json()["progress"])
        self.client.post(f"/api/v1/jobs/{job['id']}/cancel")
        self.assertEqual(self.wait(job["id"], "cancelled")["files"], [])
        retried = self.client.post(f"/api/v1/jobs/{job['id']}/retry").json()
        self.assertIn(retried["status"], ("queued", "running"))
        self.client.post(f"/api/v1/jobs/{job['id']}/cancel")

    def test_failures_retry_then_fail(self):
        self.client.patch("/api/v1/settings", json={"retries": 1})
        flaky = self.ask("flakyflaky1")
        queued, deadline = None, time.time() + 10
        while time.time() < deadline:
            queued = self.client.get(f"/api/v1/jobs/{flaky['id']}").json()
            if queued["retry_at"]:
                break
            time.sleep(0.05)
        self.assertEqual((queued["status"], queued["attempts"]), ("queued", 1))
        self.assertIn("503", queued["error"])
        gone = self.wait(self.ask("gonegonegon")["id"], "failed")
        self.assertEqual((gone["error"], gone["attempts"]), ("[youtube] gonegonegon: Video unavailable", 1))

    def test_held_files_without_a_destination(self):
        done = self.wait(self.ask("playlist000", options={"playlist": True})["id"], "finished")
        self.assertEqual([f["name"] for f in done["files"]], ["First [aaaaaaaaaa1].mp4", "Second [aaaaaaaaaa2].mp4"])
        self.assertEqual([f["path"] for f in done["files"]], [None, None])
        folder = self.config.held_dir / done["id"]
        self.assertTrue(folder.is_dir())
        self.assertEqual(self.client.get(done["files"][1]["download_url"]).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/v1/jobs/{done['id']}").status_code, 204)
        self.assertFalse(folder.exists())
        self.assertEqual(self.client.get(f"/api/v1/jobs/{done['id']}").status_code, 404)

    def test_held_files_expire(self):
        done = self.wait(self.ask("okokokokok2")["id"], "finished")
        self.services.keep_house(now=time.time() + 25 * 3600)
        again = self.client.get(f"/api/v1/jobs/{done['id']}").json()
        self.assertEqual((again["files"][0]["available"], again["files"][0]["download_url"]), (False, None))
        self.assertFalse((self.config.held_dir / done["id"]).exists())

    def test_one_site_at_a_time(self):
        self.client.patch("/api/v1/settings", json={"max_parallel": 4, "per_site_parallel": 1})
        first, second = self.ask("slowslowslo"), self.ask("slowslowslo", options={"quality": "480"})
        self.wait(first["id"], "running")
        time.sleep(0.5)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{second['id']}").json()["status"], "queued")
        self.client.post(f"/api/v1/jobs/{first['id']}/cancel")
        self.wait(second["id"], "running")
        self.client.post(f"/api/v1/jobs/{second['id']}/cancel")

    def test_priority_goes_first(self):
        self.client.patch("/api/v1/settings", json={"max_parallel": 1})
        blocker = self.ask("slowslowslo")
        self.wait(blocker["id"], "running")
        low = self.ask("okokokokok3")
        high = self.ask("okokokokok4", priority=5)
        self.client.post(f"/api/v1/jobs/{blocker['id']}/cancel")
        high_done = self.wait(high["id"], "finished")
        low_done = self.wait(low["id"], "finished")
        self.assertLess(high_done["started_at"], low_done["started_at"])

    def test_signed_callbacks(self):
        received = []

        class Hook(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((dict(self.headers), body))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Hook)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            secret = "a-long-enough-secret"
            job = self.ask("okokokokok5", callback_url=f"http://127.0.0.1:{server.server_port}/hook",
                           callback_secret=secret)
            self.assertNotIn(secret, json.dumps(job))
            self.wait(job["id"], "finished")
            deadline = time.time() + 5
            while len(received) < 2 and time.time() < deadline:
                time.sleep(0.05)
            events = [json.loads(body)["event"] for _, body in received]
            self.assertEqual(events, ["job.started", "job.finished"])
            headers, body = received[-1]
            expected = "sha256=" + hmac.new(secret.encode(), headers["X-Videodrop-Timestamp"].encode() + b"." + body,
                                            hashlib.sha256).hexdigest()
            self.assertEqual(headers["X-Videodrop-Signature"], expected)
            self.assertEqual(sign(secret, headers["X-Videodrop-Timestamp"], body), expected)
            self.assertEqual(json.loads(body)["job"]["files"][0]["name"], "Song 5 [okokokokok5].mp4")
        finally:
            server.shutdown()
            server.server_close()

    def test_live_stream_ends_with_the_job(self):
        job = self.ask("okokokokok6")
        statuses = []
        with self.client.stream("GET", f"/api/v1/jobs/{job['id']}/events") as stream:
            self.assertEqual(stream.headers["content-type"].split(";")[0], "text/event-stream")
            for line in stream.iter_lines():
                if line.startswith("data: "):
                    statuses.append(json.loads(line[6:])["status"])
        self.assertEqual(statuses[-1], "finished")

    def test_errors_are_sentences(self):
        for body, words in (({"url": "http://192.168.1.10/a.mp4"}, "this network"),
                            ({"url": watch("okokokokok7"), "destination": "nope"}, "no destination 'nope'"),
                            ({"url": watch("okokokokok7"), "options": {"quality": "999"}}, "Options quality"),
                            ({"url": watch("okokokokok7"), "callback_secret": "short"}, "Callback secret")):
            answer = self.client.post("/api/v1/jobs", json=body)
            self.assertEqual(answer.status_code, 422, body)
            self.assertIn(words, answer.json()["detail"])
        self.assertEqual(self.client.patch("/api/v1/settings", json={"default_destination": "nope"}).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/jobs/missing").json()["detail"], "There's no download 'missing'.")

    def test_destinations(self):
        listed = self.client.get("/api/v1/destinations").json()["destinations"]
        self.assertEqual([d["slug"] for d in listed], ["music-videos"])
        made = self.client.post("/api/v1/destinations", json={"name": "Clips", "folder": "videos/clips",
                                                              "template": "{uploader}/{title} [{id}]",
                                                              "options": {"quality": "720"}})
        self.assertEqual(made.status_code, 201, made.text)
        self.assertEqual(self.client.post("/api/v1/destinations", json={"name": "Out", "folder": "../etc"}).status_code,
                         422)
        self.assertEqual(self.client.post("/api/v1/destinations", json={"name": "Own", "folder": ".videodrop/x"}
                                          ).status_code, 422)
        self.client.patch("/api/v1/settings", json={"default_destination": "clips"})
        done = self.wait(self.ask("okokokokok8")["id"], "finished")
        self.assertEqual((done["destination"], done["options"]["quality"]), ("clips", "720"))
        self.assertTrue(done["files"][0]["path"].startswith("videos/clips/"))
        self.assertEqual(self.client.delete("/api/v1/destinations/clips").status_code, 204)
        self.assertEqual(self.client.get("/api/v1/settings").json()["default_destination"], "")
        self.assertEqual(self.client.get("/api/v1/folders?path=videos").json()["folders"][0]["path"], "videos/clips")

    def test_restart_requeues_running_jobs(self):
        job = self.ask("slowslowslo")
        self.wait(job["id"], "running")
        self.client.__exit__(None, None, None)
        self.services = build(self.config)
        self.client = TestClient(create_app(self.services))
        self.client.__enter__()
        again = self.wait(job["id"], "running")
        self.assertEqual(again["attempts"], 1)
        self.client.post(f"/api/v1/jobs/{job['id']}/cancel")

    def test_status_and_docs(self):
        status = self.client.get("/api/v1/status").json()
        self.assertEqual(status["max_parallel"], 2)
        self.assertIn("yt_dlp", status)
        spec = self.client.get("/api/v1/openapi.json").json()
        self.assertIn("/api/v1/jobs/{job_id}/events", spec["paths"])
        page = self.client.get("/")
        self.assertIn("default-src 'self'", page.headers["content-security-policy"])


if __name__ == "__main__":
    unittest.main()
