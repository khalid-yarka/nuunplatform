/* ============================================================
   static/js/live_quiz/new_quiz_watcher.js
   ============================================================
   Client-side poller that fires an OS notification when a new
   public quiz appears in the platform.

   Loaded on every authenticated dashboard page. Reads the bridge
   window.__LQ_NEW_QUIZ for its configuration.

   No server-side push. No VAPID. Same shape as the started-quiz
   notification — polling + reg.showNotification.

   Opt-out is honoured: if the user has turned off
   notifications.new_live_quiz in settings, the bridge sets
   enabled=false and this file exits immediately.
   ============================================================ */

(function () {
    'use strict';

    var config = window.__LQ_NEW_QUIZ;
    if (!config || !config.enabled) return;
    if (!('Notification' in window)) return;

    var storageKey = config.storageKey || 'nuun_lq_last_seen_quiz_id';
    var pollMs = parseInt(config.pollMs, 10) || 30000;

    var lastSeenId = 0;
    try {
        lastSeenId = parseInt(localStorage.getItem(storageKey) || '0', 10) || 0;
    } catch (e) { lastSeenId = 0; }

    // If we have never polled before, the first poll records the
    // current maximum id and does not notify for anything.
    var isFirstPoll = (lastSeenId === 0);

    var pollTimer = null;

    // ----------------------------------------------------------
    // Fire an OS notification for one quiz
    // ----------------------------------------------------------
    function notify(quiz) {
        if (Notification.permission !== 'granted') return;

        var creatorName = (
            (quiz.creator_first || '') + ' ' + (quiz.creator_last || '')
        ).trim() || 'NuunPlatform';

        var creatorFirst = (quiz.creator_first || '').trim()
                        || creatorName.split(' ')[0]
                        || 'Nuun';

        var title = '🎯 Tartan cusub - ' + creatorFirst;
        var body  = '"' + (quiz.title || 'Tartan') + '" — ' + creatorName
                  + '\nTaabo si aad ugu qayb gasho tartanka.';

        var url = '/live-quiz/j/' + (quiz.join_code || '');

        var opts = {
            body: body,
            icon: '/static/images/logo.png',
            badge: '/static/images/badge-nuun.png',
            tag: 'new-quiz-' + quiz.id,
            renotify: true,
            data: { url: url }
        };

        // Prefer service worker path — works in background tabs.
        if ('serviceWorker' in navigator && navigator.serviceWorker.ready) {
            navigator.serviceWorker.ready.then(function (reg) {
                return reg.showNotification(title, opts);
            }).catch(function () {
                try {
                    var n = new Notification(title, opts);
                    n.onclick = function () {
                        try { window.focus(); } catch (e) {}
                        if (url) window.location.href = url;
                        n.close();
                    };
                } catch (e) {}
            });
            return;
        }

        try {
            var n = new Notification(title, opts);
            n.onclick = function () {
                try { window.focus(); } catch (e) {}
                if (url) window.location.href = url;
                n.close();
            };
        } catch (e) {}
    }

    // ----------------------------------------------------------
    // Poll
    // ----------------------------------------------------------
    function poll(oneShot) {
        var url = '/live-quiz/api/recent-public-quizzes?since='
                + encodeURIComponent(lastSeenId);

        fetch(url, { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) return;

                var latest = parseInt(data.latest_id || 0, 10);

                // First-ever poll: record starting point, no notification.
                if (isFirstPoll) {
                    isFirstPoll = false;
                    if (latest > 0) {
                        lastSeenId = latest;
                        try {
                            localStorage.setItem(storageKey, String(lastSeenId));
                        } catch (e) {}
                    }
                    return;
                }

                // New quiz appeared — notify for the newest.
                if (Array.isArray(data.quizzes) && data.quizzes.length > 0) {
                    notify(data.quizzes[0]);
                }

                // Advance the watermark regardless.
                if (latest > lastSeenId) {
                    lastSeenId = latest;
                    try {
                        localStorage.setItem(storageKey, String(lastSeenId));
                    } catch (e) {}
                }
            })
            .catch(function () { /* silent */ })
            .then(function () {
                if (!oneShot) scheduleNextPoll();
            });
    }

    function scheduleNextPoll() {
        if (pollTimer) clearTimeout(pollTimer);
        var delay = document.hidden ? pollMs * 3 : pollMs;
        pollTimer = setTimeout(function () { poll(false); }, delay);
    }

    // Poll immediately when the tab becomes visible again.
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            if (pollTimer) clearTimeout(pollTimer);
            poll(true);
        }
    });

    // Small delay before the first poll so we don't compete with page load.
    setTimeout(function () { poll(false); }, 5000);
})();