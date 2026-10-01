/* ============================================================
   static/sw.js — NuunPlatform service worker
   Strategy:
     • Navigations (HTML)  → network-first, offline fallback
     • /static/* assets    → stale-while-revalidate
     • /admin, /webhook,   → bypass entirely (never cached)
       /api, /backup, etc.
     • Everything else     → network-with-cache-fallback
   Bump CACHE_VERSION on any release that must reach old clients.
   ============================================================ */

const CACHE_VERSION = 'nuun-v3-20261002';
const SHELL_CACHE   = 'nuun-shell-' + CACHE_VERSION;
const RUNTIME_CACHE = 'nuun-runtime-' + CACHE_VERSION;

// The shell is small. Everything else is cached lazily at runtime.
const SHELL_ASSETS = [
    '/offline.html',
    '/manifest.json',
    '/static/images/logo.png',
    '/static/images/icon-192.png',
    '/static/images/icon-512.png',
];

/* ---------- install ---------- */
self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(SHELL_CACHE).then(function (cache) {
            // Tolerate individual failures — a missing icon must not
            // block the SW from installing.
            return Promise.all(
                SHELL_ASSETS.map(function (url) {
                    return cache.add(url).catch(function () { /* skip */ });
                })
            );
        }).then(function () {
            // Activate as soon as possible. Registration code will
            // reload open clients once they receive controllerchange.
            return self.skipWaiting();
        })
    );
});

/* ---------- activate ---------- */
self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(
                keys
                    .filter(function (k) {
                        // Keep only the current two caches.
                        return k.startsWith('nuun-')
                            && k !== SHELL_CACHE
                            && k !== RUNTIME_CACHE;
                    })
                    .map(function (k) { return caches.delete(k); })
            );
        }).then(function () {
            // Take control of every open tab so the reload happens now,
            // not on the next open.
            return self.clients.claim();
        })
    );
});

/* ---------- message ---------- */
self.addEventListener('message', function (event) {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
    if (event.data && event.data.type === 'GET_VERSION') {
        event.source && event.source.postMessage({
            type: 'VERSION',
            version: CACHE_VERSION
        });
    }
});

/* ---------- fetch ---------- */
function _shouldBypass(pathname) {
    // Endpoints that must always hit the live server, never the cache.
    return (
        pathname.startsWith('/admin') ||
        pathname.startsWith('/webhook') ||
        pathname.startsWith('/telegram') ||
        pathname.startsWith('/backup') ||
        pathname.startsWith('/api/') ||
        pathname === '/health' ||
        pathname === '/maintenance' ||
        pathname === '/sw.js'
    );
}

self.addEventListener('fetch', function (event) {
    var req = event.request;

    // Only GET is cacheable.
    if (req.method !== 'GET') return;

    var url;
    try { url = new URL(req.url); } catch (e) { return; }

    // Same-origin only. Cross-origin (Telegram, CDN) passes straight through.
    if (url.origin !== self.location.origin) return;

    if (_shouldBypass(url.pathname)) return;

    // ── Navigation requests: network-first ──
    var isNav = (req.mode === 'navigate')
             || (req.headers.get('accept') || '').includes('text/html');

    if (isNav) {
        event.respondWith(
            fetch(req)
                .then(function (res) {
                    // Cache a fresh copy for offline use.
                    var copy = res.clone();
                    caches.open(RUNTIME_CACHE).then(function (c) {
                        c.put(req, copy).catch(function () {});
                    });
                    return res;
                })
                .catch(function () {
                    return caches.match(req).then(function (cached) {
                        return cached || caches.match('/offline.html');
                    });
                })
        );
        return;
    }

    // ── Static assets: stale-while-revalidate ──
    if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.json') {
        event.respondWith(
            caches.open(RUNTIME_CACHE).then(function (cache) {
                return cache.match(req).then(function (cached) {
                    var network = fetch(req).then(function (res) {
                        if (res && res.status === 200 && res.type === 'basic') {
                            cache.put(req, res.clone()).catch(function () {});
                        }
                        return res;
                    }).catch(function () { return cached; });
                    // Return cached immediately if present, but keep the
                    // network fetch alive so the cache refreshes.
                    return cached || network;
                });
            })
        );
        return;
    }

    // ── Everything else: network with cache fallback ──
    event.respondWith(
        fetch(req).catch(function () {
            return caches.match(req).then(function (cached) {
                return cached || Response.error();
            });
        })
    );
});

/* ============================================================
   NOTIFICATION CLICK HANDLER
   ============================================================
   Fires when a user taps a notification created via
   `reg.showNotification()`. Looks for an existing tab at this
   origin, focuses it, and navigates it to the URL stored in
   `event.notification.data.url`. Falls back to opening a new
   window when no same-origin tab exists.

   Without this handler, tapping a service-worker-created
   notification simply dismisses it — nothing opens.
   ============================================================ */
self.addEventListener('notificationclick', function (event) {
    event.notification.close();

    var targetUrl = '/';
    try {
        if (event.notification.data && event.notification.data.url) {
            targetUrl = event.notification.data.url;
        }
    } catch (e) { /* keep default */ }

    // Resolve target to an absolute URL.
    var absoluteUrl;
    try {
        absoluteUrl = new URL(targetUrl, self.location.origin).href;
    } catch (e) {
        absoluteUrl = self.location.origin + '/';
    }

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(function (windowClients) {

                // 1. Prefer an existing window already at the target path.
                for (var i = 0; i < windowClients.length; i++) {
                    var c = windowClients[i];
                    try {
                        var cu = new URL(c.url);
                        var tu = new URL(absoluteUrl);
                        if (cu.origin === tu.origin && cu.pathname === tu.pathname) {
                            return c.focus();
                        }
                    } catch (e) { /* skip */ }
                }

                // 2. Otherwise focus a same-origin window and navigate it.
                for (var j = 0; j < windowClients.length; j++) {
                    var w = windowClients[j];
                    try {
                        if (new URL(w.url).origin === self.location.origin) {
                            if ('navigate' in w) {
                                return w.navigate(absoluteUrl)
                                    .then(function () { return w.focus(); })
                                    .catch(function () { return w.focus(); });
                            }
                            return w.focus()
                                .then(function () { return clients.openWindow(absoluteUrl); })
                                .catch(function () { return clients.openWindow(absoluteUrl); });
                        }
                    } catch (e) { /* skip */ }
                }

                // 3. No same-origin window — open a new one.
                return clients.openWindow(absoluteUrl);
            })
    );
});