import importlib.util
import io
from pathlib import Path
import struct
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "lid_screen.py"
SPEC = importlib.util.spec_from_file_location("lid_screen", SCRIPT)
lid_screen = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(lid_screen)


class ScreenControllerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.backlight = root / "backlight"
        self.backlight.mkdir()
        (self.backlight / "brightness").write_text("70\n")
        (self.backlight / "max_brightness").write_text("100\n")
        self.blank = root / "blank"
        self.blank.write_text("0\n")
        self.saved = root / "run" / "brightness"
        self.screen = lid_screen.ScreenController(self.backlight, self.blank, self.saved)

    def tearDown(self):
        self.temp.cleanup()

    def test_close_blanks_and_open_restores_previous_brightness(self):
        self.screen.close()
        self.assertEqual((self.backlight / "brightness").read_text(), "0\n")
        self.assertEqual(self.blank.read_text(), "1\n")
        self.assertEqual(self.saved.read_text(), "70\n")

        self.screen.open()
        self.assertEqual((self.backlight / "brightness").read_text(), "70\n")
        self.assertEqual(self.blank.read_text(), "0\n")
        self.assertFalse(self.saved.exists())

    def test_repeated_close_keeps_the_nonzero_saved_brightness(self):
        self.screen.close()
        self.screen.close()
        self.assertEqual(self.saved.read_text(), "70\n")


class LidInputTest(unittest.TestCase):
    def test_reads_a_linux_lid_switch_event(self):
        payload = struct.pack("@llHHi", 1, 2, lid_screen.EV_SW, lid_screen.SW_LID, 1)
        self.assertEqual(lid_screen.read_event(io.BytesIO(payload)), (5, 0, 1))

    def test_finds_the_lid_device_by_kernel_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = root / "event9" / "device" / "name"
            name.parent.mkdir(parents=True)
            name.write_text("Lid Switch\n")
            self.assertEqual(lid_screen.find_lid_device(root), Path("/dev/input/event9"))


if __name__ == "__main__":
    unittest.main()
