"""Deterministic, offline document organisation (no AI).

Inputs are untrusted native text plus reviewed, versioned rules from the
archive's manifests/rules.json and vocabulary from manifests/taxonomy.json.
Precedence: explicit user values > saved rules > corroborated extraction >
safe defaults. Uncertainty or conflict goes to review in Unsorted instead of
aggressive filing. Text never chooses a path directly: categories, folders,
issuers and tags come from rules and the taxonomy; text supplies only matched
evidence and dates.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import jsonschema

ORGANIZER_VERSION = "organizer-1"
MAX_TEXT = 64 * 1024
CATEGORIES = ["Finance", "Employment", "Identity", "Medical", "Education", "Contracts", "Property", "Vehicles",
              "Taxes", "Personal", "Other", "Unsorted"]
MONTHS = {name: number for number, names in enumerate([
    ("january", "janvier", "jan"), ("february", "fevrier", "février", "feb", "fev", "fév"), ("march", "mars", "mar"),
    ("april", "avril", "apr", "avr"), ("may", "mai"), ("june", "juin", "jun"), ("july", "juillet", "jul", "juil"),
    ("august", "aout", "août", "aug"), ("september", "septembre", "sep", "sept"), ("october", "octobre", "oct"),
    ("november", "novembre", "nov"), ("december", "decembre", "décembre", "dec", "déc")], 1) for name in names}
DATE_PATTERNS = [
    (re.compile(r"\b(20\d\d|19\d\d)-(\d\d)-(\d\d)\b"), ("y", "m", "d")),
    (re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d\d|19\d\d)\b"), ("d", "m", "y")),
    # "15 October 2026", "1er mars 2026", "10-Jun-2026"
    (re.compile(r"\b(\d{1,2})(?:er)?(?:\s+|-)([A-Za-zÀ-ÿ]{3,9})\.?(?:\s+|-)(20\d\d|19\d\d)\b"), ("d", "mon", "y")),
    (re.compile(r"\b([A-Za-zÀ-ÿ]{3,9})\.?\s+(\d{1,2}),\s+(20\d\d|19\d\d)\b"), ("mon", "d", "y")),
]
DATE_LABELS = re.compile(r"(invoice date|date of issue|issue date|date de facture|date d'émission|date d.emission|"
                         r"statement date|date du relevé|date)\s*[:\-]?\s*$", re.I)


@dataclass
class Organisation:
    fields: dict          # sidecar fields: title, titleSource, organization, documentType, documentDate, issuer, tags
    evidence: dict        # restricted: matched rule, evidence snippets, decisions


# The archive's default vocabulary (manifests/taxonomy.json holds the live one, which
# people may extend). Revision 2 added everyday topics, so tags help find things.
DEFAULT_TAGS_REVISION = 2
DEFAULT_TAGS = (
    "finance", "invoice", "receipt", "bank", "statement", "tax", "salary", "payslip", "loan", "insurance",
    "subscription", "internet", "phone", "mobile", "tv", "streaming", "sport", "football", "electricity", "water",
    "gas", "utilities", "rent", "housing", "property", "car", "vehicle", "fuel", "transport", "travel", "flight",
    "hotel", "health", "medical", "pharmacy", "doctor", "hospital", "education", "school", "university", "course",
    "exam", "certificate", "diploma", "identity", "passport", "visa", "residence", "employment", "contract", "work",
    "administration", "government", "legal", "family", "personal", "shopping", "online", "warranty", "repair",
    "membership", "donation", "refund", "payment")


# Typographic punctuation a model (or a rule) may write, as plain ASCII. Titles keep
# their letters (accents included); only these marks are replaced.
PUNCTUATION = {"\u2013": "-", "\u2014": "-", "\u2012": "-", "\u2212": "-", "\u00b7": "-", "\u2022": "-",
               "\u201c": "'", "\u201d": "'", "\u201e": "'", "\u00ab": "'", "\u00bb": "'", "\u2018": "'",
               "\u2019": "'", "\u2026": "...", "\u00a0": " ", "\u202f": " "}


def plain_punctuation(text: str) -> str:
    for mark, plain in PUNCTUATION.items():
        text = text.replace(mark, plain)
    return re.sub(r"\s+", " ", text).strip()


def default_taxonomy(updated_at: str) -> dict:
    return {"schemaVersion": "1.0.0", "revision": DEFAULT_TAGS_REVISION, "categories": CATEGORIES, "updatedAt": updated_at, "tags": [
        {"id": tag, "label": tag.replace("-", " ").capitalize(), "aliases": [], "private": False, "blocked": False}
        for tag in DEFAULT_TAGS]}


SCHEMAS = Path(os.getenv("PDF_ARCHIVE_SCHEMA_DIR", Path(__file__).parent))


def validated(path: Path, schema_name: str, default):
    """Load a manifest file and refuse it unless it matches its contract.

    A missing file means the documented default; an invalid one is an error,
    never silently replaced (it would change filing decisions)."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
    cls = jsonschema.validators.validator_for(schema)
    cls(schema, format_checker=cls.FORMAT_CHECKER).validate(value)
    return value


