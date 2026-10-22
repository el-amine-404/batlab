"""python -m unittest discover -s tests (in the image, see docs/services.md).

The API tests run videodrop whole, its scheduler and download processes
included, with fake_runner.py in place of yt-dlp: no network."""

import hashlib
import hmac
import json
import logging
import shutil
import subprocess
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
from videodrop import checks, links, logs, lyrics, placement, previews, redact  # noqa: E402
from videodrop.api.app import create_app  # noqa: E402
from videodrop.callbacks import sign  # noqa: E402
from videodrop.domain import Invalid  # noqa: E402
from videodrop.options import DEFAULTS, Section, check_template, resolve, safe_path, to_output_template  # noqa: E402
from videodrop.scheduler import TRANSIENT, backoff  # noqa: E402
from videodrop.services import build  # noqa: E402
from videodrop.settings import SettingsPatch  # noqa: E402
from videodrop.runner import StepWatch  # noqa: E402
from videodrop.ytdlp import fingerprint, params  # noqa: E402
from batlab_common import egress  # noqa: E402


async def _resolve(host: str, port: int) -> list[str]:
    """No DNS in the tests: inside.example.com points home, any other name out."""
    if host.startswith("inside."):
        raise egress.Refused(f"{host} points to 10.0.0.5, an address on this network, not the internet")
    return ["93.184.215.14"]


links.RESOLVE = _resolve

FAKE = str(Path(__file__).with_name("fake_runner.py"))
FAKE_PROBE = str(Path(__file__).with_name("fake_probe.py"))


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


class SafeNames(unittest.TestCase):
    def test_no_character_a_disk_or_a_player_trips_on(self):
        cases = {
            # yt-dlp's look-alikes for the characters a name can't hold
            "c4s/Fat Drops\uff1a Female [xhV0I89].mp4": "c4s/Fat Drops - Female [xhV0I89].mp4",
            "AC\u29f8DC/Live at 12\uff1a30 \uff5c Paris \uff02best\uff02\uff1f [x].mkv": "AC-DC/Live at 12-30 - Paris 'best' [x].mkv",
            "a/..hidden\u200b [a].mp4": "a/hidden [a].mp4",
            "Beyonc\u00e9/D\u00e9j\u00e0 vu \u2013 \u65e5\u672c [x].mp4": "Beyonc\u00e9/D\u00e9j\u00e0 vu \u2013 \u65e5\u672c [x].mp4",
            "?/***.mp4": "_/video.mp4",
        }
        for raw, safe in cases.items():
            self.assertEqual(safe_path(raw), safe)

    def test_a_section_ends_the_name(self):
        self.assertEqual(to_output_template("{title} [{id}]", Section(start=90, end=3725)),
                         "%(title).150B [%(id)s] [1m30s-1h02m05s].%(ext)s")
        with self.assertRaises(ValueError):
            Section(start=10, end=10.5)


class Previews(unittest.TestCase):
    def test_details_gallery_storyboard_and_subtitles(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = FileChecks.make(None, Path(tmp), "a.mp4", 12)
            srt = Path(tmp, "l.srt")
            srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello\n", encoding="utf-8")
            checks.embed_subtitles(video, srt, "Lyrics")
            made = previews.Previews(Path(tmp, "previews"))
            details = made.details(video)
            self.assertEqual((details["video"]["width"], details["video"]["codec"], len(details["audio"])),
                             (160, "h264", 1))
            self.assertEqual(details["subtitles"][0]["title"], "Lyrics")
            self.assertEqual(len(details["frames"]), 4)  # a short video: 4 pictures
            self.assertEqual((details["storyboard"]["count"], details["storyboard"]["every"]), (6, 2.0))
            self.assertTrue(made.frame(video, 3).stat().st_size > 0)
            self.assertIsNone(made.frame(video, 4))
            board = made.storyboard(video)
            size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                                   str(board)], capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(size, "960,90")  # 6 pictures of 160×90 in a row
            self.assertIn("Hello", made.subtitles(video, 0).read_text())
            self.assertIsNone(made.subtitles(video, 1))
            # A link to the same file shares them; once no file has them, they go.
            linked = Path(tmp, "b.mp4")
            linked.hardlink_to(video)
            self.assertEqual(made.key(linked), made.key(video))
            self.assertEqual(made.sweep([video]), 0)
            old = time.time() - previews.UNUSED_FOR - 10
            home = Path(tmp, "previews", made.key(video))
            import os
            os.utime(home, (old, old))
            self.assertEqual(made.sweep([]), 1)
            self.assertFalse(home.exists())
            made.close()


    def test_the_gallery_follows_the_length_or_the_chapters(self):
        self.assertEqual([len(previews.gallery(seconds, [])) for seconds in (30, 300, 1800, 7200)], [4, 8, 16, 24])
        chapters = [{"start": 0.0, "end": 60.0, "title": "Intro"}, {"start": 60.0, "end": 61.0, "title": "Cut"},
                    {"start": 61.0, "end": 300.0, "title": "Song"}]
        self.assertEqual(previews.gallery(300, chapters), [{"time": 2.0, "chapter": "Intro"},
                                                           {"time": 60.25, "chapter": "Cut"},
                                                           {"time": 63.0, "chapter": "Song"}])
        self.assertEqual(len(previews.gallery(300, chapters[:2])), 8)  # 2 chapters: evenly
        self.assertEqual(previews.gallery(0, []), [])


