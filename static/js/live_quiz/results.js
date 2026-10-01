/* ============================================================
   static/js/live_quiz/results.js
   ============================================================
   Extracted verbatim from templates/dashboard/live_quiz/results.html.
   Bridge objects expected on window:
     window.__LQ_I18N   — translations
     window.__LQ        — quizId, isCreator, totalParticipants, userRank
   ============================================================ */

const I18N = window.__LQ_I18N;

document.addEventListener('DOMContentLoaded', function() {
    const quizId = String(window.__LQ.quizId);
    const isCreator = !!window.__LQ.isCreator;
    const totalParticipants = parseInt(window.__LQ.totalParticipants, 10) || 0;

    function pollResults() {
        fetch('/live-quiz/quiz-state/' + quizId + '?t=' + Date.now())
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    console.warn('Results poll error:', data.error);
                    setTimeout(pollResults, 5000);
                    return;
                }

                const countEl = document.getElementById('participantCount');
                if (countEl && data.total_participants !== undefined) {
                    countEl.textContent = data.total_participants;
                }

                setTimeout(pollResults, 3000);
            })
            .catch(function(err) {
                console.warn('Results poll error:', err);
                setTimeout(pollResults, 5000);
            });
    }

    if (isCreator) {
        fetch('/live-quiz/analysis/' + quizId + '?t=' + Date.now())
            .then(response => response.json())
            .then(data => {
                const container = document.getElementById('analysisContent');
                if (!container) return;
                let html = '';
                if (data.most_correct && data.most_correct.length > 0) {
                    html += `<div style="margin-bottom: 4px; font-weight: 600; color: #10B981;">${I18N.analysis_most_correct}</div>`;
                    data.most_correct.slice(0, 3).forEach(q => {
                        html += `
                            <div class="qa-item">
                                <span>Q${q.index + 1}: ${q.text}</span>
                                <span class="correct-rate">${q.correct_rate}% ${I18N.analysis_correct_rate}</span>
                            </div>
                        `;
                    });
                }
                if (data.most_wrong && data.most_wrong.length > 0) {
                    html += `<div style="margin-top: 8px; margin-bottom: 4px; font-weight: 600; color: #EF4444;">${I18N.analysis_most_wrong}</div>`;
                    data.most_wrong.slice(0, 3).forEach(q => {
                        html += `
                            <div class="qa-item">
                                <span>Q${q.index + 1}: ${q.text}</span>
                                <span class="wrong-rate">${q.wrong_rate}% ${I18N.analysis_wrong_rate}</span>
                            </div>
                        `;
                    });
                }
                if (!html) {
                    html = `<div style="text-align: center; color: var(--text-muted); padding: 8px 0; font-size: 13px;">${I18N.analysis_no_data}</div>`;
                }
                container.innerHTML = html;
            })
            .catch(function() {
                const container = document.getElementById('analysisContent');
                if (container) {
                    container.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 8px 0; font-size: 13px;">${I18N.analysis_unavailable}</div>`;
                }
            });
    }

    function exportResults() {
        window.location.href = '/live-quiz/export/' + quizId;
    }
    window.exportResults = exportResults;

    const hero = document.getElementById('resultHero');
    if (hero) {
        const rank = parseInt(window.__LQ.userRank, 10) || 0;
        if (rank >= 1 && rank <= 3) {
            hero.classList.add('celebrate');
        }
    }
});