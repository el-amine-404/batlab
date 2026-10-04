#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch, Mock


SCRIPT = Path(__file__).resolve().parents[1] / "subextract.py"
SPEC = importlib.util.spec_from_file_location("subextract", SCRIPT)
assert SPEC and SPEC.loader
sx = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sx
SPEC.loader.exec_module(sx)


def subtitle(index: int, codec: str, lang: str, title: str = "", default: bool = False, forced: bool = False) -> dict:
    return {"index": index, "codec_type": "subtitle", "codec_name": codec, "tags": {"language": lang, "title": title},
            "disposition": {"default": int(default), "forced": int(forced)}}


def track(title: str, default: bool, lines: int, covered: float, overlap: float) -> "sx.Track":
    t = sx.Track(0, "ass", "en", title, default)
    t.lines, t.covered, t.overlap = lines, covered, overlap
    return t


class TrackTests(unittest.TestCase):
    def test_full_dialogue_with_signs_is_not_excluded(self):
        info = {"streams": [subtitle(2, "ass", "eng", "Full Dialogue + Signs/Songs"),
                            subtitle(3, "ass", "eng", "Full Commentary")]}
        self.assertEqual([t.index for t in sx.text_tracks(info)], [2])

    def test_signs_forced_and_image_tracks_are_not_candidates(self) -> None:
        info = {"streams": [
            subtitle(4, "ass", "eng", "[MTBB] English ASS", default=True),
            subtitle(5, "ass", "enm", "[MTBB] English (Honorifics) ASS"),
            subtitle(8, "ass", "eng", "[MTBB] English (Signs/Songs Only) ASS"),
            subtitle(9, "subrip", "fre", "Forced", forced=True),
            subtitle(10, "hdmv_pgs_subtitle", "eng", "English BD PGS Sub"),
            subtitle(11, "subrip", "und"),
            subtitle(12, "subrip", "ara", "Arabic"),
        ]}
        self.assertEqual([(t.index, t.language) for t in sx.text_tracks(info)], [(4, "en"), (5, "en"), (12, "ar")])

    def test_attack_on_titan_s04e24_picks_mtbb(self) -> None:
        # Measured 2026-10-03: BlurayDesuYo and Crunchyroll carry motion-tracked
        # signs that become overlapping lines, more on screen than the episode.
        tracks = [track("[MTBB] English ASS", True, 294, 14.3 * 60, 0.05),
                  track("[MTBB] English (Honorifics) ASS", False, 294, 14.3 * 60, 0.05),
                  track("[BlurayDesuYo] English ASS", False, 1810, 15.8 * 60, 0.70),
                  track("[Crunchyroll modified] English ASS", False, 1759, 16.0 * 60, 0.65)]
        self.assertEqual(sx.choose(tracks).title, "[MTBB] English ASS")

    def test_without_a_default_the_least_cluttered_wins(self) -> None:
        tracks = [track("A", False, 1800, 900, 0.7), track("B", False, 300, 880, 0.05)]
        self.assertEqual(sx.choose(tracks).title, "B")

    def test_an_incomplete_default_loses(self) -> None:
        tracks = [track("Partial", True, 40, 200, 0.0), track("Full", False, 300, 880, 0.05)]
        self.assertEqual(sx.choose(tracks).title, "Full")

    def test_honorifics_only_when_alone(self) -> None:
        self.assertEqual(sx.choose([track("English (Honorifics)", False, 300, 880, 0.0)]).title, "English (Honorifics)")
        self.assertIsNone(sx.choose([track("Empty", True, 0, 0, 0)]))

    def test_measure_counts_overlap_and_coverage(self) -> None:
        t = sx.Track(0, "subrip", "en", "", False)
        t.srt = ("1\n00:00:01,000 --> 00:00:03,000\na\n\n2\n00:00:02,000 --> 00:00:04,000\nb\n\n"
                 "3\n00:00:10,000 --> 00:00:11,000\nc\n\n")
        sx.measure(t)
        self.assertEqual((t.lines, t.covered, round(t.overlap, 2)), (3, 4.0, 0.67))


