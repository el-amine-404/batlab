#!/usr/bin/env python3
"""Dependency-free M0 contract sanity checks; full schema tests arrive in M1."""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


for name in ("archive-metadata.schema.json", "taxonomy.schema.json", "rules.schema.json"):
    with (CONTRACTS / name).open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if value.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        fail(f"{name}: not JSON Schema 2020-12")
    if value.get("additionalProperties") is not False:
        fail(f"{name}: root must reject unknown properties")

openapi = (CONTRACTS / "openapi.yaml").read_text(encoding="utf-8")
if not openapi.startswith("openapi: 3.1.0\n"):
    fail("OpenAPI contract is not version 3.1.0")

required = {
    "/scans", "/scans/{scanId}", "/scans/{scanId}/findings",
    "/scans/{scanId}/report", "/scans/{scanId}/events",
    "/scans/{scanId}/rescan", "/scans/{scanId}/cancel",
    "/artifacts/{artifactId}/download", "/archive",
    "/archive/{documentId}", "/archive/{documentId}/metadata",
    "/archive/{documentId}/reissue", "/archive/{documentId}/file",
    "/archive/{documentId}/sidecar", "/archive/verify", "/archive/reindex",
    "/scans/{scanId}/paperless-import", "/dashboard/summary",
    "/dashboard/timeseries", "/quarantine", "/quarantine/{id}",
    "/system/health", "/system/scanners", "/system/workers", "/settings",
}
found = set(re.findall(r"^  (/[^:]+):$", openapi, flags=re.MULTILINE))
missing = sorted(required - found)
if missing:
    fail("missing OpenAPI paths: " + ", ".join(missing))

for forbidden in ("safe: true", "confidencePercentage", "docker.sock"):
    if forbidden in openapi:
        fail(f"forbidden contract text: {forbidden}")

print(f"validated 3 strict JSON Schemas and {len(required)} required OpenAPI paths")