def find_dates(text: str) -> list[tuple[date, int, bool]]:
    """All plausible dates as (date, offset, labelled)."""
    found = []
    for pattern, order in DATE_PATTERNS:
        for match in pattern.finditer(text):
            parts = dict(zip(order, match.groups()))
            try:
                month = int(parts["m"]) if "m" in parts else MONTHS[parts["mon"].lower().rstrip(".")]
                value = date(int(parts["y"]), month, int(parts["d"]))
            except (KeyError, ValueError):
                continue
            if not date(1990, 1, 1) <= value <= date(2100, 1, 1):
                continue
            prefix = text[max(0, match.start() - 40):match.start()]
            found.append((value, match.start(), bool(DATE_LABELS.search(prefix))))
    return sorted(found, key=lambda item: item[1])


def snippet(text: str, offset: int, length: int = 80) -> str:
    start = max(0, offset - 20)
    return re.sub(r"\s+", " ", text[start:start + length]).strip()[:200]


def rule_matches(rule: dict, lowered: str) -> list[int] | None:
    match = rule.get("match", {})
    offsets = []
    for needle in match.get("all", []):
        found = lowered.find(needle.lower())
        if found < 0:
            return None
        offsets.append(found)
    any_terms = match.get("any", [])
    if any_terms:
        hits = [lowered.find(term.lower()) for term in any_terms if term.lower() in lowered]
        if not hits:
            return None
        offsets.append(min(hits))
    for needle in match.get("none", []):
        if needle.lower() in lowered:
            return None
    return offsets if offsets else None


def organise(text: str, rules: dict, taxonomy: dict) -> Organisation:
    text = (text or "")[:MAX_TEXT]
    lowered = text.lower()
    known_tags = {tag["id"]: tag for tag in taxonomy.get("tags", [])}
    alias = {a.lower(): tag["id"] for tag in taxonomy.get("tags", []) for a in tag.get("aliases", [])}
    evidence = {"organizerVersion": ORGANIZER_VERSION, "rulesRevision": rules.get("revision"),
                "taxonomyRevision": taxonomy.get("revision"), "textCharacters": len(text)}

    matched = []
    for rule in rules.get("rules", []):
        if rule.get("enabled", True) and rule.get("category") in CATEGORIES:
            offsets = rule_matches(rule, lowered)
            if offsets is not None:
                matched.append((rule, offsets))
    evidence["matchedRules"] = [rule["id"] for rule, _ in matched]
    dates = find_dates(text)
    labelled = [item for item in dates if item[2]]
    chosen = (labelled or dates or [None])[0]

    def fallback(reason: str, review: str) -> Organisation:
        evidence["decision"] = reason
        return Organisation({
            "title": "Unclassified document", "titleSource": "fallback",
            "organization": {"category": "Unsorted", "reviewState": review, "reason": reason},
            "tags": [],
        }, evidence)

    if not text.strip():
        return fallback("NO_TEXT: no native text; OCR not run, classification needs review", "FALLBACK")
    if not matched:
        return fallback("NO_RULE_MATCHED", "FALLBACK")
    categories = {rule["category"] for rule, _ in matched}
    if len(categories) > 1:
        return fallback("CONFLICTING_RULES: " + ",".join(sorted(rule["id"] for rule, _ in matched))[:400], "REVIEW_REQUIRED")
    rule, offsets = sorted(matched, key=lambda item: item[0].get("priority", 0), reverse=True)[0]
    if chosen is None:
        organisation = fallback(f"RULE_WITHOUT_DATE: {rule['id']}", "REVIEW_REQUIRED")
        organisation.fields["issuer"] = {"value": rule["issuer"], "source": "rule", "reviewState": "UNVERIFIED",
                                         "evidence": snippet(text, offsets[0])}
        return organisation
    value, offset, was_labelled = chosen
    tags, dropped = [], []
    for tag in rule.get("tags", []):
        tag_id = alias.get(tag.lower(), tag)
        if tag_id in known_tags and not known_tags[tag_id]["blocked"]:
            tags.append(tag_id)
        else:
            dropped.append(tag)
    evidence.update(decision=f"RULE:{rule['id']}", droppedTags=dropped, dateLabelled=was_labelled)
    month = value.strftime("%B %Y")
    title = rule.get("title", rule.get("documentType", "Document").capitalize())
    fields = {
        "title": f"{title} - {month}"[:200], "titleSource": "rule",
        "organization": {"category": rule["category"], "reviewState": "RULE", "folder": rule["folder"],
                         "reason": f"rule {rule['id']} (rules revision {rules.get('revision')})"},
        "documentType": rule["documentType"],
        "issuer": {"value": rule["issuer"], "source": "rule", "reviewState": "UNVERIFIED", "evidence": snippet(text, offsets[0])},
        "documentDate": {"value": value.isoformat(), "source": "native_text",
                         "reviewState": "UNVERIFIED" if was_labelled else "REVIEW_REQUIRED",
                         "evidence": snippet(text, offset)},
        "tags": sorted(set(tags)),
    }
    if not was_labelled:
        fields["organization"]["reason"] += "; date not labelled, first date in text used"
    return Organisation(fields, evidence)
