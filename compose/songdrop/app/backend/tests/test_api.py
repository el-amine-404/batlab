"""The API as the page and apps use it: who is let in, whose downloads each
sees and changes, callbacks only from an app's key, the job list's ETag and
the headers. Needs httpx (FastAPI's TestClient): the image's test stage
installs it. Jobs are queued but never run (no lifespan: no workers)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = tempfile.mkdtemp(prefix="songdrop-api-")
for name, value in (("SONGDROP_STATE", f"{ROOT}/state"), ("SONGDROP_OUTPUT", ROOT), ("SONGDROP_STATIC", f"{ROOT}/static"),
                    ("SONGDROP_APPRISE_URL", ""), ("SONGDROP_DISCORD_WEBHOOK", "")):
    os.environ[name] = value
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# batlab_common: in the image on PYTHONPATH, in the repository in lib/.
if len(Path(__file__).resolve().parents) > 5:
    sys.path.insert(1, str(Path(__file__).resolve().parents[5] / "lib" / "batlab-common"))

try:
    from fastapi.testclient import TestClient

    from app.context import accounts, manager
    from app.detect import Detection
    from app.main import app
    from app import options
except ImportError:  # httpx: only in the image's test stage
    TestClient = None

PAGE = {"X-Songdrop-Page": "1"}
VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@unittest.skipIf(TestClient is None, "needs httpx (the image's test stage)")
class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        accounts.setup(accounts.setup_code(), "amine", "Amine", "a long password")
        accounts.create_user("sara", "Sara", "another password", ["download"])
        cls.app_key, _ = accounts.create_key("clipseerr", ["download"], None)
        cls.reader_key, _ = accounts.create_key("reader", [], None)
        cls.admin = cls.signed_in("amine", "a long password")
        cls.sara = cls.signed_in("sara", "another password")
        # A job from before songdrop had accounts: nobody's.
        cls.old = manager.submit(Detection("ytdlp", "YouTube", "video", VIDEO), options.parse("ytdlp", {}))["id"]

    @staticmethod
    def signed_in(username: str, password: str) -> "TestClient":
        client = TestClient(app)
        answer = client.post("/api/v1/auth/login", json={"username": username, "password": password})
        assert answer.status_code == 200, answer.text
        return client

    def test_nobody_unknown_is_let_in(self):
        anonymous = TestClient(app)
        health = anonymous.get("/api/v1/health").json()
        self.assertTrue(health["ok"])
        self.assertRegex(health["version"], r"^\d+\.\d+\.\d+$")
        for path in ("/api/v1/jobs", "/api/v1/options", "/api/v1/settings", "/api/v1/system/status", "/api/v1/users"):
            self.assertEqual(anonymous.get(path).status_code, 401, path)
        self.assertEqual(anonymous.post("/api/v1/jobs", json={"url": VIDEO}).status_code, 401)

    def test_each_sees_and_changes_their_own(self):
        mine = self.sara.post("/api/v1/jobs", json={"url": VIDEO}, headers=PAGE)
        self.assertEqual(mine.status_code, 201, mine.text)
        self.assertEqual(mine.json()["asked_by"]["name"], "Sara")
        theirs = TestClient(app).post("/api/v1/jobs", json={"url": VIDEO}, headers={"X-Api-Key": self.app_key}).json()
        self.assertEqual(theirs["asked_by"], {"kind": "key", "id": theirs["asked_by"]["id"], "name": "clipseerr"})

        seen = {job["id"] for job in self.sara.get("/api/v1/jobs").json()["jobs"]}
        self.assertIn(mine.json()["id"], seen)
        self.assertNotIn(theirs["id"], seen)
        self.assertNotIn(self.old, seen)
        everything = {job["id"] for job in self.admin.get("/api/v1/jobs").json()["jobs"]}
        self.assertTrue({mine.json()["id"], theirs["id"], self.old} <= everything)

        # Someone else's is no such job: to read, to stop, to remove.
        self.assertEqual(self.sara.get(f"/api/v1/jobs/{theirs['id']}").status_code, 404)
        self.assertEqual(self.sara.post(f"/api/v1/jobs/{theirs['id']}/cancel", headers=PAGE).status_code, 404)
        self.assertEqual(self.sara.delete(f"/api/v1/jobs/{self.old}", headers=PAGE).status_code, 404)
        self.assertEqual(self.sara.post(f"/api/v1/jobs/{mine.json()['id']}/cancel", headers=PAGE).json()["status"],
                         "cancelled")
        self.assertEqual(self.admin.post(f"/api/v1/jobs/{theirs['id']}/cancel", headers=PAGE).status_code, 200)

    def test_callbacks_are_for_apps(self):
        asked = {"url": VIDEO, "callback_url": "http://clipseerr:8820/api/v1/hooks/songdrop"}
        refused = self.sara.post("/api/v1/jobs", json=asked, headers=PAGE)
        self.assertEqual(refused.status_code, 403)
        self.assertIn("API key", refused.json()["detail"])
        made = TestClient(app).post("/api/v1/jobs", json=asked, headers={"X-Api-Key": self.app_key})
        self.assertEqual(made.status_code, 201, made.text)

    def test_a_key_does_what_it_may(self):
        reader = TestClient(app, headers={"X-Api-Key": self.reader_key})
        self.assertEqual(reader.get("/api/v1/jobs").status_code, 200)
        self.assertEqual(reader.get("/api/v1/options").status_code, 200)
        self.assertEqual(reader.post("/api/v1/jobs", json={"url": VIDEO}).status_code, 403)
        self.assertEqual(reader.post("/api/v1/detect", json={"url": VIDEO}).status_code, 403)
        self.assertEqual(reader.get("/api/v1/settings").status_code, 403)
        self.assertEqual(reader.get("/api/v1/connections").status_code, 403)
        # A change with the page's cookie carries the page's header.
        self.assertEqual(self.sara.post("/api/v1/jobs", json={"url": VIDEO}).status_code, 403)

    def test_the_list_answers_304_until_a_job_changes(self):
        first = self.admin.get("/api/v1/jobs")
        tag = first.headers["etag"]
        self.assertEqual(self.admin.get("/api/v1/jobs", headers={"If-None-Match": tag}).status_code, 304)
        # Someone else's list is another answer.
        self.assertNotEqual(self.sara.get("/api/v1/jobs").headers["etag"], tag)
        self.admin.post("/api/v1/jobs", json={"url": VIDEO}, headers=PAGE)
        self.assertEqual(self.admin.get("/api/v1/jobs", headers={"If-None-Match": tag}).status_code, 200)

    def test_headers_and_compression(self):
        page = TestClient(app).get("/")
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertEqual(page.headers["x-frame-options"], "DENY")
        spec = self.admin.get("/api/v1/openapi.json", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(spec.headers.get("content-encoding"), "gzip")


if __name__ == "__main__":
    unittest.main()
