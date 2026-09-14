/* ============================================================
   static/js/admin/content.js
   Content/ domain interactions:
   - Question editor radio/option synchronisation
   - PDF code live verification (question editor)
   - Bulk import: tabs, file drop, live preview, PDF code check
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

        // Highlight the option row matching the selected correct answer
        const radios = form.querySelectorAll('input[name="correct_answer"]');
        const rows = form.querySelectorAll('.q-opt-row');

        function sync() {
            const checked = form.querySelector('input[name="correct_answer"]:checked');
            const value = checked ? checked.value : null;
            rows.forEach(function (row) {
                row.classList.toggle('selected', row.dataset.letter === value);
            });
        }

        radios.forEach(function (r) {
            r.addEventListener('change', sync);
        });
        sync();

        // PDF code auto-format + verify
        const codeInput = document.getElementById('pdfCodeInput');
        const pageInput = document.getElementById('pdfPageInput');
        const preview = document.getElementById('pdfLookupPreview');

        if (codeInput) {
            let timer = null;

            codeInput.addEventListener('input', function () {
                let v = this.value.toUpperCase().replace(/[^A-Z0-9-]/g, '');
                if (v.length > 4 && v.charAt(4) !== '-') {
                    v = v.slice(0, 4) + '-' + v.slice(4);
                }
                v = v.slice(0, 9);
                this.value = v;

                clearTimeout(timer);
                timer = setTimeout(verifyPdfCode, 450);
            });

            // Verify on load if pre-filled
            if (codeInput.value.trim()) verifyPdfCode();
        }

        if (pageInput) {
            pageInput.addEventListener('input', function () {
                if (this.value && codeInput && !codeInput.value.trim()) {
                    this.style.borderColor = '#F59E0B';
                } else {
                    this.style.borderColor = '';
                }
            });
        }

        function verifyPdfCode() {
            if (!preview) return;
            const code = (codeInput.value || '').trim();

            if (!code) {
                preview.style.display = 'none';
                preview.className = '';
                return;
            }

            if (!/^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(code)) {
                preview.style.display = 'flex';
                preview.className = 'q-pdf-preview err';
                preview.innerHTML =
                    '<i class="fas fa-exclamation-triangle"></i> Invalid format. Expected XXXX-XXXX.';
                return;
            }

            preview.style.display = 'flex';
            preview.className = 'q-pdf-preview loading';
            preview.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Checking…';

            fetch('/admin/questions/pdf-info', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': CSRF,
                },
                body: JSON.stringify({ code: code }),
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data || !data.valid) {
                    preview.className = 'q-pdf-preview err';
                    preview.innerHTML =
                        '<i class="fas fa-exclamation-triangle"></i> Invalid code format.';
                    return;
                }
                if (!data.exists) {
                    preview.className = 'q-pdf-preview warn';
                    preview.innerHTML =
                        '<i class="fas fa-exclamation-triangle"></i> ' +
                        'Not in library — link resolves once uploaded.';
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

        // --- Input method tabs ---
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

        // --- Textarea + file sync ---
        const textarea = document.getElementById('biJsonData');
        const fileInput = document.getElementById('biJsonFile');
        const dropZone = document.getElementById('biDropZone');
        const fileName = document.getElementById('biFileName');

        if (fileInput) {
            fileInput.addEventListener('change', function () {
                const f = this.files[0];
                if (!f) return;
                handleFile(f);
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
                    window.AdminCore.toast('Only .json files are accepted.', 'warning');
                }
            });
        }

        function handleFile(file) {
            if (!textarea) return;
            if (fileName) {
                fileName.textContent =
                    '📄 ' + file.name + ' (' + (file.size / 1024).toFixed(1) + ' KB)';
                fileName.style.display = 'block';
            }
            const reader = new FileReader();
            reader.onload = function (e) {
                textarea.value = e.target.result;
                schedulePreview(true);
            };
            reader.readAsText(file);
        }

        // --- PDF code field ---
        const pdfCodeInput = document.getElementById('biPdfCode');
        const pdfStatus = document.getElementById('biPdfStatus');

        if (pdfCodeInput) {
            let pdfTimer = null;
            pdfCodeInput.addEventListener('input', function () {
                let v = this.value.toUpperCase().replace(/[^A-Z0-9-]/g, '');
                if (v.length > 4 && v.charAt(4) !== '-') {
                    v = v.slice(0, 4) + '-' + v.slice(4);
                }
                v = v.slice(0, 9);
                this.value = v;

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
                pdfStatus.innerHTML =
                    '<i class="fas fa-exclamation-triangle"></i> Invalid format. Expected XXXX-XXXX.';
                return;
            }

            pdfStatus.className = 'bi-pdf-status';
            pdfStatus.innerHTML =
                '<i class="fas fa-spinner fa-spin"></i> Checking PDF…';

            fetch('/admin/questions/pdf-info', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': CSRF,
                },
                body: JSON.stringify({ code: code }),
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                pdfCodeInput.classList.remove('ok', 'warn', 'err');
                if (!data || !data.valid) {
                    pdfCodeInput.classList.add('err');
                    pdfStatus.className = 'bi-pdf-status err';
                    pdfStatus.innerHTML =
                        '<i class="fas fa-times-circle"></i> Invalid code.';
                    return;
                }
                if (!data.exists) {
                    pdfCodeInput.classList.add('warn');
                    pdfStatus.className = 'bi-pdf-status warn';
                    pdfStatus.innerHTML =
                        '<i class="fas fa-exclamation-triangle"></i> ' +
                        'Not found in the library — you can still import.';
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
                pdfStatus.innerHTML =
                    '<i class="fas fa-exclamation-triangle"></i> Could not verify.';
            });
        }

        // --- Live preview ---
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
            textarea.addEventListener('paste', function () {
                setTimeout(function () { schedulePreview(true); }, 20);
            });
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
                previewBox.innerHTML =
                    '<div class="bi-pv-empty">' +
                    '<i class="fas fa-file-code"></i>' +
                    'Paste JSON to see a live preview here.' +
                    '</div>';
                setPreviewStatus('idle', '');
                return;
            }

            const key = raw + '||' + code;
            if (key === previewKey) return;
            if (inflight) return;

            inflight = true;
            setPreviewStatus('checking…', '');

            const body =
                'json_data=' + encodeURIComponent(raw) +
                '&pdf_code=' + encodeURIComponent(code);

            fetch('/admin/bulk-preview', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRF-Token': CSRF,
                },
                body: body,
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                inflight = false;
                if (!data || data.error) {
                    setPreviewStatus('error', 'err');
                    previewBox.innerHTML =
                        '<div class="bi-pv-empty" style="color:#DC2626;">' +
                        '<i class="fas fa-exclamation-triangle" style="color:#DC2626;"></i>' +
                        escapeHtml((data && data.error) || 'Invalid data') +
                        '</div>';
                    return;
                }
                previewKey = key;
                setPreviewStatus('up to date', 'ok');
                renderPreview(data);
            })
            .catch(function () {
                inflight = false;
                setPreviewStatus('network error', 'err');
                previewBox.innerHTML =
                    '<div class="bi-pv-empty" style="color:#DC2626;">' +
                    '<i class="fas fa-times-circle" style="color:#DC2626;"></i>' +
                    'Could not fetch preview.' +
                    '</div>';
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
                if (data.chapter) {
                    html += '<div><strong>Chapter:</strong> ' + escapeHtml(data.chapter) + '</div>';
                }
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
                        pdfChip = '<span class="qc ok">📄 ' + escapeHtml(q.pdf_code) +
                                  (q.pdf_page ? ' · p.' + q.pdf_page : '') + '</span>';
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
                html += '<div class="qm">';
                html += pdfChip;
                html += '<span>' + ('⭐'.repeat(q.difficulty || 1)) + '</span>';
                html += '<span>· ' + (q.options_count || 0) + ' options</span>';
                html += '</div></div>';
            });
            html += '</div>';

            if (data.truncated) {
                html += '<div style="text-align:center;font-size:11px;color:var(--text-muted);padding:8px 0;">' +
                        'Showing first 30 of ' + total + '</div>';
            }

            previewBox.innerHTML = html;
        }

        // Initial preview if textarea has content
        if (textarea && textarea.value.trim()) runPreview();
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
        initQuestionEditor();
        initBulkImport();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();