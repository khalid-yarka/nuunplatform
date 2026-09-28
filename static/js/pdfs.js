/* ============================================================
   static/js/pdfs.js
   NuunPlatform — PDF library interactions
   ============================================================ */
(function () {
    'use strict';

    var CFG = window.NUUN_PDF;
    if (!CFG) return;
    var L = CFG.labels || {};

    /* ---------- View toggle ---------- */
    var grid = document.getElementById('pdfGrid');
    var gridBtn = document.getElementById('viewGridBtn');
    var listBtn = document.getElementById('viewListBtn');
    var STORAGE_KEY = 'nuun.pdfs.view';

    function applyView(view) {
        if (!grid) return;
        grid.classList.toggle('list-view', view === 'list');
        if (gridBtn) gridBtn.classList.toggle('active', view === 'grid');
        if (listBtn) listBtn.classList.toggle('active', view === 'list');
        try { localStorage.setItem(STORAGE_KEY, view); } catch (e) {}
    }
    if (gridBtn) gridBtn.addEventListener('click', function () { applyView('grid'); });
    if (listBtn) listBtn.addEventListener('click', function () { applyView('list'); });
    try {
        if (localStorage.getItem(STORAGE_KEY) === 'list') applyView('list');
    } catch (e) {}

    /* ---------- Saved count badge ---------- */
    var savedCountBadge = document.getElementById('savedCountBadge');
    var savedCount = savedCountBadge ? parseInt(savedCountBadge.textContent, 10) || 0 : 0;
    function updateSavedBadge(delta) {
        savedCount = Math.max(0, savedCount + delta);
        if (savedCountBadge) savedCountBadge.textContent = String(savedCount);
    }

    /* ---------- Toast ---------- */
    var toastTimer = null;
    function showToast(msg, kind) {
        var existing = document.querySelector('.lib-toast:not(.lib-toast--quota)');
        if (existing) existing.remove();
        clearTimeout(toastTimer);
        var t = document.createElement('div');
        t.className = 'lib-toast';
        var icon = kind === 'ok' ? '✓' : kind === 'err' ? '✕' : '•';
        t.innerHTML = '<span>' + icon + '</span><span>' + msg + '</span>';
        document.body.appendChild(t);
        toastTimer = setTimeout(function () { t.remove(); }, 3200);
    }

    function showQuotaToast(resetText) {
        var existing = document.querySelector('.lib-toast--quota');
        if (existing) existing.remove();
        var t = document.createElement('div');
        t.className = 'lib-toast lib-toast--quota';
        var body = (L.quotaToastBody || '').replace('{time}', resetText || '');
        t.innerHTML =
            '<span class="lt-icon">⏰</span>' +
            '<div class="lt-body">' +
                '<div class="lt-title">' + (L.quotaToastTitle || '') + '</div>' +
                '<div class="lt-sub">' + body + '</div>' +
            '</div>';
        document.body.appendChild(t);
        setTimeout(function () {
            t.style.transition = 'opacity 0.35s ease, transform 0.35s ease';
            t.style.opacity = '0';
            t.style.transform = 'translateX(-50%) translateY(8px)';
            setTimeout(function () { t.remove(); }, 400);
        }, 5000);
    }

    /* ---------- Menu dropdowns ---------- */
    function closeAllMenus() {
        document.querySelectorAll('.pdf-card__menu-dropdown').forEach(function (d) {
            d.hidden = true;
        });
    }

    document.addEventListener('click', function (e) {
        var btn = e.target.closest && e.target.closest('[data-menu-toggle]');
        if (btn) {
            e.preventDefault(); e.stopPropagation();
            var card = btn.closest('.pdf-card');
            var dropdown = card && card.querySelector('.pdf-card__menu-dropdown');
            if (!dropdown) return;
            var wasOpen = !dropdown.hidden;
            closeAllMenus();
            dropdown.hidden = wasOpen;
            return;
        }
        var item = e.target.closest && e.target.closest('.pdf-card__menu-item');
        if (item) {
            e.preventDefault(); e.stopPropagation();
            var action = item.dataset.action;
            var pdfId = item.dataset.pdfId;
            var card = item.closest('.pdf-card');
            closeAllMenus();
            if (action === 'save') handleSaveToggle(card, pdfId, item);
            else if (action === 'report') handleReportOpen(card, pdfId, item.dataset.pdfCode, item.dataset.pdfTitle);
            return;
        }
        var shareBtn = e.target.closest && e.target.closest('.pdf-card__btn--share');
        if (shareBtn) {
            e.preventDefault(); e.stopPropagation();
            var scard = shareBtn.closest('.pdf-card');
            handleShare(scard, shareBtn.dataset.pdfId, shareBtn.dataset.pdfTitle, shareBtn.dataset.shareUrl);
            return;
        }
        if (!e.target.closest || !e.target.closest('.pdf-card__menu-dropdown')) {
            closeAllMenus();
        }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            closeAllMenus();
            var modal = document.querySelector('.report-modal-overlay');
            if (modal) modal.remove();
        }
    });

    /* ---------- Share ---------- */
    function handleShare(card, pdfId, pdfTitle, shareUrl) {
        if (!shareUrl) {
            var u = new URL(window.location.origin + '/pdfs/');
            u.searchParams.set('pdf', pdfId);
            shareUrl = u.toString();
        }
        var shareData = {
            title: pdfTitle || 'NuunPlatform PDF',
            text: 'Check out this PDF on NuunPlatform:',
            url: shareUrl
        };
        if (navigator.share) {
            navigator.share(shareData).then(function () {
                showToast(L.shareOk || 'Shared.', 'ok');
            }).catch(function (err) {
                if (err && err.name === 'AbortError') return;
                copyToClipboard(shareUrl);
            });
            return;
        }
        copyToClipboard(shareUrl);
    }
    function copyToClipboard(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(function () {
                showToast(L.copyOk || 'Link copied.', 'ok');
            }).catch(function () { promptFallback(text); });
        } else {
            promptFallback(text);
        }
    }
    function promptFallback(text) {
        try { window.prompt('Copy this link:', text); }
        catch (e) { showToast('Could not copy — copy the URL manually.', 'err'); }
    }

    /* ---------- Save toggle ---------- */
    function handleSaveToggle(card, pdfId, item) {
        if (!CFG.isLoggedIn) {
            window.location.href = CFG.loginUrl; return;
        }
        var currentlySaved = item.classList.contains('is-saved');
        var url = (currentlySaved ? CFG.unsaveUrlTemplate : CFG.saveUrlTemplate)
                    .replace('__ID__', pdfId);
        item.disabled = true;
        fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-CSRF-Token': CFG.csrfToken }
        })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
            if (!res.ok || res.data.error) {
                if (res.data && res.data.reason === 'login') { window.location.href = CFG.loginUrl; return; }
                if (res.data && res.data.reason === 'quota') { showToast(L.quotaErr || 'Save limit reached.', 'err'); return; }
                showToast((res.data && res.data.error) || L.networkErr || 'Network error.', 'err');
                return;
            }
            var saved = !!res.data.saved;
            item.classList.toggle('is-saved', saved);
            var label = item.querySelector('.js-save-label');
            if (label) label.textContent = saved ? L.saved : L.save;
            if (card) {
                card.dataset.saved = saved ? 'true' : 'false';
                card.classList.toggle('is-saved', saved);
            }
            updateSavedBadge(saved ? 1 : -1);
            if (grid && grid.dataset.savedView === 'true' && !saved && card) {
                var remaining = document.querySelectorAll('.pdf-card').length;
                if (remaining <= 1) { window.location.reload(); }
                else {
                    card.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
                    card.style.opacity = '0';
                    card.style.transform = 'scale(0.95)';
                    setTimeout(function () { card.remove(); }, 220);
                }
            }
            showToast(saved ? L.saved : L.save, 'ok');
        })
        .catch(function () { showToast(L.networkErr || 'Network error.', 'err'); })
        .finally(function () { item.disabled = false; });
    }

    /* ---------- Report modal ---------- */
    function handleReportOpen(card, pdfId, pdfCode, pdfTitle) {
        if (!CFG.isLoggedIn) { window.location.href = CFG.loginUrl; return; }
        if (card.classList.contains('is-reported')) {
            showToast(L.reportDuplicate || 'Already reported.', 'err'); return;
        }
        var container = document.getElementById('reportModalContainer');
        if (!container) return;
        var reasonOptions = (CFG.reportReasons || []).map(function (r) {
            return '<option value="' + r[0] + '">' + r[1] + '</option>';
        }).join('');
        container.innerHTML = ''
            + '<div class="report-modal-overlay">'
            +   '<div class="report-modal" role="dialog" aria-modal="true">'
            +     '<div class="report-modal__header">'
            +       '<h3><i class="fas fa-flag"></i> ' + L.reportTitle + '</h3>'
            +       '<button type="button" class="report-modal__close" data-close><i class="fas fa-times"></i></button>'
            +     '</div>'
            +     '<form class="report-modal__form">'
            +       '<div class="report-modal__body">'
            +         '<div class="report-modal__pdf">'
            +           '<strong>' + escapeHtml(pdfTitle) + '</strong><br>'
            +           '<span style="font-family:monospace;font-size:12px;">' + escapeHtml(pdfCode) + '</span>'
            +         '</div>'
            +         '<div class="report-modal__field">'
            +           '<label>' + L.reportReasonLabel + ' <span style="color:#DC2626;">*</span></label>'
            +           '<select name="reason" required><option value="">' + L.reportReasonSelect + '</option>' + reasonOptions + '</select>'
            +         '</div>'
            +         '<div class="report-modal__field">'
            +           '<label>' + L.reportComment + '</label>'
            +           '<textarea name="comment" maxlength="500" placeholder="' + L.reportCommentPh + '"></textarea>'
            +         '</div>'
            +       '</div>'
            +       '<div class="report-modal__footer">'
            +         '<button type="button" class="report-modal__btn report-modal__btn--ghost" data-close>' + L.reportCancel + '</button>'
            +         '<button type="submit" class="report-modal__btn report-modal__btn--danger"><i class="fas fa-flag"></i> ' + L.reportSubmit + '</button>'
            +       '</div>'
            +     '</form>'
            +   '</div>'
            + '</div>';
        var modal = container.querySelector('.report-modal-overlay');
        var form = container.querySelector('.report-modal__form');
        function close() { container.innerHTML = ''; }
        container.querySelectorAll('[data-close]').forEach(function (b) { b.addEventListener('click', close); });
        modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var submitBtn = form.querySelector('button[type="submit"]');
            var reason = form.reason.value;
            var comment = form.comment.value.trim();
            if (!reason) { showToast(L.reportReasonSelect, 'err'); return; }
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ' + L.reportSubmit;
            var url = CFG.reportUrlTemplate.replace('__ID__', pdfId);
            fetch(url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CFG.csrfToken },
                body: JSON.stringify({ reason: reason, comment: comment })
            })
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
            .then(function (res) {
                if (!res.ok || res.data.error) {
                    if (res.data && res.data.reason === 'login') { window.location.href = CFG.loginUrl; return; }
                    showToast((res.data && res.data.error) || L.networkErr, 'err');
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '<i class="fas fa-flag"></i> ' + L.reportSubmit;
                    return;
                }
                card.classList.add('is-reported');
                var reportMenuItem = card.querySelector('[data-action="report"]');
                if (reportMenuItem) {
                    reportMenuItem.classList.add('is-reported');
                    var label = reportMenuItem.querySelector('.js-report-label');
                    if (label) label.textContent = L.reported;
                }
                close();
                showToast(L.reportSuccess, 'ok');
            })
            .catch(function () {
                showToast(L.networkErr, 'err');
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="fas fa-flag"></i> ' + L.reportSubmit;
            });
        });
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /* ---------- Upgrade sheet delegation ---------- */
    document.addEventListener('click', function (e) {
        var el = e.target.closest && e.target.closest('.js-upgrade');
        if (!el) return;
        e.preventDefault(); e.stopPropagation();
        var feature = el.dataset.feature || 'premium_resources';
        var requiredTier = el.dataset.requiredTier || 'premium';
        if (typeof window.openUpgradeSheet === 'function') {
            window.openUpgradeSheet({ feature: feature, requiredTier: requiredTier });
            return;
        }
        window.location.href = '/upgrade/?feature=' + encodeURIComponent(feature) +
                               '&tier=' + encodeURIComponent(requiredTier);
    });

    /* ---------- Quota-locked download → toast ---------- */
    document.addEventListener('click', function (e) {
        var el = e.target.closest && e.target.closest('.js-quota-locked');
        if (!el) return;
        e.preventDefault(); e.stopPropagation();
        showQuotaToast(el.dataset.resetText || '');
    });

    /* ---------- Decrement chip after a successful direct download ---------- */
    document.addEventListener('click', function (e) {
        var el = e.target.closest && e.target.closest('[data-action="download-direct"]');
        if (!el) return;
        // Let the browser follow the link. After a short delay,
        // decrement the chip so the user sees the effect immediately.
        setTimeout(function () {
            var chip = document.getElementById('quotaChip');
            if (!chip) return;
            var state = chip.dataset.state;
            if (state === 'unlimited') return;
            var remaining = parseInt(chip.dataset.remaining || '0', 10);
            var limit = parseInt(chip.dataset.limit || '20', 10);
            if (remaining <= 0) return;
            remaining -= 1;
            chip.dataset.remaining = String(remaining);
            var pct = limit ? Math.floor(remaining / limit * 100) : 0;
            var bar = chip.querySelector('.lib-quota__bar > span');
            if (bar) bar.style.width = pct + '%';
            var txt = document.getElementById('quotaChipText');
            var icon = chip.querySelector('.lib-quota__icon');
            if (remaining <= 0) {
                chip.dataset.state = 'exhausted';
                chip.classList.remove('is-healthy', 'is-low');
                chip.classList.add('is-exhausted');
                if (icon) icon.innerHTML = '<i class="fas fa-lock"></i>';
                if (txt) txt.textContent = (L.quotaToastBody || '').includes('{time}')
                    ? chip.dataset.resetText || ''
                    : '';
            } else if (remaining <= 5) {
                chip.dataset.state = 'low';
                chip.classList.remove('is-healthy');
                chip.classList.add('is-low');
                if (txt) txt.textContent = remaining + ' / ' + limit;
            } else {
                if (txt) txt.textContent = remaining + ' / ' + limit;
            }
        }, 250);
    });

    /* ---------- Focus highlight (existing behaviour) ---------- */
    var sharedCard = document.querySelector('.pdf-card.is-shared-highlight');
    if (sharedCard) {
        setTimeout(function () {
            try { sharedCard.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
            catch (e) { sharedCard.scrollIntoView(); }
        }, 200);
        setTimeout(function () { sharedCard.classList.remove('is-shared-highlight'); }, 6000);
    }

    var params = new URLSearchParams(window.location.search);
    var highlightCode = params.get('highlight');
    if (!highlightCode) return;

    var card = document.querySelector('.pdf-card[data-code="' + CSS.escape(highlightCode) + '"]');
    if (!card) return;

    var title = card.dataset.title || highlightCode;
    var subject = card.dataset.subject || '';
    var pagesFromUrl = params.get('pages') || '';
    var missesFromUrl = params.get('misses') || '';

    var banner = document.getElementById('focusBanner');
    document.getElementById('focusBannerTitle').textContent = title;
    var subParts = [];
    if (subject) subParts.push('📚 ' + subject);
    if (missesFromUrl) subParts.push('❌ ' + missesFromUrl + ' missed');
    if (pagesFromUrl) subParts.push('📑 pages ' + pagesFromUrl);
    if (subParts.length === 0) subParts.push('Highlighted below');
    document.getElementById('focusBannerSub').textContent = subParts.join(' · ');

    banner.classList.add('visible');
    card.classList.add('focus-highlight');

    setTimeout(function () {
        var rect = card.getBoundingClientRect();
        var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
        var bannerHeight = banner.offsetHeight || 0;
        window.scrollTo({
            top: rect.top + scrollTop - bannerHeight - 104,
            behavior: 'smooth'
        });
    }, 120);

    try {
        var cleanUrl = window.location.pathname +
            window.location.search.replace(/([?&])highlight=[^&]*/g, '$1')
                .replace(/[?&]$/, '').replace(/&&/g, '&');
        history.replaceState({}, '', cleanUrl || window.location.pathname);
    } catch (e) {}

    window.clearFocusHighlight = function () {
        card.classList.remove('focus-highlight');
        banner.classList.remove('visible');
        setTimeout(function () { banner.style.display = 'none'; }, 200);
    };
})();

window.clearFocusHighlight = window.clearFocusHighlight || function () {};