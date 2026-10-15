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
# batlab_common: in the image on PYTHONPATH, in the repository in lib/.
sys.path.insert(1, str(Path(__file__).resolve().parents[5] / "lib" / "batlab-common"))

from batlab_common import fingerprint, notify, previews  # noqa: E402

from app import connections, cookies, detect, events, folders, messages, options, outbound, preferences, problems  # noqa: E402
from app.config import Settings  # noqa: E402
from app.jobs import JobError, JobManager  # noqa: E402

MAX_TRIES = 4  # the default schedule: the first try, then 10 min, 1 h and 6 h

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
        self.assertGreater(fingerprint.similarity(fingerprint.fingerprint(preview), fingerprint.fingerprint(self.song)),
                           0.85)

    def test_other_audio_scores_low(self):
        other = os.path.join(self.dir.name, "other.mp3")
        _tone(other, "-af", "aeval=sin(2*PI*330*t)*0.5|sin(2*PI*330*t)*0.5")
        self.assertLess(fingerprint.similarity(fingerprint.fingerprint(other), fingerprint.fingerprint(self.song)),
                        verify.OTHER)

    def test_no_preview_is_no_verdict(self):
        with mock.patch.object(previews, "spotify", return_value=None):
            self.assertIsNone(verify.score(self.song, "4MjhDzeuvIU3f4E8SqGH4q"))
        with mock.patch.object(previews, "spotify", side_effect=OSError("offline")):
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
                         {"title": "EL MEJOR", "tracknumber": "4/12", "album": "DESPERADO", "artist": "Stormy"})
        beat_tape = {"title": "97SPECIAL - Escape [Full BeatTape]", "uploader": "STEEZYASFUCK"}
        self.assertEqual(ytdlp_runner.chapter_tags("Chasing A Dream", 1, 13, beat_tape),
                         {"title": "Chasing A Dream", "tracknumber": "1/13", "album": "Escape", "artist": "97SPECIAL"})
        self.assertEqual(ytdlp_runner.chapter_tags("One", 1, 2, {"title": "Summer mix", "uploader": "DJ"})["album"],
                         "Summer mix")
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
            port=0, state_dir=root / "state", output_root=root, output_folder="inbox", static_dir=root / "static",
            retention_hours=24, max_items=200, concurrent_jobs=1, spotdl_parallel=3,
            job_timeout_seconds=60, spotdl_python=sys.executable, app_python=sys.executable, deno=None,
        )
        self.runner = runner
        self.events = []
        self.manager = self._manager()

    def _manager(self):
        manager = JobManager(self.settings, emit=self.events.append)
        manager._runner_command = lambda job, job_dir, url=None: ([sys.executable, str(self.runner)],
                                                                  {"url": url or job.url})
        manager.start()
        return manager

    def tearDown(self):
        self.manager.stop()
        self.tmp.cleanup()

    def _sent(self, name):
        return [event for event in self.events if event.name == name]

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
        self.assertEqual(sorted(os.listdir(self.settings.output_dir)),
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
        self.assertIn("Artist - Song 0 (2).opus", os.listdir(self.settings.output_dir))

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
        self.assertEqual(len(os.listdir(self.settings.output_dir)), 3)
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

    def test_a_missing_song_waits_for_a_later_try_and_is_reported(self):
        job = self._wait(self._spotify("flaky")["id"])
        self.assertEqual(job["status"], "partial")
        [error] = job["errors"]
        self.assertEqual((error["state"], error["tries"], error["max_tries"]), ("waiting", 1, MAX_TRIES))
        self.assertAlmostEqual(error["next_retry_at"], time.time() + 600, delta=30)
        self.assertEqual(error["url"], "https://open.spotify.com/track/comesback")
        self.assertEqual(job["notes"][0]["name"], "Artist - Song 0")
        [event] = self._sent("job.incomplete")
        self.assertEqual(len(self._sent("job.finished")), 1)
        self.assertIn("1 of 2 songs", event.subject)
        self.assertTrue(any("Artist - Song 1" in line and "Trying again in 10 min" in line for line in event.lines))
        self.assertTrue(any("another song" in line for line in event.lines))
        self.assertEqual(event.data["job"]["id"], job["id"])
        # Not removed while a try is pending, whatever its age.
        self.assertEqual(self.manager.expire(now=time.time() + 25 * 3600), 0)

    def test_a_later_try_offers_the_song(self):
        job = self._wait(self._spotify("flaky")["id"])
        job = self._try_again(job["id"])
        self.assertEqual((job["status"], job["errors"], job["completed"]), ("done", [], 2))
        self.assertIn("Artist - Song 1.opus", os.listdir(self.settings.output_dir))
        path, _ = self.manager.file(job["id"], next(f["id"] for f in job["files"] if f["title"] == "Song 1"))
        self.assertTrue(path.is_file())
        self.assertIn("got Artist - Song 1 on try 2", self._sent("song.recovered")[-1].subject)

    def test_gives_up_after_the_last_try_and_says_so(self):
        job = self._wait(self._spotify("lost")["id"])
        for _ in range(MAX_TRIES - 1):
            job = self._try_again(job["id"])
        [error] = job["errors"]
        self.assertEqual((error["state"], error["tries"], error["next_retry_at"]), ("gave_up", MAX_TRIES, None))
        self.assertIn("gave up on Artist - Song 1", self._sent("song.gave_up")[-1].subject)
        self.assertEqual(self.manager.expire(now=time.time() + 25 * 3600), 1)

    def test_no_retry_while_running_or_for_unknown_songs(self):
        job = self._wait(self._submit("partial")["id"])
        with self.assertRaises(JobError):
            self.manager.retry(job["id"], 1)  # no link of its own

    def test_settings_apply_while_running(self):
        changed = preferences.update(self.settings, self.settings.state_dir,
                                     {"deliver": False, "retry_minutes": [], "retention_hours": 2})
        self.manager.settings = changed
        job = self._wait(self._spotify("lost")["id"])
        self.assertEqual(os.listdir(self.settings.output_dir), [], "not delivered")
        self.assertFalse(job["files"][0]["delivered"])
        [error] = job["errors"]
        self.assertEqual((error["state"], error["max_tries"]), ("gave_up", 1), "no later tries")
        self.assertEqual(self.manager.expire(now=time.time() + 3 * 3600), 1)

    def test_output_folder_is_made_when_needed(self):
        self.manager.settings = preferences.update(self.settings, self.settings.state_dir,
                                                   {"output_folder": "music/new place"})
        self._wait(self._submit("ok")["id"])
        self.assertEqual(len(os.listdir(self.settings.output_root / "music" / "new place")), 3)

    def test_the_caller_is_told_when_its_job_ends(self):
        calls = []
        with mock.patch.object(outbound, "post_json", side_effect=lambda url, payload: calls.append((url, payload))):
            found = detect.Detection("ytdlp", "YouTube", "playlist", "https://youtube.com/ok")
            job = self.manager.submit(found, options.parse("ytdlp", {}), "http://browser.lan/hook")
            self._wait(job["id"])
            time.sleep(0.2)
        [(url, payload)] = calls
        self.assertEqual((url, payload["event"], payload["job"]["status"]), ("http://browser.lan/hook", "job.updated", "done"))

    def test_only_the_set_number_of_jobs_runs_at_once(self):
        first = self._submit("slow")
        second = self._submit("ok")
        time.sleep(1)
        self.assertEqual(self.manager.get(second["id"])["status"], "queued")
        self.manager.settings = preferences.update(self.settings, self.settings.state_dir, {"concurrent_jobs": 2})
        self._wait(second["id"])
        self.manager.cancel(first["id"])


class PreferencesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        (self.state / "out").mkdir()
        self.base = Settings(port=0, state_dir=self.state, output_root=self.state / "out", output_folder="music/songdrop",
                             static_dir=self.state, retention_hours=24, max_items=200, concurrent_jobs=1,
                             spotdl_parallel=3, job_timeout_seconds=10800, spotdl_python="", app_python="", deno=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_saved_settings_win_and_survive_a_restart(self):
        preferences.update(self.base, self.state, {"retention_hours": 48, "output_folder": "/music/elsewhere/"})
        again = preferences.apply(self.base, preferences.stored(self.state))
        self.assertEqual((again.retention_hours, again.output_folder), (48, "music/elsewhere"))
        self.assertEqual(again.max_items, 200, "untouched: the environment's")
        self.assertEqual(os.stat(self.state / preferences.FILE).st_mode & 0o777, 0o600)

    def test_bad_settings_refused_with_a_reason(self):
        for bad, words in (({"output_folder": "../etc"}, "inside the output folder"),
                           ({"output_folder": "/"}, "inside the output folder"),
                           ({"retry_minutes": [0]}, "between 1 minute"),
                           ({"discord_webhook": "https://discord.com/api/webhooks/1/x"}, "Discord webhook isn't"),
                           ({"concurrent_jobs": 9}, "Concurrent jobs"),
                           ({"defaults": {"ytdlp": {"format": "wav"}}}, "default download options"),
                           ({"port": 80}, "Port")):
            with self.assertRaises(preferences.Invalid, msg=bad) as caught:
                preferences.update(self.base, self.state, bad)
            self.assertIn(words, str(caught.exception))
            self.assertTrue(str(caught.exception).endswith("."), str(caught.exception))
        self.assertFalse((self.state / preferences.FILE).exists())

    def test_saved_defaults_are_used_by_jobs_and_shown(self):
        changed = preferences.update(self.base, self.state, {"defaults": {"ytdlp": {"format": "mp3"}}})
        self.assertEqual(options.with_defaults("ytdlp", changed.defaults).format, "mp3")
        self.assertEqual(options.with_defaults("ytdlp", changed.defaults, {"format": "m4a"}).format, "m4a")
        described = {f["key"]: f["default"] for f in options.describe(changed.defaults)["engines"]["ytdlp"]["fields"]}
        self.assertEqual(described["format"], "mp3")


HOOK = "https://discord.com/api/webhooks/123456789/secret-token"


class ConnectionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.sent = []
        self.post = mock.patch.object(outbound, "post_json", side_effect=lambda url, body: self.sent.append((url, body)))
        self.post.start()
        self.store = connections.Connections(self.state, connections.TYPES)

    def tearDown(self):
        self.post.stop()
        self.tmp.cleanup()

    def _wait_sent(self, count):
        deadline = time.monotonic() + 5
        while len(self.sent) < count and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.sent

    def test_secrets_are_never_shown_and_kept_when_left_out(self):
        made = self.store.create({"name": "Music", "type": "discord", "config": {"webhook_url": HOOK}})
        self.assertNotIn("secret-token", json.dumps(self.store.list()))
        self.assertEqual((made["secrets_set"], made["summary"]), (["webhook_url"], "Webhook …6789"))
        self.assertEqual(made["events"], list(events.DEFAULTS))
        changed = self.store.update(made["id"], {"name": "Renamed", "type": "discord", "events": ["job.finished"],
                                                 "config": {"webhook_url": ""}})
        self.assertEqual((changed["name"], changed["events"]), ("Renamed", ["job.finished"]))
        again = connections.Connections(self.state, connections.TYPES)  # a restart
        self.store = again
        again.emit(events.Event("job.finished", "songdrop · Album (3 of 3 songs)", ("line",)))
        [(url, body)] = self._wait_sent(1)
        self.assertEqual(url, HOOK)
        self.assertEqual(body["content"], "**songdrop · Album (3 of 3 songs)**\nline")
        self.assertEqual(os.stat(self.state / "connections.json").st_mode & 0o777, 0o600)

    def test_only_enabled_connections_that_want_the_event_get_it(self):
        self.store.create({"name": "A", "type": "webhook", "events": ["song.gave_up"],
                           "config": {"url": "http://my-app:8080/songdrop-events"}})
        self.store.create({"name": "B", "type": "webhook", "enabled": False, "events": ["job.finished"],
                           "config": {"url": "http://off.lan/hook"}})
        self.store.emit(events.Event("job.finished", "x"))
        self.store.emit(events.Event("song.gave_up", "songdrop · gave up", data={"job": {"id": "1"}}))
        [(url, body)] = self._wait_sent(1)
        time.sleep(0.1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual((url, body["source"], body["event"], body["data"]),
                         ("http://my-app:8080/songdrop-events", "songdrop", "song.gave_up", {"job": {"id": "1"}}))

    def test_bad_connections_refused_with_a_reason(self):
        for bad, words in (({"name": "x", "type": "discord", "config": {"webhook_url": "https://evil.example/h"}},
                            "isn't a Discord webhook"),
                           ({"name": "x", "type": "discord"}, "Webhook URL is needed"),
                           ({"name": "x", "type": "pigeon"}, "no connection type"),
                           ({"name": " ", "type": "webhook", "config": {"url": "http://a.lan"}}, "name"),
                           ({"name": "x", "type": "webhook", "events": ["job.exploded"], "config": {"url": "http://a"}},
                            "isn't one songdrop sends")):
            with self.assertRaises(connections.Invalid, msg=bad) as caught:
                self.store.create(bad)
            self.assertIn(words, str(caught.exception))
        self.assertEqual(self.store.list(), [])

    def test_a_test_message_says_whether_it_arrived(self):
        made = self.store.create({"name": "Music", "type": "discord", "config": {"webhook_url": HOOK}})
        result = self.store.test({"name": "Music", "type": "discord", "config": {}}, made["id"])
        self.assertEqual(result["ok"], True)
        self.assertEqual(self.sent[0][0], HOOK, "the saved secret")
        with mock.patch.object(outbound, "post_json", side_effect=outbound.SendFailed("discord.com answered 404.")):
            result = self.store.test({"name": "Music", "type": "discord", "config": {}}, made["id"])
        self.assertEqual(result, {"ok": False, "message": "discord.com answered 404."})

    def test_the_environment_names_the_first_connection(self):
        seed = connections.Seed("discord", "Discord", {"webhook_url": HOOK})
        store = connections.Connections(self.state / "fresh", connections.TYPES, seed=seed)
        [made] = store.list()
        self.assertEqual((made["type"], made["events"]), ("discord", list(events.DEFAULTS)))
        self.assertEqual(connections.Connections(self.state / "fresh", connections.TYPES, seed=None).list(),
                         [made], "kept, not made again")
        bad = connections.Seed("apprise", "Apprise", {"url": "http://apprise:8000/"})
        self.assertEqual(connections.Connections(self.state / "other", connections.TYPES, seed=bad).list(), [])

    def test_apprise_gets_the_event_with_its_tag_and_kind(self):
        sent = []
        made = self.store.create({"name": "Apprise", "type": "apprise", "events": ["song.gave_up"],
                                  "config": {"url": "http://apprise:8000/notify/batlab", "tag": "download-issues"}})
        self.assertEqual(made["summary"], "apprise:8000/notify/batlab · download-issues")
        with mock.patch.object(notify, "send", side_effect=lambda *a, **k: sent.append((a, k))):
            self.store.emit(events.Event("song.gave_up", "songdrop · gave up on PIRATE", ("3 tries",),
                                         url="http://songdrop.lan/activity/missing"))
            deadline = time.monotonic() + 5
            while not sent and time.monotonic() < deadline:
                time.sleep(0.02)
        [(args, kwargs)] = sent
        self.assertEqual(args, ("http://apprise:8000/notify/batlab", "songdrop · gave up on PIRATE",
                                "3 tries\n[Open in songdrop](http://songdrop.lan/activity/missing)"))
        self.assertEqual(kwargs, {"tag": "download-issues", "kind": "failure"})
        with mock.patch.object(notify, "send", side_effect=notify.NotSent("Apprise has no destination for the tag 'x'.")):
            result = self.store.test({"name": "Apprise", "type": "apprise", "config": made["config"]})
        self.assertEqual(result, {"ok": False, "message": "Apprise has no destination for the tag 'x'."})
        with self.assertRaisesRegex(connections.Invalid, "ends in /notify/"):
            self.store.create({"name": "x", "type": "apprise", "config": {"url": "http://apprise:8000/"}})


class FoldersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "output"
        (self.root / "music" / "songdrop").mkdir(parents=True)
        (self.root / "music" / ".hidden").mkdir()
        (self.root / "torrents").mkdir()
        (self.root / "file.txt").write_text("x")
        (self.root / "escape").symlink_to(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_folders_inside_the_root_only(self):
        top = folders.listing(self.root, "")
        self.assertEqual([f["name"] for f in top["folders"]], ["music", "torrents"])
        self.assertIsNone(top["parent"])
        inside = folders.listing(self.root, "/music/")
        self.assertEqual((inside["path"], inside["parent"], inside["folders"]),
                         ("music", "", [{"name": "songdrop", "path": "music/songdrop"}]))
        for bad in ("../", "music/../..", "escape", "nowhere"):
            with self.assertRaises(folders.Outside, msg=bad):
                folders.listing(self.root, bad)

    def test_checks_whether_songs_can_go_there(self):
        self.assertEqual(folders.check(self.root, "music/songdrop")["exists"], True)
        later = folders.check(self.root, "music/new/place")
        self.assertEqual((later["ok"], later["exists"]), (True, False))
        self.assertIn("made with the first song", later["message"])
        for bad in ("", "../x", "file.txt/x", "escape/x"):
            self.assertFalse(folders.check(self.root, bad)["ok"], bad)


class ProblemsTest(unittest.TestCase):
    def test_validation_errors_read_as_sentences(self):
        self.assertEqual(problems.explain([{"loc": ("body", "retention_hours"), "type": "less_than_equal",
                                            "msg": "Input should be less than or equal to 720"}]),
                         "Retention hours: input should be less than or equal to 720.")
        self.assertEqual(problems.explain([{"loc": ("body", "x"), "msg": "Value error, Say it like this."}]),
                         "Say it like this.")
        self.assertEqual(problems.explain([]), "That request wasn't understood.")


if __name__ == "__main__":
    unittest.main()
