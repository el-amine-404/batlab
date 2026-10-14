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

from app import detect, messages, options  # noqa: E402
from app.config import Settings  # noqa: E402
from app.jobs import JobError, JobManager  # noqa: E402


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
        self.manager = self._manager()

    def _manager(self):
        manager = JobManager(self.settings)
        manager._runner_command = lambda job, job_dir: ([sys.executable, str(self.runner)], {"url": job.url})
        manager.start()
        return manager

    def tearDown(self):
        self.manager.stop()
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
        self.assertEqual(job["errors"], [{"name": "song 1", "message": "No matching song was found to download."}])

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


if __name__ == "__main__":
    unittest.main()
