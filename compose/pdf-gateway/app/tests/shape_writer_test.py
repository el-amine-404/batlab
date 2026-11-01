#!/usr/bin/env python3
"""The trusted writer refuses whatever a compromised sandbox could send. Standard library only:
python3 -I compose/pdf-gateway/app/tests/shape_writer_test.py"""
import json, struct, sys, tempfile, zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hosts/lab2/pdf-cdr-provider"))
import shape_writer as w  # noqa: E402

PAGE = {"w": 100, "h": 100, "mode": "shapes", "words": [], "ops": []}


def frames(manifest, *blobs, raw=None):
    data = raw if raw is not None else json.dumps(manifest).encode()
    return struct.pack(">I", len(data)) + data + b"".join(struct.pack(">I", len(b)) + b for b in blobs)


def run(body):
    with tempfile.TemporaryDirectory() as folder:
        source, output = Path(folder, "f"), Path(folder, "o.pdf")
        source.write_bytes(body)
        w.build(source, output, 1024 * 1024)
        return output.read_bytes()


def man(ops=(), images=(), glyphs=(), **page):
    return {"format": "shapes-1", "pages": [{**PAGE, "ops": list(ops), **page}], "glyphs": list(glyphs), "images": list(images)}


M = [1, 0, 0, 1, 0, 0]
DRAW = ["draw", M, ["m", 0, 0, "l", 1, 1, "h"], [0, 0, 0], None, False, 1, 1, None]
GOOD = run(frames(man([DRAW], words=[[1, 1, 20, 10, "hello"]])))
assert b"/F0" in GOOD and b"/GlyphLessFont" in GOOD and b"<</Type/Catalog/Pages" in GOOD

pixels = zlib.compress(bytes([2, 1, 2, 3]))
bad = {
    "syntax in a colour": man([["draw", M, ["m", 0, 0], ["/JavaScript", 0, 0], None, False, 1, 1, None]]),
    "syntax in a path": man([["draw", M, ["m", "0 0 m /Launch", 0], [0, 0, 0], None, False, 1, 1, None]]),
    "unknown path command": man([["draw", M, ["BI", 0, 0], [0, 0, 0], None, False, 1, 1, None]]),
    "unknown op": man([["Do", "/Evil"]]),
    "huge number": man([["draw", [1e30, 0, 0, 1, 0, 0], ["m", 0, 0], [0, 0, 0], None, False, 1, 1, None]]),
    "bool as number": man([["draw", [True, 0, 0, 1, 0, 0], ["m", 0, 0], [0, 0, 0], None, False, 1, 1, None]]),
    "unbalanced q": man([["q"]]),
    "unbalanced Q": man([["Q"]]),
    "line cap": man([["draw", M, ["m", 0, 0], None, [0, 0, 0], False, 1, 1, {"w": 1, "cap": "/x", "join": "miter"}]]),
    "glyph index": man([["draw", M, 5, [0, 0, 0], None, False, 1, 1, None]]),
    "image index": man([["image", 3, M]]),
    "page size": man([], w=1e9),
    "format": {"format": "x"},
}
for name, manifest in bad.items():
    try:
        run(frames(manifest))
    except w.ShapeError:
        continue
    raise SystemExit(f"accepted: {name}")


def image(entry, data):
    return frames(man([["image", 0, M]], [entry]), data)


PX = {"kind": "pixels", "w": 1, "h": 1, "channels": 3, "bits": 8}
assert b"/Predictor 15" in run(image(PX, pixels))
JFIF = b"\xff\xe0\x00\x10JFIF\0\x01\x01\0\0\x01\0\x01\0\0"
SOF = b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
SOS = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
JP = {"kind": "jpeg", "w": 1, "h": 1, "channels": 1, "bits": 8}
assert b"/DCTDecode" in run(image(JP, b"\xff\xd8" + JFIF + SOF + SOS + b"\x12\xff\x00\x34\xff\xd9"))
images = {
    "zip bomb": image(PX, zlib.compress(b"\0" * 10_000_000)),
    "short pixels": image(PX, zlib.compress(b"\2\1")),
    "bad row filter": image(PX, zlib.compress(bytes([9, 1, 2, 3]))),
    "pixels not zlib": image(PX, b"not zlib"),
    "colour count": image({**PX, "channels": 4}, pixels),
    "too large": image({**PX, "w": 100000, "h": 100000}, pixels),
    "kind": image({**PX, "kind": "jbig2"}, pixels),
    "jpeg comment": image(JP, b"\xff\xd8\xff\xfe\x00\x04hi" + SOF + SOS + b"\xff\xd9"),
    "jpeg exif": image(JP, b"\xff\xd8\xff\xe1\x00\x04hi" + SOF + SOS + b"\xff\xd9"),
    "jpeg size lies": image({**JP, "w": 9}, b"\xff\xd8" + SOF + SOS + b"\xff\xd9"),
    "jpeg marker in data": image(JP, b"\xff\xd8" + SOF + SOS + b"\xff\xfe\xff\xd9"),
    "jpeg appended": image(JP, b"\xff\xd8" + SOF + SOS + b"\xff\xd9%PDF-1.7 /JS\xff\xd9"),
    "not jpeg": image(JP, b"%PDF-1.7"),
    "data after": image(PX, pixels) + struct.pack(">I", 1) + b"x",
    "short frame": image(PX, pixels)[:-1],
    "nan": frames(None, raw=b'{"format":"shapes-1","pages":[{"w":NaN}]}'),
    "not json": frames(None, raw=b"\xff\xfe"),
    "deep json": frames(None, raw=b"[" * 200000),
}
for name, body in images.items():
    try:
        run(body)
    except w.ShapeError:
        continue
    raise SystemExit(f"accepted: {name}")
injected = run(frames(man(words=[[1, 1, 20, 10, ">)/JS (x"]])))
assert b"/JS" not in injected and b"(x" not in injected
streams = []
for part in injected.split(b"stream\n")[1:]:
    try:
        streams.append(zlib.decompress(part.split(b"\nendstream")[0]))
    except zlib.error:
        pass
assert any(b"<003e0029002f004a0053002000280078>" in item for item in streams) and not any(b"/JS" in item for item in streams)
print(f"shape writer: {len(bad) + len(images)} hostile inputs refused")
