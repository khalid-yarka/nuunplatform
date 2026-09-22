/* ============================================================
   static/js/admin/content.js
   Content domain interactions.
   ============================================================ */

(function () {
    'use strict';

    const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    // ============================================================
    // 1. HELPERS
    // ============================================================
    function escapeHtml(s) {
        if (s == null) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
    function escapeAttr(s) { return escapeHtml(s); }

    function toast(msg, kind) {
        if (typeof window.showToast === 'function') {
            try { window.showToast(msg, kind || 'info'); return; } catch (e) {}
        }
        const el = document.createElement('div');
        el.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1A1A2E;color:#fff;padding:10px 18px;border-radius:10px;font-size:13px;font-weight:600;z-index:9999;box-shadow:0 8px 24px rgba(0,0,0,0.24);';
        el.textContent = msg;
        document.body.appendChild(el);
        setTimeout(() => el.remove(), 3200);
    }

    // ============================================================
    // 2. QUESTION EDITOR (single)
    // ============================================================
    function initQuestionEditor() {
        const form = document.getElementById('questionForm');
        if (!form) return;

        const radios = form.querySelectorAll('input[name="correct_answer"]');
        const rows = form.querySelectorAll('.q-opt-row');
        function sync() {
            const checked = form.querySelector('input[name="correct_answer"]:checked');
            const value = checked ? checked.value : null;
            rows.forEach(row => row.classList.toggle('selected', row.dataset.letter === value));
        }
        radios.forEach(r => r.addEventListener('change', sync));
        sync();

        const codeInput = document.getElementById('pdfCodeInput');
        const pageInput = document.getElementById('pdfPageInput');
        const preview = document.getElementById('pdfLookupPreview');

        if (codeInput) {
            let timer = null;
            codeInput.addEventListener('input', function () {
                let v = this.value.toUpperCase().replace(/[^A-Z0-9-]/g, '');
                if (v.length > 4 && v.charAt(4) !== '-') v = v.slice(0, 4) + '-' + v.slice(4);
                this.value = v.slice(0, 9);
                clearTimeout(timer);
                timer = setTimeout(verifyPdfCode, 450);
            });
            if (codeInput.value.trim()) verifyPdfCode();
        }

        if (pageInput) {
            pageInput.addEventListener('input', function () {
                this.style.borderColor = (this.value && codeInput && !codeInput.value.trim())
                    ? '#F59E0B' : '';
            });
        }

        function verifyPdfCode() {
            if (!preview) return;
            const code = (codeInput.value || '').trim();
            if (!code) { preview.style.display = 'none'; return; }
            if (!/^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(code)) {
                preview.style.display = 'flex';
                preview.className = 'q-pdf-preview err';
                preview.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Invalid format. Expected XXXX-XXXX.';
                return;
            }
            preview.style.display = 'flex';
            preview.className = 'q-pdf-preview loading';
            preview.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Checking…';

            fetch('/admin/questions/pdf-info', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
                body: JSON.stringify({ code: code }),
            })
            .then(r => r.json())
            .then(data => {
                if (!data || !data.valid) {
                    preview.className = 'q-pdf-preview err';
                    preview.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Invalid code format.';
                    return;
                }
                if (!data.exists) {
                    preview.className = 'q-pdf-preview warn';
                    preview.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Not in library — link resolves once uploaded.';
                    return;
                }
                const srcTag = data.source === 'main' ? 'published' : 'bot';
                const premium = data.is_premium ? ' 💎' : '';
                preview.className = 'q-pdf-preview ok';
                preview.innerHTML =
                    '<i class="fas fa-check-circle"></i> ' +
                    '<span>' + escapeHtml(data.title || code) + premium + '</span>' +
                    '<span class="src-tag">' + srcTag + '</span>';
            })
            .catch(() => {
                preview.className = 'q-pdf-preview err';
                preview.innerHTML = '<i class="fas fa-times-circle"></i> Could not verify.';
            });
        }
    }

    // ============================================================
    // 3. DUPLICATE SIDEBAR (single editor)
    // ============================================================
    function initDuplicateSidebar() {
        const bar = document.getElementById('dupBar');
        if (!bar) return;
    }

    // ============================================================
    // 4. BULK IMPORT (advanced)
    // ============================================================
    function initBulkImport() {
        const shell = document.getElementById('bulkShell');
        if (!shell) return;

        const IMPORT_URL = shell.getAttribute('data-import-url') || '/admin/bulk-import';
        const PREVIEW_URL = shell.getAttribute('data-preview-url') || '/admin/bulk-preview';

        const el = {
            tabs: shell.querySelectorAll('.bulk-tab'),
            pasteArea: document.getElementById('biPasteArea'),
            fileArea: document.getElementById('biFileArea'),
            inputMethod: document.getElementById('biInputMethod'),

            textarea: document.getElementById('biJsonData'),
            fileInput: document.getElementById('biJsonFile'),
            dropZone: document.getElementById('biDropZone'),
            fileName: document.getElementById('biFileName'),
            parseError: document.getElementById('bulkParseError'),

            pdfCode: document.getElementById('biPdfCode'),
            pdfClearBtn: document.getElementById('biPdfClearBtn'),
            pdfStatus: document.getElementById('biPdfStatus'),

            threshold: document.getElementById('bulkThreshold'),
            thresholdValue: document.getElementById('bulkThresholdValue'),
            scope: document.getElementById('bulkScope'),
            autoExact: document.getElementById('bulkAutoExact'),
            autoInvalid: document.getElementById('bulkAutoInvalid'),
            recheckBtn: document.getElementById('bulkRecheckBtn'),

            sidebarBody: document.getElementById('bulkSidebarBody'),
            sidebarFoot: document.getElementById('bulkSidebarFoot'),
            sidebarStatus: document.getElementById('bulkSidebarStatus'),
            importBtn: document.getElementById('bulkImportBtn'),
            importBtnLabel: document.getElementById('bulkImportBtnLabel'),

            drawer: document.getElementById('bulkDrawer'),
            drawerBackdrop: document.getElementById('bulkDrawerBackdrop'),
            drawerTitle: document.getElementById('bulkDrawerTitle'),
            drawerSub: document.getElementById('bulkDrawerSub'),
            drawerBody: document.getElementById('bulkDrawerBody'),
            drawerFoot: document.getElementById('bulkDrawerFoot'),
            drawerClose: document.getElementById('bulkDrawerClose'),

            undoToast: document.getElementById('bulkUndoToast'),
            undoText: document.getElementById('bulkUndoText'),
            undoBtn: document.getElementById('bulkUndoBtn'),
        };

        const S = {
            raw: '',
            parseError: null,
            serverError: null,
            serverData: null,
            inflight: false,
            retryAfterInflight: false,
            checkTimer: null,
            lastKey: '',

            filter: 'all',
            excluded: new Set(),
            keepAnyway: new Set(),

            threshold: 85,
            scope: 'same_grade',
            autoExact: true,
            autoInvalid: true,

            drawerIndex: null,
            drawerMode: null,
            edits: {},
        };

        // ============================================================
        // 4a. Tab switching
        // ============================================================
        el.tabs.forEach(tab => {
            tab.addEventListener('click', function () {
                el.tabs.forEach(t => t.classList.remove('active'));
                this.classList.add('active');
                const mode = this.getAttribute('data-mode');
                if (mode === 'paste') {
                    el.pasteArea.hidden = false;
                    el.fileArea.hidden = true;
                    if (el.inputMethod) el.inputMethod.value = 'paste';
                } else {
                    el.pasteArea.hidden = true;
                    el.fileArea.hidden = false;
                    if (el.inputMethod) el.inputMethod.value = 'file';
                }
            });
        });

        // ============================================================
        // 4b. File upload
        // ============================================================
        if (el.fileInput) {
            el.fileInput.addEventListener('change', function () {
                const f = this.files[0];
                if (f) handleFile(f);
            });
        }
        if (el.dropZone) {
            ['dragenter', 'dragover'].forEach(evt => {
                el.dropZone.addEventListener(evt, e => {
                    e.preventDefault();
                    el.dropZone.classList.add('dragover');
                });
            });
            ['dragleave', 'drop'].forEach(evt => {
                el.dropZone.addEventListener(evt, e => {
                    e.preventDefault();
                    el.dropZone.classList.remove('dragover');
                });
            });
            el.dropZone.addEventListener('drop', e => {
                const f = e.dataTransfer.files[0];
                if (f && f.name.toLowerCase().endsWith('.json')) {
                    handleFile(f);
                    if (el.fileInput) el.fileInput.files = e.dataTransfer.files;
                } else {
                    toast('Only .json files are accepted.', 'warning');
                }
            });
        }
        function handleFile(file) {
            if (!el.textarea) return;
            if (el.fileName) {
                el.fileName.textContent = '📄 ' + file.name + ' (' + (file.size / 1024).toFixed(1) + ' KB)';
                el.fileName.hidden = false;
            }
            const reader = new FileReader();
            reader.onload = e => {
                el.textarea.value = e.target.result;
                scheduleCheck(true);
            };
            reader.readAsText(file);
        }

        // ============================================================
        // 4c. Sidebar state
        // ============================================================
        function setSidebarStatus(state) {
            const st = el.sidebarStatus;
            if (!st) return;
            st.className = 'bulk-sidebar__status';
            if (state === 'checking') {
                st.classList.add('is-checking');
                st.textContent = 'checking';
            } else if (state === 'error') {
                st.classList.add('is-error');
                st.textContent = 'error';
            } else if (state === 'ready') {
                st.classList.add('is-ready');
                st.textContent = 'ready';
            } else {
                st.textContent = 'idle';
            }
        }

        function isIncluded(item) {
            const idx = item.index;
            if (S.keepAnyway.has(idx)) return true;
            if (S.excluded.has(idx)) return false;
            if (S.autoExact && item.duplicates.some(d => d.match_type === 'exact')) {
                return false;
            }
            if (S.autoInvalid && item.status === 'invalid') {
                return false;
            }
            return true;
        }

        function countImported() {
            if (!S.serverData) return 0;
            let n = 0;
            for (const item of S.serverData.preview) {
                if (isIncluded(item)) n++;
            }
            return n;
        }

        function filterItems() {
            if (!S.serverData) return [];
            return S.serverData.preview.filter(item => {
                if (S.filter === 'all') return true;
                return item.status === S.filter;
            });
        }

        // ============================================================
        // 4d. Render sidebar item — rich card
        // ============================================================
        function renderSidebarItem(item) {
            const idx = item.index;
            const included = isIncluded(item);
            const hasExact = item.duplicates.some(d => d.match_type === 'exact');
            const topDup = item.duplicates[0] || null;

            // Badge
            let badgeClass = 'badge-ready';
            let badgeLabel = 'Ready';
            if (item.status === 'duplicate') {
                badgeClass = hasExact ? 'badge-exact' : 'badge-duplicate';
                badgeLabel = hasExact
                    ? 'Identical'
                    : (topDup ? topDup.similarity_pct + '% match' : 'Duplicate');
            } else if (item.status === 'invalid') {
                badgeClass = 'badge-invalid';
                badgeLabel = 'Invalid';
            }

            // Options preview
            let optsHtml = '';
            const opts = item.options || {};
            const correct = item.correct_answer || '';
            ['A', 'B', 'C', 'D', 'E', 'F'].forEach(function (k) {
                const v = opts[k];
                if (!v) return;
                const cls = 'bulk-item__opt' + (k === correct ? ' is-correct' : '');
                optsHtml +=
                    '<div class="' + cls + '">' +
                      '<span class="bulk-item__opt-letter">' + k + '</span>' +
                      '<span class="bulk-item__opt-text">' + escapeHtml(v) + '</span>' +
                      (k === correct ? '<i class="fas fa-check"></i>' : '') +
                    '</div>';
            });

            // Meta chips
            let meta = '';
            if (item.subject_code)
                meta += '<span class="bulk-item__meta-chip is-subject">' +
                        '<i class="fas fa-book"></i>' + escapeHtml(item.subject_code) + '</span>';
            if (item.grade)
                meta += '<span class="bulk-item__meta-chip is-grade">' +
                        '🎓 ' + escapeHtml(item.grade) + '</span>';
            if (item.difficulty)
                meta += '<span class="bulk-item__meta-chip is-diff">' +
                        ('⭐'.repeat(item.difficulty)) + '</span>';
            if (item.chapter)
                meta += '<span class="bulk-item__meta-chip">' +
                        '<i class="fas fa-bookmark"></i>' + escapeHtml(item.chapter) + '</span>';
            if (item.pdf_code) {
                const cls = item.pdf_exists ? 'is-pdf' : 'is-pdf is-missing';
                const icon = item.pdf_exists ? 'fas fa-paperclip' : 'fas fa-unlink';
                meta += '<span class="bulk-item__meta-chip ' + cls + '">' +
                        '<i class="' + icon + '"></i>' + escapeHtml(item.pdf_code) + '</span>';
            }
            if (item.tags)
                meta += '<span class="bulk-item__meta-chip">' +
                        '<i class="fas fa-tag"></i>' + escapeHtml(item.tags) + '</span>';

            // Explanation
            let explHtml = '';
            if (item.has_explanation) {
                const short = item.explanation_short || '';
                explHtml =
                    '<div class="bulk-item__expl">' +
                      '<i class="fas fa-lightbulb"></i>' +
                      '<span>' + escapeHtml(short || 'Has explanation') + '</span>' +
                    '</div>';
            }

            // Actions
            let actions = '';
            if (item.status === 'duplicate') {
                if (S.keepAnyway.has(idx)) {
                    actions += '<button type="button" class="bulk-item__action bulk-item__action--danger" data-action="unkeep" data-idx="' + idx + '">' +
                        '<i class="fas fa-xmark"></i> Re-skip</button>';
                } else {
                    actions += '<button type="button" class="bulk-item__action" data-action="keep" data-idx="' + idx + '">' +
                        '<i class="fas fa-check"></i> Keep</button>';
                }
                actions += '<button type="button" class="bulk-item__action" data-action="resolve" data-idx="' + idx + '">' +
                    '<i class="fas fa-code-compare"></i> Resolve</button>';
            } else if (item.status === 'invalid') {
                actions += '<button type="button" class="bulk-item__action" data-action="edit" data-idx="' + idx + '">' +
                    '<i class="fas fa-pen"></i> Fix</button>';
            } else {
                actions += '<button type="button" class="bulk-item__action" data-action="edit" data-idx="' + idx + '">' +
                    '<i class="fas fa-pen"></i> Edit</button>';
                if (S.excluded.has(idx)) {
                    actions += '<button type="button" class="bulk-item__action" data-action="include" data-idx="' + idx + '">' +
                        '<i class="fas fa-check"></i> Include</button>';
                } else {
                    actions += '<button type="button" class="bulk-item__action bulk-item__action--danger" data-action="exclude" data-idx="' + idx + '">' +
                        '<i class="fas fa-ban"></i> Skip</button>';
                }
            }

            let html = '<div class="bulk-item status-' + item.status +
                       (included ? '' : ' excluded') + '" data-idx="' + idx + '">';
            html += '<div class="bulk-item__head">';
            html += '  <span class="bulk-item__idx">#' + idx + '</span>';
            html += '  <span class="bulk-item__badge ' + badgeClass + '">' + escapeHtml(badgeLabel) + '</span>';
            html += '</div>';

            if (item.status === 'invalid' && item.invalid_reason) {
                html += '<div class="bulk-item__text is-error">' + escapeHtml(item.invalid_reason) + '</div>';
            } else {
                html += '<div class="bulk-item__text">' + escapeHtml(item.question_short || '(empty)') + '</div>';
            }

            if (optsHtml) {
                html += '<div class="bulk-item__opts">' + optsHtml + '</div>';
            }

            if (meta) {
                html += '<div class="bulk-item__meta">' + meta + '</div>';
            }

            if (explHtml) html += explHtml;

            html += '<div class="bulk-item__actions">' + actions + '</div>';
            html += '</div>';
            return html;
        }

        function renderSidebar() {
            const body = el.sidebarBody;
            if (!body) return;

            if (!S.raw) {
                body.innerHTML =
                    '<div class="bulk-sb-empty">' +
                        '<i class="fas fa-magnifying-glass"></i>' +
                        '<p>Paste JSON to begin. Valid questions, duplicates, and errors will appear here.</p>' +
                    '</div>';
                el.sidebarFoot.hidden = true;
                setSidebarStatus('idle');
                return;
            }

            if (S.parseError) {
                body.innerHTML =
                    '<div class="bulk-sb-error">' +
                        '<i class="fas fa-circle-exclamation"></i>' +
                        '<p>Fix the JSON syntax above to continue.</p>' +
                    '</div>';
                el.sidebarFoot.hidden = true;
                setSidebarStatus('error');
                return;
            }

            if (S.serverError) {
                body.innerHTML =
                    '<div class="bulk-sb-error">' +
                        '<i class="fas fa-triangle-exclamation"></i>' +
                        '<p>' + escapeHtml(S.serverError) + '</p>' +
                    '</div>';
                el.sidebarFoot.hidden = true;
                setSidebarStatus('error');
                return;
            }

            if (!S.serverData) {
                body.innerHTML =
                    '<div class="bulk-sb-empty">' +
                        '<i class="fas fa-circle-notch fa-spin"></i>' +
                        '<p>Checking…</p>' +
                    '</div>';
                el.sidebarFoot.hidden = true;
                return;
            }

            const data = S.serverData;
            const filtered = filterItems();
            const importedCount = countImported();

            let html = '';

            html += '<div class="bulk-stat-grid">';
            html += '<div class="bulk-stat"><div class="bulk-stat-num">' + data.total + '</div><div class="bulk-stat-label">Total</div></div>';
            html += '<div class="bulk-stat tone-ok"><div class="bulk-stat-num">' + data.ready_count + '</div><div class="bulk-stat-label">Ready</div></div>';
            if (data.duplicate_count) {
                html += '<div class="bulk-stat tone-warn"><div class="bulk-stat-num">' + data.duplicate_count + '</div><div class="bulk-stat-label">Duplicate</div></div>';
            }
            if (data.invalid_count) {
                html += '<div class="bulk-stat tone-err"><div class="bulk-stat-num">' + data.invalid_count + '</div><div class="bulk-stat-label">Invalid</div></div>';
            }
            html += '</div>';

            html += '<div class="bulk-filters">';
            const filterDefs = [
                { key: 'all', label: 'All', count: data.total },
                { key: 'ready', label: 'Ready', count: data.ready_count },
                { key: 'duplicate', label: 'Duplicate', count: data.duplicate_count },
                { key: 'invalid', label: 'Invalid', count: data.invalid_count },
            ];
            for (const f of filterDefs) {
                if (f.key !== 'all' && f.count === 0) continue;
                html += '<button type="button" class="bulk-filter' +
                        (S.filter === f.key ? ' active' : '') +
                        '" data-filter="' + f.key + '">' +
                        escapeHtml(f.label) +
                        '<span class="bulk-filter-count">' + f.count + '</span>' +
                        '</button>';
            }
            html += '</div>';

            if (!filtered.length) {
                html += '<div class="bulk-sb-empty"><p>No items match this filter.</p></div>';
            } else {
                html += '<div class="bulk-items">';
                for (const item of filtered) {
                    html += renderSidebarItem(item);
                }
                html += '</div>';
            }

            body.innerHTML = html;

            el.sidebarFoot.hidden = false;
            el.importBtnLabel.textContent = 'Import ' + importedCount +
                ' question' + (importedCount === 1 ? '' : 's');
            el.importBtn.disabled = importedCount === 0 || S.inflight;
            setSidebarStatus('ready');
        }

        // ============================================================
        // 4e. Sidebar event delegation
        // ============================================================
        el.sidebarBody.addEventListener('click', function (e) {
            const filterBtn = e.target.closest('.bulk-filter');
            if (filterBtn) {
                S.filter = filterBtn.getAttribute('data-filter') || 'all';
                renderSidebar();
                return;
            }

            const actBtn = e.target.closest('[data-action]');
            if (actBtn) {
                e.stopPropagation();
                const action = actBtn.getAttribute('data-action');
                const idx = parseInt(actBtn.getAttribute('data-idx'), 10);
                if (isNaN(idx)) return;
                handleItemAction(action, idx);
                return;
            }

            const item = e.target.closest('.bulk-item');
            if (item) {
                const idx = parseInt(item.getAttribute('data-idx'), 10);
                if (!isNaN(idx)) openDrawer(idx, 'edit');
            }
        });

        function handleItemAction(action, idx) {
            switch (action) {
                case 'exclude':
                    S.excluded.add(idx);
                    S.keepAnyway.delete(idx);
                    renderSidebar();
                    break;
                case 'include':
                    S.excluded.delete(idx);
                    renderSidebar();
                    break;
                case 'keep':
                    S.keepAnyway.add(idx);
                    S.excluded.delete(idx);
                    renderSidebar();
                    break;
                case 'unkeep':
                    S.keepAnyway.delete(idx);
                    renderSidebar();
                    break;
                case 'resolve':
                    openDrawer(idx, 'resolve');
                    break;
                case 'edit':
                    openDrawer(idx, 'edit');
                    break;
            }
        }

        // ============================================================
        // 4f. Drawer
        // ============================================================
        function openDrawer(idx, mode) {
            if (!S.serverData) return;
            const item = S.serverData.preview.find(p => p.index === idx);
            if (!item) return;
            S.drawerIndex = idx;
            S.drawerMode = mode;
            el.drawerBackdrop.hidden = false;
            el.drawer.hidden = false;
            renderDrawer(item, mode);
        }

        function closeDrawer() {
            el.drawer.hidden = true;
            el.drawerBackdrop.hidden = true;
            S.drawerIndex = null;
            S.drawerMode = null;
        }

        el.drawerClose.addEventListener('click', closeDrawer);
        el.drawerBackdrop.addEventListener('click', closeDrawer);
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !el.drawer.hidden) closeDrawer();
        });

        function renderDrawer(item, mode) {
            el.drawerTitle.textContent = mode === 'resolve'
                ? 'Resolve duplicate · #' + item.index
                : 'Edit · #' + item.index;
            el.drawerSub.textContent = (item.subject_code || '') +
                (item.grade ? ' · ' + item.grade : '') +
                (item.status === 'invalid' ? ' · Invalid' : '');

            if (mode === 'resolve') {
                renderResolveView(item);
            } else {
                renderEditView(item);
            }
        }

        function renderResolveView(item) {
            const dupes = item.duplicates || [];
            if (!dupes.length) {
                el.drawerBody.innerHTML = '<p>No duplicates to compare.</p>';
                el.drawerFoot.innerHTML = '';
                return;
            }
            const top = dupes[0];

            let html = '';
            html += '<div class="bulk-sim-badge ' + (top.match_type === 'exact' ? 'exact' : 'fuzzy') + '">' +
                    (top.match_type === 'exact'
                        ? '🎯 Identical — 100% match'
                        : '🔎 ' + top.similarity_pct + '% similar') +
                    '</div>';

            html += '<div class="bulk-compare">';
            html += '  <div class="bulk-compare__col">';
            html += '    <div class="bulk-compare__label"><i class="fas fa-plus-circle"></i> New (import)</div>';
            html += '    <div class="bulk-compare__q">' + escapeHtml(item.question_full) + '</div>';
            html += '    <div class="bulk-compare__opts">';
            ['A', 'B', 'C', 'D', 'E', 'F'].forEach(function (k) {
                if (!item.options[k]) return;
                const isCorrect = item.correct_answer === k;
                html += '<div class="bulk-compare__opt' + (isCorrect ? ' correct' : '') + '">' +
                        '<span class="l">' + k + '.</span>' +
                        '<span>' + escapeHtml(item.options[k]) + '</span>' +
                        (isCorrect ? '<i class="fas fa-check"></i>' : '') +
                        '</div>';
            });
            html += '    </div>';
            html += '    <div class="bulk-compare__meta">' +
                    '<span>🎓 ' + escapeHtml(item.grade || '—') + '</span>' +
                    '<span>' + ('⭐'.repeat(item.difficulty || 1)) + '</span>' +
                    '</div>';
            html += '  </div>';

            html += '  <div class="bulk-compare__col">';
            html += '    <div class="bulk-compare__label"><i class="fas fa-database"></i> Existing · #' + top.id + '</div>';
            html += '    <div class="bulk-compare__q">' + escapeHtml(top.text) + '</div>';
            html += '    <div class="bulk-compare__opts">';
            ['A', 'B', 'C', 'D', 'E', 'F'].forEach(function (k) {
                if (!top.options || !top.options[k]) return;
                const isCorrect = top.correct_answer === k;
                html += '<div class="bulk-compare__opt' + (isCorrect ? ' correct' : '') + '">' +
                        '<span class="l">' + k + '.</span>' +
                        '<span>' + escapeHtml(top.options[k]) + '</span>' +
                        (isCorrect ? '<i class="fas fa-check"></i>' : '') +
                        '</div>';
            });
            html += '    </div>';
            html += '    <div class="bulk-compare__meta">' +
                    '<span>🎓 ' + escapeHtml(top.grade || '—') + '</span>' +
                    '<span>' + ('⭐'.repeat(top.difficulty || 1)) + '</span>' +
                    (top.chapter ? '<span>📖 ' + escapeHtml(top.chapter) + '</span>' : '') +
                    '</div>';
            html += '  </div>';
            html += '</div>';

            if (dupes.length > 1) {
                html += '<div style="font-size:11.5px;color:var(--text-muted);margin-bottom:8px;">' +
                        '+' + (dupes.length - 1) + ' more similar question' +
                        (dupes.length - 1 === 1 ? '' : 's') + ' found</div>';
            }

            el.drawerBody.innerHTML = html;

            el.drawerFoot.innerHTML = '';
            const btnSkip = document.createElement('button');
            btnSkip.type = 'button';
            btnSkip.className = 'bulk-drawer__btn danger';
            btnSkip.innerHTML = '<i class="fas fa-ban"></i> Skip this one';
            btnSkip.addEventListener('click', function () {
                S.excluded.add(item.index);
                S.keepAnyway.delete(item.index);
                closeDrawer();
                renderSidebar();
            });

            const btnKeep = document.createElement('button');
            btnKeep.type = 'button';
            btnKeep.className = 'bulk-drawer__btn primary';
            btnKeep.innerHTML = '<i class="fas fa-check"></i> Import anyway';
            btnKeep.addEventListener('click', function () {
                S.keepAnyway.add(item.index);
                S.excluded.delete(item.index);
                closeDrawer();
                renderSidebar();
            });

            el.drawerFoot.appendChild(btnSkip);
            el.drawerFoot.appendChild(btnKeep);
        }

        function renderEditView(item) {
            const overrides = S.edits[item.index] || {};

            const getOpts = () => overrides.options || item.options || {};
            const getCorrect = () => overrides.correct_answer || item.correct_answer || 'A';
            const getDifficulty = () => overrides.difficulty || item.difficulty || 1;
            const getText = () => overrides.question_full || item.question_full || '';
            const getExplanation = () => {
                if (overrides.explanation !== undefined) return overrides.explanation;
                return item.explanation || '';
            };

            let html = '';
            html += '<div class="bulk-edit-field">';
            html += '  <label>Question</label>';
            html += '  <textarea data-edit="question_full" rows="3">' + escapeHtml(getText()) + '</textarea>';
            html += '</div>';

            html += '<div class="bulk-edit-field">';
            html += '  <label>Options</label>';
            ['A', 'B', 'C', 'D', 'E', 'F'].forEach(function (k) {
                const opts = getOpts();
                const required = ['A', 'B', 'C'].indexOf(k) !== -1;
                if (!opts[k] && !required) return;
                const isCorrect = getCorrect() === k;
                html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">';
                html += '  <input type="radio" name="bulk-correct" value="' + k + '"' + (isCorrect ? ' checked' : '') + '>';
                html += '  <span style="font-weight:700;min-width:16px;">' + k + '.</span>';
                html += '  <input type="text" data-edit-opt="' + k + '" value="' + escapeAttr(opts[k] || '') + '" style="flex:1;" maxlength="300">';
                html += '</div>';
            });
            html += '</div>';

            html += '<div class="bulk-edit-field">';
            html += '  <label>Difficulty</label>';
            html += '  <select data-edit="difficulty">';
            [1, 2, 3, 4, 5].forEach(n => {
                html += '<option value="' + n + '"' + (getDifficulty() === n ? ' selected' : '') + '>' + '⭐'.repeat(n) + '</option>';
            });
            html += '  </select>';
            html += '</div>';

            html += '<div class="bulk-edit-field">';
            html += '  <label>Explanation (optional)</label>';
            html += '  <textarea data-edit="explanation" rows="3">' + escapeHtml(getExplanation()) + '</textarea>';
            html += '</div>';

            el.drawerBody.innerHTML = html;

            el.drawerFoot.innerHTML = '';
            const btnCancel = document.createElement('button');
            btnCancel.type = 'button';
            btnCancel.className = 'bulk-drawer__btn secondary';
            btnCancel.innerHTML = 'Cancel';
            btnCancel.addEventListener('click', closeDrawer);

            const btnSave = document.createElement('button');
            btnSave.type = 'button';
            btnSave.className = 'bulk-drawer__btn primary';
            btnSave.innerHTML = '<i class="fas fa-check"></i> Apply';
            btnSave.addEventListener('click', function () {
                const newOverrides = {};

                const textarea = el.drawerBody.querySelector('[data-edit="question_full"]');
                if (textarea) {
                    const v = textarea.value.trim();
                    if (v) newOverrides.question_full = v;
                }

                const opts = {};
                el.drawerBody.querySelectorAll('[data-edit-opt]').forEach(inp => {
                    const k = inp.getAttribute('data-edit-opt');
                    const v = inp.value.trim();
                    if (v) opts[k] = v;
                });
                if (opts.A && opts.B && opts.C) {
                    newOverrides.options = opts;
                }

                const correctRadio = el.drawerBody.querySelector('input[name="bulk-correct"]:checked');
                if (correctRadio) newOverrides.correct_answer = correctRadio.value;

                const diffSel = el.drawerBody.querySelector('[data-edit="difficulty"]');
                if (diffSel) newOverrides.difficulty = parseInt(diffSel.value, 10) || 1;

                const expl = el.drawerBody.querySelector('[data-edit="explanation"]');
                if (expl) newOverrides.explanation = expl.value.trim();

                S.edits[item.index] = newOverrides;

                // Update the local preview object so the sidebar reflects
                // the override immediately (until the next server refresh)
                const localItem = S.serverData && S.serverData.preview.find(p => p.index === item.index);
                if (localItem) {
                    if (newOverrides.question_full) {
                        localItem.question_full = newOverrides.question_full;
                        localItem.question_short = newOverrides.question_full.slice(0, 70) +
                            (newOverrides.question_full.length > 70 ? '…' : '');
                    }
                    if (newOverrides.options) localItem.options = newOverrides.options;
                    if (newOverrides.correct_answer) {
                        localItem.correct_answer = newOverrides.correct_answer;
                        localItem.correct_answer_text =
                            (localItem.options || {})[newOverrides.correct_answer] || '';
                    }
                    if (newOverrides.difficulty) localItem.difficulty = newOverrides.difficulty;
                    if (newOverrides.explanation !== undefined) {
                        localItem.explanation = newOverrides.explanation;
                        localItem.has_explanation = !!newOverrides.explanation;
                        localItem.explanation_short =
                            newOverrides.explanation.slice(0, 140) +
                            (newOverrides.explanation.length > 140 ? '…' : '');
                    }
                }

                S.excluded.delete(item.index);
                S.keepAnyway.delete(item.index);

                closeDrawer();
                renderSidebar();
                toast('Changes applied to preview', 'success');
            });

            el.drawerFoot.appendChild(btnCancel);
            el.drawerFoot.appendChild(btnSave);
        }

        // ============================================================
        // 4g. JSON validation + check scheduling
        // ============================================================
        function clientParse(raw) {
            if (!raw || !raw.trim()) {
                return { ok: false, empty: true, error: null };
            }
            try {
                const data = JSON.parse(raw);
                if (!data || typeof data !== 'object' || Array.isArray(data)) {
                    return { ok: false, empty: false, error: 'JSON root must be an object' };
                }
                if (!data.metadata || typeof data.metadata !== 'object') {
                    return { ok: false, empty: false, error: 'Missing "metadata" section' };
                }
                if (!Array.isArray(data.questions) || !data.questions.length) {
                    return { ok: false, empty: false, error: '"questions" must be a non-empty array' };
                }
                return { ok: true, data };
            } catch (e) {
                return { ok: false, empty: false, error: e.message };
            }
        }

        function showParseError(msg) {
            if (!el.parseError) return;
            if (!msg) {
                el.parseError.hidden = true;
                el.parseError.innerHTML = '';
                if (el.textarea) el.textarea.classList.remove('has-error');
                return;
            }
            el.parseError.hidden = false;
            el.parseError.innerHTML = '<i class="fas fa-circle-exclamation"></i><div>' +
                escapeHtml(msg) + '</div>';
            if (el.textarea) el.textarea.classList.add('has-error');
        }

        function scheduleCheck(immediate) {
            clearTimeout(S.checkTimer);
            S.checkTimer = setTimeout(runCheck, immediate ? 60 : 350);
        }

        function runCheck() {
            const raw = (el.textarea && el.textarea.value) || '';
            S.raw = raw;

            if (!raw.trim()) {
                S.parseError = null;
                S.serverData = null;
                S.serverError = null;
                showParseError(null);
                renderSidebar();
                return;
            }

            const parsed = clientParse(raw);
            if (!parsed.ok) {
                S.parseError = parsed.empty ? null : parsed.error;
                S.serverData = null;
                S.serverError = null;
                showParseError(parsed.empty ? null : parsed.error);
                renderSidebar();
                return;
            }

            S.parseError = null;
            showParseError(null);

            const pdfCode = (el.pdfCode && el.pdfCode.value) || '';
            const key = [
                raw,
                pdfCode,
                S.threshold,
                S.scope,
                S.autoExact ? '1' : '0',
                S.autoInvalid ? '1' : '0',
            ].join('||');

            if (key === S.lastKey && S.serverData) {
                renderSidebar();
                return;
            }

            if (S.inflight) {
                S.retryAfterInflight = true;
                return;
            }

            S.inflight = true;
            setSidebarStatus('checking');

            fetch(PREVIEW_URL, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': CSRF,
                },
                body: JSON.stringify({
                    json_data: raw,
                    pdf_code: pdfCode,
                    fuzzy_threshold: S.threshold / 100,
                    scope: S.scope,
                    auto_exclude_exact: S.autoExact,
                    auto_exclude_invalid: S.autoInvalid,
                }),
            })
            .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
            .then(res => {
                S.inflight = false;

                if (!res.ok || !res.data || !res.data.ok) {
                    S.serverError = (res.data && res.data.error) || 'Server could not parse the JSON';
                    S.serverData = null;
                    renderSidebar();
                    if (S.retryAfterInflight) {
                        S.retryAfterInflight = false;
                        scheduleCheck(true);
                    }
                    return;
                }

                S.serverError = null;
                S.serverData = res.data;
                S.lastKey = key;

                const liveIdx = new Set(res.data.preview.map(p => p.index));
                Object.keys(S.edits).forEach(k => {
                    if (!liveIdx.has(parseInt(k, 10))) delete S.edits[k];
                });

                renderSidebar();

                if (S.retryAfterInflight) {
                    S.retryAfterInflight = false;
                    scheduleCheck(true);
                }
            })
            .catch(() => {
                S.inflight = false;
                S.serverError = 'Network error — could not reach the server';
                S.serverData = null;
                renderSidebar();
                if (S.retryAfterInflight) {
                    S.retryAfterInflight = false;
                    scheduleCheck(true);
                }
            });
        }

        if (el.textarea) {
            el.textarea.addEventListener('input', () => scheduleCheck(false));
            el.textarea.addEventListener('paste', () => setTimeout(() => scheduleCheck(true), 20));
        }

        // ============================================================
        // 4h. Detection controls
        // ============================================================
        if (el.threshold) {
            el.threshold.addEventListener('input', function () {
                S.threshold = parseInt(this.value, 10) || 85;
                if (el.thresholdValue) el.thresholdValue.textContent = S.threshold + '%';
            });
            el.threshold.addEventListener('change', () => scheduleCheck(true));
        }

        if (el.scope) {
            el.scope.querySelectorAll('.bulk-scope-opt').forEach(opt => {
                opt.addEventListener('click', function () {
                    el.scope.querySelectorAll('.bulk-scope-opt').forEach(o => o.classList.remove('active'));
                    this.classList.add('active');
                    S.scope = this.getAttribute('data-scope') || 'same_grade';
                    scheduleCheck(true);
                });
            });
        }

        if (el.autoExact) {
            el.autoExact.addEventListener('change', function () {
                S.autoExact = this.checked;
                renderSidebar();
            });
        }
        if (el.autoInvalid) {
            el.autoInvalid.addEventListener('change', function () {
                S.autoInvalid = this.checked;
                renderSidebar();
            });
        }

        if (el.recheckBtn) {
            el.recheckBtn.addEventListener('click', function () {
                S.lastKey = '';
                scheduleCheck(true);
            });
        }

        // ============================================================
        // 4i. PDF code field
        // ============================================================
        if (el.pdfCode) {
            let pdfTimer = null;
            el.pdfCode.addEventListener('input', function () {
                let v = this.value.toUpperCase().replace(/[^A-Z0-9-]/g, '');
                if (v.length > 4 && v.charAt(4) !== '-') v = v.slice(0, 4) + '-' + v.slice(4);
                this.value = v.slice(0, 9);
                clearTimeout(pdfTimer);
                pdfTimer = setTimeout(runPdfCheck, 450);
                scheduleCheck(false);
            });
            if (el.pdfCode.value.trim()) runPdfCheck();
        }

        if (el.pdfClearBtn) {
            el.pdfClearBtn.addEventListener('click', function () {
                if (el.pdfCode) el.pdfCode.value = '';
                if (el.pdfStatus) {
                    el.pdfStatus.className = 'bulk-pdf-status hidden';
                    el.pdfStatus.innerHTML = '';
                }
                scheduleCheck(true);
            });
        }

        function runPdfCheck() {
            if (!el.pdfCode || !el.pdfStatus) return;
            const code = (el.pdfCode.value || '').trim();
            el.pdfCode.classList.remove('ok', 'warn', 'err');

            if (!code) {
                el.pdfStatus.className = 'bulk-pdf-status hidden';
                el.pdfStatus.innerHTML = '';
                return;
            }
            if (!/^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(code)) {
                el.pdfCode.classList.add('err');
                el.pdfStatus.className = 'bulk-pdf-status err';
                el.pdfStatus.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Invalid format. Expected XXXX-XXXX.';
                return;
            }
            el.pdfStatus.className = 'bulk-pdf-status';
            el.pdfStatus.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Checking PDF…';

            fetch('/admin/questions/pdf-info', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
                body: JSON.stringify({ code: code }),
            })
            .then(r => r.json())
            .then(data => {
                el.pdfCode.classList.remove('ok', 'warn', 'err');
                if (!data || !data.valid) {
                    el.pdfCode.classList.add('err');
                    el.pdfStatus.className = 'bulk-pdf-status err';
                    el.pdfStatus.innerHTML = '<i class="fas fa-times-circle"></i> Invalid code.';
                    return;
                }
                if (!data.exists) {
                    el.pdfCode.classList.add('warn');
                    el.pdfStatus.className = 'bulk-pdf-status warn';
                    el.pdfStatus.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Not found in the library — you can still import.';
                    return;
                }
                el.pdfCode.classList.add('ok');
                const srcTag = data.source === 'main' ? 'published' : 'bot';
                const premium = data.is_premium ? ' 💎' : '';
                el.pdfStatus.className = 'bulk-pdf-status ok';
                el.pdfStatus.innerHTML =
                    '<i class="fas fa-check-circle"></i>' +
                    '<span>' + escapeHtml(data.title || code) + premium + '</span>' +
                    '<span class="tag">' + srcTag + '</span>';
            })
            .catch(() => {
                el.pdfCode.classList.add('warn');
                el.pdfStatus.className = 'bulk-pdf-status warn';
                el.pdfStatus.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Could not verify.';
            });
        }

        // ============================================================
        // 4j. Import button
        // ============================================================
        if (el.importBtn) {
            el.importBtn.addEventListener('click', function () {
                if (!S.serverData) return;
                const raw = (el.textarea && el.textarea.value) || '';
                if (!raw.trim()) return;

                const includeIndices = [];
                for (const item of S.serverData.preview) {
                    if (isIncluded(item)) includeIndices.push(item.index);
                }
                if (!includeIndices.length) {
                    toast('Nothing to import.', 'warning');
                    return;
                }

                let submitRaw = raw;
                try {
                    const parsed = JSON.parse(raw);
                    if (Array.isArray(parsed.questions)) {
                        parsed.questions = parsed.questions.map((q, i) => {
                            const idx = i + 1;
                            const override = S.edits[idx];
                            if (!override) return q;
                            const merged = Object.assign({}, q);
                            if (override.question_full) merged.question = override.question_full;
                            if (override.options) {
                                merged.options = ['A', 'B', 'C', 'D', 'E', 'F']
                                    .filter(k => override.options[k])
                                    .map(k => override.options[k]);
                                const labels = ['A', 'B', 'C', 'D', 'E', 'F'];
                                const correct = override.correct_answer || 'A';
                                merged.correct = labels.indexOf(correct) + 1;
                            }
                            if (override.difficulty) merged.difficulty = override.difficulty;
                            if (override.explanation != null) merged.explanation = override.explanation;
                            return merged;
                        });
                        submitRaw = JSON.stringify(parsed);
                    }
                } catch (e) {
                    submitRaw = raw;
                }

                el.importBtn.disabled = true;
                const origLabel = el.importBtnLabel.textContent;
                el.importBtnLabel.textContent = 'Importing…';

                fetch(IMPORT_URL, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': CSRF,
                    },
                    body: JSON.stringify({
                        json_data: submitRaw,
                        pdf_code: (el.pdfCode && el.pdfCode.value) || '',
                        import_indices: includeIndices,
                    }),
                })
                .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
                .then(res => {
                    el.importBtn.disabled = false;
                    el.importBtnLabel.textContent = origLabel;

                    if (!res.ok || !res.data || res.data.error) {
                        toast((res.data && res.data.error) || 'Import failed', 'error');
                        return;
                    }

                    const imported = res.data.imported || 0;
                    toast('Imported ' + imported + ' question' + (imported === 1 ? '' : 's'), 'success');

                    if (res.data.batch_url) {
                        setTimeout(() => {
                            if (confirm('Batch created for this import.\nOpen it now to bulk-edit these questions?')) {
                                window.location.href = res.data.batch_url;
                            } else {
                                window.location.href = '/admin/questions';
                            }
                        }, 400);
                    } else {
                        setTimeout(() => { window.location.href = '/admin/questions'; }, 700);
                    }
                })
                .catch(() => {
                    el.importBtn.disabled = false;
                    el.importBtnLabel.textContent = origLabel;
                    toast('Network error', 'error');
                });
            });
        }

        // ============================================================
        // 4k. Boot
        // ============================================================
        renderSidebar();
        if (el.textarea && el.textarea.value.trim()) runCheck();
    }

    // ============================================================
    // 5. PDF STAGING
    // ============================================================
    function initPdfStaging() {
        const master = document.getElementById('stagingSelectAll');
        const rows = document.querySelectorAll('.pdf-staging-checkbox');
        const countEl = document.getElementById('stagingCount');
        const publishBtn = document.getElementById('stagingPublishBtn');
        if (!rows.length || !publishBtn) return;

        function update() {
            const checked = document.querySelectorAll('.pdf-staging-checkbox:checked');
            const n = checked.length;
            if (countEl) countEl.textContent = n;
            publishBtn.disabled = n === 0;
            if (master) {
                master.checked = n > 0 && n === rows.length;
                master.indeterminate = n > 0 && n < rows.length;
            }
        }
        if (master) {
            master.addEventListener('change', function () {
                rows.forEach(cb => { cb.checked = master.checked; });
                update();
            });
        }
        rows.forEach(cb => cb.addEventListener('change', update));

        publishBtn.addEventListener('click', function (e) {
            const n = document.querySelectorAll('.pdf-staging-checkbox:checked').length;
            if (!n) { e.preventDefault(); return; }
            if (!confirm('Publish ' + n + ' PDF' + (n === 1 ? '' : 's') + ' to the platform?')) {
                e.preventDefault();
            }
        });
        update();
    }

    function initStagingDelete() {
        document.querySelectorAll('[data-staging-delete]').forEach(btn => {
            btn.addEventListener('click', function () {
                const id = this.getAttribute('data-staging-delete');
                const title = this.getAttribute('data-staging-title') || 'this PDF';
                if (!confirm('Delete staging PDF "' + title + '"?')) return;
                const form = document.getElementById('staging-delete-' + id);
                if (form) form.submit();
            });
        });
    }

    // ============================================================
    // 6. INTAKE TAB
    // ============================================================
    function initIntakeTab() {
        const master = document.getElementById('intakeSelectAll');
        const checkboxes = document.querySelectorAll('.pdf-intake-checkbox-input');
        const countEl = document.getElementById('intakeCount');
        const publishBtn = document.getElementById('intakeSuperPublishBtn');

        if (master && checkboxes.length && publishBtn) {
            function update() {
                const checked = document.querySelectorAll('.pdf-intake-checkbox-input:checked');
                const n = checked.length;
                if (countEl) countEl.textContent = n;
                publishBtn.disabled = n === 0;
                master.checked = n > 0 && n === checkboxes.length;
                master.indeterminate = n > 0 && n < checkboxes.length;
            }
            master.addEventListener('change', function () {
                checkboxes.forEach(cb => { cb.checked = master.checked; });
                update();
            });
            checkboxes.forEach(cb => cb.addEventListener('change', update));
            update();
        }

        document.querySelectorAll('.pdf-intake-row--clickable').forEach(row => {
            function openPreview(e) {
                if (e && e.target && e.target.closest('[data-stop-row-click], a, button, label, input')) return;
                const url = row.getAttribute('data-preview-url');
                if (url) window.open(url, '_blank', 'noopener');
            }
            row.addEventListener('click', openPreview);
            row.addEventListener('keydown', e => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    openPreview(e);
                }
            });
        });
    }

    // ============================================================
    // 7. UNVERIFIED TAB
    // ============================================================
    function initUnverifiedTab() {
        const master = document.getElementById('unverifiedSelectAll');
        const checkboxes = document.querySelectorAll('.pdf-unverified-checkbox');
        const countEl = document.getElementById('unverifiedCount');
        const confirmBtn = document.getElementById('unverifiedConfirmBtn');
        const deleteBtn = document.getElementById('unverifiedDeleteBtn');
        if (!master || !checkboxes.length) return;

        function update() {
            const checked = document.querySelectorAll('.pdf-unverified-checkbox:checked');
            const n = checked.length;
            if (countEl) countEl.textContent = n;
            if (confirmBtn) confirmBtn.disabled = n === 0;
            if (deleteBtn) deleteBtn.disabled = n === 0;
            master.checked = n > 0 && n === checkboxes.length;
            master.indeterminate = n > 0 && n < checkboxes.length;
        }
        master.addEventListener('change', function () {
            checkboxes.forEach(cb => { cb.checked = master.checked; });
            update();
        });
        checkboxes.forEach(cb => cb.addEventListener('change', update));
        update();
    }

    // ============================================================
    // 8. INTAKE BULK EDITOR
    // ============================================================
    function initIntakeBulkEditor() {
        const tbody = document.getElementById('bulkTableBody');
        if (!tbody) return;

        const state = Array.isArray(window.NUUN_BULK_ROWS) ? window.NUUN_BULK_ROWS.slice() : [];
        const subjects = window.NUUN_BULK_SUBJECTS || [];
        const curricula = window.NUUN_BULK_CURRICULA || [];
        const classes = window.NUUN_BULK_CLASSES || [];
        const previewBase = window.NUUN_BULK_PREVIEW_BASE || '';
        const intakeUrl = window.NUUN_BULK_INTAKE_URL || '/admin/pdfs?tab=intake';

        if (!state.length) return;
        state.forEach(r => { r.included = true; });

        const PAGE_SIZE = 50;
        let page = 0;
        const totalPages = Math.max(1, Math.ceil(state.length / PAGE_SIZE));

        function renderSubjectOptions(selected) {
            let html = '<option value="">— select —</option>';
            subjects.forEach(s => {
                const v = s.code || '';
                const n = s.name || v;
                html += '<option value="' + escapeAttr(v) + '"' + (v === selected ? ' selected' : '') + '>' + escapeHtml(n) + '</option>';
            });
            return html;
        }
        function renderCurriculumOptions(selected) {
            let html = '';
            curricula.forEach(c => {
                const v = Array.isArray(c) ? c[0] : c;
                const n = Array.isArray(c) ? c[1] : c;
                html += '<option value="' + escapeAttr(v) + '"' + (v === selected ? ' selected' : '') + '>' + escapeHtml(n) + '</option>';
            });
            return html;
        }
        function renderClassOptions(selected) {
            let html = '<option value="">— none —</option>';
            classes.forEach(c => {
                html += '<option value="' + escapeAttr(c) + '"' + (c === selected ? ' selected' : '') + '>' + escapeHtml(c) + '</option>';
            });
            return html;
        }

        function renderPage() {
            const start = page * PAGE_SIZE;
            const end = Math.min(start + PAGE_SIZE, state.length);
            const slice = state.slice(start, end);
            let html = '';
            slice.forEach((r, localIdx) => {
                const globalIdx = start + localIdx;
                const previewUrl = previewBase.replace('__PID__', r.pending_id);
                html += '<tr data-row-index="' + globalIdx + '" class="pdf-bulk-row' + (r.included ? '' : ' excluded') + '">';
                html += '<td class="pdf-bulk-td-center"><input type="checkbox" class="bulk-row-include" ' + (r.included ? 'checked' : '') + '></td>';
                html += '<td class="pdf-bulk-td-num">' + (globalIdx + 1) + '</td>';
                html += '<td class="pdf-bulk-td-center"><a href="' + escapeAttr(previewUrl) + '" target="_blank" rel="noopener" class="pdf-bulk-preview" title="Preview"><i class="fas fa-eye"></i></a></td>';
                html += '<td><input type="text" class="pdf-bulk-input pdf-bulk-code" value="' + escapeAttr(r.code) + '" maxlength="9" spellcheck="false"></td>';
                html += '<td><input type="text" class="pdf-bulk-input" value="' + escapeAttr(r.title) + '" maxlength="200"></td>';
                html += '<td><select class="pdf-bulk-input pdf-bulk-subject">' + renderSubjectOptions(r.subject) + '</select></td>';
                html += '<td><select class="pdf-bulk-input pdf-bulk-curriculum">' + renderCurriculumOptions(r.curriculum) + '</select></td>';
                html += '<td><select class="pdf-bulk-input pdf-bulk-class">' + renderClassOptions(r.class) + '</select></td>';
                html += '<td><input type="text" class="pdf-bulk-input" value="' + escapeAttr(r.chapter || '') + '" maxlength="100"></td>';
                html += '<td><input type="text" class="pdf-bulk-input" value="' + escapeAttr(r.tags || '') + '" maxlength="200"></td>';
                html += '<td class="pdf-bulk-td-center"><input type="checkbox" class="pdf-bulk-premium" ' + (r.is_premium ? 'checked' : '') + '></td>';
                html += '</tr>';
            });
            tbody.innerHTML = html;

            const pageInfo = document.getElementById('bulkPageInfo');
            if (pageInfo) {
                pageInfo.textContent = 'Rows ' + (start + 1) + '–' + end +
                                       ' of ' + state.length +
                                       ' (page ' + (page + 1) + '/' + totalPages + ')';
            }
        }

        function bindRowEvents() {
            tbody.querySelectorAll('tr.pdf-bulk-row').forEach(tr => {
                const idx = parseInt(tr.getAttribute('data-row-index'), 10);
                if (isNaN(idx) || !state[idx]) return;

                const includeBox = tr.querySelector('.bulk-row-include');
                if (includeBox) {
                    includeBox.addEventListener('change', function () {
                        state[idx].included = includeBox.checked;
                        tr.classList.toggle('excluded', !includeBox.checked);
                        updateCounts();
                    });
                }
                const codeIn = tr.querySelector('.pdf-bulk-code');
                if (codeIn) {
                    codeIn.addEventListener('input', function () {
                        let v = codeIn.value.toUpperCase().replace(/[^A-Z0-9-]/g, '');
                        if (v.length > 4 && v.charAt(4) !== '-') v = v.slice(0, 4) + '-' + v.slice(4);
                        codeIn.value = v.slice(0, 9);
                        state[idx].code = codeIn.value;
                    });
                }
                const titleIn = tr.querySelector('td:nth-child(5) input');
                if (titleIn) titleIn.addEventListener('input', () => { state[idx].title = titleIn.value; });

                const subjSel = tr.querySelector('.pdf-bulk-subject');
                if (subjSel) subjSel.addEventListener('change', () => { state[idx].subject = subjSel.value; });

                const currSel = tr.querySelector('.pdf-bulk-curriculum');
                if (currSel) currSel.addEventListener('change', () => { state[idx].curriculum = currSel.value; });

                const classSel = tr.querySelector('.pdf-bulk-class');
                if (classSel) classSel.addEventListener('change', () => { state[idx].class = classSel.value; });

                const chapterIn = tr.querySelector('td:nth-child(9) input');
                if (chapterIn) chapterIn.addEventListener('input', () => { state[idx].chapter = chapterIn.value; });

                const tagsIn = tr.querySelector('td:nth-child(10) input');
                if (tagsIn) tagsIn.addEventListener('input', () => { state[idx].tags = tagsIn.value; });

                const premiumBox = tr.querySelector('.pdf-bulk-premium');
                if (premiumBox) {
                    premiumBox.addEventListener('change', function () {
                        state[idx].is_premium = premiumBox.checked ? 1 : 0;
                    });
                }
            });
        }

        function updateCounts() {
            const n = state.filter(r => r.included).length;
            ['bulkCountTop', 'bulkCountBottom', 'bulkIncludedCount'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.textContent = n;
            });
        }

        function submitAll() {
            const included = state.filter(r => r.included);
            if (!included.length) {
                alert('No rows selected for publishing.');
                return;
            }
            const problems = [];
            included.forEach((r, i) => {
                if (!r.code || !/^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(r.code)) problems.push('Row ' + (i + 1) + ': invalid code');
                if (!r.title || !r.title.trim()) problems.push('Row ' + (i + 1) + ': title required');
                if (!r.subject) problems.push('Row ' + (i + 1) + ': subject required');
            });
            if (problems.length) {
                alert('Fix the following:\n\n' + problems.slice(0, 8).join('\n') +
                      (problems.length > 8 ? '\n… and ' + (problems.length - 8) + ' more' : ''));
                return;
            }
            if (!confirm('Publish ' + included.length + ' PDF' +
                         (included.length === 1 ? '' : 's') + '?')) return;

            document.getElementById('bulkRowsJson').value = JSON.stringify(included);
            document.getElementById('bulkCommitForm').submit();
        }

        const selectAll = document.getElementById('bulkSelectAll');
        if (selectAll) {
            selectAll.addEventListener('change', function (e) {
                const on = e.target.checked;
                state.forEach(r => { r.included = on; });
                renderPage();
                bindRowEvents();
                updateCounts();
            });
        }
        const prevBtn = document.getElementById('bulkPrevPage');
        if (prevBtn) prevBtn.addEventListener('click', function () {
            if (page > 0) { page--; renderPage(); bindRowEvents(); }
        });
        const nextBtn = document.getElementById('bulkNextPage');
        if (nextBtn) nextBtn.addEventListener('click', function () {
            if (page < totalPages - 1) { page++; renderPage(); bindRowEvents(); }
        });

        ['bulkPublishTop', 'bulkPublishBottom'].forEach(id => {
            const btn = document.getElementById(id);
            if (btn) btn.addEventListener('click', submitAll);
        });

        ['bulkCancelBtn', 'bulkCancelBtn2'].forEach(id => {
            const btn = document.getElementById(id);
            if (btn) btn.addEventListener('click', function () {
                if (confirm('Discard edits and return to intake?')) window.location.href = intakeUrl;
            });
        });

        renderPage();
        bindRowEvents();
        updateCounts();
    }

    // ============================================================
    // 9. BOOT
    // ============================================================
    function boot() {
        initQuestionEditor();
        initDuplicateSidebar();
        initBulkImport();
        initPdfStaging();
        initStagingDelete();
        initIntakeTab();
        initUnverifiedTab();
        initIntakeBulkEditor();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();