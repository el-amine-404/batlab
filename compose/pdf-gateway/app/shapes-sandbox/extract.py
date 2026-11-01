#!/usr/bin/env python3
"""Inside the sandbox: a hostile PDF on stdin, a plain list of shapes and pixels on stdout.

Assume this process can be compromised. Everything it prints is checked by the writer on the
host (shape_writer.py), which builds the new PDF itself; no PDF syntax crosses. Anything this
code doesn't understand on a page turns that page into pixels: never guess, never pass through.

Output: frames of a 4-byte big-endian length and data. First the manifest (JSON), then one
data frame per entry of manifest["images"].
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zlib

import pymupdf
from PIL import Image, ImageChops

Image.MAX_IMAGE_PIXELS = 200_000_000
SVG = "{http://www.w3.org/2000/svg}"
HREF = "{http://www.w3.org/1999/xlink}href"
TOKEN = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?|[A-Za-z]")
FALLBACK_DPI = 200
OCR_DPI = 200
MAX_PAGE_POINTS = 2_000_000
MAX_OCR_PAGES = 200
# Read but not drawn; "data-text" is MuPDF's note of the characters behind a glyph.
ALLOWED = {"data-text", "transform", "d", "fill", "fill-rule", "fill-opacity", "stroke", "stroke-width", "stroke-opacity",
           "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset", "clip-path",
           "clip-rule", "id", "x", "y", "width", "height", HREF, "opacity", "preserveAspectRatio"}
INHERITED = {"fill", "fill-rule", "fill-opacity", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap",
             "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset"}


class Unsupported(Exception):
    pass


def matrix(text: str | None) -> list[float]:
    if not text:
        return [1, 0, 0, 1, 0, 0]
    match = re.fullmatch(r"\s*matrix\(([^)]*)\)\s*", text)
    if not match:
        raise Unsupported("transform")
    values = [float(item) for item in re.split(r"[ ,]+", match.group(1).strip())]
    if len(values) != 6:
        raise Unsupported("transform")
    return values


def multiply(a: list[float], b: list[float]) -> list[float]:
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]


def parse_path(data: str) -> list:
    """SVG path data to a flat list of absolute m/l/c/h commands."""
    tokens = TOKEN.findall(data or "")
    out: list = []
    index, command, x, y, start_x, start_y = 0, None, 0.0, 0.0, 0.0, 0.0

    def take() -> float:
        nonlocal index
        index += 1
        return float(tokens[index - 1])

    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
        if command == "M" or command == "m":
            dx, dy = take(), take()
            x, y = (dx, dy) if command == "M" else (x + dx, y + dy)
            start_x, start_y = x, y
            out += ["m", x, y]
            command = "L" if command == "M" else "l"
        elif command == "L" or command == "l":
            dx, dy = take(), take()
            x, y = (dx, dy) if command == "L" else (x + dx, y + dy)
            out += ["l", x, y]
        elif command in ("H", "h"):
            value = take()
            x = value if command == "H" else x + value
            out += ["l", x, y]
        elif command in ("V", "v"):
            value = take()
            y = value if command == "V" else y + value
            out += ["l", x, y]
        elif command in ("C", "c"):
            values = [take() for _ in range(6)]
            if command == "c":
                values = [values[i] + (x if i % 2 == 0 else y) for i in range(6)]
            out += ["c", *values]
            x, y = values[4], values[5]
        elif command in ("Z", "z"):
            out.append("h")
            x, y = start_x, start_y
        else:
            raise Unsupported("path command")
    return out


def color(value: str | None) -> list[float] | None:
    if value in (None, "none"):
        return None
    if value == "black":
        return [0, 0, 0]
    if value == "white":
        return [1, 1, 1]
    match = re.fullmatch(r"#([0-9a-fA-F]{6})", value)
    if match:
        return [int(match.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    match = re.fullmatch(r"rgb\((\d+),\s*(\d+),\s*(\d+)\)", value)
    if match:
        return [min(int(item), 255) / 255 for item in match.groups()]
    raise Unsupported("color")


class Images:
    """Every image becomes fresh bytes: a JPEG's coefficients rewritten, anything else from its pixels."""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.data: list[bytes] = []
        self.known: dict[str, int] = {}

    def add(self, raw: bytes) -> int:
        key = hashlib.sha256(raw).hexdigest()
        if key not in self.known:
            image = Image.open(io.BytesIO(raw))
            image.load()
            self.known[key] = self.jpeg(raw, image) if image.format == "JPEG" else None
            if self.known[key] is None:
                self.known[key] = self.pixels(image)
        return self.known[key]

    def jpeg(self, raw: bytes, image: Image.Image) -> int | None:
        """Rewritten from its coefficients: identical pixels, no metadata, no unusual markers."""
        if image.mode not in ("RGB", "L"):
            return None
        result = subprocess.run(["jpegtran", "-copy", "none", "-optimize"], input=raw, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=60, check=False)
        if result.returncode != 0:
            return None
        try:
            again = Image.open(io.BytesIO(result.stdout))
            again.load()
        except Exception:
            return None
        if again.size != image.size or again.mode != image.mode or again.tobytes() != image.tobytes():
            return None
        return self.store({"kind": "jpeg", "w": image.width, "h": image.height, "channels": len(image.getbands()), "bits": 8}, result.stdout)

    def pixels(self, image: Image.Image) -> int:
        mask = None
        if "A" in image.getbands() or "transparency" in image.info:
            rgba = image.convert("RGBA")
            alpha = rgba.getchannel("A")
            if alpha.getextrema() != (255, 255):
                mask = self.store({"kind": "pixels", "w": rgba.width, "h": rgba.height, "channels": 1, "bits": 8},
                                  zlib.compress(filtered(alpha.tobytes(), rgba.width, 1), 6))
            image = rgba.convert("RGB")
        elif image.mode == "1":
            return self.store({"kind": "pixels", "w": image.width, "h": image.height, "channels": 1, "bits": 1},
                              zlib.compress(image.tobytes(), 6))
        elif image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        # A scan is often plainer than its file says: true greys need one channel, black and white one bit. Same pixels.
        if image.mode == "RGB":
            red, green, blue = image.split()
            if ImageChops.difference(red, green).getextrema() == (0, 0) and ImageChops.difference(green, blue).getextrema() == (0, 0):
                image = red
        if image.mode == "L":
            colours = image.getcolors(3)
            if colours and {value for _count, value in colours} <= {0, 255}:
                return self.store({"kind": "pixels", "w": image.width, "h": image.height, "channels": 1, "bits": 1},
                                  zlib.compress(image.convert("1", dither=Image.Dither.NONE).tobytes(), 6))
        channels = len(image.getbands())
        entry = {"kind": "pixels", "w": image.width, "h": image.height, "channels": channels, "bits": 8}
        if mask is not None:
            entry["mask"] = mask
        return self.store(entry, zlib.compress(filtered(image.tobytes(), image.width, channels), 6))

    def store(self, entry: dict, data: bytes) -> int:
        self.entries.append(entry)
        self.data.append(data)
        return len(self.entries) - 1


