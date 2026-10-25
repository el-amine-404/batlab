"""Portable document archive: crash-safe publication, verification and recovery.

The archive directory tree is the source of truth. Each document directory holds
the approved PDF, an authoritative metadata.json sidecar and optional content.txt.
PostgreSQL, the manifest and any catalog are rebuildable from sidecars alone.

Layout under ROOT (all on one filesystem):
  archive/<archivePath>/{<file>.pdf, metadata.json[, content.txt]}
  staging/        private, invisible to readers; atomically renamed into archive/
  manifests/      archive-manifest.jsonl (append-only event log), taxonomy.json
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tarfile
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

SIDECAR = "metadata.json"
TEXT = "content.txt"
SCHEMA_VERSION = "1.0.0"
MANIFEST = "archive-manifest.jsonl"
EXPORT_MANIFEST = "export-manifest.json"
EXPORT_FORMAT = "pdf-archive-export-1"
MAX_COMPONENT_BYTES = 80
MAX_ARCHIVE_PATH = 200
MAX_TEXT_BYTES = 2 * 1024 * 1024
STAGING_GRACE_SECONDS = 3600
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*  ‪-‮⁦-⁩﻿]')
DOCUMENT_ID = re.compile(r"^doc-[0-9A-HJKMNP-TV-Z]{26}$")
SCHEMA_PATH = Path(os.getenv("PDF_ARCHIVE_SCHEMA", Path(__file__).with_name("archive-metadata.schema.json")))


class ArchiveError(Exception):
    """A typed refusal. Nothing partially published is visible when raised."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------- paths

def safe_component(text: str | None, fallback: str, max_bytes: int = MAX_COMPONENT_BYTES) -> str:
    """Render untrusted text as one portable path component (Linux/Windows/macOS)."""
    value = unicodedata.normalize("NFC", text or "")
    value = UNSAFE.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    while len(value.encode("utf-8")) > max_bytes:
        value = value[:-1].rstrip(" .")
    if not value or value in (".", ".."):
        value = fallback
    if value.split(".")[0].upper() in RESERVED:
        value = f"{value}_"
    return value


def document_paths(sidecar: dict) -> tuple[str, str]:
    """Return (archivePath, pdf filename) from validated organisation fields."""
    org = sidecar["organization"]
    category = org["category"]
    date = (sidecar.get("documentDate") or {}).get("value")
    issuer = (sidecar.get("issuer") or {}).get("value")
    doc_id = sidecar["documentId"]
    folder_type = safe_component(org.get("folder") or "", "General", 40)
    if category == "Unsorted" or not date:
        year = (date or sidecar["createdAt"])[:4]
        folder = f"Unsorted/{year}"
        stem = f"{date or 'undated'} - {safe_component(issuer, 'Unclassified', 40)} - {safe_component(sidecar['title'], doc_id, 60)}"
    else:
        folder = f"{category}/{date[:4]}/{folder_type}"
        stem = f"{date} - {safe_component(issuer, 'Unknown issuer', 40)} - {safe_component(sidecar['title'], 'Document', 60)}"
    stem = safe_component(stem, doc_id, 120)
    archive_path = f"{folder}/{stem} [{doc_id}]"
    if len(archive_path) > MAX_ARCHIVE_PATH:
        archive_path = f"{folder}/{safe_component(stem, doc_id, MAX_ARCHIVE_PATH - len(folder) - 40)} [{doc_id}]"
    return archive_path, f"{stem}.pdf"


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- integrity helpers

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_file(path: Path, data: bytes, mode: int = 0o644) -> None:
    with path.open("xb") as handle:
        os.fchmod(handle.fileno(), mode)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_validator = None


def validator() -> jsonschema.protocols.Validator:
    global _validator
    if _validator is None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls = jsonschema.validators.validator_for(schema)
        cls.check_schema(schema)
        _validator = cls(schema, format_checker=cls.FORMAT_CHECKER)
    return _validator


