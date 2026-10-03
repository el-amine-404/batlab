#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