class Redaction(unittest.TestCase):
    def test_secrets_never_reach_a_log(self):
        cases = {
            "GET https://rr3.googlevideo.com/videoplayback?expire=1&ip=203.0.113.9&" + "x" * 300:
                "GET https://rr3.googlevideo.com/videoplayback?•••",
            "callback http://bob:hunter2@hook.lan/x?token=abc&v=1": "callback http://•••@hook.lan/x?token=•••&v=1",
            "notify tgram://123456:AAH-bot-token/987": "notify tgram://•••",
            "Cookie: SID=abc; HSID=def": "Cookie: •••",
            "Couldn't reach bob:hunter2@127.0.0.1:9: ConnectError": "Couldn't reach •••@127.0.0.1:9: ConnectError",
            "mail me at a@b.lan, at 12:30@home": "mail me at a@b.lan, at •••@home",
            "https://site/v?id=7&sig=ABC&api_key=K": "https://site/v?id=7&sig=•••&api_key=•••",
        }
        for raw, safe in cases.items():
            self.assertEqual(redact.clean(raw), safe)

    def test_a_line_cant_forge_another(self):
        self.assertEqual(redact.clean("title\n2026 ERROR fake\r\x1b[31mred\x1b[0m\x07"), "title ⏎ 2026 ERROR fake ⏎ red")
        self.assertTrue(redact.clean("x" * 5000).endswith("(1000 more characters)"))


    def test_the_server_log_is_cleaned_and_kept(self):
        recent, logger = logs.Recent(), logging.getLogger("videodrop.test")
        recent.addFilter(logs.Clean())
        logger.setLevel(logging.INFO)
        logger.addHandler(recent)
        try:
            logger.info("callback to %s", "http://u:pw@x/h")
            try:
                raise ValueError("https://x/?token=abc")
            except ValueError:
                logger.exception("failed")
        finally:
            logger.removeHandler(recent)
        first, second = recent.lines()
        self.assertEqual(first["message"], "callback to http://•••@x/h")
        self.assertIn("token=•••", second["detail"])
        self.assertNotIn("abc", second["detail"])
        self.assertEqual(recent.lines(level="error"), [second])
        self.assertEqual(recent.lines(after=first["seq"]), [second])


class SitesOf(unittest.TestCase):
    def test_a_site_is_its_domain(self):
        from videodrop.sites import site_of
        self.assertEqual([site_of(h) for h in ("www.youtube.com", "music.youtube.com", "www.bbc.co.uk",
                                               "music.163.com", "m.facebook.com", "player.vimeo.com")],
                         ["youtube.com", "youtube.com", "bbc.co.uk", "music.163.com", "facebook.com", "vimeo.com"])


class Fingerprints(unittest.TestCase):
    def info(self, **change):
        video = {"format_id": "401", "width": 3840, "height": 2160, "vcodec": "av01", "acodec": "none",
                 "filesize": 280_000_000, "tbr": 10595.1}
        audio = {"format_id": "140", "vcodec": "none", "acodec": "mp4a", "filesize": 3_400_000, "tbr": 129.5}
        info = {"id": "DPEodkHzH-s", "extractor_key": "Youtube", "duration": 209,
                "requested_formats": [{**video, **change}, audio]}
        return info

    def test_same_video_same_fingerprint(self):
        self.assertEqual(fingerprint(self.info()), fingerprint(self.info()))
        self.assertEqual(len(fingerprint(self.info())), 32)

    def test_a_changed_video_or_another_quality_differs(self):
        for change in ({"filesize": 281_000_000}, {"format_id": "400", "height": 1440}, {"tbr": 9000}):
            with self.subTest(change):
                self.assertNotEqual(fingerprint(self.info()), fingerprint(self.info(**change)))
        self.assertIsNone(fingerprint({"_type": "playlist", "id": "x"}))


