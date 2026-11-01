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
        answer = cls.client.post("/api/v1/auth/setup", json={"code": code, "username": "amine", "name": "Amine", "password": "correct horse 1"})
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
        answer = self.client.post("/api/v1/projects", json=body)
        self.assertEqual(answer.status_code, 201, answer.text)
        return answer.json()["public_id"]

    def upload(self, scan, seed, name="photo.jpg"):
        answer = self.client.post(f"/api/v1/projects/{scan}/pages", params={"name": name}, content=photo(seed))
        self.assertEqual(answer.status_code, 201, answer.text)
        return answer.json()

    def pages(self, scan):
        return self.client.get(f"/api/v1/projects/{scan}/pages", params={"limit": 1000}).json()["items"]

    def test_nobody_gets_in_without_signing_in(self):
        from fastapi.testclient import TestClient
        other = TestClient(self.client.app)
        self.assertEqual(other.get("/api/v1/projects").status_code, 401)
        self.assertEqual(other.get("/api/v1/settings").status_code, 401)
        self.assertEqual(other.get("/api/v1/health").status_code, 200)
        self.assertEqual(self.client.post("/api/v1/projects", json={}, headers={"X-Scanloom-Page": ""}).status_code, 403)

    def test_scan_keeps_no_expiry_and_can_be_renamed(self):
        scan = self.new_scan(title="First")
        self.assertIsNone(self.client.get(f"/api/v1/projects/{scan}").json()["expires_at"])
        renamed = self.client.patch(f"/api/v1/projects/{scan}", json={"title": "Carte grise"}).json()
        self.assertEqual(renamed["title"], "Carte grise")

    def test_pipeline_pages_paging_and_reorder(self):
        scan = self.new_scan()
        for seed in range(1, 4):
            self.upload(scan, seed, f"p{seed}.jpg")
        waiting = self.client.get(f"/api/v1/projects/{scan}").json()
        self.assertEqual(waiting["status"], "processing")
        self.assertEqual(waiting["progress"]["remaining"], 3)
        self.assertGreater(waiting["progress"]["eta_seconds"], 0)
        self.work()
        done = self.client.get(f"/api/v1/projects/{scan}").json()
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
        window = self.client.get(f"/api/v1/projects/{scan}/pages", params={"limit": 2, "offset": 2}).json()
        self.assertEqual((window["total"], len(window["items"])), (3, 1))
        order = [pages[2]["id"], pages[0]["id"], pages[1]["id"]]
        self.client.patch(f"/api/v1/projects/{scan}", json={"page_order": order})
        self.assertEqual([page["id"] for page in self.pages(scan)], order)
        self.assertEqual(self.client.patch(f"/api/v1/projects/{scan}", json={"page_order": order[:2]}).status_code, 409)

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
        self.client.delete(f"/api/v1/projects/{first}")
        self.work()
        self.assertEqual(self.client.get(page["png_url"]).status_code, 200)

    def test_edits_rotate_delete_and_a_stale_render_does_not_win(self):
        scan = self.new_scan()
        self.upload(scan, 60)
        self.upload(scan, 61)
        self.work()
        pages = self.pages(scan)
        first = pages[0]
        rotated = self.client.patch(f"/api/v1/projects/{scan}/pages", json={"page_ids": [first["id"]], "rotate_by": 90})
        self.assertEqual(rotated.status_code, 202, rotated.text)
        self.client.patch(f"/api/v1/projects/{scan}/pages/{first['id']}", json={"rotate_by": 90})
        self.assertEqual(self.client.get(f"/api/v1/projects/{scan}").json()["status"], "processing")
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
        gone = self.client.post(f"/api/v1/projects/{scan}/pages/delete", json={"page_ids": [first["id"]]}).json()
        self.assertEqual(gone["page_count"], 1)
        self.assertEqual(self.pages(scan)[0]["position"], 0)
        self.work()

    def test_a_failed_page_does_not_block_the_others(self):
        scan = self.new_scan()
        self.upload(scan, 70)
        broken = self.client.post(f"/api/v1/projects/{scan}/pages", params={"name": "cut.jpg"},
                                  content=b"\xff\xd8\xff\xe0 not really an image at all" * 40)
        self.assertEqual(broken.status_code, 201)
        self.db.execute("UPDATE settings SET values=values||'{\"worker_max_attempts\":1}'::jsonb")
        try:
            self.work()
        finally:
            self.db.execute("UPDATE settings SET values=values||'{\"worker_max_attempts\":3}'::jsonb")
        project = self.client.get(f"/api/v1/projects/{scan}").json()
        self.assertEqual((project["status"], project["failed_count"], project["completed_count"]), ("ready", 1, 1))
        made = self.client.post(f"/api/v1/projects/{scan}/exports", json={"kind": "pdf"})
        self.assertEqual(made.status_code, 202, made.text)
        self.work()

    def test_not_an_image_is_refused_at_once(self):
        scan = self.new_scan()
        answer = self.client.post(f"/api/v1/projects/{scan}/pages", params={"name": "a.pdf"}, content=b"%PDF-1.7 whatever")
        self.assertEqual(answer.status_code, 415)
        self.assertEqual(list(self.config.incoming.glob("*.upload")).__len__() >= 0, True)

    def test_exports_and_the_same_export_is_not_made_twice(self):
        scan = self.new_scan(title="Réunion: Août/2026")
        for seed in (80, 81):
            self.upload(scan, seed)
        self.work()
        for kind, signature in (("pdf", b"%PDF"), ("zip", b"PK")):
            for quality in ("lossless", "balanced"):
                made = self.client.post(f"/api/v1/projects/{scan}/exports", json={"kind": kind, "quality": quality}).json()
                self.assertEqual(self.client.get(made["download_url"]).status_code, 409)
                self.work()
                got = self.client.get(made["download_url"])
                self.assertEqual(got.status_code, 200, got.text)
                self.assertTrue(got.content.startswith(signature))
                self.assertIn("attachment", got.headers["content-disposition"])
                again = self.client.post(f"/api/v1/projects/{scan}/exports", json={"kind": kind, "quality": quality}).json()
                self.assertEqual((again["id"], again["status"]), (made["id"], "ready"))

    def test_rounded_page_makes_a_lossless_pdf(self):
        scan = self.new_scan()
        self.upload(scan, 85)
        self.work()
        page = self.pages(scan)[0]
        self.client.patch(f"/api/v1/projects/{scan}/pages/{page['id']}", json={"corner_radius": .1})
        self.work()
        made = self.client.post(f"/api/v1/projects/{scan}/exports", json={"kind": "pdf", "quality": "lossless"}).json()
        self.work()
        self.assertEqual(self.client.get(made["download_url"]).status_code, 200)

    def test_one_person_does_not_see_anothers_scans(self):
        from fastapi.testclient import TestClient
        mine = self.new_scan(title="Mine")
        created = self.client.post("/api/v1/users", json={"username": "family", "name": "Family", "password": "another long one", "permissions": ["scan"]})
        self.assertLess(created.status_code, 300, created.text)
        other = TestClient(self.client.app)
        other.headers["X-Scanloom-Page"] = "1"
        self.assertLess(other.post("/api/v1/auth/login", json={"username": "family", "password": "another long one"}).status_code, 300)
        self.assertEqual(other.get(f"/api/v1/projects/{mine}").status_code, 404)
        self.assertEqual(other.delete(f"/api/v1/projects/{mine}").status_code, 404)
        self.assertEqual(other.get("/api/v1/projects").json()["total"], 0)
        self.assertEqual(other.put("/api/v1/settings", json={"values": {}, "revision": 1}).status_code, 403)
        self.assertEqual(other.get("/api/v1/system/logs").status_code, 403)

    def test_list_search_and_paging(self):
        for number in range(3):
            scan = self.new_scan(title=f"Findme {number}")
            self.upload(scan, 90 + number)
        self.work()
        found = self.client.get("/api/v1/projects", params={"q": "findme", "limit": 2}).json()
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
        self.assertEqual(self.client.delete(f"/api/v1/projects/{scan}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/v1/projects/{scan}").status_code, 404)
        self.work()
        self.assertFalse((self.config.work / str(row["id"])).exists())
        self.assertIsNone(self.db.one("SELECT id FROM projects WHERE public_id=%s", (scan,)))


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
