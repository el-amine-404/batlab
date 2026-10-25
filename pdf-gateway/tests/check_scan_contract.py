#!/usr/bin/env python3
"""Check a live GET /scans/{id} response against the OpenAPI Scan schema.

Usage: check_scan_contract.py OPENAPI_YAML < response.json
Reads the Scan schema's one-line property definitions (the contract's style)
without a YAML dependency: exact key set, enum membership and patterns.
"""

import json
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text()
block = re.search(r"^    Scan:\n(.*?)(?=^    \S)", text, re.S | re.M).group(1)
required = set(re.search(r"required: \[([^\]]*)\]", block).group(1).replace(" ", "").split(","))
properties = dict(re.findall(r"^        (\w+): (\{.*\})$", block, re.M))
response = json.load(sys.stdin)
errors = []
if set(response) != set(properties) or not required <= set(response):
    errors.append(f"keys {sorted(response)} != schema {sorted(properties)}")
for key, definition in properties.items():
    value = response.get(key)
    enum = re.search(r"enum: \[([^\]]*)\]", definition)
    pattern = re.search(r"pattern: '([^']*)'", definition)
    if enum and value not in [item.strip() for item in enum.group(1).split(",")]:
        errors.append(f"{key}={value!r} not in enum")
    if pattern and not (isinstance(value, str) and re.fullmatch(pattern.group(1).strip("^$"), value)):
        errors.append(f"{key}={value!r} does not match {pattern.group(1)}")
if errors:
    raise SystemExit("; ".join(errors))
print(json.dumps({key: response[key] for key in ("status", "stage", "detection", "policy", "artifact", "originalRetention")}))
