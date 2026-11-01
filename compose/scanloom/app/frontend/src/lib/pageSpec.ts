/** Which pages, as people write it: 1-3,6,7-9. The same rules as the server's (backend/scanloom/pagespec.py). */
export type PageSpec = { pages: number[]; error?: undefined } | { pages?: undefined; error: string };

export function parsePages(spec: string, total: number): PageSpec {
  const pages: number[] = [];
  const parts = spec.split(",").map((part) => part.trim());
  if (!parts.some(Boolean)) return { error: "Say which pages, like 1-3,6,7-9" };
  for (const part of parts) {
    if (!part) continue;
    const match = /^(\d{1,6})(?:\s*-\s*(\d{1,6}))?$/.exec(part);
    if (!match) return { error: `\u201c${part}\u201d isn't a page or a range: write them like 1-3,6,7-9` };
    const first = Number(match[1]);
    const last = match[2] ? Number(match[2]) : first;
    for (const number of [first, last]) {
      if (number < 1 || number > total) return { error: `There is no page ${number}: this scan has pages 1 to ${total}` };
    }
    const step = last >= first ? 1 : -1;
    for (let number = first; step > 0 ? number <= last : number >= last; number += step) {
      if (!pages.includes(number)) pages.push(number);
    }
  }
  return { pages };
}

/** 1,2,3,6,7,8,9 → "1–3, 6–9": what a list comes to, in a line. */
export function describePages(pages: number[]): string {
  const runs: string[] = [];
  for (let i = 0; i < pages.length; ) {
    let j = i;
    while (j + 1 < pages.length && pages[j + 1] === pages[j] + 1) j += 1;
    runs.push(j > i ? `${pages[i]}\u2013${pages[j]}` : String(pages[i]));
    i = j + 1;
  }
  return runs.join(", ");
}
