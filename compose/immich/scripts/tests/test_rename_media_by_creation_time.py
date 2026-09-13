#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "rename-media-by-creation-time.py"
SPEC = importlib.util.spec_from_file_location("media_renamer", SCRIPT)
assert SPEC and SPEC.loader
renamer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renamer)


class MediaRenamerTests(unittest.TestCase):
    def test_existing_filename_timestamp_formats(self) -> None:
        old = renamer.timestamp_from_filename("2017-11-18_21-49-59_IMG_0001.jpg")
        new = renamer.timestamp_from_filename("2017-11-18_21h-49m-59s.jpg")
        self.assertEqual(old, new)
        self.assertEqual(old.isoformat(), "2017-11-18T21:49:59")

    def test_target_name_and_collision(self) -> None:
        timestamp = renamer.dt.datetime(2017, 11, 18, 21, 49, 59)
        self.assertEqual(
            renamer.target_name(timestamp, "JPG"),
            "2017-11-18_21h-49m-59s.jpg",
        )
        self.assertEqual(
            renamer.target_name(timestamp, "jpg", collision=2),
            "2017-11-18_21h-49m-59s_02.jpg",
        )

    def test_representative_sample_includes_rare_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            files = [root / f"image-{index:02d}.jpg" for index in range(10)]
            files.extend((root / "video.mov", root / "raw.dng"))
            sample = renamer.representative_sample(root, files, 3)
            self.assertEqual({path.suffix for path in sample}, {".jpg", ".mov", ".dng"})

    def test_live_photo_group_uses_still_time_and_shared_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            still = root / "old.heic"
            video = root / "old.mov"
            unrelated = root / "other.jpg"
            for path in (still, video, unrelated):
                path.touch()
            group_id = "95324CE3-BC38-40BF-BA73-E9ED15B10668"
            metadata = {
                still: {
                    "ExifIFD:DateTimeOriginal": "2026:04:18 00:01:49",
                    "Apple:MediaGroupUUID": group_id,
                },
                video: {
                    "Keys:CreationDate": "2026:04:18 00:01:46+02:00",
                    "Keys:ContentIdentifier": group_id,
                },
                unrelated: {"ExifIFD:DateTimeOriginal": "2026:04:18 00:01:49"},
            }
            entries, skipped = renamer.make_plan_entries(
                root,
                [still, video, unrelated],
                metadata,
                allow_mtime=False,
                run_id="test",
            )
            self.assertFalse(skipped)
            targets = {item["source"]: item["target"] for item in entries}
            self.assertEqual(targets["old.heic"], "2026-04-18_00h-01m-49s.heic")
            self.assertEqual(targets["old.mov"], "2026-04-18_00h-01m-49s.mov")
            self.assertEqual(targets["other.jpg"], "2026-04-18_00h-01m-49s_02.jpg")

    def test_refuses_immich_managed_library(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "immich" / "library" / "admin"
            root.mkdir(parents=True)
            with self.assertRaises(renamer.RenameError):
                renamer.validate_root(root)


if __name__ == "__main__":
    unittest.main()
