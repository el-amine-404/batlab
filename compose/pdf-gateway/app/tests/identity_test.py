#!/usr/bin/env python3
"""Identity documents read from their machine-readable zone (archive-worker/identity.py).

ICAO 9303's own specimens, and a made-up Moroccan card as Tesseract returns real
ones: filler read as letters or «, spaces, an extra letter, the zone after other
text. A misread digit must be refused, never filed."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "archive-worker"))
import identity  # noqa: E402
import organize  # noqa: E402
from archive import schema_errors  # noqa: E402

failures = []


def check(name, condition, detail=""):
    print(("ok - " if condition else "FAIL - ") + name + ("" if condition else f": {detail}"))
    if not condition:
        failures.append(name)


# ICAO 9303 part 5 (TD1) and part 4 (TD3) specimens.
td1 = identity.read("I<UTOD231458907<<<<<<<<<<<<<<<\n7408122F1204159UTO<<<<<<<<<<<6\nERIKSSON<<ANNA<MARIA<<<<<<<<<<")
check("ICAO card specimen", td1 is not None and td1.number == "D23145890" and td1.surname == "ERIKSSON"
      and td1.given_names == "ANNA MARIA" and td1.birth == date(1974, 8, 12) and td1.expiry == date(2012, 4, 15), td1)
td3 = identity.read("P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159ZE184226B<<<<<10")
check("ICAO passport specimen", td3 is not None and td3.kind == "PASSPORT" and td3.number == "L898902C3"
      and td3.holder == "Anna Maria Eriksson", td3)

# A made-up Moroccan card (check digits computed), as OCR returns it around the card's other text.
CLEAN = ["IDMARXYZABC12<6BK123456<<<<<<<", "9507041F3302152MAR<<<<<<<<<<<0", "EXEMPLE<<SALMA<<<<<<<<<<<<<<<<"]
NOISY = f"""ROYAUME DU MAROC CARTE NATIONALE D'IDENTITE
N° BK123456   Valable jusqu'au 15.02.2033
Adresse 12 RUE EXEMPLE RABAT
{CLEAN[0]}
9507041F3302152MAR<<<<<<<<<L<0
EXEMPLE<<SALMA<<<K<<<<<<<<<<«<<"""
card = identity.read(NOISY)
check("Moroccan card through OCR noise", card is not None, NOISY)
if card:
    check("its holder, issuer and national number", card.holder == "Salma Exemple" and card.issuer == "Royaume du Maroc"
          and card.reference == "BK123456" and card.expiry == date(2033, 2, 15) and card.sex == "F", card)
    check("a French title for a Moroccan card", card.title == "Carte nationale d’identité - Salma Exemple", card.title)
twice = NOISY + "\n" + "\n".join([CLEAN[0], CLEAN[1], "EXEMPLLE<<SALMA<<<<<<<<<<<<<<<"])
check("two readings that differ in the name (no check digit there): the one the front agrees with",
      identity.read(twice) is not None and identity.read(twice).holder == "Salma Exemple", [z.holder for z in identity.zones(twice)])
check("spaces inside the zone", identity.read("\n".join(" ".join(line[i:i + 5] for i in range(0, 30, 5)) for line in CLEAN)) is not None)

# Misreads the check digits catch: refused, not guessed.
check("a changed birth date is refused", identity.read("\n".join([CLEAN[0], "9507051F3302152MAR<<<<<<<<<<<0", CLEAN[2]])) is None)
check("an extra letter in the number is refused", identity.read("\n".join(["IDMARXYZABCB12<6BK123456<<<<<<", CLEAN[1], CLEAN[2]])) is None)
check("a wrong composite is refused", identity.read("\n".join([CLEAN[0], CLEAN[1][:-1] + "1", CLEAN[2]])) is None)
check("ordinary text has no zone", identity.read("Facture No 88123\nTotal TTC MAD 1 499,00\nIBAN MA64 0000 1111") is None)

# Filed by the rules: Identity › the holder, from the issuing state, valid until; a valid sidecar.
fields = organize.organise(NOISY, {"rules": []}, organize.default_taxonomy("2026-10-27T00:00:00Z")).fields
check("filed as an identity document", fields["organization"]["category"] == "Identity" and fields["organization"]["reviewState"] == "RULE"
      and fields["issuer"]["value"] == "Royaume du Maroc" and fields["holder"]["value"] == "Salma Exemple"
      and fields["validUntil"]["value"] == "2033-02-15" and fields["reference"]["value"] == "BK123456", fields)
sidecar = {"schemaVersion": "1.0.0", "documentId": "doc-06GQAP05HY940TKCWRVQMR8G58", "artifactVersion": 1, "metadataRevision": 1,
           "relativePdfPath": "x.pdf", "integrity": {"sha256": "a" * 64, "bytes": 1, "algorithm": "sha256"},
           "derivedFrom": {}, "approval": {}, "signature": {}, "integrations": {}, "createdAt": "2026-10-27T00:00:00Z",
           "updatedAt": "2026-10-27T00:00:00Z", **fields}
errors = [error for error in schema_errors(sidecar) if any(key in error for key in ("holder", "validUntil", "issuer", "reference", "language", "organization"))]
check("the new fields fit the sidecar schema", not errors, errors)

if failures:
    sys.exit(f"{len(failures)} failed")
print("identity: all passed")