class StepProgress(unittest.TestCase):
    def test_the_new_copy_against_the_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.mp4").write_bytes(b"x" * 1000)
            Path(tmp, "a.jpg").write_bytes(b"x" * 5000)  # the cover isn't the video
            seen = []
            watch = StepWatch(lambda event, **fields: seen.append(fields["percent"]), Path(tmp))
            watch.start()
            Path(tmp, "a.temp.mp4").write_bytes(b"x" * 400)
            time.sleep(1.3)
            watch.stop()
            self.assertEqual(seen, [40])


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


class Lyrics(unittest.TestCase):
    def test_the_song_from_the_title(self):
        self.assertEqual(lyrics.guess({"title": "Tate McRae - Housewife (Official Video)", "uploader": "TateMcRaeVEVO"}),
                         ("Tate McRae", "Housewife"))
        self.assertEqual(lyrics.guess({"track": "Housewife", "artist": "Tate McRae, Someone", "title": "x"}),
                         ("Tate McRae", "Housewife"))
        self.assertEqual(lyrics.guess({"title": "Housewife [4K]", "uploader": "Tate McRae - Topic"}),
                         ("Tate McRae", "Housewife"))

    def test_lrc_to_srt(self):
        srt = lyrics.to_srt("[ar: x]\n[00:01.50] First\n[00:04.00]\n[00:05.2] Second", end=7)
        self.assertEqual(srt, "1\n00:00:01,500 --> 00:00:04,000\nFirst\n\n2\n00:00:05,200 --> 00:00:07,000\nSecond\n")

    def test_junk_lyrics_are_refused(self):
        song = "\n".join(f"[{m:02d}:{s:02d}.00] line {m}{s}" for m in range(3) for s in (5, 25, 45))
        cover = song.replace("line", "verse")
        candidates = [{"syncedLyrics": "[00:00.00]probe", "duration": 213},  # on LRCLIB for a real song
                      {"syncedLyrics": song, "duration": 400},  # another recording
                      {"syncedLyrics": cover, "duration": 212},
                      {"syncedLyrics": song, "duration": 214}, {"syncedLyrics": song, "duration": 211}]
        self.assertEqual(lyrics.choose(candidates, 213), song)
        self.assertIsNone(lyrics.choose(candidates[:1], 213))
        self.assertFalse(lyrics.plausible(song, 600), "ends too early for a 10-minute video")


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class FileChecks(unittest.TestCase):
    def make(self, folder: Path, name: str, seconds: int, audio: bool = True) -> Path:
        path = folder / name
        inputs = ["-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={seconds}"]
        if audio:
            inputs += ["-f", "lavfi", "-i", f"sine=duration={seconds}"]
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-shortest", str(path)], check=True)
        return path

    def test_a_good_video_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = checks.check(self.make(Path(tmp), "a.mp4", 3), 3)
            self.assertTrue(result["ok"], result)
            self.assertIn("160×120", result["summary"])

    def test_cut_short_and_wrong_files_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(checks.check(self.make(Path(tmp), "a.mp4", 2), 30)["ok"])
            fake = Path(tmp, "b.mp4")
            fake.write_bytes(b"MZ\x90\x00 not a video")
            self.assertFalse(checks.check(fake, None)["ok"])
            mkv = self.make(Path(tmp), "c.mkv", 2)
            renamed = mkv.rename(Path(tmp, "c.mp4"))
            self.assertIn("not the .mp4", " ".join(checks.check(renamed, 2)["problems"]))
            silent = checks.check(self.make(Path(tmp), "d.mp4", 2, audio=False), 2)
            self.assertEqual((silent["ok"], silent["problems"]), (True, ["It has no sound."]))

    def test_lyrics_become_a_subtitle_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = self.make(Path(tmp), "a.mp4", 3)
            srt = Path(tmp, "l.srt")
            srt.write_text(lyrics.to_srt("[00:00.50] Hello\n[00:02.00] World", 3))
            checks.embed_subtitles(video, srt, "Lyrics")
            kinds = [s["codec_type"] for s in checks.probe(video)["streams"]]
            self.assertEqual(kinds.count("subtitle"), 1)
            self.assertTrue(checks.check(video, 3)["ok"])


