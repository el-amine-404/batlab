#!/usr/bin/env python3
"""Final validation of the exact bytes a CDR stage produced.

Every earlier attestation applies to different bytes, so this re-runs ClamAV,
qpdf and YARA on the output itself and checks decoded-structure postconditions
of a raster reconstruction. Any finding, engine failure, or unknown result
denies release; only an empty failure list yields ALLOW_SANITIZED.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import scan

REPORT_VERSION = "final-validation-report-1"
POLICY_VERSION = "final-policy-1"
MAX_BYTES = 256 * 1024 * 1024
MAX_PAGES = 1000
# Fraction of non-white pixels below which a rendered page counts as blank.
BLANK_INK = 0.0005
MAX_JSON = 16 * 1024 * 1024
# Any of these names anywhere in the decoded object graph means the output
# carries more than page images. Raster reconstruction must never produce them.
FORBIDDEN_KEYS = {"/JS", "/JavaScript", "/AA", "/OpenAction", "/Launch", "/EmbeddedFile", "/EmbeddedFiles",
                  "/RichMedia", "/XFA", "/AcroForm", "/Annots", "/URI", "/SubmitForm", "/ImportData",
                  "/GoToR", "/GoToE", "/Names", "/Encrypt", "/Font", "/Movie", "/Sound", "/Threads", "/Metadata"}
# The one font allowed: Tesseract's GlyphLessFont, which Dangerzone's OCR uses for
# an invisible text layer. It has no glyph outlines; its bytes are pinned. Any other
# font, any font file other than this one, or visible text fails the check.
GLYPHLESS_FONT_SHA256 = {"c7845420925a23d88ed830a63957b8af85a66a8daf8d9fc90e843673b2ef1a59"}
TYPE0_KEYS = {"/Type", "/Subtype", "/BaseFont", "/Encoding", "/DescendantFonts", "/ToUnicode"}
CIDFONT_KEYS = {"/Type", "/Subtype", "/BaseFont", "/CIDSystemInfo", "/FontDescriptor", "/CIDToGIDMap", "/DW", "/W"}
DESCRIPTOR_KEYS = {"/Type", "/FontName", "/Flags", "/FontBBox", "/ItalicAngle", "/Ascent", "/Descent", "/CapHeight",
                   "/StemV", "/FontFile2"}
MAX_FONT_STREAM = 256 * 1024
# Document information may carry only these keys (string values; checked below).
INFO_KEYS = {"/Title", "/Subject", "/Keywords", "/Author", "/Creator", "/Producer", "/CreationDate", "/ModDate"}
FORBIDDEN_VALUES = {"/JavaScript", "/Launch", "/URI", "/SubmitForm", "/ImportData", "/GoToR", "/GoToE",
                    "/RichMedia", "/Movie", "/Sound", "/EmbeddedFile", "/Form", "/PS"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fail(findings: list[dict], engine: str, code: str, message: str, **evidence: object) -> None:
    findings.append(scan.finding(engine, code, "ERROR", message, **evidence))


def walk(value, findings: list[dict], where: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_KEYS:
                fail(findings, "structure", "FORBIDDEN_STRUCTURE", "Output contains a forbidden PDF name", name=key, object=where)
            walk(item, findings, where)
    elif isinstance(value, list):
        for item in value:
            walk(item, findings, where)
    elif isinstance(value, str) and value in FORBIDDEN_VALUES:
        fail(findings, "structure", "FORBIDDEN_STRUCTURE", "Output references a forbidden PDF type", name=value, object=where)


def structure(path: Path, findings: list[dict]) -> tuple[dict, list[list[float]]]:
    """Decode the object graph with qpdf, so object streams cannot hide names."""
    # scan.command keeps 64 KiB of output; the decoded graph needs a larger bound.
    try:
        result = subprocess.run(["qpdf", "--json=2", "--json-stream-data=none", str(path)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=False,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/nonexistent"},
                                preexec_fn=scan.bounded_preexec)
        status = "OK" if result.returncode == 0 and len(result.stdout) <= MAX_JSON else "FAILED"
        engine = {"engine": "qpdf-json", "status": status, "exitCode": result.returncode, "bytes": len(result.stdout)}
    except (OSError, subprocess.TimeoutExpired) as error:
        engine = {"engine": "qpdf-json", "status": "FAILED", "exitCode": None, "error": type(error).__name__}
    if engine["status"] != "OK":
        fail(findings, "qpdf-json", "STRUCTURE_UNREADABLE", "Decoded structure could not be inspected",
             status=engine["status"], exitCode=engine["exitCode"])
        return engine, []
    try:
        document = json.loads(result.stdout)
        objects = document["qpdf"][1]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        fail(findings, "qpdf-json", "STRUCTURE_UNREADABLE", "qpdf JSON was not in the expected form")
        return engine, []
    pages = []
    for name, entry in objects.items():
        value = entry.get("value") if isinstance(entry, dict) else None
        stream = entry.get("stream", {}).get("dict") if isinstance(entry, dict) else None
        # A page's own /Resources may name fonts (the text layer, checked in fonts());
        # /Font anywhere else stays forbidden.
        if isinstance(value, dict) and value.get("/Type") == "/Page" and isinstance(value.get("/Resources"), dict):
            walk({**value, "/Resources": {key: item for key, item in value["/Resources"].items() if key != "/Font"}}, findings, name)
        else:
            walk(value, findings, name)
        walk(stream, findings, name)
        if isinstance(stream, dict) and stream.get("/Type") == "/XObject" and stream.get("/Subtype") != "/Image":
            fail(findings, "structure", "NON_IMAGE_XOBJECT", "Output page uses a non-image XObject", object=name)
        if isinstance(value, dict) and value.get("/Type") == "/Page":
            box = value.get("/MediaBox")
            resources = value.get("/Resources")
            if not isinstance(box, list) or len(box) != 4:
                fail(findings, "structure", "PAGE_BOX_INVALID", "Output page lacks a MediaBox", object=name)
                continue
            pages.append([float(item) for item in box])
            if not isinstance(resources, dict) or set(resources) - {"/XObject", "/ProcSet", "/Font"}:
                fail(findings, "structure", "PAGE_RESOURCES_UNEXPECTED", "Output page has non-image resources",
                     object=name, keys=sorted(resources) if isinstance(resources, dict) else None)
    # Info dictionaries (trailer, and MuPDF's catalog-level copy) hold strings
    # under standard keys only; anything else is an unexpected carrier.
    info_refs = []  # references ("N 0 R") or inline dictionaries (MuPDF's catalog /Info)
    for holder in ("trailer", *(name for name, entry in objects.items()
                                if isinstance(entry, dict) and isinstance(entry.get("value"), dict)
                                and entry["value"].get("/Type") == "/Catalog")):
        value = objects.get(holder, {}).get("value", {})
        if isinstance(value, dict) and "/Info" in value:
            info_refs.append(value["/Info"])
    for ref in info_refs:
        info = ref if isinstance(ref, dict) else objects.get(f"obj:{ref}", {}).get("value")
        if not isinstance(info, dict):
            fail(findings, "structure", "INFO_INVALID", "Document information is not a dictionary", ref=str(ref)[:40])
            continue
        unexpected = sorted(set(info) - INFO_KEYS)
        non_strings = sorted(key for key, item in info.items() if not (isinstance(item, str) and item.startswith(("u:", "b:"))))
        if unexpected or non_strings:
            fail(findings, "structure", "INFO_NOT_ALLOWLISTED", "Document information has non-allowlisted content",
                 keys=unexpected[:10], nonStrings=non_strings[:10])
    engine["pages"] = len(pages)
    engine["infoDictionaries"] = len(info_refs)
    engine["textLayerFonts"] = fonts(path, objects, findings)
    return engine, pages


def stream_bytes(path: Path, ref: str, limit: int) -> bytes | None:
    """One object's decoded stream data, bounded; None when it can't be read."""
    match = re.fullmatch(r"(\d+) (\d+) R", ref) if isinstance(ref, str) else None
    if not match:
        return None
    try:
        result = subprocess.run(["qpdf", f"--show-object={match.group(1)},{match.group(2)}", "--filtered-stream-data", str(path)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
                                check=False, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/nonexistent"},
                                preexec_fn=scan.bounded_preexec)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 and len(result.stdout) <= limit else None


def fonts(path: Path, objects: dict, findings: list[dict]) -> int:
    """Admit only GlyphLessFont text layers drawn invisibly; returns how many fonts were checked."""
    def value(ref):
        entry = objects.get(f"obj:{ref}") if isinstance(ref, str) else None
        return entry.get("value") if isinstance(entry, dict) else None

    def bad(code, message, **evidence):
        fail(findings, "structure", code, message, **evidence)

    checked = 0
    font_maps = []
    for name, entry in objects.items():
        item = entry.get("value") if isinstance(entry, dict) else None
        stream = entry.get("stream", {}).get("dict") if isinstance(entry, dict) else None
        if isinstance(stream, dict) and stream.get("/Type") in ("/Font", "/FontDescriptor"):
            bad("FONT_NOT_ALLOWED", "A font object is a stream", object=name)
        if not isinstance(item, dict):
            continue
        if item.get("/Type") == "/Page":
            resources = item.get("/Resources")
            if isinstance(resources, dict) and "/Font" in resources:
                font_maps.append((name, resources["/Font"], item.get("/Contents")))
        if item.get("/Type") == "/Font":
            checked += 1
            subtype = item.get("/Subtype")
            if subtype == "/Type0":
                if set(item) - TYPE0_KEYS or item.get("/BaseFont") != "/GlyphLessFont" or item.get("/Encoding") != "/Identity-H":
                    bad("FONT_NOT_ALLOWED", "Only the GlyphLessFont text layer is allowed", object=name, font=str(item.get("/BaseFont"))[:40])
                to_unicode = item.get("/ToUnicode")
                if to_unicode is not None and stream_bytes(path, to_unicode, MAX_FONT_STREAM) is None:
                    bad("FONT_NOT_ALLOWED", "Text layer ToUnicode map unreadable or too large", object=name)
            elif subtype == "/CIDFontType2":
                if set(item) - CIDFONT_KEYS or item.get("/BaseFont") != "/GlyphLessFont":
                    bad("FONT_NOT_ALLOWED", "Only the GlyphLessFont text layer is allowed", object=name)
                gid_map = item.get("/CIDToGIDMap")
                if gid_map not in (None, "/Identity") and stream_bytes(path, gid_map, MAX_FONT_STREAM) is None:
                    bad("FONT_NOT_ALLOWED", "Text layer glyph map unreadable or too large", object=name)
                descriptor = value(item.get("/FontDescriptor"))
                if not isinstance(descriptor, dict) or set(descriptor) - DESCRIPTOR_KEYS \
                        or descriptor.get("/FontName") != "/GlyphLessFont" or "/FontFile2" not in descriptor:
                    bad("FONT_NOT_ALLOWED", "Text layer font descriptor is not the expected one", object=name)
                else:
                    data = stream_bytes(path, descriptor["/FontFile2"], MAX_FONT_STREAM)
                    if data is None or hashlib.sha256(data).hexdigest() not in GLYPHLESS_FONT_SHA256:
                        bad("FONT_NOT_ALLOWED", "Text layer font file is not the pinned GlyphLessFont",
                            object=name, sha256=hashlib.sha256(data).hexdigest() if data else None)
            else:
                bad("FONT_NOT_ALLOWED", "Only the GlyphLessFont text layer is allowed", object=name, subtype=str(subtype)[:20])
        if item.get("/Type") == "/FontDescriptor" and set(item) - DESCRIPTOR_KEYS:
            bad("FONT_NOT_ALLOWED", "Font descriptor carries unexpected entries", object=name, keys=sorted(set(item) - DESCRIPTOR_KEYS)[:10])
    for page, font_map, contents in font_maps:
        if not isinstance(font_map, dict) or not all(isinstance(value(ref), dict) and value(ref).get("/Subtype") == "/Type0"
                                                     for ref in font_map.values()):
            bad("FONT_NOT_ALLOWED", "Page font map does not point at the text layer font", object=page)
        refs = contents if isinstance(contents, list) else [contents]
        content = b""
        for ref in refs:
            data = stream_bytes(path, ref, 16 * 1024 * 1024)
            if data is None:
                bad("TEXT_LAYER_UNREADABLE", "Page content with a text layer could not be read", object=page)
                break
            content += data + b"\n"
        # Every text object must switch to render mode 3 (invisible) before it shows text.
        for block in re.findall(rb"\bBT\b(.*?)\bET\b", content, flags=re.S):
            shows = re.search(rb"(\)|>|\])\s*(Tj|TJ|'|\")", block)
            mode = re.search(rb"\b3\s+Tr\b", block)
            if shows and (not mode or mode.start() > shows.start()) or re.search(rb"\b[0-24-7]\s+Tr\b", block):
                bad("VISIBLE_TEXT", "Text layer is not invisible", object=page)
                break
    return checked


def page_count(path: Path) -> int | None:
    result = scan.command("qpdf-npages", ["qpdf", "--show-npages", str(path)], timeout=30)
    if result["status"] != "OK":
        return None
    try:
        return int(result["output"].strip())
    except ValueError:
        return None


def blank_pages(path: Path, pages: int, findings: list[dict]) -> dict:
    """Render the output at low resolution and measure ink per page."""
    version = scan.version(["pdftoppm", "-v"])
    with tempfile.TemporaryDirectory(prefix="render-", dir=os.getenv("TMPDIR", "/tmp")) as directory:
        result = scan.command("pdftoppm", ["pdftoppm", "-r", "12", "-gray", str(path), f"{directory}/p"], timeout=120)
        engine = {"engine": "pdftoppm", "status": result["status"], "version": version, "exitCode": result["exitCode"]}
        if result["status"] != "OK":
            fail(findings, "pdftoppm", "RENDER_FAILED", "Output could not be rendered for visual checks", status=result["status"])
            return engine
        rendered = sorted(Path(directory).glob("p-*.pgm"), key=lambda item: int(re.findall(r"(\d+)\.pgm$", item.name)[0]))
        if len(rendered) != pages:
            fail(findings, "pdftoppm", "RENDER_PAGE_COUNT", "Rendered page count differs", rendered=len(rendered), expected=pages)
            return engine
        blanks = []
        for number, image in enumerate(rendered, 1):
            data = image.read_bytes()
            match = re.match(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s", data)
            if not match:
                fail(findings, "pdftoppm", "RENDER_FAILED", "Unexpected rendered image format", page=number)
                return engine
            pixels = data[match.end():]
            ink = sum(1 for value in pixels if value < 200) / max(1, len(pixels))
            if ink < BLANK_INK:
                blanks.append(number)
        engine["blankPages"] = blanks
        if blanks and len(blanks) == pages:
            fail(findings, "visual", "OUTPUT_ALL_PAGES_BLANK", "Every output page rendered blank", pages=pages)
        elif blanks:
            # A blank page can be legitimate (separator sheets); record it for review UI.
            findings.append(scan.finding("visual", "OUTPUT_BLANK_PAGES", "INFO", "Some output pages rendered blank", pages=blanks))
        return engine


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--source", type=Path, required=True, help="quarantined original, for page-count comparison")
    parser.add_argument("--clamd-host", default=os.getenv("PDF_GATEWAY_CLAMD_HOST", "clamav"))
    parser.add_argument("--clamd-port", type=int, default=int(os.getenv("PDF_GATEWAY_CLAMD_PORT", "3310")))
    parser.add_argument("--clamd-timeout", type=int, default=30)
    parser.add_argument("--max-definition-age-hours", type=int, default=48)
    parser.add_argument("--rules", type=Path, default=Path("/opt/pdf-gateway/rules/pdf-risk.yar"))
    args = parser.parse_args()

    findings: list[dict] = []
    engines: list[dict] = []
    path = args.path.resolve(strict=True)
    size = path.stat().st_size
    initial = sha256(path)
    if not path.is_file() or not 0 < size <= MAX_BYTES:
        fail(findings, "input", "OUTPUT_SIZE_INVALID", "Output size is outside policy", bytes=size)
    if initial != args.expected_sha256:
        fail(findings, "input", "OUTPUT_HASH_MISMATCH", "Output bytes are not the recorded CDR output")

    clam_engine, clam_findings = scan.clamav(path, args.clamd_host, args.clamd_port,
                                             args.clamd_timeout, args.max_definition_age_hours)
    engines.append(clam_engine)
    findings.extend(clam_findings)
    if clam_engine["status"] not in ("CLEAN", "FOUND") and not clam_findings:
        fail(findings, "clamav", "CLAMAV_NOT_CLEAN", "ClamAV did not report a clean scan", status=clam_engine["status"])

    # Reuse the M2 structural engines on the output; at this stage every
    # keyword, YARA match, encryption result, or engine failure is a denial.
    structural_engines, structural_findings = scan.structural(path, args.rules)
    engines.extend(structural_engines)
    for item in structural_findings:
        item = dict(item, severity="ERROR") if item["severity"] != "ERROR" else item
        findings.append(item)

    decoded, boxes = structure(path, findings)
    engines.append(decoded)
    output_pages = page_count(path)
    source_pages = page_count(args.source.resolve(strict=True))
    engines.append({"engine": "page-count", "status": "OK" if output_pages and source_pages else "FAILED",
                    "outputPages": output_pages, "sourcePages": source_pages})
    if output_pages is None or source_pages is None:
        fail(findings, "page-count", "PAGE_COUNT_UNKNOWN", "Page counts could not be established")
    elif output_pages != source_pages or output_pages != len(boxes):
        fail(findings, "page-count", "PAGE_COUNT_MISMATCH", "Output page count differs from the source",
             outputPages=output_pages, sourcePages=source_pages, decodedPages=len(boxes))
    elif output_pages > MAX_PAGES:
        fail(findings, "page-count", "PAGE_LIMIT", "Output exceeds the page limit", pages=output_pages)
    else:
        engines.append(blank_pages(path, output_pages, findings))

    final = sha256(path)
    if final != initial:
        fail(findings, "input", "OUTPUT_CHANGED_DURING_VALIDATION", "Output bytes changed during validation")

    denials = [item for item in findings if item["severity"] != "INFO"]
    if any(item["code"] == "MALWARE_DETECTED" for item in denials):
        verdict, detection, decision = "FAIL", "MALICIOUS", "BLOCK"
    elif any(item["code"] in {"CLAMAV_UNAVAILABLE", "CLAMAV_DATABASE_STALE", "CLAMAV_SCAN_ERROR", "ENGINE_FAILED",
                              "STRUCTURE_UNREADABLE", "PAGE_COUNT_UNKNOWN", "RENDER_FAILED"} for item in denials):
        verdict, detection, decision = "UNKNOWN", "UNKNOWN", "REVIEW_REQUIRED"
    elif denials:
        verdict, detection, decision = "FAIL", "SUSPICIOUS", "REVIEW_REQUIRED"
    else:
        verdict, detection, decision = "PASS", "NO_THREATS_DETECTED", "ALLOW_SANITIZED"

    report = {"schemaVersion": REPORT_VERSION, "policyVersion": POLICY_VERSION,
              "rulesetVersion": scan.RULESET_VERSION, "outputSha256": initial, "outputBytes": size,
              "verdict": verdict, "detection": detection, "policyDecision": decision,
              "failureCodes": sorted({item["code"] for item in denials}),
              "engines": engines, "findings": findings,
              "limitations": ["Final validation reduces but does not eliminate risk; converter and decoder "
                              "vulnerabilities remain possible.",
                              "Visual checks flag blank or missing pages; they do not certify fidelity."]}
    json.dump(report, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return {"PASS": 0, "FAIL": 1}.get(verdict, 2)


if __name__ == "__main__":
    raise SystemExit(main())
