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
                  "/GoToR", "/GoToE", "/Names", "/Encrypt", "/Font", "/Movie", "/Sound", "/Threads"}
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
        fail(findings, "qpdf-json", "STRUCTURE_UNREADABLE", "Decoded structure could not be inspected", **engine)
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
            if not isinstance(resources, dict) or set(resources) - {"/XObject", "/ProcSet"}:
                fail(findings, "structure", "PAGE_RESOURCES_UNEXPECTED", "Output page has non-image resources",
                     object=name, keys=sorted(resources) if isinstance(resources, dict) else None)
    engine["pages"] = len(pages)
    return engine, pages


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
