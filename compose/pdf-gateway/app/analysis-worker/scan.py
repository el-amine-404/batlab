#!/usr/bin/env python3
"""Bounded, deterministic PDF scanner. It never modifies the input file."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import resource
import socket
import struct
import subprocess
import sys
from pathlib import Path

REPORT_VERSION = "scan-report-1"
POLICY_VERSION = "policy-1"
RULESET_VERSION = "pdf-risk-1"
MAX_OUTPUT = 65536
KEYWORDS = ("JS", "JavaScript", "AA", "OpenAction", "Launch", "EmbeddedFile", "RichMedia", "XFA", "AcroForm", "ObjStm")
ACTIVE = {"JS", "JavaScript", "AA", "OpenAction", "Launch", "RichMedia"}


def finding(engine: str, code: str, severity: str, message: str, **evidence: object) -> dict:
    return {"engine": engine, "code": code, "severity": severity, "message": message, "evidence": evidence}


def bounded_preexec() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))


def command(name: str, argv: list[str], timeout: int = 20) -> dict:
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout, check=False,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/nonexistent"},
                                preexec_fn=bounded_preexec)
        output = result.stdout[:MAX_OUTPUT].decode("utf-8", "replace")
        return {"engine": name, "status": "OK" if result.returncode == 0 else "FAILED",
                "exitCode": result.returncode, "output": output,
                "outputTruncated": len(result.stdout) > MAX_OUTPUT}
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or b"")[:MAX_OUTPUT].decode("utf-8", "replace")
        return {"engine": name, "status": "TIMEOUT", "exitCode": None,
                "output": output, "outputTruncated": bool(error.stdout and len(error.stdout) > MAX_OUTPUT)}
    except OSError as error:
        return {"engine": name, "status": "UNAVAILABLE", "exitCode": None,
                "output": type(error).__name__, "outputTruncated": False}


def version(argv: list[str]) -> str:
    result = command(argv[0], argv, 5)
    line = result["output"].splitlines()
    return line[0][:200] if result["status"] == "OK" and line else "UNKNOWN"


def clamd_request(host: str, port: int, payload: bytes, timeout: int) -> bytes:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload)
        chunks = []
        total = 0
        while total < MAX_OUTPUT:
            part = sock.recv(min(4096, MAX_OUTPUT - total))
            if not part:
                break
            chunks.append(part)
            total += len(part)
            if b"\0" in part:
                break
        return b"".join(chunks).rstrip(b"\0\n")


def clamav(path: Path, host: str, port: int, timeout: int, max_age_hours: int) -> tuple[dict, list[dict]]:
    findings: list[dict] = []
    try:
        raw_version = clamd_request(host, port, b"zVERSION\0", timeout).decode("utf-8", "replace")
        parts = raw_version.split("/")
        db_version = parts[1] if len(parts) > 1 else "UNKNOWN"
        db_date = parts[2] if len(parts) > 2 else ""
        try:
            parsed = dt.datetime.strptime(db_date, "%a %b %d %H:%M:%S %Y").replace(tzinfo=dt.timezone.utc)
            age_hours = max(0.0, (dt.datetime.now(dt.timezone.utc) - parsed).total_seconds() / 3600)
        except ValueError:
            age_hours = None
        if age_hours is None or age_hours > max_age_hours:
            findings.append(finding("clamav", "CLAMAV_DATABASE_STALE", "ERROR",
                                    "ClamAV signature freshness cannot satisfy policy",
                                    databaseVersion=db_version, databaseDate=db_date,
                                    maximumAgeHours=max_age_hours, observedAgeHours=age_hours))
            return ({"engine": "clamav", "status": "STALE", "version": parts[0][:120],
                     "databaseVersion": db_version, "databaseDate": db_date,
                     "databaseAgeHours": age_hours}, findings)
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"zINSTREAM\0")
            with path.open("rb") as source:
                while chunk := source.read(65536):
                    sock.sendall(struct.pack("!I", len(chunk)))
                    sock.sendall(chunk)
            sock.sendall(struct.pack("!I", 0))
            response = sock.recv(MAX_OUTPUT).rstrip(b"\0\n").decode("utf-8", "replace")
        if response.endswith(" OK"):
            status = "CLEAN"
        elif response.endswith(" FOUND"):
            status = "FOUND"
            signature = response.rsplit(": ", 1)[-1].removesuffix(" FOUND")[:240]
            findings.append(finding("clamav", "MALWARE_DETECTED", "CRITICAL",
                                    "ClamAV matched a malware signature", signature=signature))
        else:
            status = "ERROR"
            findings.append(finding("clamav", "CLAMAV_SCAN_ERROR", "ERROR",
                                    "ClamAV did not return a typed scan verdict", response=response[:300]))
        return ({"engine": "clamav", "status": status, "version": parts[0][:120],
                 "databaseVersion": db_version, "databaseDate": db_date,
                 "databaseAgeHours": round(age_hours, 2), "response": response[:300]}, findings)
    except (OSError, TimeoutError) as error:
        findings.append(finding("clamav", "CLAMAV_UNAVAILABLE", "ERROR",
                                "ClamAV could not complete the mandatory scan", error=type(error).__name__))
        return ({"engine": "clamav", "status": "UNAVAILABLE", "version": "UNKNOWN"}, findings)


def structural(path: Path, rules: Path) -> tuple[list[dict], list[dict]]:
    engines: list[dict] = []
    findings: list[dict] = []
    qpdf = command("qpdf", ["qpdf", "--check", str(path)])
    qpdf["version"] = version(["qpdf", "--version"])
    engines.append(qpdf)
    if qpdf["status"] != "OK":
        findings.append(finding("qpdf", "PDF_STRUCTURE_INVALID", "ERROR",
                                "qpdf rejected the PDF structure", exitCode=qpdf["exitCode"]))

    encrypted = command("qpdf-encryption", ["qpdf", "--requires-password", str(path)])
    encrypted["version"] = qpdf["version"]
    # qpdf: 0 password required, 2 not encrypted, 3 invalid supplied password.
    encrypted["status"] = "ENCRYPTED" if encrypted["exitCode"] == 0 else "CLEAR" if encrypted["exitCode"] == 2 else "FAILED"
    engines.append(encrypted)
    if encrypted["status"] == "ENCRYPTED":
        findings.append(finding("qpdf", "PDF_ENCRYPTED", "HIGH", "PDF requires a password"))
    elif encrypted["status"] == "FAILED":
        findings.append(finding("qpdf", "ENCRYPTION_CHECK_FAILED", "ERROR", "Encryption state is unknown"))

    pdfid = command("pdfid", ["python3", "/opt/pdf-tools/pdfid.py", "--nozero", str(path)])
    pdfid["version"] = "PDFiD 0.2.10@7d625d0b05c4"
    engines.append(pdfid)
    parser = command("pdf-parser", ["python3", "/opt/pdf-tools/pdf-parser.py", "--stats", str(path)])
    parser["version"] = "pdf-parser 0.7.14@7d625d0b05c4"
    engines.append(parser)

    data = path.read_bytes()
    counts = {key: len(re.findall(rb"/" + re.escape(key.encode()) + rb"(?=[\s<>\[\]()/%]|$)", data, re.I)) for key in KEYWORDS}
    for key, count in counts.items():
        if count:
            severity = "HIGH" if key in ACTIVE else "MEDIUM"
            findings.append(finding("pdf-keywords", "PDF_KEYWORD_" + key.upper(), severity,
                                    "PDF contains a security-relevant name", keyword=key, count=count))
    engines.append({"engine": "pdf-keywords", "status": "OK", "version": "keywords-1", "counts": counts})

    yara = command("yara", ["yara", "--timeout=15", "--print-meta", str(rules), str(path)])
    yara["version"] = version(["yara", "--version"])
    engines.append(yara)
    if yara["status"] == "OK":
        for line in yara["output"].splitlines():
            if line and not line.startswith("  "):
                rule = line.split()[0][:160]
                findings.append(finding("yara", "YARA_MATCH", "HIGH", "YARA rule matched", rule=rule))

    for engine in engines:
        if engine["status"] in {"FAILED", "TIMEOUT", "UNAVAILABLE"}:
            findings.append(finding(engine["engine"], "ENGINE_FAILED", "ERROR",
                                    "A mandatory analysis engine failed", status=engine["status"], exitCode=engine.get("exitCode")))
    return engines, findings


def decide(engines: list[dict], findings: list[dict]) -> tuple[str, str]:
    if any(item["code"] == "MALWARE_DETECTED" for item in findings):
        return "MALICIOUS", "BLOCK"
    if any(item["severity"] == "ERROR" for item in findings):
        return "UNKNOWN", "REVIEW_REQUIRED"
    if any(item["code"] in {"PDF_ENCRYPTED", "PDF_KEYWORD_EMBEDDEDFILE"} for item in findings):
        return "SUSPICIOUS", "REVIEW_REQUIRED"
    if any(item["severity"] in {"HIGH", "CRITICAL"} for item in findings):
        return "SUSPICIOUS", "CDR_ELIGIBLE"
    if not engines or any(item["status"] not in {"OK", "CLEAN", "CLEAR"} for item in engines):
        return "UNKNOWN", "REVIEW_REQUIRED"
    return "CLEAN", "CDR_ELIGIBLE"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--clamd-host", default=os.getenv("PDF_GATEWAY_CLAMD_HOST", "clamav"))
    parser.add_argument("--clamd-port", type=int, default=int(os.getenv("PDF_GATEWAY_CLAMD_PORT", "3310")))
    parser.add_argument("--clamd-timeout", type=int, default=30)
    parser.add_argument("--max-definition-age-hours", type=int, default=48)
    parser.add_argument("--rules", type=Path, default=Path("/opt/pdf-gateway/rules/pdf-risk.yar"))
    args = parser.parse_args()
    path = args.path.resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 50 * 1024 * 1024:
        raise SystemExit("input must be a regular file no larger than 50 MiB")
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    clam_engine, clam_findings = clamav(path, args.clamd_host, args.clamd_port,
                                        args.clamd_timeout, args.max_definition_age_hours)
    other_engines, other_findings = structural(path, args.rules)
    engines = [clam_engine, *other_engines]
    findings = [*clam_findings, *other_findings]
    detection, policy = decide(engines, findings)
    report = {"schemaVersion": REPORT_VERSION, "policyVersion": POLICY_VERSION,
              "rulesetVersion": RULESET_VERSION, "sourceSha256": source_hash,
              "sourceBytes": path.stat().st_size, "detection": detection,
              "policyDecision": policy, "engines": engines, "findings": findings}
    json.dump(report, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0 if detection != "UNKNOWN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