class OcrTests(unittest.TestCase):
    def test_quality_gate(self) -> None:
        # The English and Italian Blu-ray tracks of Attack on Titan S04E24, 2026-10-03.
        self.assertEqual(sx.ocr_verdict({"cues": 220, "mean_confidence": 95.7, "low_confidence_share": 0.001}), "")
        self.assertEqual(sx.ocr_verdict({"cues": 344, "mean_confidence": 95.2, "low_confidence_share": 0.005}), "")
        self.assertIn("mean confidence", sx.ocr_verdict({"cues": 300, "mean_confidence": 71, "low_confidence_share": 0.02}))
        self.assertIn("under 60%", sx.ocr_verdict({"cues": 300, "mean_confidence": 90, "low_confidence_share": 0.12}))
        self.assertIn("only 4 lines", sx.ocr_verdict({"cues": 4, "mean_confidence": 99, "low_confidence_share": 0}))

    def test_image_tracks_and_choice(self) -> None:
        def pgs(index: int, lang: str, title: str = "", default: bool = False, forced: bool = False, frames: int = 0) -> dict:
            stream = subtitle(index, "hdmv_pgs_subtitle", lang, title, default, forced)
            stream["tags"]["NUMBER_OF_FRAMES"] = str(frames)
            return stream
        info = {"streams": [pgs(10, "eng", "English BD PGS Sub", frames=440), pgs(11, "ita", "Italian BD PGS Sub", frames=688),
                            pgs(12, "ita", "Italian Forced", forced=True, frames=12), pgs(13, "ita", "Signs", frames=30),
                            pgs(14, "ita", "Italian SDH", frames=900)]}
        tracks = sx.image_tracks(info)
        self.assertEqual([t.index for t in tracks], [10, 11, 14])
        self.assertEqual(sx.choose_image([t for t in tracks if t.language == "it"]).index, 14)

    def test_clean_srt(self) -> None:
        raw = ("2\r\n00:00:05,000 --> 00:00:05,100\r\nShort\r\n\r\n"
               "1\n00:00:01,000 --> 00:00:02,000\nFirst  \n\n"
               "3\n00:00:01,000 --> 00:00:02,000\nFirst\n\n"
               "4\n00:00:09,000 --> 00:00:10,000\n\n")
        self.assertEqual(sx.clean_srt(raw), "1\n00:00:01,000 --> 00:00:02,000\nFirst\n\n"
                                            "2\n00:00:05,000 --> 00:00:05,300\nShort\n")


