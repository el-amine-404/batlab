#!/usr/bin/env python3
"""The shapes rebuild's trusted half: checks the sandbox's list and writes a new PDF from it.

Standard library only, and nothing from the list reaches the PDF as syntax: numbers are formatted
here, colours and modes come from fixed tables, pixels are inflated, measured and compressed again,
and a JPEG must be exactly the few markers jpegtran writes. Anything else is ShapeError.
"""

from __future__ import annotations

import base64
import json
import math
import struct
import zlib
from pathlib import Path

MAX_MANIFEST = 512 * 1024 * 1024
MAX_PAGES = 1000
MAX_OPS = 2_000_000
MAX_IMAGE_SIDE = 30_000
MAX_PIXELS = 200_000_000
MAX_COORDINATE = 1e7
MAX_WORDS = 50_000
CAPS = {"butt": 0, "round": 1, "square": 2}
JOINS = {"miter": 0, "round": 1, "bevel": 2}
SEGMENTS = {"m": 2, "l": 2, "c": 6, "h": 0}
FONT = json.loads(Path(__file__).with_name("glyphless-font.json").read_text())


class ShapeError(Exception):
    pass


def number(value, digits: int = 3) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > MAX_COORDINATE:
        raise ShapeError("number out of bounds")
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def numbers(values, count: int, digits: int = 3) -> str:
    if not isinstance(values, list) or len(values) != count:
        raise ShapeError("wrong count of numbers")
    return " ".join(number(value, digits) for value in values)


def segments(path) -> list[tuple[str, list]]:
    if not isinstance(path, list):
        raise ShapeError("path is not a list")
    out, index = [], 0
    while index < len(path):
        command = path[index]
        if not isinstance(command, str) or command not in SEGMENTS:
            raise ShapeError("unknown path command")
        size = SEGMENTS[command]
        out.append((command, path[index + 1:index + 1 + size]))
        if len(out[-1][1]) != size:
            raise ShapeError("short path")
        index += 1 + size
    return out


def path_text(path, digits: int = 3) -> str:
    parts = []
    for command, values in segments(path):
        parts.append(f"{numbers(values, len(values), digits)} {command}" if values else command)
    return " ".join(parts)


def rgb(value, operator: str) -> str:
    if not isinstance(value, list) or len(value) != 3 or not all(isinstance(item, (int, float)) and not isinstance(item, bool) and 0 <= item <= 1 for item in value):
        raise ShapeError("bad colour")
    return f"{number(value[0])} {number(value[1])} {number(value[2])} {operator}"


