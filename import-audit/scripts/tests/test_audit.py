#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "audit.py"
SPEC = importlib.util.spec_from_file_location("import_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


class FakeArr:
    def __init__(self, name: str, responses: dict) -> None:
        self.name, self.responses = name, responses

    def get(self, endpoint: str, **query: object) -> object:
        handler = self.responses[endpoint]
        return handler(**query) if callable(handler) else handler


class EpisodeNameTests(unittest.TestCase):
    def test_tags(self) -> None:
        cases = {
            "Courage.the.Cowardly.Dog.S01E03E04.The.Shadow.of.Courage.mp4": (1, [3, 4]),
            "Show - S02E05-E07 - Title.mkv": (2, [5, 6, 7]),
            "show.s04e13.the.mask.mp4": (4, [13]),
            "Show S03E09-10 Title.mkv": (3, [9, 10]),
        }
        for name, expected in cases.items():
            with self.subTest(name):
                self.assertEqual(audit.episodes_in_name(name), expected)

    def test_no_tag(self) -> None:
        self.assertIsNone(audit.episodes_in_name("[Foxtrot] Vinland Saga - 01 [BD 1080p].mkv"))
        self.assertIsNone(audit.episodes_in_name("Movie.2010.1080p.x264.mkv"))


class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.old = time.time() - 6 * 3600

    def tearDown(self) -> None:
        self.temp.cleanup()

    def file(self, relative: str) -> Path:
        path = self.data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        os.utime(path, (self.old, self.old))
        return path

    def link(self, source: Path, relative: str) -> Path:
        target = self.data / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, target)
        return target

    def sonarr(self, episode_files: list, episodes: list, parse: dict | None = None) -> FakeArr:
        return FakeArr("Sonarr", {
            "series": [{"id": 3, "title": "Courage"}],
            "episodefile": episode_files,
            "episode": episodes,
            "parse": lambda **_: parse or {},
            "queue": {"records": []},
        })

    def test_wrong_episodes_are_reported_and_right_ones_are_not(self) -> None:
        wrong = self.file("torrents/tv/pack/Courage.S01E03E04.Shadow.mp4")
        right = self.file("torrents/tv/pack/Courage.S01E17E18.Queen.mp4")
        self.link(wrong, "media/tv/Courage/Season 01/Courage - S01E05-E08.mp4")
        self.link(right, "media/tv/Courage/Season 01/Courage - S01E17-E18.mp4")
        sonarr = self.sonarr(
            [{"id": 1, "path": "/data/media/tv/Courage/Season 01/Courage - S01E05-E08.mp4"},
             {"id": 2, "path": "/data/media/tv/Courage/Season 01/Courage - S01E17-E18.mp4"}],
            [{"episodeNumber": n, "episodeFileId": 1} for n in (5, 6, 7, 8)]
            + [{"episodeNumber": n, "episodeFileId": 2} for n in (17, 18)])
        videos = audit.finished_videos(self.data / "torrents/tv", 0, time.time())
        problems = audit.check_wrong_episodes(sonarr, self.data, videos)
        self.assertEqual(len(problems), 1)
        self.assertIn("E3,4", problems[0].text)
        self.assertIn("E5,6,7,8", problems[0].text)

    def test_left_behind_uses_the_file_name_not_sonarrs_scene_mapping(self) -> None:
        self.file("torrents/tv/pack/Courage.S02E09E10.Title.mp4")
        radarr = FakeArr("Radarr", {"parse": {}})
        torrents = self.data / "torrents"

        def sonarr(has_9_and_10: bool) -> FakeArr:
            return FakeArr("Sonarr", {
                # Sonarr maps this name to episodes 17-20, which already have files.
                "parse": {"series": {"id": 3, "title": "Courage"},
                          "episodes": [{"seasonNumber": 2, "episodeNumber": n, "hasFile": True} for n in (17, 18, 19, 20)]},
                "episode": [{"seasonNumber": 2, "episodeNumber": n, "hasFile": has_9_and_10 if n in (9, 10) else True}
                            for n in range(1, 26)],
            })

        problems = audit.check_left_behind(sonarr(False), radarr, torrents, 3600, time.time())
        self.assertEqual(len(problems), 1)
        self.assertIn("Courage", problems[0].text)
        self.assertEqual(audit.check_left_behind(sonarr(True), radarr, torrents, 3600, time.time()), [])

    def test_left_behind_without_an_episode_tag_trusts_sonarr(self) -> None:
        self.file("torrents/tv/[Group] Anime - 01 [1080p].mkv")
        radarr = FakeArr("Radarr", {"parse": {}})
        missing = FakeArr("Sonarr", {"parse": {"series": {"id": 9, "title": "Anime"}, "episodes": [{"hasFile": False}]}})
        unknown = FakeArr("Sonarr", {"parse": None})
        self.assertEqual(len(audit.check_left_behind(missing, radarr, self.data / "torrents", 3600, time.time())), 1)
        self.assertEqual(len(audit.check_left_behind(unknown, radarr, self.data / "torrents", 3600, time.time())), 1)

    def test_a_finished_pack_still_in_the_queue_is_not_left_behind(self) -> None:
        self.file("torrents/tv/AoT S04 [Kira]/Attack on Titan - Episode 73 - Savagery.mkv")
        radarr = FakeArr("Radarr", {"parse": {}})
        sonarr = FakeArr("Sonarr", {"parse": {"series": {"id": 4, "title": "Attack on Titan"},
                                              "episodes": [{"hasFile": False}]}})
        torrents = self.data / "torrents"
        self.assertEqual(len(audit.check_left_behind(sonarr, radarr, torrents, 3600, time.time())), 1)
        self.assertEqual(audit.check_left_behind(sonarr, radarr, torrents, 3600, time.time(),
                                                 queued={torrents / "tv/AoT S04 [Kira]"}), [])

    def test_extras_of_an_imported_pack_are_not_reported(self) -> None:
        episode = self.file("torrents/tv/AoT S04/Attack on Titan - Episode 60.mkv")
        self.link(episode, "media/tv/Attack on Titan/Season 04/S04E01.mkv")
        self.file("torrents/tv/AoT S04/Extras/Staff Making Talk.mkv")
        self.file("torrents/tv/AoT S04/Extras/Creditless OPs & EDs/[Opening 06] Attack on Titan.mkv")
        self.file("torrents/tv/Other pack/Staff Making Talk.mkv")
        radarr = FakeArr("Radarr", {"parse": {}})
        sonarr = FakeArr("Sonarr", {"parse": None})
        problems = audit.check_left_behind(sonarr, radarr, self.data / "torrents", 3600, time.time())
        # The pack with nothing imported is still reported: its files may be episodes.
        self.assertEqual([p.key for p in problems], [f"left:{self.data}/torrents/tv/Other pack/Staff Making Talk.mkv"])

    def test_movie_extras_are_not_reported(self) -> None:
        movie = self.file("torrents/movies/Shutter.Island.2010/Shutter.Island.2010.mkv")
        self.file("torrents/movies/Shutter.Island.2010/Into.the.Lighthouse.mkv")
        self.link(movie, "media/movies/Shutter Island (2010)/Shutter Island (2010).mkv")
        self.file("torrents/movies/Lost.Movie.2024/Lost.Movie.2024.mkv")
        radarr = FakeArr("Radarr", {
            "parse": lambda **query: {"movie": {"id": 1 if "Shutter" in query["title"] else 2, "title": query["title"]}},
            "movie": [{"id": 1, "hasFile": True}, {"id": 2, "hasFile": False}],
        })
        sonarr = FakeArr("Sonarr", {"parse": {}})
        problems = audit.check_left_behind(sonarr, radarr, self.data / "torrents", 3600, time.time())
        self.assertEqual([problem.key for problem in problems], [f"left:{self.data / 'torrents/movies/Lost.Movie.2024'}"])

    def test_a_release_replaced_by_an_upgrade_is_not_reported(self) -> None:
        """Radarr's parse endpoint answers hasFile with null whatever the library holds."""
        self.file("torrents/movies/Robin.Hood.2026.WEB-DL.DD5.1-FIRST/Robin.Hood.2026.WEB-DL.DD5.1-FIRST.mkv")
        upgrade = self.file("torrents/movies/Robin.Hood.2026.AMZN.WEB-DL.DDP5.1-SECOND/Robin.Hood.2026.AMZN.WEB-DL.DDP5.1-SECOND.mkv")
        self.link(upgrade, "media/movies/Robin Hood (2026)/Robin Hood (2026) - SECOND.mkv")
        radarr = FakeArr("Radarr", {
            "parse": lambda **_: {"movie": {"id": 26, "title": "Robin Hood", "hasFile": None}},
            "movie": [{"id": 26, "hasFile": True}],
        })
        sonarr = FakeArr("Sonarr", {"parse": {}})
        self.assertEqual(audit.check_left_behind(sonarr, radarr, self.data / "torrents", 3600, time.time()), [])

    def test_a_movie_with_no_file_at_all_is_still_reported(self) -> None:
        self.file("torrents/movies/Lost.Movie.2024/Lost.Movie.2024.mkv")
        radarr = FakeArr("Radarr", {
            "parse": lambda **_: {"movie": {"id": 7, "title": "Lost Movie", "hasFile": None}},
            "movie": [{"id": 7, "hasFile": False}],
        })
        sonarr = FakeArr("Sonarr", {"parse": {}})
        problems = audit.check_left_behind(sonarr, radarr, self.data / "torrents", 3600, time.time())
        self.assertEqual(len(problems), 1)
        self.assertIn("Lost Movie", problems[0].text)

    def test_a_pack_still_downloading_is_not_left_behind(self) -> None:
        self.file("torrents/tv/Show.S01.1080p-Group/Show.S01E10.1080p-Group.mkv")
        partial = self.file("torrents/tv/Show.S01.1080p-Group/Show.S01E11.1080p-Group.mkv.!qB")
        sonarr = FakeArr("Sonarr", {"parse": {"episodes": [{"hasFile": False}]}})
        radarr = FakeArr("Radarr", {"parse": {}})
        self.assertEqual(audit.check_left_behind(sonarr, radarr, self.data / "torrents", 3600, time.time()), [])
        partial.rename(partial.with_suffix(""))
        self.assertEqual(len(audit.check_left_behind(sonarr, radarr, self.data / "torrents", 3600, time.time())), 2)

    def test_young_downloads_are_ignored(self) -> None:
        path = self.file("torrents/tv/pack/Courage.S01E09E10.Weremole.mp4")
        os.utime(path, None)
        sonarr = FakeArr("Sonarr", {"parse": {"episodes": [{"hasFile": False}]}})
        self.assertEqual(audit.check_left_behind(sonarr, FakeArr("Radarr", {"parse": {}}), self.data / "torrents", 3600, time.time()), [])

    def test_blocked_queue_items(self) -> None:
        added = "2026-09-15T10:00:00Z"
        app = FakeArr("Radarr", {"queue": {"records": [
            {"title": "Blocked.Movie", "downloadId": "A", "added": added, "trackedDownloadState": "importBlocked",
             "trackedDownloadStatus": "warning", "statusMessages": [{"messages": ["No files found are eligible"]}]},
            {"title": "Fine.Movie", "downloadId": "B", "added": added, "trackedDownloadState": "downloading",
             "trackedDownloadStatus": "ok"},
        ]}})
        problems = audit.check_queues([app], 3600, time.time())
        self.assertEqual(len(problems), 1)
        self.assertIn("No files found are eligible", problems[0].text)

    def test_a_pack_and_its_episodes_downloading_twice(self) -> None:
        def record(download: str, title: str, episode: int, score: int, size: float, left: float) -> dict:
            return {"downloadId": download, "title": title, "episodeId": 100 + episode, "customFormatScore": score,
                    "size": size, "sizeleft": left, "series": {"title": "Attack on Titan"},
                    "episode": {"seasonNumber": 4, "episodeNumber": episode}}
        queue = [record("PACK", "Kira S04", n, 800, 127e9, 110e9) for n in range(1, 31)]
        queue += [record(f"EP{n}", f"Baws S04E{n}", n, 600, 1e9, 0.5e9) for n in (24, 25)]
        queue += [record("OTHER", "Other S01E01", 999, 100, 1e9, 1e9)]
        problems = audit.check_duplicates([FakeArr("Sonarr", {"queue": {"records": queue}})])
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].text.startswith("Attack on Titan S04E24, S04E25: keep `Kira S04`"))
        self.assertIn("remove `Baws S04E24`", problems[0].text)
        self.assertIn("`Baws S04E25`", problems[0].text)

    def test_downloads_for_different_episodes_are_not_duplicates(self) -> None:
        queue = [{"downloadId": "A", "episodeId": 1}, {"downloadId": "B", "episodeId": 2}, {"downloadId": "A", "episodeId": 3}]
        self.assertEqual(audit.check_duplicates([FakeArr("Radarr", {"queue": {"records": queue}})]), [])

    def test_a_blocked_pack_is_reported_once(self) -> None:
        record = {"title": "[UQW] Show S1", "downloadId": "P", "added": "2026-09-15T10:00:00Z",
                  "trackedDownloadState": "importBlocked", "trackedDownloadStatus": "warning"}
        app = FakeArr("Sonarr", {"queue": {"records": [dict(record, episodeId=n) for n in range(24)]}})
        self.assertEqual(len(audit.check_queues([app], 3600, time.time())), 1)
        self.assertEqual(audit.check_queues([app], 3600, time.time(), skip={"P"}), [])


