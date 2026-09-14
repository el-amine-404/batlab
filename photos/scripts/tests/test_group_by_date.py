#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "group-by-date.py"
SPEC = importlib.util.spec_from_file_location("group_by_date", SCRIPT)
assert SPEC and SPEC.loader
group = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = group
SPEC.loader.exec_module(group)


def run(folder: Path, *extra: str) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = group.main([str(folder), *extra])
    return status, output.getvalue()


def names(folder: Path) -> set[str]:
    return {str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file()}


class GroupByDateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def touch(self, *relatives: str) -> None:
        for relative in relatives:
            path = self.folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)

    def test_preview_changes_nothing(self) -> None:
        self.touch("2025-04-18_20h-18m-01s.jpg")
        before = names(self.folder)
        status, output = run(self.folder)
        self.assertEqual(status, 0)
        self.assertIn("2025-04-18/", output)
        self.assertEqual(names(self.folder), before)

    def test_visits_single_days_and_consecutive_spans(self) -> None:
        self.touch("2025-04-18_20h-18m-01s.jpg", "2025-05-03_18h-51m-23s.heic", "2025-05-03_18h-51m-23s.mov",
                   "2025-05-04_10h-00m-00s.jpg", "2025-06-13_19h-10m-01s.mov", "2025-06-13_19h-10m-01s.mov.xmp",
                   "2025-06-15_date-only_01.jpg", "notes.txt")
        status, _ = run(self.folder, "--apply")
        self.assertEqual(status, 0)
        self.assertEqual(names(self.folder), {
            "2025-04-18/2025-04-18_20h-18m-01s.jpg",
            "2025-05-03_TO_2025-05-04/2025-05-03_18h-51m-23s.heic",
            "2025-05-03_TO_2025-05-04/2025-05-03_18h-51m-23s.mov",
            "2025-05-03_TO_2025-05-04/2025-05-04_10h-00m-00s.jpg",
            "2025-06-13/2025-06-13_19h-10m-01s.mov",
            "2025-06-13/2025-06-13_19h-10m-01s.mov.xmp",
            "2025-06-15/2025-06-15_date-only_01.jpg",
            "notes.txt",
        })

    def test_days_join_existing_visit_folders(self) -> None:
        self.touch("2024-06-20_TO_2024-06-29/2024-06-21_09h-00m-00s.jpg", "2023-02-17_DAR-NAJI/old.jpg",
                   "2024-06-25_12h-00m-00s.jpg", "2023-02-17_18h-00m-00s.jpg")
        run(self.folder, "--apply")
        self.assertIn("2024-06-20_TO_2024-06-29/2024-06-25_12h-00m-00s.jpg", names(self.folder))
        self.assertIn("2023-02-17_DAR-NAJI/2023-02-17_18h-00m-00s.jpg", names(self.folder))

    def test_never_overwrites(self) -> None:
        self.touch("2025-04-18/2025-04-18_20h-18m-01s.jpg", "2025-04-18_20h-18m-01s.jpg")
        status, output = run(self.folder, "--apply")
        self.assertEqual(status, 2)
        self.assertIn("already exists", output)
        self.assertEqual((self.folder / "2025-04-18/2025-04-18_20h-18m-01s.jpg").read_text(),
                         "2025-04-18/2025-04-18_20h-18m-01s.jpg")
        self.assertTrue((self.folder / "2025-04-18_20h-18m-01s.jpg").exists())


if __name__ == "__main__":
    unittest.main()
