"""Portable document archive: crash-safe publication, verification and recovery.

The archive directory tree is the source of truth: folders of PDFs a person can
browse, each PDF with two hidden files beside it, its authoritative sidecar and
(optionally) its text. PostgreSQL, the manifest and any catalog are rebuildable
from the sidecars alone.

Layout under ROOT (all on one filesystem):
  archive/<Section>/<Correspondent>/<YYYY-MM-DD Title>.pdf
          ... /.<YYYY-MM-DD Title>.pdf.json   sidecar (metadata, integrity)
          ... /.<YYYY-MM-DD Title>.pdf.txt    text (search, the AI)
  staging/        private, invisible to readers; renamed into archive/
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

# The first layout's names (one folder per document); migrate_layout() moves such documents.
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
from organize import plain_punctuation  # noqa: E402  (shared with the metadata writer)

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

LIGATURES = {"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ß": "ss", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D"}


def safe_component(text: str | None, fallback: str, max_bytes: int = MAX_COMPONENT_BYTES) -> str:
    """Render untrusted text as one portable path component: plain ASCII letters, digits and
    - _ ( ) & ' . , (accents dropped: "Reçu" -> "Recu"), safe on Linux, Windows and macOS."""
    value = plain_punctuation(unicodedata.normalize("NFC", text or ""))
    for letter, plain in LIGATURES.items():
        value = value.replace(letter, plain)
    value = "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))
    value = re.sub(r"[^A-Za-z0-9 _\-()&'.,]", " ", value)
    value = UNSAFE.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    while len(value.encode("utf-8")) > max_bytes:
        value = value[:-1].rstrip(" .")
    if not value or value in (".", ".."):
        value = fallback
    if value.split(".")[0].upper() in RESERVED:
        value = f"{value}_"
    return value


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


def sidecar_name(pdf_name: str) -> str:
    return f".{pdf_name}.json"


def text_name(pdf_name: str) -> str:
    return f".{pdf_name}.txt"


SIDECAR_FILE = re.compile(r"^\.(.+\.pdf)\.json$")


def document_paths(sidecar: dict) -> tuple[str, str]:
    """(archivePath, file name) from the organisation fields, before collisions are resolved.

    Section/Correspondent/YYYY-MM-DD Title.pdf; a document without a correspondent sits in
    its section; one nothing filed goes to Unsorted. The date is the document's own, or the
    day it arrived. Names are plain ASCII (safe_component)."""
    category = sidecar["organization"]["category"]
    issuer = (sidecar.get("issuer") or {}).get("value")
    date = (sidecar.get("documentDate") or {}).get("value") or sidecar["createdAt"][:10]
    if category == "Unsorted":
        folder = "Unsorted"
    elif issuer:
        folder = f"{category}/{safe_component(issuer, 'Other', 60)}"
    else:
        folder = category
    room = max(40, min(120, MAX_ARCHIVE_PATH - len(folder) - 12))
    stem = safe_component(f"{date} {safe_component(sidecar['title'], 'Document', 100)}", date, room)
    return folder, f"{stem}.pdf"


@dataclass(frozen=True)
class Document:
    """Where one document lives: its PDF and, beside it, its hidden sidecar and text."""
    sidecar: Path

    @property
    def folder(self) -> Path:
        return self.sidecar.parent

    @property
    def name(self) -> str:
        return SIDECAR_FILE.match(self.sidecar.name).group(1)

    @property
    def pdf(self) -> Path:
        return self.folder / self.name

    @property
    def text(self) -> Path:
        return self.folder / text_name(self.name)

    @property
    def staged_version(self) -> Path:
        return self.folder / f".{self.name}.next"

    def read(self) -> dict:
        return json.loads(self.sidecar.read_text(encoding="utf-8"))


