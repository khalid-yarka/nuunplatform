const CACHE = 'nuun-shell-v1';
const SHELL = [
  '/static/css/style.css',
  '/static/css/dashboard.css',
  '/static/js/main.js',
  '/static/js/dashboard.js',
  '/static/images/logo.png'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;   // don't touch Telegram/Telegram CDN
  if (url.pathname.startsWith('/admin')) return; // never cache admin
  if (url.pathname.startsWith('/webhook')) return;

  // Network-first for HTML, cache-first for static assets.
  if (req.destination === 'document') {
    e.respondWith(
      fetch(req).catch(() => caches.match('/offline.html'))
    );
    return;
  }
  e.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(req, copy));
      return res;
    }))
  );
});