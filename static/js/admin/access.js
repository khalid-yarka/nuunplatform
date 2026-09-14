/* ============================================================
   static/js/admin/access.js
   Access/ domain interactions:
   - Permission grid live search
   - Tri-state radio visual feedback
   - Override chip popovers
   - Impersonation stop confirmation
   - Public ID edit flow (user detail)
   ============================================================ */

(function () {
    'use strict';

    // ------------------------------------------------------------
    // PERMISSION GRID SEARCH
    // Filters .perm-row elements by label + description.
    // Hides groups with zero visible rows.
    // ------------------------------------------------------------
    function initPermissionSearch() {
        const input = document.getElementById('permSearch');
        if (!input) return;

        const groups = document.querySelectorAll('.perm-group');

        const apply = window.AdminCore.debounce(function () {
            const q = (input.value || '').trim().toLowerCase();

            groups.forEach(function (group) {
                let visible = 0;
                group.querySelectorAll('.perm-row').forEach(function (row) {
                    const label = row.querySelector('.perm-label');
                    const desc  = row.querySelector('.perm-desc');
                    const hay = (
                        (label ? label.textContent : '') + ' ' +
                        (desc  ? desc.textContent  : '')
                    ).toLowerCase();

                    if (!q || hay.indexOf(q) !== -1) {
                        row.classList.remove('is-hidden');
                        visible++;
                    } else {
                        row.classList.add('is-hidden');
                    }
                });
                group.classList.toggle('is-hidden', visible === 0);
            });
        }, 120);

        input.addEventListener('input', apply);

        // Esc clears the search
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                input.value = '';
                apply();
            }
        });
    }

    // ------------------------------------------------------------
    // TRI-STATE RADIO VISUAL FEEDBACK
    // Adds .active to the selected label so CSS can style it.
    // (Radios are visually hidden; the wrapping label is the
    // interactive control.)
    // ------------------------------------------------------------
    function initTriState() {
        const stateContainers = document.querySelectorAll('.perm-state');
        if (!stateContainers.length) return;

        stateContainers.forEach(function (container) {
            const radios = container.querySelectorAll('input[type="radio"]');
            const labels = container.querySelectorAll('.perm-radio');

            const sync = function () {
                labels.forEach(function (label) {
                    const radio = label.querySelector('input[type="radio"]');
                    label.classList.toggle('active', radio.checked);
                });
            };

            radios.forEach(function (r) {
                r.addEventListener('change', sync);
            });

            // Sync initial state
            sync();
        });
    }

    // ------------------------------------------------------------
    // OVERRIDE CHIP POPOVERS
    // Each chip has [data-popover="override-<uid>"] and a
    // sibling .admin-popover with that id.
    // ------------------------------------------------------------
    function initOverridePopovers() {
        document.querySelectorAll('.perm-override-chip').forEach(function (chip) {
            // Ensure the popover exists as a sibling
            let pop = chip.parentElement.querySelector('.admin-popover');
            if (!pop) {
                // Build one from the chip's data if present
                const key = chip.getAttribute('data-popover');
                if (key) {
                    pop = document.getElementById(key);
                }
            }
            if (!pop) return;

            chip.setAttribute('data-popover', pop.id);
            chip.setAttribute('aria-expanded', 'false');
        });
    }

    // ------------------------------------------------------------
    // IMPERSONATION BANNER — STOP BUTTON
    // ------------------------------------------------------------
    function initImpersonationStop() {
        const btn = document.getElementById('stopImpersonationBtn');
        if (!btn) return;

        btn.addEventListener('click', function () {
            window.AdminCore.confirm({
                title: 'Stop impersonating?',
                message: 'You will return to your own admin account.',
                confirm: 'Stop',
                danger: false,
            }).then(function (ok) {
                if (!ok) return;

                const csrf = document.querySelector('meta[name="csrf-token"]');
                const token = csrf ? csrf.content : '';

                btn.disabled = true;
                btn.textContent = 'Stopping…';

                fetch('/admin/impersonate/stop', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': token,
                    },
                    body: JSON.stringify({}),
                })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data && data.success) {
                        window.location.href = data.redirect || '/admin';
                    } else {
                        window.AdminCore.toast(
                            (data && data.error) || 'Could not stop impersonation.',
                            'error'
                        );
                        btn.disabled = false;
                        btn.textContent = 'Stop impersonating';
                    }
                })
                .catch(function () {
                    window.AdminCore.toast('Network error.', 'error');
                    btn.disabled = false;
                    btn.textContent = 'Stop impersonating';
                });
            });
        });
    }

    // ------------------------------------------------------------
    // PUBLIC ID EDIT FLOW (user detail)
    // ------------------------------------------------------------
    function initPublicIdEditor() {
        const editBtn = document.getElementById('editPublicIdBtn');
        if (!editBtn) return;

        const input = document.getElementById('publicIdInput');
        const saveBtn = document.getElementById('savePublicIdBtn');
        const cancelBtn = document.getElementById('cancelPublicIdBtn');
        const display = document.getElementById('publicIdDisplay');

        if (!input || !saveBtn || !cancelBtn || !display) return;

        const originalValue = input.value;

        editBtn.addEventListener('click', function () {
            display.style.display = 'none';
            editBtn.style.display = 'none';
            input.style.display = 'inline-block';
            saveBtn.style.display = 'inline-flex';
            cancelBtn.style.display = 'inline-flex';
            input.focus();
        });

        cancelBtn.addEventListener('click', function () {
            input.value = originalValue;
            input.style.display = 'none';
            saveBtn.style.display = 'none';
            cancelBtn.style.display = 'none';
            display.style.display = '';
            editBtn.style.display = 'inline-flex';
        });

        saveBtn.addEventListener('click', function () {
            const newValue = (input.value || '').trim().toUpperCase();
            if (!/^[A-Z0-9]{4}$/.test(newValue)) {
                window.AdminCore.toast('ID must be exactly 4 uppercase letters or digits.', 'error');
                return;
            }

            const csrf = document.querySelector('meta[name="csrf-token"]');
            const token = csrf ? csrf.content : '';
            const adminId = saveBtn.getAttribute('data-admin-id');

            saveBtn.disabled = true;
            saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

            fetch('/admin/users/' + adminId + '/public-id', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': token,
                },
                body: JSON.stringify({ public_id: newValue }),
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data && data.success) {
                    window.AdminCore.toast('Public ID updated.', 'success');
                    setTimeout(function () { window.location.reload(); }, 800);
                } else {
                    window.AdminCore.toast(
                        (data && data.error) || 'Could not update Public ID.',
                        'error'
                    );
                    saveBtn.disabled = false;
                    saveBtn.innerHTML = '<i class="fas fa-check"></i> Save';
                }
            })
            .catch(function () {
                window.AdminCore.toast('Network error.', 'error');
                saveBtn.disabled = false;
                saveBtn.innerHTML = '<i class="fas fa-check"></i> Save';
            });
        });
    }

    // ------------------------------------------------------------
    // BOOT
    // ------------------------------------------------------------
    function boot() {
        initPermissionSearch();
        initTriState();
        initOverridePopovers();
        initImpersonationStop();
        initPublicIdEditor();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();