def schema_errors(sidecar: dict) -> list[str]:
    return sorted(f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"[:300]
                  for error in validator().iter_errors(sidecar))


# ---------------------------------------------------------------- archive root

@dataclass
class Archive:
    root: Path
    _lock_handle: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.archive = self.root / "archive"
        self.staging = self.root / "staging"
        self.manifests = self.root / "manifests"

    def initialise(self) -> None:
        self.archive.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.staging.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.manifests.mkdir(mode=0o755, parents=True, exist_ok=True)
        if stat.S_IMODE(os.stat(self.staging).st_mode) != 0o700:
            os.chmod(self.staging, 0o700)
        if os.stat(self.staging).st_dev != os.stat(self.archive).st_dev:
            raise ArchiveError("STAGING_NOT_SAME_FILESYSTEM", "atomic rename requires one filesystem")

    @contextmanager
    def lock(self):
        """Single writer for publish/edit/repair; readers never need the lock."""
        self.initialise()
        handle = (self.manifests / ".writer.lock").open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()

    def _append_manifest(self, event: dict) -> None:
        line = json.dumps({"at": now(), **event}, sort_keys=True, ensure_ascii=False) + "\n"
        path = self.manifests / MANIFEST
        with path.open("a+b") as handle:
            # A crash can leave a torn last line; never glue a record onto it.
            if handle.tell() > 0:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    line = "\n" + line
            handle.write(line.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

    def document_dirs(self):
        for sidecar in sorted(self.archive.rglob(SIDECAR)):
            if sidecar.is_file() and not sidecar.is_symlink():
                yield sidecar.parent

    def find(self, document_id: str) -> Path | None:
        if not DOCUMENT_ID.match(document_id):
            raise ArchiveError("INVALID_DOCUMENT_ID")
        # Literal suffix match: as a glob pattern "[...]" would be a character class.
        suffix = f" [{document_id}]"
        matches = [path for path in self.document_dirs() if path.name.endswith(suffix)]
        if len(matches) > 1:
            raise ArchiveError("DUPLICATE_DOCUMENT_ID", document_id)
        return matches[0] if matches else None

    # ------------------------------------------------------------ publish

    def publish(self, pdf: Path, expected_sha256: str, sidecar: dict, text: str | None = None) -> Path:
        """Publish one approved PDF + sidecar. Returns the published directory.

        The caller supplies organisation and provenance fields; this method fills
        paths and integrity, validates everything, and makes the document visible
        only through a single atomic directory rename. It never overwrites.
        """
        sidecar = json.loads(json.dumps(sidecar))
        archive_path, filename = document_paths(sidecar)
        sidecar.update(schemaVersion=SCHEMA_VERSION, archivePath=archive_path, relativePdfPath=filename)
        sidecar.pop("relativeTextPath", None)
        if text is not None:
            encoded = text.encode("utf-8")[:MAX_TEXT_BYTES]
            text = encoded.decode("utf-8", "ignore")
            sidecar["relativeTextPath"] = TEXT
        with self.lock():
            if self.find(sidecar["documentId"]) is not None:
                raise ArchiveError("DOCUMENT_EXISTS", sidecar["documentId"])
            target = self.archive / archive_path
            if target.exists() or not _inside(self.archive, target):
                raise ArchiveError("PATH_COLLISION", archive_path)
            stage = self.staging / f"{sidecar['documentId']}.{secrets.token_hex(6)}"
            stage.mkdir(mode=0o700)
            try:
                digest = hashlib.sha256()
                size = 0
                with pdf.open("rb") as source, (stage / filename).open("xb") as handle:
                    os.fchmod(handle.fileno(), 0o644)
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                if digest.hexdigest() != expected_sha256:
                    raise ArchiveError("SOURCE_HASH_MISMATCH", "PDF bytes are not the validated artifact")
                sidecar["integrity"] = {"sha256": expected_sha256, "bytes": size}
                errors = schema_errors(sidecar)
                if errors:
                    raise ArchiveError("SIDECAR_INVALID", "; ".join(errors)[:500])
                if text is not None:
                    write_file(stage / TEXT, text.encode("utf-8"))
                write_file(stage / SIDECAR, canonical_json(sidecar))
                problems = self.check_document(stage, sidecar_location=archive_path)
                if problems:
                    raise ArchiveError("STAGED_DOCUMENT_INVALID", "; ".join(problems)[:500])
                os.chmod(stage, 0o755)
                fsync_dir(stage)
                parent = target.parent
                created = []
                for ancestor in reversed([parent, *parent.parents]):
                    if _inside(self.archive, ancestor) and not ancestor.exists():
                        ancestor.mkdir(mode=0o755)
                        created.append(ancestor)
                for directory in created:
                    fsync_dir(directory.parent)
                os.rename(stage, target)  # the only step that makes the document visible
                fsync_dir(parent)
            except BaseException:
                shutil.rmtree(stage, ignore_errors=True)
                raise
            self._append_manifest({"event": "PUBLISHED", "documentId": sidecar["documentId"],
                                   "artifactVersion": sidecar["artifactVersion"],
                                   "metadataRevision": sidecar["metadataRevision"], "archivePath": archive_path,
                                   "sha256": expected_sha256, "sidecarSha256": sha256_file(target / SIDECAR)})
            return target

    # ------------------------------------------------------------ sidecar edits

    def update_sidecar(self, document_id: str, expected_revision: int, changes: dict) -> dict:
        """Compare-and-swap sidecar edit. Never rewrites the PDF."""
        immutable = {"schemaVersion", "documentId", "artifactVersion", "relativePdfPath", "integrity",
                     "derivedFrom", "approval", "signature", "archivePath", "relativeTextPath", "createdAt",
                     "textExtraction"}
        if immutable & set(changes):
            raise ArchiveError("IMMUTABLE_FIELD", ",".join(sorted(immutable & set(changes))))
        with self.lock():
            directory = self.find(document_id)
            if directory is None:
                raise ArchiveError("DOCUMENT_NOT_FOUND", document_id)
            current = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))
            if current["metadataRevision"] != expected_revision:
                raise ArchiveError("REVISION_CONFLICT", f"current revision {current['metadataRevision']}")
            updated = {**current, **changes, "metadataRevision": expected_revision + 1, "updatedAt": now()}
            errors = schema_errors(updated)
            if errors:
                raise ArchiveError("SIDECAR_INVALID", "; ".join(errors)[:500])
            temporary = directory / f".{SIDECAR}.{secrets.token_hex(4)}"
            write_file(temporary, canonical_json(updated))
            os.replace(temporary, directory / SIDECAR)
            fsync_dir(directory)
            self._append_manifest({"event": "SIDECAR_UPDATED", "documentId": document_id,
                                   "metadataRevision": updated["metadataRevision"],
                                   "sidecarSha256": sha256_file(directory / SIDECAR)})
            return updated

    # ------------------------------------------------------------ re-filing

    REORGANISE_FIELDS = {"title", "titleSource", "organization", "documentType", "documentDate", "issuer", "tags"}

    def reorganise(self, document_id: str, expected_revision: int, changes: dict,
                   text: str | None = None, text_extraction: dict | None = None) -> dict:
        """Re-file a document (new organisation, so possibly a new folder and file name).

        Recoverable at every step: (1) the new sidecar, which records the intended
        location and name, replaces the old one in place; (2) the PDF is renamed;
        (3) the directory is renamed. A crash after (1) or (2) leaves a document
        whose sidecar points elsewhere, which repair() completes. The PDF bytes
        never change.
        """
        if set(changes) - self.REORGANISE_FIELDS:
            raise ArchiveError("FIELD_NOT_REORGANISABLE", ",".join(sorted(set(changes) - self.REORGANISE_FIELDS)))
        with self.lock():
            directory = self.find(document_id)
            if directory is None:
                raise ArchiveError("DOCUMENT_NOT_FOUND", document_id)
            current = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))
            if current["metadataRevision"] != expected_revision:
                raise ArchiveError("REVISION_CONFLICT", f"current revision {current['metadataRevision']}")
            updated = {**current, **changes, "metadataRevision": expected_revision + 1, "updatedAt": now()}
            for key in self.REORGANISE_FIELDS - {"title", "organization", "tags"}:
                if key in changes and changes[key] is None:
                    updated.pop(key, None)
            archive_path, filename = document_paths(updated)
            updated.update(archivePath=archive_path, relativePdfPath=filename)
            if text is not None:
                updated["relativeTextPath"] = TEXT
                if text_extraction:
                    updated["textExtraction"] = text_extraction
            errors = schema_errors(updated)
            if errors:
                raise ArchiveError("SIDECAR_INVALID", "; ".join(errors)[:500])
            target = self.archive / archive_path
            if target != directory and (target.exists() or not _inside(self.archive, target)):
                raise ArchiveError("PATH_COLLISION", archive_path)
            if text is not None:
                temporary = directory / f".{TEXT}.{secrets.token_hex(4)}"
                write_file(temporary, text.encode("utf-8")[:MAX_TEXT_BYTES])
                os.replace(temporary, directory / TEXT)
            temporary = directory / f".{SIDECAR}.{secrets.token_hex(4)}"
            write_file(temporary, canonical_json(updated))
            os.replace(temporary, directory / SIDECAR)          # (1) intent recorded
            fsync_dir(directory)
            self._complete_move(directory, updated)              # (2) and (3)
            self._append_manifest({"event": "REORGANISED", "documentId": document_id, "archivePath": archive_path,
                                   "metadataRevision": updated["metadataRevision"],
                                   "sidecarSha256": sha256_file(target / SIDECAR)})
            return updated

    def _complete_move(self, directory: Path, sidecar: dict) -> Path:
        """Finish a recorded re-filing: rename the PDF, then the directory."""
        wanted_pdf = directory / sidecar["relativePdfPath"]
        if not wanted_pdf.exists():
            candidates = [entry for entry in directory.iterdir() if entry.suffix == ".pdf" and entry.is_file()
                          and not entry.is_symlink() and sha256_file(entry) == sidecar["integrity"]["sha256"]]
            if len(candidates) != 1:
                raise ArchiveError("MOVE_UNRECOVERABLE", "the recorded PDF bytes are not present exactly once")
            os.rename(candidates[0], wanted_pdf)
            fsync_dir(directory)
        target = self.archive / sidecar["archivePath"]
        if target == directory:
            return directory
        if target.exists() or not _inside(self.archive, target):
            raise ArchiveError("PATH_COLLISION", sidecar["archivePath"])
        created = []
        for ancestor in reversed([target.parent, *target.parent.parents]):
            if _inside(self.archive, ancestor) and not ancestor.exists():
                ancestor.mkdir(mode=0o755)
                created.append(ancestor)
        for parent in created:
            fsync_dir(parent.parent)
        os.rename(directory, target)
        fsync_dir(target.parent)
        fsync_dir(directory.parent)
        return target

    # ------------------------------------------------------------ verification

    def check_document(self, directory: Path, sidecar_location: str | None = None) -> list[str]:
        """Problems with one document directory; empty means valid.

        sidecar_location is given for a staged directory, whose own name is
        random until the atomic rename gives it the canonical name."""
        problems: list[str] = []
        try:
            sidecar = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            return [f"SIDECAR_UNREADABLE: {type(error).__name__}"]
        problems += [f"SIDECAR_INVALID: {item}" for item in schema_errors(sidecar)]
        if problems:
            return problems
        location = sidecar_location or directory.relative_to(self.archive).as_posix()
        if sidecar["archivePath"] != location:
            problems.append(f"LOCATION_MISMATCH: sidecar says {sidecar['archivePath']!r}")
        if sidecar_location is None and not directory.name.endswith(f" [{sidecar['documentId']}]"):
            problems.append("DIRECTORY_ID_MISMATCH")
        allowed = {SIDECAR, sidecar["relativePdfPath"]} | ({TEXT} if sidecar.get("relativeTextPath") else set())
        for entry in directory.iterdir():
            if entry.name not in allowed:
                problems.append(f"UNEXPECTED_ENTRY: {entry.name[:80]}")
            elif entry.is_symlink() or not entry.is_file():
                problems.append(f"NOT_A_REGULAR_FILE: {entry.name[:80]}")
        pdf = directory / sidecar["relativePdfPath"]
        if not pdf.is_file() or pdf.is_symlink():
            problems.append("PDF_MISSING")
        else:
            if pdf.stat().st_size != sidecar["integrity"]["bytes"] or sha256_file(pdf) != sidecar["integrity"]["sha256"]:
                problems.append("PDF_HASH_MISMATCH")
            with pdf.open("rb") as handle:
                if handle.read(5) != b"%PDF-":
                    problems.append("PDF_SIGNATURE_INVALID")
        if sidecar.get("relativeTextPath") and not (directory / TEXT).is_file():
            problems.append("TEXT_MISSING")
        return problems

    def verify(self) -> dict:
        """Check every published document; report, never modify."""
        documents, failures, seen = 0, {}, {}
        for directory in self.document_dirs():
            documents += 1
            relative = directory.relative_to(self.archive).as_posix()
            problems = self.check_document(directory)
            try:
                doc_id = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))["documentId"]
                if doc_id in seen:
                    problems.append(f"DUPLICATE_DOCUMENT_ID: also at {seen[doc_id]}")
                seen[doc_id] = relative
            except (OSError, ValueError, KeyError, TypeError):
                pass
            if problems:
                failures[relative] = problems
        # PDFs or directories without a sidecar are damage, not documents.
        for pdf in self.archive.rglob("*.pdf"):
            if not (pdf.parent / SIDECAR).exists():
                failures.setdefault(pdf.parent.relative_to(self.archive).as_posix(), []).append("SIDECAR_MISSING")
        damaged = sum(1 for key in failures if (self.archive / key / SIDECAR).exists())
        return {"documents": documents, "valid": documents - damaged, "failures": failures, "ok": not failures}

    # ------------------------------------------------------------ reindex

    def reindex(self, catalog: Path) -> dict:
        """Build a standalone SQLite catalog from sidecars and files alone."""
        catalog.parent.mkdir(parents=True, exist_ok=True)
        temporary = catalog.with_name(f".{catalog.name}.{secrets.token_hex(4)}")
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript("""
                CREATE TABLE documents (document_id TEXT PRIMARY KEY, archive_path TEXT NOT NULL UNIQUE,
                    pdf_file TEXT NOT NULL, title TEXT NOT NULL, category TEXT NOT NULL, review_state TEXT NOT NULL,
                    document_type TEXT, document_date TEXT, issuer TEXT, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
                    artifact_version INTEGER NOT NULL, metadata_revision INTEGER NOT NULL, original_signed INTEGER NOT NULL,
                    scan_id TEXT NOT NULL, policy_version TEXT NOT NULL, sidecar TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE tags (document_id TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (document_id, tag));
                CREATE TABLE content (document_id TEXT PRIMARY KEY, text TEXT NOT NULL);
                CREATE TABLE rejected (archive_path TEXT PRIMARY KEY, problems TEXT NOT NULL);
            """)
            indexed = 0
            for directory in self.document_dirs():
                relative = directory.relative_to(self.archive).as_posix()
                problems = self.check_document(directory)
                if problems:
                    connection.execute("INSERT INTO rejected VALUES (?,?)", (relative, json.dumps(problems)))
                    continue
                sidecar = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))
                try:
                    connection.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        sidecar["documentId"], relative, sidecar["relativePdfPath"], sidecar["title"],
                        sidecar["organization"]["category"], sidecar["organization"]["reviewState"],
                        sidecar.get("documentType"), (sidecar.get("documentDate") or {}).get("value"),
                        (sidecar.get("issuer") or {}).get("value"), sidecar["integrity"]["sha256"],
                        sidecar["integrity"]["bytes"], sidecar["artifactVersion"], sidecar["metadataRevision"],
                        int(sidecar["signature"]["originalSigned"]), sidecar["derivedFrom"]["scanId"],
                        sidecar["approval"]["policyVersion"], json.dumps(sidecar, sort_keys=True), sidecar["updatedAt"]))
                except sqlite3.IntegrityError:
                    connection.execute("INSERT INTO rejected VALUES (?,?)", (relative, json.dumps(["DUPLICATE_DOCUMENT_ID"])))
                    continue
                connection.executemany("INSERT INTO tags VALUES (?,?)", [(sidecar["documentId"], tag) for tag in sidecar.get("tags", [])])
                if sidecar.get("relativeTextPath"):
                    connection.execute("INSERT INTO content VALUES (?,?)", (sidecar["documentId"],
                                       (directory / TEXT).read_text(encoding="utf-8", errors="replace")))
                indexed += 1
            rejected = connection.execute("SELECT count(*) FROM rejected").fetchone()[0]
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary, catalog)
        return {"indexed": indexed, "rejected": rejected, "catalog": str(catalog)}

    # ------------------------------------------------------------ repair

    def repair(self, apply: bool = False, grace_seconds: int = STAGING_GRACE_SECONDS) -> dict:
        """Report (and with apply, fix) crash leftovers. Never touches valid documents.

        Fixable: abandoned staging directories (never visible), interrupted
        re-filings (the sidecar already records the target), and published
        documents missing from the manifest. Everything else is reported only.
        """
        actions = {"staleStaging": [], "manifestMissing": [], "movesCompleted": [], "reportedOnly": {}}
        with self.lock():
            cutoff = time.time() - grace_seconds
            for entry in self.staging.iterdir():
                if entry.lstat().st_mtime < cutoff:
                    actions["staleStaging"].append(entry.name)
                    if apply and entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry, ignore_errors=True)
                    elif apply:
                        entry.unlink()
            recorded = set()
            path = self.manifests / MANIFEST
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # a torn final line from a crash is ignored, then superseded
                    if event.get("event") == "PUBLISHED":
                        recorded.add(event.get("documentId"))
            # Re-filings interrupted after the sidecar recorded the new location.
            actions["movesCompleted"] = []
            for directory in list(self.document_dirs()):
                try:
                    sidecar = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if schema_errors(sidecar) or sidecar["archivePath"] == directory.relative_to(self.archive).as_posix():
                    continue
                actions["movesCompleted"].append(sidecar["documentId"])
                if apply:
                    try:
                        self._complete_move(directory, sidecar)
                    except ArchiveError:
                        actions["movesCompleted"].pop()
            report = self.verify()
            actions["reportedOnly"] = report["failures"]
            for directory in self.document_dirs():
                relative = directory.relative_to(self.archive).as_posix()
                if relative in report["failures"]:
                    continue
                sidecar = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))
                if sidecar["documentId"] not in recorded:
                    actions["manifestMissing"].append(sidecar["documentId"])
                    if apply:
                        self._append_manifest({"event": "PUBLISHED", "documentId": sidecar["documentId"],
                                               "artifactVersion": sidecar["artifactVersion"],
                                               "metadataRevision": sidecar["metadataRevision"],
                                               "archivePath": relative, "sha256": sidecar["integrity"]["sha256"],
                                               "sidecarSha256": sha256_file(directory / SIDECAR), "recovered": True})
        return {"applied": apply, **actions}

    # ------------------------------------------------------------ export / import

    def export(self, bundle: Path, document_ids: list[str] | None = None) -> dict:
        """Write a self-describing, checksummed tar bundle of valid documents."""
        report = self.verify()
        if not report["ok"]:
            raise ArchiveError("ARCHIVE_NOT_CLEAN", "run verify; export only from a verified archive")
        files = []
        with tarfile.open(bundle, "x", format=tarfile.PAX_FORMAT) as tar:
            for directory in self.document_dirs():
                sidecar = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))
                if document_ids and sidecar["documentId"] not in document_ids:
                    continue
                for entry in sorted(directory.iterdir()):
                    name = f"documents/{sidecar['documentId']}/{entry.name}"
                    files.append({"path": name, "sha256": sha256_file(entry), "bytes": entry.stat().st_size})
                    tar.add(entry, arcname=name, recursive=False, filter=_normalise_member)
            manifest = canonical_json({"format": EXPORT_FORMAT, "schemaVersion": SCHEMA_VERSION,
                                       "createdAt": now(), "files": files})
            info = tarfile.TarInfo(EXPORT_MANIFEST)
            info.size, info.mode, info.mtime = len(manifest), 0o644, int(time.time())
            tar.addfile(info, io.BytesIO(manifest))
        return {"bundle": str(bundle), "documents": len({item["path"].split("/")[1] for item in files}), "files": len(files)}

    def import_bundle(self, bundle: Path) -> dict:
        """Verify every byte of a bundle, then publish each document atomically."""
        imported, skipped = [], []
        work = self.staging / f"import.{secrets.token_hex(6)}"
        self.initialise()
        work.mkdir(mode=0o700)
        try:
            with tarfile.open(bundle, "r:") as tar:
                members = {}
                for member in tar.getmembers():
                    if not member.isfile() or member.name.startswith("/") or ".." in Path(member.name).parts:
                        raise ArchiveError("BUNDLE_UNSAFE_MEMBER", member.name[:120])
                    members[member.name] = member
                manifest = json.loads(tar.extractfile(members.pop(EXPORT_MANIFEST)).read())
                if manifest.get("format") != EXPORT_FORMAT:
                    raise ArchiveError("BUNDLE_FORMAT_UNSUPPORTED", str(manifest.get("format"))[:80])
                expected = {item["path"]: item for item in manifest["files"]}
                if set(expected) != set(members):
                    raise ArchiveError("BUNDLE_MANIFEST_MISMATCH")
                for name, member in members.items():
                    parts = Path(name).parts
                    if len(parts) != 3 or parts[0] != "documents" or not DOCUMENT_ID.match(parts[1]):
                        raise ArchiveError("BUNDLE_UNSAFE_MEMBER", name[:120])
                    target = work / parts[1] / parts[2]
                    target.parent.mkdir(mode=0o700, exist_ok=True)
                    with tar.extractfile(member) as source, target.open("xb") as handle:
                        shutil.copyfileobj(source, handle)
                    if sha256_file(target) != expected[name]["sha256"]:
                        raise ArchiveError("BUNDLE_HASH_MISMATCH", name[:120])
            for document in sorted(work.iterdir()):
                sidecar = json.loads((document / SIDECAR).read_text(encoding="utf-8"))
                if sidecar.get("documentId") != document.name:
                    raise ArchiveError("BUNDLE_ID_MISMATCH", document.name)
                existing = self.find(document.name)
                if existing is not None:
                    current = json.loads((existing / SIDECAR).read_text(encoding="utf-8"))
                    if current["integrity"]["sha256"] == sidecar["integrity"]["sha256"]:
                        skipped.append(document.name)
                        continue
                    raise ArchiveError("DOCUMENT_CONFLICT", document.name)
                text = (document / TEXT).read_text(encoding="utf-8") if sidecar.get("relativeTextPath") else None
                pdf = document / sidecar["relativePdfPath"]
                self.publish(pdf, sidecar["integrity"]["sha256"], sidecar, text)
                imported.append(document.name)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return {"imported": imported, "skippedIdentical": skipped}


def _normalise_member(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o644
    return info
