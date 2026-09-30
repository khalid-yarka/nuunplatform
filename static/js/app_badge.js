/* ============================================================
   app_badge.js — PWA app icon badge
   ============================================================
   Sets and clears the small red dot (with a number) on the
   installed home-screen icon. Uses the Badging API:

       navigator.setAppBadge(n)     → show badge with number n
       navigator.clearAppBadge()    → remove the badge

   Works on:
     · Android Chrome (when installed as a PWA)
     · iOS 16.4+ (when installed as a PWA)
     · Desktop Chrome / Edge (dot on the taskbar icon)

   Does nothing in a regular browser tab — this is a platform
   limitation, not a bug. Failures are silently ignored.

   Behaviour:
     · On page load → fetch unread count → set badge
     · Every 60s while visible → refresh
     · On tab becoming visible → refresh
     · When the bell dropdown is opened → clear badge + mark all read
     · When the /notifications page loads → clear badge
   ============================================================ */

(function () {
    'use strict';

    if (!('setAppBadge' in navigator)) {
        // Badging API unavailable. Expose no-op stubs so other code
        // can call window.NuunAppBadge.* without guards.
        window.NuunAppBadge = {
            refresh: function () {},
            clear: function () {},
            set: function () {}
        };
        return;
    }

    var lastCount = -1;
    var pollTimer = null;

    function updateBadge(count) {
        count = Math.max(0, count | 0);
        if (count === lastCount) return;
        lastCount = count;
        try {
            if (count > 0) {
                navigator.setAppBadge(count).catch(function () {});
            } else {
                navigator.clearAppBadge().catch(function () {});
            }
        } catch (e) { /* silent */ }
    }

    function fetchCount() {
        return fetch('/notifications/api/unread-count', {
            credentials: 'same-origin',
            cache: 'no-store'
        })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && typeof d.count === 'number') return d.count;
                if (d && typeof d.unread === 'number') return d.unread;
                return null;
            })
            .catch(function () { return null; });
    }

    function refresh() {
        fetchCount().then(function (count) {
            if (count !== null) updateBadge(count);
        });
    }

    function clearBadge() {
        updateBadge(0);
    }

    function markAllReadOnServer() {
        var token = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
        fetch('/notifications/api/mark-all-read', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': token
            },
            body: '{}'
        }).catch(function () {});
    }

    function startPolling() {
        if (pollTimer) return;
        pollTimer = setInterval(function () {
            if (document.visibilityState === 'visible') refresh();
        }, 60000);
    }

    // Initial fetch
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            setTimeout(refresh, 600);
        });
    } else {
        setTimeout(refresh, 600);
    }

    // Refresh when tab returns to foreground
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') refresh();
    });

    startPolling();

    // Clear when the bell dropdown is opened
    document.addEventListener('click', function (e) {
        var toggle = e.target.closest && e.target.closest('#notificationToggle');
        if (!toggle) return;

        clearBadge();
        markAllReadOnServer();

        var inAppDot = document.getElementById('notificationBadge');
        if (inAppDot) {
            inAppDot.style.display = 'none';
            inAppDot.textContent = '0';
        }
    });

    // Clear when landing on the notifications page
    if (window.location.pathname.indexOf('/notifications') === 0) {
        clearBadge();
        markAllReadOnServer();
    }

    // Public API
    window.NuunAppBadge = {
        refresh: refresh,
        clear: clearBadge,
        set: updateBadge
    };
})();