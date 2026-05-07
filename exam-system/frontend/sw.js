// Service Worker — ExamLAN
// Network-first for everything: no stale cache issues

const CACHE_NAME = 'examlan-v3';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.map(k => caches.delete(k)))   // wipe all old caches
    ).then(() => self.clients.claim())
  );
});

// Pure network-first — no caching of JS/HTML to avoid stale file bugs
self.addEventListener('fetch', e => {
  // Skip non-GET and WebSocket / API requests entirely
  if (e.request.method !== 'GET') return;
  if (e.request.url.includes('/api/') || e.request.url.includes('/ws/')) return;

  // Network-first: try network, fall back to cache only as last resort
  e.respondWith(
    fetch(e.request)
      .then(res => {
        // Cache successful responses for offline fallback
        if (res.ok) {
          const clone = res.clone();
          caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        }
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
