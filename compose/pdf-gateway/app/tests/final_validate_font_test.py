#!/usr/bin/env python3
"""The text-layer font allowlist of final validation (structure check only).

Run in the analysis-worker image: final_validate_font_test.py DANGERZONE_OCR_PDF PLAIN_FONT_PDF WORKDIR
The first file is a real `dangerzone-cli --ocr-lang` output of a synthetic fixture.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/opt/pdf-gateway")
import final_validate  # noqa: E402

ocr_pdf, plain_pdf, work = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
failures = []
work.mkdir(parents=True, exist_ok=True)


def check(name, condition, detail=""):
    print(("ok - " if condition else "FAIL - ") + name + ("" if condition else f": {detail}"))
    if not condition:
        failures.append(name)


def codes(path):
    findings = []
    engine, _pages = final_validate.structure(path, findings)
    return {item["code"] for item in findings}, engine


def mutate(source, target, replace):
    """Edit a PDF's decoded objects through qpdf's QDF form, then re-pack it."""
    qdf = work / (target.stem + ".qdf.pdf")
    subprocess.run(["qpdf", "--qdf", "--object-streams=disable", str(source), str(qdf)], check=True)
    data = qdf.read_bytes()
    changed = replace(data)
    assert changed != data, "mutation did not apply"
    fixed = work / (target.stem + ".fixed.pdf")
    fixed.write_bytes(changed)
    subprocess.run(["fix-qdf", str(fixed)], check=True, stdout=open(work / (target.stem + ".fix.pdf"), "wb"))
    subprocess.run(["qpdf", str(work / (target.stem + ".fix.pdf")), str(target)], check=True)
    return target


found, engine = codes(ocr_pdf)
check("Dangerzone OCR output passes the structure check", not found, found)
check("its text layer font was checked", engine.get("textLayerFonts", 0) >= 2, engine)

found, _ = codes(plain_pdf)
check("an ordinary font is refused", "FONT_NOT_ALLOWED" in found, found)

visible = mutate(ocr_pdf, work / "visible.pdf", lambda data: data.replace(b"3 Tr", b"0 Tr"))
found, _ = codes(visible)
check("visible text in the layer is refused", "VISIBLE_TEXT" in found, found)

renamed = mutate(ocr_pdf, work / "renamed.pdf", lambda data: data.replace(b"/BaseFont /GlyphLessFont", b"/BaseFont /Helvetica", 1))
found, _ = codes(renamed)
check("another font name is refused", "FONT_NOT_ALLOWED" in found, found)


def swap_font_file(data):
    # Change one byte inside the embedded font program (FontFile2 stream of the QDF).
    match = re.search(rb"(/Length1 \d+[^>]*>>\s*stream\n)(.)", data, flags=re.S) or re.search(rb"(%% Original object ID: \d+ 0\n\d+ 0 obj\n<<\n  /Length \d+\n  /Length1[^\n]*\n>>\nstream\n)(.)", data, flags=re.S)
    assert match, "font stream not found"
    return data[:match.start(2)] + bytes([match.group(2)[0] ^ 0x01]) + data[match.end(2):]


swapped = mutate(ocr_pdf, work / "swapped.pdf", swap_font_file)
found, _ = codes(swapped)
check("a different font program is refused", "FONT_NOT_ALLOWED" in found, found)

shutil.rmtree(work, ignore_errors=True)
print("final validation font allowlist tests " + ("passed" if not failures else f"FAILED: {failures}"))
sys.exit(1 if failures else 0)
