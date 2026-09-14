/* ============================================================
   static/js/admin/community.js
   Community domain interactions:
   - Groups list: feature toggle, active toggle, delete, bulk
   - Group edit: full-form submit, live preview
   - Reports: resolve/dismiss modals
   - Achievements: delete confirmation
   - Leaderboard: point reset confirmation
   ============================================================ */

(function () {
    'use strict';

    const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    function confirm(opts) {
        return window.AdminCore.confirm(opts);
    }
    function toast(msg, tone) {
        if (window.AdminCore && AdminCore.toast) AdminCore.toast(msg, tone);
    }
    function fetchJSON(url, opts) {
        opts = opts || {};
        opts.headers = Object.assign(
            { 'X-CSRF-Token': CSRF },
            opts.headers || {}
        );
        if (opts.body && !opts.headers['Content-Type']) {
            opts.headers['Content-Type'] = 'application/json';
        }
        return fetch(url, opts).then(function (r) {
            return r.json().then(function (data) {
                return { ok: r.ok, status: r.status, data: data };
            });
        });
    }

    // ------------------------------------------------------------
    // GROUPS LIST — feature toggle
    // ------------------------------------------------------------
    function initGroupFeatureToggle() {
        document.querySelectorAll('[data-gr-feature]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const id = this.getAttribute('data-gr-feature');
                const isFeatured = this.getAttribute('data-gr-feature-state') === '1';
                const verb = isFeatured ? 'Unfeature' : 'Feature';

                confirm({
                    title: verb + ' this group?',
                    message: isFeatured
                        ? 'It will be removed from the featured section.'
                        : 'It will appear at the top of the student list.',
                    confirm: verb,
                }).then(function (ok) {
                    if (!ok) return;
                    btn.disabled = true;
                    const original = btn.innerHTML;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

                    fetchJSON('/admin/groups/api/' + id + '/toggle-featured', {
                        method: 'POST',
                        body: JSON.stringify({}),
                    }).then(function (res) {
                        if (res.ok && res.data && res.data.success) {
                            toast(verb + 'd.', 'success');
                            setTimeout(function () { window.location.reload(); }, 600);
                        } else {
                            toast((res.data && res.data.error) || 'Could not toggle.', 'error');
                            btn.disabled = false;
                            btn.innerHTML = original;
                        }
                    }).catch(function () {
                        toast('Network error.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = original;
                    });
                });
            });
        });
    }

    // ------------------------------------------------------------
    // GROUPS LIST — active toggle
    // ------------------------------------------------------------
    function initGroupActiveToggle() {
        document.querySelectorAll('[data-gr-toggle-active]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const id = this.getAttribute('data-gr-toggle-active');
                const isActive = this.getAttribute('data-gr-active-state') === '1';
                const verb = isActive ? 'Deactivate' : 'Activate';

                confirm({
                    title: verb + ' this group?',
                    message: isActive
                        ? 'Students will no longer see it.'
                        : 'It will become visible to students again.',
                    confirm: verb,
                    danger: isActive,
                }).then(function (ok) {
                    if (!ok) return;
                    btn.disabled = true;
                    const original = btn.innerHTML;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

                    fetchJSON('/admin/groups/api/' + id + '/toggle-active', {
                        method: 'POST',
                        body: JSON.stringify({}),
                    }).then(function (res) {
                        if (res.ok && res.data && res.data.success) {
                            toast(verb + 'd.', 'success');
                            setTimeout(function () { window.location.reload(); }, 600);
                        } else {
                            toast((res.data && res.data.error) || 'Could not toggle.', 'error');
                            btn.disabled = false;
                            btn.innerHTML = original;
                        }
                    }).catch(function () {
                        toast('Network error.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = original;
                    });
                });
            });
        });
    }

    // ------------------------------------------------------------
    // GROUPS LIST — delete
    // ------------------------------------------------------------
    function initGroupDelete() {
        document.querySelectorAll('[data-gr-delete]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const id = this.getAttribute('data-gr-delete');
                const name = this.getAttribute('data-gr-name') || ('group #' + id);

                confirm({
                    title: 'Delete "' + name + '"?',
                    message: 'This group will be permanently removed. The action cannot be undone.',
                    confirm: 'Delete group',
                    danger: true,
                }).then(function (ok) {
                    if (!ok) return;
                    btn.disabled = true;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

                    fetchJSON('/admin/groups/api/' + id, {
                        method: 'DELETE',
                    }).then(function (res) {
                        if (res.ok && res.data && res.data.success) {
                            toast('Group deleted.', 'success');
                            setTimeout(function () { window.location.reload(); }, 600);
                        } else {
                            toast((res.data && res.data.error) || 'Delete failed.', 'error');
                            btn.disabled = false;
                            btn.innerHTML = '<i class="fas fa-trash"></i>';
                        }
                    }).catch(function () {
                        toast('Network error.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = '<i class="fas fa-trash"></i>';
                    });
                });
            });
        });
    }

    // ------------------------------------------------------------
    // GROUPS LIST — bulk actions
    // ------------------------------------------------------------
    function initGroupBulk() {
        const master = document.getElementById('grSelectAll');
        const rows = document.querySelectorAll('[data-gr-row]');
        if (!rows.length) return;

        const countEl = document.getElementById('grBulkCount');
        const actionBtns = document.querySelectorAll('[data-gr-bulk]');

        function update() {
            const checked = document.querySelectorAll('[data-gr-row]:checked');
            const n = checked.length;
            if (countEl) countEl.textContent = n;
            actionBtns.forEach(function (b) { b.disabled = n === 0; });
            if (master) {
                master.checked = n > 0 && n === rows.length;
                master.indeterminate = n > 0 && n < rows.length;
            }
        }

        if (master) {
            master.addEventListener('change', function () {
                rows.forEach(function (cb) { cb.checked = master.checked; });
                update();
            });
        }
        rows.forEach(function (cb) { cb.addEventListener('change', update); });

        actionBtns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                const action = this.getAttribute('data-gr-bulk');
                const checked = document.querySelectorAll('[data-gr-row]:checked');
                const ids = Array.from(checked).map(function (c) { return parseInt(c.value, 10); });
                if (!ids.length) return;

                const verb = action === 'activate' ? 'Activate'
                           : action === 'deactivate' ? 'Deactivate'
                           : action === 'feature' ? 'Feature'
                           : 'Delete';

                confirm({
                    title: verb + ' ' + ids.length + ' group' + (ids.length === 1 ? '' : 's') + '?',
                    message: 'This will apply to all selected groups.',
                    confirm: verb,
                    danger: action === 'delete',
                }).then(function (ok) {
                    if (!ok) return;
                    btn.disabled = true;
                    const original = btn.innerHTML;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

                    fetchJSON('/admin/groups/api/bulk', {
                        method: 'POST',
                        body: JSON.stringify({ action: action, group_ids: ids }),
                    }).then(function (res) {
                        if (res.ok && res.data && res.data.success) {
                            toast(res.data.message || 'Done.', 'success');
                            setTimeout(function () { window.location.reload(); }, 700);
                        } else {
                            toast((res.data && res.data.error) || 'Bulk action failed.', 'error');
                            btn.disabled = false;
                            btn.innerHTML = original;
                        }
                    }).catch(function () {
                        toast('Network error.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = original;
                    });
                });
            });
        });

        update();
    }

    // ------------------------------------------------------------
    // GROUP EDIT FORM — submit + live preview
    // ------------------------------------------------------------
    function initGroupForm() {
        const form = document.getElementById('groupForm');
        if (!form) return;

        const isNew = form.getAttribute('data-is-new') === '1';
        const groupId = form.getAttribute('data-group-id');
        const submitBtn = document.getElementById('grSubmitBtn');

        // ----- Live preview -----
        const preview = {
            icon:       document.getElementById('grPreviewIcon'),
            name:       document.getElementById('grPreviewName'),
            platform:   document.getElementById('grPreviewPlatform'),
            desc:       document.getElementById('grPreviewDesc'),
            meta:       document.getElementById('grPreviewMeta'),
            badges:     document.getElementById('grPreviewBadges'),
        };

        function readValue(id) {
            const el = document.getElementById(id);
            return el ? el.value.trim() : '';
        }
        function readChecked(id) {
            const el = document.getElementById(id);
            return !!(el && el.checked);
        }
        function readPlatform() {
            const r = form.querySelector('input[name="platform"]:checked');
            return r ? r.value : 'whatsapp';
        }
        function readTier() {
            const el = document.getElementById('grTier');
            return el ? el.value : 'free';
        }
        function readCurriculum() {
            const el = document.getElementById('grCurriculum');
            return el ? el.value : '';
        }

        function esc(s) {
            return String(s == null ? '' : s)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;')
                .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }

        function updatePreview() {
            if (!preview.name) return;

            const icon = readValue('grIcon') || '📚';
            const name = readValue('grName') || 'Group name';
            const platform = readPlatform();
            const desc = readValue('grDescription') || 'Description will appear here…';
            const category = readValue('grCategory');
            const tier = readTier();
            const curriculum = readCurriculum();
            const isActive = readChecked('grIsActive');
            const isFeatured = readChecked('grIsFeatured');

            preview.icon.textContent = icon;
            preview.name.textContent = name;
            preview.desc.textContent = desc;

            const platTag = platform === 'telegram'
                ? '<span class="gr-platform-tag telegram"><i class="fab fa-telegram-plane"></i> Telegram</span>'
                : '<span class="gr-platform-tag whatsapp"><i class="fab fa-whatsapp"></i> WhatsApp</span>';
            preview.platform.innerHTML = platTag;

            const metaParts = [];
            if (category) {
                metaParts.push('<span><i class="fas fa-folder"></i> ' + esc(category) + '</span>');
            }
            if (curriculum) {
                metaParts.push('<span><i class="fas fa-graduation-cap"></i> ' + esc(curriculum) + '</span>');
            }
            preview.meta.innerHTML = metaParts.length
                ? metaParts.join('')
                : '<span style="color:var(--text-muted);">No category yet</span>';

            const badges = [];
            if (tier && tier !== 'free') {
                badges.push('<span class="admin-pill ' + esc(tier) + '">' + esc(tier) + '</span>');
            }
            if (isFeatured) badges.push('<span class="admin-chip amber">⭐ Featured</span>');
            if (!isActive)  badges.push('<span class="admin-chip neutral">Inactive</span>');
            preview.badges.innerHTML = badges.join(' ');
        }

        // Attach listeners
        ['grName','grIcon','grDescription','grCategory','grInviteLink',
         'grSubjects','grGroupType','grDisplayOrder'].forEach(function (id) {
            const el = document.getElementById(id);
            if (el) el.addEventListener('input', updatePreview);
        });
        ['grTier','grCurriculum'].forEach(function (id) {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', updatePreview);
        });
        form.querySelectorAll('input[name="platform"]').forEach(function (r) {
            r.addEventListener('change', updatePreview);
        });
        ['grIsActive','grIsFeatured'].forEach(function (id) {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', updatePreview);
        });

        updatePreview();

        // ----- Submit -----
        form.addEventListener('submit', function (e) {
            e.preventDefault();

            // Validate
            const name = readValue('grName');
            const invite = readValue('grInviteLink');
            if (name.length < 3) {
                toast('Group name must be at least 3 characters.', 'warning');
                document.getElementById('grName').focus();
                return;
            }
            if (!/^https:\/\//i.test(invite)) {
                toast('Invite link must start with https://', 'warning');
                document.getElementById('grInviteLink').focus();
                return;
            }

            const payload = {
                name: name,
                icon: readValue('grIcon') || '📚',
                description: readValue('grDescription'),
                category: readValue('grCategory'),
                platform: readPlatform(),
                invite_link: invite,
                curriculum: readCurriculum(),
                tier_required: readTier(),
                subjects: readValue('grSubjects'),
                group_type: readValue('grGroupType'),
                display_order: parseInt(readValue('grDisplayOrder'), 10) || 0,
                is_active: readChecked('grIsActive') ? 1 : 0,
                is_featured: readChecked('grIsFeatured') ? 1 : 0,
            };

            submitBtn.disabled = true;
            const original = submitBtn.innerHTML;
            submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving…';

            const url = isNew
                ? '/admin/groups/api'
                : '/admin/groups/api/' + groupId;
            const method = isNew ? 'POST' : 'POST'; // API accepts POST for update

            fetchJSON(url, {
                method: method,
                body: JSON.stringify(payload),
            }).then(function (res) {
                if (res.ok && res.data && res.data.success) {
                    toast(res.data.message || 'Saved.', 'success');
                    setTimeout(function () {
                        window.location.href = res.data.redirect
                            || '/admin/groups';
                    }, 700);
                } else {
                    toast((res.data && res.data.error) || 'Could not save.', 'error');
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = original;
                }
            }).catch(function () {
                toast('Network error.', 'error');
                submitBtn.disabled = false;
                submitBtn.innerHTML = original;
            });
        });
    }

    // ------------------------------------------------------------
    // REPORTS — resolve/dismiss
    // ------------------------------------------------------------
    function initReportActions() {
        function openReplyModal(action) {
            return new Promise(function (resolve) {
                const backdrop = document.createElement('div');
                backdrop.className = 'admin-modal-backdrop';
                const titleText = action === 'resolve' ? 'Resolve report' : 'Dismiss report';
                const confirmText = action === 'resolve' ? 'Resolve' : 'Dismiss';

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
                                (action === 'resolve' ? 'success' : 'secondary') +
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
                openReplyModal('resolve').then(function (result) {
                    if (!result) return;
                    submitReportAction(id, 'resolve', result.reply, btn);
                });
            });
        });

        document.querySelectorAll('[data-dismiss-report]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const id = this.getAttribute('data-dismiss-report');
                openReplyModal('dismiss').then(function (result) {
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
            .then(function () {
                toast(action === 'resolve' ? 'Report resolved.' : 'Report dismissed.', 'success');
                setTimeout(function () { window.location.reload(); }, 700);
            })
            .catch(function () {
                toast('Network error.', 'error');
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

                confirm({
                    title: 'Delete "' + name + '"?',
                    message: 'Users who unlocked this achievement will lose the badge. This cannot be undone.',
                    confirm: 'Delete',
                    danger: true,
                }).then(function (ok) {
                    if (!ok) return;
                    btn.disabled = true;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

                    fetchJSON('/admin/achievements/' + id + '/delete', {
                        method: 'POST',
                        body: JSON.stringify({}),
                    }).then(function (res) {
                        if (res.ok && res.data && res.data.success) {
                            toast('Achievement deleted.', 'success');
                            setTimeout(function () { window.location.reload(); }, 700);
                        } else {
                            toast((res.data && res.data.error) || 'Delete failed.', 'error');
                            btn.disabled = false;
                            btn.innerHTML = '<i class="fas fa-trash"></i>';
                        }
                    }).catch(function () {
                        toast('Network error.', 'error');
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

                confirm({
                    title: 'Reset points for ' + name + '?',
                    message: 'Their total_points will be set to 0. Quiz history is not affected.',
                    confirm: 'Reset to 0',
                    danger: true,
                }).then(function (ok) {
                    if (!ok) return;
                    btn.disabled = true;

                    fetchJSON('/admin/leaderboard/reset-points/' + userId, {
                        method: 'POST',
                        body: JSON.stringify({}),
                    }).then(function (res) {
                        if (res.ok && res.data && res.data.success) {
                            toast('Points reset.', 'success');
                            setTimeout(function () { window.location.reload(); }, 700);
                        } else {
                            toast((res.data && res.data.error) || 'Reset failed.', 'error');
                            btn.disabled = false;
                        }
                    }).catch(function () {
                        toast('Network error.', 'error');
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
        initGroupActiveToggle();
        initGroupDelete();
        initGroupBulk();
        initGroupForm();
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