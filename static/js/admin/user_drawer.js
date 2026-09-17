/* ============================================================
   static/js/admin/user_drawer.js
   Slide-in user detail drawer for /admin/users.

   Behaviour:
     - Click any row (skip interactive children) → open drawer
     - Fetch user JSON, render drawer, wire actions
     - After each action: re-fetch user, re-render, update the table row
     - Close: backdrop, X, Esc
     - Focus management: focus drawer on open, restore on close
   ============================================================ */

(function () {
    'use strict';

    // ------------------------------------------------------------
    // STATE
    // ------------------------------------------------------------
    let drawerEl = null;
    let backdropEl = null;
    let bodyEl = null;
    let footEl = null;
    let lastFocused = null;
    let currentUserId = null;
    let csrfToken = '';

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

    function toast(msg, type) {
        if (window.AdminCore && AdminCore.toast) {
            AdminCore.toast(msg, type || 'info');
        } else {
            console.warn('[user-drawer]', msg);
        }
    }

    function confirmAction(opts) {
        if (window.AdminCore && AdminCore.confirm) {
            return AdminCore.confirm(opts);
        }
        return Promise.resolve(window.confirm(opts.title || 'Are you sure?'));
    }

    function fmtDate(s) {
        if (!s) return '—';
        try {
            const d = new Date(String(s).replace(' ', 'T'));
            if (isNaN(d.getTime())) return String(s).slice(0, 10);
            const y = d.getFullYear();
            const m = String(d.getMonth() + 1).padStart(2, '0');
            const day = String(d.getDate()).padStart(2, '0');
            return y + '/' + m + '/' + day;
        } catch (e) { return String(s).slice(0, 10); }
    }

    // ------------------------------------------------------------
    // DRAWER ELEMENT ACCESS
    // ------------------------------------------------------------
    function ensureDrawerRefs() {
        if (drawerEl) return;
        drawerEl = document.getElementById('udDrawer');
        backdropEl = document.getElementById('udBackdrop');
        bodyEl = drawerEl ? drawerEl.querySelector('.ud-body') : null;
        footEl = drawerEl ? drawerEl.querySelector('.ud-foot') : null;
    }

    // ------------------------------------------------------------
    // OPEN / CLOSE
    // ------------------------------------------------------------
    function openDrawer(userId, sourceRow) {
        ensureDrawerRefs();
        if (!drawerEl) return;

        lastFocused = document.activeElement;
        currentUserId = userId;

        drawerEl.classList.add('is-open');
        backdropEl.classList.add('is-open');
        document.body.classList.add('ud-open');

        renderSkeleton();
        setFooterLoading();

        fetchUser(userId);

        // Focus the drawer
        setTimeout(function () {
            const focusTarget = drawerEl.querySelector('.ud-head__close');
            if (focusTarget) focusTarget.focus();
        }, 60);
    }

    function closeDrawer() {
        ensureDrawerRefs();
        if (!drawerEl) return;
        drawerEl.classList.remove('is-open');
        backdropEl.classList.remove('is-open');
        document.body.classList.remove('ud-open');
        currentUserId = null;
        if (lastFocused && typeof lastFocused.focus === 'function') {
            try { lastFocused.focus(); } catch (e) {}
        }
    }

    // ------------------------------------------------------------
    // RENDER — skeleton / error / content
    // ------------------------------------------------------------
    function renderSkeleton() {
        bodyEl.innerHTML =
            '<div class="ud-skeleton">' +
                '<div class="ud-sk ud-sk--hero"></div>' +
                '<div class="ud-sk ud-sk--stats"></div>' +
                '<div class="ud-sk ud-sk--line"></div>' +
                '<div class="ud-sk ud-sk--line"></div>' +
                '<div class="ud-sk ud-sk--line-sm"></div>' +
            '</div>';
        footEl.innerHTML =
            '<a href="#" class="admin-btn secondary is-disabled" aria-disabled="true">' +
                '<i class="fas fa-external-link-alt"></i> Open full profile' +
            '</a>';
    }

    function renderError(msg) {
        bodyEl.innerHTML =
            '<div class="ud-error">' +
                '<span class="ud-error__icon">⚠️</span>' +
                '<div class="ud-error__title">Could not load user</div>' +
                '<div>' + escapeHtml(msg || 'Network error') + '</div>' +
                '<button type="button" class="admin-btn primary ud-error__retry" id="udRetryBtn">' +
                    '<i class="fas fa-redo"></i> Retry' +
                '</button>' +
            '</div>';
        const btn = document.getElementById('udRetryBtn');
        if (btn) {
            btn.addEventListener('click', function () {
                if (currentUserId) {
                    renderSkeleton();
                    fetchUser(currentUserId);
                }
            });
        }
    }

    function setFooterLoading() {
        footEl.innerHTML =
            '<a href="#" class="admin-btn secondary is-disabled" aria-disabled="true">' +
                '<i class="fas fa-external-link-alt"></i> Open full profile' +
            '</a>';
    }

    function render(user) {
        // ---- HERO ----
        const initial = (user.first_name || '?')[0].toUpperCase()
                      + (user.last_name ? user.last_name[0].toUpperCase() : '');

        const badges = [];
        if (user.is_verified) {
            badges.push('<span class="ud-badge verified"><i class="fas fa-check-circle"></i> Verified</span>');
        } else {
            badges.push('<span class="ud-badge unverified"><i class="fas fa-hourglass-half"></i> Unverified</span>');
        }
        if (user.is_admin) {
            badges.push('<span class="ud-badge admin"><i class="fas fa-shield-alt"></i> Admin</span>');
        }
        const tierClass = 'tier-' + (user.tier || 'free');
        const tierIcon = user.tier === 'pro' ? '👑' : (user.tier === 'premium' ? '💎' : '⚪');
        badges.push('<span class="ud-badge ' + tierClass + '">' + tierIcon + ' ' + escapeHtml((user.tier || 'free').toUpperCase()) + '</span>');

        // ---- STATS ----
        const s = user.stats || {};

        // ---- CONTACT ----
        const phone = user.phone
            ? '<a href="https://wa.me/' + escapeHtml(String(user.phone).replace(/[^0-9]/g, '')) + '" target="_blank" rel="noopener">' +
                '<i class="fab fa-whatsapp" style="color:#25D366;"></i> ' + escapeHtml(user.phone) +
              '</a>'
            : '<span class="muted">Not set</span>';

        const school = user.school || '<span class="muted">Not set</span>';
        const city = user.city || '<span class="muted">—</span>';
        const grade = user.grade || '<span class="muted">—</span>';
        const location = user.location || '<span class="muted">—</span>';
        const curriculum = user.curriculum || '<span class="muted">—</span>';

        // ---- TIER PICKER ----
        const tiers = ['free', 'premium', 'pro'];
        const tierMeta = {
            'free':    { icon: '⚪', label: 'Free',    sub: 'Basic' },
            'premium': { icon: '💎', label: 'Premium', sub: 'Unlocked' },
            'pro':     { icon: '👑', label: 'Pro',     sub: 'Everything' },
        };

        const canSetTier = user.can && user.can.set_tier;
        const tierPickerHtml = tiers.map(function (t) {
            const meta = tierMeta[t];
            const isCurrent = (user.tier === t);
            let cls = 'ud-tier-btn';
            if (isCurrent) cls += (t === 'pro' ? ' is-current-pro' : ' is-current');
            const disabled = (!canSetTier || isCurrent) ? ' disabled' : '';
            return '<button type="button" class="' + cls + '"' + disabled +
                    ' data-tier="' + t + '">' +
                        '<span class="ud-tier-btn__icon">' + meta.icon + '</span>' +
                        '<span class="ud-tier-btn__label">' + meta.label + '</span>' +
                        '<span class="ud-tier-btn__sub">' + (isCurrent ? 'Current' : meta.sub) + '</span>' +
                    '</button>';
        }).join('');

        // ---- ACTIONS ----
        const canVerify = user.can && user.can.verify;
        const canForceLogout = user.can && user.can.force_logout;
        const canToggleAdmin = user.can && user.can.toggle_admin;
        const canNotify = user.can && user.can.notify;

        const verifyBtn = (user.is_verified
            ? '<button type="button" class="ud-action" data-action="unverify"' +
                (canVerify ? '' : ' disabled') + '>' +
                '<i class="fas fa-user-slash"></i> Unverify account' +
              '</button>'
            : '<button type="button" class="ud-action is-success" data-action="verify"' +
                (canVerify ? '' : ' disabled') + '>' +
                '<i class="fas fa-user-check"></i> Verify account' +
              '</button>');

        const adminBtn = '<button type="button" class="ud-action" data-action="toggle_admin"' +
            (canToggleAdmin ? '' : ' disabled') + '>' +
            '<i class="fas fa-shield-alt"></i> ' + (user.is_admin ? 'Revoke admin' : 'Grant admin') +
            '</button>';

        const logoutBtn = '<button type="button" class="ud-action is-danger" data-action="force_logout"' +
            (canForceLogout ? '' : ' disabled') + '>' +
            '<i class="fas fa-power-off"></i> Force logout' +
            '</button>';

        const notifyHtml = canNotify
            ? '<details class="ud-notify">' +
                '<summary><i class="fas fa-paper-plane"></i> Send notification' +
                    '<i class="fas fa-chevron-down ud-notify__chevron"></i>' +
                '</summary>' +
                '<div class="ud-notify__body">' +
                    '<input type="text" class="ud-notify__input" id="udNotifyTitle" placeholder="Title" maxlength="100">' +
                    '<textarea class="ud-notify__textarea" id="udNotifyBody" placeholder="Message…" maxlength="500"></textarea>' +
                    '<button type="button" class="admin-btn primary ud-notify__submit" id="udNotifySend">' +
                        '<i class="fas fa-paper-plane"></i> Send' +
                    '</button>' +
                '</div>' +
              '</details>'
            : '';

        // ---- COMPOSE BODY ----
        bodyEl.innerHTML =
            '<div class="ud-hero">' +
                '<div class="ud-hero__avatar">' + escapeHtml(initial) + '</div>' +
                '<div class="ud-hero__info">' +
                    '<div class="ud-hero__name">' + escapeHtml(user.full_name || 'Unknown') + '</div>' +
                    (user.public_id
                        ? '<div class="ud-hero__id"><i class="fas fa-hashtag" style="font-size:10px;"></i> ' + escapeHtml(user.public_id) + '</div>'
                        : '') +
                    '<div class="ud-hero__badges">' + badges.join('') + '</div>' +
                '</div>' +
            '</div>' +

            '<div class="ud-stats">' +
                '<div class="ud-stat"><div class="ud-stat__value">' + (s.quiz_attempts || 0) + '</div><div class="ud-stat__label">Quizzes</div></div>' +
                '<div class="ud-stat"><div class="ud-stat__value">' + (s.live_quiz_attempts || 0) + '</div><div class="ud-stat__label">Live</div></div>' +
                '<div class="ud-stat"><div class="ud-stat__value">' + (s.saved_questions || 0) + '</div><div class="ud-stat__label">Saved</div></div>' +
                '<div class="ud-stat"><div class="ud-stat__value">' + (user.total_points || 0) + '</div><div class="ud-stat__label">Points</div></div>' +
            '</div>' +

            '<div class="ud-section">' +
                '<div class="ud-section__title"><i class="fas fa-id-card"></i> Contact & profile</div>' +
                '<div class="ud-kv-list">' +
                    '<div class="ud-kv"><span class="ud-kv__k">Phone</span><span class="ud-kv__v">' + phone + '</span></div>' +
                    '<div class="ud-kv"><span class="ud-kv__k">School</span><span class="ud-kv__v">' + school + '</span></div>' +
                    '<div class="ud-kv"><span class="ud-kv__k">Grade</span><span class="ud-kv__v">' + grade + '</span></div>' +
                    '<div class="ud-kv"><span class="ud-kv__k">City</span><span class="ud-kv__v">' + city + '</span></div>' +
                    '<div class="ud-kv"><span class="ud-kv__k">Location</span><span class="ud-kv__v">' + location + '</span></div>' +
                    '<div class="ud-kv"><span class="ud-kv__k">Curriculum</span><span class="ud-kv__v">' + curriculum + '</span></div>' +
                '</div>' +
            '</div>' +

            '<div class="ud-section">' +
                '<div class="ud-section__title"><i class="fas fa-layer-group"></i> Tier</div>' +
                '<div class="ud-tier-picker">' + tierPickerHtml + '</div>' +
            '</div>' +

            '<div class="ud-section">' +
                '<div class="ud-section__title"><i class="fas fa-bolt"></i> Quick actions</div>' +
                '<div class="ud-actions">' + verifyBtn + adminBtn + logoutBtn + '</div>' +
                notifyHtml +
            '</div>' +

            '<div class="ud-section">' +
                '<div class="ud-section__title"><i class="fas fa-clock"></i> Activity</div>' +
                '<div class="ud-kv-list">' +
                    '<div class="ud-kv"><span class="ud-kv__k">Joined</span><span class="ud-kv__v">' + fmtDate(user.created_at) + '</span></div>' +
                    '<div class="ud-kv"><span class="ud-kv__k">Last login</span><span class="ud-kv__v">' + fmtDate(user.last_login_at) + '</span></div>' +
                '</div>' +
            '</div>';

        // ---- FOOTER ----
        footEl.innerHTML =
            '<a href="' + escapeHtml(user.detail_url || '#') + '" class="admin-btn secondary">' +
                '<i class="fas fa-external-link-alt"></i> Open full profile' +
            '</a>';

        // ---- WIRE ACTIONS ----
        wireActions();
    }

    // ------------------------------------------------------------
    // WIRE ACTIONS
    // ------------------------------------------------------------
    function wireActions() {
        // Tier picker
        bodyEl.querySelectorAll('.ud-tier-btn[data-tier]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                if (btn.disabled) return;
                const tier = btn.getAttribute('data-tier');
                if (!tier || !currentUserId) return;
                runAction('set_tier', { tier: tier }, 'Switching to ' + tier + '…');
            });
        });

        // Action buttons
        bodyEl.querySelectorAll('.ud-action[data-action]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const action = btn.getAttribute('data-action');
                if (!action || !currentUserId) return;

                const confirms = {
                    'verify': { title: 'Verify this account?', message: 'The user will be able to log in immediately.' },
                    'unverify': { title: 'Unverify this account?', message: 'The user will be blocked from logging in.', danger: true },
                    'toggle_admin': { title: 'Change admin privileges?', message: 'This is a significant permission change.', danger: true },
                    'force_logout': { title: 'Force logout now?', message: 'The user will be signed out on their next request.', danger: true },
                };

                const cfg = confirms[action];
                const proceed = cfg
                    ? confirmAction(cfg)
                    : Promise.resolve(true);

                proceed.then(function (ok) {
                    if (!ok) return;
                    runAction(action, {});
                });
            });
        });

        // Notify send
        const sendBtn = document.getElementById('udNotifySend');
        if (sendBtn) {
            sendBtn.addEventListener('click', function () {
                const titleEl = document.getElementById('udNotifyTitle');
                const bodyFieldEl = document.getElementById('udNotifyBody');
                const title = (titleEl.value || '').trim();
                const body = (bodyFieldEl.value || '').trim();
                if (!title || !body) {
                    toast('Title and message are required.', 'warning');
                    return;
                }
                runAction('notify', { title: title, body: body }, 'Sending…');
                titleEl.value = '';
                bodyFieldEl.value = '';
            });
        }
    }

    // ------------------------------------------------------------
    // ACTION RUNNER
    // ------------------------------------------------------------
    function runAction(action, payload, loadingMsg) {
        if (!currentUserId) return;
        if (loadingMsg) toast(loadingMsg, 'info');

        // Disable all interactive elements briefly
        const controls = bodyEl.querySelectorAll('button, input, textarea, a.admin-btn');
        controls.forEach(function (el) { el.disabled = true; });

        const url = '/admin/users/' + encodeURIComponent(currentUserId) + '/quick-action';
        const body = Object.assign({ action: action }, payload);

        fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-CSRF-Token': csrfToken,
            },
            body: JSON.stringify(body),
        })
        .then(function (r) {
            return r.json().catch(function () { return {}; })
                .then(function (data) { return { ok: r.ok, status: r.status, data: data }; });
        })
        .then(function (res) {
            if (res.ok && res.data && res.data.success) {
                toast(res.data.message || 'Done', 'success');
                // Update the table row behind (verified badge, tier pill, name)
                updateRow(currentUserId, res.data.row_updates || null);
                // Refresh the drawer from server (source of truth)
                fetchUser(currentUserId);
            } else {
                const err = (res.data && res.data.error) || 'Action failed';
                toast(err, 'error');
                // Re-enable controls so user can retry
                controls.forEach(function (el) { el.disabled = false; });
            }
        })
        .catch(function () {
            toast('Network error', 'error');
            controls.forEach(function (el) { el.disabled = false; });
        });
    }

    // ------------------------------------------------------------
    // FETCH USER
    // ------------------------------------------------------------
    function fetchUser(userId) {
        const url = '/admin/users/' + encodeURIComponent(userId) + '/json';
        fetch(url, {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' },
        })
        .then(function (r) {
            return r.json().catch(function () { return {}; })
                .then(function (data) { return { ok: r.ok, data: data }; });
        })
        .then(function (res) {
            if (res.ok && res.data && !res.data.error) {
                render(res.data);
            } else {
                renderError(res.data && res.data.error);
            }
        })
        .catch(function () {
            renderError('Network error');
        });
    }

    // ------------------------------------------------------------
    // UPDATE ROW BEHIND
    // ------------------------------------------------------------
    function updateRow(userId, updates) {
        const row = document.querySelector('tr[data-user-id="' + userId + '"]');
        if (!row) return;

        // Always update the "verified" cell and tier pill from the server-rendered
        // attributes — simplest safe approach is to re-fetch these from the drawer
        // response. If not provided, fall through silently.
        if (!updates) return;

        if (updates.verified !== undefined) {
            const cell = row.querySelector('[data-cell="verified"]');
            if (cell) {
                cell.innerHTML = updates.verified
                    ? '<span class="admin-chip green" title="Verified">✅</span>'
                    : '<span class="admin-chip amber" title="Unverified">⏳</span>';
            }
        }
        if (updates.tier) {
            const pill = row.querySelector('[data-cell="tier"] .admin-pill');
            if (pill) {
                pill.className = 'admin-pill ' + updates.tier;
                pill.textContent = updates.tier;
            }
        }
        if (updates.is_admin !== undefined) {
            const nameCell = row.querySelector('[data-cell="name"] .admin-star');
            if (nameCell) {
                nameCell.style.display = updates.is_admin ? 'inline' : 'none';
            }
        }
    }

    // ------------------------------------------------------------
    // INIT
    // ------------------------------------------------------------
    function init() {
        ensureDrawerRefs();
        if (!drawerEl) return;

        // CSRF token (from meta tag)
        const meta = document.querySelector('meta[name="csrf-token"]');
        csrfToken = meta ? meta.content : '';

        // Backdrop click + X button
        backdropEl.addEventListener('click', closeDrawer);
        const closeBtn = drawerEl.querySelector('.ud-head__close');
        if (closeBtn) closeBtn.addEventListener('click', closeDrawer);

        // Esc to close
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && drawerEl.classList.contains('is-open')) {
                closeDrawer();
            }
        });

        // Row click — event delegation
        document.addEventListener('click', function (e) {
            const row = e.target.closest('tr.ud-clickable[data-user-id]');
            if (!row) return;

            // Skip interactive elements
            if (e.target.closest('a, button, input, label, [data-no-drawer]')) return;

            const uid = row.getAttribute('data-user-id');
            if (!uid) return;
            openDrawer(uid, row);
        });

        // Keyboard — Enter or Space on focused row
        document.addEventListener('keydown', function (e) {
            const row = e.target.closest('tr.ud-clickable[data-user-id]');
            if (!row) return;
            if (e.key !== 'Enter' && e.key !== ' ') return;
            if (e.target.closest('a, button, input, label')) return;
            e.preventDefault();
            const uid = row.getAttribute('data-user-id');
            if (uid) openDrawer(uid, row);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();