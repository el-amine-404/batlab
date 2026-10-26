"""Allowlisted embedded metadata for new, unsigned, already-sanitized PDFs.

Writes only Title, Author, Subject and Keywords into the
document information dictionary of a *copy* of a validated CDR output, saved
linearized ("fast web view"; the page images are copied, not re-encoded). No
Producer, Creator, dates or tool versions are written. Any existing
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

from organize import plain_punctuation

WRITER_VERSION = "metadata-writer-3"
TYPE_LABEL = {"invoice": "Invoice", "receipt": "Receipt", "bank_statement": "Bank statement", "payslip": "Payslip",
              "contract": "Contract", "tax_document": "Tax document", "insurance": "Insurance", "medical": "Medical",
              "identity_document": "Identity document", "certificate": "Certificate", "letter": "Letter",
              "school_document": "School document", "presentation": "Presentation", "report": "Report",
              "manual": "Manual", "other": "Document"}
OUTPUT_GROUP = int(os.getenv("PDF_GATEWAY_OUTPUT_GROUP", "10010"))
CONTROL = re.compile(r"[\x00-\x1f\x7f  ‪-‮⁦-⁩﻿]")


def clean(value: str, limit: int) -> str:
    return plain_punctuation(re.sub(r"\s+", " ", CONTROL.sub(" ", value)))[:limit]


def fields_for(sidecar_fields: dict) -> dict[str, str]:
    """The allowlisted embedded values derived from the document's organisation.

    Author is the correspondent that issued the document (as PDF readers and
    Paperless use it), and only when its value was checked against the text.
    Untrusted source metadata is never copied."""
    values = {"/Title": clean(sidecar_fields["title"], 200)}
    issuer = (sidecar_fields.get("issuer") or {}).get("value")
    if issuer:
        values["/Author"] = clean(issuer, 120)
    kind = sidecar_fields.get("documentType")
    subject = [TYPE_LABEL.get(kind, kind.replace("_", " ").capitalize()) if kind else None, issuer,
               (sidecar_fields.get("documentDate") or {}).get("value"),
               f"no. {sidecar_fields['reference']['value']}" if sidecar_fields.get("reference") else None]
    if any(subject):
        values["/Subject"] = clean(" - ".join(part for part in subject if part), 200)
    if sidecar_fields.get("tags"):
        values["/Keywords"] = clean(", ".join(sidecar_fields["tags"]), 500)
    # No Producer or Creator: they would name the software that made the file.
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
        # Linearized for fast web view; streams keep their encoding (preserve), so the
        # page images are byte-for-byte the validated ones.
        pdf.save(temporary, linearize=True, object_stream_mode=pikepdf.ObjectStreamMode.disable,
                 compress_streams=True, stream_decode_level=pikepdf.StreamDecodeLevel.none,
                 fix_metadata_version=False, deterministic_id=True)
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