class AuthTests(unittest.TestCase):
    """People, sessions and keys, through the API as the page and apps use it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "media").mkdir()
        config = config_module.load({"VIDEODROP_STATE": str(root / "state"), "VIDEODROP_MEDIA": str(root / "media"),
                                     "VIDEODROP_STATIC": str(root / "static"),
                                     "VIDEODROP_DESTINATIONS": "music-videos=music-videos",
                                     "VIDEODROP_CHECK_RELEASES": "0"})
        self.services = build(replace(config, runner=(sys.executable, FAKE), prober=(sys.executable, FAKE_PROBE)))
        self.services.settings.update(SettingsPatch(min_free_gb=0))
        self.app = create_app(self.services)
        self.admin = TestClient(self.app)
        self.admin.__enter__()

    def tearDown(self):
        self.admin.__exit__(None, None, None)
        self.tmp.cleanup()

    def page(self, client: TestClient, method: str, path: str, **kwargs):
        """A change as the page asks it: with its header."""
        return client.request(method, path, headers={"X-Videodrop-Page": "1"}, **kwargs)

    def set_up_admin(self) -> None:
        code = self.services.accounts.setup_code()
        self.assertEqual(self.admin.post("/api/v1/auth/setup", json={"code": "WRONG-CODE-0000", "username": "amine",
                                                                     "password": "a long password"}).status_code, 422)
        # The dashes are only for reading: typed without them, in lower case, it is the same code.
        answer = self.admin.post("/api/v1/auth/setup", json={"code": code.replace("-", "").lower(), "username": "amine",
                                                              "name": "Amine", "password": "a long password"})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertIn("httponly", answer.headers["set-cookie"].lower())
        self.assertIn("samesite=lax", answer.headers["set-cookie"].lower())

    def test_nobody_is_let_in_before_setup_and_the_code_makes_the_admin(self):
        anonymous = TestClient(self.app)
        self.assertEqual(anonymous.get("/api/v1/health").status_code, 200)
        state = anonymous.get("/api/v1/auth/state").json()
        self.assertEqual((state["setup"], state["me"]), (True, None))
        for path in ("/api/v1/jobs", "/api/v1/settings", "/api/v1/status", "/api/v1/sites", "/api/v1/users"):
            answer = anonymous.get(path)
            self.assertEqual(answer.status_code, 401, path)
            self.assertIn("isn't set up yet", answer.json()["detail"])
        self.set_up_admin()
        state = self.admin.get("/api/v1/auth/state").json()
        self.assertEqual((state["setup"], state["me"]["name"], state["me"]["kind"]), (False, "Amine", "user"))
        self.assertEqual(len(state["me"]["permissions"]), 5)
        # Once someone exists, setup is closed for good.
        self.assertIsNone(self.services.accounts.setup_code())
        self.assertEqual(anonymous.post("/api/v1/auth/setup", json={"code": "X" * 12, "username": "eve",
                                                                    "password": "a long password"}).status_code, 409)
        self.assertEqual(anonymous.get("/api/v1/jobs").status_code, 401)

    def test_a_change_with_the_cookie_needs_the_pages_header(self):
        self.set_up_admin()
        url = watch("okokokokok1")
        self.assertEqual(self.admin.post("/api/v1/jobs", json={"url": url}).status_code, 403)
        job = self.page(self.admin, "POST", "/api/v1/jobs", json={"url": url}).json()
        self.assertEqual(job["asked_by"]["name"], "Amine")
        # A person can't make videodrop call an address: callbacks are an app's.
        answer = self.page(self.admin, "POST", "/api/v1/jobs", json={"url": watch("okokokokok2"),
                                                                    "callback_url": "http://10.0.0.1/"})
        self.assertEqual(answer.status_code, 403)

    def test_what_someone_may_do(self):
        self.set_up_admin()
        mine = self.page(self.admin, "POST", "/api/v1/jobs", json={"url": watch("okokokokok1"),
                                                                  "destination": "music-videos"}).json()
        made = self.page(self.admin, "POST", "/api/v1/users", json={"username": "bob", "name": "Bob",
                                                                   "password": "bobs password!",
                                                                   "permissions": ["download"]})
        self.assertEqual(made.status_code, 201, made.text)
        bob = TestClient(self.app)
        self.assertEqual(bob.post("/api/v1/auth/login", json={"username": "BOB", "password": "bobs password!"}
                                  ).status_code, 200)
        # His own downloads only, and kept here: no destination.
        self.assertEqual(self.page(bob, "POST", "/api/v1/jobs", json={"url": watch("okokokokok3"),
                                                                     "destination": "music-videos"}).status_code, 403)
        his = self.page(bob, "POST", "/api/v1/jobs", json={"url": watch("okokokokok3")}).json()
        self.assertIsNone(his["destination"])
        self.assertEqual([j["id"] for j in bob.get("/api/v1/jobs").json()["jobs"]], [his["id"]])
        self.assertEqual(bob.get(f"/api/v1/jobs/{mine['id']}").status_code, 404)
        self.assertEqual(self.page(bob, "POST", f"/api/v1/jobs/{mine['id']}/cancel").status_code, 404)
        self.assertEqual(bob.get("/api/v1/jobs/counts").json()["queued"] + bob.get("/api/v1/jobs/counts").json()[
            "running"] + bob.get("/api/v1/jobs/counts").json()["finished"], 1)
        # Not his to change: settings, notifications, people.
        self.assertEqual(self.page(bob, "PATCH", "/api/v1/settings", json={"max_parallel": 9}).status_code, 403)
        self.assertEqual(bob.get("/api/v1/notifications").status_code, 403)
        self.assertEqual(bob.get("/api/v1/users").status_code, 403)
        self.assertEqual(bob.get("/api/v1/log").status_code, 403)
        # The admin sees both.
        self.assertEqual(len(self.admin.get("/api/v1/jobs").json()["jobs"]), 2)
        # Turned off: his session ends at once.
        bob_id = made.json()["id"]
        self.assertEqual(self.page(self.admin, "PATCH", f"/api/v1/users/{bob_id}", json={"enabled": False}
                                   ).status_code, 200)
        self.assertEqual(bob.get("/api/v1/jobs").status_code, 401)

    def test_the_last_admin_stays(self):
        self.set_up_admin()
        me = self.admin.get("/api/v1/auth/me").json()["me"]
        self.assertEqual(self.page(self.admin, "PATCH", f"/api/v1/users/{me['id']}", json={"permissions": ["download"]}
                                   ).status_code, 409)
        self.assertEqual(self.page(self.admin, "DELETE", f"/api/v1/users/{me['id']}").status_code, 409)

    def test_wrong_passwords_slow_down(self):
        self.set_up_admin()
        eve = TestClient(self.app)
        for _ in range(5):
            self.assertEqual(eve.post("/api/v1/auth/login", json={"username": "amine", "password": "guess guess"}
                                      ).status_code, 422)
        answer = eve.post("/api/v1/auth/login", json={"username": "amine", "password": "a long password"})
        self.assertEqual(answer.status_code, 429)
        self.assertGreater(int(answer.headers["retry-after"]), 0)

    def test_a_new_password_ends_the_other_sessions(self):
        self.set_up_admin()
        other = TestClient(self.app)
        other.post("/api/v1/auth/login", json={"username": "amine", "password": "a long password"})
        self.assertEqual(other.get("/api/v1/jobs").status_code, 200)
        self.assertEqual(self.page(self.admin, "PUT", "/api/v1/auth/password",
                                   json={"current": "wrong one!", "new": "another long one"}).status_code, 422)
        self.assertEqual(self.page(self.admin, "PUT", "/api/v1/auth/password",
                                   json={"current": "a long password", "new": "another long one"}).status_code, 204)
        self.assertEqual(other.get("/api/v1/jobs").status_code, 401)
        self.assertEqual(self.admin.get("/api/v1/jobs").status_code, 200)
        self.assertEqual(self.page(self.admin, "POST", "/api/v1/auth/logout").status_code, 204)
        self.assertEqual(self.admin.get("/api/v1/jobs").status_code, 401)

    def test_an_apps_key(self):
        self.set_up_admin()
        made = self.page(self.admin, "POST", "/api/v1/keys", json={"name": "clipseerr",
                                                                  "permissions": ["download", "destinations"]}).json()
        self.assertTrue(made["key"].startswith("vd_"))
        self.assertNotIn("key", self.admin.get("/api/v1/keys").json()["keys"][0])
        self.assertEqual(self.page(self.admin, "POST", "/api/v1/keys", json={"name": "x", "permissions": ["admin"]}
                                   ).status_code, 422)
        app = TestClient(self.app, headers={"X-Api-Key": made["key"]})
        job = app.post("/api/v1/jobs", json={"url": watch("okokokokok4"), "destination": "music-videos",
                                             "callback_url": "http://clipseerr:8820/hook"})
        self.assertEqual(job.status_code, 201, job.text)
        self.assertEqual(job.json()["asked_by"], {"kind": "key", "id": made["id"], "name": "clipseerr"})
        bearer = TestClient(self.app, headers={"Authorization": f"Bearer {made['key']}"})
        self.assertEqual(bearer.get("/api/v1/jobs").status_code, 200)
        self.assertEqual(app.get("/api/v1/users").status_code, 403)
        self.page(self.admin, "DELETE", f"/api/v1/keys/{made['id']}")
        self.assertEqual(app.get("/api/v1/jobs").status_code, 401)
        self.assertEqual(TestClient(self.app, headers={"X-Api-Key": "vd_nope"}).get("/api/v1/jobs").status_code, 401)

    def test_nothing_that_signs_in_is_stored(self):
        self.set_up_admin()
        token = self.admin.cookies.get("videodrop_session")
        key = self.page(self.admin, "POST", "/api/v1/keys", json={"name": "app", "permissions": []}).json()["key"]
        raw = Path(self.services.config.database).read_bytes() + b"".join(
            p.read_bytes() for p in Path(self.services.config.database).parent.glob("*.db-wal"))
        for secret in (token, key, "a long password"):
            self.assertNotIn(secret.encode(), raw)


class Seed(unittest.TestCase):
    def test_apprise_for_failures_and_for_finished_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "media").mkdir()
            config = config_module.load({"VIDEODROP_STATE": str(Path(tmp) / "state"), "VIDEODROP_MEDIA": str(Path(tmp) / "media"),
                                         "VIDEODROP_APPRISE_URL": "http://apprise:8000/notify/videodrop",
                                         "VIDEODROP_APPRISE_TAG": "download-issues",
                                         "VIDEODROP_APPRISE_FINISHED_TAG": "downloads"})
            services = build(config)
            services.seed()
            services.seed()
            made = {item["name"]: item for item in services.notifications.list()}
            self.assertEqual(sorted(made), ["Download problems", "Finished downloads"])
            self.assertEqual(made["Download problems"]["events"], ["job.failed", "disk.low"])
            self.assertEqual(made["Finished downloads"]["events"], ["job.finished"])
            self.assertIn("· downloads", made["Finished downloads"]["summary"])


class Api(unittest.TestCase):
    """videodrop whole, against fake_runner.py."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "media").mkdir()
        config = config_module.load({"VIDEODROP_STATE": str(root / "state"), "VIDEODROP_MEDIA": str(root / "media"),
                                     "VIDEODROP_STATIC": str(root / "static"),
                                     "VIDEODROP_DESTINATIONS": "music-videos=music-videos",
                                     "VIDEODROP_CHECK_RELEASES": "0"})
        self.config = replace(config, runner=(sys.executable, FAKE), prober=(sys.executable, FAKE_PROBE))
        self.services = build(self.config)
        # Downloads wait while the disk is nearly full: the machine's, here (a busy build's can be). Not a test's business.
        self.services.settings.update(SettingsPatch(min_free_gb=0))
        # An app's key that may do all but manage people (the tests of accounts are AuthTests).
        self.key, _ = self.services.accounts.create_key("tests", ["download", "destinations", "everyone", "settings"],
                                                        None)
        self.client = TestClient(create_app(self.services), headers={"X-Api-Key": self.key})
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    # Generous: the image's build runs these on a busy machine, and a timing alone mustn't stop a deploy.
    def wait(self, job_id: str, *statuses: str, timeout: float = 60) -> dict:
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

    def test_a_link_pointing_home_is_refused_at_once(self):
        for path, body in (("/api/v1/jobs", {"url": "https://inside.example.com/watch?v=okokokokok1"}),
                           ("/api/v1/probe", {"url": "https://inside.example.com/watch?v=okokokokok1"})):
            answer = self.client.post(path, json=body)
            # Not a site yt-dlp knows comes first; a known site's name is checked the same way.
            self.assertEqual(answer.status_code, 422, answer.text)
        original = links.supported
        links.supported = lambda url: True
        try:
            answer = self.client.post("/api/v1/jobs", json={"url": "https://inside.example.com/v/1"})
        finally:
            links.supported = original
        self.assertEqual(answer.status_code, 422)
        self.assertIn("points to 10.0.0.5", answer.json()["detail"])

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
        short = self.wait(self.ask("okokokokok3", hold_hours=1)["id"], "finished")
        self.assertEqual(done["hold_hours"], 24)
        self.assertAlmostEqual(done["files"][0]["expires_at"], done["finished_at"] + 24 * 3600, places=3)
        self.assertAlmostEqual(short["files"][0]["expires_at"], short["finished_at"] + 3600, places=3)
        url = done["files"][0]["download_url"]
        # Its own hold: an hour and a half later, the one-hour job's file is gone, the other's not.
        self.services.keep_house(now=time.time() + 5400)
        self.assertEqual(self.client.get(short["files"][0]["download_url"]).status_code, 410)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.services.keep_house(now=time.time() + 25 * 3600)
        again = self.client.get(f"/api/v1/jobs/{done['id']}").json()
        self.assertEqual((again["files"][0]["available"], again["files"][0]["download_url"]), (False, None))
        self.assertIsNotNone(again["files"][0]["removed_at"])
        self.assertFalse((self.config.held_dir / done["id"]).exists())
        # Asked for after its hold: 410, and where to ask for it again; then the same job, the same link.
        gone = self.client.get(url)
        self.assertEqual(gone.status_code, 410)
        self.assertIn("kept 24 h", gone.json()["detail"])
        self.assertTrue(gone.json()["again"].endswith(f"/api/v1/jobs/{done['id']}/retry"))
        self.assertEqual(self.client.post(gone.json()["again"]).status_code, 200)
        self.wait(done["id"], "finished")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/jobs/{done['id']}/retry").status_code, 409)  # it's there
        self.assertIn("removed", self.client.get(f"/api/v1/jobs/{done['id']}/log.txt").text)

    def test_a_download_has_a_log_without_secrets(self):
        job = self.ask("okokokokok5", client="clipseerr", callback_url="http://bob:hunter2@127.0.0.1:9/h?token=abc")
        self.wait(job["id"], "finished")
        lines = self.client.get(job["links"]["log"]).json()["lines"]
        text = "\n".join(f"{line['source']} {line['message']}" for line in lines)
        for said in ("Asked by tests (through clipseerr)", "Try 1 of", "videodrop Fingerprint", "yt-dlp [info] okokokokok5",
                     "process a library's warning", "Made Fake Artist/", "Finished in"):
            self.assertIn(said, text)
        for secret in ("hunter2", "token=abc", "SECRETSIG", "203.0.113.9"):
            self.assertNotIn(secret, text)
        after = self.client.get(job["links"]["log"], params={"after": lines[-2]["seq"]}).json()["lines"]
        self.assertEqual(len(after), 1)
        text_file = self.client.get(f"{job['links']['log']}.txt")
        self.assertEqual(text_file.headers["content-disposition"], f'attachment; filename="videodrop-{job["id"][:8]}.log"')
        self.assertEqual(self.client.delete(f"/api/v1/jobs/{job['id']}").status_code, 204)
        self.assertEqual(self.services.db.one("SELECT COUNT(*) AS n FROM job_log")["n"], 0)

    def test_requests_carry_an_id(self):
        self.assertEqual(len(self.client.get("/api/v1/health").headers["x-request-id"]), 12)
        self.assertEqual(self.client.get("/api/v1/health", headers={"X-Request-ID": "clip-42"}).headers["x-request-id"],
                         "clip-42")
        self.assertNotEqual(self.client.get("/api/v1/health", headers={"X-Request-ID": "bad id\n"})
                            .headers["x-request-id"], "bad id\n")

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
        # A name past the disk's limit is refused, not a 500.
        self.assertEqual(self.client.get("/api/v1/folders?path=" + "a" * 256).status_code, 422)

    def test_restart_requeues_running_jobs(self):
        job = self.ask("slowslowslo")
        self.wait(job["id"], "running")
        self.client.__exit__(None, None, None)
        self.services = build(self.config)
        self.client = TestClient(create_app(self.services), headers={"X-Api-Key": self.key})
        self.client.__enter__()
        again = self.wait(job["id"], "running")
        self.assertEqual(again["attempts"], 1)
        self.client.post(f"/api/v1/jobs/{job['id']}/cancel")

    def test_the_same_file_is_reused_unless_asked_again(self):
        first = self.wait(self.ask("okokokokok1", destination="music-videos")["id"], "finished")
        second = self.wait(self.ask("okokokokok1", destination="music-videos")["id"], "finished")
        self.assertEqual((second["reused_from"], second["files"][0]["path"], second["files"][0]["existed"]),
                         (first["id"], first["files"][0]["path"], True))
        self.assertIn("nothing was downloaded", second["notes"][0])
        other_quality = self.wait(self.ask("okokokokok1", destination="music-videos", options={"quality": "720"})["id"],
                                  "finished")
        self.assertIsNone(other_quality["reused_from"])
        again = self.wait(self.ask("okokokokok1", destination="music-videos", again=True)["id"], "finished")
        self.assertIsNone(again["reused_from"])

    def test_a_section_is_a_download_of_its_own(self):
        whole = self.wait(self.ask("okokokokok3")["id"], "finished")
        part = self.wait(self.ask("okokokokok3", section={"start": 30, "end": 90})["id"], "finished")
        self.assertEqual((part["section"], part["reused_from"]), ({"start": 30, "end": 90}, None))
        self.assertTrue(part["files"][0]["name"].endswith("[30-90].mp4"))
        self.assertIsNone(whole["section"])
        same = self.wait(self.ask("okokokokok3", section={"start": 30, "end": 90})["id"], "finished")
        self.assertEqual(same["reused_from"], part["id"])
        playlist = self.client.post("/api/v1/jobs", json={"url": watch("okokokokok3"), "options": {"playlist": True},
                                                          "section": {"start": 1, "end": 5}})
        self.assertEqual(playlist.status_code, 422)

    def test_a_video_file_has_details_and_pictures(self):
        done = self.wait(self.ask("okokokokok4")["id"], "finished")
        path = self.services.jobs.file_path(self.services.jobs.files(done["id"])[0])
        path.unlink()
        FileChecks.make(None, path.parent, path.name, 6)
        here = f"/api/v1/jobs/{done['id']}/files/0"
        details = self.client.get(f"{here}/details")
        self.assertEqual(details.status_code, 200, details.text)
        body = details.json()
        self.assertEqual((body["video"]["height"], body["storyboard"]["count"]), (120, 3))
        frame = self.client.get(body["frames"][0]["url"])
        self.assertEqual((frame.status_code, frame.headers["content-type"]), (200, "image/jpeg"))
        self.assertEqual(self.client.get(body["storyboard"]["url"]).status_code, 200)
        self.assertEqual(self.client.get(f"{here}/frames/99.jpg").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{done['id']}/files/5/details").status_code, 404)
        self.assertEqual(len(body["frames"]), 4)

    def test_a_held_file_reused_has_its_own_life(self):
        first = self.wait(self.ask("okokokokok2")["id"], "finished")
        second = self.wait(self.ask("okokokokok2")["id"], "finished")
        self.assertEqual(second["reused_from"], first["id"])
        self.client.delete(f"/api/v1/jobs/{first['id']}")
        self.assertEqual(self.client.get(second["files"][0]["download_url"]).status_code, 200)

    def test_link_check_and_probe(self):
        good = self.client.get("/api/v1/links/check", params={"url": "https://youtu.be/okokokokok1?si=x"}).json()
        self.assertEqual((good["ok"], good["canonical"]), (True, "https://www.youtube.com/watch?v=okokokokok1"))
        bad = self.client.get("/api/v1/links/check", params={"url": "http://10.0.0.1/a"}).json()
        self.assertEqual(bad["ok"], False)
        self.assertIn("this network", bad["message"])
        probe = self.client.post("/api/v1/probe", json={"url": watch("okokokokok1"), "destination": "music-videos"})
        self.assertEqual(probe.status_code, 200, probe.text)
        info = probe.json()
        self.assertEqual((info["format"]["height"], info["target"]["path"], info["target"]["exists"]),
                         (1080, "music-videos/Fake Artist/Song [okokokokok1].mp4", False))
        gone = self.client.post("/api/v1/probe", json={"url": watch("gonegonegon")})
        self.assertEqual((gone.status_code, gone.json()["detail"]), (422, "Video unavailable"))

    def test_lyrics_notes_and_checks_reach_the_job(self):
        job = self.ask("okokokokok9", options={"lyrics": True}, song={"artist": "Tate McRae", "title": "Housewife"})
        done = self.wait(job["id"], "finished")
        self.assertEqual(done["song"], {"artist": "Tate McRae", "title": "Housewife"})
        self.assertEqual(done["notes"], ["Lyrics added as subtitles (LRCLIB: Tate McRae – Housewife)."])
        self.assertEqual(done["files"][0]["checks"]["summary"], "AV1 1920×1080 · Opus · 3:20")

    def test_issues(self):
        self.services.db.put("yt_dlp_latest", {"version": "2999.1.1", "released_at": "2020-01-01T00:00:00Z"})
        failed = self.wait(self.ask("gonegonegon")["id"], "failed")
        self.services.jobs.update(failed["id"], error="Sign in to confirm you're not a bot")
        issues = {issue["id"]: issue for issue in self.client.get("/api/v1/issues").json()["issues"]}
        self.assertIn("yt-dlp", issues)
        self.assertEqual(issues["bot-check"]["items"][0]["path"], f"/jobs/{failed['id']}")

    def test_supported_sites(self):
        answer = self.client.get("/api/v1/sites")
        self.assertEqual(answer.status_code, 200)
        body = answer.json()
        sites = {site["domain"]: site for site in body["sites"]}
        self.assertGreater(len(sites), 500)
        self.assertEqual((sites["youtube.com"]["name"], sites["youtube.com"]["category"]), ("YouTube", "video"))
        self.assertEqual(sites["xhamster.com"]["category"], "adult")
        self.assertIn("playlists", sites["youtube.com"]["kinds"])
        self.assertEqual(sum(c["count"] for c in body["categories"]), len(body["sites"]))
        # Only a listed site's icon is ever asked for.
        self.assertEqual(self.client.get("/api/v1/sites/evil.example/icon").status_code, 404)

    def test_status_and_docs(self):
        status = self.client.get("/api/v1/status").json()
        self.assertEqual(status["max_parallel"], 2)
        self.assertIn("yt_dlp", status)
        spec = self.client.get("/api/v1/openapi.json").json()
        self.assertIn("/api/v1/jobs/{job_id}/events", spec["paths"])
        self.assertEqual(self.client.get("/api/v1/log", params={"level": "warning"}).status_code, 200)
        page = self.client.get("/")
        self.assertIn("default-src 'self'", page.headers["content-security-policy"])


if __name__ == "__main__":
    unittest.main()
