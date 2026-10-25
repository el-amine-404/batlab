#!/usr/bin/env python3
"""Generate safe, deterministic M3 PDF fixtures (stdlib only, no real malware).

Usage: make_fixtures.py OUTPUT_DIR

Visual regions used by appearance comparisons are written to regions.json in
PDF points (origin bottom-left), so tests can compare the same areas after CDR.
"""

from __future__ import annotations

import json
import math
import sys
import zlib
from pathlib import Path

PAGE = (400, 200)


def pdf(objects: list[bytes]) -> bytes:
    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


def stream(content: bytes, extra: bytes = b"") -> bytes:
    return b"<< /Length %d %s>>\nstream\n" % (len(content), extra) + content + b"\nendstream"


def page_pdf(content: bytes, catalog_extra: bytes = b"", resources_extra: bytes = b"",
             extra_objects: list[bytes] | None = None, pages: int = 1) -> bytes:
    # Objects: 1 catalog, 2 pages, 3 font, 4.. page/content pairs, then extras.
    extra_objects = extra_objects or []
    first_extra = 4 + 2 * pages
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(pages))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R " + catalog_extra + b">>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index in range(pages):
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE[0]} {PAGE[1]}] "
                       f"/Resources << /Font << /F1 3 0 R >> ".encode() + resources_extra
                       + f">> /Contents {5 + 2 * index} 0 R >>".encode())
        objects.append(stream(content.replace(b"{page}", str(index + 1).encode())))
    assert len(objects) + 1 == first_extra
    return pdf(objects + extra_objects)


def text(line: str, y: int = 150) -> bytes:
    return f"BT /F1 16 Tf 30 {y} Td ({line}) Tj ET\n".encode()


def handwritten() -> tuple[bytes, dict]:
    # A thick stroked Bezier "signature" plus a raster signature-like image.
    squiggle = (b"q 0 0 0 RG 3 w 1 J 1 j\n"
                b"60 60 m 80 110 100 20 120 70 c 140 120 160 30 180 75 c 200 110 220 40 250 65 c S\n"
                b"70 45 m 240 45 l S Q\n")
    width, height = 64, 32
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            ink = abs((y - 16) - int(10 * math.sin(x / 6))) < 3
            row.append(0 if ink else 255)
        rows.append(bytes(row))
    image = zlib.compress(b"".join(rows))
    content = (text("Handwritten and graphical signature appearance", 170) + squiggle
               + b"q 80 0 0 40 290 50 cm /Im1 Do Q\n")
    image_object = stream(image, f"/Type /XObject /Subtype /Image /Width {width} /Height {height} "
                                 f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode ".encode())
    data = page_pdf(content, resources_extra=b"/XObject << /Im1 6 0 R >> ", extra_objects=[image_object])
    regions = {"vectorSignature": [55, 15, 255, 125], "rasterSignature": [290, 50, 370, 90]}
    return data, regions


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    fixtures = {
        "unsigned.pdf": page_pdf(text("M3 unsigned CDR fixture")),
        "multipage.pdf": page_pdf(text("M3 multipage fixture page {page}"), pages=3),
        # Long enough that a short provider timeout fires while the sandbox runs.
        "many-pages.pdf": page_pdf(text("M3 timeout fixture page {page}"), pages=250),
        "visible-only.pdf": page_pdf(text("Handwritten signature: Alice Example") + text("(not cryptographic)", 120)),
        # Benign JavaScript and an OpenAction: active content, not malware.
        "active-js.pdf": page_pdf(text("M3 active content fixture"), catalog_extra=b"/OpenAction 6 0 R ",
                                  extra_objects=[b"<< /Type /Action /S /JavaScript /JS (app.alert\\('m3'\\);) >>"]),
        "malformed.pdf": b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 9 0 R >>\nthis is not a valid PDF body\n%%EOF\n",
        "not-pdf.bin": b"not a PDF at all\n",
    }
    # EICAR test string (not malware) inside an uncompressed stream.
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    fixtures["eicar-in-stream.pdf"] = page_pdf(text("M3 EICAR fixture"), extra_objects=[stream(eicar)])
    # Structurally valid raster page whose only image is white: renders blank.
    white = zlib.compress(b"\xff" * 32 * 16)
    fixtures["blank-image.pdf"] = pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE[0]} {PAGE[1]}] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>".encode(),
        stream(f"q {PAGE[0]} 0 0 {PAGE[1]} 0 0 cm /Im0 Do Q".encode()),
        stream(white, b"/Type /XObject /Subtype /Image /Width 32 /Height 16 /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "),
    ])
    hand, regions = handwritten()
    fixtures["handwritten.pdf"] = hand
    for name, data in fixtures.items():
        (out / name).write_bytes(data)
    (out / "regions.json").write_text(json.dumps({"page": PAGE, "handwritten.pdf": regions,
                                                  "signed.pdf": {"visibleSignatureWidget": [30, 20, 260, 85]}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
