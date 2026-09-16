// static/js/reactions.js
// Shared reaction module for Like / Save / Report buttons.
// Auto-initializes every .nuun-reactions element on the page.
// Exposes window.NuunReactions.mount(el) for dynamically-added elements.
//
// Backend contract (both regular quiz and live quiz):
//   GET  status_url?question_id=N  → {liked, saved, reported}
//   POST like_url                  → {liked}      body {question_id}
//   POST save_url                  → {saved}      body {question_id}
//                                or 429 {error, limit}
//   POST report_url                → {success}    body {question_id, reason, comment}

(function () {
    'use strict';

    function getCsrf() {
        // 1) Preferred: page sets window.csrfToken explicitly
        if (typeof window.csrfToken === 'string' && window.csrfToken) {
            return window.csrfToken;
        }
        // 2) Legacy helper from main.js
        if (typeof window.getCsrfToken === 'function') {
            const t = window.getCsrfToken();
            if (t) return t;
        }
        // 3) Meta tag
        const m = document.querySelector('meta[name="csrf-token"]');
        if (m && m.content) return m.content;
        // 4) Hidden input
        const i = document.querySelector('input[name="csrf_token"]');
        if (i && i.value) return i.value;
        return '';
    }

    function toast(msg, type) {
        if (typeof window.showToast === 'function') {
            window.showToast(msg, type || 'info');
        } else {
            console.warn('[reactions]', msg);
        }
    }

    function fetchJson(url, opts) {
        opts = opts || {};
        opts.credentials = 'same-origin';
        opts.headers = Object.assign({
            'X-CSRF-Token': getCsrf(),
            'Accept': 'application/json'
        }, opts.headers || {});
        return fetch(url, opts).then(function (res) {
            return res.json().catch(function () { return {}; })
                .then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
        });
    }

    function setButtonState(btn, active) {
        if (!btn) return;
        btn.classList.toggle('is-active', !!active);
        btn.setAttribute('aria-pressed', active ? 'true' : 'false');

        const label = btn.querySelector('.nuun-reaction-label');
        if (label) {
            const def = label.dataset.labelDefault || '';
            const act = label.dataset.labelActive || def;
            label.textContent = active ? act : def;
        }

        const icon = btn.querySelector('.nuun-reaction-icon');
        if (icon) {
            const action = btn.dataset.action;
            if (action === 'like')   icon.textContent = active ? '♥' : '♡';
            if (action === 'save')   icon.textContent = '🔖';
            if (action === 'report') icon.textContent = active ? '⚠' : '⚑';
        }
    }

    function initRoot(root) {
        if (!root || root.dataset.nuunInit === '1') return;
        root.dataset.nuunInit = '1';

        const qid        = root.dataset.questionId;
        const statusUrl  = root.dataset.statusUrl;
        const likeUrl    = root.dataset.likeUrl;
        const saveUrl    = root.dataset.saveUrl;
        const reportUrl  = root.dataset.reportUrl;
        const saveLocked = root.dataset.saveLocked === 'true';

        const likeBtn   = root.querySelector('[data-action="like"]');
        const saveBtn   = root.querySelector('[data-action="save"]');
        const reportBtn = root.querySelector('[data-action="report"]');

        // ---- Initial state fetch ----
        if (statusUrl && qid) {
            const url = statusUrl + (statusUrl.indexOf('?') >= 0 ? '&' : '?')
                      + 'question_id=' + encodeURIComponent(qid);
            fetchJson(url, { method: 'GET' }).then(function (r) {
                if (r.ok && r.data && !r.data.error) {
                    setButtonState(likeBtn,   !!r.data.liked);
                    setButtonState(saveBtn,   !!r.data.saved);
                    setButtonState(reportBtn, !!r.data.reported);
                }
            }).catch(function () { /* silent */ });
        }

        // ---- Like ----
        if (likeBtn && likeUrl) {
            likeBtn.addEventListener('click', function (e) {
                e.preventDefault();
                if (likeBtn.disabled) return;
                likeBtn.disabled = true;
                fetchJson(likeUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ question_id: qid })
                }).then(function (r) {
                    if (r.ok && r.data && typeof r.data.liked === 'boolean') {
                        setButtonState(likeBtn, r.data.liked);
                    } else if (r.data && r.data.error) {
                        toast(r.data.error, 'error');
                    }
                }).catch(function () {
                    toast('Network error', 'error');
                }).then(function () {
                    likeBtn.disabled = false;
                });
            });
        }

        // ---- Save ----
        if (saveBtn && saveUrl) {
            saveBtn.addEventListener('click', function (e) {
                e.preventDefault();
                if (saveBtn.disabled) return;
                const isActive = saveBtn.classList.contains('is-active');
                if (saveLocked && !isActive) {
                    if (typeof window.openUpgradeSheet === 'function') {
                        window.openUpgradeSheet({ feature: 'saved_content' });
                    } else {
                        toast('Upgrade to save questions', 'warning');
                    }
                    return;
                }
                saveBtn.disabled = true;
                fetchJson(saveUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ question_id: qid })
                }).then(function (r) {
                    if (r.ok && r.data && typeof r.data.saved === 'boolean') {
                        setButtonState(saveBtn, r.data.saved);
                    } else if (r.status === 429 && r.data && r.data.error) {
                        const extra = r.data.limit != null ? ' (' + r.data.limit + ' max)' : '';
                        toast(r.data.error + extra, 'warning');
                    } else if (r.data && r.data.error) {
                        toast(r.data.error, 'error');
                    }
                }).catch(function () {
                    toast('Network error', 'error');
                }).then(function () {
                    saveBtn.disabled = false;
                });
            });
        }

        // ---- Report ----
        if (reportBtn && reportUrl) {
            reportBtn.addEventListener('click', function (e) {
                e.preventDefault();
                if (reportBtn.classList.contains('is-active')) return;
                openReportModal(qid, reportUrl, reportBtn);
            });
        }
    }

    // ============================================
    // Report modal — lazily created, single instance
    // ============================================
    let modalEl = null;
    let pendingButton = null;

    function ensureModal() {
        if (modalEl) return modalEl;
        const wrap = document.createElement('div');
        wrap.innerHTML =
            '<div class="nuun-report-modal" id="nuunReportModal" role="dialog" aria-modal="true">' +
              '<div class="nuun-report-modal__backdrop" data-close="1"></div>' +
              '<div class="nuun-report-modal__content">' +
                '<div class="nuun-report-modal__header">' +
                  '<h3 class="nuun-report-modal__title">Report Question</h3>' +
                  '<button type="button" class="nuun-report-modal__close" data-close="1" aria-label="Close">×</button>' +
                '</div>' +
                '<form class="nuun-report-modal__form">' +
                  '<label class="nuun-report-modal__label">Reason <span class="required">*</span></label>' +
                  '<select name="reason" class="nuun-report-modal__select" required>' +
                    '<option value="">Select a reason…</option>' +
                    '<option value="incorrect">Incorrect Answer</option>' +
                    '<option value="inappropriate">Inappropriate Content</option>' +
                    '<option value="spam">Spam or Irrelevant</option>' +
                    '<option value="duplicate">Duplicate Question</option>' +
                    '<option value="other">Other</option>' +
                  '</select>' +
                  '<label class="nuun-report-modal__label">Additional Comments</label>' +
                  '<textarea name="comment" class="nuun-report-modal__textarea" rows="3" placeholder="Please provide more details…"></textarea>' +
                  '<div class="nuun-report-modal__actions">' +
                    '<button type="button" class="nuun-btn nuun-btn--ghost" data-close="1">Cancel</button>' +
                    '<button type="submit" class="nuun-btn nuun-btn--primary">Submit Report</button>' +
                  '</div>' +
                '</form>' +
              '</div>' +
            '</div>';
        modalEl = wrap.firstElementChild;
        document.body.appendChild(modalEl);

        modalEl.addEventListener('click', function (e) {
            if (e.target.dataset && e.target.dataset.close === '1') closeModal();
        });

        modalEl.querySelector('form').addEventListener('submit', function (e) {
            e.preventDefault();
            const fd = new FormData(e.target);
            const reason = (fd.get('reason') || '').toString().trim();
            if (!reason) { toast('Please select a reason', 'warning'); return; }

            const payload = {
                question_id: modalEl.dataset.questionId,
                reason: reason,
                comment: (fd.get('comment') || '').toString()
            };
            const url = modalEl.dataset.url;
            const submitBtn = e.target.querySelector('button[type="submit"]');
            submitBtn.disabled = true;
            const originalText = submitBtn.textContent;
            submitBtn.textContent = 'Submitting…';

            fetchJson(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }).then(function (r) {
                if (r.ok && r.data && (r.data.success || r.data.reported)) {
                    toast('Report submitted', 'success');
                    if (pendingButton) setButtonState(pendingButton, true);
                    closeModal();
                } else {
                    toast((r.data && r.data.error) || 'Failed to submit', 'error');
                }
            }).catch(function () {
                toast('Network error', 'error');
            }).then(function () {
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
            });
        });

        return modalEl;
    }

    function openReportModal(qid, url, buttonEl) {
        const m = ensureModal();
        m.dataset.questionId = qid;
        m.dataset.url = url;
        pendingButton = buttonEl || null;
        m.querySelector('form').reset();
        m.classList.add('is-open');
    }

    function closeModal() {
        if (modalEl) modalEl.classList.remove('is-open');
        pendingButton = null;
    }

    function initAll(scope) {
        const root = scope || document;
        const nodes = root.querySelectorAll ? root.querySelectorAll('.nuun-reactions') : [];
        nodes.forEach(initRoot);
    }

    window.NuunReactions = { mount: initRoot, init: initAll };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { initAll(); });
    } else {
        initAll();
    }
})();