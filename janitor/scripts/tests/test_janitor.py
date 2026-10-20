#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "janitor.py"
SPEC = importlib.util.spec_from_file_location("janitor", SCRIPT)
assert SPEC and SPEC.loader
janitor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = janitor
SPEC.loader.exec_module(janitor)

NOW = 1_800_000_000.0
DAY = 86400.0
Image = janitor.Image


def ids(found) -> set[str]:
    return {image.id for image, _ in found}


class ImageTests(unittest.TestCase):
    def test_keeps_the_running_image_and_one_previous_version(self) -> None:
        images = [Image("new", ["ollama/ollama:0.40.4"], NOW - 6 * DAY, 9),
                  Image("prev", ["ollama/ollama:0.34.4"], NOW - 20 * DAY, 9),
                  Image("old", ["ollama/ollama:0.30.0"], NOW - 60 * DAY, 9)]
        self.assertEqual(ids(janitor.removable(images, {"new"}, set(), NOW)), {"old"})

    def test_a_pulled_image_not_yet_deployed_stays(self) -> None:
        images = [Image("pulled", ["frigate:0.19"], NOW - DAY, 9),
                  Image("running", ["frigate:0.18.2"], NOW - 5 * DAY, 9),
                  Image("prev", ["frigate:0.18.0"], NOW - 40 * DAY, 9)]
        self.assertEqual(ids(janitor.removable(images, {"running"}, set(), NOW)), set())

    def test_a_pin_counts_as_used_when_its_stack_is_down(self) -> None:
        images = [Image("pinned", ["spotdl/spotify-downloader:v4.5.2"], NOW - 90 * DAY, 2),
                  Image("nightly", ["spotdl/spotify-downloader:nightly"], NOW - 9 * DAY, 2),
                  Image("prev", ["spotdl/spotify-downloader:v4.4.0"], NOW - 200 * DAY, 2),
                  Image("older", ["spotdl/spotify-downloader:v4.3.0"], NOW - 300 * DAY, 2)]
        pins = janitor.pinned_from({"IMAGE_SPOTDL": "spotdl/spotify-downloader", "TAG_SPOTDL": "v4.5.2"})
        self.assertEqual(ids(janitor.removable(images, set(), pins, NOW)), {"older"})

    def test_an_image_nothing_uses_goes_after_a_week(self) -> None:
        images = [Image("test", ["songrec-test:latest"], NOW - 9 * DAY, 2),
                  Image("fresh", ["try-me:latest"], NOW - DAY, 2)]
        self.assertEqual(ids(janitor.removable(images, set(), set(), NOW)), {"test"})

    def test_untagged_images_go_after_a_day_unless_running(self) -> None:
        images = [Image("dangling", [], NOW - 2 * DAY, 1), Image("young", [], NOW - 3600, 1),
                  Image("running", [], NOW - 9 * DAY, 1)]
        self.assertEqual(ids(janitor.removable(images, {"running"}, set(), NOW)), {"dangling"})

    def test_each_rollback_tag_is_its_own_service(self) -> None:
        images = [Image("a", ["local/batlab-rollback:songdrop"], NOW - 30 * DAY, 1),
                  Image("b", ["local/batlab-rollback:clipseerr"], NOW - 30 * DAY, 1)]
        # The updater's previous build of each service, kept until its next rebuild moves the tag.
        self.assertEqual(janitor.repository("local/batlab-rollback:songdrop"), "local/batlab-rollback:songdrop")
        self.assertEqual(janitor.removable(images, set(), set(), NOW), [])

    def test_repository_names(self) -> None:
        self.assertEqual(janitor.repository("ghcr.io/a/b:v1"), "ghcr.io/a/b")
        self.assertEqual(janitor.repository("host:5000/c:3"), "host:5000/c")
        self.assertEqual(janitor.repository("host:5000/c"), "host:5000/c")
        self.assertEqual(janitor.normalise("docker.io/library/redis:8"), "redis:8")

    def test_docker_timestamps(self) -> None:
        self.assertEqual(janitor.timestamp("2026-10-01 09:21:52.597526262 +0000 UTC"),
                         janitor.timestamp("2026-10-01T09:21:52.597526Z"))
        self.assertGreater(janitor.timestamp("2026-09-24T03:55:15.625472039Z"), 0)
        self.assertEqual(janitor.timestamp(""), 0.0)