def filtered(raw: bytes, width: int, channels: int) -> bytes:
    """PNG rows with the "up" filter (Pillow's frame differencing would need a decode here too); the PDF's predictor 12 undoes it."""
    row_bytes = width * channels
    out = bytearray()
    previous = 0
    mask = int.from_bytes(b"\xff" * row_bytes, "big")
    low = int.from_bytes(b"\x7f" * row_bytes, "big")
    high = mask ^ low
    for start in range(0, len(raw), row_bytes):
        row = int.from_bytes(raw[start:start + row_bytes], "big")
        # Byte-wise subtraction modulo 256 on one big integer: a row at a time, no Python byte loop.
        delta = ((row | high) - (previous & low)) ^ ((row ^ ~previous) & high)
        out.append(2)
        out += (delta & mask).to_bytes(row_bytes, "big")
        previous = row
    return bytes(out)


class Page:
    def __init__(self, page: pymupdf.Page, images: Images, glyphs: dict) -> None:
        self.images = images
        self.glyphs = glyphs
        self.root = ET.fromstring(page.get_svg_image(text_as_path=True))
        self.defs: dict[str, ET.Element] = {}
        self.clips: dict[str, ET.Element] = {}
        self.ops: list = []
        self.points = 0
        for element in self.root.iter():
            if element.tag == SVG + "path" and element.get("id"):
                self.defs[element.get("id")] = element
            elif element.tag == SVG + "clipPath":
                self.clips[element.get("id")] = element

    def glyph(self, name: str) -> int:
        element = self.defs[name]
        key = element.get("d") or ""
        if key not in self.glyphs:
            self.glyphs[key] = (len(self.glyphs), parse_path(key))
        self.count(len(self.glyphs[key][1]))
        return self.glyphs[key][0]

    def count(self, points: int) -> None:
        self.points += points
        if self.points > MAX_PAGE_POINTS:
            raise Unsupported("too many shapes")

    def clip(self, element: ET.Element, transform: list[float]) -> list | None:
        reference = element.get("clip-path")
        if not reference:
            return None
        match = re.fullmatch(r"url\(#([^)]+)\)", reference)
        if not match or match.group(1) not in self.clips:
            raise Unsupported("clip")
        out = []
        for child in self.clips[match.group(1)]:
            if child.tag != SVG + "path" or set(child.attrib) - ALLOWED:
                raise Unsupported("clip content")
            path = parse_path(child.get("d"))
            self.count(len(path))
            out.append(["clip", multiply(matrix(child.get("transform")), transform), path, child.get("clip-rule") == "evenodd"])
        return out

    def walk(self, element: ET.Element, transform: list[float], style: dict) -> None:
        tag = element.tag.removeprefix(SVG)
        if tag in ("defs", "clipPath"):
            return
        if tag not in ("svg", "g", "path", "use", "image"):
            raise Unsupported(f"element {tag[:20]}")
        if tag != "svg" and set(element.attrib) - ALLOWED:
            raise Unsupported("attribute")
        style = {**style, **{key: value for key, value in element.attrib.items() if key in INHERITED or key == "opacity"}}
        own = transform if tag == "svg" else multiply(matrix(element.get("transform")), transform)
        clips = None if tag == "svg" else self.clip(element, own)
        if clips:
            self.ops.append(["q"])
            self.ops += clips
        if tag in ("svg", "g"):
            for child in element:
                self.walk(child, own, style)
        elif tag == "path":
            path = parse_path(element.get("d"))
            self.count(len(path))
            self.draw(own, path, style)
        elif tag == "use":
            target = (element.get(HREF) or "")[1:]
            if target not in self.defs:
                raise Unsupported("use")
            placed = multiply([1, 0, 0, 1, float(element.get("x", 0)), float(element.get("y", 0))], own)
            self.draw(placed, self.glyph(target), style)
        else:
            self.image(element, own)
        if clips:
            self.ops.append(["Q"])

    def draw(self, transform: list[float], shape, style: dict) -> None:
        fill, stroke = color(style.get("fill", "black")), color(style.get("stroke", "none"))
        if not fill and not stroke:
            return
        opacity = float(style.get("opacity", 1))
        line = None
        if stroke:
            dash = style.get("stroke-dasharray", "none")
            line = {"w": float(style.get("stroke-width", 1)), "cap": style.get("stroke-linecap", "butt"),
                    "join": style.get("stroke-linejoin", "miter"), "miter": float(style.get("stroke-miterlimit", 4)),
                    "dash": None if dash == "none" else [float(item) for item in re.split(r"[ ,]+", dash.strip())],
                    "phase": float(style.get("stroke-dashoffset", 0))}
        self.ops.append(["draw", transform, shape, fill, stroke, style.get("fill-rule") == "evenodd",
                         float(style.get("fill-opacity", 1)) * opacity, float(style.get("stroke-opacity", 1)) * opacity, line])

    def image(self, element: ET.Element, transform: list[float]) -> None:
        href = element.get(HREF) or ""
        if not href.startswith("data:image/") or "," not in href:
            raise Unsupported("image")
        index = self.images.add(base64.b64decode("".join(href.split(",", 1)[1].split()), validate=True))
        width, height = float(element.get("width")), float(element.get("height"))
        a, b, c, d, e, f = transform
        # A PDF image fills the unit square with its top at y=1; this page's y runs down.
        self.ops.append(["image", index, [a * width, b * width, -c * height, -d * height, e + c * height, f + d * height]])


