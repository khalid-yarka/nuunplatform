/* static/js/push-client.js
 *
 * NuunPush — a small Web Push client.
 *
 * Loaded only on the settings page. Never requests notification
 * permission automatically; the caller must call subscribe() from a
 * user gesture (a click on the toggle).
 *
 * Public API (window.NuunPush):
 *     supported()             -> bool
 *     isIOS()                 -> bool
 *     isStandalone()          -> bool
 *     getStatus()             -> Promise<state>
 *     subscribe()             -> Promise<{ success, reason?, endpoint? }>
 *     unsubscribe()           -> Promise<{ success, reason? }>
 *     sendTest()              -> Promise<{ ok, status, data }>
 *     requestPermission()     -> Promise<'granted'|'denied'|'default'>
 */
(function (window) {
    'use strict';

    var VAPID_CACHE = null;

    // ---------------------------------------------------------------
    // helpers
    // ---------------------------------------------------------------
    function csrfToken() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.content : '';
    }

    function urlB64ToUint8Array(base64) {
        var padding = '='.repeat((4 - base64.length % 4) % 4);
        var b64 = (base64 + padding).replace(/-/g, '+').replace(/_/g, '/');
        var raw = window.atob(b64);
        var arr = new Uint8Array(raw.length);
        for (var i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
        return arr;
    }

    function supported() {
        return (
            'serviceWorker' in navigator &&
            'PushManager' in window &&
            'Notification' in window
        );
    }

    function isIOS() {
        var ua = navigator.userAgent || '';
        return /iPad|iPhone|iPod/.test(ua) && !window.MSStream;
    }

    function isStandalone() {
        if (navigator.standalone) return true;
        try {
            return window.matchMedia('(display-mode: standalone)').matches;
        } catch (e) {
            return false;
        }
    }

    // ---------------------------------------------------------------
    // service worker / subscription
    // ---------------------------------------------------------------
    function getRegistration() {
        if (!supported()) return Promise.resolve(null);
        return navigator.serviceWorker.ready.catch(function () { return null; });
    }

    function getVapidKey() {
        if (VAPID_CACHE) return Promise.resolve(VAPID_CACHE);
        return fetch('/push/vapid-key', { credentials: 'same-origin' })
            .then(function (r) {
                if (!r.ok) throw new Error('vapid-unavailable');
                return r.json();
            })
            .then(function (data) {
                VAPID_CACHE = data.key;
                return VAPID_CACHE;
            });
    }

    function getExistingSubscription() {
        return getRegistration().then(function (reg) {
            if (!reg) return null;
            return reg.pushManager.getSubscription().catch(function () {
                return null;
            });
        });
    }

    // ---------------------------------------------------------------
    // public actions
    // ---------------------------------------------------------------
    function requestPermission() {
        if (!supported()) return Promise.resolve('unsupported');
        return Notification.requestPermission();
    }

    function subscribe() {
        if (!supported()) {
            return Promise.resolve({ success: false, reason: 'unsupported' });
        }
        if (isIOS() && !isStandalone()) {
            return Promise.resolve({ success: false, reason: 'ios-needs-install' });
        }

        var perm = Notification.permission;
        var permStep = (perm === 'default')
            ? requestPermission()
            : Promise.resolve(perm);

        return permStep.then(function (result) {
            if (result !== 'granted') {
                return { success: false, reason: 'denied' };
            }
            return getRegistration();
        })
        .then(function (reg) {
            if (!reg || !reg.pushManager) {
                return { success: false, reason: 'no-service-worker' };
            }
            return getVapidKey().then(function (key) {
                return reg.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: urlB64ToUint8Array(key),
                });
            }).then(function (sub) {
                var json = sub.toJSON();
                return fetch('/push/subscribe', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrfToken(),
                    },
                    body: JSON.stringify({
                        endpoint: json.endpoint,
                        keys: {
                            p256dh: json.keys.p256dh,
                            auth:   json.keys.auth,
                        },
                    }),
                }).then(function (r) {
                    if (!r.ok) {
                        return { success: false, reason: 'server-rejected' };
                    }
                    return { success: true, endpoint: json.endpoint };
                });
            });
        })
        .catch(function (err) {
            return {
                success: false,
                reason: 'subscribe-failed',
                error: String(err),
            };
        });
    }

    function unsubscribe() {
        return getExistingSubscription().then(function (sub) {
            if (!sub) return { success: true, reason: 'not-subscribed' };

            var endpoint = sub.endpoint;

            return fetch('/push/unsubscribe', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrfToken(),
                },
                body: JSON.stringify({ endpoint: endpoint }),
            })
            .catch(function () { /* ignore */ })
            .then(function () {
                return sub.unsubscribe();
            })
            .then(function () { return { success: true }; })
            .catch(function () {
                return { success: false, reason: 'unsubscribe-failed' };
            });
        });
    }

    function sendTest() {
        return fetch('/push/test', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrfToken() },
        })
        .then(function (r) {
            return r.json().catch(function () { return {}; })
                .then(function (data) {
                    return { ok: r.ok, status: r.status, data: data };
                });
        })
        .catch(function () {
            return { ok: false, status: 0, data: { error: 'Network error' } };
        });
    }

    function getStatus() {
        var s = {
            supported:       supported(),
            isIOS:           isIOS(),
            standalone:      isStandalone(),
            iosNeedsInstall: false,
            permission:      'unsupported',
            subscribed:      false,
            configured:      false,
            serverCount:     0,
        };

        if (!s.supported) return Promise.resolve(s);

        s.iosNeedsInstall = s.isIOS && !s.standalone;
        s.permission = Notification.permission;

        return getExistingSubscription().then(function (sub) {
            s.subscribed = !!sub;

            return fetch('/push/status', { credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : {}; })
                .catch(function () { return {}; })
                .then(function (d) {
                    s.configured = !!d.enabled;
                    s.serverCount = d.subscription_count || 0;
                    return s;
                });
        });
    }

    // ---------------------------------------------------------------
    // export
    // ---------------------------------------------------------------
    window.NuunPush = {
        supported:         supported,
        isIOS:             isIOS,
        isStandalone:      isStandalone,
        getStatus:         getStatus,
        subscribe:         subscribe,
        unsubscribe:       unsubscribe,
        sendTest:          sendTest,
        requestPermission: requestPermission,
    };

})(window);