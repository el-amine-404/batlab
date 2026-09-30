#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "compare-media.py"
SPEC = importlib.util.spec_from_file_location("compare_media", SCRIPT)
assert SPEC and SPEC.loader
cm = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cm
SPEC.loader.exec_module(cm)

needs_ffmpeg = unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is required")


def ffmpeg(*args: object) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *map(str, args)], check=True)


def run_main(*args: object) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = cm.main([str(a) for a in args])
    return status, out.getvalue(), err.getvalue()


@needs_ffmpeg
class MediaCase(unittest.TestCase):
    """A 6 s clip with a keyframe every second, mono AAC audio, a creation date and a rotation flag."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.temp.name)
        plain = cls.dir / "plain.mp4"
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=64x48:rate=15:duration=6", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
               "-c:v", "libx264", "-g", "15", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-ac", "1",
               "-metadata", "creation_time=2016-07-26T22:06:13Z", "-metadata:s:v", "creation_time=2016-07-26T22:06:13Z",
               "-metadata:s:a", "creation_time=2016-07-26T22:06:13Z", plain)
        cls.old = cls.dir / "old.mp4"
        ffmpeg("-display_rotation:v:0", "-90", "-i", plain, "-map", "0", "-c", "copy", "-map_metadata", "0",
               "-map_metadata:s:v:0", "0:s:v:0", "-map_metadata:s:a:0", "0:s:a:0", "-fflags", "+bitexact", cls.old)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def make(self, name: str, *args: object) -> Path:
        path = self.dir / name
        ffmpeg("-i", self.old, *args, path)
        return path

    KEEP = ("-map_metadata", "0", "-map_metadata:s:v:0", "0:s:v:0", "-map_metadata:s:a:0", "0:s:a:0", "-fflags", "+bitexact")

    def reencoded_audio(self, name: str = "reenc.mp4", bitrate: str = "96k") -> Path:
        return self.make(name, "-map", "0", "-c", "copy", "-c:a", "aac", "-b:a", bitrate, *self.KEEP)

    def cut_middle(self, name: str = "cut.mp4") -> Path:
        a, b, joined = self.dir / "p1.mp4", self.dir / "p2.mp4", self.dir / "joined.mp4"
        ffmpeg("-i", self.old, "-t", "2", "-map", "0", "-c", "copy", a)
        ffmpeg("-ss", "4", "-i", self.old, "-map", "0", "-c", "copy", b)
        (self.dir / "list.txt").write_text(f"file '{a}'\nfile '{b}'\n")
        ffmpeg("-f", "concat", "-safe", "0", "-i", self.dir / "list.txt", "-c", "copy", joined)
        path = self.dir / name
        ffmpeg("-i", joined, "-i", self.old, "-map", "0", "-c", "copy", "-map_metadata", "1", "-map_metadata:s:v:0", "1:s:v:0",
               "-map_metadata:s:a:0", "1:s:a:0", "-fflags", "+bitexact", "-movflags", "+use_metadata_tags", path)
        return path

    def compare(self, new: Path, **kwargs: object) -> "cm.Result":
        return cm.compare(self.old, new, **kwargs)


class VerdictTests(MediaCase):
    def test_a_copy_is_identical(self) -> None:
        copy = self.dir / "copy.mp4"
        shutil.copyfile(self.old, copy)
        result = self.compare(copy)
        self.assertEqual((result.verdict, result.changes, result.failures), ("IDENTICAL", [], []))
        self.assertEqual(result.facts["video_packets"]["identical"], result.facts["video_packets"]["old"])
        self.assertTrue(result.facts["audio_identical"])

    def test_re_encoded_audio_with_untouched_video_is_changed_and_says_how_close(self) -> None:
        result = self.compare(self.reencoded_audio())
        self.assertEqual((result.verdict, result.failures), ("CHANGED", []), result.failures)
        joined = " | ".join(result.changes)
        self.assertIn("audio re-encoded", joined)
        self.assertRegex(joined, r"audio bit rate: \d+ -> \d+ kbps")
        self.assertRegex(joined, r"audio loudness per second within [\d.]+ dB of the old")
        self.assertEqual(result.facts["video_packets"]["identical"], result.facts["video_packets"]["old"])  # picture untouched
        self.assertFalse(result.facts["audio_identical"])
        self.assertLess(result.facts["audio_levels"]["max_db"], 1.0)  # the same sound, encoded again
        self.assertNotIn("video removed", joined)
        self.assertEqual(result.facts["removed"], [])
        self.assertEqual(result.facts["audio_removed"], [])  # every packet differs, but no time was lost

    def test_similarity_can_be_skipped(self) -> None:
        result = self.compare(self.reencoded_audio(), similarity=False)
        self.assertNotIn("audio_levels", result.facts)
        self.assertNotIn("loudness", " ".join(result.changes))

    def test_a_removed_stretch_is_located_and_measured(self) -> None:
        result = self.compare(self.cut_middle())
        self.assertEqual(result.failures, [], result.failures)
        self.assertEqual(result.verdict, "CHANGED")
        (piece,) = result.facts["removed"]
        self.assertAlmostEqual(piece["start"], 2.0, delta=0.15)
        self.assertAlmostEqual(piece["end"], 4.0, delta=0.15)
        self.assertGreaterEqual(piece["packets"], 25)
        text = " ".join(result.changes)
        self.assertRegex(text, r"video removed from 2\.\d\d s to 4\.\d\d s")
        self.assertRegex(text, r"audio removed from")
        self.assertNotIn("re-encoded", text)  # cut audio is not re-encoded audio
        self.assertTrue(result.facts["audio_removed"])
        self.assertRegex(text, r"duration 6\.\d\d s -> 4\.\d\d s")

    def test_a_different_loudness_fails_but_a_tiny_shift_does_not(self) -> None:
        quiet = self.make("quiet.mp4", "-map", "0", "-c", "copy", "-c:a", "aac", "-b:a", "64k", "-af", "volume=-12dB", *self.KEEP)
        result = self.compare(quiet)
        self.assertEqual(result.verdict, "CHECK")
        self.assertTrue(any("dB louder or quieter" in f for f in result.failures), result.failures)
        shifted = self.make("shifted.mp4", "-map", "0", "-c", "copy", "-c:a", "aac", "-b:a", "64k", "-af", "adelay=1:all=1", *self.KEEP)
        self.assertEqual(self.compare(shifted).failures, [])

    def test_audio_running_past_the_picture_may_be_trimmed_but_not_audio_under_it(self) -> None:
        long_audio = self.dir / "long-audio.mp4"   # 6 s of picture, 9 s of audio
        ffmpeg("-i", self.old, "-map", "0", "-c", "copy", "-c:a", "aac", "-b:a", "64k", "-af", "apad=pad_dur=3", *self.KEEP, long_audio)
        trimmed = self.dir / "trimmed.mp4"
        ffmpeg("-i", long_audio, "-map", "0", "-c", "copy", "-t", "6.02", *self.KEEP, trimmed)
        result = cm.compare(long_audio, trimmed)
        self.assertEqual(result.failures, [], result.failures)
        self.assertTrue(any("audio after the end of the picture removed from 6." in c for c in result.changes), result.changes)
        reencoded = self.dir / "trimmed-reencoded.mp4"   # the same, with the audio encoded again
        ffmpeg("-i", long_audio, "-map", "0", "-c", "copy", "-c:a", "aac", "-b:a", "96k", "-t", "6.02", *self.KEEP, reencoded)
        again = cm.compare(long_audio, reencoded)
        self.assertEqual(again.failures, [], again.failures)
        self.assertTrue(any("audio after the end of the picture removed:" in c and "picture ends at 6." in c for c in again.changes),
                        again.changes)
        self.assertEqual(result.facts["video_packets"]["identical"], result.facts["video_packets"]["old"])

    def test_no_audio_is_owed_where_the_old_file_had_none_under_the_picture(self) -> None:
        holed = self.dir / "silent-end.mp4"   # audio stops at 4 s; the picture runs to 6 s
        ffmpeg("-i", self.old, "-map", "0", "-c", "copy", "-c:a", "aac", "-b:a", "64k", "-af", "atrim=0:4,apad=pad_dur=2,aselect='lt(t,4)+gt(t,7)',asetpts=PTS", *self.KEEP, holed)
        same = self.dir / "silent-end-again.mp4"
        ffmpeg("-i", holed, "-map", "0", "-c", "copy", "-c:a", "aac", "-b:a", "96k", *self.KEEP, same)
        self.assertEqual(cm.compare(holed, same).failures, [])

    HOLE = "aselect='lt(t,2)+gt(t,4)',asetpts=PTS"   # the audio has nothing between 2 s and 4 s

    def test_audio_that_never_decoded_is_located_and_not_blamed_on_the_repair(self) -> None:
        holed = self.make("holed.mp4", "-map", "0", "-c", "copy", "-c:a", "aac", "-b:a", "64k", "-af", self.HOLE, *self.KEEP)
        again = self.dir / "holed-again.mp4"
        ffmpeg("-i", holed, "-map", "0", "-c", "copy", "-c:a", "aac", "-b:a", "96k", *self.KEEP, again)
        result = cm.compare(holed, again)
        self.assertEqual(result.failures, [], result.failures)
        (start, end), = result.facts["audio_gaps"]["old"]
        self.assertAlmostEqual(start, 2.0, delta=0.1)
        self.assertAlmostEqual(end, 4.0, delta=0.1)
        self.assertTrue(any("missing in the old file" in c and "2.0" in c and "4.0" in c for c in result.changes), result.changes)
        self.assertIn("audio already lost in the original 2.0s", cm.summary_line(result))

    def test_audio_lost_by_the_repair_fails_the_check(self) -> None:
        holed = self.make("lost.mp4", "-map", "0", "-c", "copy", "-c:a", "aac", "-b:a", "64k", "-af", self.HOLE, *self.KEEP)
        result = self.compare(holed)   # the old file has all its audio; the new one does not
        self.assertEqual(result.verdict, "CHECK")
        self.assertTrue(any("audio the old file still had is missing in the new one" in f for f in result.failures), result.failures)

    def test_the_writer_tag_is_listed_but_is_not_a_failure(self) -> None:
        written = self.dir / "written.mp4"   # without bitexact ffmpeg names itself in an encoder tag
        ffmpeg("-i", self.old, "-map", "0", "-c", "copy", "-map_metadata", "0", "-map_metadata:s:v:0", "0:s:v:0",
               "-map_metadata:s:a:0", "0:s:a:0", written)
        result = cm.compare(written, self.old)
        self.assertEqual(result.failures, [], result.failures)
        self.assertTrue(any("writer tag dropped: encoder" in c for c in result.changes), result.changes)

    def test_a_lost_creation_date_fails_the_check(self) -> None:
        stripped = self.make("nodate.mp4", "-map", "0", "-c", "copy", "-map_metadata", "-1", "-fflags", "+bitexact")
        result = self.compare(stripped)
        self.assertEqual(result.verdict, "CHECK")
        self.assertTrue(any("tag lost: creation_time" in f for f in result.failures), result.failures)

    def test_a_changed_tag_and_an_added_tag(self) -> None:
        edited = self.make("edited.mp4", "-map", "0", "-c", "copy", *self.KEEP, "-metadata", "creation_time=2020-01-01T00:00:00Z",
                           "-metadata", "title=hello")
        result = self.compare(edited)
        self.assertTrue(any("tag changed: creation_time" in f for f in result.failures), result.failures)
        self.assertTrue(any("tag added: title = hello" in c for c in result.changes), result.changes)

    def test_a_changed_rotation_fails_the_check(self) -> None:
        turned = self.make("turned.mp4", "-map", "0", "-c", "copy", *self.KEEP)
        rotated = self.dir / "rotated.mp4"
        ffmpeg("-display_rotation:v:0", "90", "-i", turned, "-map", "0", "-c", "copy", *self.KEEP, rotated)
        result = self.compare(rotated)
        self.assertEqual(result.verdict, "CHECK")
        self.assertTrue(any("rotation changed: -90 -> 90" in f for f in result.failures), result.failures)
        self.assertEqual(self.compare(turned).facts["rotation"], -90)

    def test_a_missing_stream_fails_the_check(self) -> None:
        silent = self.make("silent.mp4", "-map", "0:v", "-c", "copy", *self.KEEP)
        result = self.compare(silent)
        self.assertEqual(result.verdict, "CHECK")
        self.assertTrue(any("the streams differ" in f for f in result.failures), result.failures)

    def test_shorter_audio_is_reported_where_it_ends(self) -> None:
        short = self.make("short.mp4", "-map", "0:v", "-map", "0:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "64k",
                          "-af", "atrim=0:3", *self.KEEP)
        result = self.compare(short)
        self.assertEqual(result.verdict, "CHECK")
        self.assertTrue(any("shorter than the old audio decoded to" in f and "ends at 3." in f for f in result.failures), result.failures)
        gap = result.facts["audio_ends_before_video"]
        self.assertAlmostEqual(gap["audio"], 3.0, delta=0.1)
        self.assertAlmostEqual(gap["video"], 6.0, delta=0.1)
        self.assertIn("silence from 3.0", "\n".join(cm.render_detail(result)))

    def test_a_damaged_new_video_fails_with_the_decoder_message_and_an_altered_picture(self) -> None:
        data = bytearray(self.old.read_bytes())
        start = data.index(b"mdat") + 2000
        for offset in range(start, start + 300):
            data[offset] ^= 0xFF
        broken = self.dir / "broken.mp4"
        broken.write_bytes(bytes(data))
        result = self.compare(broken)
        self.assertEqual(result.verdict, "CHECK")
        text = "\n".join(result.failures)
        self.assertIn("still has", text)
        self.assertIn("picture was altered", text)

    def test_the_new_file_must_be_readable(self) -> None:
        junk = self.dir / "junk.mp4"
        junk.write_bytes(b"not a video at all")
        with self.assertRaisesRegex(cm.MediaError, "cannot be read as media"):
            self.compare(junk)


class RenderingTests(MediaCase):
    def test_the_detail_report_says_what_changed_and_what_did_not(self) -> None:
        text = "\n".join(cm.render_detail(self.compare(self.cut_middle())))
        self.assertIn("cut.mp4: CHANGED", text)
        self.assertIn("changed: video removed from", text)
        self.assertIn("metadata: all kept, rotation -90", text)
        self.assertRegex(text, r"video:\s+\d+ of \d+ packets identical")
        same = self.dir / "same.mp4"
        shutil.copyfile(self.old, same)
        self.assertIn("nothing changed", "\n".join(cm.render_detail(self.compare(same))))

    def test_the_one_line_summary(self) -> None:
        line = cm.summary_line(self.compare(self.cut_middle()))
        self.assertRegex(line, r"6\.\ds -> 4\.\ds, video -\d\.\ds, audio -\d\.\ds")
        line = cm.summary_line(self.compare(self.reencoded_audio("reenc2.mp4")))
        self.assertIn("audio re-encoded", line)
        self.assertNotRegex(line, r"audio -")


class CommandLineTests(MediaCase):
    def test_two_files(self) -> None:
        status, out, _ = run_main(self.old, self.reencoded_audio("cli-a.mp4"), "--no-similarity")
        self.assertEqual(status, 0)
        self.assertIn("CHANGED", out)
        self.assertIn("audio re-encoded", out)
        self.assertIn("1 compared: 0 identical, 1 changed, 0 need a check", out)

    def test_a_folder_of_repairs_against_the_originals(self) -> None:
        old_root, new_root = self.dir / "library", self.dir / "repaired"
        (old_root / "A/2016").mkdir(parents=True)
        (old_root / "B").mkdir()
        (new_root / "A/2016").mkdir(parents=True)
        (new_root / "B").mkdir()
        shutil.copyfile(self.old, old_root / "A/2016/one.mp4")
        shutil.copyfile(self.old, old_root / "B/two.mp4")
        shutil.copyfile(self.reencoded_audio("f1.mp4"), new_root / "A/2016/one.mp4")
        shutil.copyfile(self.cut_middle("f2.mp4"), new_root / "B/two.mp4")
        (new_root / "manifest.json").write_text("{}")           # not media: ignored
        (new_root / "A/.hidden.mp4").write_bytes(b"junk")       # hidden: ignored
        status, out, err = run_main("--old-root", old_root, "--new-root", new_root, "--json", self.dir / "all.json")
        self.assertEqual(status, 0, out)
        self.assertIn("CHANGED   A/2016/one.mp4", out)
        self.assertIn("CHANGED   B/two.mp4", out)
        self.assertIn("2 compared: 0 identical, 2 changed, 0 need a check", out)
        self.assertRegex(out, r"Video removed in total: \d\.\d s")
        self.assertIn("[1/2] A/2016/one.mp4", err)
        saved = json.loads((self.dir / "all.json").read_text())
        self.assertEqual([r["name"] for r in saved["results"]], ["A/2016/one.mp4", "B/two.mp4"])
        self.assertEqual(saved["results"][1]["facts"]["removed"][0]["packets"] >= 25, True)

    def test_a_missing_original_or_a_failed_check_exits_2(self) -> None:
        old_root, new_root = self.dir / "o", self.dir / "n"
        old_root.mkdir()
        new_root.mkdir()
        shutil.copyfile(self.old, new_root / "orphan.mp4")
        status, out, _ = run_main("--old-root", old_root, "--new-root", new_root)
        self.assertEqual(status, 2)
        self.assertIn("UNREADABLE orphan.mp4: no original at", out)
        shutil.copyfile(self.old, old_root / "orphan.mp4")
        stripped = self.make("nd.mp4", "-map", "0", "-c", "copy", "-map_metadata", "-1", "-fflags", "+bitexact")
        shutil.copyfile(stripped, new_root / "orphan.mp4")
        status, out, _ = run_main("--old-root", old_root, "--new-root", new_root)
        self.assertEqual(status, 2)
        self.assertIn("CHECK", out)
        self.assertIn("FAILED:  file tag lost: creation_time", out)
        self.assertIn("1 need a check", out)

    def test_detail_flag(self) -> None:
        status, out, _ = run_main(self.old, self.reencoded_audio("cli-b.mp4"), "--detail", "--no-similarity")
        self.assertIn("changed: audio re-encoded", out)


class ArgumentTests(unittest.TestCase):
    def test_bad_arguments_are_refused(self) -> None:
        for args in (["one.mp4"], ["a", "b", "c"], ["--old-root", "x"], ["a", "b", "--old-root", "x", "--new-root", "y"]):
            with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
                cm.main(args)
            self.assertEqual(caught.exception.code, 2, args)

    def test_missing_tools_are_reported(self) -> None:
        with unittest.mock.patch.object(cm.shutil, "which", return_value=None):
            status, _, err = run_main("a.mp4", "b.mp4")
        self.assertEqual(status, 1)
        self.assertIn("needs ffmpeg and ffprobe", err)

    def test_roots_must_exist_and_hold_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status, _, err = run_main("--old-root", tmp, "--new-root", Path(tmp) / "nope")
            self.assertEqual(status, 1)
            (Path(tmp) / "empty").mkdir()
            status, _, err = run_main("--old-root", tmp, "--new-root", Path(tmp) / "empty")
            self.assertEqual(status, 1)
            self.assertIn("no media files", err)


class PureFunctionTests(unittest.TestCase):
    def packets(self, *hashes: str) -> list:
        return [cm.Packet(i * 0.5, 0.5, h) for i, h in enumerate(hashes)]

    def test_removed_ranges_are_found_in_the_middle_start_and_end(self) -> None:
        old = self.packets("a", "b", "c", "d", "e", "f")
        removed, added, same = cm.compare_packets(old, self.packets("a", "b", "e", "f"))
        self.assertEqual([(r.start, r.end, r.packets) for r in removed], [(1.0, 2.0, 2)])
        self.assertEqual((added, same), (0, 4))
        removed, _, _ = cm.compare_packets(old, self.packets("c", "d", "e", "f"))
        self.assertEqual([(r.start, r.end) for r in removed], [(0.0, 1.0)])
        removed, _, _ = cm.compare_packets(old, self.packets("a", "b", "c", "d"))
        self.assertEqual([(r.start, r.end) for r in removed], [(2.0, 3.0)])
        removed, added, same = cm.compare_packets(old, old)
        self.assertEqual((removed, added, same), ([], 0, 6))

    def test_replaced_packets_count_as_added(self) -> None:
        removed, added, same = cm.compare_packets(self.packets("a", "b", "c"), self.packets("a", "x", "c"))
        self.assertEqual((len(removed), added, same), (1, 1, 2))

    def test_several_separate_ranges(self) -> None:
        removed, _, _ = cm.compare_packets(self.packets("a", "b", "c", "d", "e", "f", "g"), self.packets("a", "c", "d", "f", "g"))
        self.assertEqual([(r.start, r.end) for r in removed], [(0.5, 1.0), (2.0, 2.5)])

    def test_harmless_timestamp_warnings_and_their_repeats_are_dropped(self) -> None:
        dts = "[null @ 0x1] Application provided invalid, non monotonically increasing dts to muxer in stream 0: 5 >= 5"
        real = "[h264 @ 0x2] error while decoding MB 3 4"
        self.assertEqual(cm.meaningful([dts, "    Last message repeated 4 times", real, "    Last message repeated 2 times"]),
                         [real, "Last message repeated 2 times"])
        self.assertEqual(cm.meaningful(["", "  "]), [])

    def test_message_kinds(self) -> None:
        self.assertEqual(cm.message_kind("[h264 @ 0x1] x"), "video")
        self.assertEqual(cm.message_kind("[aac @ 0x1] Input buffer exhausted"), "audio")
        self.assertEqual(cm.message_kind("[aist#0:1/aac @ 0x1] Error submitting packet"), "audio")
        self.assertEqual(cm.message_kind("Cannot determine format"), "other")

    def test_tag_changes_ignore_the_brand_fields(self) -> None:
        self.assertEqual(cm.tags_of({"tags": {"major_brand": "mp42", "creation_time": "x"}}), {"creation_time": "x"})
        self.assertEqual(cm.tag_changes({"a": 1, "b": 2}, {"b": 3, "c": 4}), {"lost": ["a"], "added": ["c"], "changed": ["b"]})

    def test_rotation_reads_side_data_or_the_old_tag(self) -> None:
        self.assertEqual(cm.rotation({"side_data_list": [{"rotation": -90}]}), -90)
        self.assertEqual(cm.rotation({"tags": {"rotate": "180"}}), 180)
        self.assertEqual(cm.rotation({}), 0)

    def test_verdicts(self) -> None:
        self.assertEqual(cm.Result("x", 1, 1).verdict, "IDENTICAL")
        self.assertEqual(cm.Result("x", 1, 1, changes=["c"]).verdict, "CHANGED")
        self.assertEqual(cm.Result("x", 1, 1, changes=["c"], failures=["f"]).verdict, "CHECK")


if __name__ == "__main__":
    unittest.main()