def words(page: pymupdf.Page) -> list:
    """What the page shows, for search and copy. Text it hides isn't kept: it can't reach search or the AI unseen."""
    hidden = [pymupdf.Rect(span["bbox"]) for span in page.get_texttrace() if span.get("type") == 3 or span.get("opacity", 1) == 0]
    out = []
    for x0, y0, x1, y1, text, *_rest in page.get_text("words"):
        text = "".join(char for char in text if char.isprintable() and ord(char) < 0xFFFF)[:200]
        if not text or not all(map(math.isfinite, (x0, y0, x1, y1))):
            continue
        middle = pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2)
        if not any(middle in rect for rect in hidden):
            out.append([x0, y0, x1, y1, text])
    return out


def trust_stored(stored: list, read: list) -> bool:
    """Whether a PDF's stored words are what the page shows: fonts can make them wrong ("e$cace", "plani,cation")."""
    def fold(items: list) -> list[str]:
        return [item[4].strip(".,;:!?()[]«»\"'’").casefold() for item in items]
    have, seen = [word for word in fold(stored) if len(word) >= 2], set(fold(read))
    if len(have) < 5:
        return False
    if any(re.search(r"[^\W\d_][\W\d_]+[^\W\d_]", word) and "-" not in word and "'" not in word and "’" not in word and word not in seen for word in have):
        return False
    return sum(word in seen for word in have) / len(have) >= 0.8


