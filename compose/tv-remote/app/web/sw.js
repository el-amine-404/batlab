"use strict";

// Opens the page at once, even on weak Wi-Fi. Only the page's own files are kept:
// never /api/, so nothing about the TV or the login is stored.
const CACHE = "tv-remote-shell-v1";
const NEVER = ["/api/", "/reload", "/healthz", "/ca.crt", "/sw.js"];
const WAIT_FOR_NETWORK = 800; // ms before a saved page is shown instead

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
  await self.clients.claim();
})()));

async function keep(request, response) {
  if (!response.ok) return response;
  const cache = await caches.open(CACHE);
  const url = new URL(request.url);
  if (url.search.startsWith("?v=")) { // an older version of this file is no use now
    for (const old of await cache.keys()) {
      const o = new URL(old.url);
      if (o.pathname === url.pathname && o.search !== url.search) await cache.delete(old);
    }
  }
  await cache.put(request, response.clone());
  return response;
}

// The page: the network if it is quick, so a new version shows; else the saved one.
async function page(request) {
  const network = fetch(request).then((r) => keep(request, r));
  const saved = await caches.match(request);
  if (!saved) return network;
  const timeout = new Promise((done) => setTimeout(() => done(saved), WAIT_FOR_NETWORK));
  return Promise.race([network.catch(() => saved), timeout]);
}

// A versioned file never changes.
async function asset(request) {
  return (await caches.match(request)) || fetch(request).then((r) => keep(request, r));
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (NEVER.some((p) => url.pathname.startsWith(p))) return;
  if (e.request.mode === "navigate") e.respondWith(page(e.request));
  else if (url.search.startsWith("?v=")) e.respondWith(asset(e.request));
});
