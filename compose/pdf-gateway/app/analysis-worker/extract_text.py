#!/usr/bin/env python3
"""Bounded native-text extraction from a quarantined original.

The text is untrusted data used only for deterministic classification and the
archive's content.txt; it never influences security decisions. pdftotext runs
under the same rlimits as the other analysis engines, in the networkless,
read-only analysis container. Failure means "no text", not a scan failure.

Usage: extract_text.py SOURCE OUTPUT_TEXT  -> prints a JSON summary
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import scan

MAX_PAGES = 20
MAX_TEXT_BYTES = int(os.getenv("PDF_TEXT_MAX_BYTES", str(256 * 1024)))


def main() -> int:
    source, output = Path(sys.argv[1]), Path(sys.argv[2])
    version = scan.version(["pdftotext", "-v"])
    summary = {"method": "native_text", "source": "original", "engine": version[:80],
               "pages": 0, "characters": 0, "truncated": False, "status": "OK"}
    with tempfile.TemporaryDirectory(dir=os.getenv("TMPDIR", "/tmp")) as directory:
        raw = Path(directory) / "text.txt"
        try:
            # -layout keeps each label on the line of its value ("Date:   10-Jun-2026"),
            # which plain reading order splits apart in two-column documents.
            result = subprocess.run(["pdftotext", "-q", "-layout", "-enc", "UTF-8", "-l", str(MAX_PAGES), str(source), str(raw)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=30, check=False, preexec_fn=scan.bounded_preexec,
                                    env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/nonexistent"})
            ok = result.returncode == 0 and raw.is_file()
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
            summary["status"] = "NO_TEXT"
            text = ""
        else:
            data = raw.read_bytes()
            summary["truncated"] = len(data) > MAX_TEXT_BYTES
            text = data[:MAX_TEXT_BYTES].decode("utf-8", "ignore")
            # Keep printable text and line structure; drop control and bidi characters.
            text = "".join(ch for ch in text if ch in "\n\t\f" or (ch.isprintable() and ch not in "‪‫‬‭‮⁦⁧⁨⁩"))
            summary["pages"] = min(text.count("\f") or (1 if text.strip() else 0), MAX_PAGES)
            # Layout padding: keep column gaps visible but short, and drop trailing spaces.
            text = "\n".join(re.sub(r" {4,}", "   ", line).rstrip() for line in text.split("\n"))
            text = text.replace("\f", "\n\f\n").strip()
            # The bound applies to what is stored, after formatting.
            text = text.encode("utf-8")[:MAX_TEXT_BYTES].decode("utf-8", "ignore")
            if not text:
                summary["status"] = "NO_TEXT"
    summary["characters"] = len(text)
    if text:
        with output.open("x", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o640)
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    json.dump(summary, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
