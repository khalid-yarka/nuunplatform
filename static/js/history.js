// static/js/history.js
// Tier-aware history page rendering.

(function() {
    'use strict';

    const cfg = window.historyConfig || {};

    let currentPage = 1;
    let perPage = 20;
    let loading = false;
    let hasMore = true;

    const timelineList     = document.getElementById('timelineList');
    const loadMoreWrap     = document.getElementById('loadMoreWrap');
    const loadMoreBtn      = document.getElementById('loadMoreBtn');
    const filterType       = document.getElementById('filterType');
    const filterStartDate  = document.getElementById('filterStartDate');
    const filterEndDate    = document.getElementById('filterEndDate');
    const filterSearch     = document.getElementById('filterSearch');
    const clearFilters     = document.getElementById('clearFilters');
    const exportBtn        = document.getElementById('exportBtn');

    // ============================================
    // ICON MAP
    // ============================================
    const ICONS = {
        quiz_attempt:   '📝',
        live_quiz:      '⚡',
        achievement:    '🏆',
        save:           '🔖',
        pdf_view:       '📄',
        pdf_download:   '⬇️',
        like:           '❤️',
        report:         '⚠️',
    };

    // ============================================
    // HELPERS
    // ============================================
    function escapeHtml(text) {
        if (text === null || text === undefined) return '';
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function parseMetadata(raw) {
        if (!raw) return {};
        try {
            return typeof raw === 'string' ? JSON.parse(raw) : raw;
        } catch (e) {
            return {};
        }
    }

    function formatDateHeader(dateStr) {
        if (!dateStr || dateStr === 'Unknown') return 'Unknown date';
        const today = new Date();
        const todayIso = today.toISOString().split('T')[0];
        const yesterday = new Date(Date.now() - 86400000).toISOString().split('T')[0];

        if (dateStr === todayIso) return '📅 Today';
        if (dateStr === yesterday) return '📅 Yesterday';

        // Relative for the current week
        try {
            const d = new Date(dateStr + 'T00:00:00');
            const diffDays = Math.floor((today - d) / 86400000);
            if (diffDays >= 2 && diffDays <= 6) {
                return '📅 ' + d.toLocaleDateString(undefined, { weekday: 'long' });
            }
            return '📅 ' + d.toLocaleDateString(undefined, {
                year: 'numeric', month: 'short', day: 'numeric'
            });
        } catch (e) {
            return dateStr;
        }
    }

    function formatEntryTime(isoStr) {
        if (!isoStr) return '';
        try {
            const d = new Date(isoStr.replace(' ', 'T'));
            if (isNaN(d.getTime())) return '';
            const hh = String(d.getHours()).padStart(2, '0');
            const mm = String(d.getMinutes()).padStart(2, '0');
            return hh + ':' + mm;
        } catch (e) {
            return '';
        }
    }

    function scoreBadgeClass(pct) {
        if (pct >= 70) return 'good';
        if (pct >= 40) return 'mid';
        return 'low';
    }

    // ============================================
    // ENTRY RENDERING
    // ============================================
    function renderEntry(entry) {
        if (!entry) return '';

        const type = entry.entry_type || 'unknown';
        const action = entry.action || '';
        const icon = ICONS[type] || '📌';
        const meta = parseMetadata(entry.metadata);
        const time = formatEntryTime(entry.created_at);

        let title = 'Unknown event';
        let detailsHtml = '';

        // ---------- Quiz attempt ----------
        if (type === 'quiz_attempt') {
            const subject = meta.subject || 'Unknown Subject';
            const score = meta.score ?? 0;
            const total = meta.total ?? 0;
            const pct = meta.percentage ?? 0;
            const cls = scoreBadgeClass(pct);
            title = 'Completed a <span class="highlight">' + escapeHtml(subject) + '</span> quiz';
            detailsHtml =
                '<span class="meta-item">' +
                    '<span class="meta-badge ' + cls + '">' + escapeHtml(String(score)) + '/' + escapeHtml(String(total)) + ' · ' + escapeHtml(String(pct)) + '%</span>' +
                '</span>' +
                '<span class="meta-item">📝 Practice</span>';
        }

        // ---------- Live quiz ----------
        else if (type === 'live_quiz') {
            const subject = meta.subject || 'Live Quiz';
            const score = meta.score ?? 0;
            const rank = meta.rank ?? null;
            const total = meta.total_questions ?? null;
            title = 'Played a <span class="highlight">' + escapeHtml(subject) + '</span> live quiz';
            detailsHtml =
                '<span class="meta-item">' +
                    '<span class="meta-badge good">' + escapeHtml(String(score)) + ' pts</span>' +
                '</span>';
            if (rank) {
                detailsHtml += '<span class="meta-item">🏅 Rank #' + escapeHtml(String(rank)) + '</span>';
            }
            if (total) {
                detailsHtml += '<span class="meta-item">⚡ ' + escapeHtml(String(total)) + ' questions</span>';
            }
        }

        // ---------- Achievement ----------
        else if (type === 'achievement') {
            const name = meta.name || 'Achievement';
            const emoji = meta.icon || '🏆';
            title = 'Unlocked <span class="highlight">' + escapeHtml(name) + '</span>';
            detailsHtml = '<span class="meta-item">' + escapeHtml(emoji) + ' Achievement</span>';
        }

        // ---------- Save ----------
        else if (type === 'save') {
            const contentType = meta.content_type || 'item';
            const cid = meta.content_id || '';
            title = action === 'unsaved'
                ? 'Removed a saved item'
                : 'Saved a <span class="highlight">' + escapeHtml(contentType) + '</span>';
            if (cid) {
                detailsHtml = '<span class="meta-item">🔖 #' + escapeHtml(String(cid)) + '</span>';
            }
        }

        // ---------- PDF ----------
        else if (type === 'pdf_view' || type === 'pdf_download') {
            const pdfTitle = meta.title || 'PDF';
            const subject = meta.subject || '';
            const isDownload = type === 'pdf_download';
            title = (isDownload ? 'Downloaded ' : 'Viewed ') +
                    '<span class="highlight">' + escapeHtml(pdfTitle) + '</span>';
            if (subject) {
                detailsHtml = '<span class="meta-item">📚 ' + escapeHtml(subject) + '</span>';
            }
        }

        // ---------- Like ----------
        else if (type === 'like') {
            const liked = action === 'liked';
            title = liked ? 'Liked a question' : 'Unliked a question';
            detailsHtml = '<span class="meta-item">' + (liked ? '❤️' : '💔') + '</span>';
        }

        // ---------- Report ----------
        else if (type === 'report') {
            const reason = meta.reason || 'Issue';
            title = 'Reported a question';
            detailsHtml = '<span class="meta-item"><span class="meta-badge low">' + escapeHtml(reason) + '</span></span>';
        }

        // ---------- Fallback ----------
        else {
            title = action
                ? escapeHtml(action.charAt(0).toUpperCase() + action.slice(1)) + ' ' + escapeHtml(type)
                : escapeHtml(type);
        }

        return (
            '<div class="hist-entry ' + escapeHtml(type) + '">' +
                '<div class="entry-icon">' + icon + '</div>' +
                '<div class="entry-body">' +
                    '<div class="entry-title">' + title + '</div>' +
                    (detailsHtml ? '<div class="entry-meta">' + detailsHtml + '</div>' : '') +
                '</div>' +
                (time ? '<div class="entry-time">' + escapeHtml(time) + '</div>' : '') +
            '</div>'
        );
    }

    // ============================================
    // PARAMS
    // ============================================
    function buildParams() {
        const params = new URLSearchParams();
        if (filterType && filterType.value) params.set('types', filterType.value);
        if (filterStartDate && filterStartDate.value) params.set('start_date', filterStartDate.value);
        if (filterEndDate && filterEndDate.value) params.set('end_date', filterEndDate.value);
        if (filterSearch && filterSearch.value && cfg.can_search) {
            params.set('search', filterSearch.value.trim());
        }
        return params;
    }

    function hasAnyFilter() {
        return (
            (filterType && filterType.value) ||
            (filterStartDate && filterStartDate.value) ||
            (filterEndDate && filterEndDate.value) ||
            (filterSearch && filterSearch.value && cfg.can_search)
        );
    }

    function updateClearButton() {
        if (!clearFilters) return;
        clearFilters.style.display = hasAnyFilter() ? 'inline-flex' : 'none';
    }

    // ============================================
    // LOAD ENTRIES
    // ============================================
    function loadEntries(reset) {
        if (loading) return;
        loading = true;
        if (reset) {
            currentPage = 1;
            hasMore = true;
        }

        const params = buildParams();
        params.set('page', currentPage);
        params.set('per_page', perPage);

        fetch('/history/api/entries?' + params.toString(), {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(r => {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(data => {
            loading = false;
            if (data.error) {
                showEmptyState('error', data.error);
                return;
            }

            const entries = data.entries || [];
            const pagination = data.pagination || {};
            const total = pagination.total || 0;
            const totalPages = pagination.pages || 1;

            if (reset && timelineList) {
                timelineList.innerHTML = '';
            }

            if (entries.length === 0 && reset) {
                showEmptyState(hasAnyFilter() ? 'filtered' : 'no-data');
                if (loadMoreWrap) loadMoreWrap.style.display = 'none';
                hasMore = false;
                return;
            }

            // Group by date
            const groups = {};
            const order = [];
            entries.forEach(e => {
                let dateStr = 'Unknown';
                if (e.created_at) {
                    try {
                        dateStr = e.created_at.split('T')[0].split(' ')[0];
                    } catch (err) {
                        dateStr = 'Unknown';
                    }
                }
                if (!groups[dateStr]) {
                    groups[dateStr] = [];
                    order.push(dateStr);
                }
                groups[dateStr].push(e);
            });

            let html = '';
            order.forEach(date => {
                const items = groups[date];
                html +=
                    '<div class="hist-day-group">' +
                        '<div class="hist-day-header">' +
                            '<span class="day-dot"></span>' +
                            '<span class="day-label">' + formatDateHeader(date) + '</span>' +
                            '<span class="day-count">' + items.length + ' ' + (items.length === 1 ? 'entry' : 'entries') + '</span>' +
                        '</div>';
                items.forEach(item => {
                    html += renderEntry(item);
                });
                html += '</div>';
            });

            if (reset && timelineList) {
                timelineList.innerHTML = html;
            } else if (timelineList) {
                timelineList.insertAdjacentHTML('beforeend', html);
            }

            if (currentPage < totalPages) {
                if (loadMoreWrap) loadMoreWrap.style.display = 'block';
                hasMore = true;
            } else {
                if (loadMoreWrap) loadMoreWrap.style.display = 'none';
                hasMore = false;
            }
            currentPage += 1;
        })
        .catch(err => {
            console.error('History fetch error:', err);
            loading = false;
            showEmptyState('error', 'Failed to load history. Please try again.');
        });
    }

    // ============================================
    // EMPTY STATE
    // ============================================
    function showEmptyState(kind, message) {
        if (!timelineList) return;

        let icon = '📭';
        let title = 'No history yet';
        let text = message || 'Start learning to build your history timeline!';
        let actionsHtml = '';

        if (kind === 'filtered') {
            icon = '🔍';
            title = 'No matching entries';
            text = 'No history entries match your current filters.';
            actionsHtml =
                '<div class="empty-actions">' +
                    '<button type="button" class="btn btn-primary" onclick="document.getElementById(\'clearFilters\').click()">' +
                        '<i class="fas fa-times"></i> Clear Filters' +
                    '</button>' +
                '</div>';
        } else if (kind === 'error') {
            icon = '⚠️';
            title = 'Something went wrong';
            actionsHtml =
                '<div class="empty-actions">' +
                    '<button type="button" class="btn btn-primary" onclick="location.reload()">' +
                        '<i class="fas fa-redo"></i> Retry' +
                    '</button>' +
                '</div>';
        } else {
            actionsHtml =
                '<div class="empty-actions">' +
                    '<a href="/quiz" class="btn btn-primary">' +
                        '<i class="fas fa-rocket"></i> Take a Quiz' +
                    '</a>' +
                    '<a href="/live-quiz/lobby" class="btn btn-secondary">' +
                        '<i class="fas fa-bolt"></i> Join Live Quiz' +
                    '</a>' +
                '</div>';
        }

        timelineList.innerHTML =
            '<div class="hist-empty">' +
                '<span class="empty-icon">' + icon + '</span>' +
                '<h3>' + title + '</h3>' +
                '<p>' + text + '</p>' +
                actionsHtml +
            '</div>';
    }

    // ============================================
    // STATS
    // ============================================
    function fetchStats() {
        fetch('/history/api/stats')
        .then(r => r.ok ? r.json() : null)
        .then(data => {
            if (!data) return;
            setText('statTotal', data.total ?? 0);
            setText('statQuizzes', data.quizzes ?? 0);
            setText('statAchievements', data.achievements ?? 0);
            setText('statSaves', data.saves ?? 0);

            const pdfTotal = (data.pdf_views ?? 0) + (data.pdf_downloads ?? 0);
            setText('statPdfs', pdfTotal);

            setText('statAvgScore', (data.avg_score ?? 0) + '%');
        })
        .catch(() => {});
    }

    function setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    // ============================================
    // EXPORT
    // ============================================
    function setupExport() {
        if (!exportBtn) return;
        exportBtn.addEventListener('click', function() {
            const params = buildParams();
            const url = '/history/api/export?' + params.toString();
            // Trigger download via hidden anchor
            const a = document.createElement('a');
            a.href = url;
            a.style.display = 'none';
            document.body.appendChild(a);
            a.click();
            setTimeout(() => document.body.removeChild(a), 100);
        });
    }

    // ============================================
    // FILTERS
    // ============================================
    let debounceTimer;
    function applyFilters() {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            updateClearButton();
            loadEntries(true);
        }, 400);
    }

    function bindFilters() {
        if (filterType) filterType.addEventListener('change', applyFilters);
        if (filterStartDate) filterStartDate.addEventListener('change', applyFilters);
        if (filterEndDate) filterEndDate.addEventListener('change', applyFilters);
        if (filterSearch && cfg.can_search) {
            filterSearch.addEventListener('input', applyFilters);
        }
        if (clearFilters) {
            clearFilters.addEventListener('click', () => {
                if (filterType) filterType.value = '';
                if (filterStartDate) filterStartDate.value = '';
                if (filterEndDate) filterEndDate.value = '';
                if (filterSearch) filterSearch.value = '';
                updateClearButton();
                loadEntries(true);
            });
        }
    }

    // ============================================
    // LOAD MORE
    // ============================================
    function setupLoadMore() {
        if (!loadMoreBtn) return;
        loadMoreBtn.addEventListener('click', () => {
            if (!hasMore || loading) return;
            loadMoreBtn.disabled = true;
            loadMoreBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
            loadEntries(false);
            // Re-enable after fetch (loading is checked inside)
            setTimeout(() => {
                if (!loading) {
                    loadMoreBtn.disabled = false;
                    loadMoreBtn.innerHTML = '<i class="fas fa-chevron-down"></i> Load More';
                }
            }, 600);
        });
    }

    // ============================================
    // INIT
    // ============================================
    document.addEventListener('DOMContentLoaded', function() {
        fetchStats();
        loadEntries(true);
        bindFilters();
        setupLoadMore();
        setupExport();
        updateClearButton();
    });
})();