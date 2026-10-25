#!/usr/bin/env python3
"""Offline, bounded, typed PDF cryptographic-signature inspection."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import os
import resource
import sys
from datetime import datetime
from pathlib import Path

from pyhanko.keys import load_certs_from_pemder
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko_certvalidator import ValidationContext

SCHEMA_VERSION = "signature-report-1"
POLICY_VERSION = "signature-policy-1"
MAX_BYTES = 50 * 1024 * 1024

# Certificate subjects and parser internals belong in the restricted report,
# not container stderr. All outcomes are emitted as bounded typed JSON.
logging.disable(logging.CRITICAL)


def bounded() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))


def json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def cert_evidence(cert) -> dict:
    if cert is None:
        return {}
    return {
        "subject": cert.subject.human_friendly[:1000],
        "issuer": cert.issuer.human_friendly[:1000],
        "serialNumberHex": format(cert.serial_number, "x"),
        "sha256": hashlib.sha256(cert.dump()).hexdigest(),
        "notBefore": cert["tbs_certificate"]["validity"]["not_before"].native.isoformat(),
        "notAfter": cert["tbs_certificate"]["validity"]["not_after"].native.isoformat(),
    }


def inspect(path: Path, trust_roots: list[Path]) -> tuple[dict, int]:
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    roots = list(load_certs_from_pemder([str(item) for item in trust_roots])) if trust_roots else []
    context = ValidationContext(
        trust_roots=roots,
        allow_fetching=False,
        revocation_mode="soft-fail",
    )
    with path.open("rb") as source:
        reader = PdfFileReader(source, strict=True)
        # Include regular signatures and document timestamps. Ignoring the
        # latter would make the reported signature count and evidence incomplete.
        embedded = list(reader.embedded_signatures)
        signatures = []
        errors = []
        for index, signature in enumerate(embedded):
            try:
                status = validate_pdf_signature(signature, signer_validation_context=context)
                modification = status.modification_level
                entry = {
                    "index": index,
                    "fieldName": signature.field_name,
                    "objectType": str(signature.sig_object_type),
                    "integrity": "INTACT" if status.intact else "BROKEN",
                    "cryptographicValidity": "VALID" if status.valid else "INVALID",
                    "trust": "TRUSTED" if status.trusted else "UNTRUSTED",
                    "revocation": "REVOKED" if status.revoked else "NOT_PROVEN_REVOKED",
                    "coverage": json_value(status.coverage),
                    "modificationLevel": json_value(modification),
                    "docMdpPermitted": bool(status.docmdp_ok),
                    "digestAlgorithm": status.md_algorithm,
                    "signatureMechanism": status.pkcs7_signature_mechanism,
                    "signerReportedAt": json_value(status.signer_reported_dt),
                    "validationTime": json_value(status.validation_time),
                    "signerCertificate": cert_evidence(status.signing_cert),
                }
                signatures.append(entry)
            except Exception as error:  # malformed signature data must fail closed
                errors.append({"index": index, "code": "SIGNATURE_VALIDATION_ERROR", "errorType": type(error).__name__})

    if errors:
        state, decision, exit_code = "UNKNOWN", "REVIEW_REQUIRED", 2
    elif not signatures:
        state, decision, exit_code = "UNSIGNED", "CDR_REQUIRED", 0
    elif any(item["integrity"] != "INTACT" or item["cryptographicValidity"] != "VALID" for item in signatures):
        state, decision, exit_code = "SIGNED_INVALID", "REVIEW_REQUIRED", 0
    elif any(not item["docMdpPermitted"] or item["modificationLevel"] not in ("ModificationLevel.NONE", "NONE") for item in signatures):
        state, decision, exit_code = "SIGNED_MODIFIED", "REVIEW_REQUIRED", 0
    elif all(item["trust"] == "TRUSTED" for item in signatures):
        state, decision, exit_code = "SIGNED_VALID_TRUSTED", "CDR_REQUIRED_PRESERVE_ORIGINAL", 0
    else:
        state, decision, exit_code = "SIGNED_VALID_UNTRUSTED", "CDR_REQUIRED_PRESERVE_ORIGINAL", 0

    return ({
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "engine": {"name": "pyHanko", "version": importlib.metadata.version("pyhanko"), "networkFetching": False},
        "sourceSha256": source_hash,
        "sourceBytes": path.stat().st_size,
        "signatureCount": len(embedded),
        "state": state,
        "policyDecision": decision,
        "preserveOriginal": bool(embedded),
        "signatures": signatures,
        "errors": errors,
        "limitations": [
            "Visible or handwritten signature-like marks are not cryptographic signatures.",
            "NOT_PROVEN_REVOKED is not proof of good standing; network revocation fetching is disabled.",
            "A sanitized viewing copy is separate and unsigned.",
        ],
    }, exit_code)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--trust-root", action="append", type=Path, default=[])
    args = parser.parse_args()
    bounded()
    try:
        path = args.path.resolve(strict=True)
        if not path.is_file() or path.stat().st_size <= 0 or path.stat().st_size > MAX_BYTES:
            raise ValueError("input must be a regular file from 1 byte through 50 MiB")
        report, exit_code = inspect(path, args.trust_root)
    except Exception as error:
        report = {
            "schemaVersion": SCHEMA_VERSION,
            "policyVersion": POLICY_VERSION,
            "engine": {"name": "pyHanko", "version": importlib.metadata.version("pyhanko"), "networkFetching": False},
            "state": "UNKNOWN",
            "policyDecision": "REVIEW_REQUIRED",
            # A parse failure cannot prove that the source is unsigned. Retain
            # it under restricted policy until a reviewer resolves the error.
            "preserveOriginal": True,
            "signatureCount": 0,
            "signatures": [],
            "errors": [{"code": "DOCUMENT_PARSE_ERROR", "errorType": type(error).__name__}],
        }
        exit_code = 2
    json.dump(report, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
