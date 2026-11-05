/*
 * sw.js — service worker.
 *
 * PENTING: index.html TIDAK BOLEH di-cache.
 * Cache shell + service worker = browser/presentation diam-diam memakai
 * index.html lama selamanya, padahal file di server sudah diperbarui.
 * Gejalanya: perubahan UI tidak terlihat meski sudah hard refresh.
 *
 * Strategi:
 *   - navigasi (HTML)  -> network-only, fallback ke cache hanya saat offline
 *   - aset statis lain  -> stale-while-revalidate
 *   - /api/ & /relay   -> biarkan browser (selalu butuh data fresh)
 */
const VERSION = "animehub-v3";
const ASSETS = ["/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(VERSION)
      .then((c) => c.addAll(ASSETS).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Data & video: selalu langsung ke jaringan.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/relay")) return;

  // Navigasi (dokumen HTML): NETWORK FIRST, jangan pernah cache.
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req).catch(() => caches.match("/index.html"))
    );
    return;
  }

  // Aset statis: cache dulu, lalu perbarui di belakang.
  e.respondWith(
    caches.match(req).then((hit) => {
      const net = fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      }).catch(() => hit);
      return hit || net;
    })
  );
});
