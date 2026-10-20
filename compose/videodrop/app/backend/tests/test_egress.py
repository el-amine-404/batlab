"""What videodrop hands its download processes: yt-dlp and ffmpeg go
through the egress proxy (batlab_common/egress.py, tested in lib/batlab-common)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videodrop.options import DEFAULTS  # noqa: E402
from videodrop.ytdlp import params  # noqa: E402


class Egress(unittest.TestCase):
    def test_ffmpeg_and_ytdlp_go_through_it(self):
        found = params(DEFAULTS, "%(title)s.%(ext)s", Path("/tmp/w"), proxy="http://127.0.0.1:9")
        self.assertEqual(found["proxy"], "http://127.0.0.1:9")
        whitelist = found["external_downloader_args"]["ffmpeg_i"]
        self.assertEqual(whitelist[0], "-protocol_whitelist")
        self.assertNotIn("file", whitelist[1].split(","))
        self.assertNotIn("proxy", params(DEFAULTS, "%(title)s.%(ext)s", Path("/tmp/w")))


if __name__ == "__main__":
    unittest.main()
