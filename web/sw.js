// VELORA staff dashboard service worker (P9-T1, P9-T7).
//
// It keeps a copy of the app's own files so the dashboard can open, and say "connection
// lost", without a network. It never touches /api/: no order, chat or customer data is
// cached, and no action is ever stored for later. An action without a connection fails,
// and the page says so.

const CACHE = "velora-shell-v1";
const SHELL = [
  "/",
  "/manifest.webmanifest",
  "/assets/app.css",
  "/assets/js/main.js",
  "/assets/js/api.js",
  "/assets/js/dom.js",
  "/assets/js/format.js",
  "/assets/js/ui.js",
  "/assets/js/views/board.js",
  "/assets/js/views/payment.js",
  "/assets/js/views/payments.js",
  "/assets/js/views/chats.js",
  "/assets/js/views/menu.js",
  "/assets/js/views/controls.js",
  "/assets/js/views/summary.js",
  "/assets/js/views/whatsapp.js",
  "/assets/js/views/staff.js",
  "/assets/js/views/settings.js",
  "/assets/js/views/me.js",
  "/assets/icons/icon.svg",
  "/assets/icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return; // always the network, never a cache
  // The app's files: the network first (so updates arrive), the saved copy when offline.
  const key = url.pathname === "/login" ? "/" : url.pathname;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok && SHELL.includes(key)) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(key, copy));
        }
        return response;
      })
      .catch(() => caches.match(key).then((hit) => hit || Response.error())),
  );
});
