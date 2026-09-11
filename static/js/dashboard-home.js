// static/js/dashboard-home.js
// Home dashboard interactions (performance chart).

(function() {
    'use strict';

    document.addEventListener('DOMContentLoaded', function() {
        const el = document.getElementById('dhData');
        const canvas = document.getElementById('dhPerformanceChart');
        if (!el || !canvas || typeof Chart === 'undefined') return;

        let payload;
        try {
            payload = JSON.parse(el.textContent);
        } catch (e) {
            console.warn('Dashboard chart: invalid payload');
            return;
        }

        const labels = payload.labels || [];
        const data = payload.data || [];
        if (!labels.length || !data.length) return;

        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        const textColor = isDark ? '#94A3B8' : '#64748B';
        const gridColor = isDark ? '#1E293B' : '#E2E8F0';

        // Format labels: "2026-09-10" -> "Sep 10"
        const prettyLabels = labels.map(function(d) {
            if (!d) return '';
            try {
                const dt = new Date(d + 'T00:00:00');
                if (isNaN(dt.getTime())) return d;
                return dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
            } catch (e) {
                return d;
            }
        });

        new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: {
                labels: prettyLabels,
                datasets: [{
                    label: 'Score %',
                    data: data,
                    borderColor: '#FF3138',
                    backgroundColor: 'rgba(255, 49, 56, 0.10)',
                    fill: true,
                    tension: 0.35,
                    pointBackgroundColor: '#FF3138',
                    pointBorderColor: '#FFFFFF',
                    pointBorderWidth: 2,
                    pointRadius: data.length > 20 ? 0 : 4,
                    pointHoverRadius: 6,
                    borderWidth: 2,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#1A1A2E',
                        titleColor: '#FFFFFF',
                        bodyColor: '#E5E7EB',
                        padding: 10,
                        cornerRadius: 6,
                        displayColors: false,
                        callbacks: {
                            label: function(ctx) {
                                return ctx.parsed.y + '% correct';
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        min: 0,
                        max: 100,
                        ticks: {
                            color: textColor,
                            font: { size: 10 },
                            stepSize: 25,
                            callback: function(v) { return v + '%'; }
                        },
                        grid: {
                            color: gridColor,
                            drawBorder: false,
                        }
                    },
                    x: {
                        ticks: {
                            color: textColor,
                            font: { size: 10 },
                            maxRotation: 45,
                            minRotation: 0,
                            autoSkip: true,
                            maxTicksLimit: 8,
                        },
                        grid: { display: false }
                    }
                },
                interaction: {
                    intersect: false,
                    mode: 'index'
                }
            }
        });
    });
})();