def unit(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ShapeError("bad opacity")
    return round(value, 3)


def check_jpeg(data: bytes, width: int, height: int, channels: int) -> None:
    """SOI, an optional JFIF, tables, one baseline frame, scans, EOI; the entropy data isn't syntax."""
    if data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        raise ShapeError("not a JPEG")
    index, frames = 2, 0
    while True:
        if index + 4 > len(data) or data[index] != 0xFF:
            raise ShapeError("JPEG marker expected")
        marker = data[index + 1]
        length = struct.unpack(">H", data[index + 2:index + 4])[0]
        if marker == 0xC0:
            frames += 1
            bits, h, w, n = struct.unpack(">BHHB", data[index + 4:index + 10])
            if frames > 1 or bits != 8 or (w, h, n) != (width, height, channels):
                raise ShapeError("JPEG frame differs from its entry")
        elif marker == 0xE0:
            if data[index + 4:index + 9] != b"JFIF\0" or length != 16:
                raise ShapeError("JPEG APP0 is not plain JFIF")
        elif marker == 0xDA:
            if not frames:
                raise ShapeError("JPEG scan before frame")
            # Everything after the scan header is entropy data up to the only EOI, at the end.
            body = data[index + 2 + length:-2]
            if any(body[at + 1] != 0 and not 0xD0 <= body[at + 1] <= 0xD7 for at in _markers(body)):
                raise ShapeError("JPEG has markers after its scan")
            return
        elif marker not in (0xDB, 0xC4):
            raise ShapeError("JPEG marker not allowed")
        index += 2 + length


def _markers(body: bytes):
    at = body.find(b"\xff")
    while at != -1 and at + 1 < len(body):
        yield at
        at = body.find(b"\xff", at + 2)


class Writer:
    def __init__(self) -> None:
        self.objects: list[bytes] = []
        self.pages_ref = self.add(b"")
        self.catalog_ref = self.add(b"")

    def add(self, body: bytes) -> int:
        self.objects.append(body)
        return len(self.objects)

    def stream(self, dictionary: bytes, data: bytes) -> int:
        return self.add(b"<<" + dictionary + b"/Length %d>>\nstream\n" % len(data) + data + b"\nendstream")

    def font(self) -> int:
        file2 = base64.b64decode(FONT["fontFile2"])
        font_file = self.stream(b"/Length1 %d/Filter/FlateDecode" % len(file2), zlib.compress(file2, 9))
        gid_map = self.stream(b"/Filter/FlateDecode", base64.b64decode(FONT["cidToGidMap"]))
        descriptor = self.add(b"<</Type/FontDescriptor/FontName/GlyphLessFont/Flags 5/FontBBox[0 0 500 1000]/ItalicAngle 0"
                              b"/Ascent 1000/Descent -1/CapHeight 1000/StemV 80/FontFile2 %d 0 R>>" % font_file)
        cid = self.add(b"<</Type/Font/Subtype/CIDFontType2/BaseFont/GlyphLessFont/CIDSystemInfo<</Registry(Adobe)/Ordering(Identity)/Supplement 0>>"
                       b"/FontDescriptor %d 0 R/CIDToGIDMap %d 0 R/DW 500>>" % (descriptor, gid_map))
        unicode_map = self.stream(b"/Filter/FlateDecode", zlib.compress(FONT["toUnicode"].encode(), 9))
        return self.add(b"<</Type/Font/Subtype/Type0/BaseFont/GlyphLessFont/Encoding/Identity-H/DescendantFonts[%d 0 R]/ToUnicode %d 0 R>>" % (cid, unicode_map))

    def image(self, entry: dict, data: bytes, mask_ref: int | None) -> int:
        kind, width, height, channels, bits = (entry.get(key) for key in ("kind", "w", "h", "channels", "bits"))
        if (not isinstance(width, int) or not isinstance(height, int) or not 0 < width <= MAX_IMAGE_SIDE or not 0 < height <= MAX_IMAGE_SIDE
                or width * height > MAX_PIXELS or channels not in (1, 3) or bits not in (1, 8) or (bits == 1 and channels != 1)):
            raise ShapeError("image out of bounds")
        space = b"DeviceRGB" if channels == 3 else b"DeviceGray"
        head = b"/Type/XObject/Subtype/Image/Width %d/Height %d/BitsPerComponent %d/ColorSpace/%s" % (width, height, bits, space)
        if mask_ref is not None:
            head += b"/SMask %d 0 R" % mask_ref
        if kind == "jpeg":
            if bits != 8:
                raise ShapeError("JPEG bits")
            check_jpeg(data, width, height, channels)
            return self.stream(head + b"/Filter/DCTDecode", data)
        if kind != "pixels":
            raise ShapeError("unknown image kind")
        row = width * channels if bits == 8 else (width + 7) // 8
        # bits == 8 rows carry a PNG filter byte.
        expected = (row + 1) * height if bits == 8 else row * height
        inflater = zlib.decompressobj()
        try:
            raw = inflater.decompress(data, expected + 1)
        except zlib.error as error:
            raise ShapeError("image data") from error
        if len(raw) != expected or inflater.unconsumed_tail or not inflater.eof:
            raise ShapeError("image size differs from its entry")
        if bits == 8:
            if any(raw[at] > 4 for at in range(0, expected, row + 1)):
                raise ShapeError("bad row filter")
            head += b"/DecodeParms<</Predictor 15/Colors %d/Columns %d>>" % (channels, width)
        return self.stream(head + b"/Filter/FlateDecode", zlib.compress(raw, 9))


def text_layer(words, height: float) -> str:
    if not isinstance(words, list) or len(words) > MAX_WORDS:
        raise ShapeError("too many words")
    parts = ["BT", "3 Tr"]
    for word in words:
        if not isinstance(word, list) or len(word) != 5 or not isinstance(word[4], str):
            raise ShapeError("bad word")
        x0, y0, x1, y1 = word[:4]
        number(x0), number(y0), number(x1), number(y1)
        codes = [ord(char) for char in word[4][:200] if ord(char) < 0xFFFF]
        size = y1 - y0
        if not codes or size <= 0 or x1 <= x0:
            continue
        stretch = max(1.0, min(1000.0, 100 * (x1 - x0) / (0.5 * size * len(codes))))
        parts.append(f"/F0 {number(size)} Tf {number(stretch, 1)} Tz 1 0 0 1 {number(x0)} {number(height - y1 + size * 0.2)} Tm <{''.join(f'{code:04x}' for code in codes)}> Tj")
    parts.append("ET")
    return "\n".join(parts) if len(parts) > 3 else ""


def build(frames_path: Path, output: Path, max_output: int) -> dict:
    """Read the sandbox's frames; returns what the page holds for the record."""
    with frames_path.open("rb") as source:
        def frame(limit: int) -> bytes:
            head = source.read(4)
            if len(head) != 4:
                raise ShapeError("short frame")
            size = struct.unpack(">I", head)[0]
            if size > limit:
                raise ShapeError("frame too large")
            data = source.read(size)
            if len(data) != size:
                raise ShapeError("short frame")
            return data

        try:
            manifest = json.loads(frame(MAX_MANIFEST), parse_constant=lambda _name: (_ for _ in ()).throw(ShapeError("NaN")))
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise ShapeError("manifest") from error
        if not isinstance(manifest, dict) or manifest.get("format") != "shapes-1":
            raise ShapeError("manifest format")
        pages, glyphs, entries = manifest.get("pages"), manifest.get("glyphs"), manifest.get("images")
        if not isinstance(pages, list) or not 0 < len(pages) <= MAX_PAGES or not isinstance(glyphs, list) or not isinstance(entries, list):
            raise ShapeError("manifest shape")
        writer = Writer()
        font_ref = None
        image_refs: list[int] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ShapeError("image entry")
            mask = entry.get("mask")
            if mask is not None and (not isinstance(mask, int) or isinstance(mask, bool) or not 0 <= mask < index or entries[mask].get("bits") != 8 or entries[mask].get("channels") != 1):
                raise ShapeError("image mask")
            image_refs.append(writer.image(entry, frame(512 * 1024 * 1024), None if mask is None else image_refs[mask]))
        if source.read(1):
            raise ShapeError("data after the last image")
    glyph_text = [path_text(glyph, 5) for glyph in glyphs]
    kids = []
    summary = {"pages": len(pages), "pixelPages": 0, "images": len(entries)}
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("ops"), list) or len(page["ops"]) > MAX_OPS:
            raise ShapeError("page")
        width, height = page.get("w"), page.get("h")
        for value in (width, height):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 14400:
                raise ShapeError("page size")
        if page.get("mode") == "pixels":
            summary["pixelPages"] += 1
        lines = [f"q 1 0 0 -1 0 {number(height)} cm"]
        states: dict[tuple[float, float], str] = {}
        used: dict[int, str] = {}
        depth = 0
        stack: list = []
        fill_now = stroke_now = None
        alpha_now = (1.0, 1.0)
        for op in page["ops"]:
            kind = op[0] if isinstance(op, list) and op else None
            if kind == "q" and len(op) == 1:
                depth += 1
                lines.append("q")
                stack.append((fill_now, stroke_now, alpha_now))
            elif kind == "Q" and len(op) == 1:
                depth -= 1
                if depth < 0:
                    raise ShapeError("unbalanced Q")
                lines.append("Q")
                fill_now, stroke_now, alpha_now = stack.pop()
            elif kind == "clip" and len(op) == 4:
                lines.append(f"{clip_path(op[1], op[2])} {'W*' if op[3] is True else 'W'} n")
            elif kind == "draw" and len(op) == 9:
                _kind, transform, shape, fill, stroke, even_odd, fill_alpha, stroke_alpha, line = op
                if fill is None and stroke is None:
                    raise ShapeError("nothing to draw")
                body = []
                if fill is not None and fill != fill_now:
                    body.append(rgb(fill, "rg"))
                    fill_now = fill
                if stroke is not None and stroke != stroke_now:
                    body.append(rgb(stroke, "RG"))
                    stroke_now = stroke
                alpha = (unit(fill_alpha), unit(stroke_alpha))
                if alpha != alpha_now:
                    body.append(f"/{states.setdefault(alpha, f'G{len(states)}')} gs")
                    alpha_now = alpha
                if isinstance(shape, int) and not isinstance(shape, bool):
                    if not 0 <= shape < len(glyph_text):
                        raise ShapeError("glyph index")
                    path = glyph_text[shape]
                else:
                    path = path_text(shape)
                style = ""
                if stroke is not None:
                    if not isinstance(line, dict) or line.get("cap") not in CAPS or line.get("join") not in JOINS:
                        raise ShapeError("line style")
                    style = f"{number(line.get('w'))} w {CAPS[line['cap']]} J {JOINS[line['join']]} j {number(max(1, line.get('miter', 10)))} M "
                    if line.get("dash"):
                        style += f"[{numbers(line['dash'], len(line['dash']))}] {number(line.get('phase', 0))} d "
                eo = even_odd is True
                paint = ("B*" if eo else "B") if fill is not None and stroke is not None else (("f*" if eo else "f") if fill is not None else "S")
                body.append(f"q {numbers(transform, 6, 5)} cm {style}{path} {paint} Q")
                lines.append(" ".join(body))
            elif kind == "image" and len(op) == 3:
                index = op[1]
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(image_refs):
                    raise ShapeError("image index")
                name = used.setdefault(index, f"I{len(used)}")
                lines.append(f"q {numbers(op[2], 6)} cm /{name} Do Q")
            else:
                raise ShapeError("unknown operation")
        if depth != 0:
            raise ShapeError("unbalanced q")
        lines.append("Q")
        layer = text_layer(page.get("words", []), height)
        if layer:
            lines.append(layer)
            if font_ref is None:
                font_ref = writer.font()
        content = writer.stream(b"/Filter/FlateDecode", zlib.compress("\n".join(lines).encode("ascii"), 9))
        resources = b""
        if used:
            resources += b"/XObject<<" + b"".join(b"/%s %d 0 R" % (name.encode(), image_refs[index]) for index, name in used.items()) + b">>"
        if states:
            resources += b"/ExtGState<<" + b"".join(b"/%s<</ca %s/CA %s>>" % (name.encode(), number(key[0]).encode(), number(key[1]).encode()) for key, name in states.items()) + b">>"
        if layer:
            resources += b"/Font<</F0 %d 0 R>>" % font_ref
        kids.append(writer.add(b"<</Type/Page/Parent %d 0 R/MediaBox[0 0 %s %s]/Contents %d 0 R/Resources<<%s>>>>"
                               % (writer.pages_ref, number(width).encode(), number(height).encode(), content, resources)))
    writer.objects[writer.pages_ref - 1] = b"<</Type/Pages/Count %d/Kids[%s]>>" % (len(kids), " ".join(f"{kid} 0 R" for kid in kids).encode())
    writer.objects[writer.catalog_ref - 1] = b"<</Type/Catalog/Pages %d 0 R>>" % writer.pages_ref
    total = 0
    offsets = []
    with output.open("xb") as target:
        def put(data: bytes) -> None:
            nonlocal total
            total += len(data)
            if total > max_output:
                raise ShapeError("output too large")
            target.write(data)

        put(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        for number_, body in enumerate(writer.objects, 1):
            offsets.append(total)
            put(b"%d 0 obj\n" % number_ + body + b"\nendobj\n")
        start = total
        put(b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1) + b"".join(b"%010d 00000 n \n" % offset for offset in offsets))
        put(b"trailer\n<</Size %d/Root %d 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(offsets) + 1, writer.catalog_ref, start))
    return summary


def clip_path(transform, path) -> str:
    """A clip outlives a cm's q/Q, so its points are moved here."""
    if not isinstance(transform, list) or len(transform) != 6:
        raise ShapeError("clip transform")
    numbers(transform, 6, 5)
    a, b, c, d, e, f = transform
    moved = []
    for command, values in segments(path):
        moved.append(command)
        for at in range(0, len(values), 2):
            x, y = values[at], values[at + 1]
            number(x), number(y)
            moved += [a * x + c * y + e, b * x + d * y + f]
    return path_text(moved)


if __name__ == "__main__":
    import sys
    try:
        print(json.dumps(build(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]))))
    except ShapeError as error:
        print(json.dumps({"refused": str(error)[:120]}))
        raise SystemExit(4)
