/* ============================================================
   static/js/admin/content.js
   Content domain interactions.
   ============================================================ */

(function () {
    'use strict';

    const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    // ------------------------------------------------------------
    // QUESTION EDITOR
    // ------------------------------------------------------------
    function initQuestionEditor() {
        const form = document.getElementById('questionForm');
        if (!form) return;

        const radios = form.querySelectorAll('input[name="correct_answer"]');
        const rows = form.querySelectorAll('.q-opt-row');

        function sync() {
            const checked = form.querySelector('input[name="correct_answer"]:checked');
            const value = checked ? checked.value : null;
            rows.forEach(function (row) {
                row.classList.toggle('selected', row.dataset.letter === value);
            });
        }
        radios.forEach(function (r) { r.addEventListener('change', sync); });
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
            .then(function (r) { return r.json(); })
            .then(function (data) {
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
            .catch(function () {
                preview.className = 'q-pdf-preview err';
                preview.innerHTML = '<i class="fas fa-times-circle"></i> Could not verify.';
            });
        }
    }

    // ------------------------------------------------------------
    // BULK IMPORT
    // ------------------------------------------------------------
    function initBulkImport() {
        const form = document.getElementById('bulkForm');
        if (!form) return;

        const tabs = form.querySelectorAll('.bi-tab');
        const pasteArea = document.getElementById('biPasteArea');
        const fileArea = document.getElementById('biFileArea');

        tabs.forEach(function (tab) {
            tab.addEventListener('click', function () {
                tabs.forEach(function (t) { t.classList.remove('active'); });
                this.classList.add('active');
                const mode = this.getAttribute('data-mode');
                if (mode === 'paste') {
                    if (pasteArea) pasteArea.style.display = '';
                    if (fileArea) fileArea.style.display = 'none';
                } else {
                    if (pasteArea) pasteArea.style.display = 'none';
                    if (fileArea) fileArea.style.display = '';
                }
            });
        });

        const textarea = document.getElementById('biJsonData');
        const fileInput = document.getElementById('biJsonFile');
        const dropZone = document.getElementById('biDropZone');
        const fileName = document.getElementById('biFileName');

        if (fileInput) {
            fileInput.addEventListener('change', function () {
                const f = this.files[0];
                if (f) handleFile(f);
            });
        }

        if (dropZone) {
            ['dragenter', 'dragover'].forEach(function (evt) {
                dropZone.addEventListener(evt, function (e) {
                    e.preventDefault();
                    dropZone.classList.add('dragover');
                });
            });
            ['dragleave', 'drop'].forEach(function (evt) {
                dropZone.addEventListener(evt, function (e) {
                    e.preventDefault();
                    dropZone.classList.remove('dragover');
                });
            });
            dropZone.addEventListener('drop', function (e) {
                const f = e.dataTransfer.files[0];
                if (f && f.name.toLowerCase().endsWith('.json')) {
                    handleFile(f);
                    if (fileInput) fileInput.files = e.dataTransfer.files;
                } else {
                    if (window.AdminCore) window.AdminCore.toast('Only .json files are accepted.', 'warning');
                }
            });
        }

        function handleFile(file) {
            if (!textarea) return;
            if (fileName) {
                fileName.textContent = '📄 ' + file.name + ' (' + (file.size / 1024).toFixed(1) + ' KB)';
                fileName.style.display = 'block';
            }
            const reader = new FileReader();
            reader.onload = function (e) {
                textarea.value = e.target.result;
                schedulePreview(true);
            };
            reader.readAsText(file);
        }

        const pdfCodeInput = document.getElementById('biPdfCode');
        const pdfStatus = document.getElementById('biPdfStatus');

        if (pdfCodeInput) {
            let pdfTimer = null;
            pdfCodeInput.addEventListener('input', function () {
                let v = this.value.toUpperCase().replace(/[^A-Z0-9-]/g, '');
                if (v.length > 4 && v.charAt(4) !== '-') v = v.slice(0, 4) + '-' + v.slice(4);
                this.value = v.slice(0, 9);
                clearTimeout(pdfTimer);
                pdfTimer = setTimeout(runPdfCheck, 450);
                schedulePreview(false);
            });
            if (pdfCodeInput.value.trim()) runPdfCheck();
        }

        function runPdfCheck() {
            if (!pdfCodeInput || !pdfStatus) return;
            const code = (pdfCodeInput.value || '').trim();
            pdfCodeInput.classList.remove('ok', 'warn', 'err');
            if (!code) {
                pdfStatus.className = 'bi-pdf-status hidden';
                pdfStatus.innerHTML = '';
                return;
            }
            if (!/^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(code)) {
                pdfCodeInput.classList.add('err');
                pdfStatus.className = 'bi-pdf-status err';
                pdfStatus.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Invalid format. Expected XXXX-XXXX.';
                return;
            }
            pdfStatus.className = 'bi-pdf-status';
            pdfStatus.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Checking PDF…';

            fetch('/admin/questions/pdf-info', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
                body: JSON.stringify({ code: code }),
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                pdfCodeInput.classList.remove('ok', 'warn', 'err');
                if (!data || !data.valid) {
                    pdfCodeInput.classList.add('err');
                    pdfStatus.className = 'bi-pdf-status err';
                    pdfStatus.innerHTML = '<i class="fas fa-times-circle"></i> Invalid code.';
                    return;
                }
                if (!data.exists) {
                    pdfCodeInput.classList.add('warn');
                    pdfStatus.className = 'bi-pdf-status warn';
                    pdfStatus.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Not found in the library — you can still import.';
                    return;
                }
                pdfCodeInput.classList.add('ok');
                const srcTag = data.source === 'main' ? 'published' : 'bot';
                const premium = data.is_premium ? ' 💎' : '';
                pdfStatus.className = 'bi-pdf-status ok';
                pdfStatus.innerHTML =
                    '<i class="fas fa-check-circle"></i>' +
                    '<span>' + escapeHtml(data.title || code) + premium + '</span>' +
                    '<span class="tag">' + srcTag + '</span>';
            })
            .catch(function () {
                pdfCodeInput.classList.add('warn');
                pdfStatus.className = 'bi-pdf-status warn';
                pdfStatus.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Could not verify.';
            });
        }

        const previewBox = document.getElementById('biPreviewContent');
        const previewStatus = document.getElementById('biPreviewStatus');
        let previewTimer = null;
        let previewKey = '';
        let inflight = false;

        function schedulePreview(immediate) {
            clearTimeout(previewTimer);
            previewTimer = setTimeout(runPreview, immediate ? 50 : 500);
        }

        if (textarea) {
            textarea.addEventListener('input', function () { schedulePreview(false); });
            textarea.addEventListener('paste', function () { setTimeout(function () { schedulePreview(true); }, 20); });
        }

        function setPreviewStatus(text, tone) {
            if (!previewStatus) return;
            previewStatus.textContent = text;
            previewStatus.style.color =
                tone === 'err'  ? '#DC2626' :
                tone === 'warn' ? '#D97706' :
                tone === 'ok'   ? '#10B981' :
                'var(--text-muted)';
        }

        function runPreview() {
            if (!textarea || !previewBox) return;
            const raw = (textarea.value || '').trim();
            const code = pdfCodeInput ? (pdfCodeInput.value || '').trim() : '';
            if (!raw) {
                previewBox.innerHTML = '<div class="bi-pv-empty"><i class="fas fa-file-code"></i>Paste JSON to see a live preview here.</div>';
                setPreviewStatus('idle', '');
                return;
            }
            const key = raw + '||' + code;
            if (key === previewKey || inflight) return;
            inflight = true;
            setPreviewStatus('checking…', '');

            fetch('/admin/bulk-preview', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRF-Token': CSRF,
                },
                body: 'json_data=' + encodeURIComponent(raw) + '&pdf_code=' + encodeURIComponent(code),
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                inflight = false;
                if (!data || data.error) {
                    setPreviewStatus('error', 'err');
                    previewBox.innerHTML =
                        '<div class="bi-pv-empty" style="color:#DC2626;">' +
                        '<i class="fas fa-exclamation-triangle" style="color:#DC2626;"></i>' +
                        escapeHtml((data && data.error) || 'Invalid data') + '</div>';
                    return;
                }
                previewKey = key;
                setPreviewStatus('up to date', 'ok');
                renderPreview(data);
            })
            .catch(function () {
                inflight = false;
                setPreviewStatus('network error', 'err');
            });
        }

        function renderPreview(data) {
            const total = data.total || 0;
            const linked = data.linked_count || 0;
            const unlinked = total - linked;

            let html = '';
            html += '<div class="bi-pv-stats">';
            html += '<div class="bi-pv-stat"><div class="n">' + total + '</div><div class="l">Total</div></div>';
            html += '<div class="bi-pv-stat green"><div class="n">' + linked + '</div><div class="l">Linked</div></div>';
            html += '<div class="bi-pv-stat amber"><div class="n">' + unlinked + '</div><div class="l">Unlinked</div></div>';
            html += '</div>';

            if (data.subject_code) {
                html += '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:10px;">';
                html += '<div><strong>Subject:</strong> ' + escapeHtml(data.subject_code) + '</div>';
                if (data.chapter) html += '<div><strong>Chapter:</strong> ' + escapeHtml(data.chapter) + '</div>';
                html += '</div>';
            }

            if ((data.unknown_codes || []).length) {
                html += '<div style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:6px;padding:8px 12px;margin-bottom:12px;font-size:12px;color:#92400E;">';
                html += '<div style="font-weight:700;margin-bottom:4px;">⚠ PDF code not found</div>';
                data.unknown_codes.forEach(function (c) {
                    html += '<div>• <code>' + escapeHtml(c) + '</code></div>';
                });
                html += '</div>';
            }

            html += '<div class="bi-pv-list">';
            (data.preview || []).forEach(function (q) {
                let cls = 'bi-pv-item';
                if (q.orphan || q.invalid_code) cls += ' warn';
                let pdfChip = '';
                if (q.pdf_code) {
                    if (q.pdf_exists) {
                        pdfChip = '<span class="qc ok">📄 ' + escapeHtml(q.pdf_code) + (q.pdf_page ? ' · p.' + q.pdf_page : '') + '</span>';
                    } else if (q.invalid_code) {
                        pdfChip = '<span class="qc warn">⚠ invalid format</span>';
                    } else {
                        pdfChip = '<span class="qc warn">⚠ ' + escapeHtml(q.pdf_code) + ' not found</span>';
                    }
                } else {
                    pdfChip = '<span class="qc none">no source</span>';
                }
                html += '<div class="' + cls + '">';
                html += '<div><span class="qi">' + q.index + '.</span> <span class="qt">' + escapeHtml(q.question) + '</span></div>';
                html += '<div class="qm">' + pdfChip + '<span>' + ('⭐'.repeat(q.difficulty || 1)) + '</span><span>· ' + (q.options_count || 0) + ' options</span></div></div>';
            });
            html += '</div>';
            if (data.truncated) {
                html += '<div style="text-align:center;font-size:11px;color:var(--text-muted);padding:8px 0;">Showing first 30 of ' + total + '</div>';
            }
            previewBox.innerHTML = html;
        }

        if (textarea && textarea.value.trim()) runPreview();
    }

    // ------------------------------------------------------------
    // PDF STAGING — bulk publish selector
    // ------------------------------------------------------------
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
                rows.forEach(function (cb) { cb.checked = master.checked; });
                update();
            });
        }
        rows.forEach(function (cb) { cb.addEventListener('change', update); });

        publishBtn.addEventListener('click', function (e) {
            const n = document.querySelectorAll('.pdf-staging-checkbox:checked').length;
            if (!n) { e.preventDefault(); return; }
            if (!confirm('Publish ' + n + ' PDF' + (n === 1 ? '' : 's') + ' to the platform? This makes them visible to all students.')) {
                e.preventDefault();
            }
        });
        update();
    }

    function initStagingDelete() {
        document.querySelectorAll('[data-staging-delete]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const id = this.getAttribute('data-staging-delete');
                const title = this.getAttribute('data-staging-title') || 'this PDF';
                if (!confirm('Delete staging PDF "' + title + '"? This cannot be undone.')) return;
                const form = document.getElementById('staging-delete-' + id);
                if (form) form.submit();
            });
        });
    }

    // ------------------------------------------------------------
    // INTAKE — bulk select + row-click preview
    // ------------------------------------------------------------
    function initIntakeTab() {
        const master = document.getElementById('intakeSelectAll');
        const checkboxes = document.querySelectorAll('.pdf-intake-checkbox-input');
        const countEl = document.getElementById('intakeCount');
        const publishBtn = document.getElementById('intakeSuperPublishBtn');

        // --- Bulk select ---
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
                checkboxes.forEach(function (cb) { cb.checked = master.checked; });
                update();
            });
            checkboxes.forEach(function (cb) { cb.addEventListener('change', update); });
            update();
        }

        // --- Row-click preview ---
        document.querySelectorAll('.pdf-intake-row--clickable').forEach(function (row) {
            function openPreview(e) {
                if (e && e.target && e.target.closest('[data-stop-row-click], a, button, label, input')) {
                    return;
                }
                const url = row.getAttribute('data-preview-url');
                if (!url) return;
                window.open(url, '_blank', 'noopener');
            }
            row.addEventListener('click', openPreview);
            row.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    openPreview(e);
                }
            });
        });
    }

    // ------------------------------------------------------------
    // UNVERIFIED TAB
    // ------------------------------------------------------------
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
            checkboxes.forEach(function (cb) { cb.checked = master.checked; });
            update();
        });
        checkboxes.forEach(function (cb) { cb.addEventListener('change', update); });
        update();
    }

    // ------------------------------------------------------------
    // BULK DIRECT PUBLISH — editable table
    // ------------------------------------------------------------
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

        state.forEach(function (r) { r.included = true; });

        const PAGE_SIZE = 50;
        let page = 0;
        const totalPages = Math.max(1, Math.ceil(state.length / PAGE_SIZE));

        // ---- Render helpers ----
        function renderSubjectOptions(selected) {
            let html = '<option value="">— select —</option>';
            subjects.forEach(function (s) {
                const v = (s.code || '');
                const n = (s.name || v);
                html += '<option value="' + escapeAttr(v) + '"' +
                        (v === selected ? ' selected' : '') + '>' +
                        escapeHtml(n) + '</option>';
            });
            return html;
        }
        function renderCurriculumOptions(selected) {
            let html = '';
            curricula.forEach(function (c) {
                const v = Array.isArray(c) ? c[0] : c;
                const n = Array.isArray(c) ? c[1] : c;
                html += '<option value="' + escapeAttr(v) + '"' +
                        (v === selected ? ' selected' : '') + '>' +
                        escapeHtml(n) + '</option>';
            });
            return html;
        }
        function renderClassOptions(selected) {
            let html = '<option value="">— none —</option>';
            classes.forEach(function (c) {
                html += '<option value="' + escapeAttr(c) + '"' +
                        (c === selected ? ' selected' : '') + '>' +
                        escapeHtml(c) + '</option>';
            });
            return html;
        }

        function renderPage() {
            const start = page * PAGE_SIZE;
            const end = Math.min(start + PAGE_SIZE, state.length);
            const slice = state.slice(start, end);

            let html = '';
            slice.forEach(function (r, localIdx) {
                const globalIdx = start + localIdx;
                const previewUrl = previewBase.replace('__PID__', r.pending_id);

                html += '<tr data-row-index="' + globalIdx + '" class="pdf-bulk-row' +
                        (r.included ? '' : ' excluded') + '">';

                // Included checkbox
                html += '<td class="pdf-bulk-td-center">' +
                        '<input type="checkbox" class="bulk-row-include" ' +
                        (r.included ? 'checked' : '') + '>' +
                        '</td>';

                // Row number
                html += '<td class="pdf-bulk-td-num">' + (globalIdx + 1) + '</td>';

                // Preview
                html += '<td class="pdf-bulk-td-center">' +
                        '<a href="' + escapeAttr(previewUrl) + '" target="_blank" rel="noopener" ' +
                        'class="pdf-bulk-preview" title="Preview">' +
                        '<i class="fas fa-eye"></i></a>' +
                        '</td>';

                // Code
                html += '<td><input type="text" class="pdf-bulk-input pdf-bulk-code" ' +
                        'value="' + escapeAttr(r.code) + '" maxlength="9" spellcheck="false"></td>';

                // Title
                html += '<td><input type="text" class="pdf-bulk-input" ' +
                        'value="' + escapeAttr(r.title) + '" maxlength="200"></td>';

                // Subject
                html += '<td><select class="pdf-bulk-input pdf-bulk-subject">' +
                        renderSubjectOptions(r.subject) + '</select></td>';

                // Curriculum
                html += '<td><select class="pdf-bulk-input pdf-bulk-curriculum">' +
                        renderCurriculumOptions(r.curriculum) + '</select></td>';

                // Class
                html += '<td><select class="pdf-bulk-input pdf-bulk-class">' +
                        renderClassOptions(r.class) + '</select></td>';

                // Chapter
                html += '<td><input type="text" class="pdf-bulk-input" ' +
                        'value="' + escapeAttr(r.chapter || '') + '" maxlength="100"></td>';

                // Tags
                html += '<td><input type="text" class="pdf-bulk-input" ' +
                        'value="' + escapeAttr(r.tags || '') + '" maxlength="200"></td>';

                // Premium
                html += '<td class="pdf-bulk-td-center">' +
                        '<input type="checkbox" class="pdf-bulk-premium" ' +
                        (r.is_premium ? 'checked' : '') + '></td>';

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
            tbody.querySelectorAll('tr.pdf-bulk-row').forEach(function (tr) {
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
                if (titleIn) {
                    titleIn.addEventListener('input', function () {
                        state[idx].title = titleIn.value;
                    });
                }

                const subjSel = tr.querySelector('.pdf-bulk-subject');
                if (subjSel) subjSel.addEventListener('change', function () { state[idx].subject = subjSel.value; });

                const currSel = tr.querySelector('.pdf-bulk-curriculum');
                if (currSel) currSel.addEventListener('change', function () { state[idx].curriculum = currSel.value; });

                const classSel = tr.querySelector('.pdf-bulk-class');
                if (classSel) classSel.addEventListener('change', function () { state[idx].class = classSel.value; });

                const chapterIn = tr.querySelector('td:nth-child(9) input');
                if (chapterIn) chapterIn.addEventListener('input', function () { state[idx].chapter = chapterIn.value; });

                const tagsIn = tr.querySelector('td:nth-child(10) input');
                if (tagsIn) tagsIn.addEventListener('input', function () { state[idx].tags = tagsIn.value; });

                const premiumBox = tr.querySelector('.pdf-bulk-premium');
                if (premiumBox) {
                    premiumBox.addEventListener('change', function () {
                        state[idx].is_premium = premiumBox.checked ? 1 : 0;
                    });
                }
            });
        }

        function updateCounts() {
            const n = state.filter(function (r) { return r.included; }).length;
            ['bulkCountTop', 'bulkCountBottom', 'bulkIncludedCount'].forEach(function (id) {
                const el = document.getElementById(id);
                if (el) el.textContent = n;
            });
        }

        function submitAll() {
            const included = state.filter(function (r) { return r.included; });
            if (!included.length) {
                alert('No rows selected for publishing.');
                return;
            }

            // Validation
            const problems = [];
            included.forEach(function (r, i) {
                if (!r.code || !/^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(r.code)) {
                    problems.push('Row ' + (i + 1) + ': invalid code');
                }
                if (!r.title || !r.title.trim()) {
                    problems.push('Row ' + (i + 1) + ': title required');
                }
                if (!r.subject) {
                    problems.push('Row ' + (i + 1) + ': subject required');
                }
            });
            if (problems.length) {
                alert('Fix the following before publishing:\n\n' + problems.slice(0, 8).join('\n') +
                      (problems.length > 8 ? '\n… and ' + (problems.length - 8) + ' more' : ''));
                return;
            }

            const summary = 'Publish ' + included.length + ' PDF' +
                           (included.length === 1 ? '' : 's') +
                           ' directly to the library?\n\n' +
                           'Auto-generated metadata will be used as-is. ' +
                           'You can edit any PDF later from the Unverified tab.';
            if (!confirm(summary)) return;

            document.getElementById('bulkRowsJson').value = JSON.stringify(included);
            document.getElementById('bulkCommitForm').submit();
        }

        // ---- Wire buttons ----
        document.getElementById('bulkSelectAll').addEventListener('change', function (e) {
            const on = e.target.checked;
            state.forEach(function (r) { r.included = on; });
            renderPage();
            bindRowEvents();
            updateCounts();
        });

        document.getElementById('bulkPrevPage').addEventListener('click', function () {
            if (page > 0) { page--; renderPage(); bindRowEvents(); }
        });
        document.getElementById('bulkNextPage').addEventListener('click', function () {
            if (page < totalPages - 1) { page++; renderPage(); bindRowEvents(); }
        });

        ['bulkPublishTop', 'bulkPublishBottom'].forEach(function (id) {
            const btn = document.getElementById(id);
            if (btn) btn.addEventListener('click', submitAll);
        });

        ['bulkCancelBtn', 'bulkCancelBtn2'].forEach(function (id) {
            const btn = document.getElementById(id);
            if (btn) {
                btn.addEventListener('click', function () {
                    if (confirm('Discard edits and return to intake?')) {
                        window.location.href = intakeUrl;
                    }
                });
            }
        });

        // ---- Initial render ----
        renderPage();
        bindRowEvents();
        updateCounts();
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
    function escapeAttr(s) {
        return escapeHtml(s);
    }

    // ------------------------------------------------------------
    // BOOT
    // ------------------------------------------------------------
    function boot() {
        initQuestionEditor();
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