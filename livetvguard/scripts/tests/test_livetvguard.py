#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "livetvguard.py"
SPEC = importlib.util.spec_from_file_location("livetvguard", SCRIPT)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)

NOW = 1_800_000_000.0
STREAM = {"id": "e23_af9_heavy_c5"}

# Jellyfin 12.2's lines for the stream that filled lab2's disk on 2026-10-20.
LOG = """\
[2026-10-19 19:31:43.306 +00:00] [INF] [35] Jellyfin.LiveTv.TunerHosts.HdHomerun.HdHomerunHost: Opening "SharedHttpStream" Live stream from "http://gluetun:9191/proxy/ts/stream/8e40"
[2026-10-19 19:31:46.953 +00:00] [INF] [35] Jellyfin.LiveTv.TunerHosts.HdHomerun.HdHomerunHost: Beginning "SharedHttpStream" stream to "/cache/transcodes/8c3999f3ec0340838c13ff1dc5d952c2.ts"
[2026-10-19 19:31:50.014 +00:00] [INF] [20] Emby.Server.Implementations.Library.MediaSourceManager: Live stream opened: MediaSourceInfo { Protocol: Http, Id: "heavy_c5", Path: "http://172.19.0.41:8096/LiveTv/LiveStreamFiles/8c3999f3ec0340838c13ff1dc5d952c2/stream.ts", OpenToken: null, LiveStreamId: "e23_af9_heavy_c5" }
[2026-10-19 19:31:50.632 +00:00] [INF] [11] Emby.Server.Implementations.Session.SessionManager: User "potato" started playback of '"Arryadia (720p)"' ("Jellyfin for Tizen" "1.1.0")
"""


class LogTests(unittest.TestCase):
    def test_reads_the_stream_its_channel_and_the_app_from_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "log_20261019.log").write_text(LOG)
            streams = guard.live_streams(Path(folder))
        self.assertEqual(streams, {"8c3999f3ec0340838c13ff1dc5d952c2": {
            "id": "e23_af9_heavy_c5", "opened": "2026-10-19 19:31",
            "channel": "Arryadia (720p)", "client": "Jellyfin for Tizen"}})


class OrphanTests(unittest.TestCase):
    def test_a_buffer_growing_unplayed_for_ten_minutes_is_closed(self) -> None:
        state: dict = {}
        nobody = guard.Viewing()
        buffer = lambda size: guard.Buffer("f", size, STREAM)  # noqa: E731
        self.assertEqual(guard.orphans([buffer(100)], nobody, state, NOW), [])
        self.assertEqual(guard.orphans([buffer(200)], nobody, state, NOW + 120), [])
        self.assertEqual(guard.orphans([buffer(300)], nobody, state, NOW + 720), [buffer(300)])

    def test_a_watched_buffer_is_left_alone(self) -> None:
        state = {"f": {"size": 100, "orphan_since": NOW - 3600}}
        watching = guard.Viewing(live_ids={STREAM["id"]}, anything=True)
        self.assertEqual(guard.orphans([guard.Buffer("f", 900, STREAM)], watching, state, NOW), [])
        self.assertNotIn("orphan_since", state["f"])

    def test_a_channel_playing_without_its_stream_id_protects_every_buffer(self) -> None:
        state = {"f": {"size": 100, "orphan_since": NOW - 3600}}
        unknown = guard.viewing([{"NowPlayingItem": {"Type": "TvChannel"}, "PlayState": {}}])
        self.assertEqual(guard.orphans([guard.Buffer("f", 900, STREAM)], unknown, state, NOW), [])

    def test_a_buffer_that_stopped_growing_starts_over(self) -> None:
        state = {"f": {"size": 900, "orphan_since": NOW - 3600}}
        self.assertEqual(guard.orphans([guard.Buffer("f", 900, STREAM)], guard.Viewing(), state, NOW), [])
        self.assertNotIn("orphan_since", state["f"])

    def test_sessions(self) -> None:
        seen = guard.viewing([{"NowPlayingItem": {"Type": "Episode"}, "PlayState": {}},
                              {"NowPlayingItem": {"Type": "TvChannel"}, "PlayState": {"LiveStreamId": "x"}},
                              {"PlayState": {}}])
        self.assertEqual((seen.anything, seen.live_ids, seen.live_unknown), (True, {"x"}, False))


class CardTests(unittest.TestCase):
    def test_durations(self) -> None:
        self.assertEqual([guard.duration(s) for s in (600, 5340, 5400, 93_780)],
                         ["10 min", "89 min", "1 h 30 min", "26 h 03 min"])

    def test_the_card_says_what_where_how_long_and_what_was_done(self) -> None:
        stream = {"id": "x", "opened": "2026-10-19 19:31", "channel": "Arryadia (720p)", "client": "Jellyfin for Tizen"}
        title, body = guard.card(guard.Buffer("f", 37_805_277_184, stream), 26 * 3600, "closed through Jellyfin's API")
        self.assertEqual(title, "Closed a live TV stream nobody was watching")
        self.assertEqual(body, "**Channel**  Arryadia (720p)\n"
                               "**Opened**  2026-10-19 19:31 UTC on Jellyfin for Tizen\n"
                               "**Unwatched for**  26 h 00 min\n"
                               "**Recorded**  37.8 GB, deleted\n"
                               "**Done**  closed through Jellyfin's API")


if __name__ == "__main__":
    unittest.main()
