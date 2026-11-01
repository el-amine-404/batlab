"""Validation and application of OCR/AI results from the enrichment agent.

Agent output is untrusted. OCR text is bounded and cleaned, then used like any
other text. The AI files documents the rules don't recognise: its answer is
applied only field by field, and only what the document itself supports
survives (types and sections from fixed lists, a correspondent named in the
text, a date printed in it, a number and an amount that appear verbatim, plain
keywords). Nothing here can touch a security verdict.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
import uuid
from datetime import date, timedelta

from organize import find_dates, plain_punctuation

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
    # Always read first, even when the PDF has text: a PDF's text is often wrong (fonts that map "fl" to "1").
    # The document is already saved; this only makes it better, however long the laptop takes.
    return queue(cursor, document_id, sha256, "OCR", {"force": True})


def queue(cursor, document_id: str, sha256: str, kind: str, context: dict | None = None) -> str | None:
    cursor.execute("SELECT count(*) FROM enrichment_jobs WHERE status='WAITING_FOR_AI'")
    if cursor.fetchone()[0] >= BACKLOG_CAP:
        cursor.execute("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id,details) VALUES ('ENRICHMENT_SKIPPED','DOCUMENT',%s,'FAILURE',%s,'{\"code\":\"BACKLOG_FULL\"}'::jsonb)",
                       (document_id, str(uuid.uuid4())))
        return None
    # Asked again for the same bytes: the job that answered last time goes back in the queue.
    forced = bool(context and context.get("force"))
    cursor.execute("""INSERT INTO enrichment_jobs(id,document_id,artifact_sha256,kind,context) VALUES (%s,%s,%s,%s,%s::jsonb)
                      ON CONFLICT (document_id,kind,artifact_sha256) DO UPDATE SET status='WAITING_FOR_AI', context=EXCLUDED.context,
                             result=NULL, applied=NULL, lease_agent=NULL, lease_until=NULL, attempts=0, error_code=NULL,
                             completed_at=NULL, deadline_at=NULL, expires_at=now()+interval '30 days'
                       WHERE %s AND enrichment_jobs.status NOT IN ('WAITING_FOR_AI','LEASED','SUBMITTED')""",
                   (str(uuid.uuid4()), document_id, sha256, kind, json.dumps(context) if context else None, forced))
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
# Words that describe rather than name ("Acme Sports", "Northwind Telecom"): they need not be printed,
# but every other word of a correspondent's name must be.
GENERIC_NAME_WORDS = {"sports", "sport", "telecom", "telecoms", "bank", "banque", "group", "groupe", "company", "societe",
                      "maroc", "morocco", "france", "international", "services", "service", "online", "store", "shop"}
TAG = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
MAX_TAGS = 6
# A credit line, or a full address to somewhere's page (a bare "www.acme.example" in a letterhead is the sender's).
CITED_SOURCE = re.compile(r"(?im)^.*(?:\b(?:source|sources|bibliographie|références)\s*:|(?:https?://|www\.)\S+/\S).*$")


def letters(text: str) -> str:
    """Case, accents and punctuation folded away: what a quote check compares."""
    decomposed = unicodedata.normalize("NFKD", clean(text)).casefold()
    return re.sub(r"[^0-9a-z]+", " ", "".join(ch for ch in decomposed if not unicodedata.combining(ch))).strip()


MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december", "janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
          "septembre", "octobre", "novembre", "decembre"}


def title_named_in_text(title: str, haystack: str) -> bool:
    """Names in a title must be printed in the document: a word written as a name (capitalised
    after the first word, or mixed case like "eBay") that the text never says means the title
    was borrowed (from an example, another document) or guessed."""
    for position, word in enumerate(re.findall(r"[^\W_]+", title)):
        named = (position > 0 and word[0].isupper()) or any(ch.isupper() for ch in word[1:])
        folded = letters(word)
        if named and len(folded) >= 2 and folded not in MONTHS and f" {folded} " not in haystack:
            return False
    return True


def snapped_to_text(name: str, text: str) -> str:
    """A name with its words as the document prints them: a model copying "HOUJJAJI" can drop a letter, and a name
    must never be spelled differently from the page. Only a near miss moves; anything else stays and fails the check."""
    printed = {word.casefold(): word for word in re.findall(r"[^\W\d_]{3,}", text)}
    def fix(match: re.Match) -> str:
        word = match.group()
        if word.casefold() in printed:
            return word
        near = difflib.get_close_matches(word.casefold(), printed, n=1, cutoff=0.85)
        return printed[near[0]] if near else word
    return re.sub(r"[^\W\d_]{3,}", fix, name)


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
        # "Acme Sports" passes on "Acme" (in "www.acme.example"); "Northwind Maroc" fails on a text that
        # only says "Maroc" in an address.
        words = [word for word in letters(name).split() if len(word) >= 3 and word not in NAME_STOPWORDS]
        if not words:
            # A name in another script: letters() can't see it.
            printed = set(tokens(text))
            return bool(tokens(name)) and all(word in printed for word in tokens(name))
        distinctive = [word for word in words if word not in GENERIC_NAME_WORDS] or words
        return bool(distinctive) and all(f" {word} " in haystack for word in distinctive)

    authors = [snapped_to_text(name.strip(), text) for name in re.split(r"[,;\n]", value_of("author")) if name.strip()]
    known = [name for name in authors if len(name) <= 80 and named_in_text(name)]
    if len(known) < len(authors):
        dropped.append("author: a name is not in the document")
    if known:
        fields["author"] = ", ".join(known)[:200]
    subject = plain_punctuation(value_of("subject"))[:200].strip()
    if len(subject) >= 10:
        fields["subject"] = subject
    # A cited source isn't who issued the document, however often the slides credit it.
    # With named authors, whoever issued it signs the first page too: a logo on slide 9 is a credit.
    own_words = f" {letters(CITED_SOURCE.sub(' ', text.split(chr(12))[0] if known else text))} "
    for name in ("correspondent", "correspondentLegalName", "holder"):
        value = snapped_to_text(value_of(name), text)
        if value:
            words = [word for word in letters(value).split() if len(word) >= 3 and word not in NAME_STOPWORDS]
            if len(value) > 80 or not named_in_text(value):
                dropped.append(f"{name}: not named in the document")
            elif name != "holder" and not any(f" {word} " in own_words for word in words):
                dropped.append(f"{name}: only named in a cited source")
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
    title = plain_punctuation(value_of("title"))[:120].strip(" -")
    if title and (letters(title) in GENERIC_TITLES or len(title) < 4):
        dropped.append("title: too generic")
    elif title and not title_named_in_text(title, haystack):
        dropped.append("title: names something the document does not")
    elif title:
        fields["title"] = title
    language = value_of("language")
    # The model says "ar" for a mostly French form with Arabic beside it.
    arabic = sum("\u0600" <= char <= "\u06ff" for char in text)
    if language == "ar" and arabic < sum(char.isalpha() for char in text) / 2:
        language = "fr"
    if language in LANGUAGES:
        fields["language"] = language
    # The archive's vocabulary, or words the document prints: a topic the vocabulary lacks ("logistique-urbaine")
    # still gets a tag, and a word that isn't on the page can't. Blocked and private tags are refused either way.
    vocabulary = {tag["id"] for tag in taxonomy.get("tags", []) if not tag.get("blocked") and not tag.get("private")}
    refused = {tag["id"] for tag in taxonomy.get("tags", []) if tag.get("blocked") or tag.get("private")}
    unaccented = unicodedata.normalize("NFD", haystack)
    unaccented = "".join(char for char in unaccented if not unicodedata.combining(char))
    printed = lambda word: all(f" {part} " in unaccented or f" {part}s " in unaccented for part in word.split("-"))
    echo = {letters(kind).replace(" ", "-") if kind else "", *(letters(fields[k]["value"]).replace(" ", "-") for k in ("correspondent",) if k in fields)}
    tags = []
    for item in raw.get("tags", []) if isinstance(raw.get("tags"), list) else []:
        item = item.get("value") if isinstance(item, dict) else item
        tag = clean(item).strip().lower() if isinstance(item, str) else ""
        if not TAG.fullmatch(tag) or tag in refused or tag in echo or (tag not in vocabulary and not printed(tag)):
            if tag:
                dropped.append(f"tag {tag[:30]!r}: neither in the archive's vocabulary nor printed in the document")
            continue
        tags.append(tag)
    if tags:
        fields["tags"] = list(dict.fromkeys(tags))[:MAX_TAGS]
    return fields, dropped


def tokens(text: str) -> list[str]:
    """Words in any script, unlike letters(), which keeps a-z and digits only: an Arabic name has to be comparable too."""
    decomposed = unicodedata.normalize("NFKD", clean(text)).casefold()
    return re.findall(r"[^\W_]{2,}", "".join(ch for ch in decomposed if not unicodedata.combining(ch)))


def field_checks(fields: dict, second: str) -> dict:
    """Which checked fields a second reader of the page also saw: "CONFIRMED", or "CHECK" for a person to look at.
    Nothing here changes a value. Without a second reading nothing is claimed."""
    if not second.strip():
        return {}
    seen = set(tokens(second))
    def found(value: str) -> bool:
        words = tokens(value)
        return bool(words) and all(word in seen for word in words)
    checks = {}
    for name, key in (("correspondent", "issuer"), ("holder", "holder"), ("reference", "reference"), ("author", "author")):
        item = fields.get(name)
        value = item.get("value") if isinstance(item, dict) else item
        if value:
            checks[key] = "CONFIRMED" if found(value) else "CHECK"
    if fields.get("documentDate"):
        printed = {day.isoformat() for day, _offset, _labelled in find_dates(second)}
        checks["documentDate"] = "CONFIRMED" if fields["documentDate"]["value"] in printed else "CHECK"
    if fields.get("amount"):
        number = fields["amount"]["value"]
        printed = {match.replace(",", ".").replace(" ", "") for match in re.findall(r"\d[\d ]*[.,]\d{2}\b|\d+", second)}
        checks["amount"] = "CONFIRMED" if number in printed else "CHECK"
    return checks


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
    if "author" in fields:
        changes["author"] = {"value": fields["author"], "source": "ai", "reviewState": "UNVERIFIED"}
    if "subject" in fields:
        changes["subject"] = fields["subject"]
    if "tags" in fields:
        changes["tags"] = sorted(set(base.get("tags", [])) | set(fields["tags"]))[:64]
    holder = fields.get("holder")
    if holder and fields.get("documentType") in ("identity_document", "certificate"):
        changes["holder"] = {"value": holder["value"], "source": "ai", "reviewState": "UNVERIFIED",
                             **({"evidence": holder["evidence"]} if holder.get("evidence") else {})}
    # An identity document often prints no issue date: its holder files it instead.
    if "section" in fields and "documentType" in fields and ("documentDate" in fields or "holder" in changes):
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