class SidecarTests(unittest.TestCase):
    def test_sidecars_by_language(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "Show - S01E01 - Title [x].mkv"
            for name in ("Show - S01E01 - Title [x].mkv", "Show - S01E01 - Title [x].en.srt",
                         "Show - S01E01 - Title [x].ar.hi.srt", "Show - S01E01 - Title [x].fr.forced.srt",
                         "Show - S01E01 - Title [x].eng.ass", "Show - S01E01 - Title [x].nfo",
                         "Show - S01E02 - Other.en.srt"):
                (root / name).write_text("x")
            found = {lang: sorted(p.name[len(video.stem):] for p in paths)
                     for lang, paths in sx.sidecars_by_language(video).items()}
            self.assertEqual(found, {"en": [".en.srt", ".eng.ass"], "ar": [".ar.hi.srt"]})


GOOD_SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello.\n\n2\n00:00:31,000 --> 00:00:32,000\nGoodbye.\n"


class AudioWindowTests(unittest.TestCase):
    def test_window_clips_and_rebases_cues(self):
        text = sx.srt_window(GOOD_SRT, 1500, 31500)
        self.assertEqual(text, "1\n00:00:00,000 --> 00:00:00,500\nHello.\n\n"
                               "2\n00:00:29,500 --> 00:00:30,000\nGoodbye.\n")
        self.assertEqual(sx.srt_window(GOOD_SRT, 40000, 60000), "")

    def test_audio_and_subtitle_halves_have_matching_origins(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.wav"
            first_audio, second_audio = b"\x01\x00" * 200, b"\x02\x00" * 200
            with wave.open(str(reference), "wb") as wav:
                wav.setparams((1, 2, 100, 0, "NONE", "not compressed"))
                wav.writeframes(first_audio + second_audio)
            subtitle = root / "full.srt"
            subtitle.write_text("\n".join(
                f"{n + 1}\n{sx.keeper_time(n * 100)} --> {sx.keeper_time(n * 100 + 80)}\nLine {n}\n"
                for n in range(40)))
            checker = sx.AudioChecker(root / "video.mkv", {"format": {"duration": "4"}}, root, root, "bazarr")
            checker.reference = reference
            seen = []
            def align(path):
                with wave.open(str(checker.segment_reference), "rb") as wav:
                    seen.append((path.read_text(), wav.readframes(wav.getnframes())))
                return .2
            with patch.object(checker, "ffsubsync", side_effect=align):
                self.assertEqual(checker.offset(subtitle), (200, ""))
            self.assertEqual([audio for _, audio in seen], [first_audio, second_audio])
            self.assertIn("00:00:00,000 --> 00:00:00,080\nLine 0", seen[0][0])
            self.assertIn("00:00:00,000 --> 00:00:00,080\nLine 20", seen[1][0])
            self.assertNotIn("Line 0\n", seen[1][0])
            self.assertIsNone(checker.segment_reference)

    def test_sparse_subtitles_do_not_decode_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subtitle = root / "sparse.srt"
            subtitle.write_text(GOOD_SRT)
            checker = sx.AudioChecker(root / "video.mkv", {"format": {"duration": "60"}}, root, root, "bazarr")
            with patch.object(checker, "reference_audio") as reference:
                shift, reason = checker.offset(subtitle)
            self.assertIsNone(shift)
            self.assertIn("too few", reason)
            reference.assert_not_called()


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "test.mkv"
        self.video.write_bytes(b"video")
        self.sidecar = self.root / "test.en.srt"
        self.info = {"streams": [subtitle(2, "subrip", "eng")], "format": {"duration": "60"}}
        self.probe = patch.object(sx.subprocess, "run", return_value=Mock(stdout=json.dumps(self.info)))
        self.probe.start()
        self.addCleanup(self.probe.stop)

        def extracted(video, tracks, workdir):
            for t in tracks:
                t.srt = GOOD_SRT
                sx.measure(t)
        self.extract = patch.object(sx, "extract", side_effect=extracted)
        self.extract.start()
        self.addCleanup(self.extract.stop)

    def process(self, apply=True, ocr=None):
        return sx.process(self.video, self.root, "bazarr", apply, {"en"}, ocr)

    def test_new_subtitles_must_pass_audio_check(self):
        with patch.object(sx, "Fit", return_value=lambda p: (False, "halves disagree")):
            result = self.process()
        self.assertFalse(self.sidecar.exists())
        self.assertFalse(result.written)
        self.assertIn("halves disagree", result.refused[0])

    def test_existing_srt_without_embedded_track_is_checked(self):
        sx.subprocess.run.return_value.stdout = json.dumps({"streams": [], "format": {"duration": "60"}})
        self.sidecar.write_text(GOOD_SRT)
        checker = Mock(return_value=(True, "timing OK"))
        with patch.object(sx, "Fit", return_value=checker):
            result = self.process()
        checker.assert_called_once_with(self.sidecar)
        self.assertTrue(result.kept)
        self.assertFalse(result.written)

    def test_bad_existing_srt_without_replacement_is_preserved(self):
        sx.subprocess.run.return_value.stdout = json.dumps({"streams": [], "format": {"duration": "60"}})
        self.sidecar.write_text(GOOD_SRT)
        with patch.object(sx, "Fit", return_value=lambda p: (False, "30 seconds off")):
            result = self.process()
        self.assertIn("nothing could replace it", result.refused[0])
        self.assertEqual(self.sidecar.read_text(), GOOD_SRT)

    def test_untagged_srt_is_also_checked(self):
        sx.subprocess.run.return_value.stdout = json.dumps({"streams": [], "format": {"duration": "60"}})
        untagged = self.root / "test.srt"
        untagged.write_text(GOOD_SRT)
        checker = Mock(return_value=(True, "timing OK"))
        with patch.object(sx, "Fit", return_value=checker):
            result = self.process()
        checker.assert_called_once_with(untagged)
        self.assertTrue(result.kept)

    def test_bad_variant_is_reported_even_when_another_srt_fits(self):
        self.sidecar.write_text(GOOD_SRT)
        variant = self.root / "test.en.hi.srt"
        variant.write_text("wrong")
        with patch.object(sx, "Fit", return_value=lambda p: (p == self.sidecar, "timing")):
            result = self.process()
        self.assertTrue(result.kept)
        self.assertIn("another SRT", result.refused[0])
        self.assertEqual(variant.read_text(), "wrong")

    def test_write_failure_preserves_existing_subtitle(self):
        self.sidecar.write_text("original")
        with patch.object(sx, "Fit", return_value=lambda p: (p != self.sidecar, "timing")), \
                patch.object(sx, "atomic_text", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.process()
        self.assertEqual(self.sidecar.read_text(), "original")

    def test_replacement_has_backup_and_valid_srt(self):
        self.sidecar.write_text("original")
        with patch.object(sx, "Fit", return_value=lambda p: (p != self.sidecar, "timing")):
            result = self.process()
        self.assertEqual(self.sidecar.read_text(), sx.clean_srt(GOOD_SRT))
        self.assertEqual(len(result.replaced), 1)
        backups = list((self.root / "recycle/subextract").rglob("*.srt"))
        self.assertEqual([p.read_text() for p in backups], ["original"])

    def test_atomic_replace_failure_does_not_truncate(self):
        self.sidecar.write_text("original")
        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                sx.atomic_text(self.sidecar, "replacement")
        self.assertEqual(self.sidecar.read_text(), "original")
        self.assertFalse(list(self.root.glob(".subextract-*.tmp")))

    def test_external_sidecar_change_is_preserved(self):
        self.sidecar.write_text("original")
        def check(path):
            if path != self.sidecar:
                self.sidecar.write_text("Bazarr updated this")
                return True, "OK"
            return False, "bad timing"
        with patch.object(sx, "Fit", return_value=check):
            with self.assertRaisesRegex(RuntimeError, "sidecars changed"):
                self.process()
        self.assertEqual(self.sidecar.read_text(), "Bazarr updated this")

    def test_dry_run_never_replaces(self):
        self.sidecar.write_text("original")
        with patch.object(sx, "Fit", return_value=lambda p: (p != self.sidecar, "timing")):
            result = self.process(apply=False)
        self.assertTrue(result.written)
        self.assertEqual(self.sidecar.read_text(), "original")
        self.assertFalse((self.root / "recycle/subextract").exists())

    def test_unusable_text_falls_back_to_pgs(self):
        self.info["streams"].append(subtitle(3, "hdmv_pgs_subtitle", "eng"))
        sx.subprocess.run.return_value.stdout = json.dumps(self.info)
        worker = Mock()
        worker.run.return_value = (GOOD_SRT, {"cues": 40, "mean_confidence": 96, "low_confidence_share": 0})
        with patch.object(sx, "extract"), patch.object(sx, "Fit", return_value=lambda p: (True, "OK")):
            result = self.process(ocr=worker)
        self.assertTrue(result.written)
        worker.run.assert_called_once()

    def test_fingerprint_changes_with_settings_and_sidecars(self):
        initial = sx.fingerprint(self.video, {"languages": ["en"]})
        self.assertNotEqual(initial, sx.fingerprint(self.video, {"languages": ["en", "fr"]}))
        self.sidecar.write_text(GOOD_SRT)
        self.assertNotEqual(initial, sx.fingerprint(self.video, {"languages": ["en"]}))

    def test_invalid_subtitles_are_rejected(self):
        for text in ("", "1\n00:00:01,000 --> 00:00:02,000\n<i></i>\n", GOOD_SRT.replace("32,000", "62,000"),
                     GOOD_SRT.replace("Hello.", "bad\ufffdtext"), GOOD_SRT.replace("02,000", "00,000")):
            with self.subTest(text=text):
                self.assertTrue(sx.srt_problem(text, 60))
        self.assertEqual(sx.srt_problem(GOOD_SRT, 60), "")

    def test_ffsubsync_zero_exit_with_failed_alignment_is_rejected(self):
        checker = sx.AudioChecker(self.video, self.info, self.root, self.root, "bazarr")
        checker.reference = self.root / "reference.wav"
        sx.subprocess.run.return_value = Mock(returncode=0, stdout='SUBEXTRACT_RESULT=' + json.dumps({
            "sync_was_successful": False, "offset_seconds": 0, "framerate_scale_factor": 1}), stderr="")
        with self.assertRaisesRegex(ValueError, "unsuccessful"):
            checker.ffsubsync(self.sidecar)
        command = sx.subprocess.run.call_args.args[0]
        self.assertIn("--skip-infer-framerate-ratio", command)
        self.assertIn("--no-fix-framerate", command)

    def test_ocr_missing_images_cannot_pass_on_confidence_alone(self):
        report = {"cues": 300, "mean_confidence": 99, "low_confidence_share": 0, "unreadable_share": .2}
        self.assertIn("unreadable", sx.ocr_verdict(report))

    def test_checkpoint_survives_interruption_on_next_video(self):
        second = self.root / "second.mkv"
        second.write_bytes(b"second video")
        args = ["--all", "--no-ocr", "--quiet", "--data-root", str(self.root), "--state-dir", str(self.root / "state")]
        with patch.object(sx, "library_videos", return_value=[self.video, second]), \
                patch.object(sx, "process", side_effect=[sx.Outcome([], [], [], []), KeyboardInterrupt]), \
                patch("builtins.print"):
            with self.assertRaises(KeyboardInterrupt):
                sx.main(args)
        handled = json.loads((self.root / "state/handled.json").read_text())
        self.assertIn(str(self.video), handled)
        self.assertNotIn(str(second), handled)

    def test_refusals_are_retried_and_report_failure(self):
        args = ["--all", "--no-ocr", "--quiet", "--data-root", str(self.root), "--state-dir", str(self.root / "state")]
        with patch.object(sx, "library_videos", return_value=[self.video]), \
                patch.object(sx, "process", return_value=sx.Outcome([], [], [], [], ["bad timing"])) as process, \
                patch.object(sx, "tell_jellyfin"), patch("builtins.print"):
            self.assertEqual(sx.main(args), 1)
            self.assertEqual(sx.main(args), 1)
            self.assertEqual(process.call_count, 2)

    def test_simultaneous_runs_use_the_same_lock(self):
        lockroot = self.root / "recycle/.subextract"
        lockroot.mkdir(parents=True)
        with (lockroot / "run.lock").open("a") as lock, patch.object(sx, "run_batch") as batch, patch("builtins.print"):
            sx.fcntl.flock(lock, sx.fcntl.LOCK_EX | sx.fcntl.LOCK_NB)
            self.assertEqual(sx.main(["--data-root", str(self.root)]), 0)
            batch.assert_not_called()

    def test_library_batch_waits_for_a_running_timer(self):
        with patch.object(sx.fcntl, "flock", side_effect=[BlockingIOError, None]) as flock, \
                patch.object(sx, "run_batch", return_value=0) as batch, patch("builtins.print"):
            self.assertEqual(sx.main(["--all", "--data-root", str(self.root)]), 0)
        self.assertEqual(flock.call_count, 2)
        self.assertEqual(flock.call_args.args[1], sx.fcntl.LOCK_EX)
        batch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
