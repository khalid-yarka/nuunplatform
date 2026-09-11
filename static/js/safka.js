// ============================================
// SAFKA SHEET – Feature & Upgrade Modals
// Advanced multi-slide upgrade wizard (frontend only)
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
        step: 1,                 // 1=plans 2=details 3=submit 4=success
        tier: null,              // 'dhexe' | 'hore'
        duration: 'yearly',      // 'monthly' | 'term' | 'yearly'
        originalPrice: 0,
        discountCode: null,
        discountAmount: 0,
        finalPrice: 0,
        note: '',
        requestId: null,
        feature: null,
        message: null,
    };

    const PRICES = {
        dhexe: { monthly: 1.25, term: 3.00, yearly: 5.00 },
        hore:  { monthly: 2.00, term: 4.50, yearly: 7.00 }
    };

    // Savings vs. monthly (approximate %, computed once)
    const SAVINGS = {
        dhexe: { monthly: 0, term: 40, yearly: 66 },
        hore:  { monthly: 0, term: 43, yearly: 71 }
    };

    const DURATION_LABEL = {
        monthly: 'Monthly',
        term: 'Term · 4 months',
        yearly: 'Yearly'
    };

    const TIER_META = {
        dhexe: {
            icon: '🔑',
            name: 'Dhexe',
            tagline: 'Advanced features for serious learners',
            accent: 'pink',
            features: [
                'Advanced analytics & progress charts',
                'Host live quizzes (up to 50 participants)',
                'Save up to 50 questions',
                '30 quiz attempts per day',
                'Subject filters for PDFs',
                'Expanded achievements',
                'Accent colour themes',
                'Daily digest notifications',
            ],
        },
        hore: {
            icon: '⭐',
            name: 'Hore',
            tagline: 'Complete access — nothing held back',
            accent: 'gold',
            features: [
                'Full analytics & historical trends',
                'Access to all premium PDFs',
                'Unlimited quiz attempts',
                'Unlimited saved items',
                'Full live quiz hosting & scheduling',
                'All achievements & badges',
                'Personal learning insights',
                'Weekly summary reports',
                'Advanced search filters',
                'Priority support',
            ],
        },
    };

    // ============================================
    // INIT
    // ============================================
    function init() {
        backdrop = document.getElementById('safkaBackdrop');
        sheet = document.getElementById('safkaSheet');
        content = document.getElementById('safkaTrack');
        closeBtn = document.getElementById('safkaClose');
        handle = document.getElementById('safkaHandle');

        if (!backdrop || !sheet || !content) {
            console.warn('Safka sheet elements not found.');
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

        window.openSafkaPreview = openSafkaPreview;
        window.closeSafkaSheet = closeSheet;
        window.openUpgradeSheet = function(tier) {
            openSafkaPreview({ requiredTier: tier });
        };

        // Delegated trigger for [data-tier-locked]
        document.addEventListener('click', function(e) {
            const target = e.target.closest('[data-tier-locked]');
            if (target) {
                e.preventDefault();
                openSafkaPreview({
                    feature: target.dataset.feature || null,
                    requiredTier: target.dataset.requiredTier || 'dhexe',
                    message: target.dataset.lockReason || null,
                });
            }
        });
    }

    // ============================================
    // OPEN / CLOSE
    // ============================================
    function openSafkaPreview(options) {
        options = options || {};

        state.feature = options.feature || null;
        state.message = options.message || null;
        state.tier = options.requiredTier || null;
        state.duration = 'yearly';
        state.step = 1;
        state.discountCode = null;
        state.discountAmount = 0;
        state.requestId = null;
        state.note = '';

        updatePrices();

        // Hide the legacy pagination dots
        const pag = document.getElementById('safkaPagination');
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
        state.tier = null;
        state.duration = 'yearly';
        state.discountCode = null;
        state.discountAmount = 0;
        state.finalPrice = 0;
        state.requestId = null;
        state.note = '';
        state.feature = null;
        state.message = null;
    }

    // ============================================
    // HELPERS
    // ============================================
    function updatePrices() {
        if (!state.tier) return;
        state.originalPrice = PRICES[state.tier][state.duration] || 0;
        state.finalPrice = Math.max(0, state.originalPrice - state.discountAmount);
    }

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
    // RENDER – ROUTER
    // ============================================
    function renderStep() {
        if (!content) return;

        let html = '';
        if (state.step === 1)      html = renderPlans();
        else if (state.step === 2) html = renderDetails();
        else if (state.step === 3) html = renderConfirm();
        else                       html = renderSuccess();

        content.innerHTML = html;
        bindStep();
    }

    // ============================================
    // STEP 1 – PLANS
    // ============================================
    function renderPlans() {
        const ctxMsg = state.message
            ? `<div class="sf-context">${escapeHtml(state.message)}</div>`
            : '';

        const tierCard = (key) => {
            const meta = TIER_META[key];
            const fromPrice = PRICES[key].monthly;
            const selected = state.tier === key ? 'selected' : '';
            return `
                <button type="button" class="sf-plan ${meta.accent} ${selected}" data-tier="${key}">
                    <div class="sf-plan-head">
                        <div class="sf-plan-badge">${meta.icon}</div>
                        <div class="sf-plan-title">
                            <span class="sf-plan-name">${meta.name}</span>
                            <span class="sf-plan-tag">${meta.tagline}</span>
                        </div>
                        <div class="sf-plan-price">
                            <span class="sf-from">from</span>
                            <span class="sf-amount">${money(fromPrice)}</span>
                            <span class="sf-per">/mo</span>
                        </div>
                    </div>
                    <ul class="sf-plan-list">
                        ${meta.features.slice(0, 4).map(f => `<li>${f}</li>`).join('')}
                    </ul>
                    <div class="sf-plan-cta">
                        <span>Choose ${meta.name}</span>
                        <i class="fas fa-arrow-right"></i>
                    </div>
                </button>
            `;
        };

        return `
            <div class="sf-step" data-step="1">
                <div class="sf-head">
                    <div class="sf-head-title">
                        <h2>Upgrade your plan</h2>
                        <p>Unlock more quizzes, features, and premium content.</p>
                    </div>
                    <div class="sf-steps">
                        <span class="sf-dot active"></span>
                        <span class="sf-dot"></span>
                        <span class="sf-dot"></span>
                        <span class="sf-dot"></span>
                    </div>
                </div>
                ${ctxMsg}
                <div class="sf-plan-grid">
                    ${tierCard('dhexe')}
                    ${tierCard('hore')}
                </div>
                <div class="sf-note">
                    🔒 Admin approval required. No payment is collected here.
                </div>
            </div>
        `;
    }

    // ============================================
    // STEP 2 – DETAILS
    // ============================================
    function renderDetails() {
        const meta = TIER_META[state.tier];
        const durations = ['monthly', 'term', 'yearly'];
        const savings = SAVINGS[state.tier] || {};

        const durationBtns = durations.map(d => {
            const active = state.duration === d ? 'active' : '';
            const price = PRICES[state.tier][d];
            const save = savings[d] || 0;
            const saveBadge = save > 0
                ? `<span class="sf-save">Save ${save}%</span>`
                : '';
            return `
                <button type="button" class="sf-dur ${active}" data-dur="${d}">
                    <span class="sf-dur-label">${DURATION_LABEL[d]}</span>
                    <span class="sf-dur-price">${money(price)}</span>
                    ${saveBadge}
                </button>
            `;
        }).join('');

        return `
            <div class="sf-step" data-step="2">
                <div class="sf-head">
                    <button type="button" class="sf-back" data-back="1">
                        <i class="fas fa-arrow-left"></i>
                    </button>
                    <div class="sf-head-title">
                        <h2>${meta.icon} ${meta.name}</h2>
                        <p>${meta.tagline}</p>
                    </div>
                    <div class="sf-steps">
                        <span class="sf-dot"></span>
                        <span class="sf-dot active"></span>
                        <span class="sf-dot"></span>
                        <span class="sf-dot"></span>
                    </div>
                </div>

                <div class="sf-dur-grid">
                    ${durationBtns}
                </div>

                <div class="sf-features">
                    <div class="sf-features-title">What you get</div>
                    <ul>
                        ${meta.features.map(f => `<li><i class="fas fa-check"></i> ${f}</li>`).join('')}
                    </ul>
                </div>
            </div>

            <div class="sf-footer">
                <div class="sf-footer-price">
                    <span class="sf-footer-label">Total</span>
                    <span class="sf-footer-amount" data-amount>${money(PRICES[state.tier][state.duration])}</span>
                </div>
                <button type="button" class="sf-cta" data-next="3">
                    Continue <i class="fas fa-arrow-right"></i>
                </button>
            </div>
        `;
    }

    // ============================================
    // STEP 3 – CONFIRM
    // ============================================
    function renderConfirm() {
        const meta = TIER_META[state.tier];
        const durationLabel = DURATION_LABEL[state.duration];
        const discountVisible = state.discountAmount > 0 ? '' : 'hidden';
        const discountText = state.discountAmount > 0
            ? `-${money(state.discountAmount)}`
            : '';

        return `
            <div class="sf-step" data-step="3">
                <div class="sf-head">
                    <button type="button" class="sf-back" data-back="2">
                        <i class="fas fa-arrow-left"></i>
                    </button>
                    <div class="sf-head-title">
                        <h2>Review & submit</h2>
                        <p>Confirm your request before sending.</p>
                    </div>
                    <div class="sf-steps">
                        <span class="sf-dot"></span>
                        <span class="sf-dot"></span>
                        <span class="sf-dot active"></span>
                        <span class="sf-dot"></span>
                    </div>
                </div>

                <div class="sf-summary">
                    <div class="sf-summary-row">
                        <span>Plan</span>
                        <span><strong>${meta.icon} ${meta.name}</strong></span>
                    </div>
                    <div class="sf-summary-row">
                        <span>Duration</span>
                        <span><strong>${durationLabel}</strong></span>
                    </div>
                    <div class="sf-summary-row">
                        <span>Price</span>
                        <span><strong>${money(state.originalPrice)}</strong></span>
                    </div>
                    <div class="sf-summary-row sf-discount-row" ${discountVisible ? '' : 'style="display:none;"'}>
                        <span>Discount</span>
                        <span style="color:#10B981;"><strong data-discount-text>${discountText}</strong></span>
                    </div>
                    <div class="sf-summary-row sf-total-row">
                        <span>Total</span>
                        <span data-total>${money(state.finalPrice)}</span>
                    </div>
                </div>

                <div class="sf-discount">
                    <button type="button" class="sf-discount-toggle" data-toggle-discount>
                        <i class="fas fa-tag"></i>
                        <span>Have a discount code?</span>
                        <i class="fas fa-chevron-down"></i>
                    </button>
                    <div class="sf-discount-body" hidden>
                        <div class="sf-discount-input">
                            <input type="text" placeholder="Enter code" data-discount-input
                                   value="${escapeHtml(state.discountCode || '')}"
                                   autocomplete="off" spellcheck="false">
                            <button type="button" data-apply-discount>Apply</button>
                        </div>
                        <div class="sf-discount-feedback" data-discount-feedback></div>
                    </div>
                </div>

                <div class="sf-note-box">
                    <i class="fas fa-info-circle"></i>
                    <span>After submitting, the admin will contact you on WhatsApp to complete payment.</span>
                </div>
            </div>

            <div class="sf-footer">
                <div class="sf-footer-price">
                    <span class="sf-footer-label">Total</span>
                    <span class="sf-footer-amount" data-amount>${money(state.finalPrice)}</span>
                </div>
                <button type="button" class="sf-cta sf-cta-primary" data-submit>
                    <i class="fas fa-paper-plane"></i> Submit Request
                </button>
            </div>
        `;
    }

    // ============================================
    // STEP 4 – SUCCESS
    // ============================================
    function renderSuccess() {
        const tier = state.tier;
        const meta = TIER_META[tier];
        const durationLabel = DURATION_LABEL[state.duration];
        const reqId = state.requestId || '—';

        const baseUrl = (window.baseUrl || window.location.origin).replace(/\/$/, '');
        const requestUrl = baseUrl + '/upgrade/admin/upgrade-requests/' + encodeURIComponent(reqId);
        const adminPhone = (window.upgradeAdminPhone || '').replace(/[^\d]/g, '');

        const messageBody =
            'Hello Admin, I have submitted an upgrade request.\n\n' +
            '📌 Request ID: ' + reqId + '\n' +
            '👤 Name: ' + (window.userName || 'User') + '\n' +
            '📞 Phone: ' + (window.userPhone || '') + '\n' +
            '🏷️ Plan: ' + meta.name.toUpperCase() + ' — ' + durationLabel + '\n' +
            '💰 Amount: ' + money(state.finalPrice) + '\n\n' +
            '🔗 Review here:\n' + requestUrl;

        const waUrl = adminPhone
            ? 'https://wa.me/' + adminPhone + '?text=' + encodeURIComponent(messageBody)
            : 'https://wa.me/?text=' + encodeURIComponent(messageBody);

        return `
            <div class="sf-step sf-step-success" data-step="4">
                <div class="sf-success-icon">
                    <svg viewBox="0 0 80 80" width="80" height="80">
                        <circle cx="40" cy="40" r="34" fill="none" stroke="#10B981" stroke-width="3" opacity="0.3"/>
                        <circle cx="40" cy="40" r="34" fill="none" stroke="#10B981" stroke-width="3"
                                stroke-dasharray="214" stroke-dashoffset="214" class="sf-success-ring"/>
                        <path d="M24 40 L36 52 L58 30" fill="none" stroke="#10B981" stroke-width="4"
                              stroke-linecap="round" stroke-linejoin="round"
                              stroke-dasharray="50" stroke-dashoffset="50" class="sf-success-check"/>
                    </svg>
                </div>

                <h2 class="sf-success-title">Request submitted!</h2>
                <p class="sf-success-sub">We've sent it to the admin for review.</p>

                <div class="sf-success-card">
                    <div class="sf-success-row">
                        <span>Request ID</span>
                        <span class="sf-id-chip">
                            ${escapeHtml(reqId)}
                            <button type="button" class="sf-copy" data-copy="${escapeHtml(reqId)}" title="Copy">
                                <i class="fas fa-copy"></i>
                            </button>
                        </span>
                    </div>
                    <div class="sf-success-row">
                        <span>Plan</span>
                        <span><strong>${meta.icon} ${meta.name}</strong></span>
                    </div>
                    <div class="sf-success-row">
                        <span>Duration</span>
                        <span>${durationLabel}</span>
                    </div>
                    <div class="sf-success-row sf-success-total">
                        <span>Amount</span>
                        <span>${money(state.finalPrice)}</span>
                    </div>
                </div>

                <a href="${waUrl}" target="_blank" rel="noopener" class="sf-wa">
                    <i class="fab fa-whatsapp"></i> Notify Admin on WhatsApp
                </a>

                <button type="button" class="sf-close-btn" data-close>
                    Close
                </button>
            </div>
        `;
    }

    // ============================================
    // BIND STEP EVENTS
    // ============================================
    function bindStep() {
        // Plan selection
        content.querySelectorAll('.sf-plan').forEach(el => {
            el.addEventListener('click', () => {
                state.tier = el.dataset.tier;
                state.duration = 'yearly';
                state.discountAmount = 0;
                state.discountCode = null;
                updatePrices();
                state.step = 2;
                renderStep();
            });
        });

        // Back buttons
        content.querySelectorAll('.sf-back').forEach(el => {
            el.addEventListener('click', () => {
                const back = parseInt(el.dataset.back, 10);
                if (!isNaN(back)) {
                    state.step = back;
                    renderStep();
                }
            });
        });

        // Duration picker
        content.querySelectorAll('.sf-dur').forEach(el => {
            el.addEventListener('click', () => {
                state.duration = el.dataset.dur;
                updatePrices();

                // Update UI without a full re-render (keeps the slide smooth)
                content.querySelectorAll('.sf-dur').forEach(x => x.classList.remove('active'));
                el.classList.add('active');

                // Update the sticky footer price
                const amountEl = content.querySelector('.sf-footer-amount[data-amount]');
                if (amountEl) amountEl.textContent = money(PRICES[state.tier][state.duration]);
            });
        });

        // Continue
        content.querySelectorAll('[data-next]').forEach(el => {
            el.addEventListener('click', () => {
                state.step = parseInt(el.dataset.next, 10) || 2;
                renderStep();
            });
        });

        // Discount toggle
        const toggle = content.querySelector('[data-toggle-discount]');
        if (toggle) {
            toggle.addEventListener('click', () => {
                const body = toggle.nextElementSibling;
                const open = !body.hidden;
                body.hidden = open;
                toggle.classList.toggle('open', !open);
            });
        }

        // Apply discount
        const applyBtn = content.querySelector('[data-apply-discount]');
        if (applyBtn) {
            applyBtn.addEventListener('click', () => {
                const input = content.querySelector('[data-discount-input]');
                const code = (input ? input.value : '').trim();
                const fb = content.querySelector('[data-discount-feedback]');
                if (!code) {
                    if (fb) { fb.textContent = 'Please enter a code.'; fb.className = 'sf-discount-feedback err'; }
                    return;
                }
                applyBtn.disabled = true;
                applyBtn.textContent = '…';
                if (fb) { fb.textContent = 'Checking…'; fb.className = 'sf-discount-feedback'; }

                fetch('/upgrade/api/validate-discount', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': getCsrfToken()
                    },
                    body: JSON.stringify({
                        code: code,
                        tier: state.tier,
                        duration: state.duration
                    })
                })
                .then(r => r.json())
                .then(data => {
                    applyBtn.disabled = false;
                    applyBtn.textContent = 'Apply';

                    if (data.valid) {
                        state.discountCode = code;
                        state.discountAmount = data.discount_amount || 0;
                        state.finalPrice = data.final_price;

                        if (fb) {
                            fb.textContent = '✅ ' + (data.message || 'Applied');
                            fb.className = 'sf-discount-feedback ok';
                        }

                        // Update summary numbers
                        const totalEl = content.querySelector('[data-total]');
                        if (totalEl) totalEl.textContent = money(state.finalPrice);
                        const footerEl = content.querySelector('.sf-footer-amount[data-amount]');
                        if (footerEl) footerEl.textContent = money(state.finalPrice);
                        const discRow = content.querySelector('.sf-discount-row');
                        const discText = content.querySelector('[data-discount-text]');
                        if (discRow) discRow.style.display = '';
                        if (discText) discText.textContent = '-' + money(state.discountAmount);
                    } else {
                        if (fb) {
                            fb.textContent = '❌ ' + (data.message || 'Invalid code');
                            fb.className = 'sf-discount-feedback err';
                        }
                    }
                })
                .catch(() => {
                    applyBtn.disabled = false;
                    applyBtn.textContent = 'Apply';
                    if (fb) { fb.textContent = '❌ Network error. Try again.'; fb.className = 'sf-discount-feedback err'; }
                });
            });
        }

        // Submit
        const submitBtn = content.querySelector('[data-submit]');
        if (submitBtn) {
            submitBtn.addEventListener('click', () => {
                if (submitBtn.disabled) return;
                submitBtn.disabled = true;
                const original = submitBtn.innerHTML;
                submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Submitting…';

                fetch('/upgrade/api/request', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': getCsrfToken()
                    },
                    body: JSON.stringify({
                        tier: state.tier,
                        duration: state.duration,
                        discount_code: state.discountCode || null,
                        note: state.note || ''
                    })
                })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        state.requestId = data.request_id;
                        state.step = 4;
                        renderStep();
                    } else {
                        submitBtn.disabled = false;
                        submitBtn.innerHTML = original;
                        alert('Error: ' + (data.message || 'Could not submit. Please try again.'));
                    }
                })
                .catch(() => {
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = original;
                    alert('Network error. Please try again.');
                });
            });
        }

        // Copy on success
        content.querySelectorAll('[data-copy]').forEach(el => {
            el.addEventListener('click', () => {
                const text = el.dataset.copy || '';
                if (!text) return;
                const done = () => {
                    el.innerHTML = '<i class="fas fa-check"></i>';
                    el.classList.add('copied');
                    setTimeout(() => {
                        el.innerHTML = '<i class="fas fa-copy"></i>';
                        el.classList.remove('copied');
                    }, 1400);
                };
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
                } else {
                    fallbackCopy(text, done);
                }
            });
        });

        // Close on success button
        content.querySelectorAll('[data-close]').forEach(el => {
            el.addEventListener('click', closeSheet);
        });

        // Animate success ring + check
        if (state.step === 4) {
            setTimeout(() => {
                const ring = content.querySelector('.sf-success-ring');
                const check = content.querySelector('.sf-success-check');
                if (ring) ring.style.strokeDashoffset = '0';
                if (check) check.style.strokeDashoffset = '0';
            }, 60);
        }
    }

    function fallbackCopy(text, cb) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); cb && cb(); } catch (e) {}
        document.body.removeChild(ta);
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