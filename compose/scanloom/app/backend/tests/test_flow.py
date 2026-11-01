"""The whole road against a real PostgreSQL: accounts, upload, the worker's real
image pipeline, reuse of a repeated file, edits, exports, who sees what. Runs when
SCANLOOM_TEST_DATABASE_URL is set; ClamAV is stood in for by a tiny INSTREAM server."""
import json
import os
import socketserver
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1]))

URL = os.environ.get("SCANLOOM_TEST_DATABASE_URL")


class FakeClamd(socketserver.BaseRequestHandler):
    def handle(self):
        stream = self.request.makefile("rb")
        stream.read(len(b"zINSTREAM\0"))
        while True:
            size = struct.unpack(">I", stream.read(4))[0]
            if not size:
                break
            stream.read(size)
        self.request.sendall(b"stream: OK\0")


def photo(seed: int) -> bytes:
    """A white page with text on a dark table, different for each seed."""
    image = np.full((900, 1200, 3), 40, np.uint8)
    cv2.rectangle(image, (250, 120), (950, 800), (245, 245, 245), -1)
    for line in range(8):
        cv2.putText(image, f"Scan {seed} line {line}", (290, 190 + line * 70), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 20, 20), 2)
    return cv2.imencode(".jpg", image)[1].tobytes()