def _write_replace(path: Path, data: bytes) -> None:
    """Write a file atomically in its folder (hidden temporary, then rename)."""
    temporary = path.with_name(f"{path.name}.tmp{secrets.token_hex(4)}")
    write_file(temporary, data)
    os.replace(temporary, path)


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

    # ------------------------------------------------------------ finding documents

    def documents(self):
        """Every document's sidecar in the archive (whether or not its PDF is there)."""
        for path in sorted(self.archive.rglob(".*.pdf.json")):
            if SIDECAR_FILE.match(path.name) and path.is_file() and not path.is_symlink():
                yield Document(path)

    def find(self, document_id: str) -> Document | None:
        if not DOCUMENT_ID.match(document_id):
            raise ArchiveError("INVALID_DOCUMENT_ID")
        matches = []
        for document in self.documents():
            try:
                if document.read().get("documentId") == document_id:
                    matches.append(document)
            except (OSError, ValueError):
                continue
        if len(matches) > 1:
            raise ArchiveError("DUPLICATE_DOCUMENT_ID", document_id)
        return matches[0] if matches else None

    def relative(self, path: Path) -> str:
        return path.relative_to(self.archive).as_posix()

    def _staged_publication(self, document: Document) -> Path | None:
        """The staged PDF of a publication that has not moved it in yet, if any."""
        try:
            sidecar = document.read()
        except (OSError, ValueError):
            return None
        for stage in self.staging.glob(f"{sidecar.get('documentId')}.*"):
            if (stage / document.name).is_file() and sha256_file(stage / document.name) == sidecar.get("integrity", {}).get("sha256"):
                return stage / document.name
        return None

    def _place(self, folder: str, name: str, document_id: str) -> str:
        """A free file name in folder: name, else "name (2).pdf"... A document's own files count as free."""
        stem = name[:-4]
        for number in range(1, 1000):
            candidate = name if number == 1 else f"{stem} ({number}).pdf"
            pdf, sidecar = self.archive / folder / candidate, self.archive / folder / sidecar_name(candidate)
            owner = None
            if sidecar.exists():
                try:
                    owner = json.loads(sidecar.read_text(encoding="utf-8")).get("documentId")
                except (OSError, ValueError):
                    owner = "?"
            if owner == document_id or (owner is None and not pdf.exists()):
                return candidate
        raise ArchiveError("PATH_COLLISION", f"{folder}/{name}")

    def _make_folders(self, folder: Path) -> None:
        if not _inside(self.archive, folder):
            raise ArchiveError("PATH_COLLISION", str(folder))
        created = []
        for ancestor in reversed([folder, *folder.parents]):
            if _inside(self.archive, ancestor) and not ancestor.exists():
                ancestor.mkdir(mode=0o755)
                created.append(ancestor)
        for directory in created:
            fsync_dir(directory.parent)

    def _prune(self, folder: Path) -> None:
        """Remove folders left empty, up to the archive root."""
        while folder != self.archive and _inside(self.archive, folder) and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent
        if folder.is_dir():
            fsync_dir(folder)

    # ------------------------------------------------------------ publish

    def publish(self, pdf: Path, expected_sha256: str, sidecar: dict, text: str | None = None) -> Document:
        """Publish one approved PDF + sidecar. Returns the published document.

        The PDF, sidecar and text are prepared in staging. The text and the sidecar move
        into the folder first (hidden), the PDF last, so a PDF is never visible without
        its sidecar; repair() completes or removes what a crash leaves. Never overwrites.
        """
        sidecar = json.loads(json.dumps(sidecar))
        sidecar.pop("relativeTextPath", None)
        if text is not None:
            text = text.encode("utf-8")[:MAX_TEXT_BYTES].decode("utf-8", "ignore")
        with self.lock():
            if self.find(sidecar["documentId"]) is not None:
                raise ArchiveError("DOCUMENT_EXISTS", sidecar["documentId"])
            folder, base = document_paths(sidecar)
            name = self._place(folder, base, sidecar["documentId"])
            sidecar.update(schemaVersion=SCHEMA_VERSION, archivePath=folder, relativePdfPath=name)
            if text is not None:
                sidecar["relativeTextPath"] = text_name(name)
            target = self.archive / folder
            stage = self.staging / f"{sidecar['documentId']}.{secrets.token_hex(6)}"
            stage.mkdir(mode=0o700)
            try:
                digest = hashlib.sha256()
                size = 0
                with pdf.open("rb") as source, (stage / name).open("xb") as handle:
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
                    write_file(stage / text_name(name), text.encode("utf-8"))
                write_file(stage / sidecar_name(name), canonical_json(sidecar))
                problems = self.check_document(Document(stage / sidecar_name(name)), location=folder)
                if problems:
                    raise ArchiveError("STAGED_DOCUMENT_INVALID", "; ".join(problems)[:500])
                fsync_dir(stage)
                self._make_folders(target)
                if text is not None:
                    os.rename(stage / text_name(name), target / text_name(name))
                os.rename(stage / sidecar_name(name), target / sidecar_name(name))
                fsync_dir(target)
                os.rename(stage / name, target / name)  # the step that makes the document visible
                fsync_dir(target)
                stage.rmdir()
            except BaseException:
                if not (target / name).exists():
                    for leftover in (target / sidecar_name(name), target / text_name(name)):
                        if leftover.exists() and leftover.parent == target:
                            leftover.unlink()
                shutil.rmtree(stage, ignore_errors=True)
                raise
            document = Document(target / sidecar_name(name))
            self._append_manifest({"event": "PUBLISHED", "documentId": sidecar["documentId"],
                                   "artifactVersion": sidecar["artifactVersion"],
                                   "metadataRevision": sidecar["metadataRevision"], "archivePath": folder,
                                   "file": name, "sha256": expected_sha256, "sidecarSha256": sha256_file(document.sidecar)})
            return document

    # ------------------------------------------------------------ sidecar edits

    def update_sidecar(self, document_id: str, expected_revision: int, changes: dict) -> dict:
        """Compare-and-swap sidecar edit. Never rewrites the PDF."""
        immutable = {"schemaVersion", "documentId", "artifactVersion", "relativePdfPath", "integrity",
                     "derivedFrom", "approval", "signature", "archivePath", "relativeTextPath", "createdAt",
                     "textExtraction"}
        if immutable & set(changes):
            raise ArchiveError("IMMUTABLE_FIELD", ",".join(sorted(immutable & set(changes))))
        with self.lock():
            document = self.find(document_id)
            if document is None:
                raise ArchiveError("DOCUMENT_NOT_FOUND", document_id)
            current = document.read()
            if current["metadataRevision"] != expected_revision:
                raise ArchiveError("REVISION_CONFLICT", f"current revision {current['metadataRevision']}")
            updated = {**current, **changes, "metadataRevision": expected_revision + 1, "updatedAt": now()}
            errors = schema_errors(updated)
            if errors:
                raise ArchiveError("SIDECAR_INVALID", "; ".join(errors)[:500])
            _write_replace(document.sidecar, canonical_json(updated))
            fsync_dir(document.folder)
            self._append_manifest({"event": "SIDECAR_UPDATED", "documentId": document_id,
                                   "metadataRevision": updated["metadataRevision"],
                                   "sidecarSha256": sha256_file(document.sidecar)})
            return updated

    # ------------------------------------------------------------ re-filing

    REORGANISE_FIELDS = {"title", "titleSource", "organization", "documentType", "documentDate", "issuer", "tags",
                         "reference", "amount", "language"}

    def reorganise(self, document_id: str, expected_revision: int, changes: dict,
                   text: str | None = None, text_extraction: dict | None = None) -> dict:
        """Re-file a document (new organisation, so possibly a new folder and file name).

        Recoverable at every step: (1) the new sidecar, which records the intended folder and
        name, replaces the old one in place; (2) the text and the PDF move; (3) the sidecar
        moves last. A crash after (1) or (2) leaves a sidecar that names another place, which
        repair() completes. The PDF bytes never change.
        """
        if set(changes) - self.REORGANISE_FIELDS:
            raise ArchiveError("FIELD_NOT_REORGANISABLE", ",".join(sorted(set(changes) - self.REORGANISE_FIELDS)))
        with self.lock():
            document = self.find(document_id)
            if document is None:
                raise ArchiveError("DOCUMENT_NOT_FOUND", document_id)
            current = document.read()
            if current["metadataRevision"] != expected_revision:
                raise ArchiveError("REVISION_CONFLICT", f"current revision {current['metadataRevision']}")
            updated = {**current, **changes, "metadataRevision": expected_revision + 1, "updatedAt": now()}
            for key in self.REORGANISE_FIELDS - {"title", "organization", "tags"}:
                if key in changes and changes[key] is None:
                    updated.pop(key, None)
            folder, base = document_paths(updated)
            name = self._place(folder, base, document_id)
            updated.update(archivePath=folder, relativePdfPath=name)
            if text is not None or current.get("relativeTextPath"):
                updated["relativeTextPath"] = text_name(name)
            if text is not None and text_extraction:
                updated["textExtraction"] = text_extraction
            errors = schema_errors(updated)
            if errors:
                raise ArchiveError("SIDECAR_INVALID", "; ".join(errors)[:500])
            if text is not None:
                _write_replace(document.text, text.encode("utf-8")[:MAX_TEXT_BYTES])
            _write_replace(document.sidecar, canonical_json(updated))       # (1) intent recorded
            fsync_dir(document.folder)
            moved = self._complete_move(document, updated)               # (2) and (3)
            self._append_manifest({"event": "REORGANISED", "documentId": document_id, "archivePath": folder, "file": name,
                                   "metadataRevision": updated["metadataRevision"], "sidecarSha256": sha256_file(moved.sidecar)})
            return updated

    def _complete_move(self, document: Document, sidecar: dict) -> Document:
        """Finish a recorded re-filing: move the text and the PDF, then the sidecar."""
        target = self.archive / sidecar["archivePath"]
        name = sidecar["relativePdfPath"]
        if target / name == document.pdf:
            return document
        self._make_folders(target)
        new_pdf = target / name
        if not new_pdf.exists():
            if not document.pdf.is_file() or sha256_file(document.pdf) != sidecar["integrity"]["sha256"]:
                raise ArchiveError("MOVE_UNRECOVERABLE", "the recorded PDF bytes are not where the sidecar was")
            os.rename(document.pdf, new_pdf)
        if document.text.exists() and not (target / text_name(name)).exists():
            os.rename(document.text, target / text_name(name))
        fsync_dir(target)
        moved = Document(target / sidecar_name(name))
        os.replace(document.sidecar, moved.sidecar)
        fsync_dir(target)
        self._prune(document.folder)
        return moved

    # ------------------------------------------------------------ new versions and deletion

    def replace_pdf(self, document_id: str, expected_revision: int, pdf: Path, expected_sha256: str, changes: dict) -> dict:
        """Publish a new version of a document's PDF (validated elsewhere; its sha256 is given).

        Recoverable: (1) the new bytes are written beside the PDF as .<name>.next; (2) the
        sidecar, now recording the new hash and version, replaces the old one; (3) the PDF is
        replaced. A crash after (2) leaves a sidecar that names bytes found in .next, which
        repair() puts in place; a crash before (2) leaves the old version intact.
        """
        with self.lock():
            document = self.find(document_id)
            if document is None:
                raise ArchiveError("DOCUMENT_NOT_FOUND", document_id)
            current = document.read()
            if current["metadataRevision"] != expected_revision:
                raise ArchiveError("REVISION_CONFLICT", f"current revision {current['metadataRevision']}")
            data = pdf.read_bytes()
            if hashlib.sha256(data).hexdigest() != expected_sha256:
                raise ArchiveError("INPUT_HASH_MISMATCH", "the new version is not the validated bytes")
            if document.staged_version.exists():
                document.staged_version.unlink()
            write_file(document.staged_version, data)                        # (1)
            updated = {**current, **changes, "integrity": {"sha256": expected_sha256, "bytes": len(data)},
                       "artifactVersion": current["artifactVersion"] + 1,
                       "metadataRevision": expected_revision + 1, "updatedAt": now()}
            errors = schema_errors(updated)
            if errors:
                document.staged_version.unlink()
                raise ArchiveError("SIDECAR_INVALID", "; ".join(errors)[:500])
            _write_replace(document.sidecar, canonical_json(updated))         # (2)
            os.replace(document.staged_version, document.pdf)                # (3)
            fsync_dir(document.folder)
            self._append_manifest({"event": "VERSIONED", "documentId": document_id,
                                   "artifactVersion": updated["artifactVersion"], "metadataRevision": updated["metadataRevision"],
                                   "sha256": expected_sha256, "sidecarSha256": sha256_file(document.sidecar)})
            return updated

    def delete(self, document_id: str) -> str | None:
        """Remove a document (PDF, sidecar, text) and the folders it leaves empty. Returns its path."""
        with self.lock():
            document = self.find(document_id)
            if document is None:
                return None
            relative = self.relative(document.pdf)
            for path in (document.pdf, document.text, document.staged_version, document.sidecar):
                path.unlink(missing_ok=True)
            self._prune(document.folder)
            self._append_manifest({"event": "DELETED", "documentId": document_id, "archivePath": relative})
            return relative

    # ------------------------------------------------------------ verification

    def check_document(self, document: Document, location: str | None = None) -> list[str]:
        """Problems with one document; empty means valid.

        location is given for a staged document, whose folder is not its final one."""
        problems: list[str] = []
        try:
            sidecar = document.read()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            return [f"SIDECAR_UNREADABLE: {type(error).__name__}"]
        problems += [f"SIDECAR_INVALID: {item}" for item in schema_errors(sidecar)]
        if problems:
            return problems
        where = location if location is not None else self.relative(document.folder)
        if sidecar["archivePath"] != where or sidecar["relativePdfPath"] != document.name:
            problems.append(f"LOCATION_MISMATCH: sidecar says {sidecar['archivePath']}/{sidecar['relativePdfPath']}")
        pdf = document.pdf
        if not pdf.is_file() or pdf.is_symlink():
            problems.append("PDF_MISSING")
        else:
            if pdf.stat().st_size != sidecar["integrity"]["bytes"] or sha256_file(pdf) != sidecar["integrity"]["sha256"]:
                problems.append("PDF_HASH_MISMATCH")
            with pdf.open("rb") as handle:
                if handle.read(5) != b"%PDF-":
                    problems.append("PDF_SIGNATURE_INVALID")
        if sidecar.get("relativeTextPath"):
            if sidecar["relativeTextPath"] != text_name(document.name) or not document.text.is_file() or document.text.is_symlink():
                problems.append("TEXT_MISSING")
        if document.staged_version.exists():
            problems.append("UNFINISHED_VERSION")
        return problems

    def verify(self) -> dict:
        """Check every published document; report, never modify."""
        documents, failures, seen, described = 0, {}, {}, set()
        for document in self.documents():
            relative = self.relative(document.pdf)
            described.add(document.pdf)
            problems = self.check_document(document)
            if problems == ["PDF_MISSING"] and self._staged_publication(document):
                continue  # a publication under way (or interrupted): never visible, not damage
            documents += 1
            try:
                doc_id = document.read()["documentId"]
                if doc_id in seen:
                    problems.append(f"DUPLICATE_DOCUMENT_ID: also at {seen[doc_id]}")
                seen[doc_id] = relative
            except (OSError, ValueError, KeyError, TypeError):
                pass
            if problems:
                failures[relative] = problems
        # PDFs without a sidecar are damage (or something added by hand), not documents.
        for pdf in self.archive.rglob("*.pdf"):
            if not pdf.name.startswith(".") and pdf not in described:
                failures.setdefault(self.relative(pdf), []).append("SIDECAR_MISSING")
        for legacy in self.archive.rglob(SIDECAR):
            failures.setdefault(self.relative(legacy.parent), []).append("LEGACY_LAYOUT: run migrate-layout")
        damaged = sum(1 for key, problems in failures.items() if "SIDECAR_MISSING" not in problems)
        return {"documents": documents, "valid": documents - min(damaged, documents), "failures": failures, "ok": not failures}

    # ------------------------------------------------------------ reindex

    def reindex(self, catalog: Path) -> dict:
        """Build a standalone SQLite catalog from sidecars and files alone."""
        catalog.parent.mkdir(parents=True, exist_ok=True)
        temporary = catalog.with_name(f".{catalog.name}.{secrets.token_hex(4)}")
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript("""
                CREATE TABLE documents (document_id TEXT PRIMARY KEY, archive_path TEXT NOT NULL,
                    pdf_file TEXT NOT NULL, title TEXT NOT NULL, category TEXT NOT NULL, review_state TEXT NOT NULL,
                    document_type TEXT, document_date TEXT, issuer TEXT, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
                    artifact_version INTEGER NOT NULL, metadata_revision INTEGER NOT NULL, original_signed INTEGER NOT NULL,
                    scan_id TEXT NOT NULL, policy_version TEXT NOT NULL, sidecar TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE (archive_path, pdf_file));
                CREATE TABLE tags (document_id TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (document_id, tag));
                CREATE TABLE content (document_id TEXT PRIMARY KEY, text TEXT NOT NULL);
                CREATE TABLE rejected (archive_path TEXT PRIMARY KEY, problems TEXT NOT NULL);
            """)
            indexed = 0
            for document in self.documents():
                relative = self.relative(document.pdf)
                problems = self.check_document(document)
                if problems:
                    connection.execute("INSERT INTO rejected VALUES (?,?)", (relative, json.dumps(problems)))
                    continue
                sidecar = document.read()
                try:
                    connection.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        sidecar["documentId"], sidecar["archivePath"], sidecar["relativePdfPath"], sidecar["title"],
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
                                       document.text.read_text(encoding="utf-8", errors="replace")))
                indexed += 1
            rejected = connection.execute("SELECT count(*) FROM rejected").fetchone()[0]
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary, catalog)
        return {"catalog": str(catalog), "indexed": indexed, "rejected": rejected}

    # ------------------------------------------------------------ repair

    def repair(self, apply: bool = False, grace_seconds: int = STAGING_GRACE_SECONDS) -> dict:
        """Report (and with apply, fix) crash leftovers. Never touches valid documents.

        Fixable: publications interrupted before the PDF moved in (finished from staging, or
        removed if never visible), new versions interrupted after the sidecar changed,
        re-filings interrupted after the sidecar recorded the new place, abandoned staging
        directories, and published documents missing from the manifest.
        """
        actions = {"staleStaging": [], "manifestMissing": [], "movesCompleted": [], "versionsCompleted": [],
                   "publishesCompleted": [], "orphansRemoved": [], "reportedOnly": {}}
        with self.lock():
            cutoff = time.time() - grace_seconds
            for document in list(self.documents()):
                try:
                    sidecar = document.read()
                except (OSError, ValueError):
                    continue
                if schema_errors(sidecar):
                    continue
                doc_id = sidecar["documentId"]
                at_place = sidecar["archivePath"] == self.relative(document.folder) and sidecar["relativePdfPath"] == document.name
                if at_place and not document.pdf.exists():
                    # A publication that stopped before its PDF moved in: finish it from staging, or
                    # (the PDF is gone with its stage) remove what was never visible.
                    staged = self._staged_publication(document)
                    if staged:
                        actions["publishesCompleted"].append(doc_id)
                        if apply:
                            os.rename(staged, document.pdf)
                            fsync_dir(document.folder)
                    elif document.sidecar.stat().st_mtime < cutoff:
                        actions["orphansRemoved"].append(doc_id)
                        if apply:
                            document.text.unlink(missing_ok=True)
                            document.sidecar.unlink()
                            self._prune(document.folder)
                    continue
                if document.staged_version.is_file() and not document.staged_version.is_symlink():
                    if sha256_file(document.staged_version) == sidecar["integrity"]["sha256"]:
                        actions["versionsCompleted"].append(doc_id)
                        if apply:
                            os.replace(document.staged_version, document.pdf)
                            fsync_dir(document.folder)
                    elif apply and document.pdf.is_file() and sha256_file(document.pdf) == sidecar["integrity"]["sha256"]:
                        document.staged_version.unlink()  # written before the sidecar changed: the old version stands
                if not at_place:
                    actions["movesCompleted"].append(doc_id)
                    if apply:
                        try:
                            self._complete_move(document, sidecar)
                        except ArchiveError:
                            actions["movesCompleted"].pop()
            # A text moved in before its sidecar, by a publication that then stopped: never part of a document.
            for text in self.archive.rglob(".*.pdf.txt"):
                if not (text.parent / f"{text.name[:-4]}.json").exists() and text.lstat().st_mtime < cutoff:
                    actions["orphansRemoved"].append(self.relative(text))
                    if apply:
                        text.unlink()
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
                    if event.get("event") in ("PUBLISHED", "MIGRATED"):
                        recorded.add(event.get("documentId"))
            report = self.verify()
            actions["reportedOnly"] = report["failures"]
            for document in self.documents():
                if self.relative(document.pdf) in report["failures"]:
                    continue
                sidecar = document.read()
                if sidecar["documentId"] not in recorded:
                    actions["manifestMissing"].append(sidecar["documentId"])
                    if apply:
                        self._append_manifest({"event": "PUBLISHED", "documentId": sidecar["documentId"],
                                               "artifactVersion": sidecar["artifactVersion"],
                                               "metadataRevision": sidecar["metadataRevision"],
                                               "archivePath": sidecar["archivePath"], "file": sidecar["relativePdfPath"],
                                               "sha256": sidecar["integrity"]["sha256"],
                                               "sidecarSha256": sha256_file(document.sidecar), "recovered": True})
        return {"applied": apply, **actions}

    # ------------------------------------------------------------ migration from folder-per-document

    def migrate_layout(self, apply: bool = False) -> dict:
        """Move documents of the first layout (one folder per document: <file>.pdf, metadata.json,
        content.txt, named "... [doc-...]") to Section/Correspondent/YYYY-MM-DD Title.pdf with hidden
        sidecars. Each move: new sidecar first (hidden, new place), then text, then PDF, then the old
        folder goes. Running it again finishes an interrupted move and changes nothing else."""
        moved = []
        with self.lock():
            for legacy in sorted(self.archive.rglob(SIDECAR)):
                directory = legacy.parent
                try:
                    current = json.loads(legacy.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                doc_id = current.get("documentId")
                if not isinstance(doc_id, str) or not DOCUMENT_ID.match(doc_id):
                    continue
                existing = self.find(doc_id)
                if existing is None:
                    folder, base = document_paths(current)
                    name = self._place(folder, base, doc_id)
                else:
                    folder, name = self.relative(existing.folder), existing.name
                moved.append({"documentId": doc_id, "from": self.relative(directory), "to": f"{folder}/{name}"})
                if not apply:
                    continue
                target = self.archive / folder
                updated = {**current, "archivePath": folder, "relativePdfPath": name, "updatedAt": now(),
                           "metadataRevision": current["metadataRevision"] + (0 if existing else 1)}
                old_text = directory / TEXT
                if current.get("relativeTextPath"):
                    updated["relativeTextPath"] = text_name(name)
                errors = schema_errors(updated)
                if errors:
                    raise ArchiveError("SIDECAR_INVALID", "; ".join(errors)[:500])
                self._make_folders(target)
                document = Document(target / sidecar_name(name))
                if existing is None:
                    _write_replace(document.sidecar, canonical_json(updated))
                if old_text.exists():
                    os.replace(old_text, document.text)
                old_pdf = directory / current["relativePdfPath"]
                if old_pdf.exists() and not document.pdf.exists():
                    os.rename(old_pdf, document.pdf)
                fsync_dir(target)
                if document.pdf.exists():
                    legacy.unlink()
                    for leftover in directory.iterdir():
                        if leftover.name.startswith(".metadata.json."):
                            leftover.unlink()  # a temporary sidecar of the first layout
                    self._prune(directory)
                    self._append_manifest({"event": "MIGRATED", "documentId": doc_id, "archivePath": folder, "file": name,
                                           "sidecarSha256": sha256_file(document.sidecar)})
        return {"applied": apply, "documents": moved}

    # ------------------------------------------------------------ export / import

    def export(self, bundle: Path, document_ids: list[str] | None = None) -> dict:
        """Write a self-describing, checksummed tar bundle of valid documents."""
        report = self.verify()
        if not report["ok"]:
            raise ArchiveError("ARCHIVE_NOT_CLEAN", "run verify; export only from a verified archive")
        files = []
        with tarfile.open(bundle, "x", format=tarfile.PAX_FORMAT) as tar:
            for document in self.documents():
                sidecar = document.read()
                if document_ids and sidecar["documentId"] not in document_ids:
                    continue
                for entry in (document.pdf, document.sidecar, *([document.text] if sidecar.get("relativeTextPath") else [])):
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
            for folder in sorted(work.iterdir()):
                sidecars = [entry for entry in folder.iterdir() if SIDECAR_FILE.match(entry.name)]
                legacy = folder / SIDECAR
                source = sidecars[0] if len(sidecars) == 1 else legacy if legacy.exists() else None
                if source is None:
                    raise ArchiveError("BUNDLE_ID_MISMATCH", folder.name)
                sidecar = json.loads(source.read_text(encoding="utf-8"))
                if sidecar.get("documentId") != folder.name:
                    raise ArchiveError("BUNDLE_ID_MISMATCH", folder.name)
                existing = self.find(folder.name)
                if existing is not None:
                    if existing.read()["integrity"]["sha256"] == sidecar["integrity"]["sha256"]:
                        skipped.append(folder.name)
                        continue
                    raise ArchiveError("DOCUMENT_CONFLICT", folder.name)
                text_file = folder / (sidecar.get("relativeTextPath") or "") if sidecar.get("relativeTextPath") else None
                if text_file is not None and not text_file.is_file():
                    text_file = folder / TEXT
                text = text_file.read_text(encoding="utf-8") if text_file is not None and text_file.is_file() else None
                pdf = folder / sidecar["relativePdfPath"]
                self.publish(pdf, sidecar["integrity"]["sha256"], sidecar, text)
                imported.append(folder.name)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return {"imported": imported, "skippedIdentical": skipped}


def _normalise_member(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o644
    return info
