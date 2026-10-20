"""The API as the page and apps use it: who is let in, whose requests each
sees and changes, the open callbacks, and the headers. Requests are made in
the store (no catalog, no network); the server's loops never start (no
lifespan)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = tempfile.mkdtemp(prefix="clipseerr-api-")
for name, value in (("CLIPSEERR_STATE", ROOT), ("CLIPSEERR_STATIC", f"{ROOT}/static")):
    os.environ[name] = value
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.context import accounts, requests  # noqa: E402
from app.main import app  # noqa: E402

PAGE = {"X-Clipseerr-Page": "1"}
# What media.snapshot() keeps of a video.
MEDIA = {"video_key": "yt:aaaaaaaaaaa", "video_id": "aaaaaaaaaaa", "audio_id": None, "title": "Song", "artist": "Artist",
         "album": None, "year": "2026", "duration": 200, "thumbnail": None, "video_title": "Song", "artist_id": None,
         "links": {}}


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        accounts.setup(accounts.setup_code(), "amine", "Amine", "a long password")
        sara = accounts.create_user("sara", "Sara", "another password", ["request"])
        cls.reader_key, _ = accounts.create_key("reader", [], None)
        cls.admin = cls.signed_in("amine", "a long password")
        cls.sara = cls.signed_in("sara", "another password")
        cls.hers = requests.create("audio", MEDIA, owner=f"user:{sara['id']}", owner_name="Sara")["id"]
        cls.old = requests.create("video", MEDIA)["id"]  # from before accounts: nobody's

    @staticmethod
    def signed_in(username: str, password: str) -> TestClient:
        client = TestClient(app)
        answer = client.post("/api/v1/auth/login", json={"username": username, "password": password})
        assert answer.status_code == 200, answer.text
        return client

    def test_nobody_unknown_is_let_in_but_the_callbacks(self):
        anonymous = TestClient(app)
        health = anonymous.get("/api/v1/health").json()
        self.assertTrue(health["ok"])
        self.assertRegex(health["version"], r"^\d+\.\d+\.\d+$")
        for path in ("/api/v1/requests", "/api/v1/home", "/api/v1/tags", "/api/v1/system/status", "/api/v1/settings",
                     "/api/v1/services", "/api/v1/users"):
            self.assertEqual(anonymous.get(path).status_code, 401, path)
        # The apps' callbacks only wake the engine: open.
        self.assertEqual(anonymous.post("/api/v1/hooks/songdrop?request=x").status_code, 200)

    def test_each_sees_their_own_requests(self):
        mine = self.sara.get("/api/v1/requests").json()
        self.assertEqual([r["id"] for r in mine["requests"]], [self.hers])
        self.assertEqual(mine["requests"][0]["asked_by"]["name"], "Sara")
        self.assertEqual(mine["counts"]["all"], 1)
        everything = {r["id"] for r in self.admin.get("/api/v1/requests").json()["requests"]}
        self.assertEqual(everything, {self.hers, self.old})
        self.assertEqual(self.sara.get(f"/api/v1/requests/{self.old}").status_code, 404)
        self.assertEqual(self.sara.delete(f"/api/v1/requests/{self.old}", headers=PAGE).status_code, 404)
        self.assertEqual(self.sara.get(f"/api/v1/requests/{self.hers}").status_code, 200)

    def test_a_key_does_what_it_may(self):
        reader = TestClient(app, headers={"X-Api-Key": self.reader_key})
        self.assertEqual(reader.get("/api/v1/requests").json()["requests"], [])
        refused = reader.post("/api/v1/requests", json={"video_id": "yt:aaaaaaaaaaa", "kind": "audio"})
        self.assertEqual(refused.status_code, 403)
        self.assertIn("reader may not ask for songs and videos", refused.json()["detail"])
        for path in ("/api/v1/settings", "/api/v1/services", "/api/v1/notifications", "/api/v1/libraries"):
            self.assertEqual(reader.get(path).status_code, 403, path)
        self.assertEqual(reader.put("/api/v1/hot/picks/yt:aaaaaaaaaaa").status_code, 403)
        # A change with the page's cookie carries the page's header.
        self.assertEqual(self.sara.post(f"/api/v1/requests/{self.hers}/retry").status_code, 403)

    def test_headers(self):
        page = TestClient(app).get("/")
        policy = page.headers["content-security-policy"]
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("frame-src https://www.youtube-nocookie.com", policy)
        self.assertEqual(page.headers["x-frame-options"], "DENY")


if __name__ == "__main__":
    unittest.main()
