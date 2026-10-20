#!/usr/bin/env python3

from __future__ import annotations

import datetime
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "backupprune.py"
SPEC = importlib.util.spec_from_file_location("backupprune", SCRIPT)
assert SPEC and SPEC.loader
prune = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prune
SPEC.loader.exec_module(prune)

TODAY = datetime.date(2026, 10, 20)


class Tests(unittest.TestCase):
    def test_the_date_is_the_last_one_in_the_name(self) -> None:
        self.assertEqual(prune.dated("jellyfin-backup-10.11.3-20260915"), datetime.date(2026, 9, 15))
        self.assertEqual(prune.dated("pre-20250101-upgrade-20261017"), datetime.date(2026, 10, 17))
        self.assertIsNone(prune.dated("pre-subfeed"))
        self.assertIsNone(prune.dated("pre-x-20261399"))

    def test_only_dated_folders_past_their_days_go(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            for name in ("pre-upgrade-20261017", "pre-subfeed-20260901", "pre-undated"):
                (Path(root) / name).mkdir()
            (Path(root) / "pre-file-20250101").write_text("a file, not a folder")
            found = prune.expired(sorted(str(p) for p in Path(root).iterdir()), 14, TODAY)
        self.assertEqual([(path.name, day) for path, day in found],
                         [("pre-subfeed-20260901", datetime.date(2026, 9, 1))])

    def test_rules_fill_in_home_and_volumes_root(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".txt") as config:
            config.write("# comment\n~/pre-*   14\n${VOLUMES_ROOT}/*-backup-*  30  # trailing\n")
            config.flush()
            found = prune.rules(Path(config.name), {"VOLUMES_ROOT": "/mnt/docker-volumes"})
        self.assertEqual(found, [(str(Path.home() / "pre-*"), 14), ("/mnt/docker-volumes/*-backup-*", 30)])

    def test_card(self) -> None:
        title, body = prune.card([(Path("/mnt/docker-volumes/jellyfin-backup-10.11.3-20260915"),
                                   datetime.date(2026, 9, 15), 190_000_000)], [])
        self.assertEqual(title, "Removed 1 old backup folder (190 MB)")
        self.assertEqual(body, "**Removed**  /mnt/docker-volumes/jellyfin-backup-10.11.3-20260915  190 MB, from 2026-09-15")


if __name__ == "__main__":
    unittest.main()
