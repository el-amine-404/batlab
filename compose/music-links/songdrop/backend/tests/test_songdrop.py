"""python -m unittest discover -s tests (in the image: see docs/music.md)."""

import json
import os
import sys
import tempfile
import textwrap
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cookies, detect, messages, notify, options  # noqa: E402
from app.config import Settings  # noqa: E402
from app.jobs import MAX_TRIES, JobError, JobManager  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "runners"))
import verify  # noqa: E402
import ytdlp_runner  # noqa: E402


def _tone(path, *filters):
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i", "anoisesrc=d=40:c=pink:seed=7",
                    *filters, "-ac", "2", path], check=True)


class VerifyTest(unittest.TestCase):
    """A file is compared with Spotify's preview: 30 s cut from it, or other audio."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.song = os.path.join(self.dir.name, "song.opus")
        _tone(self.song)

    def tearDown(self):
        self.dir.cleanup()

    def test_a_cut_of_the_same_recording_scores_high(self):
        preview = os.path.join(self.dir.name, "preview.mp3")
        import subprocess
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-ss", "8", "-t", "30", "-i", self.song,
                        "-b:a", "96k", preview], check=True)
        self.assertGreater(verify.similarity(verify.fingerprint(preview), verify.fingerprint(self.song)), 0.85)

    def test_other_audio_scores_low(self):
        other = os.path.join(self.dir.name, "other.mp3")
        _tone(other, "-af", "aeval=sin(2*PI*330*t)*0.5|sin(2*PI*330*t)*0.5")
        self.assertLess(verify.similarity(verify.fingerprint(other), verify.fingerprint(self.song)), verify.OTHER)

    def test_no_preview_is_no_verdict(self):
        with mock.patch.object(verify, "preview_url", return_value=None):
            self.assertIsNone(verify.score(self.song, "4MjhDzeuvIU3f4E8SqGH4q"))
        with mock.patch.object(verify, "preview_url", side_effect=OSError("offline")):
            self.assertIsNone(verify.score(self.song, "4MjhDzeuvIU3f4E8SqGH4q"))


class DetectTest(unittest.TestCase):
    def test_spotify_links(self):
        for url, kind in [
            ("https://open.spotify.com/track/09CMjY5ObC4G9NNyGH7Upc?si=abc", "track"),
            ("https://open.spotify.com/intl-fr/album/3Fhd5mu9DDnph1m4j9Hioo", "album"),
            ("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "playlist"),
            ("https://open.spotify.com/artist/0TnOYISbd1XYRBk9myaseg", "artist"),
        ]:
            found = detect.detect(url)
            self.assertEqual((found.engine, found.kind, found.site), ("spotdl", kind, "Spotify"))
            self.assertNotIn("?", found.url)

    def test_spotify_other_pages_refused(self):
        for url in ("https://open.spotify.com/show/09CMjY5ObC4G9NNyGH7Upc", "https://spotify.link/abcdef"):
            with self.assertRaises(detect.UnsupportedLink):
                detect.detect(url)

    def test_youtube(self):
        found = detect.detect("https://music.youtube.com/watch?v=wS_mhM74xWg")
        self.assertEqual((found.engine, found.site, found.kind, found.has_playlist), ("ytdlp", "YouTube Music", "media", False))
        found = detect.detect("https://www.youtube.com/watch?v=wS_mhM74xWg&list=PL590L5WQmH8fJ54F369BLDSqIwcs-TCfs")
        self.assertTrue(found.has_playlist)
        found = detect.detect("https://www.youtube.com/playlist?list=PL590L5WQmH8fJ54F369BLDSqIwcs-TCfs")
        self.assertEqual(found.kind, "playlist")
        self.assertEqual(detect.detect("https://youtu.be/wS_mhM74xWg").site, "YouTube")

    def test_soundcloud(self):
        found = detect.detect("https://soundcloud.com/forss/flickermood")
        self.assertEqual((found.engine, found.site), ("ytdlp", "SoundCloud"))

    def test_refused(self):
        for url in ("", "   ", "not a link", "ftp://example.com/a.mp3", "https://example.com/some/page",
                    "https://a.com/x https://b.com/y", "https://x.com/" + "a" * 3000):
            with self.assertRaises(detect.UnsupportedLink, msg=url):
                detect.detect(url)


class OptionsTest(unittest.TestCase):
    def test_fields_match_models(self):
        described = options.describe()["engines"]
        for engine, model in options.MODELS.items():
            keys = [field["key"] for field in described[engine]["fields"]]
            self.assertEqual(sorted(keys), sorted(model.model_fields), engine)
            for field in described[engine]["fields"]:
                if field["type"] == "select":
                    self.assertIn(field["default"], [choice["value"] for choice in field["choices"]])

    def test_unknown_or_bad_options_refused(self):
        with self.assertRaises(Exception):
            options.parse("spotdl", {"output": "/etc/passwd"})
        with self.assertRaises(Exception):
            options.parse("ytdlp", {"format": "wav"})

    def test_spotdl_settings(self):
        settings = options.spotdl_settings(options.parse("spotdl", {}), "/state/jobs/x", 3)
        self.assertEqual(settings["bitrate"], "disable")  # Opus is copied
        self.assertEqual(settings["output"], "/state/jobs/x/{artists} - {title}.{output-ext}")
        self.assertEqual(settings["lyrics_providers"], [])
        self.assertIsNone(settings["album_type"])
        settings = options.spotdl_settings(options.parse("spotdl", {"format": "mp3", "album_type": "album"}), "/j", 3)
        self.assertEqual((settings["bitrate"], settings["album_type"]), ("auto", "album"))

    def test_ytdlp_settings(self):
        self.assertEqual(options.ytdlp_settings(options.parse("ytdlp", {}))["codec"], "best")
        settings = options.ytdlp_settings(options.parse("ytdlp", {"format": "mp3", "bitrate": "320k"}))
        self.assertEqual((settings["codec"], settings["quality"]), ("mp3", "320K"))


class CutTest(unittest.TestCase):
    """Clips, chapters and playlist limits (yt-dlp options)."""

    def test_times(self):
        self.assertEqual([options.seconds(t) for t in ("", "90", "1:30", "1:02:03")], [None, 90, 90, 3723])
        for bad in ("1:75", "1:60:00", "abc", "1:2:3:4", "-5", "1;30"):
            with self.assertRaises(ValueError, msg=bad):
                options.seconds(bad)

    def test_clip_settings(self):
        settings = options.ytdlp_settings(options.parse("ytdlp", {"clip_start": "1:00", "clip_end": "4:30"}))
        self.assertEqual(settings["clip"], [60, 270])
        self.assertEqual(options.ytdlp_settings(options.parse("ytdlp", {"clip_end": "30"}))["clip"], [0, 30])
        self.assertEqual(options.ytdlp_settings(options.parse("ytdlp", {"clip_start": "2:00"}))["clip"], [120, None])
        self.assertIsNone(options.ytdlp_settings(options.parse("ytdlp", {}))["clip"])

    def test_bad_cuts_refused(self):
        for bad in ({"clip_start": "4:00", "clip_end": "1:00"}, {"clip_start": "x"},
                    {"split_chapters": True, "clip_start": "1:00"}, {"playlist_limit": "7"}):
            with self.assertRaises(Exception, msg=bad):
                options.parse("ytdlp", bad)

    def test_playlist_limit(self):
        self.assertEqual(options.ytdlp_settings(options.parse("ytdlp", {"playlist_limit": "25"}))["playlist_limit"], 25)
        self.assertIsNone(options.ytdlp_settings(options.parse("ytdlp", {}))["playlist_limit"])

    def test_chapter_tags(self):
        info = {"title": "Stormy - DESPERADO (Full Album)", "uploader": "Stormy - Topic"}
        self.assertEqual(ytdlp_runner.chapter_tags("01. EL MEJOR", 4, 12, info),
                         {"title": "EL MEJOR", "tracknumber": "4/12", "album": "Stormy - DESPERADO (Full Album)",
                          "artist": "Stormy"})
        self.assertEqual(ytdlp_runner.chapter_tags("[03:15] Manal - KATANA", 2, 3, info)["artist"], "Manal")
        self.assertEqual(ytdlp_runner.chapter_tags("", 5, 9, info)["title"], "Part 5")

    def test_chapters_become_songs_and_the_whole_video_goes(self):
        import mutagen
        events = []
        collect = ytdlp_runner._Collect(mock.Mock(emit=lambda kind, **fields: events.append((kind, fields))), True)
        with tempfile.TemporaryDirectory() as folder:
            whole = os.path.join(folder, "mix.opus")
            parts = [os.path.join(folder, f"mix - 00{i}.opus") for i in (1, 2)]
            for path in [whole, *parts]:
                _tone(path)
            info = {"filepath": whole, "title": "Summer mix", "uploader": "DJ",
                    "chapters": [{"title": "1. Artist A - One", "filepath": parts[0]},
                                 {"title": "2. Two", "filepath": parts[1]}]}
            collect.run(info)
            self.assertFalse(os.path.exists(whole))
            self.assertEqual([(k, f["index"], f["title"], f["artist"]) for k, f in events],
                             [("file", 0, "One", "Artist A"), ("file", 1, "Two", "DJ")])
            tags = mutagen.File(parts[1], easy=True)
            self.assertEqual((tags["title"], tags["album"], tags["tracknumber"]), (["Two"], ["Summer mix"], ["2/2"]))

    def test_a_video_without_chapters_stays_whole_and_says_so(self):
        events = []
        collect = ytdlp_runner._Collect(mock.Mock(emit=lambda kind, **fields: events.append(kind)), True)
        with tempfile.TemporaryDirectory() as folder:
            whole = os.path.join(folder, "song.opus")
            _tone(whole)
            collect.run({"filepath": whole, "title": "Song"})
            self.assertTrue(os.path.exists(whole))
        self.assertEqual(events, ["note", "file"])


COOKIES = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t{exp}\tSID\tsecret-value\n"


class CookiesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_saved_privately_and_never_shown(self):
        later = int(time.time()) + 86400 * 30
        status = cookies.save(self.state, COOKIES.format(exp=later).encode())
        self.assertEqual((status["present"], status["expires_at"], status["expired"]), (True, later, False))
        self.assertNotIn("secret-value", json.dumps(status))
        self.assertEqual(os.stat(cookies.path(self.state)).st_mode & 0o777, 0o600)
        job = self.state / "job"
        job.mkdir()
        copy = cookies.copy_for(self.state, job)
        self.assertIn("secret-value", Path(copy).read_text())
        cookies.delete(self.state)
        self.assertEqual(cookies.status(self.state), {"present": False})
        self.assertIsNone(cookies.copy_for(self.state, job))

    def test_expired_cookies_say_so(self):
        cookies.save(self.state, COOKIES.format(exp=int(time.time()) - 60).encode())
        self.assertTrue(cookies.status(self.state)["expired"])

    def test_other_files_refused(self):
        for bad in (b"\xff\xfe", b"hello", b"# Netscape HTTP Cookie File\nbroken line\n",
                    COOKIES.format(exp=1).replace(".youtube.com", ".example.com").encode(),
                    b"#" * (cookies.MAX_BYTES + 1)):
            with self.assertRaises(cookies.BadCookies):
                cookies.save(self.state, bad)
        self.assertFalse(cookies.path(self.state).exists())

    def test_spotdl_gets_the_copy(self):
        settings = options.spotdl_settings(options.parse("spotdl", {}), "/j", 3, "/j/.cookies.txt")
        self.assertEqual(settings["cookie_file"], "/j/.cookies.txt")


class MessagesTest(unittest.TestCase):
    def test_known_and_cleaned(self):
        self.assertIn("isn't a bot", messages.friendly("ERROR: [youtube] abc: Sign in to confirm you’re not a bot"))
        self.assertEqual(messages.friendly("ERROR: [youtube] dQw4w9WgXcQ: Some new problem"), "Some new problem")
        self.assertEqual(messages.friendly("LookupError: No results found for song: A - B"),
                         "No matching song was found to download.")
        self.assertEqual(messages.friendly(""), "Something went wrong.")
        self.assertLessEqual(len(messages.friendly("x" * 1000)), messages.MAX_LENGTH)


FAKE_RUNNER = textwrap.dedent('''
    import json, os, sys, time
    config = json.load(sys.stdin)
    events = os.fdopen(int(os.environ["SONGDROP_EVENTS_FD"]), "w", buffering=1)
    def emit(kind, **fields):
        events.write(json.dumps({"type": kind, **fields}) + "\\n")
    mode = config["url"].rsplit("/", 1)[-1]
    here = os.getcwd()
    if mode == "fatal":
        emit("item_error", index=0, name="abc123", message="ERROR: [youtube] abc123: HTTP Error 403: Forbidden")
        emit("fatal", message="ERROR: [youtube] abc123: HTTP Error 403: Forbidden")
        sys.exit(1)
    if mode == "crash":
        print("Traceback: something broke", flush=True)
        sys.exit(3)
    if mode == "comesback":
        emit("meta", title="Artist - Song 1", total=1)
        path = os.path.join(here, "Artist - Song 1.opus")
        with open(path, "wb") as handle:
            handle.write(b"OggS" + bytes(50))
        emit("file", index=0, path=path, title="Song 1", artist="Artist")
        sys.exit(0)
    if mode == "staysgone":
        emit("meta", title="Artist - Song 1", total=1)
        emit("item_error", index=0, name="Artist - Song 1", message="HTTP Error 403: Forbidden",
             url="https://open.spotify.com/track/staysgone")
        sys.exit(0)
    if mode in ("flaky", "lost"):
        emit("meta", title="Album", total=2)
        path = os.path.join(here, "Artist - Song 0.opus")
        with open(path, "wb") as handle:
            handle.write(b"OggS" + bytes(10))
        emit("file", index=0, path=path, title="Song 0", artist="Artist")
        emit("note", index=0, name="Artist - Song 0", message="YouTube's first pick was another song; replaced.")
        url = "https://open.spotify.com/track/" + ("comesback" if mode == "flaky" else "staysgone")
        emit("item_error", index=1, name="Artist - Song 1", message="HTTP Error 403: Forbidden", url=url)
        sys.exit(0)
    if mode == "slow":
        emit("meta", title="Slow", total=1)
        emit("item_start", index=0, name="slow")
        time.sleep(60)
    emit("meta", title="Album", total=3)
    for index in range(3):
        emit("item_start", index=index, name=f"song {index}")
        emit("progress", index=index, percent=50)
        if index == 1 and mode == "partial":
            emit("item_error", index=index, name="song 1", message="LookupError: No results found for song: song 1")
            continue
        path = os.path.join(here, f"Artist - Song {index}.opus")
        with open(path, "wb") as handle:
            handle.write(b"OggS" + bytes(100 + index))
        emit("file", index=index, path=path, title=f"Song {index}", artist="Artist")
    emit("file", index=9, path="/etc/passwd", title="nope", artist="nope")
    open(os.path.join(here, "leftover.webp"), "wb").close()
''')


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "inbox").mkdir()
        runner = root / "fake_runner.py"
        runner.write_text(FAKE_RUNNER)
        self.settings = Settings(
            port=0, state_dir=root / "state", inbox_dir=root / "inbox", static_dir=root / "static",
            retention_hours=24, max_items=200, concurrent_jobs=1, spotdl_parallel=3,
            job_timeout_seconds=60, spotdl_python=sys.executable, app_python=sys.executable, deno=None,
        )
        self.runner = runner
        self.sent = []
        self.notify = mock.patch.object(notify, "send", side_effect=lambda hook, lines: self.sent.append(lines))
        self.notify.start()
        self.manager = self._manager()

    def _manager(self):
        manager = JobManager(self.settings)
        manager._runner_command = lambda job, job_dir, url=None: ([sys.executable, str(self.runner)],
                                                                  {"url": url or job.url})
        manager.start()
        return manager

    def tearDown(self):
        self.manager.stop()
        self.notify.stop()
        self.tmp.cleanup()

    def _submit(self, mode):
        found = detect.Detection("ytdlp", "YouTube", "playlist", f"https://youtube.com/{mode}")
        return self.manager.submit(found, options.parse("ytdlp", {}))

    def _wait(self, job_id, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.manager.get(job_id)
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(0.05)
        self.fail(f"job still {job['status']}")

    def test_done_files_delivered_and_tidy(self):
        job = self._wait(self._submit("ok")["id"])
        self.assertEqual(job["status"], "done")
        self.assertEqual((job["title"], job["total"], job["completed"], job["progress"]), ("Album", 3, 3, 1.0))
        self.assertEqual(sorted(os.listdir(self.settings.inbox_dir)),
                         ["Artist - Song 0.opus", "Artist - Song 1.opus", "Artist - Song 2.opus"])
        self.assertTrue(all(item["delivered"] for item in job["files"]))
        job_dir = self.settings.jobs_dir / job["id"]
        self.assertEqual(sorted(os.listdir(job_dir)),
                         ["Artist - Song 0.opus", "Artist - Song 1.opus", "Artist - Song 2.opus", "job.json"])
        path, name = self.manager.file(job["id"], job["files"][0]["id"])
        self.assertEqual(name, "Artist - Song 0.opus")
        self.assertTrue(path.is_file())

        # The same songs again land beside the first copies, never over them.
        second = self._wait(self._submit("ok")["id"])
        self.assertEqual(second["status"], "done")
        self.assertIn("Artist - Song 0 (2).opus", os.listdir(self.settings.inbox_dir))

        archive, archive_name = self.manager.archive(job["id"])
        self.assertEqual(archive_name, "Album.zip")
        with zipfile.ZipFile(archive) as opened:
            self.assertEqual(len(opened.namelist()), 3)

    def test_partial(self):
        job = self._wait(self._submit("partial")["id"])
        self.assertEqual(job["status"], "partial")
        self.assertEqual(job["completed"], 2)
        self.assertEqual([(e["name"], e["message"], e["state"]) for e in job["errors"]],
                         [("song 1", "No matching song was found to download.", "failed")])

    def test_fatal_and_crash(self):
        job = self._wait(self._submit("fatal")["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("403", job["message"])
        self.assertEqual(job["errors"], [])
        job = self._wait(self._submit("crash")["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("something broke", job["message"])

    def test_cancel_running(self):
        job = self._submit("slow")
        deadline = time.monotonic() + 10
        while self.manager.get(job["id"])["status"] != "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
        self.manager.cancel(job["id"])
        self.assertEqual(self._wait(job["id"])["status"], "cancelled")

    def test_bad_ids(self):
        for bad in ("../../etc", "", "x" * 32):
            with self.assertRaises(JobError):
                self.manager.get(bad)
        job = self._wait(self._submit("ok")["id"])
        with self.assertRaises(JobError):
            self.manager.file(job["id"], "../job.json")

    def test_delete_and_expire(self):
        job = self._wait(self._submit("ok")["id"])
        self.assertEqual(self.manager.expire(now=time.time() + 3600), 0)
        self.assertEqual(self.manager.expire(now=time.time() + 25 * 3600), 1)
        self.assertFalse((self.settings.jobs_dir / job["id"]).exists())
        # The library keeps its copies; only the download links expire.
        self.assertEqual(len(os.listdir(self.settings.inbox_dir)), 3)
        other = self._wait(self._submit("ok")["id"])
        self.manager.delete(other["id"])
        with self.assertRaises(JobError):
            self.manager.get(other["id"])

    def test_restart(self):
        job = self._wait(self._submit("ok")["id"])
        state = self.settings.jobs_dir / job["id"] / "job.json"
        data = json.loads(state.read_text())
        data["status"] = "running"
        state.write_text(json.dumps(data))
        self.manager.stop()
        self.manager = self._manager()
        reloaded = self.manager.get(job["id"])
        self.assertEqual(reloaded["status"], "partial")
        self.assertIn("restarted", reloaded["message"])


    def _spotify(self, mode):
        found = detect.Detection("spotdl", "Spotify", "album", f"https://open.spotify.com/album/{mode}")
        return self.manager.submit(found, options.parse("spotdl", {}))

    def _try_again(self, job_id, timeout=20):
        """Presses "Retry now" and waits for that try to end."""
        self.manager.retry(job_id, 1)
        self.manager._queue_due_retries()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.manager.get(job_id)
            if not any(e["state"] in ("queued", "retrying") for e in job["errors"]):
                return job
            time.sleep(0.05)
        self.fail("retry did not end")

    def test_a_missing_song_waits_for_a_later_try_and_discord_hears(self):
        job = self._wait(self._spotify("flaky")["id"])
        self.assertEqual(job["status"], "partial")
        [error] = job["errors"]
        self.assertEqual((error["state"], error["tries"], error["max_tries"]), ("waiting", 1, MAX_TRIES))
        self.assertAlmostEqual(error["next_retry_at"], time.time() + 600, delta=30)
        self.assertEqual(error["url"], "https://open.spotify.com/track/comesback")
        self.assertEqual(job["notes"][0]["name"], "Artist - Song 0")
        [message] = self.sent
        self.assertIn("1 of 2 songs", message[0])
        self.assertTrue(any("Artist - Song 1" in line and "Trying again in 10 min" in line for line in message))
        self.assertTrue(any("another song" in line for line in message))
        # Not removed while a try is pending, whatever its age.
        self.assertEqual(self.manager.expire(now=time.time() + 25 * 3600), 0)

    def test_a_later_try_offers_the_song(self):
        job = self._wait(self._spotify("flaky")["id"])
        job = self._try_again(job["id"])
        self.assertEqual((job["status"], job["errors"], job["completed"]), ("done", [], 2))
        self.assertIn("Artist - Song 1.opus", os.listdir(self.settings.inbox_dir))
        path, _ = self.manager.file(job["id"], next(f["id"] for f in job["files"] if f["title"] == "Song 1"))
        self.assertTrue(path.is_file())
        self.assertIn("got Artist - Song 1 on try 2", self.sent[-1][0])

    def test_gives_up_after_the_last_try_and_says_so(self):
        job = self._wait(self._spotify("lost")["id"])
        for _ in range(MAX_TRIES - 1):
            job = self._try_again(job["id"])
        [error] = job["errors"]
        self.assertEqual((error["state"], error["tries"], error["next_retry_at"]), ("gave_up", MAX_TRIES, None))
        self.assertIn("gave up on Artist - Song 1", self.sent[-1][0])
        self.assertEqual(self.manager.expire(now=time.time() + 25 * 3600), 1)

    def test_no_retry_while_running_or_for_unknown_songs(self):
        job = self._wait(self._submit("partial")["id"])
        with self.assertRaises(JobError):
            self.manager.retry(job["id"], 1)  # no link of its own


if __name__ == "__main__":
    unittest.main()
