// static/js/docs-progress.js
// ============================================================
// localStorage mirror of the server-side progress dict.
//
// The server is authoritative — every page load writes the
// current progress into the DOM via the sidebar counts and
// progress bars. This script's job is:
//
//   1. Cache the server-rendered progress into localStorage so
//      the home page can render instantly on the next visit
//      (before server data arrives).
//   2. Provide a tiny helper for optimistic UI updates (e.g.
//      marking a step seen without a full reload — not used yet,
//      but kept for future use).
//
// Nothing here mutates the server. Reset is done via POST to
// /docs/reset-progress.
// ============================================================

(function () {
    'use strict';

    var KEY = 'nuun.docs.progress';

    function readCache() {
        try {
            var raw = localStorage.getItem(KEY);
            if (!raw) return {};
            var parsed = JSON.parse(raw);
            return (parsed && typeof parsed === 'object') ? parsed : {};
        } catch (e) {
            return {};
        }
    }

    function writeCache(obj) {
        try {
            localStorage.setItem(KEY, JSON.stringify(obj));
        } catch (e) {
            // localStorage unavailable (private mode) — ignore.
        }
    }

    // ------------------------------------------------------------
    // Cache current server-rendered progress on page load.
    // Reads the count text "seen/total" from each sidebar item.
    // ------------------------------------------------------------
    function cacheServerProgress() {
        var items = document.querySelectorAll('.docs-sidebar__item');
        if (!items.length) return;

        var cache = readCache();

        items.forEach(function (item) {
            var link = item.querySelector('.docs-sidebar__link');
            if (!link) return;
            var href = link.getAttribute('href') || '';
            var match = href.match(/\/docs\/([^\/]+)/);
            if (!match) return;
            var key = match[1];

            var count = item.querySelector('.docs-sidebar__count');
            if (count) {
                var parts = (count.textContent || '').split('/');
                var seen = parseInt(parts[0], 10);
                if (!isNaN(seen)) {
                    cache[key] = Math.max(cache[key] || 0, seen);
                }
            } else if (item.querySelector('.docs-sidebar__check')) {
                // Feature is complete — we don't know the total here,
                // but we know it's >= what we had before.
                cache[key] = cache[key] || 0;
            }
        });

        writeCache(cache);
    }

    // ------------------------------------------------------------
    // Public helper (kept for future optimistic updates).
    // ------------------------------------------------------------
    window.NuunDocsProgress = {
        get: function (key) {
            var cache = readCache();
            return cache[key] || 0;
        },
        all: readCache,
        reset: function () {
            writeCache({});
        },
    };

    function init() {
        cacheServerProgress();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();