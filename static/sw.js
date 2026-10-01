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
            return Promise.all(
                SHELL_ASSETS.map(function (url) {
                    return cache.add(url).catch(function () { /* skip */ });
                })
            );
        }).then(function () {
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
                        return k.startsWith('nuun-')
                            && k !== SHELL_CACHE
                            && k !== RUNTIME_CACHE;
                    })
                    .map(function (k) { return caches.delete(k); })
            );
        }).then(function () {
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

/* ============================================================
   PUSH HANDLER
   ============================================================
   Renders an OS notification for every Web Push message the
   browser delivers. Reads payload fields the server sets:

     title, body, url, icon, tag, data

   Priority defaults per type:
     · live_quiz_start → requireInteraction + renotify + vibrate
     · new_live_quiz   → nothing special (informational)

   Anything else falls through to the plain defaults.
   ============================================================ */
self.addEventListener('push', function (event) {
    var payload = {};
    try {
        payload = event.data ? event.data.json() : {};
    } catch (e) {
        try { payload = { title: 'NuunPlatform', body: event.data.text() }; }
        catch (e2) { payload = { title: 'NuunPlatform', body: 'New notification' }; }
    }

    var title = payload.title || 'NuunPlatform';
    var body  = payload.body  || '';
    var url   = payload.url   || '/';
    var icon  = payload.icon  || '/static/images/icon-192.png';
    var badge = payload.badge || '/static/images/badge-nuun.png';
    var tag   = payload.tag   || ('nuun-push-' + Date.now());
    var type  = (payload.data && payload.data.type) || '';

    var opts = {
        body: body,
        icon: icon,
        badge: badge,
        tag: tag,
        data: { url: url, type: type }
    };

    // Priority defaults for time-sensitive types
    if (type === 'live_quiz_start') {
        opts.requireInteraction = true;
        opts.renotify = true;
        opts.vibrate = [200, 100, 200, 100, 200];
    }

    event.waitUntil(self.registration.showNotification(title, opts));
});

/* ---------- fetch ---------- */
function _shouldBypass(pathname) {
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

    if (req.method !== 'GET') return;

    var url;
    try { url = new URL(req.url); } catch (e) { return; }

    if (url.origin !== self.location.origin) return;

    if (_shouldBypass(url.pathname)) return;

    var isNav = (req.mode === 'navigate')
             || (req.headers.get('accept') || '').includes('text/html');

    if (isNav) {
        event.respondWith(
            fetch(req)
                .then(function (res) {
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
                    return cached || network;
                });
            })
        );
        return;
    }

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
   ============================================================ */
self.addEventListener('notificationclick', function (event) {
    event.notification.close();

    var targetUrl = '/';
    try {
        if (event.notification.data && event.notification.data.url) {
            targetUrl = event.notification.data.url;
        }
    } catch (e) { /* keep default */ }

    var absoluteUrl;
    try {
        absoluteUrl = new URL(targetUrl, self.location.origin).href;
    } catch (e) {
        absoluteUrl = self.location.origin + '/';
    }

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(function (windowClients) {
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

                return clients.openWindow(absoluteUrl);
            })
    );
});