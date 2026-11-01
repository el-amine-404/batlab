"""Which pages, as people write it: 1-3,6,7-9."""
from __future__ import annotations

import re

_PART = re.compile(r"^(\d{1,6})(?:\s*-\s*(\d{1,6}))?$")


def parse_pages(spec: str, total: int) -> list[int]:
    """The page numbers, in the order written. A range may run backwards (9-7 is 9, 8, 7);
    a page named twice comes where it was first named. Anything else is a ValueError
    whose message says which part is wrong. Same rules: frontend/src/lib/pageSpec.ts."""
    pages: list[int] = []
    seen: set[int] = set()
    parts = [part.strip() for part in spec.split(",")]
    if not any(parts):
        raise ValueError("Say which pages, like 1-3,6,7-9")
    for part in parts:
        if not part:
            continue  # a stray comma is nothing to refuse
        match = _PART.match(part)
        if not match:
            raise ValueError(f"\u201c{part}\u201d isn't a page or a range: write them like 1-3,6,7-9")
        first = int(match[1])
        last = int(match[2]) if match[2] else first
        for number in (first, last):
            if not 1 <= number <= total:
                raise ValueError(f"There is no page {number}: this scan has pages 1 to {total}")
        step = 1 if last >= first else -1
        for number in range(first, last + step, step):
            if number not in seen:
                seen.add(number)
                pages.append(number)
    return pages
