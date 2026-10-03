#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import struct
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "pgsocr.py"
SPEC = importlib.util.spec_from_file_location("pgsocr", SCRIPT)
assert SPEC and SPEC.loader
ocr = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ocr
SPEC.loader.exec_module(ocr)


def segment(pts: int, kind: int, payload: bytes) -> bytes:
    return b"PG" + struct.pack(">IIBH", pts, 0, kind, len(payload)) + payload


def pcs(objects: list[tuple[int, int, int]], palette_only: bool = False, palette_id: int = 0) -> bytes:
    body = struct.pack(">HHBHBBBB", 1920, 1080, 0x10, 1, 0x80, 0x80 if palette_only else 0, palette_id, len(objects))
    return body + b"".join(struct.pack(">HBBHH", o, 0, 0, x, y) for o, x, y in objects)


def pds(entries: dict[int, tuple[int, int]], palette_id: int = 0) -> bytes:
    return bytes([palette_id, 0]) + b"".join(bytes([i, y, 128, 128, a]) for i, (y, a) in entries.items())


def rle(rows: list[list[int]]) -> bytes:
    out = bytearray()
    for row in rows:
        i = 0
        while i < len(row):
            colour, run = row[i], 1
            while i + run < len(row) and row[i + run] == colour and run < 16383:
                run += 1
            if colour and run < 3:
                out += bytes([colour]) * run
            else:
                flag = (0x80 if colour else 0) | (0x40 if run > 63 else 0)
                out += bytes([0, flag | (run >> 8 if run > 63 else run)]) + (bytes([run & 0xFF]) if run > 63 else b"")
                if colour:
                    out.append(colour)
            i += run
        out += b"\0\0"
    return bytes(out)


def ods(object_id: int, rows: list[list[int]]) -> bytes:
    data = rle(rows)
    return struct.pack(">HBB", object_id, 0, 0xC0) + (len(data) + 4).to_bytes(3, "big") + \
        struct.pack(">HH", len(rows[0]), len(rows)) + data


ROWS = [[0] * 5 + [1] * 70 + [0] * 5, [2] * 80, [0] * 3 + [1] * 77]


class ParseTests(unittest.TestCase):
    def test_rle_round_trip(self) -> None:
        width = len(ROWS[0])
        pixels = ocr.decode_rle(rle(ROWS), width, len(ROWS))
        self.assertEqual(pixels, bytes(sum(ROWS, [])))

    def test_a_shown_then_cleared_subtitle_is_one_event(self) -> None:
        stream = (segment(90000, 0x16, pcs([(0, 100, 900)])) + segment(90000, 0x14, pds({1: (235, 255), 2: (16, 255)}))
                  + segment(90000, 0x15, ods(0, ROWS)) + segment(90000, 0x80, b"")
                  + segment(270000, 0x16, pcs([])) + segment(270000, 0x80, b""))
        events = ocr.parse(stream)
        self.assertEqual([(e.start, e.end) for e in events], [(90000, 270000)])
        x, y, bitmap, palette = events[0].images[0]
        self.assertEqual((x, y, bitmap.width, bitmap.height, palette[1]), (100, 900, 80, 3, (235, 255)))

    def test_a_fade_in_keeps_the_opaque_palette(self) -> None:
        stream = (segment(0, 0x16, pcs([(0, 0, 0)])) + segment(0, 0x14, pds({1: (235, 20)}))
                  + segment(0, 0x15, ods(0, ROWS)) + segment(0, 0x80, b"")
                  + segment(9000, 0x16, pcs([(0, 0, 0)], palette_only=True)) + segment(9000, 0x14, pds({1: (235, 255)}))
                  + segment(9000, 0x80, b"")
                  + segment(90000, 0x16, pcs([])) + segment(90000, 0x80, b""))
        events = ocr.parse(stream)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].images[0][3][1], (235, 255))

    def test_not_pgs_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            list(ocr.segments(b"\x1aE\xdf\xa3 matroska header, not PGS"))


class ImageTests(unittest.TestCase):
    def test_text_comes_out_dark_on_white_and_cropped(self) -> None:
        bitmap = ocr.Bitmap(80, 3, bytes(sum(ROWS, [])))
        image = ocr.to_pgm(bitmap, {1: (235, 255), 2: (16, 255)})
        header, pixels = image.split(b"\n", 1)
        width, height = (int(v) for v in header.split()[1:3])
        self.assertEqual((width, height), (77 * 2 + 40, 3 * 2 + 40))   # cropped to the ink, doubled, padded
        self.assertEqual(pixels[0], 255)                         # padding is white
        self.assertLess(min(pixels), 30)                         # the white text became dark

    def test_a_transparent_object_gives_no_image(self) -> None:
        self.assertEqual(ocr.to_pgm(ocr.Bitmap(4, 1, b"\1\1\1\1"), {1: (235, 0)}), b"")


class CleanTests(unittest.TestCase):
    def test_english_fixes(self) -> None:
        cases = {"l'm here, l think.": "I'm here, I think.", "lt was | who said it": "It was I who said it",
                 "Wait ... what ?": "Wait... what?", "-Hey!": "- Hey!", "He said ‘no’ to “them”": "He said 'no' to \"them\"",
                 "Al l0ve": "Al love", "Lillian will fly": "Lillian will fly"}
        for raw, expected in cases.items():
            with self.subTest(raw):
                self.assertEqual(ocr.clean_line(raw, "eng"), expected)

    def test_french_keeps_its_spaces(self) -> None:
        self.assertEqual(ocr.clean_line("Quoi ? Il est là , non !", "fra"), "Quoi ? Il est là, non !")

    def test_srt_time(self) -> None:
        self.assertEqual(ocr.srt_time(90 * 3723004), "01:02:03,004")


if __name__ == "__main__":
    unittest.main()
