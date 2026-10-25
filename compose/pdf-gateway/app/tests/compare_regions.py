#!/usr/bin/env python3
"""Compare ink inside named regions of a generated fixture and its CDR output.

Usage: compare_regions.py FIXTURE.pdf OUTPUT.pdf REGIONS_JSON NAME

Only test-generated fixtures and sanitized outputs are rendered. Prints one
JSON line per region and exits non-zero if any region lost its appearance.
This checks gross preservation (ink present where the mark was), not fidelity.
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DPI = 50


def render(path: str, directory: Path, prefix: str) -> tuple[int, int, bytes]:
    subprocess.run(["pdftoppm", "-r", str(DPI), "-gray", "-f", "1", "-l", "1", path, str(directory / prefix)],
                   check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    data = next(directory.glob(f"{prefix}-*.pgm")).read_bytes()
    match = re.match(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s", data)
    return int(match.group(1)), int(match.group(2)), data[match.end():]


def ink(image: tuple[int, int, bytes], box: list[float], page: list[float]) -> float:
    width, height, pixels = image
    sx, sy = width / page[0], height / page[1]
    x0, x1 = int(box[0] * sx), int(box[2] * sx)
    # PDF y grows upwards; image rows grow downwards.
    y0, y1 = int((page[1] - box[3]) * sy), int((page[1] - box[1]) * sy)
    dark = total = 0
    for y in range(max(0, y0), min(height, y1)):
        row = pixels[y * width:(y + 1) * width]
        for x in range(max(0, x0), min(width, x1)):
            total += 1
            dark += row[x] < 160
    return dark / max(1, total)


def main() -> int:
    fixture, output, regions_file, name = sys.argv[1:5]
    spec = json.loads(Path(regions_file).read_text())
    page = spec["page"]
    with tempfile.TemporaryDirectory() as directory:
        source_image = render(fixture, Path(directory), "src")
        output_image = render(output, Path(directory), "out")
    ok = True
    for region, box in spec[name].items():
        before, after = ink(source_image, box, page), ink(output_image, box, page)
        preserved = before > 0.01 and after >= 0.5 * before
        ok &= preserved
        print(json.dumps({"fixture": name, "region": region, "sourceInk": round(before, 4),
                          "outputInk": round(after, 4), "preserved": preserved}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
