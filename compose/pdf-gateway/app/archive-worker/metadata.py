"""Allowlisted embedded metadata for new, unsigned, already-sanitized PDFs.

Writes only Title, Subject, Keywords and a fixed Producer into the document
information dictionary of a *copy* of a validated CDR output. Any existing
info dictionary, XMP stream or catalog-level /Info is removed, and the file is
rewritten in full (no incremental update), so no earlier metadata survives in
an older revision. The result is a new artifact that must pass final
validation again before it can be published. Signed originals are never input.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pikepdf

WRITER_VERSION = "metadata-writer-1"
PRODUCER = "Batlab PDF Gateway (metadata-writer-1)"
OUTPUT_GROUP = int(os.getenv("PDF_GATEWAY_OUTPUT_GROUP", "10010"))
CONTROL = re.compile(r"[\x00-\x1f\x7f  ‪-‮⁦-⁩﻿]")


def clean(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", CONTROL.sub(" ", value)).strip()[:limit]


def fields_for(sidecar_fields: dict) -> dict[str, str]:
    """The allowlisted embedded values derived from organisation fields.

    Author is never written automatically: issuer is not author, and an author
    claim would need verification. Untrusted source metadata is never copied."""
    values = {"/Title": clean(sidecar_fields["title"], 200)}
    if sidecar_fields.get("documentType"):
        values["/Subject"] = clean(sidecar_fields["documentType"].replace("_", " "), 100)
    if sidecar_fields.get("tags"):
        values["/Keywords"] = clean(", ".join(sidecar_fields["tags"]), 500)
    values["/Producer"] = PRODUCER
    return values


def write(source: Path, target: Path, values: dict[str, str]) -> None:
    """Write a new PDF at target (mode 0640) with exactly the given metadata."""
    with pikepdf.open(source) as pdf:
        if "/Metadata" in pdf.Root:
            del pdf.Root["/Metadata"]
        if "/Info" in pdf.Root:  # MuPDF output carries a non-standard catalog /Info
            del pdf.Root["/Info"]
        info = pdf.make_indirect(pikepdf.Dictionary({key: pikepdf.String(value) for key, value in values.items()}))
        pdf.trailer["/Info"] = info
        temporary = target.with_name(f".{target.name}.partial")
        pdf.save(temporary, linearize=False, object_stream_mode=pikepdf.ObjectStreamMode.disable,
                 compress_streams=True, fix_metadata_version=False, deterministic_id=True)
    with temporary.open("rb+") as handle:
        # Readable only by the validation stage, like CDR output (group 10010).
        os.fchown(handle.fileno(), -1, OUTPUT_GROUP)
        os.fchmod(handle.fileno(), 0o640)
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read(path: Path) -> dict[str, str]:
    with pikepdf.open(path) as pdf:
        return {str(key): str(value) for key, value in pdf.docinfo.items()}
