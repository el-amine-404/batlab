"""What commands do to a TV, with a fake TV; and the key sequence that was
confirmed on the real one, which must not change by accident."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import commands  # noqa: E402
import tvcontrol  # noqa: E402
from profiles import profile_for  # noqa: E402


class FakeTv:
    def __init__(self, power="on", volume=20):
        self.power, self.volume, self.mute = power, volume, False
        self.keys, self.apps, self.toggles = [], [], 0

    def inputs(self):
        return profile_for()["inputs"]

    def status(self):
        return {"power": self.power}

    def toggle_power(self):
        self.toggles += 1
        self.power = "off" if self.power == "on" else "on"

    def press(self, key):
        self.keys.append(key)

    def open_app(self, app_id):
        self.apps.append(app_id)

    def audio(self):
        return {"volume": self.volume, "mute": self.mute}

    def set_volume(self, level):
        self.volume = level

    def set_mute(self, mute):
        self.mute = mute

    def select_input(self, input_id):
        return next((i["name"] for i in self.inputs() if i["id"] == input_id), None)


def run(tv, **intent):
    with mock.patch("time.sleep"):
        return commands.run_intent(tv, intent)


class Commands(unittest.TestCase):
    def test_volume_by_an_amount_stays_in_range(self):
        tv = FakeTv(volume=98)
        self.assertEqual(run(tv, type="volume", dir=1, by=5, label="")["say"], "Volume 100")

    def test_keys_need_the_tv_on(self):
        tv = FakeTv(power="off")
        self.assertTrue(run(tv, type="key", key="KEY_HOME", label="Home")["failed"])
        self.assertEqual(tv.keys, [])

    def test_opening_an_app_wakes_the_tv(self):
        tv = FakeTv(power="off")
        run(tv, type="app", id="x", name="X", label="")
        self.assertEqual((tv.toggles, tv.apps), (1, ["x"]))

    def test_power_off_when_already_off_does_nothing(self):
        tv = FakeTv(power="off")
        run(tv, type="power", action="off", label="")
        self.assertEqual(tv.toggles, 0)

    def test_unknown_input_is_said_not_pressed(self):
        self.assertTrue(run(FakeTv(), type="input", id="hdmi3", label="HDMI 3")["failed"])

    def test_every_intent_voice_can_produce_has_a_handler(self):
        import voice
        for intent in voice.PHRASES.values():
            self.assertIn(intent["type"], commands._HANDLERS)


class InputWalk(unittest.TestCase):
    """Do not "simplify" these: each key was found necessary on the Q8F."""

    def walk(self, input_id):
        tv = tvcontrol.SamsungTv("aa:bb:cc:dd:ee:ff")
        keys = []
        with mock.patch.object(tv, "press", keys.append), mock.patch.object(tv, "inputs", lambda: profile_for()["inputs"]), \
                mock.patch("time.sleep"):
            name = tv.select_input(input_id)
        return name, keys

    def test_hdmi2(self):
        self.assertEqual(self.walk("hdmi2"), ("DVR", ["KEY_HOME", "KEY_SOURCE", "KEY_UP", "KEY_UP", "KEY_DOWN", "KEY_DOWN", "KEY_ENTER"]))

    def test_tv(self):
        self.assertEqual(self.walk("tv"), ("TV", ["KEY_HOME", "KEY_SOURCE", "KEY_UP", "KEY_UP", "KEY_ENTER"]))

    def test_a_second_walk_is_refused(self):
        with tvcontrol._walking:
            with self.assertRaises(ConnectionError):
                self.walk("tv")

    def test_unknown_presses_nothing(self):
        self.assertEqual(self.walk("hdmi4"), (None, []))


if __name__ == "__main__":
    unittest.main()
