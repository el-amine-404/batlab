#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import errno
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from zoneinfo import ZoneInfo


SCRIPT = Path(__file__).resolve().parents[1] / "organize-media.py"
SPEC = importlib.util.spec_from_file_location("organize_media", SCRIPT)
assert SPEC and SPEC.loader
media = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = media
SPEC.loader.exec_module(media)

NOW = dt.datetime(2026, 9, 14, 12, 0, 0)
CASABLANCA = "Africa/Casablanca"


def resolve(relative: str, record: dict, mtime: float = 0.0, rules=(), zone: str = CASABLANCA, use_modified_time=False):
    return media.resolve_capture_time(relative, record, mtime, rules, zone, 1990, NOW, use_modified_time)


def casablanca_offset(local: dt.datetime) -> int:
    return int(local.replace(tzinfo=ZoneInfo(CASABLANCA)).utcoffset().total_seconds() // 60)


class ResolutionTests(unittest.TestCase):
    def test_iphone_photo_with_offset_is_exact(self) -> None:
        result = resolve("travel/IMG_2754.heic", {
            "ExifIFD:DateTimeOriginal": "2026:04:12 23:55:07",
            "ExifIFD:OffsetTimeOriginal": "+01:00",
        })
        self.assertEqual((result.local, result.offset_minutes, result.confidence), ("2026-04-12T23:55:07", 60, "exact"))
        self.assertTrue(media.embedded_is_authoritative(result))

    def test_iphone_video_uses_local_time_of_the_place_not_utc(self) -> None:
        result = resolve("travel/france/2/2026-04-15_14-08-42_IMG_3278.mov", {
            "QuickTime:CreateDate": "2026:04:15 13:08:42",
            "Keys:CreationDate": "2026:04:15 15:08:42+02:00",
        })
        self.assertEqual((result.local, result.offset_minutes, result.confidence), ("2026-04-15T15:08:42", 120, "exact"))

    def test_gps_clock_proves_the_offset_of_a_zoneless_photo(self) -> None:
        result = resolve("a/photo.jpg", {
            "IFD0:DateTimeOriginal": "2019:07:14 18:30:10",
            "GPS:GPSDateStamp": "2019:07:14",
            "GPS:GPSTimeStamp": "22:30:04",
        })
        self.assertEqual((result.confidence, result.offset_minutes), ("exact", -240))
        self.assertFalse(media.embedded_is_authoritative(result))

    def test_zoneless_photo_without_gps_assumes_the_default_zone(self) -> None:
        local = dt.datetime(2016, 12, 24, 21, 39, 1)
        result = resolve("BIRTHDAYS/IMG_20161224_213857540.jpg", {"IFD0:DateTimeOriginal": "2016:12:24 21:39:01"})
        self.assertEqual((result.local, result.confidence, result.zone), (local.isoformat(), "assumed", CASABLANCA))
        self.assertEqual(result.offset_minutes, casablanca_offset(local))

    def test_utc_video_follows_morocco_ramadan_offset(self) -> None:
        normal = resolve("v/a.mp4", {"QuickTime:CreateDate": "2026:01:10 12:00:00"})
        ramadan = resolve("v/b.mp4", {"QuickTime:CreateDate": "2026:03:01 12:00:00"})
        for result, utc in ((normal, dt.datetime(2026, 1, 10, 12)), (ramadan, dt.datetime(2026, 3, 1, 12))):
            expected = utc.replace(tzinfo=dt.timezone.utc).astimezone(ZoneInfo(CASABLANCA))
            self.assertEqual(result.local, expected.replace(tzinfo=None).isoformat())
            self.assertEqual(result.offset_minutes, int(expected.utcoffset().total_seconds() // 60))
            self.assertEqual(result.confidence, "assumed")

    def test_folder_zone_rule_converts_utc_video(self) -> None:
        rules = [media.FolderRule(prefix="travel/france", zone="Europe/Paris")]
        result = resolve("travel/france/2/clip.mp4", {"QuickTime:CreateDate": "2026:04:15 13:08:42"}, rules=rules)
        self.assertEqual((result.local, result.offset_minutes), ("2026-04-15T15:08:42", 120))

    def test_clock_shift_rule_corrects_a_camera_left_on_home_time(self) -> None:
        rules = [media.FolderRule(prefix="trip", zone="Europe/Paris", shift_minutes=60)]
        result = resolve("trip/DSCN0001.jpg", {"ExifIFD:DateTimeOriginal": "2012:07:01 10:00:00"}, rules=rules)
        self.assertEqual((result.local, result.offset_minutes), ("2012-07-01T11:00:00", 120))
        self.assertIn("shifted +60m", result.evidence)

    def test_fake_dates_are_rejected_and_the_next_source_is_used(self) -> None:
        cases = {
            "quicktime epoch": {"QuickTime:CreateDate": "1904:01:01 00:00:00"},
            "future": {"ExifIFD:DateTimeOriginal": "2031:05:05 10:00:00"},
            "camera reset": {"ExifIFD:DateTimeOriginal": "2002:01:01 00:00:00"},
            "old year": {"ExifIFD:DateTimeOriginal": "1980:06:06 10:00:00"},
        }
        for label, record in cases.items():
            with self.subTest(label):
                name = "clip.mp4" if "QuickTime" in next(iter(record)) else "photo.jpg"
                result = resolve(f"x/IMG_20160318_223555_{name}", record)
                self.assertEqual(result.local, "2016-03-18T22:35:55")
                self.assertEqual(len(result.rejected), 1)

    def test_file_already_named_with_a_fake_date_is_renamed(self) -> None:
        result = resolve("x/1903-12-31_23h-29m-40s.mp4", {"QuickTime:CreateDate": "1904:01:01 00:00:00"},
                         mtime=dt.datetime(2018, 7, 29, 21, 0, tzinfo=dt.timezone.utc).timestamp(), use_modified_time=True)
        self.assertNotEqual(result.local[:4], "1903")
        self.assertEqual(result.confidence, "weak")
        self.assertEqual(len(result.rejected), 2)

    def test_whatsapp_name_gives_a_day_and_borrows_time_only_from_the_same_day(self) -> None:
        other_day = dt.datetime(2018, 1, 23, 11, 24, tzinfo=dt.timezone.utc).timestamp()
        day_only = resolve("F/IMG-20170928-WA0021.jpg", {}, mtime=other_day)
        self.assertEqual((day_only.precision, day_only.confidence, day_only.local), ("day", "weak", "2017-09-28T00:00:00"))
        same_day = dt.datetime(2018, 8, 24, 15, 46, tzinfo=dt.timezone.utc).timestamp()
        with_time = resolve("F/IMG-20180824-WA0011.jpg", {}, mtime=same_day, use_modified_time=True)
        self.assertEqual((with_time.precision, with_time.confidence), ("second", "weak"))
        self.assertTrue(with_time.local.startswith("2018-08-24T16:46"))
        without_modified_time = resolve("F/IMG-20180824-WA0011.jpg", {}, mtime=same_day)
        self.assertEqual((without_modified_time.precision, without_modified_time.local), ("day", "2018-08-24T00:00:00"))

    def test_dated_folder_names_are_used(self) -> None:
        result = resolve("REUNIONS/2022-OCT-01/random.jpg", {}, mtime=0)
        self.assertEqual((result.local, result.precision), ("2022-10-01T00:00:00", "day"))
        span = resolve("CITIES/2015-09-06_TO_2015-09-08/random.jpg", {}, mtime=0)
        self.assertEqual(span.local, "2015-09-06T00:00:00")

    def test_unix_millisecond_names(self) -> None:
        result = resolve("K/1373721639632.jpg", {})
        self.assertEqual((result.local, result.confidence), ("2013-07-13T13:20:39", "assumed"))

    def test_sidecars_only_where_the_file_alone_would_mislead(self) -> None:
        embedded_local = resolve("a/old.jpg", {"IFD0:DateTimeOriginal": "2016:12:24 21:39:01"})
        utc_video = resolve("a/clip.mp4", {"QuickTime:CreateDate": "2026:01:10 12:00:00"})
        from_name = resolve("a/IMG_20160318_223555.jpg", {})
        self.assertFalse(media.sidecar_needed(embedded_local))
        self.assertTrue(media.sidecar_needed(utc_video))
        self.assertTrue(media.sidecar_needed(from_name))

    def test_zero_dates_are_reported(self) -> None:
        result = resolve("a/DSCN0931.mp4", {"QuickTime:CreateDate": "0000:00:00 00:00:00"}, mtime=1.7e9)
        self.assertIn("empty date", result.rejected[0])

    def test_modified_time_is_the_last_resort(self) -> None:
        stamp = dt.datetime(2008, 7, 11, 10, 33, tzinfo=dt.timezone.utc).timestamp()
        result = resolve("OLD/Photo_047.jpg", {}, mtime=stamp, use_modified_time=True)
        self.assertEqual((result.confidence, result.evidence), ("weak", "file modified date"))

    def test_modified_time_is_not_used_by_default(self) -> None:
        stamp = dt.datetime(2008, 7, 11, 10, 33, tzinfo=dt.timezone.utc).timestamp()
        result = resolve("OLD/Photo_047.jpg", {}, mtime=stamp)
        self.assertEqual((result.confidence, result.local), ("none", ""))

    def test_separated_date_and_time_names(self) -> None:
        for name, expected in (("2015_08_03_23h_28m_08s.mp4", "2015-08-03T23:28:08"),
                               ("Screenshot_2019-05-12-18-30-45.png", "2019-05-12T18:30:45"),
                               ("IMG_2019.05.12_18.30.45.jpg", "2019-05-12T18:30:45")):
            with self.subTest(name):
                result = resolve(f"x/{name}", {})
                self.assertEqual((result.local, result.confidence), (expected, "assumed"))

    def test_separated_date_only_names(self) -> None:
        for name in ("VID_2019-05-12.mp4", "2019_05_12_1.png"):
            with self.subTest(name):
                result = resolve(f"x/{name}", {})
                self.assertEqual((result.local, result.precision), ("2019-05-12T00:00:00", "day"))

    def test_mixed_separators_are_not_a_date(self) -> None:
        self.assertEqual(resolve("x/2019-05_12.png", {}).confidence, "none")


class NamingTests(unittest.TestCase):
    def test_target_names(self) -> None:
        moment = dt.datetime(2017, 11, 18, 21, 49, 59)
        self.assertEqual(media.target_name(moment, "second", "JPG"), "2017-11-18_21h-49m-59s.jpg")
        self.assertEqual(media.target_name(moment, "second", "jpg", collision=2), "2017-11-18_21h-49m-59s_02.jpg")
        self.assertEqual(media.target_name(moment, "day", "jpg"), "2017-11-18_date-only_01.jpg")

    def plan(self, root: Path, metadata: dict, **kwargs):
        files = sorted(metadata)
        return media.make_plan_entries(root, files, metadata, "test", now=NOW, **kwargs)

    def test_live_photo_pair_shares_the_still_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            still, video, other = root / "a.heic", root / "a.mov", root / "b.jpg"
            for path in (still, video, other):
                path.touch()
            group = "95324ce3-bc38-40bf-ba73-e9ed15b10668"
            entries, skipped = self.plan(root, {
                still: {"ExifIFD:DateTimeOriginal": "2026:04:18 00:01:49", "ExifIFD:OffsetTimeOriginal": "+02:00",
                        "Apple:MediaGroupUUID": group},
                video: {"Keys:CreationDate": "2026:04:18 00:01:46+02:00", "Keys:ContentIdentifier": group},
                other: {"ExifIFD:DateTimeOriginal": "2026:04:18 00:01:49", "ExifIFD:OffsetTimeOriginal": "+02:00"},
            })
            self.assertFalse(skipped)
            targets = {item["source"]: item for item in entries}
            self.assertEqual(targets["a.heic"]["target"], "2026-04-18_00h-01m-49s.heic")
            self.assertEqual(targets["a.mov"]["target"], "2026-04-18_00h-01m-49s.mov")
            self.assertEqual(targets["b.jpg"]["target"], "2026-04-18_00h-01m-49s_02.jpg")
            self.assertIsNone(targets["a.heic"]["sidecar"])
            self.assertIsNone(targets["b.jpg"]["sidecar"], "exact embedded time needs no sidecar")
            self.assertIsNotNone(targets["a.mov"]["sidecar"], "video time aligned to the still needs a sidecar")

    def test_already_correct_name_is_not_bumped_by_a_neighbour(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            keeper, newcomer = root / "2020-05-05_21h-45m-30s.jpg", root / "IMG_0001.jpg"
            keeper.touch()
            newcomer.touch()
            record = {"ExifIFD:DateTimeOriginal": "2020:05:05 21:45:30", "ExifIFD:OffsetTimeOriginal": "+01:00"}
            entries, _ = self.plan(root, {newcomer: dict(record), keeper: dict(record)})
            targets = {item["source"]: item["target"] for item in entries}
            self.assertEqual(targets["2020-05-05_21h-45m-30s.jpg"], "2020-05-05_21h-45m-30s.jpg")
            self.assertEqual(targets["IMG_0001.jpg"], "2020-05-05_21h-45m-30s_02.jpg")

    def test_day_only_names_count_from_01(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            first, second = root / "IMG-20170928-WA0021.jpg", root / "IMG-20170928-WA0022.jpg"
            for path in (first, second):
                path.touch()
                os.utime(path, (0, dt.datetime(2019, 1, 1, tzinfo=dt.timezone.utc).timestamp()))
            entries, _ = self.plan(root, {first: {}, second: {}})
            self.assertEqual(sorted(item["target"] for item in entries),
                             ["2017-09-28_date-only_01.jpg", "2017-09-28_date-only_02.jpg"])
            self.assertTrue(all(item["flagged"] and item["sidecar"] for item in entries))

    def test_foreign_sidecar_blocks_the_rename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            photo = root / "IMG_20160318_223555.jpg"
            photo.touch()
            (root / "IMG_20160318_223555.jpg.xmp").write_text("<x:xmpmeta>darktable</x:xmpmeta>")
            entries, skipped = self.plan(root, {photo: {}})
            self.assertFalse(entries)
            self.assertIn("another tool", skipped[0]["reason"])

    def test_refuses_immich_managed_library(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "immich" / "library" / "admin"
            root.mkdir(parents=True)
            with self.assertRaises(media.RenameError):
                media.validate_root(root)

    def test_rules_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rules_path = Path(temporary) / "rules.conf"
            rules_path.write_text("# comment\ntravel/usa  zone America/New_York\ntravel/usa/gopro  clock local\n"
                                  "old/scan  date 1998-06-01\ntrip  shift -0h30m\n")
            rules = media.parse_rules(rules_path)
            merged = media.effective_rule(rules, "travel/usa/gopro/GX01.mp4")
            self.assertEqual((merged.zone, merged.clock_local), ("America/New_York", True))
            self.assertEqual(media.effective_rule(rules, "trip/x.jpg").shift_minutes, -30)
            rules_path.write_text("x zone Mars/Olympus\n")
            with self.assertRaises(media.RenameError):
                media.parse_rules(rules_path)


@unittest.skipUnless(shutil.which("exiftool"), "exiftool is required for the end-to-end test")
class EndToEndTests(unittest.TestCase):
    def test_plan_apply_rollback_with_sidecars_and_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, state = base / "library", base / "state"
            (root / "OLD").mkdir(parents=True)
            (root / "F").mkdir()
            names = {
                "F/IMG_20160318_223555.jpg": None,
                "F/IMG-20170928-WA0021.jpg": None,
                "OLD/Photo_047.jpg": None,
                "OLD/._Photo_047.jpg": None,
                "F/1903-12-31_23h-29m-40s.mp4": None,
            }
            stamp = dt.datetime(2008, 7, 11, 10, 33, tzinfo=dt.timezone.utc).timestamp()
            for relative in names:
                path = root / relative
                path.write_bytes(b"not really media")
                os.utime(path, (stamp, stamp))

            self.assertEqual(media.main(["plan", str(root), "--recursive", "--use-modified-time",
                                         "--state-base", str(state)]), 0)
            bundle = next(state.iterdir())
            manifest = json.loads((bundle / "manifest.json").read_text())
            self.assertNotIn("OLD/._Photo_047.jpg", {item["source"] for item in manifest["entries"]})
            self.assertEqual(media.main(["apply", str(bundle), "--yes"]), 1, "weak dates need --accept-weak")
            self.assertEqual(media.main(["apply", str(bundle), "--yes", "--accept-weak"]), 0)

            renamed = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
            self.assertIn("F/2016-03-18_22h-35m-55s.jpg", renamed)
            self.assertIn("F/2017-09-28_date-only_01.jpg", renamed)
            self.assertIn("OLD/2008-07-11_11h-33m-00s.jpg", renamed, "10:33 UTC is 11:33 in Morocco's 2008 summer time")
            self.assertIn("F/2008-07-11_11h-33m-00s.mp4", renamed, "fake 1903 name replaced by the modified date")
            self.assertIn("OLD/._Photo_047.jpg", renamed, "AppleDouble files are left alone")

            sidecar = root / "OLD/2008-07-11_11h-33m-00s.jpg.xmp"
            read = json.loads(subprocess.run(["exiftool", "-j", "-G1", str(sidecar)],
                                             capture_output=True, text=True, check=True).stdout)[0]
            self.assertEqual(read["XMP-exif:DateTimeOriginal"], "2008:07:11 11:33:00+01:00")
            self.assertEqual(read["XMP-xmpMM:PreservedFileName"], "Photo_047.jpg")
            self.assertIn(media.REVIEW_TAG, str(read["XMP-dc:Subject"]))

            with (root / ".organize/unverified.tsv").open() as handle:
                flagged = {row["path"] for row in csv.DictReader(handle, dialect="excel-tab")}
            self.assertIn("OLD/2008-07-11_11h-33m-00s.jpg", flagged)
            self.assertNotIn("F/2016-03-18_22h-35m-55s.jpg", flagged)

            self.assertEqual(media.main(["rollback", str(bundle), "--yes"]), 0)
            restored = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
                        and ".organize" not in path.parts}
            self.assertEqual(restored, set(names))
            flagged_after = (root / ".organize/unverified.tsv").read_text().strip().splitlines()
            self.assertEqual(len(flagged_after), 1, "only the header remains")


class GpsTests(unittest.TestCase):
    def test_western_longitude_uses_its_reference(self) -> None:
        record = {"GPS:GPSLatitude": 31.51, "GPS:GPSLatitudeRef": "N",
                  "GPS:GPSLongitude": 9.77, "GPS:GPSLongitudeRef": "W"}
        self.assertEqual(media.gps_position(record), (31.51, -9.77))

    def test_southern_latitude_uses_its_reference(self) -> None:
        record = {"GPS:GPSLatitude": 33.87, "GPS:GPSLatitudeRef": "South",
                  "GPS:GPSLongitude": 151.21, "GPS:GPSLongitudeRef": "East"}
        self.assertEqual(media.gps_position(record), (-33.87, 151.21))

    def test_composite_coordinates_are_already_signed(self) -> None:
        record = {"Composite:GPSLatitude": 31.51, "Composite:GPSLongitude": -9.77, "GPS:GPSLongitude": 9.77}
        self.assertEqual(media.gps_position(record), (31.51, -9.77))

    def test_quicktime_coordinates_are_signed(self) -> None:
        record = {"Keys:GPSCoordinates": "31.5100 -9.7700 12.3"}
        self.assertEqual(media.gps_position(record), (31.51, -9.77))

    @unittest.skipIf(media.TimezoneFinder is None, "timezonefinder is not installed")
    def test_essaouira_resolves_to_casablanca(self) -> None:
        record = {"GPS:GPSLatitude": 31.51, "GPS:GPSLatitudeRef": "N",
                  "GPS:GPSLongitude": 9.77, "GPS:GPSLongitudeRef": "W"}
        self.assertEqual(media.zone_at(media.gps_position(record)), "Africa/Casablanca")


class RenameTests(unittest.TestCase):
    def without_renameat2_or_links(self, errno_value: int):
        platform = unittest.mock.patch.object(media.sys, "platform", "darwin")
        link = unittest.mock.patch.object(media.os, "link", side_effect=OSError(errno_value, os.strerror(errno_value)))
        return platform, link

    def test_falls_back_to_checked_rename_without_hard_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp, "a.jpg"), Path(temp, "sub/b.jpg")
            source.write_text("a")
            platform, link = self.without_renameat2_or_links(errno.EPERM)
            with platform, link, contextlib.redirect_stderr(io.StringIO()):
                media.rename_noreplace(source, target)
            self.assertFalse(source.exists())
            self.assertEqual(target.read_text(), "a")

    def test_checked_rename_never_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp, "a.jpg"), Path(temp, "b.jpg")
            source.write_text("a")
            target.write_text("b")
            platform, link = self.without_renameat2_or_links(errno.EPERM)
            with platform, link, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(FileExistsError):
                media.rename_noreplace(source, target)
            self.assertEqual((source.read_text(), target.read_text()), ("a", "b"))

    def test_other_link_errors_are_raised(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp, "a.jpg"), Path(temp, "b.jpg")
            source.write_text("a")
            platform, link = self.without_renameat2_or_links(errno.EACCES)
            with platform, link, self.assertRaises(PermissionError):
                media.rename_noreplace(source, target)
            self.assertTrue(source.exists())
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
