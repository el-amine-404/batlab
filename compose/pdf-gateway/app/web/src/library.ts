import { useQuery } from "@tanstack/react-query";
import { api, type Document, type Scan } from "./api";

// The person's archive as the sidebar, the browse sheet and the attention list see it.

export type Tree = { section: string; count: number; correspondents: { name: string; count: number }[] }[];

const SECTION_ORDER = ["Finance", "Employment", "Identity", "Medical", "Education", "Contracts", "Property", "Vehicles",
  "Taxes", "Personal", "Other", "Unsorted"];

/** Sections in the archive's order, each with its correspondents (most documents first). */
export function tree(documents: Document[]): Tree {
  const sections = new Map<string, Map<string, number>>();
  for (const doc of documents) {
    const names = sections.get(doc.category) ?? new Map<string, number>();
    const from = doc.issuer?.trim();
    if (from) names.set(from, (names.get(from) ?? 0) + 1);
    sections.set(doc.category, names);
  }
  return [...sections.entries()]
    .sort(([a], [b]) => (SECTION_ORDER.indexOf(a) + 100 * +!SECTION_ORDER.includes(a)) - (SECTION_ORDER.indexOf(b) + 100 * +!SECTION_ORDER.includes(b)))
    .map(([section, names]) => ({
      section, count: documents.filter((doc) => doc.category === section).length,
      correspondents: [...names.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([name, count]) => ({ name, count })),
    }));
}

export function useLibrary() {
  return useQuery({ queryKey: ["documents"], queryFn: api.allDocuments, staleTime: 15_000 });
}

/** Unfiled: nothing (no rule, no AI, no person) gave it a section. */
export function unfiled(doc: Document) {
  return doc.category === "Unsorted" || doc.reviewState === "FALLBACK" || doc.reviewState === "REVIEW_REQUIRED";
}

/** What needs the person: files held or blocked or that failed, and documents nobody filed. */
export function useAttention() {
  const library = useLibrary();
  const held = useQuery({ queryKey: ["quarantine"], queryFn: api.quarantine, staleTime: 15_000 });
  const heldItems: Scan[] = held.data?.items ?? [];
  const unfiledItems = (library.data ?? []).filter(unfiled);
  return { held: heldItems, unfiled: unfiledItems, count: heldItems.length + unfiledItems.length, loading: library.isPending || held.isPending,
    error: library.error ?? held.error };
}
