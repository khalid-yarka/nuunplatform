/* ============================================================
   static/js/live_quiz/lobby.js
   ============================================================
   Extracted from templates/dashboard/live_quiz/lobby.html.
   Bridge objects expected on window:
     window.__LQ_I18N   — translations
     window.__LQ        — currently unused; reserved
   ============================================================ */

const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

const I18N = window.__LQ_I18N;

// Grade-locked prompt — opens the platform upgrade sheet.
// Free users who select a grade they don't own see this.
window.showGradeLocked = function(requiredGrade) {
    var msg = requiredGrade
        ? 'This competition is for ' + requiredGrade + ' grade.'
        : 'This competition is for a different grade.';
    if (typeof window.openUpgradeSheet === 'function') {
        window.openUpgradeSheet({
            feature: 'live_quiz_grade',
            requiredTier: 'premium',
            message: msg
        });
    } else {
        window.location.href = '/upgrade/?feature=live_quiz_grade&tier=premium';
    }
};

document.addEventListener('DOMContentLoaded', function() {
    const searchInput = document.getElementById('searchInput');
    const statusFilter = document.getElementById('statusFilter');
    const subjectFilter = document.getElementById('subjectFilter');
    const quizListContainer = document.getElementById('quizListContainer');

    let debounceTimer;
    let autoRefreshInterval = null;
    let currentPage = 1;

    function applyFilters() {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(function() { refreshLobby(1); }, 500);
    }

    function updateClearButtonVisibility() {
        const btn = document.getElementById('clearFiltersBtn');
        if (!btn) return;
        const hasFilter = (searchInput.value.trim() !== '')
                       || (statusFilter.value !== '')
                       || (subjectFilter.value !== '');
        btn.style.display = hasFilter ? '' : 'none';
    }

    function refreshLobby(pageOverride) {
        const params = new URLSearchParams();
        const search = searchInput.value.trim();
        const status = statusFilter.value;
        const subject = subjectFilter.value;
        if (search) params.set('search', search);
        if (status) params.set('status', status);
        if (subject) params.set('subject', subject);
        const page = pageOverride || 1;
        currentPage = page;
        params.set('page', String(page));

        fetch(window.location.pathname + '?' + params.toString(), {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(response => response.text())
        .then(html => {
            if (quizListContainer) {
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = html;
                const newContent = tempDiv.querySelector('#quizListContainer');
                quizListContainer.innerHTML = newContent
                    ? newContent.innerHTML
                    : tempDiv.innerHTML;
            }
            updateClearButtonVisibility();
        })
        .catch(err => console.warn('Lobby refresh error:', err));
    }

    if (searchInput) searchInput.addEventListener('input', function() { updateClearButtonVisibility(); applyFilters(); });
    if (statusFilter) statusFilter.addEventListener('change', function() { updateClearButtonVisibility(); applyFilters(); });
    if (subjectFilter) subjectFilter.addEventListener('change', function() { updateClearButtonVisibility(); applyFilters(); });

    document.addEventListener('click', function(e) {
        const clearBtn = e.target.closest('#clearFiltersBtn');
        if (!clearBtn) return;
        e.preventDefault();
        if (searchInput) searchInput.value = '';
        if (statusFilter) statusFilter.value = '';
        if (subjectFilter) subjectFilter.value = '';
        updateClearButtonVisibility();
        refreshLobby(1);
        if (searchInput) searchInput.focus();
    });

    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.lobby-page-btn');
        if (!btn || btn.disabled) return;
        const page = parseInt(btn.getAttribute('data-page'), 10);
        if (isNaN(page) || page < 1) return;
        refreshLobby(page);
        const container = document.getElementById('quizListContainer');
        if (container) container.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });

    function startAutoSync() {
        if (autoRefreshInterval) clearInterval(autoRefreshInterval);
        autoRefreshInterval = setInterval(function() {
            const hasFilter = (searchInput.value.trim() !== '')
                           || (statusFilter.value !== '')
                           || (subjectFilter.value !== '');
            if (currentPage === 1 && !hasFilter) refreshLobby(1);
        }, 30000);
    }
    startAutoSync();

    window.joinQuiz = function(quizId) {
        const btn = event.target.closest('.btn-sm');
        if (!btn) return;
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ' + I18N.joining;

        fetch('/live-quiz/lobby/join/' + quizId, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken,
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({})
        })
        .then(response => response.json().then(d => ({ ok: response.ok, data: d })))
        .then(resp => {
            const data = resp.data || {};
            if (data.reason === 'grade_mismatch') {
                btn.disabled = false;
                btn.innerHTML = originalText;
                showGradeLocked(data.required_grade);
                return;
            }
            if (data.error) {
                showToast(data.error, 'error');
                btn.disabled = false;
                btn.innerHTML = originalText;
                return;
            }
            if (data.success && data.redirect) {
                showToast(I18N.joined_ok, 'success');
                setTimeout(function() { window.location.href = data.redirect; }, 800);
            }
        })
        .catch(function(err) {
            console.error('Join error:', err);
            showToast(I18N.join_error, 'error');
            btn.disabled = false;
            btn.innerHTML = originalText;
        });
    };

    let deleteQuizId = null;

    window.deleteQuiz = function(quizId, title) {
        deleteQuizId = quizId;
        document.getElementById('deleteQuizTitle').textContent = title || I18N.delete_this;
        document.getElementById('deleteModal').classList.add('active');
    };

    function closeDeleteModal() {
        document.getElementById('deleteModal').classList.remove('active');
        deleteQuizId = null;
    }
    window.closeDeleteModal = closeDeleteModal;

    document.getElementById('confirmDeleteBtn').addEventListener('click', function() {
        if (!deleteQuizId) return;
        const btn = this;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ' + I18N.deleting;

        fetch('/live-quiz/delete/' + deleteQuizId, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken,
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({})
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast(I18N.delete_ok, 'success');
                closeDeleteModal();
                refreshLobby(currentPage);
            } else {
                showToast(data.error || I18N.delete_error, 'error');
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-trash"></i> ' + I18N.delete_confirm;
            }
        })
        .catch(function(err) {
            console.error('Delete error:', err);
            showToast(I18N.delete_error, 'error');
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-trash"></i> ' + I18N.delete_confirm;
        });
    });

    document.getElementById('deleteModal').addEventListener('click', function(e) {
        if (e.target === this) closeDeleteModal();
    });

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && document.getElementById('deleteModal').classList.contains('active')) {
            closeDeleteModal();
        }
    });

    function showToast(message, type) {
        if (typeof window.showToast === 'function') {
            window.showToast(message, type || 'info');
        } else {
            alert(message);
        }
    }

    // ============================================================
    // TELEGRAM JOIN PANEL
    // The X sets a server-side flag so the panel does not reappear
    // on any device. The CTA opens the bot; the flag is also set so
    // the panel is not shown again on the next page load after the
    // user has already acted.
    // ============================================================
    (function setupTelegramPanel() {
        var panel = document.getElementById('lobbyTgPanel');
        if (!panel) return;

        var closeBtn = document.getElementById('lobbyTgClose');
        var ctaBtn   = document.getElementById('lobbyTgCta');

        function persistDismissal() {
            try {
                fetch('/settings/api', {
                    method: 'PATCH',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrfToken
                    },
                    body: JSON.stringify({
                        'onboarding.telegram_prompt_dismissed': true
                    })
                }).catch(function () { /* silent */ });
            } catch (e) { /* silent */ }
        }

        function dismiss() {
            panel.classList.add('is-dismissing');
            setTimeout(function () {
                if (panel && panel.parentNode) panel.parentNode.removeChild(panel);
            }, 260);
            persistDismissal();
        }

        if (closeBtn) {
            closeBtn.addEventListener('click', function (e) {
                e.preventDefault();
                dismiss();
            });
        }

        if (ctaBtn) {
            ctaBtn.addEventListener('click', function () {
                persistDismissal();
            });
        }
    })();

    updateClearButtonVisibility();
});