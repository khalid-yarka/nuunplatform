/* ============================================================
   static/js/admin/overview.js
   Admin Control Centre interactions:
   - Client-side greeting (time-aware)
   - Inline SVG sparklines
   - Chart.js trend + donut
   - ⌘K / Ctrl+K global search modal
   - Collapsible command centre state persistence
   - Live ribbon refresh
   ============================================================ */

(function () {
    'use strict';

    // ------------------------------------------------------------
    // 1. GREETING — time-aware
    // ------------------------------------------------------------
    function initGreeting() {
        const el = document.getElementById('ccGreetingTime');
        if (!el) return;
        const hour = new Date().getHours();
        let text = 'Good evening';
        if (hour < 12) text = 'Good morning';
        else if (hour < 17) text = 'Good afternoon';
        el.textContent = text;
    }

    // ------------------------------------------------------------
    // 2. SPARKLINES — inline SVG, no library
    // ------------------------------------------------------------
    function renderSparkline(el, values) {
        if (!Array.isArray(values) || values.length < 2) {
            el.innerHTML = '';
            return;
        }

        const W = 100, H = 30, PAD = 2;
        const min = Math.min(...values);
        const max = Math.max(...values);
        const range = (max - min) || 1;
        const stepX = (W - PAD * 2) / (values.length - 1);

        const points = values.map(function (v, i) {
            const x = PAD + i * stepX;
            const y = H - PAD - ((v - min) / range) * (H - PAD * 2);
            return [x, y];
        });

        // Build smooth path
        let path = 'M ' + points[0][0] + ',' + points[0][1];
        for (let i = 1; i < points.length; i++) {
            const [x0, y0] = points[i - 1];
            const [x1, y1] = points[i];
            const cx = (x0 + x1) / 2;
            path += ' C ' + cx + ',' + y0 + ' ' + cx + ',' + y1 + ' ' + x1 + ',' + y1;
        }

        const area = path +
            ' L ' + points[points.length - 1][0] + ',' + H +
            ' L ' + points[0][0] + ',' + H + ' Z';

        const gradId = 'spark-' + Math.random().toString(36).slice(2, 8);

        el.innerHTML =
            '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">' +
                '<defs>' +
                    '<linearGradient id="' + gradId + '" x1="0" y1="0" x2="0" y2="1">' +
                        '<stop offset="0%" stop-color="var(--primary)" stop-opacity="0.35"/>' +
                        '<stop offset="100%" stop-color="var(--primary)" stop-opacity="0"/>' +
                    '</linearGradient>' +
                '</defs>' +
                '<path d="' + area + '" fill="url(#' + gradId + ')"/>' +
                '<path d="' + path + '" fill="none" ' +
                      'stroke="var(--primary)" stroke-width="1.8" ' +
                      'stroke-linecap="round" stroke-linejoin="round" ' +
                      'vector-effect="non-scaling-stroke"/>' +
            '</svg>';
    }

    function initSparklines() {
        document.querySelectorAll('.cc-kpi__spark').forEach(function (el) {
            let values = [];
            try {
                values = JSON.parse(el.dataset.spark || '[]');
            } catch (e) { values = []; }
            renderSparkline(el, values);
        });
    }

    // ------------------------------------------------------------
    // 3. CHARTS
    // ------------------------------------------------------------
    function initCharts() {
        const dataEl = document.getElementById('ccChartData');
        if (!dataEl || typeof Chart === 'undefined') return;

        let data;
        try { data = JSON.parse(dataEl.textContent); }
        catch (e) { return; }

        // ---- Trend chart ----
        const trendCanvas = document.getElementById('ccTrendChart');
        if (trendCanvas && data.series) {
            new Chart(trendCanvas, {
                type: 'line',
                data: {
                    labels: data.series.labels,
                    datasets: [
                        {
                            label: 'Signups',
                            data: data.series.signups,
                            borderColor: '#3B82F6',
                            backgroundColor: 'rgba(59,130,246,0.12)',
                            fill: true,
                            tension: 0.35,
                            borderWidth: 2,
                            pointRadius: 0,
                            pointHoverRadius: 5,
                        },
                        {
                            label: 'Quizzes',
                            data: data.series.quizzes,
                            borderColor: '#10B981',
                            backgroundColor: 'rgba(16,185,129,0.12)',
                            fill: true,
                            tension: 0.35,
                            borderWidth: 2,
                            pointRadius: 0,
                            pointHoverRadius: 5,
                        },
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { intersect: false, mode: 'index' },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: 'rgba(20,20,30,0.95)',
                            padding: 10,
                            cornerRadius: 8,
                            titleFont: { size: 12, weight: '700' },
                            bodyFont: { size: 12 },
                        }
                    },
                    scales: {
                        x: {
                            grid: { display: false },
                            ticks: {
                                color: getComputedStyle(document.documentElement)
                                       .getPropertyValue('--text-muted').trim() || '#9CA3AF',
                                font: { size: 11 },
                                maxRotation: 0,
                                autoSkip: true,
                                maxTicksLimit: 8,
                            }
                        },
                        y: {
                            beginAtZero: true,
                            grid: { color: 'rgba(0,0,0,0.05)' },
                            ticks: {
                                color: getComputedStyle(document.documentElement)
                                       .getPropertyValue('--text-muted').trim() || '#9CA3AF',
                                font: { size: 11 },
                                precision: 0,
                            }
                        }
                    }
                }
            });
        }

        // ---- Tier donut ----
        const tierCanvas = document.getElementById('ccTierChart');
        if (tierCanvas && data.tiers) {
            const colors = ['#9CA3AF', '#FF3138', '#8B5CF6'];
            new Chart(tierCanvas, {
                type: 'doughnut',
                data: {
                    labels: ['Free', 'Premium', 'Pro'],
                    datasets: [{
                        data: [data.tiers.free, data.tiers.premium, data.tiers.pro],
                        backgroundColor: colors,
                        borderWidth: 0,
                        hoverOffset: 6,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '68%',
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: 'rgba(20,20,30,0.95)',
                            padding: 10,
                            cornerRadius: 8,
                        }
                    }
                }
            });

            // Custom legend below
            const legend = document.getElementById('ccTierLegend');
            if (legend) {
                const total = (data.tiers.free + data.tiers.premium + data.tiers.pro) || 1;
                legend.innerHTML = ['Free', 'Premium', 'Pro'].map(function (label, i) {
                    const val = [data.tiers.free, data.tiers.premium, data.tiers.pro][i];
                    const pct = Math.round(100 * val / total);
                    return '<div class="cc-donut-legend__item">' +
                        '<span class="cc-donut-legend__dot" style="background:' + colors[i] + '"></span>' +
                        '<span>' + label + '</span>' +
                        '<span class="cc-donut-legend__count">' + val + ' · ' + pct + '%</span>' +
                    '</div>';
                }).join('');
            }
        }
    }

    // ------------------------------------------------------------
    // 4. ⌘K SEARCH MODAL
    // ------------------------------------------------------------
    function initSearchModal() {
        const modal = document.getElementById('ccSearchModal');
        const trigger = document.getElementById('ccSearchTrigger');
        const input = document.getElementById('ccSearchInput');
        if (!modal) return;

        function open() {
            modal.classList.add('is-open');
            if (input) {
                setTimeout(function () { input.focus(); input.select(); }, 30);
            }
        }
        function close() {
            modal.classList.remove('is-open');
        }

        if (trigger) trigger.addEventListener('click', open);

        modal.addEventListener('click', function (e) {
            if (e.target.dataset && e.target.dataset.close === '1') close();
        });

        document.addEventListener('keydown', function (e) {
            // ⌘K / Ctrl+K
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
                e.preventDefault();
                modal.classList.contains('is-open') ? close() : open();
            }
            if (e.key === 'Escape' && modal.classList.contains('is-open')) {
                close();
            }
        });
    }

    // ------------------------------------------------------------
    // 5. COMMAND CENTRE COLLAPSE STATE
    // ------------------------------------------------------------
    function initCommandCentrePersistence() {
        const KEY = 'nuun-cc-open-sections';
        let open = [];
        try { open = JSON.parse(localStorage.getItem(KEY) || '[]'); }
        catch (e) { open = []; }

        document.querySelectorAll('.cc-collapse[data-cc-key]').forEach(function (el) {
            const key = el.getAttribute('data-cc-key');
            if (open.indexOf(key) >= 0) el.setAttribute('open', '');
            el.addEventListener('toggle', function () {
                const current = [];
                document.querySelectorAll('.cc-collapse[data-cc-key]').forEach(function (d) {
                    if (d.open) current.push(d.getAttribute('data-cc-key'));
                });
                try { localStorage.setItem(KEY, JSON.stringify(current)); }
                catch (e) {}
            });
        });
    }

    // ------------------------------------------------------------
    // 6. LIVE RIBBON REFRESH — polls /health for uptime + db
    // ------------------------------------------------------------
    function initLiveRefresh() {
        const refreshEl = document.getElementById('ccRibbonRefresh');
        if (!refreshEl) return;

        setInterval(function () {
            refreshEl.style.opacity = '0.4';
            fetch('/health', { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    if (!data) return;
                    // Only update errors today (cheap; other values rarely change)
                    const errEl = document.querySelector('[data-ribbon="errors"] .cc-ribbon__value');
                    if (errEl && data.components && data.components.errors) {
                        errEl.textContent = data.components.errors.unresolved || 0;
                    }
                })
                .catch(function () {})
                .finally(function () {
                    refreshEl.style.opacity = '';
                });
        }, 30000);
    }

    // ------------------------------------------------------------
    // BOOT
    // ------------------------------------------------------------
    function boot() {
        initGreeting();
        initSparklines();
        initCharts();
        initSearchModal();
        initCommandCentrePersistence();
        initLiveRefresh();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();