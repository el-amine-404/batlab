#!/usr/bin/env python3
"""M4 slice 2: deterministic organisation from real extracted text.

Runs inside the archive image: python /tests/organize_test.py /data
/data/texts/<fixture>.txt and <fixture>.json come from extract_text.py.
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/pdf-gateway")
from archive import Archive, schema_errors  # noqa: E402
from organize import default_taxonomy, organise, validated  # noqa: E402

DATA = Path(sys.argv[1])
NOW = "2026-10-25T12:00:00Z"
RULES = {"schemaVersion": "1.0.0", "revision": 3, "updatedAt": NOW, "rules": [
    {"id": "example-telecom-invoice", "priority": 10, "issuer": "Example Telecom",
     "match": {"all": ["example telecom"], "any": ["invoice", "facture"]}, "category": "Finance",
     "folder": "Invoices", "documentType": "invoice", "title": "Internet Invoice",
     "tags": ["finance", "internet", "invoice", "not-in-taxonomy"]},
    {"id": "banque-exemple-statement", "issuer": "Banque Exemple", "match": {"all": ["banque exemple"], "any": ["releve", "statement"]},
     "category": "Finance", "folder": "Statements", "documentType": "bank_statement", "title": "Bank Statement",
     "tags": ["finance", "bank", "statement"]},
    {"id": "example-telecom-contract", "issuer": "Example Telecom", "match": {"all": ["example telecom", "releve"]},
     "category": "Contracts", "folder": "Telecom", "documentType": "contract", "title": "Telecom Contract"},
]}


def check(name, condition, detail=""):
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    print(f"ok - {name}")


def text(name):
    path = DATA / "texts" / f"{name}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


summary = {name: json.loads((DATA / "texts" / f"{name}.json").read_text()) for name in
           ("invoice-en", "statement-fr", "unfamiliar", "conflicting", "undated-invoice", "blank-image", "malformed", "large-text")}
check("extraction found text", summary["invoice-en"]["status"] == "OK" and summary["invoice-en"]["characters"] > 100, summary["invoice-en"])
check("image-only and malformed give NO_TEXT, not failure",
      summary["blank-image"]["status"] == summary["malformed"]["status"] == "NO_TEXT")
check("extraction is bounded", summary["large-text"]["truncated"] and summary["large-text"]["characters"] <= 16 * 1024, summary["large-text"])

# Manifests are contract-validated; an invalid rules file is refused, not ignored.
(DATA / "rules.json").write_text(json.dumps(RULES))
rules = validated(DATA / "rules.json", "rules.schema.json", None)
broken = dict(RULES, rules=[dict(RULES["rules"][0], category="Unsorted")])
(DATA / "bad-rules.json").write_text(json.dumps(broken))
try:
    validated(DATA / "bad-rules.json", "rules.schema.json", None)
    raise AssertionError("invalid rules accepted")
except Exception as error:
    check("invalid rules refused", type(error).__name__ == "ValidationError", error)
taxonomy = default_taxonomy(NOW)
check("default taxonomy valid", validated(DATA / "missing.json", "taxonomy.schema.json", taxonomy) is taxonomy)

invoice = organise(text("invoice-en"), rules, taxonomy)
f = invoice.fields
check("named-issuer invoice classified by rule", f["organization"] | {} == {
    "category": "Finance", "reviewState": "RULE", "folder": "Invoices",
    "reason": "rule example-telecom-invoice (rules revision 3)"}, f["organization"])
check("labelled invoice date chosen over due date", f["documentDate"]["value"] == "2026-10-15"
      and f["documentDate"]["reviewState"] == "UNVERIFIED" and "Invoice date" in f["documentDate"]["evidence"], f["documentDate"])
check("evidence-backed title and issuer", f["title"] == "Internet Invoice - October 2026" and f["titleSource"] == "rule"
      and f["issuer"]["value"] == "Example Telecom" and "Example Telecom" in f["issuer"]["evidence"])
check("tags limited to taxonomy", f["tags"] == ["finance", "internet", "invoice"]
      and invoice.evidence["droppedTags"] == ["not-in-taxonomy"])

statement = organise(text("statement-fr"), rules, taxonomy)
check("French statement with dd/mm/yyyy date", statement.fields["organization"]["folder"] == "Statements"
      and statement.fields["documentDate"]["value"] == "2026-10-31", statement.fields)

for name, reason, review in [("unfamiliar", "NO_RULE_MATCHED", "FALLBACK"), ("blank-image", "NO_TEXT", "FALLBACK"),
                             ("conflicting", "CONFLICTING_RULES", "REVIEW_REQUIRED"),
                             ("undated-invoice", "RULE_WITHOUT_DATE", "REVIEW_REQUIRED")]:
    result = organise(text(name), rules, taxonomy).fields
    check(f"{name} goes to Unsorted for review", result["organization"]["category"] == "Unsorted"
          and result["organization"]["reviewState"] == review and result["organization"]["reason"].startswith(reason), result)

# Same inputs give the same decision (no clock, randomness or network involved).
check("deterministic", organise(text("invoice-en"), rules, taxonomy).fields == f)

# Organised fields publish to the expected, portable paths; hostile text never reaches a path.
archive = Archive(DATA / "root")
pdf = DATA / "fixtures" / "invoice-en.pdf"
digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
base = {"documentId": "doc-01J000000000000000000000G1", "artifactVersion": 1, "metadataRevision": 1,
        "derivedFrom": {"scanId": "scan-01J00000000000000000000060", "kind": "CDR_RECONSTRUCTION"},
        "approval": {"policyVersion": "final-policy-1", "decision": "ALLOW_SANITIZED", "finalReportId": "validation-x-1"},
        "signature": {"originalSigned": False, "viewingCopySigned": False}, "integrations": {},
        "createdAt": NOW, "updatedAt": NOW,
        "textExtraction": {key: summary["invoice-en"][key] for key in ("method", "source", "engine", "pages", "characters", "truncated")}}
errors = schema_errors({**base, **f, "schemaVersion": "1.0.0", "relativePdfPath": "x.pdf", "integrity": {"sha256": digest, "bytes": 1}})
check("organised sidecar valid", not errors, errors)
published = archive.publish(pdf, digest, {**base, **f}, text=text("invoice-en"))
check("invoice published to deterministic path", published.relative_to(archive.archive).as_posix()
      == "Finance/2026/Invoices/2026-10-15 - Example Telecom - Internet Invoice - October 2026 [doc-01J000000000000000000000G1]", published)
fallback = organise(text("unfamiliar"), rules, taxonomy).fields
path = archive.publish(DATA / "fixtures" / "unfamiliar.pdf", hashlib.sha256((DATA / "fixtures" / "unfamiliar.pdf").read_bytes()).hexdigest(),
                       {**base, **fallback, "documentId": "doc-01J000000000000000000000G2"}, text=text("unfamiliar"))
check("unfamiliar document published to Unsorted fallback", path.relative_to(archive.archive).as_posix()
      == "Unsorted/2026/undated - Unclassified document [doc-01J000000000000000000000G2]", path)
check("content.txt keeps hostile text as inert data", "<script>" in (published / "content.txt").read_text()
      and archive.verify()["ok"])
print("M4 organisation tests passed")
