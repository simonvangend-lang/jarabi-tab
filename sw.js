const CACHE = 'jarabi-v88';
const LOCAL = ['./', './index.html', './scores/scores.json', './manifest.json', './icon.svg'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(LOCAL)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network-first for HTML + notes.json (so edits/code updates appear immediately),
// cache-first for everything else local; CDN traffic passes through.
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return;

  const isDynamic = url.pathname.endsWith('index.html')
                 || url.pathname.endsWith('jarabi.html')   // legacy URL still works for old clients
                 || url.pathname.endsWith('notes.json')    // legacy
                 || /\/scores\/.+\.json$/.test(url.pathname)
                 || url.pathname === '/' || url.pathname.endsWith('/');

  if (isDynamic) {
    // Network-first
    e.respondWith(
      fetch(e.request).then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      }).catch(() => caches.match(e.request))
    );
  } else {
    // Cache-first
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request).then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      }))
    );
  }
});
