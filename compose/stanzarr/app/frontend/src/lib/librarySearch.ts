/**
 * Where the library was left (its filters, sort and page, as the address
 * has them), so a song's page leads back there and not to a fresh library.
 */
const KEY = "stanzarr:library-search";

export function rememberLibrary(search: string) {
  try {
    sessionStorage.setItem(KEY, search);
  } catch {
    // private window: back leads to the whole library
  }
}

export function libraryLink(): string {
  try {
    const search = sessionStorage.getItem(KEY);
    return search ? `/library${search.startsWith("?") ? search : `?${search}`}` : "/library";
  } catch {
    return "/library";
  }
}

/** A setting of this browser (how the table looks, how many a page). */
export function stored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function store(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // not remembered
  }
}
