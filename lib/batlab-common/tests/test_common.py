import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from batlab_common import fingerprint, http as fetch, notify, titles  # noqa: E402


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *args], check=True)


class Titles(unittest.TestCase):
    def test_one_title(self):
        self.assertTrue(titles.same_title("PIRATE (feat. X) [Prod. Y]", "PIRATE"))
        self.assertTrue(titles.same_title("Mômes", "Momes"))
        self.assertFalse(titles.same_title("PIRATE", "KATANA"))

    def test_artists_in_common(self):
        self.assertEqual(titles.names("Manal & Stormy"), {"manal", "stormy"})
        self.assertTrue(titles.agrees("PIRATE", "Stormy", "PIRATE", "Manal & Stormy"))
        self.assertTrue(titles.contradicts("PIRATE", "Stormy", "Just TEN", "Thedjnino"))
        self.assertFalse(titles.contradicts("PIRATE (Remix)", "Someone", "PIRATE", "Stormy"))

    def test_versions(self):
        self.assertEqual(titles.version("PIRATE"), ())
        self.assertEqual(titles.version("PIRATE (Club Mix)"), ("remix",))
        self.assertEqual(titles.version("PIRATE - Live at Bercy"), ("live",))
        self.assertEqual(titles.version("PIRATE (feat. X) [Sped Up]"), ("sped up",))
        for same in ("PIRATE (Original Mix)", "PIRATE (Radio Edit)", "PIRATE (Remastered 2011)",
                     "Live Forever", "PIRATE (Official Video)", "X (Delivered)"):
            self.assertEqual(titles.version(same), (), same)
        self.assertFalse(titles.same_version("PIRATE", "PIRATE (Remix)"))


class Fingerprints(unittest.TestCase):
    def test_similarity_finds_the_preview_inside_the_song(self):
        song = [(i * 2654435761) & 0xFFFFFFFF for i in range(300)]
        self.assertEqual(fingerprint.similarity(song[100:140], song), 1.0)
        other = [(i * 40503 + 7) & 0xFFFFFFFF for i in range(40)]
        self.assertLess(fingerprint.similarity(other, song), fingerprint.SAME)
        self.assertEqual(fingerprint.similarity([], song), 0.0)

    def test_the_same_song_from_another(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg not installed")
        workdir = tempfile.mkdtemp()
        try:
            song, clip, other = (os.path.join(workdir, n) for n in ("song.flac", "clip.mp3", "other.flac"))
            # Two different melodies: Chromaprint hears pitch, so noise
            # against noise would look alike (0.76) without being a song.
            ffmpeg("-f", "lavfi", "-i", "aevalsrc=0.4*sin(2*PI*(262+131*floor(mod(t*2\\,4)))*t)"
                   "+0.2*sin(2*PI*(392+98*floor(mod(t*3\\,5)))*t):d=60:s=44100", song)
            ffmpeg("-ss", "20", "-t", "30", "-i", song, clip)
            ffmpeg("-f", "lavfi", "-i", "aevalsrc=0.4*sin(2*PI*(311+155*floor(mod(t*5\\,3)))*t)"
                   "+0.2*sin(2*PI*(466+117*floor(mod(t*1.5\\,6)))*t):d=30:s=44100", other)
            prints = {name: fingerprint.fingerprint(path) for name, path in
                      (("song", song), ("clip", clip), ("other", other))}
            self.assertGreaterEqual(fingerprint.similarity(prints["clip"], prints["song"]), fingerprint.SAME)
            self.assertLess(fingerprint.similarity(prints["other"], prints["song"]), fingerprint.OTHER)
        finally:
            shutil.rmtree(workdir)


class Apprise(http.server.BaseHTTPRequestHandler):
    """Apprise's /notify/<key>: answers what the test sets."""
    status = 200
    received = []

    def do_POST(self):
        self.received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if self.status >= 400:
            # Apprise 2: nothing tried when no URL has the tag, else what each did.
            tried = [] if self.received[-1][1].get("tag") == "nowhere" else [["ERROR", "…", "Discord said no"]]
            self.wfile.write(json.dumps({"error": "Discord said no" if tried else "One or more notification could "
                                         "not be sent", "details": tried}).encode())

    def log_message(self, *args):
        pass


class Notify(unittest.TestCase):
    def setUp(self):
        Apprise.status, Apprise.received = 200, []
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Apprise)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/notify/batlab"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_a_message_with_its_tag(self):
        notify.send(self.endpoint, "Ready to play", "**Trop beau**", tag="downloads", kind="success")
        path, body = Apprise.received[0]
        self.assertEqual(path, "/notify/batlab")
        self.assertEqual(body, {"title": "Ready to play", "body": "**Trop beau**", "type": "success",
                                "format": "markdown", "tag": "downloads"})

    def test_failures_say_why(self):
        Apprise.status = 424
        with self.assertRaisesRegex(notify.NotSent, "every destination: Discord said no"):
            notify.send(self.endpoint, "t", "b", tag="downloads")
        with self.assertRaisesRegex(notify.NotSent, "no destination for the tag 'nowhere'"):
            notify.send(self.endpoint, "t", "b", tag="nowhere")
        with self.assertRaisesRegex(notify.NotSent, "Couldn't reach Apprise"):
            notify.send("http://127.0.0.1:9/notify/batlab", "t", "b", timeout=2)

    def test_a_sender_has_its_own_address(self):
        base = self.endpoint.rsplit("/", 1)[0]
        self.assertEqual(notify.deliver("updater", "updates", "Updated x", "b", "success", base=base), "apprise")
        self.assertEqual(Apprise.received[0][0], "/notify/updater")
        self.assertEqual(notify.sender("watchdog", host="lab2")["name"], "watchdog on lab2")
        self.assertEqual(notify.sender("someone")["name"], "someone")

    def test_apprise_down_goes_straight_to_discord(self):
        hook = self.endpoint.replace("/notify/batlab", "/api/webhooks/1/x")
        how = notify.deliver("netdata", "alerts", "Disk full", notify.fields_text([("Value", "98%")]), "failure",
                             fallback=hook, base="http://127.0.0.1:9/notify", timeout=2)
        self.assertEqual(how, "discord")
        path, posted = Apprise.received[0]
        self.assertEqual(path, "/api/webhooks/1/x")
        self.assertTrue(posted["username"].startswith("netdata on "))
        card = posted["embeds"][0]
        self.assertEqual((card["title"], card["description"], card["color"], card["url"]),
                         ("Disk full", "**Value**\n98%", 15158332, "http://netdata.homelab.lan"))

    def test_no_fallback_when_apprise_answered(self):
        Apprise.status = 424
        base = self.endpoint.rsplit("/", 1)[0]
        with self.assertRaisesRegex(notify.NotSent, "every destination"):
            notify.deliver("updater", "updates", "t", "b", fallback=base + "/api/webhooks/1/x", base=base)
        self.assertEqual(len(Apprise.received), 1)


class Spacing(unittest.TestCase):
    def test_requests_to_one_host_are_spaced(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), type("H", (http.server.BaseHTTPRequestHandler,), {
            "do_GET": lambda self: (self.send_response(200), self.end_headers(), self.wfile.write(b"ok")),
            "log_message": lambda self, *a: None}))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/"
        try:
            start = time.monotonic()
            for _ in range(3):
                self.assertEqual(fetch.get(url, 10, spacing=0.2), b"ok")
            self.assertGreaterEqual(time.monotonic() - start, 0.4)
            with self.assertRaises(fetch.TooLarge):
                fetch.get(url, 1)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
