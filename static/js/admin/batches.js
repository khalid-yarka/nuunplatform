/* ============================================================
   static/js/admin/batches.js
   Batch list actions + batch detail editor.

   Bulk edits are STAGED. Single-item edits save immediately.
   Permanent delete is destructive, confirm-gated, and offers an
   optional pre-purge database backup (default: on).

   The confirm input uses letter-by-letter progress. The button
   is enabled only when (a) the preview loaded, (b) the input
   equals DELETE, and (c) no request is in flight.
   ============================================================ */

(function () {
    'use strict';

    var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    function toast(msg, kind) {
        if (typeof window.showToast === 'function') {
            window.showToast(msg, kind || 'info');
            return;
        }
        var el = document.createElement('div');
        el.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1A1A2E;color:#fff;padding:10px 18px;border-radius:10px;font-size:13px;font-weight:600;z-index:9999;box-shadow:0 8px 24px rgba(0,0,0,0.24);';
        el.textContent = msg;
        document.body.appendChild(el);
        setTimeout(function () { el.remove(); }, 3200);
    }

    // ============================================================
    // LIST PAGE
    // ============================================================
    function initListPage() {
        var shell = document.querySelector('.batch-shell');
        if (shell) return;

        document.addEventListener('click', function (e) {
            var pinBtn = e.target.closest('[data-batch-pin]');
            if (pinBtn) {
                e.preventDefault();
                var id = pinBtn.getAttribute('data-batch-pin');
                fetch('/admin/batches/' + id + '/pin', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': CSRF, 'X-Requested-With': 'XMLHttpRequest' }
                })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (d && d.success) window.location.reload();
                    else toast((d && d.error) || 'Pin failed', 'error');
                })
                .catch(function () { toast('Network error', 'error'); });
                return;
            }

            var delBtn = e.target.closest('[data-batch-delete]');
            if (delBtn) {
                e.preventDefault();
                var did = delBtn.getAttribute('data-batch-delete');
                var name = delBtn.getAttribute('data-batch-name') || 'this batch';
                if (!confirm('Delete batch "' + name + '"?\n\nThe items are not deleted — only the batch that groups them.')) return;
                fetch('/admin/batches/' + did + '/delete', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': CSRF, 'X-Requested-With': 'XMLHttpRequest' }
                })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (d && d.success) {
                        toast('Batch deleted', 'success');
                        setTimeout(function () { window.location.reload(); }, 400);
                    } else {
                        toast((d && d.error) || 'Delete failed', 'error');
                    }
                })
                .catch(function () { toast('Network error', 'error'); });
            }
        });
    }

    // ============================================================
    // DETAIL PAGE
    // ============================================================
    function initDetailPage() {
        var shell = document.querySelector('.batch-shell');
        if (!shell) return;

        var BATCH_ID = parseInt(shell.getAttribute('data-batch-id'), 10);

        var SUBJECTS = [];
        try {
            var subjEl = document.getElementById('batchSubjectsData');
            if (subjEl) SUBJECTS = JSON.parse(subjEl.textContent) || [];
        } catch (e) { SUBJECTS = []; }

        var state = {
            type: 'question',
            items: [],
            selected: new Set(),
            totalInBatch: { question: 0, pdf: 0 },
            pendingChanges: { question: [], pdf: [] },
            undoTimeout: null,
        };

        state.totalInBatch.question = parseInt(
            document.getElementById('tabCountQuestion').textContent.trim(), 10) || 0;
        state.totalInBatch.pdf = parseInt(
            document.getElementById('tabCountPdf').textContent.trim(), 10) || 0;

        var el = {
            tabs: document.querySelectorAll('.batch-tab'),
            tableBody: document.getElementById('batchTableBody'),
            masterCheck: document.getElementById('batchMasterCheck'),
            bulkBar: document.getElementById('batchBulkBar'),
            bbSelected: document.getElementById('bbSelectedCount'),
            bbPending: document.getElementById('bbPending'),
            bbPendingCount: document.getElementById('bbPendingCount'),
            bbPendingLabel: document.getElementById('bbPendingLabel'),
            bbActions: document.getElementById('bbActions'),
            bbSelectAllVisible: document.getElementById('bbSelectAllVisible'),
            bbSelectAllTotal: document.getElementById('bbSelectAllTotal'),
            bbClear: document.getElementById('bbClearSelection'),
            bbSaveCluster: document.getElementById('bbSaveCluster'),
            bbSave: document.getElementById('bbSave'),
            bbDiscard: document.getElementById('bbDiscard'),
            search: document.getElementById('batchSearch'),
            grade: document.getElementById('batchGradeFilter'),
            subject: document.getElementById('batchSubjectFilter'),
            status: document.getElementById('batchStatusFilter'),
            clearFilters: document.getElementById('batchClearFilters'),

            drawer: document.getElementById('batchDrawer'),
            drawerBackdrop: document.getElementById('batchDrawerBackdrop'),
            drawerTitle: document.getElementById('batchDrawerTitle'),
            drawerSub: document.getElementById('batchDrawerSub'),
            drawerBody: document.getElementById('batchDrawerBody'),
            bdClose: document.getElementById('bdClose'),
            bdCancel: document.getElementById('bdCancel'),
            bdSave: document.getElementById('bdSave'),

            undoToast: document.getElementById('batchUndoToast'),
            undoText: document.getElementById('batchUndoText'),
            undoBtn: document.getElementById('batchUndoBtn'),

            renameModal: document.getElementById('renameModal'),
            renameInput: document.getElementById('renameInput'),
            renameBtn: document.getElementById('batchRenameBtn'),
            renameConfirm: document.getElementById('renameConfirmBtn'),

            deleteModal: document.getElementById('deleteModal'),
            deleteBtn: document.getElementById('batchDeleteBtn'),
            deleteConfirm: document.getElementById('deleteConfirmBtn'),

            purgeModal: document.getElementById('purgeModal'),
            purgeStats: document.getElementById('purgeStats'),
            purgeConfirmInput: document.getElementById('purgeConfirmInput'),
            purgeConfirmBtn: document.getElementById('purgeConfirmBtn'),
            purgeBackupCheck: document.getElementById('purgeBackupCheck'),
            purgeBackupHint: document.getElementById('purgeBackupHint'),
            purgeProgress: document.getElementById('purgeProgress'),
            purgeConfirmStatus: document.getElementById('purgeConfirmStatus'),

            pinBtn: document.getElementById('batchPinBtn'),
            pinLabel: document.getElementById('pinLabel'),
        };

        // ─── Pending-changes helpers ───
        function getPending() { return state.pendingChanges[state.type]; }
        function pendingCount() { return getPending().length; }
        function hasAnyPending() {
            return state.pendingChanges.question.length > 0
                || state.pendingChanges.pdf.length > 0;
        }
        function pendingForField(action) {
            var list = getPending();
            for (var i = 0; i < list.length; i++) {
                if (list[i].action === action) return list[i];
            }
            return null;
        }

        function stageChange(action, value) {
            var ids = Array.from(state.selected);
            if (!ids.length) {
                toast('Select items first.', 'warning');
                return false;
            }
            var list = getPending();

            if (action === 'add_tag' || action === 'remove_tag') {
                var tagVal = (value || '').trim();
                if (!tagVal) return false;
                for (var i = 0; i < list.length; i++) {
                    if (list[i].action === action && list[i].value === tagVal) {
                        toast('Already staged: ' + action + ' ' + tagVal, 'info');
                        return true;
                    }
                }
                list.push({ action: action, value: tagVal, item_ids: ids });
            } else if (action === 'clear_tags') {
                state.pendingChanges[state.type] = list.filter(function (p) {
                    return p.action !== 'add_tag' && p.action !== 'remove_tag';
                });
                getPending().push({ action: action, value: null, item_ids: ids });
            } else {
                state.pendingChanges[state.type] = list.filter(function (p) {
                    return p.action !== action;
                });
                getPending().push({ action: action, value: value, item_ids: ids });
            }

            renderTable();
            updateBulkBar();
            return true;
        }

        function clearPending(type) {
            type = type || state.type;
            state.pendingChanges[type] = [];
            if (type === state.type) {
                renderTable();
                updateBulkBar();
            }
        }

        function buildQuery() {
            var p = new URLSearchParams();
            p.set('type', state.type);
            if (el.search.value.trim()) p.set('search', el.search.value.trim());
            if (state.type === 'question') {
                if (el.grade.value) p.set('grade', el.grade.value);
                if (el.subject.value) p.set('subject', el.subject.value);
                if (el.status.value) p.set('status', el.status.value);
            }
            return p.toString();
        }

        var loadTimer = null;
        function reloadItems() {
            clearTimeout(loadTimer);
            loadTimer = setTimeout(function () {
                el.tableBody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:32px;color:var(--text-muted);"><i class="fas fa-spinner fa-spin"></i> Loading…</td></tr>';
                fetch('/admin/batches/' + BATCH_ID + '/items?' + buildQuery(), {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    state.items = (d && d.items) || [];
                    renderTable();
                })
                .catch(function () {
                    el.tableBody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:32px;color:#DC2626;">Could not load items.</td></tr>';
                });
            }, 200);
        }

        function escapeHtml(s) {
            if (s == null) return '';
            return String(s)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;')
                .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function rowHasPending(itemId) {
            var list = getPending();
            for (var i = 0; i < list.length; i++) {
                if (list[i].item_ids.indexOf(itemId) !== -1) return true;
            }
            return false;
        }

        function renderPendingChips(itemId) {
            var list = getPending();
            var chips = [];
            for (var i = 0; i < list.length; i++) {
                var p = list[i];
                if (p.item_ids.indexOf(itemId) === -1) continue;
                var label = pendingChipLabel(p);
                if (label) chips.push('<span class="batch-pending-chip">' + label + '</span>');
            }
            return chips.join('');
        }

        function pendingChipLabel(p) {
            var v = p.value;
            switch (p.action) {
                case 'set_grade':      return '🎓 → ' + escapeHtml(String(v || ''));
                case 'set_subject':    return '📚 → ' + escapeHtml(String(v || ''));
                case 'set_chapter':    return '📖 → ' + escapeHtml(String(v || ''));
                case 'set_difficulty': return '⭐ → ' + '⭐'.repeat(parseInt(v, 10) || 1);
                case 'set_status':     return '🔖 → ' + escapeHtml(String(v || ''));
                case 'set_pdf_code':   return '📄 → ' + escapeHtml(String(v || ''));
                case 'set_pdf_page':   return '📄 p.' + escapeHtml(String(v || ''));
                case 'set_curriculum': return '🌍 → ' + escapeHtml(String(v || ''));
                case 'set_class':      return '🎓 → ' + escapeHtml(String(v || ''));
                case 'toggle_premium': return (v ? '💎 Premium On' : '💎 Premium Off');
                case 'add_tag':        return '+ ' + escapeHtml(String(v || ''));
                case 'remove_tag':     return '− ' + escapeHtml(String(v || ''));
                case 'clear_tags':     return '× all tags';
            }
            return '';
        }

        function renderTable() {
            if (!state.items.length) {
                el.tableBody.innerHTML =
                    '<tr><td colspan="4">' +
                    '  <div class="batch-empty">' +
                    '    <i class="fas fa-inbox"></i>' +
                    '    <h4>No items' + (state.type === 'question' ? ' matching those filters' : '') + '</h4>' +
                    '    <p>Add items from the ' + (state.type === 'question' ? 'questions' : 'PDF') + ' list to include them here.</p>' +
                    '  </div>' +
                    '</td></tr>';
                updateBulkBar();
                return;
            }

            var html = '';
            state.items.forEach(function (it) {
                var checked = state.selected.has(it.id) ? 'checked' : '';
                var rowHasPendingChange = rowHasPending(it.id);
                var rowCls = '';
                if (state.selected.has(it.id)) rowCls += ' selected';
                if (rowHasPendingChange) rowCls += ' batch-row-pending';

                var pendingChips = renderPendingChips(it.id);

                if (state.type === 'question') {
                    var meta = ''
                        + '<span>' + escapeHtml(it.subject_icon + ' ' + (it.subject_name || it.subject_code)) + '</span>'
                        + '<span>🎓 ' + escapeHtml(it.grade || 'F4') + '</span>'
                        + (it.chapter ? '<span>📖 ' + escapeHtml(it.chapter) + '</span>' : '')
                        + '<span>' + ('⭐'.repeat(it.difficulty || 1)) + '</span>'
                        + '<span>' + escapeHtml(it.status || 'active') + '</span>'
                        + pendingChips;
                    html += ''
                        + '<tr class="' + rowCls.trim() + '" data-id="' + it.id + '">'
                        +   '<td style="text-align:center;">'
                        +     '<input type="checkbox" class="batch-checkbox" ' + checked + '>'
                        +   '</td>'
                        +   '<td>'
                        +     '<div class="batch-row-title">' + escapeHtml(it.question_text) + '</div>'
                        +     '<div class="batch-row-meta">' + meta + '</div>'
                        +   '</td>'
                        +   '<td class="hide-mobile" style="font-size:11px;color:var(--text-muted);font-family:ui-monospace,monospace;">'
                        +     '#' + it.id
                        +     (it.pdf_code ? '<br>' + escapeHtml(it.pdf_code) : '')
                        +   '</td>'
                        +   '<td style="text-align:right;">'
                        +     '<button type="button" class="batch-row-edit" data-edit-item="' + it.id + '" title="Edit">'
                        +       '<i class="fas fa-pen"></i>'
                        +     '</button>'
                        +   '</td>'
                        + '</tr>';
                } else {
                    var meta2 = ''
                        + '<span>🎓 ' + escapeHtml(it.class || '—') + '</span>'
                        + (it.curriculum ? '<span>' + escapeHtml(it.curriculum) + '</span>' : '')
                        + (it.subject ? '<span>' + escapeHtml(it.subject) + '</span>' : '')
                        + (it.is_premium ? '<span>💎 Premium</span>' : '')
                        + (it.chapter ? '<span>📖 ' + escapeHtml(it.chapter) + '</span>' : '')
                        + pendingChips;
                    html += ''
                        + '<tr class="' + rowCls.trim() + '" data-id="' + it.id + '">'
                        +   '<td style="text-align:center;">'
                        +     '<input type="checkbox" class="batch-checkbox" ' + checked + '>'
                        +   '</td>'
                        +   '<td>'
                        +     '<div class="batch-row-title">' + escapeHtml(it.title) + '</div>'
                        +     '<div class="batch-row-meta">' + meta2 + '</div>'
                        +   '</td>'
                        +   '<td class="hide-mobile" style="font-size:11px;color:var(--text-muted);font-family:ui-monospace,monospace;">'
                        +     escapeHtml(it.code || '') + '<br>#' + it.id
                        +   '</td>'
                        +   '<td style="text-align:right;">'
                        +     '<button type="button" class="batch-row-edit" data-edit-item="' + it.id + '" title="Edit">'
                        +       '<i class="fas fa-pen"></i>'
                        +     '</button>'
                        +   '</td>'
                        + '</tr>';
                }
            });
            el.tableBody.innerHTML = html;
            updateMasterCheck();
            updateBulkBar();
        }

        function updateMasterCheck() {
            if (!el.masterCheck) return;
            if (!state.items.length) {
                el.masterCheck.checked = false;
                el.masterCheck.indeterminate = false;
                return;
            }
            var visibleIds = state.items.map(function (x) { return x.id; });
            var selectedVisible = visibleIds.filter(function (id) { return state.selected.has(id); });
            if (selectedVisible.length === 0) {
                el.masterCheck.checked = false;
                el.masterCheck.indeterminate = false;
            } else if (selectedVisible.length === visibleIds.length) {
                el.masterCheck.checked = true;
                el.masterCheck.indeterminate = false;
            } else {
                el.masterCheck.checked = false;
                el.masterCheck.indeterminate = true;
            }
        }

        function updateBulkBar() {
            var n = state.selected.size;
            var pcount = pendingCount();
            var anyPending = hasAnyPending();

            if (n === 0 && pcount === 0) { el.bulkBar.hidden = true; return; }
            el.bulkBar.hidden = false;
            el.bbSelected.textContent = n;

            if (pcount > 0) {
                el.bbPending.hidden = false;
                el.bbPendingCount.textContent = pcount;
                el.bbPendingLabel.textContent = pcount === 1 ? 'change staged' : 'changes staged';
            } else {
                el.bbPending.hidden = true;
            }

            el.bbSaveCluster.hidden = !anyPending;

            var visibleIds = state.items.map(function (x) { return x.id; });
            var visibleSelected = visibleIds.filter(function (id) { return state.selected.has(id); }).length;
            var totalBatch = state.totalInBatch[state.type === 'question' ? 'question' : 'pdf'];
            el.bbSelectAllTotal.hidden = !(visibleSelected === visibleIds.length
                                          && visibleSelected > 0
                                          && totalBatch > visibleIds.length);

            el.bbActions.style.display = (n === 0) ? 'none' : '';
            renderBulkActions();
        }

        function renderBulkActions() {
            var html = '';
            if (state.type === 'question') {
                html += bulkAction('grade', '🎓', 'Grade');
                html += bulkAction('subject', '📚', 'Subject');
                html += bulkAction('chapter', '📖', 'Chapter');
                html += bulkAction('difficulty', '⭐', 'Difficulty');
                html += bulkAction('status', '🔖', 'Status');
                html += bulkAction('tags', '🏷️', 'Tags');
                html += bulkAction('pdf', '📄', 'PDF');
            } else {
                html += bulkAction('curriculum', '🌍', 'Curriculum');
                html += bulkAction('class', '🎓', 'Class');
                html += bulkAction('subject', '📚', 'Subject');
                html += bulkAction('chapter', '📖', 'Chapter');
                html += bulkAction('tags', '🏷️', 'Tags');
                html += bulkAction('premium', '💎', 'Premium');
            }
            html += '<button type="button" class="bb-action bb-action--danger" data-bulk="remove">'
                  + '<i class="fas fa-unlink"></i> Remove from batch</button>';

            html += '<button type="button" class="bb-action bb-action--purge" data-bulk="purge">'
                  + '<i class="fas fa-skull-crossbones"></i> Permanently delete</button>';

            el.bbActions.innerHTML = html;

            var stagedActions = {};
            getPending().forEach(function (p) { stagedActions[p.action] = true; });

            el.bbActions.querySelectorAll('.bb-action').forEach(function (btn) {
                var key = btn.getAttribute('data-bulk');
                if (!key) return;
                var mapped = mapBulkKeyToAction(key);
                if (mapped && stagedActions[mapped]) btn.classList.add('has-pending');
                if (key === 'tags' && (stagedActions['add_tag']
                                      || stagedActions['remove_tag']
                                      || stagedActions['clear_tags'])) {
                    btn.classList.add('has-pending');
                }
            });
        }

        function mapBulkKeyToAction(key) {
            switch (key) {
                case 'grade':      return 'set_grade';
                case 'subject':    return 'set_subject';
                case 'chapter':    return 'set_chapter';
                case 'difficulty': return 'set_difficulty';
                case 'status':     return 'set_status';
                case 'pdf':        return 'set_pdf_code';
                case 'curriculum': return 'set_curriculum';
                case 'class':      return 'set_class';
                case 'premium':    return 'toggle_premium';
            }
            return null;
        }

        function bulkAction(key, icon, label) {
            return '<button type="button" class="bb-action" data-bulk="' + key + '">'
                 + '<span>' + icon + '</span> ' + label
                 + '</button>';
        }

        el.tableBody.addEventListener('change', function (e) {
            var cb = e.target.closest('.batch-checkbox');
            if (!cb) return;
            var row = cb.closest('tr');
            var id = parseInt(row.getAttribute('data-id'), 10);
            if (cb.checked) state.selected.add(id); else state.selected.delete(id);
            renderTable();
        });

        el.masterCheck.addEventListener('change', function () {
            var on = el.masterCheck.checked;
            state.items.forEach(function (it) {
                if (on) state.selected.add(it.id); else state.selected.delete(it.id);
            });
            renderTable();
        });

        el.bbClear.addEventListener('click', function () {
            state.selected.clear();
            renderTable();
        });

        el.bbSelectAllVisible.addEventListener('click', function () {
            state.items.forEach(function (it) { state.selected.add(it.id); });
            renderTable();
        });

        el.bbSelectAllTotal.addEventListener('click', function () {
            fetch('/admin/batches/' + BATCH_ID + '/items?type=' + state.type, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                (d.items || []).forEach(function (it) { state.selected.add(it.id); });
                renderTable();
            });
        });

        el.bbDiscard.addEventListener('click', function () {
            if (!hasAnyPending()) return;
            if (!confirm('Discard all staged changes?')) return;
            clearPending(state.type);
            toast('Staged changes discarded', 'info');
        });

        el.bbSave.addEventListener('click', function () {
            var pending = getPending();
            if (!pending.length) { toast('Nothing to save.', 'info'); return; }

            var totalIds = {};
            pending.forEach(function (p) {
                p.item_ids.forEach(function (id) { totalIds[id] = true; });
            });
            var affectedCount = Object.keys(totalIds).length;
            if (affectedCount > 5) {
                if (!confirm('Apply ' + pending.length + ' change'
                           + (pending.length === 1 ? '' : 's') + ' to '
                           + affectedCount + ' item' + (affectedCount === 1 ? '' : 's')
                           + '?')) return;
            }

            var changes = pending.map(function (p) {
                return { action: p.action, value: p.value, item_ids: p.item_ids.slice() };
            });

            el.bbSave.disabled = true;
            var origHTML = el.bbSave.innerHTML;
            el.bbSave.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving…';

            fetch('/admin/batches/' + BATCH_ID + '/bulk-edit-batch', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': CSRF,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({ item_type: state.type, changes: changes })
            })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                el.bbSave.disabled = false;
                el.bbSave.innerHTML = origHTML;
                if (!d || !d.success) {
                    toast((d && d.error) || 'Save failed', 'error');
                    return;
                }
                toast(d.affected + ' items updated', 'success');
                clearPending(state.type);
                state.selected.clear();
                showUndoToast(d.affected, d.undo_id, d.undo_seconds);
                reloadItems();
            })
            .catch(function () {
                el.bbSave.disabled = false;
                el.bbSave.innerHTML = origHTML;
                toast('Network error', 'error');
            });
        });

        ['search', 'grade', 'subject', 'status'].forEach(function (k) {
            var inp = el[k];
            if (!inp) return;
            if (inp.tagName === 'SELECT') {
                inp.addEventListener('change', function () {
                    updateClearFilters();
                    reloadItems();
                });
            } else {
                var t = null;
                inp.addEventListener('input', function () {
                    clearTimeout(t);
                    t = setTimeout(function () {
                        updateClearFilters();
                        reloadItems();
                    }, 260);
                });
            }
        });

        function updateClearFilters() {
            var has = (el.search.value.trim() !== '')
                   || (el.grade && el.grade.value)
                   || (el.subject && el.subject.value)
                   || (el.status && el.status.value);
            el.clearFilters.hidden = !has;
        }

        el.clearFilters.addEventListener('click', function () {
            el.search.value = '';
            if (el.grade) el.grade.value = '';
            if (el.subject) el.subject.value = '';
            if (el.status) el.status.value = '';
            updateClearFilters();
            reloadItems();
        });

        el.tabs.forEach(function (tab) {
            tab.addEventListener('click', function () {
                var type = tab.getAttribute('data-type');
                if (type === state.type) return;
                state.type = type;
                state.selected.clear();

                el.tabs.forEach(function (t) {
                    t.classList.toggle('active', t === tab);
                    t.setAttribute('aria-selected', t === tab ? 'true' : 'false');
                });

                [el.grade, el.subject, el.status].forEach(function (sel) {
                    if (!sel) return;
                    var scope = sel.getAttribute('data-scope');
                    sel.style.display = (scope === 'question' && type !== 'question') ? 'none' : '';
                });

                updateClearFilters();
                reloadItems();
            });
        });

        el.tableBody.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-edit-item]');
            if (!btn) return;
            openDrawer(parseInt(btn.getAttribute('data-edit-item'), 10));
        });

        function openDrawer(itemId) {
            el.drawerBackdrop.hidden = false;
            el.drawer.hidden = false;
            el.drawerBody.innerHTML =
                '<div style="text-align:center;padding:40px;color:var(--text-muted);">'
              + '<i class="fas fa-spinner fa-spin"></i> Loading…</div>';

            fetch('/admin/batches/' + BATCH_ID + '/item/' + state.type + '/' + itemId, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!d.item) {
                    el.drawerBody.innerHTML = '<p style="color:#DC2626;">Item not found.</p>';
                    return;
                }
                renderDrawer(d.item, itemId);
            })
            .catch(function () {
                el.drawerBody.innerHTML = '<p style="color:#DC2626;">Could not load item.</p>';
            });
        }

        function subjectOptionsHtml(selectedCode) {
            var html = '<option value="">— Pick a subject —</option>';
            SUBJECTS.forEach(function (s) {
                var code = s.code || '';
                var icon = s.icon || '📚';
                var name = s.name || code;
                html += '<option value="' + escapeHtml(code) + '"'
                     +  (code === selectedCode ? ' selected' : '') + '>'
                     +  icon + ' ' + escapeHtml(name)
                     +  '</option>';
            });
            return html;
        }

        function renderDrawer(item, itemId) {
            el.drawerTitle.textContent = state.type === 'question' ? 'Edit question' : 'Edit PDF';
            el.drawerSub.textContent = '#' + itemId;
            el.drawer.dataset.itemId = itemId;

            var html = '';
            if (state.type === 'question') {
                var options = item.options || {};
                var correct = item.correct_answer || 'A';
                html += ''
                    + '<div class="admin-form-group"><label>Subject</label><select data-field="subject_code">' + subjectOptionsHtml(item.subject_code || '') + '</select></div>'
                    + '<div class="admin-form-group"><label>Grade</label><select data-field="grade">' + ['F4','F3','G8','G7'].map(function(g){ return '<option value="' + g + '"' + (item.grade === g ? ' selected' : '') + '>' + g + '</option>'; }).join('') + '</select></div>'
                    + '<div class="admin-form-group"><label>Question</label><textarea data-field="question_text" rows="4">' + escapeHtml(item.question_text || '') + '</textarea></div>'
                    + '<div class="admin-form-group"><label>Option A</label><input type="text" data-opt="A" value="' + escapeHtml(options.A || '') + '"></div>'
                    + '<div class="admin-form-group"><label>Option B</label><input type="text" data-opt="B" value="' + escapeHtml(options.B || '') + '"></div>'
                    + '<div class="admin-form-group"><label>Option C</label><input type="text" data-opt="C" value="' + escapeHtml(options.C || '') + '"></div>'
                    + '<div class="admin-form-group"><label>Option D (optional)</label><input type="text" data-opt="D" value="' + escapeHtml(options.D || '') + '"></div>'
                    + '<div class="admin-form-group"><label>Option E (optional)</label><input type="text" data-opt="E" value="' + escapeHtml(options.E || '') + '"></div>'
                    + '<div class="admin-form-group"><label>Option F (optional)</label><input type="text" data-opt="F" value="' + escapeHtml(options.F || '') + '"></div>'
                    + '<div class="admin-form-group"><label>Correct answer</label><select data-field="correct_answer">' + ['A','B','C','D','E','F'].map(function(k){ return '<option value="' + k + '"' + (correct === k ? ' selected' : '') + '>' + k + '</option>'; }).join('') + '</select></div>'
                    + '<div class="admin-form-group"><label>Difficulty</label><select data-field="difficulty">' + [1,2,3,4,5].map(function(n){ return '<option value="' + n + '"' + (item.difficulty === n ? ' selected' : '') + '>' + '⭐'.repeat(n) + '</option>'; }).join('') + '</select></div>'
                    + '<div class="admin-form-group"><label>Chapter</label><input type="text" data-field="chapter" value="' + escapeHtml(item.chapter || '') + '"></div>'
                    + '<div class="admin-form-group"><label>Tags (comma-separated)</label><input type="text" data-field="tags" value="' + escapeHtml(item.tags || '') + '"></div>'
                    + '<div class="admin-form-group"><label>Explanation</label><textarea data-field="explanation" rows="3">' + escapeHtml(item.explanation || '') + '</textarea></div>'
                    + '<div class="admin-form-group"><label>PDF code (optional)</label><input type="text" data-field="pdf_code" value="' + escapeHtml(item.pdf_code || '') + '"></div>'
                    + '<div class="admin-form-group"><label>PDF page (optional)</label><input type="number" data-field="pdf_page" value="' + (item.pdf_page || '') + '" min="1"></div>'
                    + '<div class="admin-form-group"><label>Status</label><select data-field="status">' + ['active','archived','draft'].map(function(s){ return '<option value="' + s + '"' + (item.status === s ? ' selected' : '') + '>' + s + '</option>'; }).join('') + '</select></div>';
            } else {
                html += ''
                    + '<div class="admin-form-group"><label>Title</label><input type="text" data-field="title" value="' + escapeHtml(item.title || '') + '"></div>'
                    + '<div class="admin-form-group"><label>Subject</label><select data-field="subject">' + subjectOptionsHtml(item.subject || '') + '</select></div>'
                    + '<div class="admin-form-group"><label>Class</label><select data-field="class">' + ['F4','F3','G8','G7'].map(function(c){ return '<option value="' + c + '"' + (item.class === c ? ' selected' : '') + '>' + c + '</option>'; }).join('') + '</select></div>'
                    + '<div class="admin-form-group"><label>Curriculum</label><select data-field="curriculum">' + ['PL','SO','SL'].map(function(c){ return '<option value="' + c + '"' + (item.curriculum === c ? ' selected' : '') + '>' + c + '</option>'; }).join('') + '</select></div>'
                    + '<div class="admin-form-group"><label>Chapter</label><input type="text" data-field="chapter" value="' + escapeHtml(item.chapter || '') + '"></div>'
                    + '<div class="admin-form-group"><label>Tags (comma-separated)</label><input type="text" data-field="tags" value="' + escapeHtml(item.tags || '') + '"></div>'
                    + '<div class="admin-form-group"><label>Premium</label><select data-field="is_premium">'
                    +   '<option value="0"' + (!item.is_premium ? ' selected' : '') + '>No</option>'
                    +   '<option value="1"' + (item.is_premium ? ' selected' : '') + '>Yes</option>'
                    + '</select></div>'
                    + '<div class="admin-form-group"><label>Description</label><textarea data-field="description" rows="3">' + escapeHtml(item.description || '') + '</textarea></div>';
            }
            el.drawerBody.innerHTML = html;
        }

        function closeDrawer() {
            el.drawer.hidden = true;
            el.drawerBackdrop.hidden = true;
        }

        el.bdClose.addEventListener('click', closeDrawer);
        el.bdCancel.addEventListener('click', closeDrawer);
        el.drawerBackdrop.addEventListener('click', closeDrawer);

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !el.drawer.hidden) closeDrawer();
        });

        el.bdSave.addEventListener('click', function () {
            var itemId = parseInt(el.drawer.dataset.itemId, 10);
            if (!itemId) return;
            var payload = {};
            el.drawerBody.querySelectorAll('[data-field]').forEach(function (inp) {
                payload[inp.getAttribute('data-field')] = inp.value;
            });
            var opts = {};
            el.drawerBody.querySelectorAll('[data-opt]').forEach(function (inp) {
                opts[inp.getAttribute('data-opt')] = inp.value;
            });
            if (state.type === 'question') payload.options = opts;

            el.bdSave.disabled = true;
            el.bdSave.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving…';

            fetch('/admin/batches/' + BATCH_ID + '/item/' + state.type + '/' + itemId, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': CSRF,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify(payload)
            })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                el.bdSave.disabled = false;
                el.bdSave.innerHTML = '<i class="fas fa-save"></i> Save';
                if (d && d.success) {
                    toast('Item saved', 'success');
                    closeDrawer();
                    reloadItems();
                } else {
                    toast((d && d.error) || 'Save failed', 'error');
                }
            })
            .catch(function () {
                el.bdSave.disabled = false;
                el.bdSave.innerHTML = '<i class="fas fa-save"></i> Save';
                toast('Network error', 'error');
            });
        });

        var activePopover = null;
        function closePopover() {
            if (activePopover) { activePopover.remove(); activePopover = null; }
        }
        document.addEventListener('click', function (e) {
            if (activePopover && !activePopover.contains(e.target)
                && !e.target.closest('[data-bulk]')) {
                closePopover();
            }
        });

        el.bbActions.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-bulk]');
            if (!btn) return;
            var key = btn.getAttribute('data-bulk');
            if (key === 'remove') { doRemoveSelected(); return; }
            if (key === 'purge') { openPurgeModal(); return; }
            openPopover(btn, key);
        });

        function openPopover(anchorBtn, key) {
            closePopover();
            var rect = anchorBtn.getBoundingClientRect();
            var pop = document.createElement('div');
            pop.className = 'bb-popover';
            pop.style.position = 'fixed';
            pop.style.top = (rect.bottom + 8) + 'px';
            pop.style.left = Math.min(rect.left, window.innerWidth - 280) + 'px';

            var html = '';
            html += '<div class="bb-popover__title">' + key + '</div>';
            var actionKey = mapBulkKeyToAction(key);
            var staged = actionKey ? pendingForField(actionKey) : null;
            var stagedValue = staged ? staged.value : null;

            if (key === 'grade') {
                html += '<div class="bb-popover__row">'
                     + ['F4','F3','G8','G7'].map(function(g){
                           return '<button type="button" class="bb-grade-opt'
                             + (stagedValue === g ? ' active' : '')
                             + '" data-val="' + g + '">' + g + '</button>';
                       }).join('') + '</div>'
                     + '<button type="button" class="bb-popover__apply" disabled>Stage change</button>';
            } else if (key === 'subject') {
                html += '<div class="bb-popover__row">'
                     + '<select data-input>'
                     +   '<option value="">— Pick a subject —</option>'
                     +   SUBJECTS.map(function(s){
                             var code = s.code || ''; var icon = s.icon || '📚'; var name = s.name || code;
                             return '<option value="' + escapeHtml(code) + '"'
                                  + (stagedValue === code ? ' selected' : '') + '>'
                                  + icon + ' ' + escapeHtml(name) + '</option>';
                         }).join('')
                     + '</select></div>'
                     + '<button type="button" class="bb-popover__apply">Stage change</button>';
            } else if (key === 'chapter') {
                html += '<div class="bb-popover__row">'
                     + '<input type="text" data-input placeholder="e.g. Chapter 3" value="'
                     + escapeHtml(stagedValue || '') + '"></div>'
                     + '<button type="button" class="bb-popover__apply">Stage change</button>';
            } else if (key === 'difficulty') {
                html += '<div class="bb-popover__row">'
                     + '<select data-input>'
                     + [1,2,3,4,5].map(function(n){
                           return '<option value="' + n + '"'
                             + (String(stagedValue) === String(n) ? ' selected' : '') + '>'
                             + '⭐'.repeat(n) + '</option>';
                       }).join('') + '</select></div>'
                     + '<button type="button" class="bb-popover__apply">Stage change</button>';
            } else if (key === 'status') {
                html += '<div class="bb-popover__row">'
                     + '<select data-input>'
                     + ['active','archived','draft'].map(function(s){
                           return '<option value="' + s + '"'
                             + (stagedValue === s ? ' selected' : '') + '>'
                             + s + '</option>';
                       }).join('') + '</select></div>'
                     + '<button type="button" class="bb-popover__apply">Stage change</button>';
            } else if (key === 'curriculum') {
                html += '<div class="bb-popover__row">'
                     + '<select data-input>'
                     + ['PL','SO','SL'].map(function(c){
                           return '<option value="' + c + '"'
                             + (stagedValue === c ? ' selected' : '') + '>'
                             + c + '</option>';
                       }).join('') + '</select></div>'
                     + '<button type="button" class="bb-popover__apply">Stage change</button>';
            } else if (key === 'class') {
                html += '<div class="bb-popover__row">'
                     + ['F4','F3','G8','G7'].map(function(c){
                           return '<button type="button" class="bb-grade-opt'
                             + (stagedValue === c ? ' active' : '')
                             + '" data-val="' + c + '">' + c + '</button>';
                       }).join('') + '</div>'
                     + '<button type="button" class="bb-popover__apply" disabled>Stage change</button>';
            } else if (key === 'tags') {
                html += '<div class="bb-popover__row">'
                     + '<input type="text" data-input placeholder="tag name"></div>'
                     + '<div class="bb-popover__row">'
                     +   '<button type="button" class="bb-popover__apply" data-tag-mode="add" style="flex:1;">+ Stage add</button>'
                     +   '<button type="button" class="bb-popover__apply" data-tag-mode="remove" style="flex:1;background:#DC2626;">− Stage remove</button>'
                     + '</div>'
                     + '<div class="bb-popover__row" style="margin-top:6px;">'
                     +   '<button type="button" class="bb-popover__apply" data-tag-mode="clear" style="background:transparent;color:var(--text-secondary);border:1px solid var(--border);">Clear all tags</button>'
                     + '</div>';
            } else if (key === 'pdf') {
                html += '<div class="bb-popover__row">'
                     + '<input type="text" data-input placeholder="XXXX-XXXX" value="'
                     + escapeHtml(stagedValue || '') + '"></div>'
                     + '<button type="button" class="bb-popover__apply">Stage change</button>';
            } else if (key === 'premium') {
                html += '<div class="bb-popover__row">'
                     + '<button type="button" class="bb-grade-opt'
                     + (stagedValue === true || stagedValue === 1 ? ' active' : '')
                     + '" data-val="1" style="flex:1;">💎 On</button>'
                     + '<button type="button" class="bb-grade-opt'
                     + (stagedValue === false || stagedValue === 0 ? ' active' : '')
                     + '" data-val="0" style="flex:1;">Off</button>'
                     + '</div>'
                     + '<button type="button" class="bb-popover__apply" disabled>Stage change</button>';
            }

            pop.innerHTML = html;
            document.body.appendChild(pop);
            activePopover = pop;

            var applyBtn = pop.querySelector('.bb-popover__apply:not([data-tag-mode])');
            var chosenValue = null;

            pop.querySelectorAll('.bb-grade-opt').forEach(function (opt) {
                opt.addEventListener('click', function () {
                    pop.querySelectorAll('.bb-grade-opt').forEach(function (o) {
                        o.classList.remove('active');
                    });
                    opt.classList.add('active');
                    chosenValue = opt.getAttribute('data-val');
                    if (applyBtn) applyBtn.disabled = false;
                });
            });

            function fire(val, extraKey) {
                var realAction = extraKey || actionKey;
                if (!realAction) return;
                closePopover();
                if (stageChange(realAction, val)) {
                    toast('Staged: ' + (extraKey || key), 'info');
                }
            }

            if (applyBtn) {
                applyBtn.addEventListener('click', function () {
                    var v;
                    if (chosenValue !== null && (key === 'grade' || key === 'class')) {
                        v = chosenValue;
                    } else if (key === 'premium') {
                        v = chosenValue === '1' ? 1 : 0;
                    } else {
                        var input = pop.querySelector('[data-input]');
                        v = input ? input.value : '';
                    }
                    if (key === 'subject' && !v) return;
                    if (key === 'difficulty' && !v) return;
                    fire(v);
                });
            }

            pop.querySelectorAll('[data-tag-mode]').forEach(function (b) {
                b.addEventListener('click', function () {
                    var mode = b.getAttribute('data-tag-mode');
                    if (mode === 'clear') {
                        if (stageChange('clear_tags', null)) toast('Staged: clear all tags', 'info');
                        closePopover(); return;
                    }
                    var input = pop.querySelector('[data-input]');
                    var tag = input ? input.value.trim() : '';
                    if (!tag) { toast('Enter a tag name.', 'warning'); return; }
                    if (stageChange(mode === 'add' ? 'add_tag' : 'remove_tag', tag)) {
                        toast('Staged: ' + (mode === 'add' ? '+ ' : '− ') + tag, 'info');
                    }
                    closePopover();
                });
            });
        }

        function doRemoveSelected() {
            var ids = Array.from(state.selected);
            if (!ids.length) return;
            if (!confirm('Remove ' + ids.length + ' item(s) from this batch?\n\nThe items stay in the library.')) return;
            fetch('/admin/batches/' + BATCH_ID + '/remove-items', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': CSRF,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({ item_type: state.type, item_ids: ids })
            })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d && d.success) {
                    toast(d.removed + ' items removed from batch', 'success');
                    state.selected.clear();
                    state.totalInBatch[state.type] = Math.max(0, state.totalInBatch[state.type] - d.removed);
                    document.getElementById(
                        state.type === 'question' ? 'tabCountQuestion' : 'tabCountPdf'
                    ).textContent = state.totalInBatch[state.type];
                    reloadItems();
                } else {
                    toast((d && d.error) || 'Remove failed', 'error');
                }
            })
            .catch(function () { toast('Network error', 'error'); });
        }

        // ─── PERMANENT DELETE ───
        var purgeState = {
            preview: null,
            loading: false,
        };

        function updatePurgeButton() {
            if (!el.purgeConfirmInput || !el.purgeConfirmBtn) return;
            var upper = (el.purgeConfirmInput.value || '').trim().toUpperCase();
            el.purgeConfirmBtn.disabled =
                (upper !== 'DELETE')
                || purgeState.loading
                || !purgeState.preview;
        }

        function updatePurgeConfirmProgress() {
            if (!el.purgeConfirmInput || !el.purgeProgress || !el.purgeConfirmStatus) return;

            var value = (el.purgeConfirmInput.value || '').toUpperCase();
            var target = 'DELETE';

            var slots = el.purgeProgress.querySelectorAll('.bm-confirm__slot');
            slots.forEach(function (slot, i) {
                var letter = target.charAt(i);
                var typed = value.charAt(i);
                if (typed && typed === letter) {
                    slot.classList.add('is-filled');
                } else {
                    slot.classList.remove('is-filled');
                }
            });

            var isReady = (value === target);

            if (isReady) {
                el.purgeConfirmStatus.className = 'bm-confirm__status is-ready';
                el.purgeConfirmStatus.innerHTML =
                    '<i class="fas fa-check-circle"></i>' +
                    '<span>Ready to delete</span>';
            } else if (value.length > 0) {
                var remaining = Math.max(0, target.length - value.length);
                el.purgeConfirmStatus.className = 'bm-confirm__status is-typing';
                el.purgeConfirmStatus.innerHTML =
                    '<i class="fas fa-circle-notch fa-spin"></i>' +
                    '<span>Keep typing… ' + remaining +
                    ' letter' + (remaining === 1 ? '' : 's') + ' left</span>';
            } else {
                el.purgeConfirmStatus.className = 'bm-confirm__status';
                el.purgeConfirmStatus.innerHTML =
                    '<i class="fas fa-circle-info"></i>' +
                    '<span>Confirmation required</span>';
            }

            updatePurgeButton();
        }

        function updateBackupHint() {
            if (!el.purgeBackupHint || !el.purgeBackupCheck) return;
            if (el.purgeBackupCheck.checked) {
                el.purgeBackupHint.className = 'bm-backup__hint is-on';
                el.purgeBackupHint.innerHTML =
                    '<i class="fas fa-shield-alt"></i>' +
                    '<span>A full database backup will be saved to ' +
                    '<code>BACKUPS/</code> before any rows are deleted.</span>';
            } else {
                el.purgeBackupHint.className = 'bm-backup__hint is-off';
                el.purgeBackupHint.innerHTML =
                    '<i class="fas fa-exclamation-triangle"></i>' +
                    '<span>No backup will be created. The deletion is ' +
                    'permanent and cannot be undone.</span>';
            }
        }

        function openPurgeModal() {
            if (!el.purgeModal) {
                toast('Purge modal missing from detail.html.', 'error');
                return;
            }
            var ids = Array.from(state.selected);
            if (!ids.length) { toast('Select items first.', 'warning'); return; }

            purgeState.preview = null;
            purgeState.loading = true;

            el.purgeConfirmInput.value = '';
            el.purgeConfirmInput.setAttribute('autocapitalize', 'characters');

            // Reset progress
            if (el.purgeProgress) {
                el.purgeProgress.querySelectorAll('.bm-confirm__slot').forEach(function (s) {
                    s.classList.remove('is-filled');
                });
            }
            if (el.purgeConfirmStatus) {
                el.purgeConfirmStatus.className = 'bm-confirm__status';
                el.purgeConfirmStatus.innerHTML =
                    '<i class="fas fa-circle-info"></i>' +
                    '<span>Confirmation required</span>';
            }
            updatePurgeButton();

            el.purgeStats.innerHTML =
                '<div class="bm-impact-loading">' +
                '<i class="fas fa-spinner fa-spin"></i>' +
                '<span>Computing impact…</span>' +
                '</div>';

            if (el.purgeBackupCheck) el.purgeBackupCheck.checked = true;
            updateBackupHint();

            el.purgeModal.hidden = false;

            fetch('/admin/batches/' + BATCH_ID + '/purge-preview', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': CSRF,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({
                    item_ids: ids,
                    item_type: state.type,
                })
            })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                purgeState.loading = false;
                if (!d || d.error) {
                    el.purgeStats.innerHTML =
                        '<div class="bm-impact-error">' +
                        '<i class="fas fa-exclamation-circle"></i>' +
                        '<span>' + escapeHtml((d && d.error) || 'Could not load preview.') + '</span>' +
                        '</div>';
                    updatePurgeButton();
                    return;
                }
                purgeState.preview = d;
                renderPurgeStats(d);
                updatePurgeButton();
            })
            .catch(function () {
                purgeState.loading = false;
                el.purgeStats.innerHTML =
                    '<div class="bm-impact-error">' +
                    '<i class="fas fa-exclamation-circle"></i>' +
                    '<span>Network error — could not reach the server.</span>' +
                    '</div>';
                updatePurgeButton();
            });
        }

        function renderPurgeStats(d) {
            var c = d.counts || {};
            var qCount = c.questions || 0;
            var pCount = c.pdfs || 0;

            var html = '';

            html += '<div class="bm-impact-grid">';
            if (qCount > 0) {
                html += '<div class="bm-impact-stat bm-impact-stat--q">' +
                        '<div class="bm-impact-stat__icon">📝</div>' +
                        '<div class="bm-impact-stat__body">' +
                        '<div class="bm-impact-stat__num">' + qCount + '</div>' +
                        '<div class="bm-impact-stat__label">Question' + (qCount === 1 ? '' : 's') + '</div>' +
                        '</div>' +
                        '</div>';
            }
            if (pCount > 0) {
                html += '<div class="bm-impact-stat bm-impact-stat--p">' +
                        '<div class="bm-impact-stat__icon">📄</div>' +
                        '<div class="bm-impact-stat__body">' +
                        '<div class="bm-impact-stat__num">' + pCount + '</div>' +
                        '<div class="bm-impact-stat__label">PDF' + (pCount === 1 ? '' : 's') + '</div>' +
                        '</div>' +
                        '</div>';
            }
            html += '</div>';

            var related = [];
            var relLabels = {
                quiz_ratings: 'Quiz ratings',
                question_interactions: 'Likes, saves & reports',
                question_miss_stats: 'Miss-rate statistics',
                question_duplicate_dismissals: 'Duplicate dismissals',
                pdf_reports: 'PDF reports',
                saved_content: 'User bookmarks',
                unverified_pdfs: 'Unverified queue entries',
                content_batch_items: 'Batch memberships (across all batches)',
            };
            Object.keys(relLabels).forEach(function (k) {
                var n = c[k] || 0;
                if (n > 0) related.push({ label: relLabels[k], count: n });
            });

            if (related.length) {
                html += '<div class="bm-impact-related">';
                html += '<div class="bm-impact-related__head">';
                html += '<i class="fas fa-link"></i>';
                html += '<span>Also removed</span>';
                html += '</div>';
                html += '<ul class="bm-impact-related__list">';
                related.forEach(function (r) {
                    html += '<li>';
                    html += '<span class="bm-impact-related__count">' + r.count + '</span>';
                    html += '<span class="bm-impact-related__label">' + escapeHtml(r.label) + '</span>';
                    html += '</li>';
                });
                html += '</ul>';
                html += '</div>';
            }

            el.purgeStats.innerHTML = html;
        }

        if (el.purgeConfirmInput && el.purgeConfirmBtn && el.purgeModal) {

            if (el.purgeBackupCheck) {
                el.purgeBackupCheck.addEventListener('change', updateBackupHint);
                updateBackupHint();
            }

            // Input handler — normalises case, updates slots, updates status,
            // and enables/disables the confirm button via updatePurgeButton().
            el.purgeConfirmInput.addEventListener('input', function () {
                var raw = el.purgeConfirmInput.value;
                var upper = (raw || '').toUpperCase();
                if (upper !== raw) el.purgeConfirmInput.value = upper;
                updatePurgeConfirmProgress();
            });

            el.purgeConfirmInput.addEventListener('paste', function () {
                setTimeout(updatePurgeConfirmProgress, 0);
            });
            el.purgeConfirmInput.addEventListener('change', updatePurgeConfirmProgress);
            el.purgeConfirmInput.addEventListener('keyup', updatePurgeConfirmProgress);

            el.purgeConfirmBtn.addEventListener('click', function () {
                if (!purgeState.preview) return;
                var ids = Array.from(state.selected);
                if (!ids.length) { toast('Selection is empty.', 'warning'); return; }

                var wantBackup = el.purgeBackupCheck ? el.purgeBackupCheck.checked : true;

                var origHTML = el.purgeConfirmBtn.innerHTML;
                el.purgeConfirmBtn.disabled = true;
                el.purgeConfirmBtn.innerHTML =
                    '<i class="fas fa-spinner fa-spin"></i> Deleting…';

                fetch('/admin/batches/' + BATCH_ID + '/purge', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': CSRF,
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    body: JSON.stringify({
                        confirm: 'DELETE',
                        item_ids: ids,
                        item_type: state.type,
                        backup: wantBackup,
                    })
                })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    el.purgeConfirmBtn.disabled = false;
                    el.purgeConfirmBtn.innerHTML = origHTML;

                    if (!d || !d.success) {
                        toast((d && d.error) || 'Purge failed', 'error');
                        return;
                    }

                    var n = d.total_deleted || 0;
                    var msg = n + ' item' + (n === 1 ? '' : 's') + ' permanently deleted';
                    if (d.backup_path) msg += ' (backup saved)';
                    toast(msg, 'success');

                    el.purgeModal.hidden = true;
                    state.selected.clear();

                    var pq = (d.purged && d.purged.questions) || 0;
                    var pp = (d.purged && d.purged.pdfs) || 0;
                    if (pq > 0) {
                        state.totalInBatch.question = Math.max(0, state.totalInBatch.question - pq);
                        document.getElementById('tabCountQuestion').textContent = state.totalInBatch.question;
                    }
                    if (pp > 0) {
                        state.totalInBatch.pdf = Math.max(0, state.totalInBatch.pdf - pp);
                        document.getElementById('tabCountPdf').textContent = state.totalInBatch.pdf;
                    }

                    reloadItems();
                })
                .catch(function () {
                    el.purgeConfirmBtn.disabled = false;
                    el.purgeConfirmBtn.innerHTML = origHTML;
                    toast('Network error', 'error');
                });
            });

            el.purgeModal.addEventListener('click', function (e) {
                if (e.target === el.purgeModal || e.target.hasAttribute('data-close-modal')) {
                    el.purgeModal.hidden = true;
                }
            });

            document.addEventListener('keydown', function (e) {
                if (e.key === 'Escape' && el.purgeModal && !el.purgeModal.hidden) {
                    el.purgeModal.hidden = true;
                }
            });
        }

        function showUndoToast(count, editId, seconds) {
            if (!editId) return;
            el.undoText.textContent = count + ' items updated';
            el.undoToast.hidden = false;
            el.undoBtn.dataset.editId = editId;
            clearTimeout(state.undoTimeout);
            state.undoTimeout = setTimeout(function () {
                el.undoToast.hidden = true;
            }, (seconds || 60) * 1000);
        }

        el.undoBtn.addEventListener('click', function () {
            var editId = el.undoBtn.dataset.editId;
            if (!editId) return;
            el.undoBtn.disabled = true;
            el.undoBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            fetch('/admin/batches/' + BATCH_ID + '/undo/' + editId, {
                method: 'POST',
                headers: { 'X-CSRF-Token': CSRF, 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                el.undoBtn.disabled = false;
                el.undoBtn.innerHTML = '<i class="fas fa-undo"></i> Undo';
                if (d && d.success) {
                    toast('Restored ' + d.restored + ' items', 'success');
                    el.undoToast.hidden = true;
                    reloadItems();
                } else {
                    toast((d && d.error) || 'Undo failed', 'error');
                }
            })
            .catch(function () {
                el.undoBtn.disabled = false;
                el.undoBtn.innerHTML = '<i class="fas fa-undo"></i> Undo';
                toast('Network error', 'error');
            });
        });

        el.renameBtn.addEventListener('click', function () {
            el.renameModal.hidden = false;
            setTimeout(function () { el.renameInput.focus(); el.renameInput.select(); }, 20);
        });
        el.renameModal.addEventListener('click', function (e) {
            if (e.target === el.renameModal || e.target.hasAttribute('data-close-modal')) {
                el.renameModal.hidden = true;
            }
        });
        el.renameConfirm.addEventListener('click', function () {
            var name = el.renameInput.value.trim();
            if (!name) return;
            fetch('/admin/batches/' + BATCH_ID + '/rename', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': CSRF,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({ name: name })
            })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d && d.success) {
                    toast('Renamed', 'success');
                    el.renameModal.hidden = true;
                    setTimeout(function () { window.location.reload(); }, 400);
                } else {
                    toast((d && d.error) || 'Rename failed', 'error');
                }
            });
        });

        el.pinBtn.addEventListener('click', function () {
            fetch('/admin/batches/' + BATCH_ID + '/pin', {
                method: 'POST',
                headers: { 'X-CSRF-Token': CSRF, 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d && d.success) {
                    var pinned = d.pinned;
                    el.pinBtn.setAttribute('data-pinned', pinned ? '1' : '0');
                    el.pinLabel.textContent = pinned ? 'Unpin' : 'Pin';
                    toast(pinned ? 'Pinned' : 'Unpinned', 'success');
                }
            });
        });

        el.deleteBtn.addEventListener('click', function () { el.deleteModal.hidden = false; });
        el.deleteModal.addEventListener('click', function (e) {
            if (e.target === el.deleteModal || e.target.hasAttribute('data-close-modal')) {
                el.deleteModal.hidden = true;
            }
        });
        el.deleteConfirm.addEventListener('click', function () {
            fetch('/admin/batches/' + BATCH_ID + '/delete', {
                method: 'POST',
                headers: { 'X-CSRF-Token': CSRF, 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d && d.success) {
                    window.location.href = '/admin/batches/';
                } else {
                    toast((d && d.error) || 'Delete failed', 'error');
                }
            });
        });

        window.addEventListener('beforeunload', function (e) {
            if (hasAnyPending()) {
                e.preventDefault();
                e.returnValue = '';
                return '';
            }
        });

        updateClearFilters();
        reloadItems();
    }

    function boot() {
        if (document.querySelector('.batch-shell')) {
            initDetailPage();
        } else {
            initListPage();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();