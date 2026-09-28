// ============================================================
// static/js/onboarding.js
// Handles the three onboarding choices + soft-gate close.
// ============================================================

(function () {
    'use strict';

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    function showError(msg) {
        var existing = document.querySelector('.onb-toast');
        if (existing) existing.remove();

        var t = document.createElement('div');
        t.className = 'onb-toast';
        t.textContent = msg;
        document.body.appendChild(t);

        setTimeout(function () {
            t.style.opacity = '0';
            setTimeout(function () { t.remove(); }, 400);
        }, 3600);
    }

    function lockButtons() {
        document.querySelectorAll('[data-choice]').forEach(function (b) {
            b.disabled = true;
        });
    }

    function unlockButtons() {
        document.querySelectorAll('[data-choice]').forEach(function (b) {
            b.disabled = false;
        });
    }

    function postChoice(choice, onSuccess) {
        lockButtons();

        fetch('/welcome/choose', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            credentials: 'same-origin',
            body: JSON.stringify({ choice: choice }),
        })
        .then(function (r) {
            return r.json().then(function (d) {
                return { ok: r.ok, data: d };
            });
        })
        .then(function (res) {
            if (res.ok && res.data && res.data.success) {
                if (typeof onSuccess === 'function') {
                    onSuccess(res.data);
                } else if (res.data.redirect) {
                    window.location.href = res.data.redirect;
                }
            } else {
                unlockButtons();
                var msg = (res.data && res.data.error) || 'Something went wrong.';
                showError(msg);
            }
        })
        .catch(function () {
            unlockButtons();
            showError('Network error. Please try again.');
        });
    }

    // ─── Wire up every button ─────────────────────────────────
    document.querySelectorAll('[data-choice]').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            var choice = btn.dataset.choice;
            if (!choice) return;
            postChoice(choice);
        });
    });

    // ─── Close button behaves like "continue on Free" ─────────
    var closeBtn = document.getElementById('onboardingClose');
    if (closeBtn) {
        closeBtn.addEventListener('click', function (e) {
            e.preventDefault();
            postChoice('free');
        });
    }

    // ─── Escape key too ──────────────────────────────────────
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            postChoice('free');
        }
    });
})();