def upright(page: pymupdf.Page) -> None:
    """A scan on its side is turned, for reading and for whoever opens the copy; no pixel changes."""
    pixmap = page.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY, alpha=False)
    found = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "0"], input=pixmap.tobytes("png"),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120, check=False).stdout.decode("utf-8", "replace")
    rotate, confidence = re.search(r"Rotate:\s*(\d+)", found), re.search(r"Orientation confidence:\s*([\d.]+)", found)
    if rotate and confidence and int(rotate.group(1)) and float(confidence.group(1)) >= 3:
        page.set_rotation((page.rotation + int(rotate.group(1))) % 360)


def ocr(page: pymupdf.Page) -> list:
    """Invisible text for a page that has none: the reader's search and copy, never what's drawn."""
    pixmap = page.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY, alpha=False)
    result = subprocess.run(["tesseract", "stdin", "stdout", "-l", "fra+eng", "tsv"], input=pixmap.tobytes("png"),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120, check=False)
    if result.returncode != 0:
        return []
    scale = 72 / OCR_DPI
    found = []
    for line in result.stdout.decode("utf-8", "replace").splitlines()[1:]:
        cells = line.split("\t")
        if len(cells) == 12 and cells[0] == "5" and float(cells[10]) >= 30 and cells[11].strip():
            left, top, width, height = (int(item) * scale for item in cells[6:10])
            text = "".join(char for char in cells[11] if char.isprintable() and ord(char) < 0xFFFF)[:200]
            if text:
                found.append([left, top, left + width, top + height, text])
    return found


def main() -> int:
    # A temporary name because MuPDF's repair needs a seekable file; /tmp is the sandbox's own tmpfs.
    with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
        source.write(sys.stdin.buffer.read())
        source.flush()
        document = pymupdf.open(source.name)
        if document.needs_pass:
            return 3
        images = Images()
        glyphs: dict = {}
        pages = []
        ocr_left = MAX_OCR_PAGES
        for page in document:
            # Only a page with no text of its own: a designed page may well be sideways on purpose.
            native = words(page)
            if not native and page.get_images() and ocr_left > 0:
                upright(page)
                native = words(page)
            rect = page.rect
            record: dict = {"w": rect.width, "h": rect.height}
            try:
                rendered = Page(page, images, glyphs)
                rendered.walk(rendered.root, [1, 0, 0, 1, 0, 0], {})
                record["ops"] = rendered.ops
                record["mode"] = "shapes"
            except (Unsupported, KeyError, ValueError, TypeError, ET.ParseError, OSError) as error:
                pixmap = page.get_pixmap(dpi=FALLBACK_DPI, alpha=False)
                index = images.add(pixmap.tobytes("png"))
                record["ops"] = [["image", index, [rect.width, 0, 0, -rect.height, 0, rect.height]]]
                record["mode"] = "pixels"
                record["reason"] = str(error)[:60] or type(error).__name__
            # The search layer: the page's own words when a reader confirms them, else what the reader sees.
            record["words"] = native
            if ocr_left > 0 and (native or page.get_images()):
                ocr_left -= 1
                read = ocr(page)
                if read and not trust_stored(native, read):
                    record["words"] = read
            pages.append(record)
        ordered = sorted(glyphs.values(), key=lambda item: item[0])
        manifest = {"format": "shapes-1", "pages": pages, "glyphs": [path for _index, path in ordered], "images": images.entries}
    out = sys.stdout.buffer
    encoded = json.dumps(manifest, separators=(",", ":"), allow_nan=False).encode()
    out.write(struct.pack(">I", len(encoded)) + encoded)
    for data in images.data:
        out.write(struct.pack(">I", len(data)) + data)
    out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
