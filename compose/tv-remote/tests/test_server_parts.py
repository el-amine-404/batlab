"""Sessions, the sleep timer and the shared status, with files in a temp folder."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import auth  # noqa: E402
import livestate  # noqa: E402
import sleeptimer  # noqa: E402

_TMP = Path(tempfile.mkdtemp())
auth.AUTH_FILE = _TMP / "auth.json"
auth.SESSIONS_FILE = _TMP / "sessions.json"
sleeptimer._FILE = _TMP / "sleep.json"


class Sessions(unittest.TestCase):
    def setUp(self):
        auth.set_password("a long enough password")

    def cookie(self):
        return f"tvr={auth.make_session()}"

    def test_a_session_works_until_logged_out(self):
        c = self.cookie()
        self.assertTrue(auth.valid_session(c))
        auth.end_session(c)
        self.assertFalse(auth.valid_session(c))

    def test_logout_ends_only_that_device(self):
        a, b = self.cookie(), self.cookie()
        auth.end_session(a)
        self.assertTrue(auth.valid_session(b))

    def test_log_out_everywhere(self):
        a, b = self.cookie(), self.cookie()
        auth.log_out_everywhere()
        self.assertFalse(auth.valid_session(a) or auth.valid_session(b))

    def test_made_up_cookies_fail(self):
        self.assertFalse(auth.valid_session("tvr=" + "A" * 43))
        self.assertFalse(auth.valid_session(None))

    def test_renewed_at_most_daily(self):
        self.assertFalse(auth.renew_session(self.cookie()))

    def test_the_token_is_not_stored(self):
        token = auth.make_session()
        self.assertNotIn(token, auth.SESSIONS_FILE.read_text())


class SleepTimer(unittest.TestCase):
    def test_fires_once_and_can_be_cancelled(self):
        at = sleeptimer.set_timer("aa", 10)
        self.assertEqual(sleeptimer.when("aa"), at)
        self.assertEqual(sleeptimer.due(at - 1), [])
        self.assertEqual(sleeptimer.due(at + 1), ["aa"])
        self.assertEqual(sleeptimer.due(at + 2), [])
        sleeptimer.set_timer("bb", 10)
        self.assertTrue(sleeptimer.cancel("bb"))
        self.assertFalse(sleeptimer.cancel("bb"))


class Differs(unittest.TestCase):
    def playing(self, position, at, state="playing"):
        return {"power": "on", "media": {"id": "x", "state": state, "position": position, "at": at}}

    def test_a_clock_running_on_is_not_news(self):
        self.assertFalse(livestate._differs(self.playing(10, 100), self.playing(11, 101)))

    def test_a_seek_is_news(self):
        self.assertTrue(livestate._differs(self.playing(10, 100), self.playing(60, 101)))

    def test_pause_and_volume_are_news(self):
        self.assertTrue(livestate._differs(self.playing(10, 100), self.playing(10, 100, "paused")))
        self.assertTrue(livestate._differs({"power": "on", "volume": 1}, {"power": "on", "volume": 2}))


class Reads(unittest.TestCase):
    def test_pages_share_one_read(self):
        reads = []

        class Tv:
            def status(self):
                reads.append(1)
                return {"power": "on"}

        life = livestate.LiveState(Tv)
        life.current()
        life.current()
        self.assertEqual(len(reads), 1)
        life.poke()
        life.current()
        self.assertEqual(len(reads), 2)


if __name__ == "__main__":
    unittest.main()
