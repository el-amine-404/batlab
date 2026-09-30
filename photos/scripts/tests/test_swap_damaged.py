#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "swap-damaged.py"
SPEC = importlib.util.spec_from_file_location("swap_damaged", SCRIPT)
assert SPEC and SPEC.loader
swap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = swap
SPEC.loader.exec_module(swap)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Every file under root with its size and modification time: equal snapshots mean nothing changed."""
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / "share" / "library"
        self.damaged = self.root / "share" / "damaged"
        self.repaired = self.root / "repaired"
        self.library.mkdir(parents=True)
        self.repaired.mkdir()
        self.plan_file = self.root / "plan.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make(self, relative: str, data: bytes, sidecar: str | None = None) -> Path:
        path = self.library / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, ns=(1_500_000_000_000_000_000, 1_500_000_000_000_000_000))
        if sidecar:
            (path.parent / sidecar).write_text('<x:xmpmeta date="2015-01-01"/>')
        return path

    def repair(self, name: str, data: bytes) -> Path:
        path = self.repaired / name
        path.write_bytes(data)
        os.utime(path, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
        return path

    def write_plan(self, entries: list[dict], **extra: object) -> Path:
        plan = {"library_root": str(self.library), "damaged_root": str(self.damaged), "batch": "2026-test",
                "entries": entries, **extra}
        self.plan_file.write_text(json.dumps(plan))
        return self.plan_file

    def run_main(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = swap.main([str(self.plan_file), *args])
        return status, out.getvalue(), err.getvalue()

    def manifest(self) -> dict:
        return json.loads((self.damaged / "manifest.json").read_text())

    def part_files(self) -> list[str]:
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*.part"))


class Fixture(Base):
    """One damaged file with a repair, one unrecoverable file, each with a sidecar."""

    def setUp(self) -> None:
        super().setUp()
        self.bad = self.make("WEDDINGS/Marriage karima/clip é.mp4", b"damaged bytes" * 100, "clip é.mp4.xmp")
        self.lost = self.make("FAMILLY/ZAIIR/lost.mp4", b"unrecoverable" * 50, "lost.mp4.xmp")
        self.good = self.repair("clip.mp4", b"repaired bytes" * 80)
        st = self.bad.stat()
        self.entries = [
            {"path": "WEDDINGS/Marriage karima/clip é.mp4", "action": "replace", "replacement": str(self.good),
             "reason": "picture data damaged during a copy", "expect": {"size": st.st_size, "mtime_ns": st.st_mtime_ns},
             "details": {"kind": "video-errors", "removed_seconds": 4.7}},
            {"path": "FAMILLY/ZAIIR/lost.mp4", "action": "quarantine", "reason": "unrecoverable",
             "details": {"tried": ["untrunc", "salvage"]}},
        ]
        self.write_plan(self.entries)


class PlanValidationTests(Base):
    def problems(self, **override: object) -> str:
        plan = {"library_root": str(self.library), "damaged_root": str(self.damaged), "batch": "b",
                "entries": [{"path": "a.mp4", "action": "quarantine", "reason": "x"}]}
        plan.update(override)
        self.plan_file.write_text(json.dumps(plan))
        with self.assertRaises(swap.PlanError) as caught:
            swap.load_plan(self.plan_file)
        return str(caught.exception)

    def test_a_valid_plan_loads(self) -> None:
        self.plan_file.write_text(json.dumps({"library_root": str(self.library), "damaged_root": str(self.damaged),
                                              "batch": "b", "entries": [{"path": "a.mp4", "action": "quarantine", "reason": "x"}]}))
        plan = swap.load_plan(self.plan_file)
        self.assertEqual((plan.batch, plan.mtime, len(plan.entries)), ("b", "now", 1))

    def test_unusable_files(self) -> None:
        self.plan_file.write_text("{not json")
        with self.assertRaisesRegex(swap.PlanError, "cannot read the plan"):
            swap.load_plan(self.plan_file)
        self.plan_file.write_text("[]")
        with self.assertRaisesRegex(swap.PlanError, "JSON object"):
            swap.load_plan(self.plan_file)
        with self.assertRaisesRegex(swap.PlanError, "cannot read the plan"):
            swap.load_plan(self.root / "missing.json")

    def test_required_keys_and_values(self) -> None:
        self.assertIn('"batch" is missing', self.problems(batch=""))
        self.assertIn("non-empty list", self.problems(entries=[]))
        self.assertIn("letters, digits", self.problems(batch="../x"))
        self.assertIn('"mtime" must be', self.problems(mtime="later"))
        self.assertIn("library folder does not exist", self.problems(library_root=str(self.root / "nope")))

    def test_the_damaged_folder_must_be_outside_the_library(self) -> None:
        self.assertIn("outside the library", self.problems(damaged_root=str(self.library / "damaged")))
        self.assertIn("outside the library", self.problems(damaged_root=str(self.library)))
        self.assertIn("outside the library", self.problems(damaged_root=str(self.root)))  # the library would sit inside it

    def test_paths_cannot_escape_the_library(self) -> None:
        for bad in ("../outside.mp4", "/etc/passwd", "a/../../b.mp4", ""):
            self.assertIn("relative path inside the library",
                          self.problems(entries=[{"path": bad, "action": "quarantine", "reason": "x"}]), bad)

    def test_entry_rules(self) -> None:
        one = {"path": "a.mp4", "action": "quarantine", "reason": "x"}
        self.assertIn("listed twice", self.problems(entries=[one, dict(one)]))
        self.assertIn('"action" must be', self.problems(entries=[dict(one, action="delete")]))
        self.assertIn('a "reason" is required', self.problems(entries=[dict(one, reason=" ")]))
        self.assertIn('needs a "replacement"', self.problems(entries=[dict(one, action="replace")]))
        self.assertIn('takes no "replacement"', self.problems(entries=[dict(one, replacement="x.mp4")]))
        self.assertIn('"expect" must hold whole numbers', self.problems(entries=[dict(one, expect={"size": "big"})]))
        self.assertIn('"details" must be an object', self.problems(entries=[dict(one, details=[1])]))

    def test_every_problem_is_reported_at_once(self) -> None:
        text = self.problems(batch="", entries=[{"path": "../x", "action": "quarantine", "reason": "x"},
                                                {"path": "a", "action": "nope", "reason": "y"}])
        self.assertIn('"batch" is missing', text)
        self.assertIn("relative path inside the library", text)
        self.assertIn('"action" must be', text)
        self.assertEqual(len(text.splitlines()), 3)

    def test_relative_replacements_are_read_relative_to_the_plan(self) -> None:
        self.plan_file.write_text(json.dumps({"library_root": str(self.library), "damaged_root": str(self.damaged), "batch": "b",
                                              "entries": [{"path": "a.mp4", "action": "replace", "replacement": "repaired/a.mp4",
                                                           "reason": "x"}]}))
        self.assertEqual(swap.load_plan(self.plan_file).entries[0].replacement, self.root / "repaired" / "a.mp4")


class PreviewTests(Fixture):
    def test_the_preview_changes_nothing_and_says_so(self) -> None:
        before = snapshot(self.root)
        status, out, _ = self.run_main()
        self.assertEqual(status, 0)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(self.damaged.exists())
        self.assertIn("Nothing changed. Add --apply", out)
        self.assertIn("replace", out)
        self.assertIn("quarantine", out)
        self.assertIn("2 to do, 0 already done, 0 blocked", out)

    def test_a_read_only_library_is_noted_in_the_preview_and_refused_on_apply(self) -> None:
        real = os.access
        with unittest.mock.patch.object(swap.os, "access", lambda p, m: False if Path(p) == self.library else real(p, m)):
            status, out, _ = self.run_main()
            self.assertEqual(status, 0)
            self.assertIn("--apply will refuse to run", out)
            before = snapshot(self.root)
            status, _, err = self.run_main("--apply")
        self.assertEqual(status, 1)
        self.assertIn("read-only or not writable", err)
        self.assertEqual(snapshot(self.root), before)

    def test_blocked_entries_make_the_preview_exit_2(self) -> None:
        self.entries[0]["replacement"] = str(self.repaired / "missing.mp4")
        self.write_plan(self.entries)
        status, out, _ = self.run_main()
        self.assertEqual(status, 2)
        self.assertIn("the replacement file is missing", out)


class ApplyTests(Fixture):
    def test_replace_keeps_the_damaged_original_and_installs_the_repair(self) -> None:
        original = self.bad.read_bytes()
        status, out, err = self.run_main("--apply")
        self.assertEqual((status, err), (0, ""), out)
        self.assertEqual(self.bad.read_bytes(), self.good.read_bytes())
        kept = self.damaged / "2026-test" / "WEDDINGS/Marriage karima/clip é.mp4"
        self.assertEqual(kept.read_bytes(), original)
        self.assertEqual(kept.stat().st_mtime_ns, 1_500_000_000_000_000_000)  # the damaged copy keeps its own date
        self.assertTrue((kept.parent / "clip é.mp4.xmp").is_file())            # sidecar copied out ...
        self.assertTrue((self.bad.parent / "clip é.mp4.xmp").is_file())        # ... and still beside the repaired file
        self.assertEqual(self.part_files(), [])

    def test_a_replaced_file_gets_a_fresh_date_by_default_so_immich_notices(self) -> None:
        self.run_main("--apply")
        self.assertGreater(self.bad.stat().st_mtime_ns, 1_600_000_000_000_000_000)

    def test_mtime_keep_uses_the_replacements_own_date(self) -> None:
        self.write_plan(self.entries, mtime="keep")
        status, out, _ = self.run_main("--apply")
        self.assertEqual(status, 0)
        self.assertEqual(self.bad.stat().st_mtime_ns, 1_600_000_000_000_000_000)
        self.assertIn("Scan All Library Files", out)

    def test_quarantine_moves_the_file_and_its_sidecar_out_of_the_library(self) -> None:
        data = self.lost.read_bytes()
        self.run_main("--apply")
        self.assertFalse(self.lost.exists())
        self.assertFalse((self.lost.parent / "lost.mp4.xmp").exists())
        moved = self.damaged / "2026-test" / "FAMILLY/ZAIIR/lost.mp4"
        self.assertEqual(moved.read_bytes(), data)
        self.assertEqual(moved.stat().st_mtime_ns, 1_500_000_000_000_000_000)
        self.assertTrue((moved.parent / "lost.mp4.xmp").is_file())

    def test_quarantine_across_disks_copies_verifies_then_removes_the_source(self) -> None:
        data = self.lost.read_bytes()
        with unittest.mock.patch.object(swap, "same_device", lambda a, b: False):
            status, _, _ = self.run_main("--apply")
        self.assertEqual(status, 0)
        self.assertFalse(self.lost.exists())
        self.assertEqual((self.damaged / "2026-test" / "FAMILLY/ZAIIR/lost.mp4").read_bytes(), data)
        self.assertEqual(self.part_files(), [])

    def test_the_manifest_records_everything_about_every_damaged_file(self) -> None:
        bad_bytes, lost_bytes = self.bad.read_bytes(), self.lost.read_bytes()
        self.run_main("--apply")
        manifest = self.manifest()
        self.assertEqual((manifest["schema"], manifest["library_root"]), (1, str(self.library)))
        first, second = manifest["files"]  # sorted by path: FAMILLY/... then WEDDINGS/...
        self.assertEqual((first["path"], first["status"], first["action"]), ("FAMILLY/ZAIIR/lost.mp4", "quarantined", "quarantined"))
        self.assertEqual(first["original"]["sha256"], digest(lost_bytes))
        self.assertEqual((first["original"]["size"], first["original"]["mtime"]), (len(lost_bytes), "2017-07-14T02:40:00+00:00"))
        self.assertIsNone(first["replacement"])
        self.assertEqual(first["reason"], "unrecoverable")
        self.assertEqual(first["details"], {"tried": ["untrunc", "salvage"]})
        self.assertEqual(first["damaged_copy"], "2026-test/FAMILLY/ZAIIR/lost.mp4")
        self.assertEqual(first["sidecars"][0]["name"], "lost.mp4.xmp")
        self.assertEqual(second["status"], "replaced")
        self.assertEqual(second["original"]["sha256"], digest(bad_bytes))
        self.assertEqual(second["replacement"]["sha256"], digest(self.good.read_bytes()))
        self.assertEqual(second["replacement"]["source"], str(self.good))
        self.assertEqual(second["details"], {"kind": "video-errors", "removed_seconds": 4.7})
        self.assertEqual((second["batch"], second["reason"]), ("2026-test", "picture data damaged during a copy"))
        self.assertIn("processed_at", second)

    def test_a_second_run_does_nothing(self) -> None:
        self.run_main("--apply")
        before, manifest = snapshot(self.root), (self.damaged / "manifest.json").read_bytes()
        status, out, _ = self.run_main("--apply")
        self.assertEqual(status, 0)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual((self.damaged / "manifest.json").read_bytes(), manifest)

    def test_limit_handles_a_few_files_and_the_next_run_continues(self) -> None:
        status, out, _ = self.run_main("--apply", "--limit", "1")
        self.assertEqual(status, 0)
        self.assertEqual(len(self.manifest()["files"]), 1)
        self.run_main("--apply")
        self.assertEqual(len(self.manifest()["files"]), 2)

    def test_the_manifest_accumulates_across_batches(self) -> None:
        self.run_main("--apply")
        self.make("BIRTHDAYS/later.mp4", b"another damaged file")
        self.plan_file.write_text(json.dumps({"library_root": str(self.library), "damaged_root": str(self.damaged), "batch": "2027-second",
                                              "entries": [{"path": "BIRTHDAYS/later.mp4", "action": "quarantine", "reason": "later"}]}))
        self.run_main("--apply")
        files = self.manifest()["files"]
        self.assertEqual([(f["batch"], f["path"]) for f in files],
                         [("2026-test", "FAMILLY/ZAIIR/lost.mp4"), ("2026-test", "WEDDINGS/Marriage karima/clip é.mp4"),
                          ("2027-second", "BIRTHDAYS/later.mp4")])

    def test_symbolic_links_and_non_files_are_never_touched(self) -> None:
        target = self.library / "linked.mp4"
        target.symlink_to(self.bad)
        self.write_plan([{"path": "linked.mp4", "action": "quarantine", "reason": "x"}])
        status, out, _ = self.run_main("--apply")
        self.assertEqual(status, 2)
        self.assertIn("symbolic link", out)
        self.assertTrue(target.is_symlink())
        (self.library / "folder.mp4").mkdir()
        self.write_plan([{"path": "folder.mp4", "action": "quarantine", "reason": "x"}])
        self.assertIn("not a regular file", self.run_main()[1])

    def test_not_enough_free_space_stops_before_anything_changes(self) -> None:
        before = snapshot(self.root)
        with unittest.mock.patch.object(swap.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})()):
            status, _, err = self.run_main("--apply")
        self.assertEqual(status, 1)
        self.assertIn("not enough free space", err)
        self.assertEqual(snapshot(self.root), before)


class SafetyTests(Fixture):
    def test_a_file_changed_since_it_was_examined_is_left_alone(self) -> None:
        self.bad.write_bytes(b"someone edited this")
        before = snapshot(self.library)
        status, out, _ = self.run_main("--apply")
        self.assertEqual(status, 2)
        self.assertIn("the size changed since it was examined", out)
        self.assertEqual(self.bad.read_bytes(), b"someone edited this")
        self.assertFalse((self.damaged / "2026-test" / "WEDDINGS").exists())
        self.assertEqual([f["path"] for f in self.manifest()["files"]], ["FAMILLY/ZAIIR/lost.mp4"])  # the other file still went
        self.assertEqual(before.get("WEDDINGS/Marriage karima/clip é.mp4"), snapshot(self.library).get("WEDDINGS/Marriage karima/clip é.mp4"))

    def test_a_changed_date_alone_also_blocks_and_ignore_expect_overrides(self) -> None:
        os.utime(self.bad, ns=(1_400_000_000_000_000_000, 1_400_000_000_000_000_000))
        status, out, _ = self.run_main()
        self.assertEqual(status, 2)
        self.assertIn("modification date changed", out)
        status, out, _ = self.run_main("--apply", "--ignore-expect")
        self.assertEqual(status, 0)
        self.assertEqual(self.bad.read_bytes(), self.good.read_bytes())

    def test_a_missing_replacement_blocks_only_that_file(self) -> None:
        self.good.unlink()
        status, out, _ = self.run_main("--apply")
        self.assertEqual(status, 2)
        self.assertIn("the replacement file is missing", out)
        self.assertEqual(self.bad.read_bytes(), b"damaged bytes" * 100)
        self.assertFalse(self.lost.exists())  # the quarantine still happened

    def test_an_empty_replacement_is_refused(self) -> None:
        self.good.write_bytes(b"")
        self.assertIn("the replacement file is empty", self.run_main()[1])

    def test_a_different_file_already_at_the_destination_is_never_overwritten(self) -> None:
        stranger = self.damaged / "2026-test" / "WEDDINGS/Marriage karima/clip é.mp4"
        stranger.parent.mkdir(parents=True)
        stranger.write_bytes(b"something else entirely")
        status, _, err = self.run_main("--apply")
        self.assertEqual(status, 2)
        self.assertIn("a different file is already at the damaged destination", err)
        self.assertEqual(stranger.read_bytes(), b"something else entirely")
        self.assertEqual(self.bad.read_bytes(), b"damaged bytes" * 100)  # the library file is unchanged

    def test_a_corrupted_damaged_copy_stops_the_swap_and_leaves_no_part_file(self) -> None:
        real = swap.shutil.copyfile
        damaged_dir = str(self.damaged)

        def corrupting(source, destination, *a, **k):
            result = real(source, destination, *a, **k)
            if str(destination).startswith(damaged_dir) and str(destination).endswith("clip é.mp4.part"):
                Path(destination).write_bytes(b"bit rot")
            return result
        with unittest.mock.patch.object(swap.shutil, "copyfile", corrupting):
            status, _, err = self.run_main("--apply")
        self.assertEqual(status, 2)
        self.assertIn("does not match the original", err)
        self.assertEqual(self.bad.read_bytes(), b"damaged bytes" * 100)  # nothing replaced without a verified copy
        self.assertFalse((self.damaged / "2026-test" / "WEDDINGS/Marriage karima/clip é.mp4").exists())
        self.assertEqual(self.part_files(), [])

    def test_a_corrupted_staged_replacement_leaves_the_library_file_untouched(self) -> None:
        real = swap.shutil.copyfile

        def corrupting(source, destination, *a, **k):
            result = real(source, destination, *a, **k)
            if str(destination).startswith(str(self.library)) and str(destination).endswith("clip é.mp4.part"):
                Path(destination).write_bytes(b"truncated in transit")
            return result
        with unittest.mock.patch.object(swap.shutil, "copyfile", corrupting):
            status, _, err = self.run_main("--apply")
        self.assertEqual(status, 2)
        self.assertIn("staged replacement does not match", err)
        self.assertEqual(self.bad.read_bytes(), b"damaged bytes" * 100)
        self.assertEqual(self.part_files(), [])

    def test_stray_part_files_from_a_crash_are_overwritten_not_indexed(self) -> None:
        stray_library = self.bad.with_name(self.bad.name + ".part")
        stray_library.write_bytes(b"half a file")
        stray_damaged = self.damaged / "2026-test" / "WEDDINGS/Marriage karima" / "clip é.mp4.part"
        stray_damaged.parent.mkdir(parents=True)
        stray_damaged.write_bytes(b"half a copy")
        status, _, _ = self.run_main("--apply")
        self.assertEqual(status, 0)
        self.assertEqual(self.part_files(), [])
        self.assertEqual(self.bad.read_bytes(), self.good.read_bytes())

    def test_the_replacement_is_staged_under_a_name_immich_ignores(self) -> None:
        seen = []
        real = swap.os.replace

        def spy(source, destination):
            seen.append(Path(source).name)
            return real(source, destination)
        with unittest.mock.patch.object(swap.os, "replace", spy):
            self.run_main("--apply")
        self.assertIn("clip é.mp4.part", seen)
        self.assertTrue(all(name.endswith(".part") or name.endswith("lost.mp4") or name.endswith(".xmp") for name in seen), seen)


class ResumeTests(Fixture):
    def test_interrupted_between_the_swap_and_the_manifest_is_completed_on_the_next_run(self) -> None:
        original = self.bad.read_bytes()
        with unittest.mock.patch.object(swap, "save_record", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.run_main("--apply")
        self.assertEqual(self.bad.read_bytes(), self.good.read_bytes())  # the swap did happen
        self.assertFalse((self.damaged / "manifest.json").exists())
        status, out, err = self.run_main("--apply")
        self.assertEqual((status, err), (0, ""), out)
        record = next(r for r in self.manifest()["files"] if r["status"] == "replaced")
        self.assertEqual(record["original"]["sha256"], digest(original))  # recovered from the damaged copy
        self.assertEqual(record["replacement"]["sha256"], digest(self.good.read_bytes()))
        self.assertEqual(record["sidecars"][0]["name"], "clip é.mp4.xmp")

    def test_an_installed_replacement_without_a_damaged_copy_cannot_be_recorded(self) -> None:
        self.bad.write_bytes(self.good.read_bytes())  # someone installed it by hand: the original is gone
        status, _, err = self.run_main("--apply")
        self.assertEqual(status, 2)
        self.assertIn("no damaged copy exists", err)

    def test_interrupted_quarantine_across_disks_resumes(self) -> None:
        data = self.lost.read_bytes()
        copied = self.damaged / "2026-test" / "FAMILLY/ZAIIR/lost.mp4"
        copied.parent.mkdir(parents=True)
        copied.write_bytes(data)  # the copy finished, the source was not yet removed
        status, _, _ = self.run_main("--apply")
        self.assertEqual(status, 0)
        self.assertFalse(self.lost.exists())
        self.assertEqual(copied.read_bytes(), data)
        self.assertEqual(next(r for r in self.manifest()["files"] if r["path"].endswith("lost.mp4"))["original"]["sha256"], digest(data))

    def test_a_quarantined_file_whose_source_is_already_gone_is_recorded(self) -> None:
        data = self.lost.read_bytes()
        moved = self.damaged / "2026-test" / "FAMILLY/ZAIIR/lost.mp4"
        moved.parent.mkdir(parents=True)
        moved.write_bytes(data)
        self.lost.unlink()
        (self.library / "FAMILLY/ZAIIR/lost.mp4.xmp").unlink()
        status, out, _ = self.run_main("--apply")
        self.assertEqual(status, 0, out)
        self.assertEqual(next(r for r in self.manifest()["files"] if r["path"].endswith("lost.mp4"))["status"], "quarantined")


class VerifyTests(Fixture):
    def test_verify_reports_ok_after_apply(self) -> None:
        self.run_main("--apply")
        status, out, _ = self.run_main("--verify")
        self.assertEqual(status, 0)
        self.assertIn("All files match the manifest.", out)
        self.assertEqual(out.count("OK"), 2)

    def test_verify_before_apply_says_not_done(self) -> None:
        status, out, _ = self.run_main("--verify")
        self.assertEqual(status, 2)
        self.assertEqual(out.count("not done"), 2)

    def test_verify_notices_each_kind_of_damage(self) -> None:
        self.run_main("--apply")
        (self.damaged / "2026-test" / "FAMILLY/ZAIIR/lost.mp4").write_bytes(b"rotted")
        self.bad.write_bytes(b"replaced again by something else")
        status, out, _ = self.run_main("--verify")
        self.assertEqual(status, 2)
        self.assertIn("damaged copy differs from the manifest", out)
        self.assertIn("library file is not the recorded replacement", out)
        self.assertIn("2 file(s) need attention", out)

    def test_verify_notices_a_quarantined_file_that_came_back_and_a_lost_sidecar(self) -> None:
        self.run_main("--apply")
        (self.library / "FAMILLY/ZAIIR/lost.mp4").write_bytes(b"came back")
        (self.damaged / "2026-test" / "FAMILLY/ZAIIR/lost.mp4.xmp").unlink()
        out = self.run_main("--verify")[1]
        self.assertIn("a file is back in the library", out)
        self.assertIn("sidecar lost.mp4.xmp missing or changed", out)

    def test_verify_cannot_be_combined_with_apply(self) -> None:
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
            swap.main([str(self.plan_file), "--verify", "--apply"])
        self.assertEqual(caught.exception.code, 2)


class RollbackTests(Fixture):
    def test_rollback_preview_changes_nothing(self) -> None:
        self.run_main("--apply")
        before = snapshot(self.root)
        status, out, _ = self.run_main("--rollback")
        self.assertEqual(status, 0)
        self.assertIn("would undo", out)
        self.assertIn("Nothing changed", out)
        self.assertEqual(snapshot(self.root), before)

    def test_rollback_restores_both_files_exactly(self) -> None:
        bad, lost = self.bad.read_bytes(), self.lost.read_bytes()
        self.run_main("--apply")
        status, _, err = self.run_main("--rollback", "--apply")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(self.bad.read_bytes(), bad)
        self.assertEqual(self.bad.stat().st_mtime_ns, 1_500_000_000_000_000_000)
        self.assertEqual(self.lost.read_bytes(), lost)
        self.assertTrue((self.lost.parent / "lost.mp4.xmp").is_file())
        self.assertEqual({r["status"] for r in self.manifest()["files"]}, {"rolled-back"})
        self.assertEqual(self.part_files(), [])

    def test_the_batch_can_be_applied_again_after_a_rollback(self) -> None:
        self.run_main("--apply")
        self.run_main("--rollback", "--apply")
        status, _, _ = self.run_main("--apply", "--ignore-expect")
        self.assertEqual(status, 0)
        self.assertEqual(self.bad.read_bytes(), self.good.read_bytes())
        self.assertFalse(self.lost.exists())
        self.assertEqual({r["status"] for r in self.manifest()["files"]}, {"replaced", "quarantined"})

    def test_rollback_refuses_to_overwrite_a_file_changed_after_the_swap(self) -> None:
        self.run_main("--apply")
        self.bad.write_bytes(b"a later manual edit")
        status, _, err = self.run_main("--rollback", "--apply")
        self.assertEqual(status, 2)
        self.assertIn("changed after the swap", err)
        self.assertEqual(self.bad.read_bytes(), b"a later manual edit")

    def test_rollback_refuses_when_something_occupies_the_original_path(self) -> None:
        self.run_main("--apply")
        self.lost.write_bytes(b"a new file with the same name")
        status, _, err = self.run_main("--rollback", "--apply")
        self.assertEqual(status, 2)
        self.assertIn("something already exists", err)
        self.assertEqual(self.lost.read_bytes(), b"a new file with the same name")

    def test_rollback_needs_an_intact_damaged_copy(self) -> None:
        self.run_main("--apply")
        (self.damaged / "2026-test" / "FAMILLY/ZAIIR/lost.mp4").write_bytes(b"rotted")
        status, _, err = self.run_main("--rollback", "--apply")
        self.assertEqual(status, 2)
        self.assertIn("missing or differs", err)
        self.assertFalse(self.lost.exists())

    def test_rollback_on_a_batch_that_was_never_applied(self) -> None:
        status, out, _ = self.run_main("--rollback", "--apply")
        self.assertEqual(status, 0)
        self.assertEqual(out.count("nothing to undo"), 2)


class HelperTests(Base):
    def test_sidecars_are_found_by_both_naming_styles_once(self) -> None:
        clip = self.make("a/clip.mp4", b"x", "clip.mp4.xmp")
        (clip.parent / "clip.xmp").write_text("second style")
        self.assertEqual(sorted(p.name for p in swap.find_sidecars(clip)), ["clip.mp4.xmp", "clip.xmp"])
        self.assertEqual(swap.find_sidecars(self.make("a/none.mp4", b"x")), [])

    def test_the_same_sidecar_listed_twice_by_a_case_insensitive_share_counts_once(self) -> None:
        clip = self.make("a/clip.mp4", b"x", "clip.mp4.xmp")
        real = os.path.samefile
        with unittest.mock.patch.object(swap.os.path, "samefile", lambda a, b: True if Path(a).name.lower() == Path(b).name.lower() else real(a, b)):
            self.assertEqual(len(swap.find_sidecars(clip)), 1)

    def test_a_damaged_manifest_is_reported_not_replaced(self) -> None:
        self.make("a.mp4", b"x")
        self.write_plan([{"path": "a.mp4", "action": "quarantine", "reason": "x"}])
        self.damaged.mkdir(parents=True)
        (self.damaged / "manifest.json").write_text("{half")
        status, _, err = self.run_main("--apply")
        self.assertEqual(status, 1)
        self.assertIn("cannot read", err)
        self.assertEqual((self.damaged / "manifest.json").read_text(), "{half")

    def test_manifest_writes_are_atomic(self) -> None:
        self.damaged.mkdir(parents=True)
        swap.write_json(self.damaged / "m.json", {"a": 1})
        self.assertEqual(json.loads((self.damaged / "m.json").read_text()), {"a": 1})
        self.assertEqual(list(self.damaged.glob("*.part")), [])

    def test_small_formatting_helpers(self) -> None:
        self.assertEqual([swap.human(n) for n in (0, 1023, 1024, 5 * 1024 ** 2)], ["0 B", "1023 B", "1.0 KB", "5.0 MB"])
        self.assertEqual(swap.iso(1_500_000_000_000_000_000), "2017-07-14T02:40:00+00:00")

    def test_bad_arguments(self) -> None:
        self.write_plan([{"path": "a.mp4", "action": "quarantine", "reason": "x"}])
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
            swap.main([str(self.plan_file), "--limit", "0"])
        self.assertEqual(caught.exception.code, 2)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(swap.main([str(self.root / "nope.json")]), 1)
        self.assertIn("Error:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
