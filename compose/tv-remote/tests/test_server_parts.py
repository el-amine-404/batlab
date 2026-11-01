"""Sessions, the sleep timer and the shared status, with files in a temp folder."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import auth  # noqa: E402
import livestate  # noqa: E402
import powerlog  # noqa: E402
import samsung  # noqa: E402
import sleeptimer  # noqa: E402

_TMP = Path(tempfile.mkdtemp())
auth.AUTH_FILE = _TMP / "auth.json"
auth.SESSIONS_FILE = _TMP / "sessions.json"
sleeptimer._FILE = _TMP / "sleep.json"
powerlog._FILE = _TMP / "power.json"


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


class PowerLog(unittest.TestCase):
    def test_since_counts_from_the_change(self):
        powerlog.observe("p1", "on", 100)
        powerlog.observe("p1", "on", 200)
        self.assertEqual(powerlog.since("p1"), 100)

    def test_one_lost_answer_is_not_off_and_off_dates_from_the_first(self):
        powerlog.observe("p2", "on", 100)
        powerlog.observe("p2", "off", 200)
        self.assertEqual(powerlog.since("p2"), 100)
        powerlog.observe("p2", "on", 230)
        self.assertEqual(powerlog.since("p2"), 100)
        powerlog.observe("p2", "off", 300)
        powerlog.observe("p2", "standby", 330)
        self.assertEqual(powerlog.since("p2"), 300)
        powerlog.observe("p2", "on", 400)
        self.assertEqual(powerlog.since("p2"), 400)


class TvThatIsOff(unittest.TestCase):
    """Keys to a TV that is off once used up the container's threads."""

    def test_keys_fail_at_once_after_a_failure_and_scan_once(self):
        calls = []
        r = samsung.Remote.__new__(samsung.Remote)
        r.mac, r.lock, r.tv = "aa", threading.Lock(), None
        r.failed_at, r.relocated_at, r.last_used = -1e9, -1e9, 0.0

        def refuse():
            calls.append("connect")
            raise OSError("timed out")

        with mock.patch.object(r, "_connect", refuse), mock.patch.object(samsung, "scan", lambda: calls.append("scan") or []):
            for _ in range(5):
                with self.assertRaises(ConnectionError):
                    r.run(lambda tv: None)
        self.assertEqual(calls.count("connect"), 3)
        self.assertEqual(calls.count("scan"), 1)

    def test_presses_already_queued_do_not_each_retry(self):
        calls = []
        r = samsung.Remote.__new__(samsung.Remote)
        r.mac, r.lock, r.tv = "aa", threading.Lock(), None
        r.failed_at, r.relocated_at, r.last_used = -1e9, -1e9, 0.0

        def refuse():
            calls.append("connect")
            raise OSError("timed out")

        def press():
            try:
                r.run(lambda tv: None)
            except ConnectionError:
                pass

        with mock.patch.object(r, "_connect", refuse), mock.patch.object(samsung, "scan", lambda: []):
            r.lock.acquire()
            threads = [threading.Thread(target=press) for _ in range(5)]
            for t in threads:
                t.start()
            time.sleep(0.1)  # all five are past the first check, waiting for the lock
            r.lock.release()
            for t in threads:
                t.join()
        self.assertEqual(len(calls), 3)

    def test_scans_at_the_same_time_share_one(self):
        runs = []
        samsung._last_scan = (0.0, [])

        def probe(ip):
            runs.append(ip)
            return None

        with mock.patch.object(samsung, "device_info", probe):
            threads = [threading.Thread(target=samsung.scan) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(runs), samsung.TV_SUBNET.num_addresses - 2)


if __name__ == "__main__":
    unittest.main()
