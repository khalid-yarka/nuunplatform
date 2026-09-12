// static/js/focus.js
// Focus page charts (subject misses + accuracy over time).

(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        const el = document.getElementById('focusData');
        if (!el || typeof Chart === 'undefined') return;

        let payload;
        try {
            payload = JSON.parse(el.textContent);
        } catch (e) {
            console.warn('Focus: invalid chart payload');
            return;
        }

        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        const textColor = isDark ? '#94A3B8' : '#64748B';
        const gridColor = isDark ? '#1E293B' : '#E2E8F0';

        // ---------- Accuracy line ----------
        const accCanvas = document.getElementById('focusAccuracyChart');
        if (accCanvas && payload.accuracy && payload.accuracy.labels?.length) {
            const prettyLabels = payload.accuracy.labels.map(function (d) {
                try {
                    const dt = new Date(d + 'T00:00:00');
                    if (isNaN(dt.getTime())) return d;
                    return dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
                } catch (e) { return d; }
            });

            new Chart(accCanvas.getContext('2d'), {
                type: 'line',
                data: {
                    labels: prettyLabels,
                    datasets: [{
                        label: 'Accuracy %',
                        data: payload.accuracy.values,
                        borderColor: '#FF3138',
                        backgroundColor: 'rgba(255, 49, 56, 0.10)',
                        fill: true,
                        tension: 0.35,
                        pointBackgroundColor: '#FF3138',
                        pointBorderColor: '#FFFFFF',
                        pointBorderWidth: 2,
                        pointRadius: payload.accuracy.values.length > 20 ? 0 : 4,
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
                                label: function (ctx) {
                                    return ctx.parsed.y + '% correct';
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            min: 0, max: 100,
                            ticks: {
                                color: textColor, font: { size: 10 },
                                stepSize: 25,
                                callback: function (v) { return v + '%'; }
                            },
                            grid: { color: gridColor, drawBorder: false }
                        },
                        x: {
                            ticks: {
                                color: textColor, font: { size: 10 },
                                maxRotation: 45, minRotation: 0,
                                autoSkip: true, maxTicksLimit: 8
                            },
                            grid: { display: false }
                        }
                    },
                    interaction: { intersect: false, mode: 'index' }
                }
            });
        }

        // ---------- Miss count by subject (horizontal bar) ----------
        const missCanvas = document.getElementById('focusMissChart');
        if (missCanvas && Array.isArray(payload.miss_by_subject) && payload.miss_by_subject.length) {
            const labels = payload.miss_by_subject.map(function (x) { return x.subject_name; });
            const values = payload.miss_by_subject.map(function (x) { return x.miss_count; });

            new Chart(missCanvas.getContext('2d'), {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Missed questions',
                        data: values,
                        backgroundColor: 'rgba(255, 49, 56, 0.75)',
                        borderColor: '#FF3138',
                        borderWidth: 1,
                        borderRadius: 6,
                        maxBarThickness: 32
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
                                label: function (ctx) {
                                    return ctx.parsed.y + ' missed';
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                color: textColor, font: { size: 10 },
                                precision: 0
                            },
                            grid: { color: gridColor, drawBorder: false }
                        },
                        x: {
                            ticks: {
                                color: textColor, font: { size: 10 },
                                maxRotation: 45, minRotation: 0,
                                autoSkip: false
                            },
                            grid: { display: false }
                        }
                    }
                }
            });
        }
    });
})();