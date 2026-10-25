#!/usr/bin/env python3
"""M4 slice 1: real-filesystem tests of the portable archive library and CLI.

Runs inside the archive-worker image: python /tests/archive_test.py /data
"""

import hashlib
import io
import json
import os
import random
import signal
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, "/opt/pdf-gateway")
from archive import Archive, ArchiveError, SIDECAR, document_paths, safe_component  # noqa: E402

DATA = Path(sys.argv[1])
CLI = ["python", "/opt/pdf-gateway/pdf_archive.py"]
counter = 0


def doc_id() -> str:
    global counter
    counter += 1
    return "doc-01J" + format(counter, "023d")


def pdf_bytes(label: str, padding: int = 0) -> bytes:
    body = f"BT /F1 12 Tf 20 20 Td ({label}) Tj ET".encode()
    return (b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n% " + body + b"\n"
            + b"%" + os.urandom(padding // 2).hex().encode()[:padding] + b"\n%%EOF\n")


def sidecar(document_id: str, title: str = "Internet Invoice", issuer: str = "Example Telecom",
            date: str | None = "2026-10-15", category: str = "Finance", tags=("finance", "internet")) -> dict:
    value = {
        "documentId": document_id, "artifactVersion": 1, "metadataRevision": 1, "title": title,
        "organization": {"category": category, "reviewState": "RULE", "folder": "Invoices", "reason": "test rule"},
        "tags": list(tags), "derivedFrom": {"scanId": "scan-01J00000000000000000000050", "kind": "CDR_RECONSTRUCTION"},
        "approval": {"policyVersion": "final-policy-1", "decision": "ALLOW_SANITIZED", "finalReportId": "validation-x-1"},
        "signature": {"originalSigned": False, "viewingCopySigned": False}, "integrations": {},
        "createdAt": "2026-10-25T12:00:00Z", "updatedAt": "2026-10-25T12:00:00Z",
    }
    if issuer:
        value["issuer"] = {"value": issuer, "source": "rule", "reviewState": "UNVERIFIED"}
    if date:
        value["documentDate"] = {"value": date, "source": "rule", "reviewState": "UNVERIFIED"}
    return value


def put(name: str, data: bytes) -> tuple[Path, str]:
    path = DATA / "inputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def refused(code: str, function, *args) -> None:
    try:
        function(*args)
    except ArchiveError as error:
        assert error.code == code, (error.code, code, error.detail)
        return
    raise AssertionError(f"expected refusal {code}")


def visible(archive: Archive) -> list[Path]:
    return list(archive.document_dirs())


def check(name: str, condition: bool, detail="") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    print(f"ok - {name}")


archive = Archive(DATA / "root")
archive.initialise()

# 1. Basic publish: canonical location, permissions, manifest, verify.
pdf, digest = put("a.pdf", pdf_bytes("invoice A"))
first = doc_id()
published = archive.publish(pdf, digest, sidecar(first), text="Internet invoice October total 29.99")
expected = f"Finance/2026/Invoices/2026-10-15 - Example Telecom - Internet Invoice [{first}]"
check("publish location", published.relative_to(archive.archive).as_posix() == expected, published)
check("pdf named for humans", (published / "2026-10-15 - Example Telecom - Internet Invoice.pdf").is_file())
check("shared read-only permissions", oct(published.stat().st_mode & 0o777) == "0o755"
      and all(oct(p.stat().st_mode & 0o777) == "0o644" for p in published.iterdir()))
check("staging left empty", not list(archive.staging.iterdir()))
manifest = [json.loads(line) for line in (archive.manifests / "archive-manifest.jsonl").read_text().splitlines()]
check("manifest records publish", manifest[-1]["documentId"] == first and manifest[-1]["sha256"] == digest)
check("verify clean", archive.verify()["ok"])

# 2. Hostile names never escape or produce non-portable paths.
hostile = [("../../../etc/passwd", "..\\..\\evil"), ("CON", "NUL.txt"), ("a/b\\c:d*e?f\"g<h>i|j", "x\x00y\x1fz"),
           ("\u202eexe.fdp", "\u200bzero\ufeffwidth"), ("é" * 190, "日本語" * 60), ("   ...   ", "")]
for title, issuer in hostile:
    item = sidecar(doc_id(), title=title or "T", issuer=issuer)
    path = archive.publish(*put(f"h{counter}.pdf", pdf_bytes(title)), item)
    relative = path.relative_to(archive.archive).as_posix()
    parts = relative.split("/")
    check(f"hostile name contained: {title[:12]!r}",
          path.resolve().is_relative_to(archive.archive.resolve()) and len(parts) == 4 and ".." not in parts
          and all(part and part.strip(" .") == part and len(part.encode()) <= 160 for part in parts)
          and not any(c in relative for c in '\\:*?"<>|\x00\u202e\ufeff')
          and parts[-1].split(".")[0].upper() not in {"CON", "NUL"}, relative)
check("reserved device name suffixed", safe_component("CON", "x") == "CON_" and safe_component("nul.txt", "x") == "nul.txt_")

# 3. Collisions: same human name gets distinct directories; same ID is refused.
twin_a, twin_b = doc_id(), doc_id()
path_a = archive.publish(*put("t1.pdf", pdf_bytes("twin 1")), sidecar(twin_a))
path_b = archive.publish(*put("t2.pdf", pdf_bytes("twin 2")), sidecar(twin_b))
check("identical names do not overwrite", path_a != path_b and path_a.exists() and path_b.exists())
refused("DOCUMENT_EXISTS", archive.publish, *put("t3.pdf", pdf_bytes("twin 3")), sidecar(twin_a))

# 4/5. Refusals leave nothing visible and nothing staged.
before = len(visible(archive))
bad_pdf, bad_digest = put("bad.pdf", pdf_bytes("bad"))
refused("SOURCE_HASH_MISMATCH", archive.publish, bad_pdf, "0" * 64, sidecar(doc_id()))
broken = sidecar(doc_id())
del broken["approval"]
refused("SIDECAR_INVALID", archive.publish, bad_pdf, bad_digest, broken)
signed_copy = sidecar(doc_id())
signed_copy["signature"]["viewingCopySigned"] = True
refused("SIDECAR_INVALID", archive.publish, bad_pdf, bad_digest, signed_copy)
check("refusals publish nothing", len(visible(archive)) == before and not list(archive.staging.iterdir()))

# Undated / unclassified fallback goes to Unsorted with a stable name.
fallback = doc_id()
path = archive.publish(*put("u.pdf", pdf_bytes("scan")), sidecar(fallback, title="Scanned document", issuer=None,
                                                                   date=None, category="Unsorted", tags=()))
check("fallback location", path.relative_to(archive.archive).as_posix()
      == f"Unsorted/2026/undated - Unclassified - Scanned document [{fallback}]", path)

# 8. Sidecar compare-and-swap edits never touch the PDF.
pdf_hash = hashlib.sha256((published / "2026-10-15 - Example Telecom - Internet Invoice.pdf").read_bytes()).hexdigest()
updated = archive.update_sidecar(first, 1, {"tags": ["finance", "internet", "paid"]})
check("sidecar revision advanced", updated["metadataRevision"] == 2)
refused("REVISION_CONFLICT", archive.update_sidecar, first, 1, {"tags": ["stale"]})
refused("IMMUTABLE_FIELD", archive.update_sidecar, first, 2, {"integrity": {"sha256": "0" * 64, "bytes": 1}})
refused("SIDECAR_INVALID", archive.update_sidecar, first, 2, {"tags": ["Not A Valid Tag!"]})
check("edit left PDF bytes unchanged", hashlib.sha256(
    (published / "2026-10-15 - Example Telecom - Internet Invoice.pdf").read_bytes()).hexdigest() == pdf_hash == digest)
check("verify clean after edits", archive.verify()["ok"])

# 10. Reindex from files alone (no database anywhere in this container).
result = archive.reindex(DATA / "catalog.sqlite")
db = sqlite3.connect(DATA / "catalog.sqlite")
check("reindex counts", result["indexed"] == len(visible(archive)) and result["rejected"] == 0, result)
check("reindex tags", {row[0] for row in db.execute("SELECT tag FROM tags WHERE document_id=?", (first,))}
      == {"finance", "internet", "paid"})
check("reindex content", "29.99" in db.execute("SELECT text FROM content WHERE document_id=?", (first,)).fetchone()[0])
check("reindex revision", db.execute("SELECT metadata_revision FROM documents WHERE document_id=?", (first,)).fetchone()[0] == 2)
db.close()

# 11. Export, import into a fresh archive, idempotent re-import, hostile bundles.
bundle = DATA / "export.tar"
exported = archive.export(bundle)
fresh = Archive(DATA / "fresh")
imported = fresh.import_bundle(bundle)
check("import publishes every document", len(imported["imported"]) == exported["documents"] == len(visible(fresh)))
check("imported archive verifies", fresh.verify()["ok"])
check("re-import is idempotent", fresh.import_bundle(bundle)["skippedIdentical"] == sorted(imported["imported"]))
original_hashes = {json.loads((d / SIDECAR).read_text())["integrity"]["sha256"] for d in visible(archive)}
check("imported bytes identical", original_hashes == {json.loads((d / SIDECAR).read_text())["integrity"]["sha256"] for d in visible(fresh)})


def rewrite_bundle(target: Path, mutate) -> None:
    with tarfile.open(bundle) as source, tarfile.open(target, "w") as out:
        for member in source.getmembers():
            data = source.extractfile(member).read()
            member, data = mutate(member, data)
            if member is not None:
                member.size = len(data)
                out.addfile(member, io.BytesIO(data))


def flip_pdf(member, data):
    return member, (data[:-8] + b"TAMPERED" if member.name.endswith(".pdf") else data)


def add_traversal(member, data):
    if member.name == "export-manifest.json":
        member.name = "../../escape.json"
    return member, data


for name, mutate, code in [("tampered", flip_pdf, "BUNDLE_HASH_MISMATCH"), ("traversal", add_traversal, "BUNDLE_UNSAFE_MEMBER")]:
    hostile_bundle = DATA / f"{name}.tar"
    rewrite_bundle(hostile_bundle, mutate)
    target = Archive(DATA / f"target-{name}")
    refused(code, target.import_bundle, hostile_bundle)
    check(f"{name} bundle imports nothing", not visible(target) and not list(target.staging.iterdir()))

# 6/7. Tampering and stray files are detected, reported and excluded from the catalog.
victim = path_a / next(p.name for p in path_a.iterdir() if p.suffix == ".pdf")
victim.write_bytes(victim.read_bytes()[:-3] + b"XX\n")
(path_b / "dropped.exe").write_bytes(b"MZ")
report = archive.verify()
rel_a, rel_b = (p.relative_to(archive.archive).as_posix() for p in (path_a, path_b))
check("tamper detected", "PDF_HASH_MISMATCH" in report["failures"].get(rel_a, []), report["failures"])
check("stray file detected", any(p.startswith("UNEXPECTED_ENTRY") for p in report["failures"].get(rel_b, [])))
check("tampered excluded from catalog", archive.reindex(DATA / "catalog2.sqlite")["rejected"] == 2)
refused("ARCHIVE_NOT_CLEAN", archive.export, DATA / "refused.tar")

# 9. Real crash loop: SIGKILL a publishing process at random points.
crash = Archive(DATA / "crash")
crash.initialise()
big, big_digest = put("big.pdf", pdf_bytes("large", padding=24 * 1024 * 1024))
attempted, random_state = [], random.Random(20261025)
# Calibrate: an uninterrupted publish (process start to exit) sets the kill window.
calibration = DATA / "inputs" / "crash-calibration.json"
calibration.write_text(json.dumps(sidecar(doc_id(), title="Crash calibration")))
started = time.monotonic()
subprocess.run([*CLI, "--root", str(crash.root), "publish", "--pdf", str(big), "--sha256", big_digest,
                "--sidecar", str(calibration)], check=True, stdout=subprocess.DEVNULL)
duration = time.monotonic() - started
for round_number in range(25):
    item = sidecar(doc_id(), title=f"Crash round {round_number}")
    spec = DATA / "inputs" / f"crash-{round_number}.json"
    spec.write_text(json.dumps(item))
    process = subprocess.Popen([*CLI, "--root", str(crash.root), "publish", "--pdf", str(big), "--sha256", big_digest,
                                "--sidecar", str(spec)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(random_state.uniform(0.0, duration * 1.3))
    process.send_signal(signal.SIGKILL)
    process.wait()
    attempted.append(item["documentId"])
    state = crash.verify()
    assert state["ok"], (round_number, state["failures"])
outcomes = {"published": len(visible(crash)), "stagingLeftovers": len(list(crash.staging.iterdir()))}
check("crash loop: every visible document is complete and valid", crash.verify()["ok"], outcomes)
outcomes["publishSeconds"] = round(duration, 2)
# The calibration publish is one of the visible documents.
check("crash loop hit both sides of the rename", 1 < outcomes["published"] < 26 and outcomes["stagingLeftovers"] > 0, outcomes)
repaired = crash.repair(apply=True, grace_seconds=0)
check("repair removes only invisible leftovers", not list(crash.staging.iterdir())
      and len(visible(crash)) == outcomes["published"], repaired)
recorded = {json.loads(line)["documentId"] for line in (crash.manifests / "archive-manifest.jsonl").read_text().splitlines()
            if line.strip().endswith("}")}
check("manifest reconciled with published documents",
      {json.loads((d / SIDECAR).read_text())["documentId"] for d in visible(crash)} <= recorded)
check("repair is idempotent", not crash.repair(apply=True, grace_seconds=0)["manifestMissing"])
print(json.dumps({"crashLoop": outcomes, "repairedManifest": len(repaired["manifestMissing"]),
                  "removedStaging": len(repaired["staleStaging"])}))

# Crash after the rename but before the manifest append (too narrow to hit by
# random kill): drop the last PUBLISHED line and a torn partial line, then repair.
manifest_path = crash.manifests / "archive-manifest.jsonl"
lines = manifest_path.read_text().splitlines()
lost = json.loads(lines[-1])["documentId"]
manifest_path.write_text("\n".join(lines[:-1]) + '\n{"event": "PUBLISHED", "documentId": "doc-01J0')
dry = crash.repair(apply=False, grace_seconds=0)
check("repair dry-run reports the unrecorded document and changes nothing",
      dry["manifestMissing"] == [lost] and lost not in manifest_path.read_text())
applied = crash.repair(apply=True, grace_seconds=0)
check("repair re-records it, tolerating the torn line", applied["manifestMissing"] == [lost]
      and not crash.repair(apply=False, grace_seconds=0)["manifestMissing"] and crash.verify()["ok"])

# Re-filing (e.g. after OCR): move out of Unsorted, rename the PDF, never change its bytes.
refile = Archive(DATA / "refile")
scan_pdf, scan_digest = put("scan.pdf", pdf_bytes("scan"))
unsorted = doc_id()
moved_from = refile.publish(scan_pdf, scan_digest, sidecar(unsorted, title="Unclassified document", issuer=None,
                                                          date=None, category="Unsorted", tags=()))
organised = {"title": "Internet Invoice - October 2026", "titleSource": "rule",
             "organization": {"category": "Finance", "reviewState": "RULE", "folder": "Invoices", "reason": "rule after OCR"},
             "documentType": "invoice", "tags": ["finance", "invoice"],
             "issuer": {"value": "Example Telecom", "source": "rule", "reviewState": "UNVERIFIED"},
             "documentDate": {"value": "2026-10-15", "source": "ocr", "reviewState": "UNVERIFIED"}}
ocr_meta = {"method": "ocr", "source": "viewing_copy", "engine": "tesseract 5", "pages": 1, "characters": 30, "truncated": False}
result = refile.reorganise(unsorted, 1, organised, text="Example Telecom invoice 29.99", text_extraction=ocr_meta)
moved_to = refile.find(unsorted)
check("re-filed to the rule's folder", moved_to.relative_to(refile.archive).as_posix().startswith("Finance/2026/Invoices/2026-10-15 - Example Telecom")
      and not moved_from.exists() and result["metadataRevision"] == 2, moved_to)
check("re-filed PDF bytes unchanged", hashlib.sha256((moved_to / result["relativePdfPath"]).read_bytes()).hexdigest() == scan_digest)
check("OCR text and provenance recorded", (moved_to / "content.txt").read_text().startswith("Example Telecom")
      and result["textExtraction"]["method"] == "ocr" and refile.verify()["ok"])
refused("REVISION_CONFLICT", refile.reorganise, unsorted, 1, organised)
refused("FIELD_NOT_REORGANISABLE", refile.reorganise, unsorted, 2, {"integrity": {}})

def interrupted(stage: int) -> None:
    """Re-file a fresh Unsorted document, but stop after step 1 or 2 (simulated crash)."""
    victim = doc_id()
    source = refile.publish(*put(f"crash{stage}.pdf", pdf_bytes(f"crash {stage}")), sidecar(victim, title="Unclassified document",
                            issuer=None, date=None, category="Unsorted", tags=()))
    current = json.loads((source / SIDECAR).read_text())
    target = {**current, **organised, "metadataRevision": 2, "title": f"Crash {stage}"}
    path, name = document_paths(target)
    target.update(archivePath=path, relativePdfPath=name)
    (source / SIDECAR).write_text(json.dumps(target, indent=2, sort_keys=True) + "\n")   # step 1 done
    if stage == 2:
        old_pdf = next(entry for entry in source.iterdir() if entry.suffix == ".pdf")
        old_pdf.rename(source / name)                                                      # step 2 done
    check(f"crash after step {stage}: verify reports it", not refile.verify()["ok"])
    check(f"crash after step {stage}: repair dry-run lists it, changes nothing",
          refile.repair(apply=False, grace_seconds=0)["movesCompleted"] == [victim] and source.exists())
    refile.repair(apply=True, grace_seconds=0)
    check(f"crash after step {stage}: repair completes the move", refile.verify()["ok"] and not source.exists()
          and refile.find(victim).relative_to(refile.archive).as_posix() == path)

interrupted(1)
interrupted(2)

# CLI surface: verify exit codes on clean and damaged archives.
clean = subprocess.run([*CLI, "--root", str(fresh.root), "verify"], capture_output=True, text=True)
damaged = subprocess.run([*CLI, "--root", str(archive.root), "verify"], capture_output=True, text=True)
check("cli verify exit codes", clean.returncode == 0 and damaged.returncode == 1 and json.loads(damaged.stdout)["ok"] is False)
print("M4 archive library tests passed")
