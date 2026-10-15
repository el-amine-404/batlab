import contextlib
import http.server
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extras


class Arr:
    def __init__(self):
        self.posts = []
        self.episode = {"seasonNumber": 0, "episodeNumber": 24, "tvdbId": 8147465, "hasFile": False}

    def get(self, endpoint, **kwargs):
        if endpoint == "series":
            return [{"id": 4, "tvdbId": 267440, "title": "Attack on Titan", "path": "/data/media/tv/Attack on Titan"}]
        return [self.episode]

    def post(self, endpoint, body):
        self.posts.append(body)


class Qbit:
    def __init__(self):
        self.progress = 1
        self.state = "downloading"

    def get(self, endpoint, **kwargs):
        if endpoint == "torrents/info":
            return [{"content_path": "/data/torrents/tv/Pack", "state": self.state, "hash": "123"}]
        return [{"name": "Pack/Extras/chibi.mkv", "progress": self.progress, "size": 5}]


class ExtrasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = self.root / "media/tv/Attack on Titan"
        self.library.mkdir(parents=True)
        self.source = self.root / "torrents/tv/Pack/Extras/chibi.mkv"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"video")
        self.entry = {"source": "Extras/chibi.mkv", "destination": "Season 00/AoT S00E24.mkv",
                      "episode": 24, "episode_tvdb_id": 8147465, "title": "Chibi & Levi"}
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps([{"source": "Pack", "tvdb_id": 267440, "files": [self.entry]}]))
        self.env = self.root / "env"
        self.env.write_text("")
        self.state = self.root / "state"
        self.arr, self.qbit = Arr(), Qbit()
        self.refreshes = []
        self.refresh = lambda *args: self.refreshes.append(args)
        self.destination = self.library / self.entry["destination"]

    def run_import(self, apply=True):
        with contextlib.redirect_stdout(io.StringIO()):
            return extras.run(self.arr, self.root, self.env, self.state, apply, self.config,
                              self.qbit, self.refresh)

    def test_complete_file_in_incomplete_pack_and_repeat(self):
        self.assertEqual(self.run_import(), 0)
        self.assertTrue(os.path.samefile(self.source, self.destination))
        self.assertEqual(self.source.stat().st_nlink, 2)
        metadata = ET.parse(self.destination.with_suffix(".nfo"))
        self.assertEqual(metadata.findtext("title"), "Chibi & Levi")
        self.assertEqual(metadata.findtext("uniqueid"), "8147465")
        self.assertEqual(metadata.findtext("showtitle"), "Attack on Titan")
        self.assertEqual(self.arr.posts, [{"name": "RescanSeries", "seriesId": 4}])
        self.arr.episode["hasFile"] = True
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.source.stat().st_nlink, 2)
        self.assertEqual(len(self.arr.posts), 1)
        self.assertEqual(len(self.refreshes), 1)

    def test_dry_run_does_not_write_or_refresh(self):
        self.assertEqual(self.run_import(False), 0)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.destination.parent.exists())
        self.assertEqual(self.arr.posts, [])
        self.assertEqual(self.refreshes, [])

    def test_partial_and_rechecking_files_wait(self):
        self.qbit.progress = 0.9999
        self.assertEqual(self.run_import(), 0)
        self.assertFalse(self.destination.exists())
        self.qbit.progress = 1
        self.qbit.state = "checkingUP"
        self.assertEqual(self.run_import(), 0)
        self.assertFalse(self.destination.exists())

    def test_existing_destination_is_never_overwritten(self):
        self.destination.parent.mkdir()
        self.destination.write_bytes(b"other")
        self.assertEqual(self.run_import(), 1)
        self.assertEqual(self.destination.read_bytes(), b"other")
        self.assertEqual(self.source.read_bytes(), b"video")

    def test_existing_sonarr_episode_is_kept(self):
        self.arr.episode["hasFile"] = True
        self.assertEqual(self.run_import(), 0)
        self.assertFalse(self.destination.exists())

    def test_metadata_identity_must_match(self):
        self.arr.episode["tvdbId"] = 99
        self.assertEqual(self.run_import(), 1)
        self.assertFalse(self.destination.exists())

    def test_failed_refresh_retries_without_copying_again(self):
        def fail(*args):
            raise urllib.error.HTTPError("Jellyfin", 401, "Unauthorized", {}, None)
        self.refresh = fail
        self.assertEqual(self.run_import(), 1)
        self.arr.episode["hasFile"] = True
        self.assertEqual(json.loads((self.state / "extras.json").read_text())["jellyfin"], [267440])
        self.refresh = lambda *args: self.refreshes.append(args)
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.source.stat().st_nlink, 2)
        self.assertEqual(json.loads((self.state / "extras.json").read_text())["jellyfin"], [])

    def test_missing_metadata_recovered(self):
        self.destination.parent.mkdir()
        os.link(self.source, self.destination)
        self.arr.episode["hasFile"] = True
        self.assertEqual(self.run_import(), 0)
        self.assertTrue(self.destination.with_suffix(".nfo").is_file())
        self.assertEqual(len(self.refreshes), 1)

    def test_symlink_and_traversal_refused(self):
        for rel in ("../escape", "/tmp/escape"):
            with self.assertRaises(ValueError):
                extras.contained(self.root, rel)
        (self.library / "Season 00").symlink_to(self.source.parent)
        self.assertEqual(self.run_import(), 1)
        self.assertFalse((self.source.parent / self.destination.name).exists())

    def test_incomplete_config_entry_is_refused(self):
        self.config.write_text(json.dumps([{"source": "Pack", "files": [self.entry]}]))
        with self.assertRaisesRegex(ValueError, "incomplete entry"):
            extras.load_config(self.config)

    def test_jellyfin_refresh_finds_the_show_by_tvdb_id(self):
        calls = []

        class Jellyfin(http.server.BaseHTTPRequestHandler):
            def answer(self, body=b""):
                calls.append((self.command, self.path, self.headers["Authorization"]))
                self.send_response(200 if body else 204)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                # Every show, as Jellyfin 10.11 answers whatever the filter.
                self.answer(json.dumps({"Items": [{"Id": "a", "ProviderIds": {"Tvdb": "1"}},
                                                  {"Id": "b", "ProviderIds": {"Tvdb": "267440"}}]}).encode())

            def do_POST(self):
                self.answer()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Jellyfin)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        extras.refresh_jellyfin(f"http://127.0.0.1:{server.server_port}", "k", [267440])
        self.assertEqual(calls[1][0], "POST")
        self.assertTrue(calls[1][1].startswith("/Items/b/Refresh"))
        self.assertEqual(calls[0][2], 'MediaBrowser Token="k"')
        with self.assertRaisesRegex(ValueError, "exactly one show"):
            extras.refresh_jellyfin(f"http://127.0.0.1:{server.server_port}", "k", [999])

    def test_reviewed_config_has_nine_unique_specials(self):
        entries = [entry for pack in extras.load_config(extras.CONFIG) for entry in pack["files"]]
        self.assertEqual(sorted(e["episode"] for e in entries if "episode" in e), list(range(24, 31)) + [36, 37])


if __name__ == "__main__":
    unittest.main()