class AutoImportTests(unittest.TestCase):
    EPISODES = [{"id": 100 + n, "seasonNumber": 1, "episodeNumber": n} for n in range(1, 25)]

    def items(self, *names: str) -> list[dict]:
        return [{"id": i, "path": f"/data/torrents/tv/pack/{name}", "quality": {}, "languages": []}
                for i, name in enumerate(names)]

    def test_loose_names(self) -> None:
        cases = {
            "[UQW] Vinland Saga S1 - Ep01 [BDRip 1080p HEVC FLAC].mkv": (1, [1]),
            "Show S2 Episode 05 [1080p].mkv": (2, [5]),
            "Show.S01E03.1080p.mkv": (1, [3]),
        }
        for name, expected in cases.items():
            with self.subTest(name):
                self.assertEqual(audit.episode_for_import(name), expected)
        self.assertIsNone(audit.episode_for_import("[SubsPlease] Show - 01 (1080p).mkv"))
        self.assertIsNone(audit.episode_for_import("Show S1 [BDRip] Extras.mkv"))

    def test_a_pack_maps_one_file_per_episode(self) -> None:
        names = [f"[UQW] Vinland Saga S1 - Ep{n:02d} [BDRip].mkv" for n in range(1, 25)] + ["readme.txt"]
        plan = audit.plan_import(self.items(*names), self.EPISODES)
        self.assertEqual([(season, ids) for _, season, ids in plan][:2], [(1, [101]), (1, [102])])
        self.assertEqual(len(plan), 24)

    def test_unmappable_downloads_are_left_alone(self) -> None:
        cases = {
            "names no episode": ["Show S1 - Ep01.mkv", "Show S1 Creditless OP.mkv"],
            "does not have": ["Show S1 - Ep25.mkv"],
            "also names": ["Show S1 - Ep01.mkv", "Show S01E01 v2.mkv"],
            "no video": ["Show S1 - Ep01.srt"],
        }
        for reason, names in cases.items():
            with self.subTest(reason):
                self.assertIn(reason, audit.plan_import(self.items(*names), self.EPISODES))

    def test_imports_only_what_sonarr_confirms(self) -> None:
        posts = []

        class Sonarr(FakeArr):
            def post(self, endpoint: str, body: object) -> object:
                posts.append(endpoint)
                if endpoint == "manualimport":
                    return [dict(item, rejections=self.responses["rejections"]) for item in body]
                return {}

        queue = {"records": [{"title": "[UQW] Show S1", "downloadId": "P", "seriesId": 7, "added": "2026-09-15T10:00:00Z",
                              "trackedDownloadState": "importBlocked"}]}
        for rejections, imported in (([], {"P"}), ([{"reason": "Not an upgrade"}], set())):
            with self.subTest(rejections=rejections):
                posts.clear()
                sonarr = Sonarr("Sonarr", {"queue": queue, "episode": self.EPISODES, "rejections": rejections,
                                           "manualimport": self.items("Show S1 - Ep01.mkv", "Show S1 - Ep02.mkv")})
                problems, done = audit.auto_import(sonarr, 3600, time.time(), apply=True)
                self.assertEqual(done, imported)
                self.assertEqual(posts, ["manualimport", "command"] if imported else ["manualimport"])
                self.assertEqual(len(problems), len(imported))


if __name__ == "__main__":
    unittest.main()
