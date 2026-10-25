"""Validation and application of OCR/AI results from the enrichment agent.

Agent output is untrusted. OCR text is bounded and cleaned, then used like any
other text: deterministic rules may re-file the document (a recoverable archive
move; the PDF never changes). AI output only becomes *proposals*, and only
fields whose evidence quote occurs in the document text and whose tags belong
to the controlled vocabulary survive. Nothing here can touch a security verdict.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
from datetime import date

OCR_MAX_PAGES = 1000
OCR_MAX_CHARS = 2 * 1024 * 1024
BREAKER_FAILURES = int(os.getenv("PDF_GATEWAY_AI_BREAKER_FAILURES", "3"))
BREAKER_MINUTES = int(os.getenv("PDF_GATEWAY_AI_BREAKER_MINUTES", "15"))
BACKLOG_CAP = int(os.getenv("PDF_GATEWAY_ENRICHMENT_BACKLOG", "500"))
ENABLED = os.getenv("PDF_GATEWAY_ENRICHMENT_ENABLED", "false") == "true"
UNSAFE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩﻿]")


class InvalidResult(Exception):
    """The agent returned something that violates the contract."""


def clean(text: str) -> str:
    return UNSAFE.sub("", unicodedata.normalize("NFC", text))


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", clean(text)).strip().casefold()


def queue_initial(cursor, document_id: str, sha256: str, review_state: str, has_text: bool) -> str | None:
    """Queue enrichment for documents filed for review. Returns the job kind or None."""
    if not ENABLED or review_state not in ("FALLBACK", "REVIEW_REQUIRED"):
        return None
    return queue(cursor, document_id, sha256, "SUGGEST" if has_text else "OCR")


def queue(cursor, document_id: str, sha256: str, kind: str) -> str | None:
    cursor.execute("SELECT count(*) FROM enrichment_jobs WHERE status='WAITING_FOR_AI'")
    if cursor.fetchone()[0] >= BACKLOG_CAP:
        cursor.execute("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id,details) VALUES ('ENRICHMENT_SKIPPED','DOCUMENT',%s,'FAILURE',%s,'{\"code\":\"BACKLOG_FULL\"}'::jsonb)",
                       (document_id, str(uuid.uuid4())))
        return None
    cursor.execute("INSERT INTO enrichment_jobs(id,document_id,artifact_sha256,kind) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                   (str(uuid.uuid4()), document_id, sha256, kind))
    return kind


def validate_ocr(result: dict) -> tuple[str, dict]:
    if result.get("kind") != "OCR" or not isinstance(result.get("pages"), list):
        raise InvalidResult("not an OCR result")
    pages = result["pages"]
    if not 0 < len(pages) <= OCR_MAX_PAGES:
        raise InvalidResult("page count out of bounds")
    texts = []
    for expected, page in enumerate(pages, 1):
        if not isinstance(page, dict) or page.get("page") != expected or not isinstance(page.get("text"), str):
            raise InvalidResult("pages must be numbered text entries")
        texts.append(clean(page["text"]))
    engine = result.get("engine")
    if not isinstance(engine, str) or not 0 < len(engine) <= 80:
        raise InvalidResult("engine missing")
    text = "\n\f\n".join(texts).strip()
    truncated = len(text) > OCR_MAX_CHARS
    text = text[:OCR_MAX_CHARS]
    meta = {"method": "ocr", "source": "viewing_copy", "engine": clean(engine)[:80], "pages": len(pages),
            "characters": len(text), "truncated": truncated or bool(result.get("truncated"))}
    return text, meta


def validate_suggestion(result: dict, text: str, vocabulary: set[str]) -> tuple[dict, list[str]]:
    """Keep only evidence-backed, vocabulary-conforming fields. Returns (fields, dropped reasons)."""
    if result.get("kind") != "SUGGEST" or not isinstance(result.get("fields"), dict):
        raise InvalidResult("not a suggestion result")
    for key in ("model", "promptVersion"):
        if not isinstance(result.get(key), str) or not 0 < len(result[key]) <= 120:
            raise InvalidResult(f"{key} missing")
    haystack = normalise(text)
    fields, dropped = {}, []

    def evidenced(name: str, item) -> str | None:
        if not isinstance(item, dict) or not isinstance(item.get("value"), str) or not isinstance(item.get("evidence"), str):
            dropped.append(f"{name}: malformed")
            return None
        value, quote = clean(item["value"]).strip(), normalise(item["evidence"])
        if not value or len(value) > 200:
            dropped.append(f"{name}: value length")
            return None
        if len(quote) < 3 or len(quote) > 300 or quote not in haystack:
            dropped.append(f"{name}: evidence not found in the document text")
            return None
        return value

    raw = result["fields"]
    for name in ("title", "issuer"):
        if name in raw and (value := evidenced(name, raw[name])) is not None:
            fields[name] = {"value": value, "evidence": clean(raw[name]["evidence"])[:300]}
    if "documentType" in raw and (value := evidenced("documentType", raw["documentType"])) is not None:
        slug = re.sub(r"[^a-z0-9_-]+", "_", value.lower()).strip("_")[:64]
        if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", slug):
            fields["documentType"] = {"value": slug, "evidence": clean(raw["documentType"]["evidence"])[:300]}
        else:
            dropped.append("documentType: not a valid type identifier")
    if "documentDate" in raw and (value := evidenced("documentDate", raw["documentDate"])) is not None:
        try:
            fields["documentDate"] = {"value": date.fromisoformat(value).isoformat(), "evidence": clean(raw["documentDate"]["evidence"])[:300]}
        except ValueError:
            dropped.append("documentDate: not an ISO date")
    tags = []
    for item in raw.get("tags", []) if isinstance(raw.get("tags"), list) else []:
        value = evidenced("tag", item)
        if value is None:
            continue
        if value.lower() not in vocabulary:
            dropped.append(f"tag {value[:40]!r}: not in the controlled vocabulary")
            continue
        tags.append(value.lower())
    if tags:
        fields["tags"] = sorted(set(tags))
    return fields, dropped


def record_failure(cursor, job_id: str, agent: str | None, code: str) -> None:
    cursor.execute("UPDATE enrichment_jobs SET status='FAILED', error_code=%s, completed_at=now() WHERE id=%s", (code[:48], job_id))
    if agent:
        cursor.execute("""UPDATE ai_agents SET consecutive_failures=consecutive_failures+1, last_error=%s,
                               breaker_open_until=CASE WHEN consecutive_failures+1 >= %s THEN now()+make_interval(mins=>%s) ELSE breaker_open_until END
                           WHERE agent_id=%s""", (code[:80], BREAKER_FAILURES, BREAKER_MINUTES, agent))


def record_success(cursor, job_id: str, agent: str | None) -> None:
    cursor.execute("UPDATE enrichment_jobs SET status='COMPLETED', completed_at=now() WHERE id=%s", (job_id,))
    if agent:
        cursor.execute("UPDATE ai_agents SET consecutive_failures=0, last_success_at=now(), last_error=NULL WHERE agent_id=%s", (agent,))


def suggestion_record(job_id: str, document_id: str, result: dict, fields: dict, dropped: list[str]) -> tuple:
    return (str(uuid.uuid4()), document_id, job_id, clean(result["model"])[:120], clean(result["promptVersion"])[:40],
            json.dumps({**fields, "dropped": dropped[:50]}))
