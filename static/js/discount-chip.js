// ============================================================
// static/js/discount-chip.js
// ============================================================
// Adaptive countdown formatter. Never shows a large "total hours"
// number — instead picks the natural unit for the timespan:
//
//   6 days  →  6d 23h 45m left
//   23 hours →  23h 45m 12s left
//   45 min   →  45m 12s left
//   12 sec   →  12s left
//
// Updates every second. Removes the container when the timer
// reaches zero.
// ============================================================

(function () {
    'use strict';

    function pad2(n) {
        return n < 10 ? '0' + n : '' + n;
    }

    function formatDuration(secs) {
        if (secs < 0) secs = 0;

        var days    = Math.floor(secs / 86400);
        var hours   = Math.floor((secs % 86400) / 3600);
        var minutes = Math.floor((secs % 3600) / 60);
        var seconds = secs % 60;

        // Days remaining → show d / h / m (no seconds, too noisy)
        if (days > 0) {
            return days + 'd ' + hours + 'h ' + minutes + 'm left';
        }

        // Hours remaining → show h / m / s
        if (hours > 0) {
            return hours + 'h ' + minutes + 'm ' + seconds + 's left';
        }

        // Minutes remaining → show m / s
        if (minutes > 0) {
            return minutes + 'm ' + seconds + 's left';
        }

        // Seconds only
        return seconds + 's left';
    }

    function update(el) {
        if (el.dataset.dcExpired === '1') return;

        var until = new Date(el.dataset.countdownUntil);
        if (isNaN(until.getTime())) return;

        var secs = Math.max(0, Math.floor((until.getTime() - Date.now()) / 1000));

        // ── Timer expired ──
        if (secs <= 0) {
            el.dataset.dcExpired = '1';
            el.classList.add('expired');
            setTimeout(function () {
                if (el.parentNode) el.parentNode.removeChild(el);
            }, 700);
            return;
        }

        // ── Update the visible duration ──
        var timeEl = el.querySelector('[data-countdown-text]');
        if (timeEl) {
            var text = formatDuration(secs);
            if (timeEl.textContent !== text) {
                timeEl.textContent = text;
            }
        }

        // ── Urgency classes on the container ──
        el.classList.remove('warn', 'critical', 'danger');
        if (secs < 600)         el.classList.add('danger');    // < 10 minutes
        else if (secs < 3600)   el.classList.add('critical');  // < 1 hour
        else if (secs < 21600)  el.classList.add('warn');      // < 6 hours
    }

    function tickAll() {
        var els = document.querySelectorAll('[data-countdown-until]');
        for (var i = 0; i < els.length; i++) update(els[i]);
    }

    function boot() {
        tickAll();
        setInterval(tickAll, 1000);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();