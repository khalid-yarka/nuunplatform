/* ============================================================
   static/js/admin/community.js
   Community/ domain interactions:
   - Groups: bulk actions, feature toggle, delete confirmation
   - Reports: resolve/dismiss modals with optional reply
   - Achievements: delete confirmation
   - Leaderboard: point reset confirmation
   ============================================================ */

(function () {
    'use strict';

    const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    // ------------------------------------------------------------
    // GROUPS — feature toggle
    // ------------------------------------------------------------
    function initGroupFeatureToggle() {
        document.querySelectorAll('[data-toggle-feature]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const groupId = this.getAttribute('data-toggle-feature');
                const name = this.getAttribute('data-group-name') || ('group #' + groupId);
                const isFeatured = this.getAttribute('data-is-featured') === '1';

                const verb = isFeatured ? 'Unfeature' : 'Feature';

                window.AdminCore.confirm({
                    title: verb + ' "' + name + '"?',
                    message: isFeatured
                        ? 'This group will be removed from the featured section.'
                        : 'This group will be highlighted in the featured section.',
                    confirm: verb,
                }).then(function (ok) {
                    if (!ok) return;

                    btn.disabled = true;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

                    fetch('/admin/groups/api/' + groupId + '/toggle-featured', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': CSRF,
                        },
                        body: JSON.stringify({}),
                    })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data && data.success) {
                            window.AdminCore.toast(verb + 'd.', 'success');
                            setTimeout(function () { window.location.reload(); }, 700);
                        } else {
                            window.AdminCore.toast(
                                (data && data.error) || 'Could not toggle.',
                                'error'
                            );
                            btn.disabled = false;
                            btn.innerHTML = '<i class="fas fa-star"></i>';
                        }
                    })
                    .catch(function () {
                        window.AdminCore.toast('Network error.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = '<i class="fas fa-star"></i>';
                    });
                });
            });
        });
    }

    // ------------------------------------------------------------
    // GROUPS — delete
    // ------------------------------------------------------------
    function initGroupDelete() {
        document.querySelectorAll('[data-delete-group]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const groupId = this.getAttribute('data-delete-group');
                const name = this.getAttribute('data-group-name') || ('group #' + groupId);

                window.AdminCore.confirm({
                    title: 'Delete "' + name + '"?',
                    message: 'This group will be permanently removed. The action cannot be undone.',
                    confirm: 'Delete group',
                    danger: true,
                }).then(function (ok) {
                    if (!ok) return;

                    btn.disabled = true;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

                    fetch('/admin/groups/api/' + groupId, {
                        method: 'DELETE',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': CSRF,
                        },
                    })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data && data.success) {
                            window.AdminCore.toast('Group deleted.', 'success');
                            setTimeout(function () { window.location.reload(); }, 700);
                        } else {
                            window.AdminCore.toast(
                                (data && data.error) || 'Delete failed.',
                                'error'
                            );
                            btn.disabled = false;
                            btn.innerHTML = '<i class="fas fa-trash"></i>';
                        }
                    })
                    .catch(function () {
                        window.AdminCore.toast('Network error.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = '<i class="fas fa-trash"></i>';
                    });
                });
            });
        });
    }

    // ------------------------------------------------------------
    // REPORTS — resolve / dismiss with reply
    // ------------------------------------------------------------
    function initReportActions() {
        function openReplyModal(opts) {
            // opts: { reportId, action: 'resolve'|'dismiss' }
            return new Promise(function (resolve) {
                const backdrop = document.createElement('div');
                backdrop.className = 'admin-modal-backdrop';
                const titleText = opts.action === 'resolve' ? 'Resolve report' : 'Dismiss report';
                const confirmText = opts.action === 'resolve' ? 'Resolve' : 'Dismiss';

                backdrop.innerHTML =
                    '<div class="admin-modal" role="dialog" aria-modal="true">' +
                        '<div class="admin-modal-head">' +
                            '<h3>' + titleText + '</h3>' +
                            '<div class="sub">Optionally send a reply to the reporter.</div>' +
                        '</div>' +
                        '<div class="admin-modal-body">' +
                            '<textarea class="admin-textarea" placeholder="Reply (optional)…" id="reportReplyText"></textarea>' +
                        '</div>' +
                        '<div class="admin-modal-actions">' +
                            '<button type="button" class="admin-btn secondary" data-act="cancel">Cancel</button>' +
                            '<button type="button" class="admin-btn ' +
                                (opts.action === 'resolve' ? 'success' : 'secondary') +
                                '" data-act="confirm">' + confirmText + '</button>' +
                        '</div>' +
                    '</div>';

                document.body.appendChild(backdrop);
                requestAnimationFrame(function () { backdrop.classList.add('is-open'); });

                const ta = backdrop.querySelector('#reportReplyText');
                if (ta) ta.focus();

                function close(result) {
                    backdrop.classList.remove('is-open');
                    setTimeout(function () { backdrop.remove(); }, 200);
                    resolve(result);
                }

                backdrop.addEventListener('click', function (e) {
                    if (e.target === backdrop) close(null);
                    else if (e.target.closest('[data-act="cancel"]')) close(null);
                    else if (e.target.closest('[data-act="confirm"]')) {
                        close({ reply: (ta && ta.value.trim()) || '' });
                    }
                });

                document.addEventListener('keydown', function esc(e) {
                    if (e.key === 'Escape') {
                        document.removeEventListener('keydown', esc);
                        close(null);
                    }
                });
            });
        }

        document.querySelectorAll('[data-resolve-report]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const id = this.getAttribute('data-resolve-report');
                openReplyModal({ reportId: id, action: 'resolve' }).then(function (result) {
                    if (!result) return;
                    submitReportAction(id, 'resolve', result.reply, btn);
                });
            });
        });

        document.querySelectorAll('[data-dismiss-report]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const id = this.getAttribute('data-dismiss-report');
                openReplyModal({ reportId: id, action: 'dismiss' }).then(function (result) {
                    if (!result) return;
                    submitReportAction(id, 'dismiss', result.reply, btn);
                });
            });
        });

        function submitReportAction(reportId, action, reply, btn) {
            btn.disabled = true;
            const original = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

            const path = action === 'resolve' ? '/resolve' : '/dismiss';

            fetch('/admin/reports/' + reportId + path, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRF-Token': CSRF,
                },
                body: 'reply=' + encodeURIComponent(reply || ''),
            })
            .then(function (r) { return r.text(); })
            .then(function () {
                window.AdminCore.toast(
                    action === 'resolve' ? 'Report resolved.' : 'Report dismissed.',
                    'success'
                );
                setTimeout(function () { window.location.reload(); }, 700);
            })
            .catch(function () {
                window.AdminCore.toast('Network error.', 'error');
                btn.disabled = false;
                btn.innerHTML = original;
            });
        }
    }

    // ------------------------------------------------------------
    // ACHIEVEMENTS — delete
    // ------------------------------------------------------------
    function initAchievementDelete() {
        document.querySelectorAll('[data-delete-achievement]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const id = this.getAttribute('data-delete-achievement');
                const name = this.getAttribute('data-achievement-name') || ('achievement #' + id);

                window.AdminCore.confirm({
                    title: 'Delete "' + name + '"?',
                    message: 'Users who unlocked this achievement will lose the badge. This cannot be undone.',
                    confirm: 'Delete',
                    danger: true,
                }).then(function (ok) {
                    if (!ok) return;

                    btn.disabled = true;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

                    fetch('/admin/achievements/' + id + '/delete', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': CSRF,
                        },
                        body: JSON.stringify({}),
                    })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data && data.success) {
                            window.AdminCore.toast('Achievement deleted.', 'success');
                            setTimeout(function () { window.location.reload(); }, 700);
                        } else {
                            window.AdminCore.toast(
                                (data && data.error) || 'Delete failed.',
                                'error'
                            );
                            btn.disabled = false;
                            btn.innerHTML = '<i class="fas fa-trash"></i>';
                        }
                    })
                    .catch(function () {
                        window.AdminCore.toast('Network error.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = '<i class="fas fa-trash"></i>';
                    });
                });
            });
        });
    }

    // ------------------------------------------------------------
    // LEADERBOARD — point reset
    // ------------------------------------------------------------
    function initLeaderboardPointReset() {
        document.querySelectorAll('[data-reset-points]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const userId = this.getAttribute('data-reset-points');
                const name = this.getAttribute('data-user-name') || ('user #' + userId);

                window.AdminCore.confirm({
                    title: 'Reset points for ' + name + '?',
                    message: 'Their total_points will be set to 0. Quiz history is not affected.',
                    confirm: 'Reset to 0',
                    danger: true,
                }).then(function (ok) {
                    if (!ok) return;

                    btn.disabled = true;

                    fetch('/admin/leaderboard/reset-points/' + userId, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': CSRF,
                        },
                        body: JSON.stringify({}),
                    })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data && data.success) {
                            window.AdminCore.toast('Points reset.', 'success');
                            setTimeout(function () { window.location.reload(); }, 700);
                        } else {
                            window.AdminCore.toast(
                                (data && data.error) || 'Reset failed.',
                                'error'
                            );
                            btn.disabled = false;
                        }
                    })
                    .catch(function () {
                        window.AdminCore.toast('Network error.', 'error');
                        btn.disabled = false;
                    });
                });
            });
        });
    }

    // ------------------------------------------------------------
    // BOOT
    // ------------------------------------------------------------
    function boot() {
        initGroupFeatureToggle();
        initGroupDelete();
        initReportActions();
        initAchievementDelete();
        initLeaderboardPointReset();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();