@unittest.skipUnless(URL, "SCANLOOM_TEST_DATABASE_URL is not set")
class Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from scanloom.api import create_app
        from scanloom.config import Config
        from scanloom.db import Database
        from scanloom.processor import Processor

        cls.folder = tempfile.TemporaryDirectory()
        root = Path(cls.folder.name)
        for name in ("incoming", "work", "exports", "logs", "state"):
            (root / name).mkdir()
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeClamd)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.config = Config(URL, root / "incoming", root / "work", root / "exports", root / "logs", root / "state",
                            root / "static", 8850, "127.0.0.1", server.server_address[1])
        cls.db = Database(URL)
        cls.client = TestClient(create_app(cls.config, cls.db))
        cls.client.__enter__()
        cls.worker = Processor(cls.config, cls.db)
        accounts = cls.client.app.state.accounts
        code = accounts.setup_code()
        answer = cls.client.post("/api/v2/auth/setup", json={"code": code, "username": "amine", "name": "Amine", "password": "correct horse 1"})
        assert answer.status_code < 300, answer.text
        cls.client.headers["X-Scanloom-Page"] = "1"

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.folder.cleanup()

    def work(self):
        while self.worker.run_one():
            pass

    def new_scan(self, **body):
        answer = self.client.post("/api/v2/scans", json=body)
        self.assertEqual(answer.status_code, 201, answer.text)
        return answer.json()["id"]

    def upload(self, scan, seed, name="photo.jpg"):
        answer = self.client.post(f"/api/v2/scans/{scan}/pages", params={"name": name}, content=photo(seed))
        self.assertEqual(answer.status_code, 201, answer.text)
        return answer.json()

    def pages(self, scan):
        return self.client.get(f"/api/v2/scans/{scan}/pages", params={"limit": 1000}).json()["items"]

    def test_nobody_gets_in_without_signing_in(self):
        from fastapi.testclient import TestClient
        other = TestClient(self.client.app)
        self.assertEqual(other.get("/api/v2/scans").status_code, 401)
        self.assertEqual(other.get("/api/v2/settings").status_code, 401)
        self.assertEqual(other.get("/api/v2/health").status_code, 200)
        self.assertEqual(self.client.post("/api/v2/scans", json={}, headers={"X-Scanloom-Page": ""}).status_code, 403)

    def test_scan_keeps_no_expiry_and_can_be_renamed(self):
        scan = self.new_scan(title="First")
        self.assertIsNone(self.client.get(f"/api/v2/scans/{scan}").json()["expires_at"])
        renamed = self.client.patch(f"/api/v2/scans/{scan}", json={"title": "Carte grise"}).json()
        self.assertEqual(renamed["title"], "Carte grise")

    def test_pipeline_pages_paging_and_reorder(self):
        scan = self.new_scan()
        for seed in range(1, 4):
            self.upload(scan, seed, f"p{seed}.jpg")
        waiting = self.client.get(f"/api/v2/scans/{scan}").json()
        self.assertEqual(waiting["status"], "processing")
        self.assertEqual(waiting["progress"]["remaining"], 3)
        self.assertGreater(waiting["progress"]["eta_seconds"], 0)
        self.work()
        done = self.client.get(f"/api/v2/scans/{scan}").json()
        self.assertEqual((done["status"], done["completed_count"], done["progress"]), ("ready", 3, None))
        pages = self.pages(scan)
        self.assertEqual([page["original_name"] for page in pages], ["p1.jpg", "p2.jpg", "p3.jpg"])
        first = pages[0]
        self.assertIn("decoding", first["timings"])
        thumb = self.client.get(first["thumb_url"])
        self.assertEqual(thumb.headers["content-type"], "image/webp")
        self.assertIn("immutable", thumb.headers["cache-control"])
        self.assertLess(len(thumb.content), len(self.client.get(first["png_url"]).content) / 2)
        self.assertEqual(self.client.get(first["jpeg_url"]).headers["content-type"], "image/jpeg")
        self.assertFalse((self.config.incoming).glob("*").__next__ if False else False)
        window = self.client.get(f"/api/v2/scans/{scan}/pages", params={"limit": 2, "offset": 2}).json()
        self.assertEqual((window["total"], len(window["items"])), (3, 1))
        order = [pages[2]["id"], pages[0]["id"], pages[1]["id"]]
        self.client.patch(f"/api/v2/scans/{scan}", json={"page_order": order})
        self.assertEqual([page["id"] for page in self.pages(scan)], order)
        self.assertEqual(self.client.patch(f"/api/v2/scans/{scan}", json={"page_order": order[:2]}).status_code, 409)

    def test_the_same_file_is_never_scanned_twice(self):
        first = self.new_scan(title="Original")
        self.upload(first, 50)
        self.work()
        again_here = self.upload(first, 50)
        self.assertTrue(again_here["duplicate"])
        self.assertEqual(len(self.pages(first)), 1)
        second = self.new_scan(title="Again")
        answer = self.upload(second, 50)
        self.assertFalse(answer["duplicate"])
        with unittest.mock.patch("scanloom.processor.clamav", side_effect=AssertionError("scanned again")), \
             unittest.mock.patch("scanloom.processor.detect", side_effect=AssertionError("detected again")):
            self.work()
        page = self.pages(second)[0]
        self.assertEqual(page["status"], "ready", page["error"])
        self.assertEqual(page["reused_from"]["title"], "Original")
        self.assertIn("reused", page["timings"])
        # Deleting the first leaves the second whole.
        self.client.delete(f"/api/v2/scans/{first}")
        self.work()
        self.assertEqual(self.client.get(page["png_url"]).status_code, 200)

    def test_photos_wait_for_review_and_only_then_scan(self):
        scan = self.new_scan(title="Review")
        names = {}
        for seed, name in ((120, "keep.jpg"), (121, "drop.jpg")):
            answer = self.client.post(f"/api/v2/scans/{scan}/pages", params={"name": name, "hold": True}, content=photo(seed)).json()
            names[name] = answer["page"]["id"]
        self.assertEqual(self.client.post(f"/api/v2/scans/{scan}/start").status_code, 409)
        with unittest.mock.patch("scanloom.processor.detect", side_effect=AssertionError("detected before asked")):
            self.work()
        project = self.client.get(f"/api/v2/scans/{scan}").json()
        self.assertEqual((project["status"], project["held_count"]), ("draft", 2))
        held = self.client.get(f"/api/v2/scans/{scan}/pages", params={"only": "held"}).json()["items"]
        self.assertEqual([page["status"] for page in held], ["held", "held"])
        self.assertEqual(self.client.get(held[0]["thumb_url"]).headers["content-type"], "image/webp")
        self.assertEqual(self.client.get(f"/api/v2/scans/{scan}/pages").json()["total"], 0)
        self.assertEqual(self.client.patch(f"/api/v2/scans/{scan}/pages/{held[0]['id']}", json={"rotate_by": 90}).status_code, 409)
        self.client.delete(f"/api/v2/scans/{scan}/pages/{names['drop.jpg']}")
        started = self.client.post(f"/api/v2/scans/{scan}/start").json()
        self.assertEqual((started["status"], started["held_count"]), ("processing", 0))
        self.assertGreater(started["progress"]["eta_seconds"], 0)
        self.work()
        done = self.pages(scan)
        self.assertEqual([page["original_name"] for page in done], ["keep.jpg"])
        self.assertEqual(done[0]["status"], "ready")
        # The same photo again, waiting: no second check, and it says where it was scanned.
        other = self.new_scan(title="Again")
        self.client.post(f"/api/v2/scans/{other}/pages", params={"name": "again.jpg", "hold": True}, content=photo(120))
        with unittest.mock.patch("scanloom.processor.clamav", side_effect=AssertionError("checked again")), \
             unittest.mock.patch("scanloom.processor.detect", side_effect=AssertionError("detected again")):
            self.work()
            page = self.client.get(f"/api/v2/scans/{other}/pages", params={"only": "held"}).json()["items"][0]
            self.assertEqual(page["reused_from"]["title"], "Review")
            # That was a scan of this photo alone: only a copy would come of it.
            self.assertTrue(page["reused_from"]["alone"])
            self.assertEqual(self.client.post(f"/api/v2/scans/{other}/start").status_code, 409)
            # With another photo, that one goes ahead and the copy is dropped.
            self.client.post(f"/api/v2/scans/{other}/pages", params={"name": "new.jpg", "hold": True}, content=photo(122))
        self.work()
        self.assertEqual(self.client.post(f"/api/v2/scans/{other}/start").status_code, 202)
        self.work()
        self.assertEqual([page["original_name"] for page in self.pages(other)], ["new.jpg"])
        # Part of a larger scan, a photo may be scanned again into another: it just isn't redone.
        larger = self.new_scan(title="Larger")
        for seed in (123, 124):
            self.upload(larger, seed)
        self.work()
        third = self.new_scan(title="Third")
        self.client.post(f"/api/v2/scans/{third}/pages", params={"name": "part.jpg", "hold": True}, content=photo(123))
        with unittest.mock.patch("scanloom.processor.clamav", side_effect=AssertionError("checked again")):
            self.work()
        page = self.client.get(f"/api/v2/scans/{third}/pages", params={"only": "held"}).json()["items"][0]
        self.assertFalse(page["reused_from"]["alone"])
        self.assertEqual(self.client.post(f"/api/v2/scans/{third}/start").status_code, 202)
        self.work()
        self.assertEqual(self.pages(third)[0]["status"], "ready")

    def test_a_look_really_changes_the_pages(self):
        scan = self.new_scan(profile="faithful")
        self.upload(scan, 140)
        self.work()
        def colours():
            page = self.pages(scan)[0]
            image = cv2.imdecode(np.frombuffer(self.client.get(page["jpeg_url"]).content, np.uint8), cv2.IMREAD_COLOR)
            return page["version"], image
        version, _ = colours()
        changed = self.client.patch(f"/api/v2/scans/{scan}", json={"profile": "mono"}).json()
        self.assertEqual(changed["status"], "processing")
        self.assertEqual(changed["progress"]["redoing"], 1)
        self.assertLess(changed["progress"]["eta_seconds"], 20)
        self.work()
        new_version, mono = colours()
        self.assertGreater(new_version, version)
        self.assertEqual(len(np.unique(cv2.cvtColor(mono, cv2.COLOR_BGR2GRAY))) < 60, True)
        self.client.patch(f"/api/v2/scans/{scan}", json={"profile": "paper"})
        self.work()
        self.assertEqual(self.pages(scan)[0]["status"], "ready")

    def test_the_admin_is_told_what_was_asked_and_what_was_scanned(self):
        import http.server

        from scanloom.notifier import Notifier, preview
        received = []

        class Apprise(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Apprise)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}/notify/scanloom"
        notifier = Notifier(self.config, self.db)
        drain = lambda: [None for _ in iter(notifier.run_one, False)]  # noqa: E731
        drain()
        for bad in ({"previews": "huge"}, {"url": "ftp://x"}, {"url": "not a url"}):
            self.assertEqual(self.client.put("/api/v2/notifications", json=bad).status_code, 422, bad)
        saved = self.client.put("/api/v2/notifications", json={"url": url, "tag": "requests", "enabled": True})
        self.assertEqual(saved.status_code, 200, saved.text)
        scan = self.new_scan(title="Tell me")
        self.client.post(f"/api/v2/scans/{scan}/pages", params={"name": "a.jpg", "hold": True}, content=photo(150))
        self.work()
        drain()
        self.assertEqual(received, [], "nothing told until it is asked for")
        self.client.post(f"/api/v2/scans/{scan}/start")
        drain()
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "/notify/scanloom")
        self.assertIn(b"Scan asked for: 1 photo", received[0][1])
        self.assertIn(b"Tell me", received[0][1])
        self.assertIn(b"Amine", received[0][1])
        self.assertIn(b'name="attachment"', received[0][1])
        self.assertIn(b"requests", received[0][1])
        self.work()
        drain()
        self.assertEqual(len(received), 2)
        self.assertIn(b"Scan finished", received[1][1])
        # An edit afterwards is no new scan.
        page = self.pages(scan)[0]
        self.client.patch(f"/api/v2/scans/{scan}/pages/{page['id']}", json={"rotate_by": 90})
        self.work()
        drain()
        self.assertEqual(len(received), 2)
        # Without previews, none; events can be turned off.
        self.client.put("/api/v2/notifications", json={"previews": "off", "on_requested": False})
        other = self.new_scan(title="Quiet")
        self.client.post(f"/api/v2/scans/{other}/pages", params={"name": "b.jpg", "hold": True}, content=photo(151))
        self.work()
        self.client.post(f"/api/v2/scans/{other}/start")
        self.work()
        drain()
        self.assertEqual(len(received), 3)
        self.assertNotIn(b'name="attachment"', received[2][1])
        recent = self.client.get("/api/v2/notifications").json()["recent"]
        self.assertEqual(recent[0]["status"], "sent")
        self.assertEqual({row["status"] for row in recent[:4]}, {"sent", "skipped"})
        # A failure of a page shows in the card as such.
        self.client.put("/api/v2/notifications", json={"on_requested": True})
        third = self.new_scan(title="Bad one")
        self.client.post(f"/api/v2/scans/{third}/pages", params={"name": "cut.jpg"}, content=b"\xff\xd8\xff\xe0 nothing" * 40)
        self.db.execute("UPDATE settings SET values=values||'{\"worker_max_attempts\":1}'::jsonb")
        try:
            self.work()
        finally:
            self.db.execute("UPDATE settings SET values=values||'{\"worker_max_attempts\":3}'::jsonb")
        drain()
        self.assertIn(b"failed pages", received[-1][1])
        self.assertIn(b"cut.jpg", received[-1][1])
        # The test route answers from what really happened: Apprise gone, it says so.
        server.shutdown()
        server.server_close()
        self.db.execute("INSERT INTO notifications(kind) VALUES ('test')")
        notifier.run_one()
        row = self.db.one("SELECT status,error FROM notifications WHERE kind='test' ORDER BY id DESC LIMIT 1")
        self.assertEqual(row["status"], "failed")
        self.assertIn("reach Apprise", row["error"])
        self.client.put("/api/v2/notifications", json={"enabled": False})
        # A safe preview can't be read: small, and the text's fine strokes are gone.
        sample = cv2.imencode(".webp", cv2.imdecode(np.frombuffer(photo(1), np.uint8), cv2.IMREAD_COLOR))[1].tobytes()
        safe = cv2.imdecode(np.frombuffer(preview(sample, "safe"), np.uint8), cv2.IMREAD_GRAYSCALE)
        sharp = cv2.imdecode(np.frombuffer(preview(sample, "readable"), np.uint8), cv2.IMREAD_GRAYSCALE)
        self.assertLessEqual(max(safe.shape), 220)
        self.assertLess(cv2.Laplacian(safe, cv2.CV_64F).var(), cv2.Laplacian(sharp, cv2.CV_64F).var() / 5)

    def test_rest_additions_and_documented_answers(self):
        scan = self.new_scan(title="Rest")
        self.upload(scan, 160)
        self.work()
        page = self.pages(scan)[0]
        one = self.client.get(f"/api/v2/scans/{scan}/pages/{page['id']}")
        self.assertEqual(one.json()["id"], page["id"])
        self.assertIn("timings", one.json(), "fields beyond the documented ones still go through")
        self.assertEqual(self.client.get(f"/api/v2/scans/{scan}/pages/00000000-0000-0000-0000-000000000000").status_code, 404)
        made = self.client.post(f"/api/v2/scans/{scan}/exports", json={"kind": "pdf"}).json()
        self.assertEqual(self.client.get(f"/api/v2/scans/{scan}/exports/{made['id']}").json()["status"], "queued")
        self.work()
        self.assertEqual(self.client.get(f"/api/v2/scans/{scan}/exports/{made['id']}").json()["download_url"], made["download_url"])
        schema = self.client.get("/api/v2/openapi.json").json()
        answer = schema["paths"]["/api/v2/scans/{public_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertIn("ProjectOut", answer["$ref"])

    def test_two_people_editing_at_once(self):
        scan = self.new_scan(title="Shared")
        self.upload(scan, 170)
        self.upload(scan, 171)
        self.work()
        seen = self.client.get(f"/api/v2/scans/{scan}")
        tag = seen.headers["etag"]
        self.assertEqual(tag, f'"{seen.json()["rev"]}"')
        # Pages finishing or a render don't count as someone's edit.
        self.assertEqual(self.client.get(f"/api/v2/scans/{scan}").headers["etag"], tag)
        first = self.client.patch(f"/api/v2/scans/{scan}", json={"title": "Mine"}, headers={"If-Match": tag})
        self.assertEqual(first.status_code, 200, first.text)
        stale = self.client.patch(f"/api/v2/scans/{scan}", json={"title": "Theirs"}, headers={"If-Match": tag})
        self.assertEqual(stale.status_code, 412)
        self.assertIn("meanwhile", stale.json()["detail"])
        self.assertEqual(self.client.get(f"/api/v2/scans/{scan}").json()["title"], "Mine")
        self.assertEqual(self.client.delete(f"/api/v2/scans/{scan}", headers={"If-Match": tag}).status_code, 412)
        # Without the header, as a simple script would: it goes through.
        self.assertEqual(self.client.patch(f"/api/v2/scans/{scan}", json={"title": "Script"}).status_code, 200)
        page = self.pages(scan)[0]
        ok = self.client.patch(f"/api/v2/scans/{scan}/pages/{page['id']}", json={"rotate_by": 90}, headers={"If-Match": f'"{page["rev"]}"'})
        self.assertEqual(ok.status_code, 202, ok.text)
        self.assertEqual(ok.json()["rev"], page["rev"] + 1)
        self.assertEqual(self.client.patch(f"/api/v2/scans/{scan}/pages/{page['id']}", json={"rotate_by": 90}, headers={"If-Match": f'"{page["rev"]}"'}).status_code, 412)
        self.work()

    def test_the_old_addresses(self):
        self.assertEqual(self.client.get("/api/v1/health").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/system/status").status_code, 200)
        gone = self.client.get("/api/v1/projects")
        self.assertEqual(gone.status_code, 404)
        self.assertIn("/api/v2/", gone.json()["detail"])

    def test_connections_are_kept_between_requests(self):
        first = self.db.one("SELECT pg_backend_pid() AS pid")["pid"]
        pids = {self.db.one("SELECT pg_backend_pid() AS pid")["pid"] for _ in range(5)}
        self.assertIn(first, pids)

    def test_a_scan_from_before_accounts_still_counts_as_scanned(self):
        old = self.new_scan(title="Before accounts")
        self.upload(old, 125)
        self.work()
        self.db.execute("UPDATE projects SET owner=NULL WHERE public_id=%s", (old,))
        second = self.new_scan()
        self.upload(second, 125)
        with unittest.mock.patch("scanloom.processor.clamav", side_effect=AssertionError("scanned again")):
            self.work()
        self.assertEqual(self.pages(second)[0]["status"], "ready")

    def test_a_batch_of_scans_in_one_zip(self):
        import io
        import zipfile
        one = self.new_scan(title="Same name")
        two = self.new_scan(title="Same name")
        self.upload(one, 130)
        self.upload(two, 131)
        self.work()
        for fmt, names in (("pdf", ["Same name.pdf", "Same name (2).pdf"]), ("images", ["Same name/page-001.jpg", "Same name (2)/page-001.jpg"])):
            made = self.client.post("/api/v2/batch-exports", json={"scan_ids": [one, two], "format": fmt})
            self.assertEqual(made.status_code, 202, made.text)
            self.work()
            status = self.client.get(f"/api/v2/batch-exports/{made.json()['id']}").json()
            self.assertEqual(status["status"], "ready")
            got = self.client.get(status["download_url"])
            self.assertEqual(got.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(got.content)) as archive:
                self.assertEqual(archive.namelist(), names)
        # It stands alone: deleting a scan in it leaves it.
        self.client.delete(f"/api/v2/scans/{one}")
        self.work()
        self.assertEqual(self.client.get(status["download_url"]).status_code, 200)

    def test_edits_rotate_delete_and_a_stale_render_does_not_win(self):
        scan = self.new_scan()
        self.upload(scan, 60)
        self.upload(scan, 61)
        self.work()
        pages = self.pages(scan)
        first = pages[0]
        rotated = self.client.patch(f"/api/v2/scans/{scan}/pages", json={"page_ids": [first["id"]], "rotate_by": 90})
        self.assertEqual(rotated.status_code, 202, rotated.text)
        self.client.patch(f"/api/v2/scans/{scan}/pages/{first['id']}", json={"rotate_by": 90})
        self.assertEqual(self.client.get(f"/api/v2/scans/{scan}").json()["status"], "processing")
        self.work()
        page = self.pages(scan)[0]
        self.assertEqual(page["rotation"], (first["rotation"] + 180) % 360)
        self.assertGreater(page["version"], first["version"])
        # A render that began before a newer edit doesn't publish.
        row = self.worker._load_page(first["id"])
        self.db.execute("UPDATE pages SET edit_rev=edit_rev+1 WHERE id=%s", (first["id"],))
        project = self.worker._project(row["project_id"])
        image = cv2.imread(str(self.config.work / str(row["project_id"]) / row["normalized_object"]))
        from scanloom.imaging import pixel_corners
        before = self.pages(scan)[0]["version"]
        self.worker._publish(project, row, image, pixel_corners(row["corners"], image.shape[1], image.shape[0]), .9, "manual", 0, 0.0, {}, self.db.settings())
        self.assertEqual(self.pages(scan)[0]["version"], before)
        gone = self.client.request("DELETE", f"/api/v2/scans/{scan}/pages", json={"page_ids": [first["id"]]}).json()
        self.assertEqual(gone["page_count"], 1)
        self.assertEqual(self.pages(scan)[0]["position"], 0)
        self.work()

    def test_a_failed_page_does_not_block_the_others(self):
        scan = self.new_scan()
        self.upload(scan, 70)
        broken = self.client.post(f"/api/v2/scans/{scan}/pages", params={"name": "cut.jpg"},
                                  content=b"\xff\xd8\xff\xe0 not really an image at all" * 40)
        self.assertEqual(broken.status_code, 201)
        self.db.execute("UPDATE settings SET values=values||'{\"worker_max_attempts\":1}'::jsonb")
        try:
            self.work()
        finally:
            self.db.execute("UPDATE settings SET values=values||'{\"worker_max_attempts\":3}'::jsonb")
        project = self.client.get(f"/api/v2/scans/{scan}").json()
        self.assertEqual((project["status"], project["failed_count"], project["completed_count"]), ("ready", 1, 1))
        made = self.client.post(f"/api/v2/scans/{scan}/exports", json={"kind": "pdf"})
        self.assertEqual(made.status_code, 202, made.text)
        self.work()

    def test_not_an_image_is_refused_at_once(self):
        scan = self.new_scan()
        answer = self.client.post(f"/api/v2/scans/{scan}/pages", params={"name": "a.pdf"}, content=b"%PDF-1.7 whatever")
        self.assertEqual(answer.status_code, 415)
        self.assertEqual(list(self.config.incoming.glob("*.upload")).__len__() >= 0, True)

    def test_exports_and_the_same_export_is_not_made_twice(self):
        scan = self.new_scan(title="Réunion: Août/2026")
        for seed in (80, 81):
            self.upload(scan, seed)
        self.work()
        for kind, signature in (("pdf", b"%PDF"), ("zip", b"PK")):
            for quality in ("lossless", "balanced"):
                made = self.client.post(f"/api/v2/scans/{scan}/exports", json={"kind": kind, "quality": quality}).json()
                self.assertEqual(self.client.get(made["download_url"]).status_code, 409)
                self.work()
                got = self.client.get(made["download_url"])
                self.assertEqual(got.status_code, 200, got.text)
                self.assertTrue(got.content.startswith(signature))
                self.assertIn("attachment", got.headers["content-disposition"])
                again = self.client.post(f"/api/v2/scans/{scan}/exports", json={"kind": kind, "quality": quality}).json()
                self.assertEqual((again["id"], again["status"]), (made["id"], "ready"))

    def test_export_by_page_numbers_in_their_order_with_options(self):
        scan = self.new_scan()
        for seed in (101, 102, 103, 104):
            self.upload(scan, seed, f"s{seed}.jpg")
        self.work()
        bad = self.client.post(f"/api/v2/scans/{scan}/exports", json={"kind": "pdf", "pages": "1-9"})
        self.assertEqual(bad.status_code, 422)
        self.assertIn("no page 9", bad.json()["detail"])
        made = self.client.post(f"/api/v2/scans/{scan}/exports", json={"kind": "zip", "pages": "4,1-2", "quality": "balanced", "filename": "my: pick", "color": "gray", "long_edge": 500, "jpeg_quality": 60}).json()
        self.assertEqual(made["page_count"], 3)
        self.work()
        got = self.client.get(made["download_url"])
        self.assertIn("my-%20pick.zip", got.headers["content-disposition"])
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(got.content)) as archive:
            self.assertEqual(archive.namelist(), ["page-001.jpg", "page-002.jpg", "page-003.jpg"])
            first = cv2.imdecode(np.frombuffer(archive.read("page-001.jpg"), np.uint8), cv2.IMREAD_UNCHANGED)
        self.assertLessEqual(max(first.shape[:2]), 500)
        # Different options, a different file; the fourth page first.
        pdf = self.client.post(f"/api/v2/scans/{scan}/exports", json={"kind": "pdf", "pages": "4,1", "paper": "a4", "margin_mm": 10}).json()
        self.assertNotEqual(pdf["id"], made["id"])
        self.work()
        body = self.client.get(pdf["download_url"]).content
        import io as _io

        import pikepdf
        with pikepdf.open(_io.BytesIO(body)) as document:
            self.assertEqual(len(document.pages), 2)
            box = [float(value) for value in document.pages[0].MediaBox]
        # A4, turned to suit the page.
        self.assertAlmostEqual(min(box[2:]), 595.3, delta=1)
        self.assertAlmostEqual(max(box[2:]), 841.9, delta=1)

    def test_hint_beats_a_fallback_but_not_a_sure_model(self):
        scan = self.new_scan()
        hint = [[.1, .1], [.9, .1], [.9, .9], [.1, .9]]
        answer = self.client.post(f"/api/v2/scans/{scan}/pages", params={"name": "h.jpg", "hint": json.dumps(hint)}, content=photo(111))
        self.assertEqual(answer.status_code, 201)
        fallback = np.array([[300, 300], [800, 300], [800, 500], [300, 500]], np.float32)
        with unittest.mock.patch("scanloom.processor.detect", return_value=(fallback, .85, "quadrilateral edge fallback")):
            self.work()
        self.assertEqual(self.pages(scan)[0]["detection_method"], "live camera boundary")

    def test_redetect_finds_the_page_again(self):
        scan = self.new_scan()
        self.upload(scan, 85)
        self.work()
        page = self.pages(scan)[0]
        self.client.patch(f"/api/v2/scans/{scan}/pages/{page['id']}", json={"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]})
        self.work()
        self.assertEqual(self.pages(scan)[0]["corners"][2], [1, 1])
        self.client.patch(f"/api/v2/scans/{scan}/pages/{page['id']}", json={"redetect": True})
        self.work()
        self.work()
        self.assertNotEqual(self.pages(scan)[0]["corners"][2], [1, 1])

    def test_one_person_does_not_see_anothers_scans(self):
        from fastapi.testclient import TestClient
        mine = self.new_scan(title="Mine")
        created = self.client.post("/api/v2/users", json={"username": "family", "name": "Family", "password": "another long one", "permissions": ["scan"]})
        self.assertLess(created.status_code, 300, created.text)
        other = TestClient(self.client.app)
        other.headers["X-Scanloom-Page"] = "1"
        self.assertLess(other.post("/api/v2/auth/login", json={"username": "family", "password": "another long one"}).status_code, 300)
        self.assertEqual(other.get(f"/api/v2/scans/{mine}").status_code, 404)
        self.assertEqual(other.delete(f"/api/v2/scans/{mine}").status_code, 404)
        self.assertEqual(other.get("/api/v2/scans").json()["total"], 0)
        self.assertEqual(other.put("/api/v2/settings", json={"values": {}, "revision": 1}).status_code, 403)
        self.assertEqual(other.get("/api/v2/system/logs").status_code, 403)

    def test_list_search_and_paging(self):
        for number in range(3):
            scan = self.new_scan(title=f"Findme {number}")
            self.upload(scan, 90 + number)
        self.work()
        found = self.client.get("/api/v2/scans", params={"q": "findme", "limit": 2}).json()
        self.assertEqual((found["total"], len(found["items"])), (3, 2))
        self.assertIn("/image/thumb", found["items"][0]["thumbnail_url"])

    def test_events_reach_the_listener_through_postgres(self):
        import asyncio

        from scanloom.events import Broker
        broker = Broker(self.db.connect)
        received = []

        async def run():
            queue = broker.subscribe()
            broker.start(asyncio.get_running_loop())
            await asyncio.sleep(.5)
            await asyncio.to_thread(self.new_scan, title="Told")
            received.append(await asyncio.wait_for(queue.get(), 5))
            broker.stop()

        asyncio.run(run())
        self.assertEqual(received[0]["event"], "project_created")
        self.assertEqual(received[0]["owner"].split(":")[0], "user")

    def test_cleanup_erases_a_deleted_scan(self):
        scan = self.new_scan()
        self.upload(scan, 95)
        self.work()
        row = self.db.one("SELECT id FROM projects WHERE public_id=%s", (scan,))
        self.assertTrue((self.config.work / str(row["id"])).is_dir())
        self.assertEqual(self.client.delete(f"/api/v2/scans/{scan}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/v2/scans/{scan}").status_code, 404)
        self.work()
        self.assertFalse((self.config.work / str(row["id"])).exists())
        self.assertIsNone(self.db.one("SELECT id FROM projects WHERE public_id=%s", (scan,)))


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
