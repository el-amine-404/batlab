"""accounts.py, netnames.py and web.py: people, sessions and keys in a
SQLite file, and the routes of an app behind a Gate. web.py's tests need
FastAPI (and httpx, for its TestClient): they run in the apps' images and are
skipped elsewhere."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from batlab_common import netnames  # noqa: E402
from batlab_common.accounts import (Accounts, Conflict, Database, Invalid, Throttle,  # noqa: E402
                                    check_password, hash_password)

PERMISSIONS = {
    "download": ("Ask for downloads", "Their own."),
    "everyone": ("See everyone's", "Everyone's."),
    "admin": ("Admin", "Everything."),
}

try:
    from fastapi import APIRouter, Depends, FastAPI
    from fastapi.testclient import TestClient

    from batlab_common.web import Gate
except ImportError:  # outside the apps' images
    FastAPI = None


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "accounts.db")
        self.accounts = Accounts(self.db, "testapp", PERMISSIONS, key_prefix="ta_")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_the_file_is_private(self):
        self.assertEqual(os.stat(self.db.path).st_mode & 0o777, 0o600)

    def test_passwords_are_hashed(self):
        stored = hash_password("a long password")
        self.assertNotIn("a long password", stored)
        self.assertTrue(check_password("a long password", stored))
        self.assertFalse(check_password("a long passworD", stored))
        self.assertFalse(check_password("x", "not a hash"))

    def test_the_setup_code_makes_the_first_admin_once(self):
        code = self.accounts.setup_code()
        self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertEqual(self.accounts.setup_code(), code)  # the same until used
        with self.assertRaises(Invalid):
            self.accounts.setup("ABCD-EFGH-JKLM", "amine", "", "a long password")
        user = self.accounts.setup(code.replace("-", "").lower(), "amine", "Amine", "a long password")
        self.assertEqual(user["permissions"], ["admin"])
        self.assertIsNone(self.accounts.setup_code())
        with self.assertRaises(Conflict):
            self.accounts.setup(code, "other", "", "a long password")

    def test_the_last_admin_stays(self):
        admin = self.accounts.setup(self.accounts.setup_code(), "amine", "", "a long password")
        with self.assertRaises(Conflict):
            self.accounts.update_user(admin["id"], permissions=["download"])
        with self.assertRaises(Conflict):
            self.accounts.delete_user(admin["id"])
        other = self.accounts.create_user("sara", "Sara", "another password", ["download", "admin"])
        self.accounts.update_user(admin["id"], permissions=["download"])
        self.assertEqual(self.accounts.user(admin["id"])["permissions"], ["download"])
        self.assertEqual(self.accounts.user(other["id"])["permissions"], ["download", "admin"])

    def test_a_session_and_a_key_say_who_and_what(self):
        self.accounts.setup(self.accounts.setup_code(), "amine", "Amine", "a long password")
        sara = self.accounts.create_user("sara", "Sara", "another password", ["everyone", "download"])
        token, _ = self.accounts.sign_in("SARA", "another password", "192.168.1.20", "Firefox")
        who = self.accounts.session(token)
        self.assertEqual((who.kind, who.id, who.name), ("user", sara["id"], "Sara"))
        self.assertEqual(who.public()["permissions"], ["download", "everyone"])  # the app's order
        self.assertTrue(who.can("download") and not who.can("admin"))
        with self.assertRaises(Invalid):
            self.accounts.sign_in("sara", "wrong password", "x", "")
        self.accounts.update_user(sara["id"], enabled=False)
        self.assertIsNone(self.accounts.session(token))  # turned off: its sessions end

        key, made = self.accounts.create_key("clipseerr", ["download"], None)
        self.assertTrue(key.startswith("ta_") and made["prefix"] == key[:10])
        self.assertEqual(self.accounts.key(key, "172.19.0.5").name, "clipseerr")
        self.assertIsNone(self.accounts.key("vd_" + key[3:], "x"))  # another app's prefix
        with self.assertRaises(Invalid):
            self.accounts.create_key("root", ["admin"], None)
        self.accounts.delete_key(made["id"])
        self.assertIsNone(self.accounts.key(key, "x"))

    def test_the_database_keeps_nothing_that_signs_in(self):
        self.accounts.setup(self.accounts.setup_code(), "amine", "", "a long password")
        token, _ = self.accounts.sign_in("amine", "a long password", "x", "")
        key, _ = self.accounts.create_key("app", [], None)
        raw = b"".join(p.read_bytes() for p in Path(self.tmp.name).iterdir())
        for secret in (token, key, "a long password"):
            self.assertNotIn(secret.encode(), raw)


class Shell(unittest.TestCase):
    """admin.py: the commands whoever runs the server types."""

    def test_a_key_made_and_removed(self):
        import contextlib
        import io

        from batlab_common import admin

        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "accounts.db")
            accounts = Accounts(db, "testapp", PERMISSIONS, key_prefix="ta_", package="app")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(admin.run(accounts, ["download"], ["add-key", "reader", "--can", ""]), 0)
                self.assertEqual(admin.run(accounts, ["download"], ["setup-code"]), 0)
            self.assertIn("ta_", out.getvalue())
            self.assertEqual([k["name"] for k in accounts.keys()], ["reader"])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(admin.run(accounts, ["download"], ["remove-key", "READER"]), 0)
            self.assertEqual(accounts.keys(), [])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(admin.run(accounts, ["download"], ["remove-key", "reader"]), 1)
            self.assertEqual(accounts.setup_command, "docker exec testapp python -m app.admin setup-code")
            db.close()


class Throttling(unittest.TestCase):
    def test_after_five_failures_each_try_waits(self):
        throttle = Throttle()
        for _ in range(4):
            throttle.failed("ip:x")
        self.assertEqual(throttle.wait("ip:x"), 0)
        throttle.failed("ip:x")
        self.assertGreater(throttle.wait("ip:x", "name:y"), 25)
        throttle.succeeded("ip:x")
        self.assertEqual(throttle.wait("ip:x"), 0)


class AddressNames(unittest.TestCase):
    def test_an_address_is_named_by_the_network(self):
        names = {"172.19.10.30": "homepage.home_server", "192.168.1.20": "pixel-7.lan", "8.8.8.8": "dns.google"}

        def resolve(address):
            if address not in names:
                raise OSError("no name")
            return names[address], [], [address]

        def name(address):
            return netnames.name_of(address, resolve, itself="testapp itself")

        self.assertEqual(name("172.19.10.30"), "homepage")
        self.assertEqual(name("192.168.1.20"), "pixel-7")
        self.assertEqual(name("8.8.8.8"), "dns.google")
        self.assertEqual(name("127.0.0.1"), "testapp itself")
        self.assertIsNone(name("10.9.9.9"))
        self.assertIsNone(name("testclient"))
        self.assertIsNone(name(None))


@unittest.skipIf(FastAPI is None, "needs FastAPI and httpx (the apps' images)")
class Web(unittest.TestCase):
    """An app behind a Gate, asked as the page and as another app."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "accounts.db")
        self.accounts = Accounts(self.db, "testapp", PERMISSIONS, key_prefix="ta_")
        gate = Gate("testapp", PERMISSIONS, user_default=["download"], key_default=["download"])
        things = APIRouter()

        @things.get("/things")
        def read(who: gate.Who):
            return {"who": who.name}

        @things.post("/things", dependencies=[gate.need("download")])
        def make():
            return {"made": True}

        @things.get("/feed")
        def feed():
            return {"items": []}

        app = FastAPI()
        gate.install(app, self.accounts)
        app.include_router(gate.public, prefix="/api")
        app.include_router(gate.router, prefix="/api", dependencies=[Depends(gate.principal)])
        app.include_router(things, prefix="/api", dependencies=[Depends(gate.principal)])
        app.include_router(APIRouter(routes=[r for r in things.routes if r.path == "/feed"]), prefix="/open",
                           dependencies=[Depends(gate.principal_or_address_key)])
        self.client = TestClient(app)
        self.page = {"X-Testapp-Page": "1"}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def sign_in_admin(self) -> None:
        code = self.accounts.setup_code()
        answer = self.client.post("/api/auth/setup", json={"code": code, "username": "amine", "password": "a long password"})
        self.assertEqual(answer.status_code, 200, answer.text)

    def test_nobody_is_let_in_and_setup_signs_the_admin_in(self):
        self.assertEqual(self.client.get("/api/auth/state").json()["setup"], True)
        answer = self.client.get("/api/things")
        self.assertEqual(answer.status_code, 401)
        self.assertIn("isn't set up yet", answer.json()["detail"])
        self.sign_in_admin()
        self.assertEqual(self.client.get("/api/things").json(), {"who": "amine"})
        self.assertEqual(self.client.get("/api/auth/state").json()["me"]["permissions"], ["download", "everyone", "admin"])

    def test_a_change_with_the_cookie_carries_the_page_header(self):
        self.sign_in_admin()
        self.assertEqual(self.client.post("/api/things").status_code, 403)
        self.assertEqual(self.client.post("/api/things", headers=self.page).json(), {"made": True})

    def test_a_key_does_what_it_may_and_no_more(self):
        self.sign_in_admin()
        made = self.client.post("/api/keys", json={"name": "reader", "permissions": []}, headers=self.page).json()
        app = TestClient(self.client.app)
        self.assertEqual(app.get("/api/things", headers={"X-Api-Key": made["key"]}).json(), {"who": "reader"})
        refused = app.post("/api/things", headers={"Authorization": f"Bearer {made['key']}"})
        self.assertEqual(refused.status_code, 403)
        self.assertIn("reader may not ask for downloads", refused.json()["detail"])
        self.assertEqual(app.get("/api/users", headers={"X-Api-Key": made["key"]}).status_code, 403)
        # In the address only where the route allows it (a feed reader's).
        self.assertEqual(app.get(f"/open/feed?key={made['key']}").status_code, 200)
        self.assertEqual(app.get(f"/api/things?key={made['key']}").status_code, 401)
        self.assertEqual(app.get("/open/feed?key=ta_wrong").status_code, 401)

    def test_wrong_passwords_are_throttled(self):
        self.sign_in_admin()
        other = TestClient(self.client.app)
        for _ in range(5):
            self.assertEqual(other.post("/api/auth/login", json={"username": "amine", "password": "nope"}).status_code,
                             422)
        answer = other.post("/api/auth/login", json={"username": "amine", "password": "a long password"})
        self.assertEqual(answer.status_code, 429)
        self.assertGreater(int(answer.headers["Retry-After"]), 0)

    def test_signing_out_ends_the_session(self):
        self.sign_in_admin()
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/api/things").status_code, 401)
        started = time.time()
        self.assertEqual(self.client.post("/api/auth/login",
                                          json={"username": "AMINE", "password": "a long password"}).status_code, 200)
        self.assertLess(time.time() - started, 5)
        self.assertEqual(self.client.get("/api/auth/me").json()["username"], "amine")


if __name__ == "__main__":
    unittest.main()
