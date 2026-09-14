/* ============================================================
   static/js/admin/ops.js
   Ops/ domain interactions:
   - Platform monitor: 30s auto-refresh
   - Log viewer: tail + level filter + auto-refresh
   - Cache: clear confirmation
   - Sessions: force-logout confirmation
   - Backups: create / verify / restore / delete flows
   - Scheduled tasks: manual trigger
   ============================================================ */

(function () {
    'use strict';

    const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    // ------------------------------------------------------------
    // PLATFORM MONITOR AUTO-REFRESH
    // ------------------------------------------------------------
    function initPlatformAutoRefresh() {
        const container = document.getElementById('platformStats');
        if (!container) return;

        function refresh() {
            fetch('/admin/platform/api/stats', {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) return;
                Object.keys(data).forEach(function (key) {
                    const el = document.querySelector('[data-stat="' + key + '"]');
                    if (el) el.textContent = data[key];
                });
            })
            .catch(function () {});
        }

        setInterval(refresh, 30000);
    }

    // ------------------------------------------------------------
    // LOG VIEWER
    // ------------------------------------------------------------
    function initLogViewer() {
        const viewer = document.getElementById('logViewer');
        if (!viewer) return;

        const fileButtons = document.querySelectorAll('.logs-sidebar .file-btn');
        const levelSelect = document.getElementById('logLevelFilter');
        const autoRefreshToggle = document.getElementById('logAutoRefresh');
        let refreshTimer = null;

        function fetchLog(file) {
            viewer.innerHTML =
                '<span class="line INFO">Loading ' + file + '…</span>';

            const level = levelSelect ? levelSelect.value : '';
            const url = '/admin/logs/read?file=' + encodeURIComponent(file) +
                        (level ? '&level=' + encodeURIComponent(level) : '');

            fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data || !data.lines) {
                    viewer.innerHTML = '<span class="line">No lines to show.</span>';
                    return;
                }
                let html = '';
                data.lines.forEach(function (line) {
                    const cls = levelOf(line);
                    html += '<span class="line ' + cls + '">' + escapeHtml(line) + '</span>';
                });
                viewer.innerHTML = html;
                viewer.scrollTop = viewer.scrollHeight;
            })
            .catch(function () {
                viewer.innerHTML =
                    '<span class="line CRITICAL">Failed to load log.</span>';
            });
        }

        function levelOf(line) {
            if (line.indexOf(' CRITICAL ') !== -1) return 'CRITICAL';
            if (line.indexOf(' ERROR ') !== -1)    return 'ERROR';
            if (line.indexOf(' WARNING ') !== -1)  return 'WARNING';
            if (line.indexOf(' INFO ') !== -1)     return 'INFO';
            if (line.indexOf(' DEBUG ') !== -1)    return 'DEBUG';
            return 'INFO';
        }

        fileButtons.forEach(function (btn) {
            btn.addEventListener('click', function () {
                fileButtons.forEach(function (b) { b.classList.remove('active'); });
                this.classList.add('active');
                fetchLog(this.getAttribute('data-file'));
            });
        });

        if (levelSelect) {
            levelSelect.addEventListener('change', function () {
                const active = document.querySelector('.logs-sidebar .file-btn.active');
                if (active) fetchLog(active.getAttribute('data-file'));
            });
        }

        if (autoRefreshToggle) {
            autoRefreshToggle.addEventListener('change', function () {
                if (refreshTimer) {
                    clearInterval(refreshTimer);
                    refreshTimer = null;
                }
                if (this.checked) {
                    refreshTimer = setInterval(function () {
                        const active = document.querySelector('.logs-sidebar .file-btn.active');
                        if (active) fetchLog(active.getAttribute('data-file'));
                    }, 5000);
                }
            });
        }

        // Initial load: first file
        if (fileButtons.length) fileButtons[0].click();
    }

    // ------------------------------------------------------------
    // CACHE CLEAR
    // ------------------------------------------------------------
    function initCacheClear() {
        const btn = document.getElementById('clearCacheBtn');
        if (!btn) return;

        btn.addEventListener('click', function () {
            window.AdminCore.confirm({
                title: 'Clear the in-memory cache?',
                message: 'This wipes the process-local LRU cache. Active requests continue normally.',
                confirm: 'Clear cache',
                danger: true,
            }).then(function (ok) {
                if (!ok) return;

                btn.disabled = true;
                btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Clearing…';

                fetch('/admin/cache/clear', {
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
                        window.AdminCore.toast('Cache cleared.', 'success');
                        setTimeout(function () { window.location.reload(); }, 700);
                    } else {
                        window.AdminCore.toast(
                            (data && data.error) || 'Could not clear cache.',
                            'error'
                        );
                        btn.disabled = false;
                        btn.innerHTML = '<i class="fas fa-broom"></i> Clear cache';
                    }
                })
                .catch(function () {
                    window.AdminCore.toast('Network error.', 'error');
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-broom"></i> Clear cache';
                });
            });
        });
    }

    // ------------------------------------------------------------
    // SESSION REVOKE (per user + global)
    // ------------------------------------------------------------
    function initSessionRevoke() {
        document.querySelectorAll('[data-revoke-user]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const userId = this.getAttribute('data-revoke-user');
                const userName = this.getAttribute('data-revoke-name') || ('user #' + userId);

                window.AdminCore.confirm({
                    title: 'Force logout ' + userName + '?',
                    message: 'All their active sessions will be invalidated on their next request.',
                    confirm: 'Force logout',
                    danger: true,
                }).then(function (ok) {
                    if (!ok) return;

                    btn.disabled = true;

                    fetch('/admin/sessions/revoke-user/' + userId, {
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
                            window.AdminCore.toast('User will be logged out.', 'success');
                            setTimeout(function () { window.location.reload(); }, 700);
                        } else {
                            window.AdminCore.toast(
                                (data && data.error) || 'Could not revoke session.',
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

        const revokeAll = document.getElementById('revokeAllSessionsBtn');
        if (revokeAll) {
            revokeAll.addEventListener('click', function () {
                window.AdminCore.confirm({
                    title: 'Revoke ALL sessions?',
                    message: 'Every user — including you — will need to log in again. ' +
                             'This is a destructive emergency action.',
                    confirm: 'Revoke all',
                    danger: true,
                }).then(function (ok) {
                    if (!ok) return;

                    revokeAll.disabled = true;
                    revokeAll.innerHTML =
                        '<i class="fas fa-spinner fa-spin"></i> Revoking…';

                    fetch('/admin/sessions/revoke-all', {
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
                            window.AdminCore.toast('All sessions revoked.', 'success');
                            setTimeout(function () {
                                window.location.href = '/login';
                            }, 900);
                        } else {
                            window.AdminCore.toast(
                                (data && data.error) || 'Could not revoke sessions.',
                                'error'
                            );
                            revokeAll.disabled = false;
                            revokeAll.innerHTML = '<i class="fas fa-power-off"></i> Revoke all';
                        }
                    })
                    .catch(function () {
                        window.AdminCore.toast('Network error.', 'error');
                        revokeAll.disabled = false;
                        revokeAll.innerHTML = '<i class="fas fa-power-off"></i> Revoke all';
                    });
                });
            });
        }
    }

    // ------------------------------------------------------------
    // BACKUPS
    // ------------------------------------------------------------
    function initBackups() {
        // Create backup
        const createBtn = document.getElementById('createBackupBtn');
        if (createBtn) {
            createBtn.addEventListener('click', function () {
                window.AdminCore.confirm({
                    title: 'Create a new backup?',
                    message: 'A fresh snapshot of the database will be written to disk.',
                    confirm: 'Create backup',
                }).then(function (ok) {
                    if (!ok) return;

                    createBtn.disabled = true;
                    createBtn.innerHTML =
                        '<i class="fas fa-spinner fa-spin"></i> Creating…';

                    fetch('/admin/backups/create', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': CSRF,
                        },
                        body: JSON.stringify({ type: 'manual' }),
                    })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data && data.success) {
                            window.AdminCore.toast(
                                'Backup created: ' + (data.filename || 'ok'),
                                'success'
                            );
                            setTimeout(function () { window.location.reload(); }, 900);
                        } else {
                            window.AdminCore.toast(
                                (data && data.message) || 'Backup failed.',
                                'error'
                            );
                            createBtn.disabled = false;
                            createBtn.innerHTML =
                                '<i class="fas fa-plus"></i> Create backup now';
                        }
                    })
                    .catch(function () {
                        window.AdminCore.toast('Network error.', 'error');
                        createBtn.disabled = false;
                        createBtn.innerHTML =
                            '<i class="fas fa-plus"></i> Create backup now';
                    });
                });
            });
        }

        // Verify
        document.querySelectorAll('[data-verify-backup]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const file = this.getAttribute('data-verify-backup');
                btn.disabled = true;
                btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

                fetch('/admin/backups/verify/' + encodeURIComponent(file), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': CSRF,
                    },
                    body: JSON.stringify({}),
                })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-check"></i>';
                    if (data && data.success) {
                        window.AdminCore.toast('Backup verified as valid.', 'success');
                    } else {
                        window.AdminCore.toast(
                            'Backup invalid: ' +
                            ((data && data.details && data.details.error) || 'unknown'),
                            'error'
                        );
                    }
                })
                .catch(function () {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-check"></i>';
                    window.AdminCore.toast('Network error.', 'error');
                });
            });
        });

        // Restore
        document.querySelectorAll('[data-restore-backup]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const file = this.getAttribute('data-restore-backup');
                window.AdminCore.confirm({
                    title: 'Restore ' + file + '?',
                    message: 'The current database will be replaced by this snapshot. ' +
                             'A safety copy is created first.',
                    confirm: 'Restore database',
                    danger: true,
                }).then(function (ok) {
                    if (!ok) return;

                    const password = window.prompt(
                        'Enter the admin password to confirm restore:'
                    );
                    if (!password) return;

                    btn.disabled = true;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

                    const form = new FormData();
                    form.append('confirm_password', password);

                    fetch('/admin/backups/restore/' + encodeURIComponent(file), {
                        method: 'POST',
                        headers: { 'X-CSRF-Token': CSRF },
                        body: form,
                    })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data && data.success) {
                            window.AdminCore.toast('Restore successful.', 'success');
                            setTimeout(function () { window.location.reload(); }, 900);
                        } else {
                            window.AdminCore.toast(
                                (data && data.message) || 'Restore failed.',
                                'error'
                            );
                            btn.disabled = false;
                            btn.innerHTML = '<i class="fas fa-undo"></i>';
                        }
                    })
                    .catch(function () {
                        window.AdminCore.toast('Network error.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = '<i class="fas fa-undo"></i>';
                    });
                });
            });
        });

        // Delete
        document.querySelectorAll('[data-delete-backup]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const file = this.getAttribute('data-delete-backup');
                window.AdminCore.confirm({
                    title: 'Delete ' + file + '?',
                    message: 'This backup file will be permanently removed.',
                    confirm: 'Delete',
                    danger: true,
                }).then(function (ok) {
                    if (!ok) return;

                    fetch('/admin/backups/delete/' + encodeURIComponent(file), {
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
                            window.AdminCore.toast('Backup deleted.', 'success');
                            setTimeout(function () { window.location.reload(); }, 700);
                        } else {
                            window.AdminCore.toast(
                                (data && data.error) || 'Delete failed.',
                                'error'
                            );
                        }
                    })
                    .catch(function () {
                        window.AdminCore.toast('Network error.', 'error');
                    });
                });
            });
        });
    }

    // ------------------------------------------------------------
    // SCHEDULED TASKS — MANUAL TRIGGER
    // ------------------------------------------------------------
    function initTaskTriggers() {
        document.querySelectorAll('[data-trigger-task]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const taskName = this.getAttribute('data-trigger-task');
                window.AdminCore.confirm({
                    title: 'Run task "' + taskName + '"?',
                    message: 'The task will execute immediately on the server.',
                    confirm: 'Run now',
                }).then(function (ok) {
                    if (!ok) return;

                    btn.disabled = true;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

                    fetch('/admin/tasks/trigger/' + encodeURIComponent(taskName), {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': CSRF,
                        },
                        body: JSON.stringify({}),
                    })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data && (data.status === 'success' || data.success)) {
                            window.AdminCore.toast(
                                'Task completed: ' + (data.items || 0) + ' items in ' +
                                (data.duration_ms || 0) + 'ms',
                                'success'
                            );
                        } else {
                            window.AdminCore.toast(
                                'Task failed: ' + ((data && data.error) || 'unknown error'),
                                'error'
                            );
                        }
                        btn.disabled = false;
                        btn.innerHTML = '<i class="fas fa-play"></i>';
                    })
                    .catch(function () {
                        window.AdminCore.toast('Network error.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = '<i class="fas fa-play"></i>';
                    });
                });
            });
        });
    }

    // ------------------------------------------------------------
    // HELPERS
    // ------------------------------------------------------------
    function escapeHtml(s) {
        if (s == null) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // ------------------------------------------------------------
    // BOOT
    // ------------------------------------------------------------
    function boot() {
        initPlatformAutoRefresh();
        initLogViewer();
        initCacheClear();
        initSessionRevoke();
        initBackups();
        initTaskTriggers();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();