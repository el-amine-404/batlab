#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import errno
import importlib.util
import io
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "group-by-date.py"
SPEC = importlib.util.spec_from_file_location("group_by_date", SCRIPT)
assert SPEC and SPEC.loader
group = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = group
SPEC.loader.exec_module(group)


def run(folder: Path, *extra: str) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
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
        self.assertFalse((self.folder / "2025-04-18").exists())

    def test_visits_single_days_and_consecutive_spans(self) -> None:
        self.touch("2025-04-18_20h-18m-01s.jpg", "2025-05-03_18h-51m-23s.heic", "2025-05-03_18h-51m-23s.mov",
                   "2025-05-04_10h-00m-00s_02.jpg", "2025-06-13_19h-10m-01s.mov", "2025-06-13_19h-10m-01s.mov.xmp",
                   "2025-06-15_date-only_01.jpg", "notes.txt", "2025-06-15_contract.pdf")
        status, _ = run(self.folder, "--apply")
        self.assertEqual(status, 0)
        self.assertEqual(names(self.folder), {
            "2025-04-18/2025-04-18_20h-18m-01s.jpg",
            "2025-05-03_TO_2025-05-04/2025-05-03_18h-51m-23s.heic",
            "2025-05-03_TO_2025-05-04/2025-05-03_18h-51m-23s.mov",
            "2025-05-03_TO_2025-05-04/2025-05-04_10h-00m-00s_02.jpg",
            "2025-06-13/2025-06-13_19h-10m-01s.mov",
            "2025-06-13/2025-06-13_19h-10m-01s.mov.xmp",
            "2025-06-15/2025-06-15_date-only_01.jpg",
            "notes.txt",
            "2025-06-15_contract.pdf",
        })

    def test_days_join_existing_visit_folders(self) -> None:
        self.touch("2024-06-20_TO_2024-06-29/2024-06-21_09h-00m-00s.jpg", "2023-02-17_DAR-NAJI/old.jpg",
                   "2024-06-25_12h-00m-00s.jpg", "2023-02-17_18h-00m-00s.jpg")
        run(self.folder, "--apply")
        self.assertIn("2024-06-20_TO_2024-06-29/2024-06-25_12h-00m-00s.jpg", names(self.folder))
        self.assertIn("2023-02-17_DAR-NAJI/2023-02-17_18h-00m-00s.jpg", names(self.folder))

    def test_never_overwrites_and_keeps_a_photo_with_its_sidecar(self) -> None:
        self.touch("2025-04-18/2025-04-18_20h-18m-01s.jpg", "2025-04-18_20h-18m-01s.jpg",
                   "2025-04-18/2025-04-18_21h-00m-00s.jpg.xmp", "2025-04-18_21h-00m-00s.jpg",
                   "2025-04-18_21h-00m-00s.jpg.xmp")
        status, output = run(self.folder, "--apply")
        self.assertEqual(status, 2)
        self.assertIn("already exists", output)
        self.assertEqual((self.folder / "2025-04-18/2025-04-18_20h-18m-01s.jpg").read_text(),
                         "2025-04-18/2025-04-18_20h-18m-01s.jpg")
        self.assertTrue((self.folder / "2025-04-18_20h-18m-01s.jpg").exists())
        self.assertTrue((self.folder / "2025-04-18_21h-00m-00s.jpg").exists(), "sidecar clash keeps the photo too")
        self.assertTrue((self.folder / "2025-04-18_21h-00m-00s.jpg.xmp").exists())

    def test_preview_reports_clashes(self) -> None:
        self.touch("2025-04-18/2025-04-18_20h-18m-01s.jpg", "2025-04-18_20h-18m-01s.jpg")
        _, output = run(self.folder)
        self.assertIn("would clash", output)

    def test_target_name_taken_by_a_file(self) -> None:
        self.touch("2025-04-18", "2025-04-18_20h-18m-01s.jpg")
        status, output = run(self.folder, "--apply")
        self.assertEqual(status, 2)
        self.assertIn("not a folder", output)
        self.assertTrue((self.folder / "2025-04-18_20h-18m-01s.jpg").exists())

    def test_share_without_hard_links_or_atomic_rename(self) -> None:
        self.touch("2025-04-18_20h-18m-01s.jpg", "2025-04-18_20h-18m-01s.jpg.xmp",
                   "2025-04-18/2025-04-18_21h-00m-00s.jpg", "2025-04-18_21h-00m-00s.jpg")
        refused = OSError(errno.EPERM, os.strerror(errno.EPERM))
        with unittest.mock.patch.object(group.sys, "platform", "darwin"), \
                unittest.mock.patch.object(group.os, "link", side_effect=refused):
            status, _ = run(self.folder, "--apply")
        self.assertEqual(status, 2)
        self.assertIn("2025-04-18/2025-04-18_20h-18m-01s.jpg", names(self.folder))
        self.assertIn("2025-04-18/2025-04-18_20h-18m-01s.jpg.xmp", names(self.folder))
        self.assertEqual((self.folder / "2025-04-18/2025-04-18_21h-00m-00s.jpg").read_text(),
                         "2025-04-18/2025-04-18_21h-00m-00s.jpg")

    def test_a_failed_sidecar_move_brings_the_photo_back(self) -> None:
        self.touch("2025-04-18_20h-18m-01s.jpg", "2025-04-18_20h-18m-01s.jpg.xmp")
        real = group.move_noreplace

        def flaky(source: Path, target: Path) -> None:
            if source.name.endswith(".xmp"):
                raise OSError(errno.EIO, os.strerror(errno.EIO))
            real(source, target)

        with unittest.mock.patch.object(group, "move_noreplace", side_effect=flaky):
            status, output = run(self.folder, "--apply")
        self.assertEqual(status, 2)
        self.assertEqual(names(self.folder), {"2025-04-18_20h-18m-01s.jpg", "2025-04-18_20h-18m-01s.jpg.xmp"})


if __name__ == "__main__":
    unittest.main()