LOG = """\
[2026-10-19 19:31:43.306 +00:00] [INF] [35] Jellyfin.LiveTv.TunerHosts.HdHomerun.HdHomerunHost: Opening "SharedHttpStream" Live stream from "http://gluetun:9191/proxy/ts/stream/8e40"
[2026-10-19 19:31:46.953 +00:00] [INF] [35] Jellyfin.LiveTv.TunerHosts.HdHomerun.HdHomerunHost: Beginning "SharedHttpStream" stream to "/cache/transcodes/8c3999f3ec0340838c13ff1dc5d952c2.ts"
[2026-10-19 19:31:50.014 +00:00] [INF] [20] Emby.Server.Implementations.Library.MediaSourceManager: Live stream opened: MediaSourceInfo { Protocol: Http, Id: "heavy_c5", Path: "http://172.19.0.41:8096/LiveTv/LiveStreamFiles/8c3999f3ec0340838c13ff1dc5d952c2/stream.ts", OpenToken: null, LiveStreamId: "e23_af9_heavy_c5" }
[2026-10-19 19:31:50.632 +00:00] [INF] [11] Emby.Server.Implementations.Session.SessionManager: User "potato" started playback of '"Arryadia (720p)"' ("Jellyfin for Tizen" "1.1.0")
"""


class LiveTvTests(unittest.TestCase):
    def test_reads_the_stream_id_and_channel_from_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "log_20261019.log").write_text(LOG)
            streams = janitor.live_streams(Path(folder))
        self.assertEqual(streams, {"8c3999f3ec0340838c13ff1dc5d952c2":
                                   {"id": "e23_af9_heavy_c5", "channel": "Arryadia (720p)"}})

    def test_a_buffer_growing_unplayed_for_ten_minutes_is_left_open(self) -> None:
        state: dict = {}
        nobody = janitor.Viewing()
        buffer = lambda size: janitor.Buffer("f", size, "e23")  # noqa: E731
        self.assertEqual(janitor.orphans([buffer(100)], nobody, state, NOW), [])
        self.assertEqual(janitor.orphans([buffer(200)], nobody, state, NOW + 120), [])
        self.assertEqual(janitor.orphans([buffer(300)], nobody, state, NOW + 720), [buffer(300)])

    def test_a_watched_buffer_is_never_left_open(self) -> None:
        state = {"f": {"size": 100, "orphan_since": NOW - 3600}}
        watching = janitor.Viewing(live_ids={"e23"}, anything=True)
        self.assertEqual(janitor.orphans([janitor.Buffer("f", 900, "e23")], watching, state, NOW), [])
        self.assertNotIn("orphan_since", state["f"])

    def test_a_channel_playing_without_its_stream_id_protects_every_buffer(self) -> None:
        state = {"f": {"size": 100, "orphan_since": NOW - 3600}}
        unknown = janitor.viewing([{"NowPlayingItem": {"Type": "TvChannel"}, "PlayState": {}}])
        self.assertEqual(janitor.orphans([janitor.Buffer("f", 900, "e23")], unknown, state, NOW), [])

    def test_a_buffer_that_stopped_growing_starts_over(self) -> None:
        state = {"f": {"size": 900, "orphan_since": NOW - 3600}}
        self.assertEqual(janitor.orphans([janitor.Buffer("f", 900, "e23")], janitor.Viewing(), state, NOW), [])
        self.assertNotIn("orphan_since", state["f"])

    def test_sessions(self) -> None:
        seen = janitor.viewing([{"NowPlayingItem": {"Type": "Episode"}, "PlayState": {}},
                                {"NowPlayingItem": {"Type": "TvChannel"}, "PlayState": {"LiveStreamId": "x"}},
                                {"PlayState": {}}])
        self.assertEqual((seen.anything, seen.live_ids, seen.live_unknown), (True, {"x"}, False))


if __name__ == "__main__":
    unittest.main()
