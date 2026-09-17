const CACHE = 'nuun-shell-v2';
const SHELL = [
  '/static/css/style.css',
  '/static/css/dashboard.css',
  '/static/js/main.js',
  '/static/js/dashboard.js',
  '/static/images/logo.png',
  '/static/images/icon-192.png'
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith('/admin')) return;
  if (url.pathname.startsWith('/webhook')) return;

  if (req.destination === 'document') {
    e.respondWith(fetch(req).catch(() => caches.match('/offline.html')));
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

// ═══════════════════════════════════════════════════════════════════
//  WEB PUSH
// ═══════════════════════════════════════════════════════════════════

self.addEventListener('push', function (event) {
  // Default fallback if the payload is missing or malformed.
  var data = {
    title: 'NuunPlatform',
    body: 'You have a new notification.',
    url: '/',
  };

  try {
    if (event.data) data = event.data.json();
  } catch (e) {
    // Keep the fallback.
  }

  var options = {
    body: data.body || '',
    icon: data.icon || '/static/images/icon-192.png',
    badge: '/static/images/icon-192.png',
    tag: data.tag || 'nuun-default',
    data: {
      url: data.url || '/',
      extra: data.data || {},
    },
    vibrate: [80, 40, 80],
  };

  event.waitUntil(
    self.registration.showNotification(
      data.title || 'NuunPlatform',
      options
    )
  );
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();

  var data = event.notification.data || {};
  var target = data.url || '/';

  event.waitUntil(
    clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then(function (list) {
        // Focus an existing tab on our origin and navigate it.
        for (var i = 0; i < list.length; i++) {
          var c = list[i];
          if (c.url.indexOf(self.location.origin) === 0) {
            c.focus();
            if ('navigate' in c) return c.navigate(target);
            return;
          }
        }
        // Otherwise open a new window.
        if (clients.openWindow) return clients.openWindow(target);
      })
  );
});