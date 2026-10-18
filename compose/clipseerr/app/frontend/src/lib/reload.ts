/**
 * A page opened before clipseerr was updated asks for code files the update
 * replaced (their names carry a hash), and gets a 404. Reloading fetches the
 * new page and its files. Once a minute at most, so a file truly missing
 * can't make the page reload forever.
 */
const KEY = "reloaded-for-update";

export function isStaleCode(error: unknown): boolean {
  const text = error instanceof Error ? `${error.name} ${error.message}` : String(error);
  return /dynamically imported module|Importing a module script failed|Failed to fetch dynamically|error loading dynamically|ChunkLoadError/i.test(text);
}

/** Reloads for the new version; false when it just did, and the error is real. */
export function reloadForUpdate(): boolean {
  let last = 0;
  try {
    last = Number(sessionStorage.getItem(KEY) ?? 0);
    if (Date.now() - last < 60_000) return false;
    sessionStorage.setItem(KEY, String(Date.now()));
  } catch {
    // no storage: reload anyway, once per page load at most by the browser's own course
  }
  window.location.reload();
  return true;
}

// Vite says so when a page's code fails to load ahead of use.
window.addEventListener("vite:preloadError", (event) => {
  if (reloadForUpdate()) event.preventDefault();
});
