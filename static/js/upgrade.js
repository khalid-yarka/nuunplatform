// ============================================
// UPGRADE SHEET — single-tier Premium
// ============================================
// Two steps:
//   1. Plan — single Premium card with discount price
//   2. Confirm — summary + Submit → WhatsApp handoff
//
// On submit:
//   1. Reserve a new tab (WhatsApp)
//   2. POST /upgrade/api/request
//   3. Redirect the reserved tab to the pre-filled WhatsApp URL
//   4. On error, close the reserved tab and show an inline message
//
// Somali WhatsApp message is built in buildWhatsAppUrl().
// ============================================

(function() {
    'use strict';

    // ----- DOM -----
    let backdrop, sheet, content, closeBtn, handle;
    let isOpen = false;
    let isDragging = false;
    let dragStartY = 0;
    let sheetOffsetY = 0;

    // ----- State -----
    const state = {
        step: 1,
        originalPrice: 1.00,
        discountedPrice: 0.50,
        hasDiscount: false,
        finalPrice: 1.00,
        requestId: null,
        note: '',
    };

    // ----- Static config -----
    const TIER = {
        icon: '🔑',
        name: 'Premium',
        tagline: 'Everything in Free, plus the good stuff',
        features: [
            'Premium PDFs · 20 downloads/day',
            'Progress analytics & performance charts',
            'Subject analytics & detailed ranking',
            'History search, trends & CSV export',
            'Personal learning insights',
            'Advanced focus analytics',
            'Badge showcase on your profile',
            'Somali language & appearance customisation',
        ],
    };

    const PRICE = {
        monthly: 1.00,
        discounted: 0.50,
    };

    // ============================================
    // INIT
    // ============================================
    function init() {
        backdrop = document.getElementById('upgradeBackdrop');
        sheet    = document.getElementById('upgradeSheet');
        content  = document.getElementById('upgradeTrack');
        closeBtn = document.getElementById('upgradeClose');
        handle   = document.getElementById('upgradeHandle');

        if (!backdrop || !sheet || !content) {
            console.warn('Upgrade sheet elements not found.');
            return;
        }

        backdrop.addEventListener('click', closeSheet);
        if (closeBtn) closeBtn.addEventListener('click', closeSheet);

        if (handle) {
            handle.addEventListener('mousedown', onDragStart);
            handle.addEventListener('touchstart', onDragStartTouch, { passive: false });
        }

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && isOpen) closeSheet();
        });

        // Public API
        window.openUpgradeSheet = openUpgradeSheet;
        window.closeUpgradeSheet = closeSheet;

        // Backward-compat alias
        window.openSafkaPreview = openUpgradeSheet;

        // Delegated trigger for [data-tier-locked]
        document.addEventListener('click', function(e) {
            const target = e.target.closest('[data-tier-locked]');
            if (target) {
                e.preventDefault();
                openUpgradeSheet({
                    feature: target.dataset.feature || null,
                    requiredTier: target.dataset.requiredTier || 'premium',
                    message: target.dataset.lockReason || null,
                });
            }
        });
    }

    // ============================================
    // OPEN / CLOSE
    // ============================================
    function openUpgradeSheet(options) {
        options = options || {};

        state.step = 1;
        state.requestId = null;
        state.note = options.message || '';

        // Determine if the user qualifies for the first-payment discount.
        // Prefer the server-rendered flag; fall back to false.
        state.hasDiscount = !!(window.upgradeState && window.upgradeState.hasDiscount);

        state.originalPrice   = PRICE.monthly;
        state.discountedPrice = PRICE.discounted;
        state.finalPrice      = state.hasDiscount ? state.discountedPrice : state.originalPrice;

        const pag = document.getElementById('upgradePagination');
        if (pag) pag.style.display = 'none';

        sheet.style.transform = 'translateY(0)';
        sheet.classList.add('active');
        backdrop.classList.add('active');
        document.body.style.overflow = 'hidden';
        isOpen = true;

        renderStep();
    }

    function closeSheet() {
        if (!isOpen) return;
        sheet.style.transform = 'translateY(100%)';
        sheet.classList.remove('active');
        backdrop.classList.remove('active');
        document.body.style.overflow = '';
        isOpen = false;
        state.step = 1;
        state.requestId = null;
        state.note = '';
    }

    // ============================================
    // HELPERS
    // ============================================
    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.content;
        const input = document.querySelector('input[name="csrf_token"]');
        return input ? input.value : '';
    }

    function escapeHtml(text) {
        if (text === null || text === undefined) return '';
        return String(text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function money(n) { return '$' + Number(n || 0).toFixed(2); }

    // ============================================
    // ROUTER
    // ============================================
    function renderStep() {
        if (!content) return;
        let html = '';
        if (state.step === 1)      html = renderPlan();
        else if (state.step === 2) html = renderConfirm();
        content.innerHTML = html;
        bindStep();
    }

    // ============================================
    // STEP 1 — PLAN
    // ============================================
    function renderPlan() {
        const ctxMsg = state.note
            ? `<div class="sf-context">${escapeHtml(state.note)}</div>`
            : '';

        const discountRibbon = state.hasDiscount
            ? `<div class="sf-ribbon"><span>50% OFF</span><small>first month</small></div>`
            : '';

        const priceBlock = state.hasDiscount
            ? `
                <div class="sf-price">
                    <span class="sf-price-original">${money(PRICE.monthly)}</span>
                    <span class="sf-price-current">${money(PRICE.discounted)}</span>
                    <span class="sf-price-period">first month</span>
                    <span class="sf-price-after">then ${money(PRICE.monthly)}/month · cancel anytime</span>
                </div>
            `
            : `
                <div class="sf-price">
                    <span class="sf-price-current">${money(PRICE.monthly)}</span>
                    <span class="sf-price-period">per month</span>
                    <span class="sf-price-after">cancel anytime</span>
                </div>
            `;

        return `
            <div class="sf-step" data-step="1">
                <div class="sf-head">
                    <div class="sf-head-title">
                        <h2>Unlock Premium</h2>
                        <p>Everything in Free, plus the extras.</p>
                    </div>
                </div>

                ${ctxMsg}

                <div class="sf-plan-card ${state.hasDiscount ? 'sf-plan-card--discount' : ''}">
                    ${discountRibbon}
                    <div class="sf-plan-head">
                        <div class="sf-plan-badge">${TIER.icon}</div>
                        <div class="sf-plan-title">
                            <span class="sf-plan-name">${TIER.name}</span>
                            <span class="sf-plan-tag">${TIER.tagline}</span>
                        </div>
                    </div>

                    ${priceBlock}

                    <ul class="sf-plan-list">
                        ${TIER.features.map(f => `<li><i class="fas fa-check"></i> ${f}</li>`).join('')}
                    </ul>
                </div>

                <div class="sf-note">
                    🔒 No card needed. Admin activates manually.
                </div>
            </div>

            <div class="sf-footer">
                <div class="sf-footer-price">
                    <span class="sf-footer-label">First payment</span>
                    <span class="sf-footer-amount">${money(state.finalPrice)}</span>
                </div>
                <button type="button" class="sf-cta" data-next="2">
                    Continue <i class="fas fa-arrow-right"></i>
                </button>
            </div>
        `;
    }

    // ============================================
    // STEP 2 — CONFIRM
    // ============================================
    function renderConfirm() {
        const rows = [];
        rows.push(['Plan', `<strong>${TIER.icon} ${TIER.name}</strong>`]);
        rows.push(['Duration', 'Monthly']);

        if (state.hasDiscount) {
            rows.push(['First month', `<strong>${money(PRICE.discounted)}</strong>`]);
            rows.push(['Renews at', `${money(PRICE.monthly)}/month`]);
        } else {
            rows.push(['Per month', `<strong>${money(PRICE.monthly)}</strong>`]);
        }

        const rowsHtml = rows.map(([label, value]) => `
            <div class="sf-summary-row">
                <span>${label}</span>
                <span>${value}</span>
            </div>
        `).join('');

        return `
            <div class="sf-step" data-step="2">
                <div class="sf-head">
                    <button type="button" class="sf-back" data-back="1">
                        <i class="fas fa-arrow-left"></i>
                    </button>
                    <div class="sf-head-title">
                        <h2>Review &amp; submit</h2>
                        <p>You'll be redirected to WhatsApp.</p>
                    </div>
                </div>

                <div class="sf-summary">
                    ${rowsHtml}
                    <div class="sf-summary-row sf-total-row">
                        <span>Total today</span>
                        <span>${money(state.finalPrice)}</span>
                    </div>
                </div>

                <div class="sf-note-box">
                    <i class="fas fa-info-circle"></i>
                    <span>
                        Taabo <strong>Open WhatsApp</strong> si aad u dalbato m
                        <strong>premium-ka</strong>, aanad u bixiso khidmadda.
                    </span>
                </div>
            </div>

            <div class="sf-footer">
                <div class="sf-footer-price">
                    <span class="sf-footer-label">Total</span>
                    <span class="sf-footer-amount">${money(state.finalPrice)}</span>
                </div>
                <button type="button" class="sf-cta sf-cta-primary" data-submit>
                    <i class="fab fa-whatsapp"></i> Open WhatsApp
                </button>
            </div>
        `;
    }

    // ============================================
    // WHATSAPP URL BUILDER
    // ============================================
    function buildWhatsAppUrl(requestId) {
        const adminPhone = String(window.upgradeAdminPhone || '').replace(/[^\d]/g, '');
        const userName   = window.userName || 'User';
        const userPhone  = window.userPhone || '';
        const publicId   = (window.upgradeState && window.upgradeState.publicId) || '----';
        const baseUrl    = (window.baseUrl || window.location.origin).replace(/\/$/, '');
        const adminUrl   = baseUrl + '/admin/users/' +
            encodeURIComponent((window.upgradeState && window.upgradeState.userId) || '');

        const priceLine = state.hasDiscount
            ? `$0.50 (50% dhimis, qime hore: $1)`
            : `$1.00/bil`;

        const lines = [
            `Asc, wll. Waxaan raba inan Premium furto`,
            ``,
            `📌 Request ID: ${requestId}`,
            `👤 Magaca: ${userName}`,
            `📞 Telefoonka: ${userPhone}`,
            `🆔 Aqoonsiga: ${publicId}`,
            `🏷️ Dalabka: Premium — Hal Bil`,
            `💰 Qiimaha: ${priceLine}`,
            ``,
            `🔗 Eeg profaylkayga:`,
            adminUrl,
            ``,
            `Numberka lacagta lagu sodiraya waa Kee wll?`,
        ];

        const body = lines.join('\n');
        const phoneSegment = adminPhone ? adminPhone : '';
        return `https://wa.me/${phoneSegment}?text=${encodeURIComponent(body)}`;
    }

    // ============================================
    // BIND STEP EVENTS
    // ============================================
    function bindStep() {
        content.querySelectorAll('[data-back]').forEach(el => {
            el.addEventListener('click', () => {
                const back = parseInt(el.dataset.back, 10);
                if (!isNaN(back)) { state.step = back; renderStep(); }
            });
        });

        content.querySelectorAll('[data-next]').forEach(el => {
            el.addEventListener('click', () => {
                state.step = parseInt(el.dataset.next, 10) || 2;
                renderStep();
            });
        });

        const submitBtn = content.querySelector('[data-submit]');
        if (submitBtn) {
            submitBtn.addEventListener('click', onSubmit);
        }
    }

    // ============================================
    // SUBMIT
    // ============================================
    function onSubmit(e) {
        const submitBtn = e.currentTarget;
        if (submitBtn.disabled) return;

        submitBtn.disabled = true;
        const originalHtml = submitBtn.innerHTML;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Submitting…';

        // Reserve the WhatsApp tab NOW (synchronously in the click handler)
        // so popup blockers don't kill it after the async fetch.
        let waWindow = null;
        try { waWindow = window.open('about:blank', '_blank'); } catch (err) {}

        fetch('/upgrade/api/request', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': getCsrfToken(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify({
                tier: 'premium',
                duration: 'monthly',
                note: '',
            }),
        })
        .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
        .then(res => {
            const data = res.data || {};
            if (!res.ok || !data.success) {
                if (waWindow) { try { waWindow.close(); } catch (e) {} }
                submitBtn.disabled = false;
                submitBtn.innerHTML = originalHtml;
                showError(data.message || 'Could not submit. Please try again.');
                return;
            }

            state.requestId = data.request_id || '';
            const waUrl = buildWhatsAppUrl(state.requestId);

            if (waWindow && !waWindow.closed) {
                waWindow.location.href = waUrl;
                // Close the sheet behind the new tab
                setTimeout(closeSheet, 300);
            } else {
                // Popup blocked — navigate the current tab
                window.location.href = waUrl;
            }
        })
        .catch(() => {
            if (waWindow) { try { waWindow.close(); } catch (e) {} }
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalHtml;
            showError('Network error. Please try again.');
        });
    }

    function showError(msg) {
        let el = content.querySelector('.sf-error');
        if (!el) {
            el = document.createElement('div');
            el.className = 'sf-error';
            content.appendChild(el);
        }
        el.textContent = msg;
        el.style.display = 'block';
        clearTimeout(el._hideTimer);
        el._hideTimer = setTimeout(() => { el.style.display = 'none'; }, 5000);
    }

    // ============================================
    // DRAG TO DISMISS
    // ============================================
    function onDragStart(e) {
        if (!isOpen) return;
        isDragging = true;
        dragStartY = e.clientY;
        sheetOffsetY = 0;
        sheet.classList.add('dragging');
        document.addEventListener('mousemove', onDragMove);
        document.addEventListener('mouseup', onDragEnd);
        e.preventDefault();
    }
    function onDragMove(e) {
        if (!isDragging) return;
        const delta = e.clientY - dragStartY;
        if (delta > 0) {
            sheet.style.transform = 'translateY(' + delta + 'px)';
            sheetOffsetY = delta;
        }
    }
    function onDragEnd() {
        if (!isDragging) return;
        isDragging = false;
        sheet.classList.remove('dragging');
        document.removeEventListener('mousemove', onDragMove);
        document.removeEventListener('mouseup', onDragEnd);
        if (sheetOffsetY > 90) closeSheet();
        else sheet.style.transform = 'translateY(0)';
    }
    function onDragStartTouch(e) {
        if (!isOpen) return;
        const t = e.touches[0];
        isDragging = true;
        dragStartY = t.clientY;
        sheetOffsetY = 0;
        sheet.classList.add('dragging');
        document.addEventListener('touchmove', onDragMoveTouch, { passive: false });
        document.addEventListener('touchend', onDragEndTouch, { passive: false });
        e.preventDefault();
    }
    function onDragMoveTouch(e) {
        if (!isDragging) return;
        const t = e.touches[0];
        const delta = t.clientY - dragStartY;
        if (delta > 0) {
            sheet.style.transform = 'translateY(' + delta + 'px)';
            sheetOffsetY = delta;
        }
        e.preventDefault();
    }
    function onDragEndTouch() {
        if (!isDragging) return;
        isDragging = false;
        sheet.classList.remove('dragging');
        document.removeEventListener('touchmove', onDragMoveTouch);
        document.removeEventListener('touchend', onDragEndTouch);
        if (sheetOffsetY > 90) closeSheet();
        else sheet.style.transform = 'translateY(0)';
    }

    // ============================================
    // BOOT
    // ============================================
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();