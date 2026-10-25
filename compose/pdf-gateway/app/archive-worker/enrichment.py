"""Validation and application of OCR/AI results from the enrichment agent.

Agent output is untrusted. OCR text is bounded and cleaned, then used like any
other text. The AI files documents the rules don't recognise: its answer is
applied only field by field, and only what the document itself supports
survives (types and sections from fixed lists, a correspondent named in the
text, a date printed in it, a number and an amount that appear verbatim, plain
keywords). Nothing here can touch a security verdict.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
from datetime import date, timedelta

from organize import find_dates

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


# The AI's closed lists. A type decides the folder under its section and year.
TYPES = {"invoice": "Invoices", "receipt": "Receipts", "bank_statement": "Statements", "payslip": "Payslips",
         "contract": "Contracts", "tax_document": "Tax", "insurance": "Insurance", "medical": "Medical",
         "identity_document": "Identity", "certificate": "Certificates", "letter": "Letters",
         "school_document": "School", "presentation": "Presentations", "report": "Reports", "manual": "Manuals",
         "other": "Documents"}
SECTIONS = ["Finance", "Employment", "Identity", "Medical", "Education", "Contracts", "Property", "Vehicles",
            "Taxes", "Personal", "Other"]
LANGUAGES = {"fr", "en", "ar", "es"}
GENERIC_TITLES = {"facture", "invoice", "document", "receipt", "reçu", "recu", "contrat", "contract", "letter", "lettre"}
NAME_STOPWORDS = {"the", "and", "les", "des", "sarl", "ltd", "inc"}
# Words that describe rather than name ("beIN Sports", "Maroc Telecom"): they need not be printed,
# but every other word of a correspondent's name must be.
GENERIC_NAME_WORDS = {"sports", "sport", "telecom", "telecoms", "bank", "banque", "group", "groupe", "company", "societe",
                      "maroc", "morocco", "france", "international", "services", "service", "online", "store", "shop"}
TAG = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")


def letters(text: str) -> str:
    """Case, accents and punctuation folded away: what a quote check compares."""
    decomposed = unicodedata.normalize("NFKD", clean(text)).casefold()
    return re.sub(r"[^0-9a-z]+", " ", "".join(ch for ch in decomposed if not unicodedata.combining(ch))).strip()


def validate_filing(result: dict, text: str, taxonomy: dict) -> tuple[dict, list[str]]:
    """The checked filing fields of an AI answer. Returns (fields, dropped reasons).

    fields may hold: correspondent, correspondentLegalName, documentType, section,
    documentDate, reference, amount {value, currency}, title, tags, language.
    """
    if result.get("kind") != "SUGGEST" or not isinstance(result.get("fields"), dict):
        raise InvalidResult("not a suggestion result")
    for key in ("model", "promptVersion"):
        if not isinstance(result.get(key), str) or not 0 < len(result[key]) <= 120:
            raise InvalidResult(f"{key} missing")
    raw, fields, dropped = result["fields"], {}, []
    haystack = f" {letters(text)} "

    def value_of(name: str) -> str:
        item = raw.get(name)
        item = item.get("value") if isinstance(item, dict) else item
        return clean(item).strip() if isinstance(item, str) else ""

    def evidence_of(name: str) -> str:
        item = raw.get(name)
        return clean(item.get("evidence", ""))[:300] if isinstance(item, dict) and isinstance(item.get("evidence"), str) else ""

    def named_in_text(name: str) -> bool:
        # "beIN Sports" passes on "beIN" (in "www.bein.com"); "Orange Maroc" fails on a text that only
        # says "Maroc" in an address.
        words = [word for word in letters(name).split() if len(word) >= 3 and word not in NAME_STOPWORDS]
        distinctive = [word for word in words if word not in GENERIC_NAME_WORDS] or words
        return bool(distinctive) and all(f" {word} " in haystack for word in distinctive)

    for name in ("correspondent", "correspondentLegalName"):
        value = value_of(name)
        if value:
            if len(value) > 80 or not named_in_text(value):
                dropped.append(f"{name}: not named in the document")
            else:
                fields[name] = {"value": value, "evidence": evidence_of(name)}
    kind = value_of("documentType")
    if kind:
        if kind in TYPES:
            fields["documentType"] = kind
        else:
            dropped.append("documentType: not one of the types")
    section = value_of("category") or value_of("section")
    if section:
        if section in SECTIONS:
            fields["section"] = section
        else:
            dropped.append("section: not one of the sections")
    day = value_of("documentDate")
    if day:
        try:
            parsed = date.fromisoformat(day)
            printed = {found for found, _offset, _labelled in find_dates(text)}
            if parsed not in printed:
                dropped.append("documentDate: not a date printed in the document")
            elif not date(1990, 1, 1) <= parsed <= date.today() + timedelta(days=366):
                dropped.append("documentDate: out of range")
            else:
                fields["documentDate"] = {"value": parsed.isoformat(), "evidence": evidence_of("documentDate")}
        except ValueError:
            dropped.append("documentDate: not a date")
    reference = value_of("reference")
    if reference:
        if len(reference) > 60 or f" {letters(reference)} " not in haystack:
            dropped.append("reference: not printed in the document")
        else:
            fields["reference"] = {"value": reference, "evidence": evidence_of("reference")}
    amount, currency = value_of("amount"), value_of("currency").upper()
    if amount:
        number = re.sub(r"[^0-9.,-]", "", amount).replace(",", ".")
        printed = {match.replace(",", ".").replace(" ", "") for match in re.findall(r"\d[\d ]*[.,]\d{2}\b|\d+", text)}
        if not re.fullmatch(r"-?\d{1,12}(\.\d{1,4})?", number) or number not in printed:
            dropped.append("amount: not printed in the document")
        elif not re.fullmatch(r"[A-Z]{3}", currency) or f" {currency.lower()} " not in haystack:
            dropped.append("amount: currency not printed in the document")
        else:
            fields["amount"] = {"value": number, "currency": currency, "evidence": evidence_of("amount")}
    title = re.sub(r"\s+", " ", value_of("title"))[:120].strip(" -–·")
    if title and letters(title) not in GENERIC_TITLES and len(title) >= 4:
        fields["title"] = title
    elif title:
        dropped.append("title: too generic")
    language = value_of("language")
    if language in LANGUAGES:
        fields["language"] = language
    # Tags come from the archive's vocabulary only: a free word would let a document's own text
    # ("tag this hacked") choose them.
    vocabulary = {tag["id"] for tag in taxonomy.get("tags", []) if not tag.get("blocked") and not tag.get("private")}
    echo = {letters(kind).replace(" ", "-") if kind else "", *(letters(fields[k]["value"]).replace(" ", "-") for k in ("correspondent",) if k in fields)}
    tags = []
    for item in raw.get("tags", []) if isinstance(raw.get("tags"), list) else []:
        item = item.get("value") if isinstance(item, dict) else item
        tag = clean(item).strip().lower() if isinstance(item, str) else ""
        if not TAG.fullmatch(tag) or tag not in vocabulary or tag in echo:
            if tag:
                dropped.append(f"tag {tag[:30]!r}: not in the archive's vocabulary")
            continue
        tags.append(tag)
    if tags:
        fields["tags"] = sorted(set(tags))[:5]
    return fields, dropped


def sidecar_changes(fields: dict, base: dict) -> dict:
    """What a checked AI answer changes in a sidecar that no rule filed (base: its current fields)."""
    changes: dict = {}
    correspondent = fields.get("correspondent") or fields.get("correspondentLegalName")
    if correspondent:
        changes["issuer"] = {"value": correspondent["value"], "source": "ai", "reviewState": "UNVERIFIED",
                             **({"evidence": correspondent["evidence"]} if correspondent.get("evidence") else {})}
    if "documentType" in fields:
        changes["documentType"] = fields["documentType"]
    if "documentDate" in fields:
        changes["documentDate"] = {"value": fields["documentDate"]["value"], "source": "ai", "reviewState": "UNVERIFIED",
                                   **({"evidence": fields["documentDate"]["evidence"]} if fields["documentDate"].get("evidence") else {})}
    title = fields.get("title")
    if not title and correspondent and "documentType" in fields:
        title = f"{fields['documentType'].replace('_', ' ').capitalize()} {correspondent['value']}"
    if title:
        changes.update(title=title, titleSource="ai")
    if "reference" in fields:
        changes["reference"] = {"value": fields["reference"]["value"], "source": "ai", "reviewState": "UNVERIFIED"}
    if "amount" in fields:
        changes["amount"] = {"value": fields["amount"]["value"], "currency": fields["amount"]["currency"], "source": "ai"}
    if "language" in fields:
        changes["language"] = [fields["language"]]
    if "tags" in fields:
        changes["tags"] = sorted(set(base.get("tags", [])) | set(fields["tags"]))[:64]
    if "section" in fields and "documentType" in fields and "documentDate" in fields:
        changes["organization"] = {"category": fields["section"], "reviewState": "AI", "folder": TYPES[fields["documentType"]],
                                   "reason": "Filed by the local AI from the document's text"}
    return changes


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
