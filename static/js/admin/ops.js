/* ============================================================
   static/js/admin/ops.js
   Ops domain interactions:
   - Log viewer (rewritten): search, level filter, auto-refresh,
     download, click-to-copy, smart auto-scroll
   - Platform monitor: 30s auto-refresh
   - Cache: clear confirmation
   - Sessions: force-logout confirmation
   - Backups: create / verify / restore / delete flows
   - Scheduled tasks: manual trigger
   ============================================================ */

(function () {
    'use strict';

    const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    // ------------------------------------------------------------
    // LOG VIEWER (rewritten)
    // ------------------------------------------------------------
    function initLogViewer() {
        const viewer = document.getElementById('logViewer');
        if (!viewer) return;

        const fileButtons        = document.querySelectorAll('.logs-sidebar .file-btn');
        const levelSelect        = document.getElementById('logLevelFilter');
        const autoRefreshToggle  = document.getElementById('logAutoRefresh');
        const searchInput        = document.getElementById('logSearchInput');
        const searchClear        = document.getElementById('logSearchClear');
        const meta               = document.getElementById('logMeta');
        const downloadBtn        = document.getElementById('logDownloadBtn');
        const pausedHint         = document.getElementById('logsPausedHint');

        let refreshTimer    = null;
        let currentFile     = null;
        let fetchSeq        = 0;
        let autoScroll      = true;

        // ----- Fetch one file -----
        function fetchLog(file) {
            if (!file) return;
            currentFile = file;

            const seq   = ++fetchSeq;
            const level = levelSelect ? levelSelect.value : '';
            const q     = searchInput ? searchInput.value.trim() : '';

            const params = new URLSearchParams();
            params.set('file', file);
            if (level) params.set('level', level);
            if (q)     params.set('search', q);

            setMetaLoading(file);

            fetch('/admin/logs/read?' + params.toString(), {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            })
            .then(r => r.json())
            .then(data => {
                if (seq !== fetchSeq) return;  // stale response — ignore
                if (!data || data.error) {
                    showError(data && data.error ? data.error : 'Could not load log.');
                    return;
                }
                render(data);
            })
            .catch(() => {
                if (seq !== fetchSeq) return;
                showError('Network error while fetching log.');
            });
        }

        // ----- Render lines -----
        function render(data) {
            const lines = data.lines || [];
            if (!lines.length) {
                viewer.innerHTML = '<span class="line INFO">No matching lines.</span>';
                setMeta(data, 0);
                updateDownloadButton(true);
                return;
            }

            // Build DOM fragment
            const frag = document.createDocumentFragment();
            lines.forEach((line, i) => {
                const span = document.createElement('span');
                span.className = 'line ' + levelOf(line);
                span.dataset.lineNo = i + 1;
                span.title = 'Click to copy';
                span.textContent = line;
                frag.appendChild(span);
            });

            viewer.innerHTML = '';
            viewer.appendChild(frag);
            setMeta(data, lines.length);
            updateDownloadButton(false);

            // Auto-scroll only if the user hasn't scrolled up
            if (autoScroll) {
                viewer.scrollTop = viewer.scrollHeight;
                if (pausedHint) pausedHint.style.display = 'none';
            }
        }

        // ----- Footer meta -----
        function setMetaLoading(file) {
            if (meta) meta.textContent = 'Loading ' + file + '…';
        }
        function setMeta(data, shownCount) {
            if (!meta) return;
            const matched = data.total_matched != null ? data.total_matched : shownCount;
            meta.textContent = data.file + ' · showing ' +
                shownCount + ' of ' + matched + ' matching lines';
        }
        function showError(msg) {
            viewer.innerHTML = '<span class="line CRITICAL">' + escapeHtml(msg) + '</span>';
            if (meta) meta.textContent = 'Error';
        }

        // ----- Level detection -----
        function levelOf(line) {
            if (line.indexOf(' CRITICAL ') !== -1) return 'CRITICAL';
            if (line.indexOf(' ERROR ')    !== -1) return 'ERROR';
            if (line.indexOf(' WARNING ')  !== -1) return 'WARNING';
            if (line.indexOf(' INFO ')     !== -1) return 'INFO';
            if (line.indexOf(' DEBUG ')    !== -1) return 'DEBUG';
            return 'INFO';
        }

        // ----- Download button -----
        function updateDownloadButton(disabled) {
            if (!downloadBtn) return;
            downloadBtn.disabled = disabled;
        }
        if (downloadBtn) {
            downloadBtn.addEventListener('click', function () {
                if (!currentFile) return;
                window.location.href = '/admin/logs/download/' +
                    encodeURIComponent(currentFile);
            });
        }

        // ----- File buttons -----
        fileButtons.forEach(btn => {
            btn.addEventListener('click', function () {
                if (this.disabled) return;
                fileButtons.forEach(b => b.classList.remove('active'));
                this.classList.add('active');

                // Reset view when switching files
                autoScroll = true;
                if (searchInput) {
                    searchInput.value = '';
                    if (searchClear) searchClear.style.display = 'none';
                }
                fetchLog(this.getAttribute('data-file'));

                // Reset auto-refresh timer so first load is immediate
                restartAutoRefresh();
            });
        });

        // ----- Level filter -----
        if (levelSelect) {
            levelSelect.addEventListener('change', function () {
                if (currentFile) fetchLog(currentFile);
            });
        }

        // ----- Search (debounced) -----
        let searchTimer = null;
        if (searchInput) {
            searchInput.addEventListener('input', function () {
                if (searchClear) {
                    searchClear.style.display = this.value ? 'inline-flex' : 'none';
                }
                clearTimeout(searchTimer);
                searchTimer = setTimeout(function () {
                    if (currentFile) fetchLog(currentFile);
                }, 350);
            });
        }
        if (searchClear) {
            searchClear.addEventListener('click', function () {
                if (!searchInput) return;
                searchInput.value = '';
                this.style.display = 'none';
                if (currentFile) fetchLog(currentFile);
            });
        }

        // ----- Auto-refresh -----
        function restartAutoRefresh() {
            if (refreshTimer) {
                clearInterval(refreshTimer);
                refreshTimer = null;
            }
            if (autoRefreshToggle && autoRefreshToggle.checked && currentFile) {
                refreshTimer = setInterval(function () {
                    if (currentFile) fetchLog(currentFile);
                }, 5000);
            }
        }
        if (autoRefreshToggle) {
            autoRefreshToggle.addEventListener('change', restartAutoRefresh);
        }

        // ----- Smart auto-scroll -----
        viewer.addEventListener('scroll', function () {
            const distanceFromBottom =
                viewer.scrollHeight - viewer.scrollTop - viewer.clientHeight;
            autoScroll = distanceFromBottom < 24;
            if (pausedHint) {
                pausedHint.style.display = autoScroll ? 'none' : 'inline';
            }
        });

        // ----- Click-to-copy any line -----
        viewer.addEventListener('click', function (e) {
            const line = e.target.closest('.line');
            if (!line) return;
            const text = line.textContent || '';

            const done = () => {
                line.classList.add('copied');
                setTimeout(() => line.classList.remove('copied'), 900);
                if (window.AdminCore && AdminCore.toast) {
                    AdminCore.toast('Line copied.', 'success', 1500);
                }
            };

            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(done).catch(() => done());
            } else {
                const ta = document.createElement('textarea');
                ta.value = text;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                try { document.execCommand('copy'); } catch (err) {}
                document.body.removeChild(ta);
                done();
            }
        });

        // ----- Initial load: first available file -----
        const firstAvailable = Array.from(fileButtons).find(b => !b.disabled);
        if (firstAvailable) firstAvailable.click();
    }

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
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (!data) return;
                Object.keys(data).forEach(key => {
                    const el = document.querySelector('[data-stat="' + key + '"]');
                    if (el) el.textContent = data[key];
                });
            })
            .catch(() => {});
        }

        setInterval(refresh, 30000);
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
            }).then(ok => {
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
                .then(r => r.json())
                .then(data => {
                    if (data && data.success) {
                        window.AdminCore.toast('Cache cleared.', 'success');
                        setTimeout(() => window.location.reload(), 700);
                    } else {
                        window.AdminCore.toast((data && data.error) || 'Could not clear cache.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = '<i class="fas fa-broom"></i> Clear cache';
                    }
                })
                .catch(() => {
                    window.AdminCore.toast('Network error.', 'error');
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-broom"></i> Clear cache';
                });
            });
        });
    }

    // ------------------------------------------------------------
    // SESSION REVOKE
    // ------------------------------------------------------------
    function initSessionRevoke() {
        document.querySelectorAll('[data-revoke-user]').forEach(btn => {
            btn.addEventListener('click', function () {
                const userId = this.getAttribute('data-revoke-user');
                const userName = this.getAttribute('data-revoke-name') || ('user #' + userId);

                window.AdminCore.confirm({
                    title: 'Force logout ' + userName + '?',
                    message: 'All their active sessions will be invalidated on their next request.',
                    confirm: 'Force logout',
                    danger: true,
                }).then(ok => {
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
                    .then(r => r.json())
                    .then(data => {
                        if (data && data.success) {
                            window.AdminCore.toast('User will be logged out.', 'success');
                            setTimeout(() => window.location.reload(), 700);
                        } else {
                            window.AdminCore.toast((data && data.error) || 'Could not revoke session.', 'error');
                            btn.disabled = false;
                        }
                    })
                    .catch(() => {
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
                    message: 'Every user — including you — will need to log in again. This is a destructive emergency action.',
                    confirm: 'Revoke all',
                    danger: true,
                }).then(ok => {
                    if (!ok) return;
                    revokeAll.disabled = true;
                    revokeAll.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Revoking…';

                    fetch('/admin/sessions/revoke-all', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': CSRF,
                        },
                        body: JSON.stringify({}),
                    })
                    .then(r => r.json())
                    .then(data => {
                        if (data && data.success) {
                            window.AdminCore.toast('All sessions revoked.', 'success');
                            setTimeout(() => window.location.href = '/login', 900);
                        } else {
                            window.AdminCore.toast((data && data.error) || 'Could not revoke sessions.', 'error');
                            revokeAll.disabled = false;
                            revokeAll.innerHTML = '<i class="fas fa-power-off"></i> Revoke all';
                        }
                    })
                    .catch(() => {
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
        const createBtn = document.getElementById('createBackupBtn');
        if (createBtn) {
            createBtn.addEventListener('click', function () {
                window.AdminCore.confirm({
                    title: 'Create a new backup?',
                    message: 'A fresh snapshot of the database will be written to disk.',
                    confirm: 'Create backup',
                }).then(ok => {
                    if (!ok) return;
                    createBtn.disabled = true;
                    createBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Creating…';

                    fetch('/admin/backups/create', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': CSRF,
                        },
                        body: JSON.stringify({ type: 'manual' }),
                    })
                    .then(r => r.json())
                    .then(data => {
                        if (data && data.success) {
                            window.AdminCore.toast('Backup created: ' + (data.filename || 'ok'), 'success');
                            setTimeout(() => window.location.reload(), 900);
                        } else {
                            window.AdminCore.toast((data && data.message) || 'Backup failed.', 'error');
                            createBtn.disabled = false;
                            createBtn.innerHTML = '<i class="fas fa-plus"></i> Create backup now';
                        }
                    })
                    .catch(() => {
                        window.AdminCore.toast('Network error.', 'error');
                        createBtn.disabled = false;
                        createBtn.innerHTML = '<i class="fas fa-plus"></i> Create backup now';
                    });
                });
            });
        }

        document.querySelectorAll('[data-verify-backup]').forEach(btn => {
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
                .then(r => r.json())
                .then(data => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-check"></i>';
                    if (data && data.success) {
                        window.AdminCore.toast('Backup verified as valid.', 'success');
                    } else {
                        window.AdminCore.toast('Backup invalid: ' +
                            ((data && data.details && data.details.error) || 'unknown'), 'error');
                    }
                })
                .catch(() => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-check"></i>';
                    window.AdminCore.toast('Network error.', 'error');
                });
            });
        });

        document.querySelectorAll('[data-restore-backup]').forEach(btn => {
            btn.addEventListener('click', function () {
                const file = this.getAttribute('data-restore-backup');
                window.AdminCore.confirm({
                    title: 'Restore ' + file + '?',
                    message: 'The current database will be replaced by this snapshot. A safety copy is created first.',
                    confirm: 'Restore database',
                    danger: true,
                }).then(ok => {
                    if (!ok) return;
                    const password = window.prompt('Enter the admin password to confirm restore:');
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
                    .then(r => r.json())
                    .then(data => {
                        if (data && data.success) {
                            window.AdminCore.toast('Restore successful.', 'success');
                            setTimeout(() => window.location.reload(), 900);
                        } else {
                            window.AdminCore.toast((data && data.message) || 'Restore failed.', 'error');
                            btn.disabled = false;
                            btn.innerHTML = '<i class="fas fa-undo"></i>';
                        }
                    })
                    .catch(() => {
                        window.AdminCore.toast('Network error.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = '<i class="fas fa-undo"></i>';
                    });
                });
            });
        });

        document.querySelectorAll('[data-delete-backup]').forEach(btn => {
            btn.addEventListener('click', function () {
                const file = this.getAttribute('data-delete-backup');
                window.AdminCore.confirm({
                    title: 'Delete ' + file + '?',
                    message: 'This backup file will be permanently removed.',
                    confirm: 'Delete',
                    danger: true,
                }).then(ok => {
                    if (!ok) return;
                    fetch('/admin/backups/delete/' + encodeURIComponent(file), {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': CSRF,
                        },
                        body: JSON.stringify({}),
                    })
                    .then(r => r.json())
                    .then(data => {
                        if (data && data.success) {
                            window.AdminCore.toast('Backup deleted.', 'success');
                            setTimeout(() => window.location.reload(), 700);
                        } else {
                            window.AdminCore.toast((data && data.error) || 'Delete failed.', 'error');
                        }
                    })
                    .catch(() => window.AdminCore.toast('Network error.', 'error'));
                });
            });
        });
    }

    // ------------------------------------------------------------
    // SCHEDULED TASKS — MANUAL TRIGGER
    // ------------------------------------------------------------
    function initTaskTriggers() {
        document.querySelectorAll('[data-trigger-task]').forEach(btn => {
            btn.addEventListener('click', function () {
                const taskName = this.getAttribute('data-trigger-task');
                window.AdminCore.confirm({
                    title: 'Run task "' + taskName + '"?',
                    message: 'The task will execute immediately on the server.',
                    confirm: 'Run now',
                }).then(ok => {
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
                    .then(r => r.json())
                    .then(data => {
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
                    .catch(() => {
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
        initLogViewer();
        initPlatformAutoRefresh();
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