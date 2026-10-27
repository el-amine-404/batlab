"""Identity cards and passports, read from their machine-readable zone (ICAO 9303).

The zone is the two or three lines of capitals and '<' at the bottom of a card or a
passport's photo page. OCR reads it well (a plain monospaced font), and it carries
check digits: a zone is used only when the number, the birth date, the expiry and
the whole (composite) check digits all agree, so a misread letter is refused rather
than filed. Nothing here guesses: what the zone doesn't say stays empty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# The issuing state, as people name it (the document's own language where it has one).
STATES = {
    "MAR": "Royaume du Maroc", "FRA": "République française", "BEL": "Belgique", "ESP": "Reino de España",
    "DEU": "Deutschland", "D<<": "Deutschland", "ITA": "Repubblica Italiana", "NLD": "Nederland", "PRT": "Portugal",
    "CHE": "Schweiz", "GBR": "United Kingdom", "USA": "United States", "CAN": "Canada", "DZA": "Algérie",
    "TUN": "Tunisie", "SEN": "Sénégal",
}
FRENCH = {"MAR", "FRA", "BEL", "DZA", "TUN", "SEN", "CHE"}
# OCR's usual confusions, undone only where the zone wants a digit.
DIGITS = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7"})
WEIGHTS = (7, 3, 1)


def check_digit(field: str) -> str:
    total = 0
    for index, char in enumerate(field):
        value = 0 if char == "<" else int(char) if char.isdigit() else ord(char) - 55
        total += value * WEIGHTS[index % 3]
    return str(total % 10)


@dataclass(frozen=True)
class Zone:
    kind: str              # "ID" or "PASSPORT"
    state: str             # issuing state, ICAO code (MAR)
    number: str            # the document's number (MRZ)
    national_id: str | None  # the person's national number when the card carries it (Morocco: AD320585)
    surname: str
    given_names: str
    birth: date
    sex: str | None
    expiry: date
    lines: tuple[str, ...]

    @property
    def holder(self) -> str:
        return " ".join(part.capitalize() for part in f"{self.given_names} {self.surname}".split())

    @property
    def issuer(self) -> str:
        return STATES.get(self.state, self.state.replace("<", ""))

    @property
    def title(self) -> str:
        french = self.state in FRENCH
        if self.kind == "PASSPORT":
            return f"{'Passeport' if french else 'Passport'} - {self.holder}"
        return f"{'Carte nationale d’identité' if french else 'Identity card'} - {self.holder}"

    @property
    def reference(self) -> str:
        return self.national_id or self.number


def _chars(line: str) -> str:
    """A text line as zone characters: capitals, digits and '<' (OCR reads '<' as « or <<)."""
    line = line.upper().replace("«", "<").replace("‹", "<").replace("〈", "<")
    return re.sub(r"[^A-Z0-9<]", "", line)


def _quiet(segment: str) -> str:
    """A stretch that should be filler: mostly '<' with a stray letter or two becomes all '<'."""
    return "<" * len(segment) if segment.count("<") >= 0.6 * len(segment) else segment


def _fit(text: str, length: int) -> str:
    """Pad or trim filler at the end so a line has its fixed length."""
    if len(text) < length:
        return text + "<" * (length - len(text))
    while len(text) > length and text.endswith("<"):
        text = text[:-1]
    return text


def _date(digits: str, future: bool) -> date | None:
    try:
        year, month, day = int(digits[:2]), int(digits[2:4]), int(digits[4:6])
        today = date.today()
        century = 2000 if future or year <= today.year % 100 else 1900
        return date(century + year, month, day)
    except ValueError:
        return None


def _numeric(field: str) -> str:
    return field.translate(DIGITS)


def _names(line: str) -> tuple[str, str] | None:
    line = re.sub(r"<{3,}.*$", "", line.rstrip("<") + "<<<")  # the names end where the long filler starts
    surname, _, given = line.partition("<<")
    surname = surname.replace("<", " ").strip()
    given = " ".join(part for part in given.split("<") if part)
    if not re.fullmatch(r"[A-Z ]{2,60}", surname) or not re.fullmatch(r"[A-Z ]{0,60}", given):
        return None
    return surname, given.strip()


def _td1(l1: str, l2: str, l3: str) -> Zone | None:
    """Cards: three lines of 30."""
    if not re.match(r"^[ACI][A-Z<][A-Z<]{3}", l1):
        return None
    l1 = _fit(l1[:15] + _quiet(l1[15:]), 30)
    head = _numeric(l2[:7]) + l2[7] + _numeric(l2[8:15]) + l2[15:18]
    l2 = _fit(head + _quiet(l2[18:-1]) if len(l2) > 19 else head, 29)[:29] + _numeric(l2[-1:])
    if len(l1) != 30 or len(l2) != 30:
        return None
    number, number_check = l1[5:14], l1[14]
    birth, birth_check, sex, expiry, expiry_check = l2[0:6], l2[6], l2[7], l2[8:14], l2[14]
    composite = l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]
    if not (check_digit(number) == number_check and check_digit(birth) == birth_check
            and check_digit(expiry) == expiry_check and check_digit(composite) == l2[29]):
        return None
    names = _names(l3)
    born, ends = _date(birth, False), _date(expiry, True)
    if names is None or born is None or ends is None:
        return None
    optional = l1[15:30].strip("<")
    national = optional if re.fullmatch(r"[A-Z]{1,3}[0-9]{4,10}", optional) else None
    return Zone("PASSPORT" if l1[0] == "P" else "ID", l1[2:5], number.strip("<"), national, names[0], names[1], born,
                sex if sex in "MF" else None, ends, (l1, l2, l3))


def _td3(l1: str, l2: str) -> Zone | None:
    """Passports: two lines of 44."""
    if not l1.startswith("P"):
        return None
    l1 = _fit(l1, 44)
    head = l2[:9] + _numeric(l2[9:10]) + l2[10:13] + _numeric(l2[13:20]) + l2[20] + _numeric(l2[21:28])
    l2 = _fit(head + l2[28:-2], 42)[:42] + _numeric(l2[-2:])
    if len(l1) != 44 or len(l2) != 44:
        return None
    number, birth, expiry = l2[0:9], l2[13:19], l2[21:27]
    composite = l2[0:10] + l2[13:20] + l2[21:43]
    if not (check_digit(number) == l2[9] and check_digit(birth) == l2[19] and check_digit(expiry) == l2[27]
            and check_digit(composite) == l2[43]):
        return None
    names = _names(l1[5:])
    born, ends = _date(birth, False), _date(expiry, True)
    if names is None or born is None or ends is None:
        return None
    return Zone("PASSPORT", l1[2:5], number.strip("<"), None, names[0], names[1], born,
                l2[20] if l2[20] in "MF" else None, ends, (l1, l2))


def zones(text: str) -> list[Zone]:
    """Every zone in the text whose check digits all agree (OCR passes may each hold one)."""
    lines = [_chars(line) for line in (text or "").splitlines()]
    lines = [line for line in lines if len(line) >= 25 and line.count("<") >= 2]
    found = []
    for index in range(len(lines)):
        zone = (_td1(*lines[index:index + 3]) if index + 2 < len(lines) else None) or \
               (_td3(*lines[index:index + 2]) if index + 1 < len(lines) else None)
        if zone:
            found.append(zone)
    return found


def read(text: str) -> Zone | None:
    """The document's zone; None when no zone's check digits agree.

    The names carry no check digit, so when OCR read the zone more than once (two
    passes) and the spellings differ, the one the rest of the document agrees with
    wins: the card prints the name on its front too."""
    found = zones(text)
    if not found:
        return None
    words = re.findall(r"[A-Z]{2,}", re.sub(r"[^A-Z\n]+", " ", (text or "").upper()))

    def support(zone: Zone) -> int:
        names = f"{zone.surname} {zone.given_names}".split()
        return sum(words.count(name) for name in names) + sum(other.holder == zone.holder for other in found)
    return max(found, key=support)


def refiled(zone: Zone) -> dict:
    """The changes that re-file a published document from its zone: its fields, and no
    document date or amount (a date read before was likely the birth date)."""
    return {**fields(zone), "documentDate": None, "amount": None}


def fields(zone: Zone) -> dict:
    """The sidecar fields an identity document gets from its zone."""
    evidence = " / ".join(zone.lines)[:500]
    rule = {"source": "rule", "reviewState": "UNVERIFIED", "evidence": evidence}
    return {
        "title": zone.title[:200], "titleSource": "rule",
        "organization": {"category": "Identity", "reviewState": "RULE", "folder": "Identity",
                         "reason": "read from its machine-readable zone; every check digit agrees"},
        "documentType": "identity_document",
        "issuer": {"value": zone.issuer, **rule},
        "holder": {"value": zone.holder[:200], **rule},
        "reference": {"value": zone.reference, **rule},
        "validUntil": {"value": zone.expiry.isoformat(), **rule},
        "language": ["fr" if zone.state in FRENCH else "en"],
    }
