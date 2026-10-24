#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "dockerprune.py"
SPEC = importlib.util.spec_from_file_location("dockerprune", SCRIPT)
assert SPEC and SPEC.loader
prune = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prune
SPEC.loader.exec_module(prune)

NOW = 1_800_000_000.0
DAY = 86400.0
Image = prune.Image


def ids(found) -> set[str]:
    return {image.id for image, _ in found}


class ImageTests(unittest.TestCase):
    def test_keeps_the_running_image_and_one_previous_version(self) -> None:
        images = [Image("new", ["ollama/ollama:0.40.4"], NOW - 6 * DAY, 9),
                  Image("prev", ["ollama/ollama:0.34.4"], NOW - 20 * DAY, 9),
                  Image("old", ["ollama/ollama:0.30.0"], NOW - 60 * DAY, 9)]
        self.assertEqual(ids(prune.removable(images, {"new"}, set(), NOW)), {"old"})

    def test_a_pulled_image_not_yet_deployed_stays(self) -> None:
        images = [Image("pulled", ["frigate:0.19"], NOW - DAY, 9),
                  Image("running", ["frigate:0.18.2"], NOW - 5 * DAY, 9),
                  Image("prev", ["frigate:0.18.0"], NOW - 40 * DAY, 9)]
        self.assertEqual(ids(prune.removable(images, {"running"}, set(), NOW)), set())

    def test_a_pin_counts_as_used_when_its_stack_is_down(self) -> None:
        images = [Image("pinned", ["spotdl/spotify-downloader:v4.5.2"], NOW - 90 * DAY, 2),
                  Image("nightly", ["spotdl/spotify-downloader:nightly"], NOW - 9 * DAY, 2),
                  Image("prev", ["spotdl/spotify-downloader:v4.4.0"], NOW - 200 * DAY, 2),
                  Image("older", ["spotdl/spotify-downloader:v4.3.0"], NOW - 300 * DAY, 2)]
        pins = prune.pinned_from({"IMAGE_SPOTDL": "spotdl/spotify-downloader", "TAG_SPOTDL": "v4.5.2"})
        self.assertEqual(ids(prune.removable(images, set(), pins, NOW)), {"older"})

    def test_an_image_nothing_uses_goes_after_a_week(self) -> None:
        images = [Image("test", ["songrec-test:latest"], NOW - 9 * DAY, 2),
                  Image("fresh", ["try-me:latest"], NOW - DAY, 2)]
        self.assertEqual(ids(prune.removable(images, set(), set(), NOW)), {"test"})

    def test_pressure_mode_removes_a_genuinely_unused_image_after_a_day(self) -> None:
        images = [Image("unused", ["try-me:latest"], NOW - 2 * DAY, 2),
                  Image("configured", ["important:latest"], NOW - 90 * DAY, 2)]
        self.assertEqual(
            ids(prune.removable(images, set(), {"important:latest"}, NOW,
                                prune.PRESSURE_UNUSED_IMAGE_DAYS)),
            {"unused"},
        )

    def test_untagged_images_go_after_a_day_unless_running(self) -> None:
        images = [Image("dangling", [], NOW - 2 * DAY, 1), Image("young", [], NOW - 3600, 1),
                  Image("running", [], NOW - 9 * DAY, 1)]
        self.assertEqual(ids(prune.removable(images, {"running"}, set(), NOW)), {"dangling"})

    def test_each_rollback_tag_is_its_own_service(self) -> None:
        images = [Image("a", ["local/batlab-rollback:songdrop"], NOW - 30 * DAY, 1),
                  Image("b", ["local/batlab-rollback:clipseerr"], NOW - 30 * DAY, 1)]
        # The updater's previous build of each service, kept until its next rebuild moves the tag.
        self.assertEqual(prune.repository("local/batlab-rollback:songdrop"), "local/batlab-rollback:songdrop")
        self.assertEqual(prune.removable(images, set(), set(), NOW), [])

    def test_repository_names(self) -> None:
        self.assertEqual(prune.repository("ghcr.io/a/b:v1"), "ghcr.io/a/b")
        self.assertEqual(prune.repository("host:5000/c:3"), "host:5000/c")
        self.assertEqual(prune.repository("host:5000/c"), "host:5000/c")
        self.assertEqual(prune.normalise("docker.io/library/redis:8"), "redis:8")

    def test_docker_timestamps(self) -> None:
        self.assertEqual(prune.timestamp("2026-10-01 09:21:52.597526262 +0000 UTC"),
                         prune.timestamp("2026-10-01T09:21:52.597526Z"))
        self.assertGreater(prune.timestamp("2026-09-24T03:55:15.625472039Z"), 0)
        self.assertEqual(prune.timestamp(""), 0.0)


class ReportTests(unittest.TestCase):
    def test_reads_what_the_build_cache_prune_freed(self) -> None:
        self.assertEqual(prune.reclaimed("ID\tRECLAIMABLE\nabc\ttrue\nTotal:\t13.6GB\n"), 13.6e9)
        self.assertEqual(prune.reclaimed("Total reclaimed space: 512MB"), 512e6)
        self.assertEqual(prune.reclaimed(""), 0.0)

    def test_the_card_lists_the_biggest_images_first_and_skips_empty_parts(self) -> None:
        report = prune.Report(freed=17.7e9, used_percent=75, free=59e9,
                                images=[("alpine:3", 8_000_000), ("songrec-test:latest", 2_500_000_000)],
                                build_cache=13.6e9)
        title, body = prune.card(report)
        self.assertEqual(title, "Freed 17.7 GB of old Docker images and build cache")
        self.assertEqual(body, "**Root disk**  75% used, 59.0 GB free\n"
                               "**Images removed (2)**\nsongrec-test:latest  2.5 GB\nalpine:3  0.0 GB\n"
                               "**Build cache**  13.6 GB")


if __name__ == "__main__":
    unittest.main()
