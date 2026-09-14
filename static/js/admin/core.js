/* ============================================================
   static/js/admin/core.js
   Shared admin shell utilities.
   - Toast notifications
   - Confirm modal
   - Popover controller
   - Dirty-state tracker + floating save bar
   - Form helpers (auto-submit on filter change)
   - Keyboard shortcuts (Esc closes modals/popovers)
   No external dependencies.
   ============================================================ */

(function () {
    'use strict';

    // ------------------------------------------------------------
    // TOAST HOST
    // ------------------------------------------------------------
    let _toastHost = null;

    function ensureToastHost() {
        if (_toastHost && document.body.contains(_toastHost)) return _toastHost;
        _toastHost = document.createElement('div');
        _toastHost.className = 'admin-toast-host';
        _toastHost.setAttribute('aria-live', 'polite');
        document.body.appendChild(_toastHost);
        return _toastHost;
    }

    const ICONS = {
        success: 'fa-check-circle',
        error:   'fa-times-circle',
        warning: 'fa-exclamation-triangle',
        info:    'fa-info-circle',
    };

    function toast(message, type, duration) {
        const host = ensureToastHost();
        type = type || 'info';
        duration = typeof duration === 'number' ? duration : 4000;

        const el = document.createElement('div');
        el.className = 'admin-toast ' + type;
        el.innerHTML =
            '<i class="fas ' + (ICONS[type] || ICONS.info) + '"></i>' +
            '<span class="txt"></span>' +
            '<button class="close" aria-label="Dismiss">&times;</button>';

        el.querySelector('.txt').textContent = String(message || '');

        const remove = () => {
            if (!el.parentNode) return;
            el.style.transition = 'opacity 0.25s ease, transform 0.25s ease';
            el.style.opacity = '0';
            el.style.transform = 'translateX(30px)';
            setTimeout(() => el.remove(), 260);
        };

        el.querySelector('.close').addEventListener('click', remove);
        host.appendChild(el);

        if (duration > 0) setTimeout(remove, duration);
        return el;
    }

    // ------------------------------------------------------------
    // CONFIRM MODAL
    // ------------------------------------------------------------
    function confirmModal(opts) {
        opts = opts || {};
        const title    = opts.title    || 'Are you sure?';
        const message  = opts.message  || '';
        const confirm  = opts.confirm  || 'Confirm';
        const cancel   = opts.cancel   || 'Cancel';
        const danger   = !!opts.danger;

        return new Promise(function (resolve) {
            const backdrop = document.createElement('div');
            backdrop.className = 'admin-modal-backdrop';
            backdrop.innerHTML =
                '<div class="admin-modal" role="dialog" aria-modal="true">' +
                    '<div class="admin-modal-head">' +
                        '<h3>' + (danger ? '⚠️ ' : '') + escapeHtml(title) + '</h3>' +
                        (message ? '<div class="sub">' + escapeHtml(message) + '</div>' : '') +
                    '</div>' +
                    '<div class="admin-modal-actions">' +
                        '<button type="button" class="admin-btn secondary" data-act="cancel">' +
                            escapeHtml(cancel) +
                        '</button>' +
                        '<button type="button" class="admin-btn ' +
                            (danger ? 'danger' : 'primary') +
                            '" data-act="confirm">' +
                            escapeHtml(confirm) +
                        '</button>' +
                    '</div>' +
                '</div>';

            document.body.appendChild(backdrop);
            requestAnimationFrame(() => backdrop.classList.add('is-open'));

            const close = (result) => {
                backdrop.classList.remove('is-open');
                setTimeout(() => backdrop.remove(), 200);
                resolve(result);
            };

            backdrop.addEventListener('click', function (e) {
                if (e.target === backdrop) close(false);
                else if (e.target.closest('[data-act="cancel"]')) close(false);
                else if (e.target.closest('[data-act="confirm"]')) close(true);
            });

            const onKey = (e) => {
                if (e.key === 'Escape') { document.removeEventListener('keydown', onKey); close(false); }
            };
            document.addEventListener('keydown', onKey);
        });
    }

    // ------------------------------------------------------------
    // POPOVER CONTROLLER
    // Auto-positions [data-popover] triggers next to their target.
    // Auto-closes on click-outside, Esc, and scroll.
    // ------------------------------------------------------------
    let _activePopover = null;

    function closeActivePopover() {
        if (!_activePopover) return;
        _activePopover.pop.classList.remove('is-open');
        _activePopover = null;
    }

    function openPopover(trigger, pop) {
        closeActivePopover();

        pop.classList.add('is-open');
        const rect = trigger.getBoundingClientRect();
        const popRect = pop.getBoundingClientRect();

        // Horizontal: align right edge of popover with right edge of trigger
        let left = rect.right - popRect.width;
        let top = rect.bottom + 8;

        // Clamp to viewport
        if (left < 8) left = 8;
        if (left + popRect.width > window.innerWidth - 8) {
            left = window.innerWidth - popRect.width - 8;
        }
        if (top + popRect.height > window.innerHeight - 8) {
            top = rect.top - popRect.height - 8;
        }

        // Append to body for absolute positioning
        pop.style.position = 'fixed';
        pop.style.left = left + 'px';
        pop.style.top = top + 'px';
        document.body.appendChild(pop);

        _activePopover = { trigger: trigger, pop: pop };
        trigger.setAttribute('aria-expanded', 'true');
    }

    function initPopovers() {
        document.addEventListener('click', function (e) {
            const trigger = e.target.closest('[data-popover]');
            if (trigger) {
                e.preventDefault();
                const key = trigger.getAttribute('data-popover');
                // The popover lives as the next sibling or by id
                let pop = document.getElementById(key);
                if (!pop) {
                    const parent = trigger.parentElement;
                    if (parent) pop = parent.querySelector('.admin-popover');
                }
                if (!pop) return;
                if (_activePopover && _activePopover.pop === pop) {
                    closeActivePopover();
                    trigger.setAttribute('aria-expanded', 'false');
                    return;
                }
                openPopover(trigger, pop);
                return;
            }
            if (_activePopover && !e.target.closest('.admin-popover')) {
                closeActivePopover();
            }
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') closeActivePopover();
        });

        // Reposition on scroll (or close if the trigger is out of view)
        window.addEventListener('scroll', function () {
            if (!_activePopover) return;
            const rect = _activePopover.trigger.getBoundingClientRect();
            if (rect.bottom < 0 || rect.top > window.innerHeight) {
                closeActivePopover();
                return;
            }
            // Reposition
            const pop = _activePopover.pop;
            const popRect = pop.getBoundingClientRect();
            let left = rect.right - popRect.width;
            let top = rect.bottom + 8;
            if (left < 8) left = 8;
            if (left + popRect.width > window.innerWidth - 8) {
                left = window.innerWidth - popRect.width - 8;
            }
            if (top + popRect.height > window.innerHeight - 8) {
                top = rect.top - popRect.height - 8;
            }
            pop.style.left = left + 'px';
            pop.style.top = top + 'px';
        }, { passive: true });
    }

    // ------------------------------------------------------------
    // DIRTY-STATE TRACKER + FLOATING SAVE BAR
    // Usage:
    //   AdminCore.initDirtyForm(formEl, {
    //       barId: 'adminDirtyBar',
    //       onSave: () => formEl.requestSubmit()
    //   });
    //
    // The bar element must exist in the DOM with the given id.
    // It starts hidden and is toggled by the module.
    // ------------------------------------------------------------
    function initDirtyForm(form, opts) {
        opts = opts || {};
        const bar = opts.barId ? document.getElementById(opts.barId) : null;
        const countEl = bar ? bar.querySelector('.count') : null;
        const saveBtn = bar ? bar.querySelector('[data-act="save"]') : null;
        const discardBtn = bar ? bar.querySelector('[data-act="discard"]') : null;

        const initial = snapshot(form);

        function snapshot(el) {
            const data = {};
            el.querySelectorAll('input, select, textarea').forEach(function (field) {
                if (!field.name || field.name === 'csrf_token') return;
                const key = field.name + '::' + (field.value !== undefined ? field.value : '');
                if (field.type === 'checkbox' || field.type === 'radio') {
                    data[field.name + '::' + field.value] = field.checked;
                } else {
                    data[field.name] = field.value;
                }
            });
            return data;
        }

        function diffCount() {
            const current = snapshot(form);
            let n = 0;
            const keys = new Set([...Object.keys(initial), ...Object.keys(current)]);
            keys.forEach(function (k) {
                if (initial[k] !== current[k]) n++;
            });
            return n;
        }

        function update() {
            const n = diffCount();
            if (bar) {
                if (n > 0) {
                    bar.classList.add('is-visible');
                    if (countEl) countEl.textContent = n;
                } else {
                    bar.classList.remove('is-visible');
                }
            }
        }

        form.addEventListener('input', update, true);
        form.addEventListener('change', update, true);
        form.addEventListener('submit', function () {
            if (bar) bar.classList.remove('is-visible');
        });

        if (saveBtn) {
            saveBtn.addEventListener('click', function () {
                if (typeof opts.onSave === 'function') {
                    opts.onSave();
                } else {
                    form.requestSubmit ? form.requestSubmit() : form.submit();
                }
            });
        }

        if (discardBtn) {
            discardBtn.addEventListener('click', function () {
                confirmModal({
                    title: 'Discard changes?',
                    message: 'All unsaved changes will be lost.',
                    confirm: 'Discard',
                    cancel: 'Keep editing',
                    danger: true,
                }).then(function (ok) {
                    if (ok) window.location.reload();
                });
            });
        }

        // Warn on navigation
        window.addEventListener('beforeunload', function (e) {
            if (diffCount() > 0) {
                e.preventDefault();
                e.returnValue = '';
                return '';
            }
        });
    }

    // ------------------------------------------------------------
    // AUTO-SUBMIT ON FILTER CHANGE
    // Any <select> or <input type=date> with [data-autofilter]
    // submits its parent form on change.
    // ------------------------------------------------------------
    function initAutoFilters() {
        document.querySelectorAll('[data-autofilter]').forEach(function (el) {
            el.addEventListener('change', function () {
                const form = this.closest('form');
                if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
            });
        });
    }

    // ------------------------------------------------------------
    // DEBOUNCED SEARCH INPUTS
    // Inputs with [data-search-form] submit their form after N ms.
    // ------------------------------------------------------------
    function initSearchDebounce() {
        document.querySelectorAll('[data-search-form]').forEach(function (el) {
            const form = el.closest('form');
            if (!form) return;
            let timer = null;
            el.addEventListener('input', function () {
                clearTimeout(timer);
                timer = setTimeout(function () {
                    form.requestSubmit ? form.requestSubmit() : form.submit();
                }, 400);
            });
        });
    }

    // ------------------------------------------------------------
    // TABLE ROW SELECTION + BULK ACTIONS
    // Elements:
    //   [data-check-all]      — master checkbox
    //   [data-check-row]      — per-row checkbox
    //   [data-bulk-count]     — element that receives the count
    //   [data-bulk-action]    — buttons that are enabled when count > 0
    //   [data-bulk-form]      — form that receives the submitted action
    // ------------------------------------------------------------
    function initBulkSelection() {
        const master = document.querySelector('[data-check-all]');
        const rows = document.querySelectorAll('[data-check-row]');
        if (!rows.length) return;

        const countEl = document.querySelector('[data-bulk-count]');
        const actionButtons = document.querySelectorAll('[data-bulk-action]');
        const bulkForm = document.querySelector('[data-bulk-form]');
        const actionInput = bulkForm ? bulkForm.querySelector('[data-bulk-action-input]') : null;

        function update() {
            const checked = document.querySelectorAll('[data-check-row]:checked');
            const n = checked.length;

            if (countEl) countEl.textContent = n;
            actionButtons.forEach(function (b) { b.disabled = n === 0; });

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

        rows.forEach(function (cb) {
            cb.addEventListener('change', update);
        });

        actionButtons.forEach(function (btn) {
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                const action = this.getAttribute('data-bulk-action');
                const verb = this.getAttribute('data-bulk-verb') || action;
                const n = document.querySelectorAll('[data-check-row]:checked').length;
                if (!n) return;

                confirmModal({
                    title: 'Confirm ' + verb,
                    message: 'This will apply to ' + n + ' item' + (n === 1 ? '' : 's') + '.',
                    confirm: verb,
                    danger: this.classList.contains('danger'),
                }).then(function (ok) {
                    if (!ok || !bulkForm) return;
                    if (actionInput) actionInput.value = action;
                    bulkForm.requestSubmit ? bulkForm.requestSubmit() : bulkForm.submit();
                });
            });
        });

        update();
    }

    // ------------------------------------------------------------
    // COPY TO CLIPBOARD
    // Any element with [data-copy="text"] copies on click.
    // ------------------------------------------------------------
    function initCopyButtons() {
        document.addEventListener('click', function (e) {
            const btn = e.target.closest('[data-copy]');
            if (!btn) return;
            e.preventDefault();
            const text = btn.getAttribute('data-copy') || '';
            if (!text) return;

            const done = () => {
                const original = btn.innerHTML;
                btn.innerHTML = '<i class="fas fa-check"></i>';
                btn.classList.add('copied');
                toast('Copied to clipboard', 'success', 1800);
                setTimeout(function () {
                    btn.innerHTML = original;
                    btn.classList.remove('copied');
                }, 1400);
            };

            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(done).catch(function () {
                    fallbackCopy(text);
                    done();
                });
            } else {
                fallbackCopy(text);
                done();
            }
        });
    }

    function fallbackCopy(text) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); } catch (e) {}
        document.body.removeChild(ta);
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

    function debounce(fn, wait) {
        let t = null;
        return function () {
            const ctx = this;
            const args = arguments;
            clearTimeout(t);
            t = setTimeout(function () { fn.apply(ctx, args); }, wait);
        };
    }

    // ------------------------------------------------------------
    // BOOT
    // ------------------------------------------------------------
    function boot() {
        initPopovers();
        initAutoFilters();
        initSearchDebounce();
        initBulkSelection();
        initCopyButtons();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    // ------------------------------------------------------------
    // PUBLIC API
    // ------------------------------------------------------------
    window.AdminCore = {
        toast: toast,
        confirm: confirmModal,
        initDirtyForm: initDirtyForm,
        escapeHtml: escapeHtml,
        debounce: debounce,
        copy: function (text) {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                return navigator.clipboard.writeText(text);
            }
            fallbackCopy(text);
            return Promise.resolve();
        },
    };
})();