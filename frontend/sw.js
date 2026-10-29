// Service worker: cache the static SPA shell so repeat page loads are instant.
// API responses are never cached (live data must always be fresh).
// Bump VERSION whenever the static file list changes to force a clean precache.
const VERSION = "ucap-v6";
const STATIC_CACHE = `ucap-static-${VERSION}`;
const SHELL_CACHE = `ucap-shell-${VERSION}`;

const PRECACHE = [
  "/",
  "/index.html",
  "/css/styles.css",
  "/css/assistant.css",
  "/vendor/gridstack.min.css",
  "/vendor/gridstack-all.js",
  "/js/app.js",
  "/js/ai-assistant.js",
  "/js/api.js",
  "/js/store.js",
  "/js/ui.js",
  "/js/widgets.js",
  "/js/views/activate.js",
  "/js/views/apps.js",
  "/js/views/board.js",
  "/js/views/catalog.js",
  "/js/views/commission.js",
  "/js/views/connection.js",
  "/js/views/dashboard.js",
  "/js/views/device.js",
  "/js/views/graphs.js",
  "/js/views/login.js",
  "/js/views/mapping.js",
  "/js/views/purpose.js",
  "/js/views/report.js",
  "/js/views/rules.js",
  "/js/views/stepper.js",
  "/js/views/template.js",
];

const STATIC_PREFIXES = ["/js/", "/css/", "/vendor/"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(STATIC_CACHE);
      const shell = await caches.open(SHELL_CACHE);
      await Promise.all(
        PRECACHE.map(async (url) => {
          try {
            const res = await fetch(new Request(url, { cache: "reload" }));
            if (res.ok) {
              await cache.put(url, res.clone());
              if (url === "/index.html" || url === "/") await shell.put("/index.html", res);
            }
          } catch {
            /* offline install: keep whatever the HTTP cache already has */
          }
        })
      );
      await self.skipWaiting();
    })()
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keep = new Set([STATIC_CACHE, SHELL_CACHE]);
      const names = await caches.keys();
      await Promise.all(names.filter((name) => !keep.has(name)).map((name) => caches.delete(name)));
      await self.clients.claim();
    })()
  );
});

async function staleWhileRevalidate(event, request) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((res) => {
      if (res && res.ok) cache.put(request, res.clone());
      return res;
    })
    .catch(() => null);
  if (cached) {
    // Refresh in the background so the next load picks up changes.
    event.waitUntil(network);
    return cached;
  }
  const fresh = await network;
  return fresh || Response.error();
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/sw.js") return;

  // Navigations: always try the network first so UI changes appear immediately,
  // fall back to the cached shell when offline.
  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const fresh = await fetch(request);
          if (fresh && fresh.ok) {
            const shell = await caches.open(SHELL_CACHE);
            shell.put("/index.html", fresh.clone());
          }
          return fresh;
        } catch {
          const shell = await caches.open(SHELL_CACHE);
          return (await shell.match("/index.html")) || (await shell.match("/")) || Response.error();
        }
      })()
    );
    return;
  }

  if (STATIC_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) {
    // JS changes often during commissioning: prefer the network so a reload
    // picks up fixes immediately, and fall back to the cached copy offline.
    if (url.pathname.startsWith("/js/")) {
      event.respondWith(
        (async () => {
          try {
            const fresh = await fetch(request);
            if (fresh && fresh.ok) {
              const cache = await caches.open(STATIC_CACHE);
              cache.put(request, fresh.clone());
            }
            return fresh;
          } catch {
            return (await caches.match(request)) || Response.error();
          }
        })()
      );
      return;
    }
    event.respondWith(staleWhileRevalidate(event, request));
  }
});
