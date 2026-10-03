#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "ass2srt.py"
SPEC = importlib.util.spec_from_file_location("ass2srt", SCRIPT)
assert SPEC and SPEC.loader
ass2srt = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ass2srt
SPEC.loader.exec_module(ass2srt)

HEADER = """[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def script(*events: str) -> str:
    return HEADER + "\n".join("Dialogue: " + event for event in events) + "\n"


class ConvertTests(unittest.TestCase):
    def test_dialogue_keeps_text_and_drops_tags(self) -> None:
        cues = ass2srt.convert(script(r"0,0:00:21.25,0:00:22.86,Default,,0,0,0,,{\be2}Thors!\NWait."))
        self.assertEqual(cues, [(21250, 22860, "Thors!\nWait.")])

    def test_layers_of_one_lyric_become_one_italic_cue(self) -> None:
        cues = ass2srt.convert(script(
            r"1,0:04:13.59,0:04:16.47,OP,,0,0,0,,{\blur0.75}Well tell me what went wrong",
            r"2,0:04:13.59,0:04:16.47,OP,,0,0,0,,{\bord0}Well tell me what went wrong",
            r"0,0:04:13.59,0:04:16.47,OP,,0,0,0,,{\blur4.5}Well tell me what went wrong"))
        self.assertEqual(cues, [(253590, 256470, "<i>Well tell me what went wrong</i>")])

    def test_song_styles(self) -> None:
        for style, italic in (("OP2", True), ("ED Song", True), ("Opening", True), ("Default", False), ("Edited", False)):
            with self.subTest(style):
                body = ass2srt.convert(script(f"0,0:00:01.00,0:00:02.00,{style},,0,0,0,,Line"))[0][2]
                self.assertEqual(body.startswith("<i>"), italic)

    def test_effects_and_drawings_are_dropped(self) -> None:
        cues = ass2srt.convert(script(
            r"0,0:04:28.66,0:04:29.20,mid,lead-in,0,0,0,Effector [fx],{\pos(710,57)}N",
            r"0,0:03:47.99,0:03:53.00,Sign,,0,0,0,,{\p1\c&H232527&}m 377 -5 l -66 -6 l -66 204",
            r"0,0:08:09.63,0:08:13.01,Default,,0,0,0,,{\be2}هل تحسّنت نظرتكم تجاهي؟"))
        self.assertEqual([body for _, _, body in cues], ["هل تحسّنت نظرتكم تجاهي؟"])

    def test_karaoke_syllable_styles_are_dropped(self) -> None:
        syllables = [f"0,0:22:{10 + n // 10:02d}.{n % 10}0,0:22:{11 + n // 10:02d}.00,snk4p2-ed1-rom,,0,0,0,,{s}"
                     for n, s in enumerate(["te", "tsu", "no", "ka", "ze"] * 10)]
        cues = ass2srt.convert(script("0,0:00:03.12,0:00:05.03,Default,,0,0,0,,Hey! What's wrong, Oliver?",
                                      "0,0:00:05.03,0:00:06.19,Default,,0,0,0,,No.", *syllables))
        self.assertEqual([body for _, _, body in cues], ["Hey! What's wrong, Oliver?", "No."])

    def test_frame_by_frame_sign_and_its_fragment_merge(self) -> None:
        cues = ass2srt.convert(script(
            "0,0:00:14.95,0:00:15.07,Sign,,0,0,0,,Wherefore dost thou forget us",
            "0,0:00:15.07,0:00:15.19,Sign,,0,0,0,,Wherefore dost thou forget us",
            "0,0:00:15.19,0:00:20.83,Sign,,0,0,0,,Wherefore dost thou forget us",
            "1,0:00:05.48,0:00:11.28,Title,,0,0,0,,Child of",
            "0,0:00:05.48,0:00:11.28,Title,,0,0,0,,Child of a Hero"))
        self.assertEqual(cues, [(5480, 11280, "Child of a Hero"), (14950, 20830, "Wherefore dost thou forget us")])

    def test_lines_sharing_a_span_join_in_script_order(self) -> None:
        cues = ass2srt.convert(script(
            "0,0:00:14.95,0:00:20.83,Sign,,0,0,0,,Wherefore dost thou forget us for ever,",
            "0,0:00:14.95,0:00:20.83,Sign,,0,0,0,,and forsake us so long time?"))
        self.assertEqual(cues, [(14950, 20830, "Wherefore dost thou forget us for ever,\nand forsake us so long time?")])

    def test_srt_time(self) -> None:
        self.assertEqual(ass2srt.srt_time(3723004), "01:02:03,004")


if __name__ == "__main__":
    unittest.main()
