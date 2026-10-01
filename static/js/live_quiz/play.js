/* ============================================================
   static/js/live_quiz/play.js
   ============================================================
   Extracted verbatim from templates/dashboard/live_quiz/play.html.
   Bridge objects expected on window:
     window.__LQ_I18N   — translations
     window.__LQ        — quizId, quizTitle, totalQuestions,
                          timePerQuestion, isCreator, userId
   ============================================================ */

const csrfToken = window.csrfToken || document.querySelector('meta[name="csrf-token"]')?.content || '';

const I18N = window.__LQ_I18N;

var quizExiting = false;
window.addEventListener('beforeunload', function(e) {
    if (quizExiting) return;
    e.preventDefault();
    e.returnValue = '';
    return '';
});

document.addEventListener('DOMContentLoaded', function() {
    const quizId = String(window.__LQ.quizId);
    const quizTitle = window.__LQ.quizTitle || 'Live Quiz';
    const totalQuestions = parseInt(window.__LQ.totalQuestions, 10) || 0;
    const timePerQuestion = parseInt(window.__LQ.timePerQuestion, 10) || 30;
    const ratingTime = 10;
    const totalDuration = totalQuestions * (timePerQuestion + ratingTime);
    const isCreator = !!window.__LQ.isCreator;
    const sessionUserId = String(window.__LQ.userId);

    let currentQuestionIndex = 0;
    let timer = timePerQuestion;
    let timerInterval = null;
    let isAnswered = false;
    let questionData = null;
    let isCompleted = false;
    let quizStarted = false;
    let quizEnded = false;
    let previousStatus = 'waiting';
    let notificationShown = false;
    let answerData = null;
    let isReconnecting = false;
    let hasAnsweredCurrent = false;

    let pollInterval = 1500;
    let pollTimer = null;
    let leaderboardTimer = null;
    let leaderboardInterval = 3000;
    let isRedirecting = false;
    let completedParticipantsCount = 0;
    let totalParticipantsCount = 0;

    // ============================================================
    // TITLE SHRINK — word-boundary cut for the OS notification body
    // ============================================================
    // Long quiz titles are unreadable inside a device notification.
    // Cuts at the last word boundary within `maxLen - 1` characters
    // when that boundary falls past 60% of the budget; otherwise
    // hard-cuts. Always ends with a single '…' glyph.
    function shortenTitle(text, maxLen) {
        if (!text) return '';
        text = String(text).trim();
        if (text.length <= maxLen) return text;

        var budget = maxLen - 1;
        var cut = text.slice(0, budget);
        var lastSpace = cut.lastIndexOf(' ');

        if (lastSpace >= budget * 0.6) {
            return cut.slice(0, lastSpace).replace(/\s+$/, '') + '…';
        }
        return cut.replace(/\s+$/, '') + '…';
    }

    // Build the quiz-started body from the template-provided frame.
    // I18N.notif_quiz_started_body is: '"{title}" Wuu socda, Yan Laga Tagin.'
    function buildQuizStartedBody() {
        var short = shortenTitle(quizTitle, 40);
        return (I18N.notif_quiz_started_body || '" {title} " Wuu socda, Yan Laga Tagin.')
            .replace('{title}', short);
    }

    function restoreStateFromStorage() {
        try {
            const saved = sessionStorage.getItem('live_quiz_' + quizId);
            if (saved) {
                const state = JSON.parse(saved);
                if (state && state.currentQuestionIndex !== undefined) {
                    currentQuestionIndex = state.currentQuestionIndex;
                    isAnswered = state.isAnswered || false;
                    isCompleted = state.isCompleted || false;
                    quizStarted = state.quizStarted || false;
                    hasAnsweredCurrent = state.hasAnsweredCurrent || false;
                }
            }
        } catch (e) { console.warn('Failed to restore state:', e); }
    }
    function saveStateToStorage() {
        try {
            const state = { currentQuestionIndex, isAnswered, isCompleted, quizStarted, hasAnsweredCurrent, timestamp: Date.now() };
            sessionStorage.setItem('live_quiz_' + quizId, JSON.stringify(state));
        } catch (e) { console.warn('Failed to save state:', e); }
    }

    function fetchWithTimeout(url, options, timeout = 10000) {
        options = options || {};
        options.headers = options.headers || {};
        options.headers['X-CSRF-Token'] = csrfToken;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeout);
        return fetch(url, { ...options, signal: controller.signal })
            .finally(() => clearTimeout(timeoutId))
            .catch(error => { if (error.name === 'AbortError') throw new Error('Request timeout'); throw error; });
    }

    // ── Browser notification: prefers the service worker path (works
    //    in hidden tabs on Android Chrome); falls back to in-page API.
    //
    //    `priority` is an optional object that overrides the defaults:
    //      priority.tag                 — replaces any notification with
    //                                     the same tag (idempotent)
    //      priority.vibrate             — Android vibration pattern
    //      priority.requireInteraction  — stays until the user acts
    //      priority.renotify            — re-alerts even if tag matches
    // ────────────────────────────────────────────────────────────────
    function sendBrowserNotification(title, body, url, priority) {
        if (!('Notification' in window)) return;
        if (Notification.permission !== 'granted') return;

        priority = priority || {};

        var opts = {
            body: body,
            icon: '/static/images/logo.png',
            badge: '/static/images/badge-nuun.png',
            tag: priority.tag || ('live-quiz-' + quizId),
            requireInteraction: priority.requireInteraction !== undefined
                ? priority.requireInteraction
                : true,
            data: { url: url }
        };

        if (Array.isArray(priority.vibrate)) opts.vibrate = priority.vibrate;
        if (priority.renotify) opts.renotify = true;
        if (typeof priority.silent === 'boolean') opts.silent = priority.silent;

        if ('serviceWorker' in navigator && navigator.serviceWorker.ready) {
            navigator.serviceWorker.ready.then(function (reg) {
                return reg.showNotification(title, opts);
            }).catch(function () {
                try {
                    var n = new Notification(title, opts);
                    n.onclick = function () { window.focus(); if (url) window.location.href = url; n.close(); };
                    setTimeout(function () { try { n.close(); } catch (e) {} }, 10000);
                } catch (e) {}
            });
            return;
        }

        try {
            var n = new Notification(title, opts);
            n.onclick = function() { window.focus(); if (url) window.location.href = url; n.close(); };
            setTimeout(() => n.close(), 10000);
        } catch (e) { console.warn('Browser notification error:', e); }
    }

    function showToast(message, type, duration) {
        if (typeof window.showToast === 'function') {
            window.showToast(message, type || 'info', duration || 4000);
        } else {
            const toast = document.createElement('div');
            toast.style.cssText = `position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: ${type === 'error' ? '#DC2626' : type === 'success' ? '#10B981' : '#1A1A2E'}; color: white; padding: 10px 20px; border-radius: 8px; font-size: 14px; font-weight: 500; z-index: 9999; box-shadow: 0 4px 16px rgba(0,0,0,0.25); max-width: 90%; text-align: center;`;
            toast.textContent = message;
            document.body.appendChild(toast);
            setTimeout(() => { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s ease'; setTimeout(() => toast.remove(), 400); }, duration || 4000);
        }
    }
    function showNotification(text) {
        const banner = document.getElementById('notificationBanner');
        const textEl = document.getElementById('notificationText');
        if (banner && textEl) { textEl.textContent = text; banner.classList.add('show'); clearTimeout(banner._hideTimeout); banner._hideTimeout = setTimeout(() => banner.classList.remove('show'), 5000); }
    }
    function showReconnectionNotice() {
        const notice = document.getElementById('reconnectionNotice');
        if (notice) { notice.classList.add('show'); setTimeout(() => notice.classList.remove('show'), 3000); }
    }
    function playNotificationSound() {
        try {
            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = audioCtx.createOscillator(); const gain = audioCtx.createGain();
            osc.connect(gain); gain.connect(audioCtx.destination);
            osc.frequency.value = 880; osc.type = 'sine';
            gain.gain.setValueAtTime(0.3, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.3);
            osc.start(audioCtx.currentTime); osc.stop(audioCtx.currentTime + 0.3);
            setTimeout(() => {
                const osc2 = audioCtx.createOscillator(); const gain2 = audioCtx.createGain();
                osc2.connect(gain2); gain2.connect(audioCtx.destination);
                osc2.frequency.value = 1100; osc2.type = 'sine';
                gain2.gain.setValueAtTime(0.3, audioCtx.currentTime);
                gain2.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.2);
                osc2.start(audioCtx.currentTime); osc2.stop(audioCtx.currentTime + 0.2);
            }, 200);
        } catch (e) {}
    }

    function updateTotalTimer(remaining) {
        const display = document.getElementById('totalTimerDisplay');
        const progress = document.getElementById('totalProgressFill');
        if (!display || !progress) return;
        if (remaining <= 0) { display.textContent = '00:00'; display.className = 'time-display danger'; progress.style.width = '0%'; return; }
        const minutes = Math.floor(remaining / 60); const seconds = Math.floor(remaining % 60);
        display.textContent = String(minutes).padStart(2, '0') + ':' + String(seconds).padStart(2, '0');
        if (remaining < 60) display.className = 'time-display danger';
        else if (remaining < 300) display.className = 'time-display warning';
        else display.className = 'time-display';
        const pct = Math.max(0, (remaining / totalDuration) * 100);
        progress.style.width = pct + '%';
    }
    function formatTime(seconds) { const m = Math.floor(seconds / 60); const s = Math.floor(seconds % 60); return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0'); }

    function updateParticipantProgress(progressData) {
        const container = document.getElementById('participantProgressList');
        if (!container) return;
        if (!progressData || progressData.length === 0) {
            container.innerHTML = '<div style="text-align: center; color: var(--text-muted); font-size: 13px; padding: 4px 0;">' + I18N.no_participants + '</div>';
            return;
        }
        let html = '';
        progressData.forEach(p => {
            const pct = totalQuestions > 0 ? Math.round((p.current_question_index / totalQuestions) * 100) : 0;
            let statusClass = 'waiting', statusText = '⏳ ' + I18N.status_playing;
            if (p.status === 'left') { statusClass = 'left'; statusText = '🔴 ' + I18N.status_left; }
            else if (p.current_question_index >= totalQuestions) { statusClass = 'done'; statusText = '✅ ' + I18N.status_done; }
            const isYou = String(p.user_id) === sessionUserId;
            html += `<div class="progress-item">
                        <span class="name">${p.name}${isYou ? ' (' + I18N.you_label + ')' : ''}</span>
                        <div class="bar-wrap">
                            <div class="fill" style="width: ${pct}%;"></div>
                        </div>
                        <span class="status ${statusClass}">${statusText}</span>
                    </div>`;
        });
        container.innerHTML = html;
    }

    const leaveBtn = document.getElementById('leaveBtn');
    if (leaveBtn) {
        leaveBtn.addEventListener('click', function() {
            if (!confirm(I18N.leave_confirm)) return;
            leaveBtn.disabled = true; leaveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ' + I18N.leaving;
            fetchWithTimeout('/live-quiz/leave/' + quizId, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                body: JSON.stringify({})
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        quizExiting = true;
                        sessionStorage.removeItem('live_quiz_' + quizId);
                        window.location.href = data.redirect;
                    }
                    else { showToast(data.error || I18N.toast_leave_error, 'error'); leaveBtn.disabled = false; leaveBtn.innerHTML = '<i class="fas fa-sign-out-alt"></i> ' + I18N.leave_btn; }
                })
                .catch(() => { showToast(I18N.toast_network, 'error'); leaveBtn.disabled = false; leaveBtn.innerHTML = '<i class="fas fa-sign-out-alt"></i> ' + I18N.leave_btn; });
        });
    }

    function showErrorState(message, redirectUrl) {
        const container = document.getElementById('questionContainer');
        if (!container) return;
        clearInterval(timerInterval); clearTimeout(pollTimer); clearTimeout(leaderboardTimer);
        container.innerHTML = `<div class="error-state"><div class="icon">⚠️</div><h3>${I18N.unable_title}</h3><p>${message || I18N.error_unavailable}</p><a href="${redirectUrl || '/live-quiz/lobby'}" class="btn"><i class="fas fa-arrow-left"></i> ${I18N.return_lobby}</a></div>`;
        showToast(message || I18N.error_unavailable, 'error', 5000);
    }

    // ============================================================
    // POLL QUIZ STATE
    // Handles HTTP 404 as "quiz cancelled by a super admin" and
    // redirects everyone to the lobby with a toast.
    // ============================================================
    function pollQuizState() {
        fetchWithTimeout('/live-quiz/quiz-state/' + quizId + '?t=' + Date.now(), {}, 8000)
            .then(response => {
                // 404 → the quiz was deleted from the DB.
                if (response.status === 404) {
                    if (!isRedirecting) {
                        isRedirecting = true;
                        quizExiting = true;
                        clearInterval(timerInterval);
                        clearTimeout(pollTimer);
                        clearTimeout(leaderboardTimer);
                        if (window._completedTimerInterval) clearInterval(window._completedTimerInterval);
                        try {
                            sessionStorage.removeItem('live_quiz_' + quizId);
                        } catch (e) {}
                        try {
                            if (typeof window.showToast === 'function') {
                                window.showToast(I18N.cancelled_toast, 'info', 3000);
                            }
                        } catch (e) {}
                        setTimeout(function () {
                            window.location.replace('/live-quiz/lobby');
                        }, 400);
                    }
                    return null;
                }
                return response.json();
            })
            .then(data => {
                if (!data) return;
                if (data.abort) {
                    clearInterval(timerInterval); clearTimeout(pollTimer); clearTimeout(leaderboardTimer);
                    showErrorState(data.message || I18N.error_unavailable, data.redirect || '/live-quiz/lobby');
                    return;
                }
                if (data.error) { console.warn('Polling error:', data.error); pollInterval = Math.min(pollInterval + 500, 5000); resetPollTimer(); return; }

                if (data.remaining_time !== undefined) {
                    updateTotalTimer(data.remaining_time);
                    if (data.remaining_time <= 0 && !isRedirecting && data.status === 'active') {
                        isRedirecting = true;
                        quizExiting = true;
                        window.location.href = '/live-quiz/results/' + quizId;
                        return;
                    }
                }

                if (data.participant_progress) {
                    updateParticipantProgress(data.participant_progress);
                }

                if (data.redirect_url && !isRedirecting) {
                    isRedirecting = true;
                    quizExiting = true;
                    clearInterval(timerInterval); clearTimeout(pollTimer);
                    if (leaderboardTimer) clearTimeout(leaderboardTimer);
                    window.location.href = data.redirect_url;
                    return;
                }

                if (data.status === 'finished' && !quizEnded) {
                    quizEnded = true;
                    quizExiting = true;
                    sendBrowserNotification(
                        I18N.notif_quiz_complete_title,
                        I18N.notif_quiz_complete_body,
                        '/live-quiz/results/' + quizId
                    );
                    window.location.href = data.redirect_url || '/live-quiz/results/' + quizId;
                    return;
                }

                if (data.status === 'active') {
                    if (!quizStarted) {
                        quizStarted = true;
                        if (!notificationShown) {
                            notificationShown = true;
                            showNotification(I18N.toast_started);
                            playNotificationSound();
                            // High-priority OS notification: buzzes, stays
                            // pinned until the user acts, and reuses the
                            // same tag per quiz so a second fire replaces
                            // the first instead of stacking.
                            sendBrowserNotification(
                                I18N.notif_quiz_started_title,
                                buildQuizStartedBody(),
                                '/live-quiz/play/' + quizId,
                                {
                                    tag: 'live-quiz-start-' + quizId,
                                    vibrate: [200, 100, 200, 100, 200],
                                    renotify: true,
                                    requireInteraction: true
                                }
                            );
                        }
                        loadQuestion();
                        resetPollTimer();
                        return;
                    }

                    if (questionData) {
                        if (data.current_question_index !== undefined && data.current_question_index !== currentQuestionIndex) {
                            if (!isAnswered) loadQuestion();
                        }
                        resetPollTimer();
                        return;
                    }

                    if (!questionData && !isCompleted) {
                        loadQuestion();
                        resetPollTimer();
                        return;
                    }

                    if (data.all_completed && data.status === 'active') {
                        if (data.is_completed) {
                            quizExiting = true;
                            sendBrowserNotification(
                                I18N.notif_all_finished_title,
                                I18N.notif_all_finished_body,
                                '/live-quiz/results/' + quizId
                            );
                            window.location.href = '/live-quiz/results/' + quizId;
                            return;
                        }
                    }

                    if (data.is_completed && !isCompleted) {
                        isCompleted = true;
                        showCompletedState(data);
                        saveStateToStorage();
                        pollInterval = 1000;
                        resetPollTimer();
                        return;
                    }
                }

                previousStatus = data.status;
                resetPollTimer();
            })
            .catch(function(err) { console.warn('Polling fetch error:', err); pollInterval = Math.min(pollInterval + 500, 5000); resetPollTimer(); });
    }

    function loadQuestion() {
        if (isCompleted) return;
        fetchWithTimeout('/live-quiz/get-question/' + quizId + '?t=' + Date.now(), {}, 8000)
            .then(response => response.json())
            .then(data => {
                if (data.abort) { showErrorState(data.message || I18N.error_unavailable, data.redirect || '/live-quiz/lobby'); return; }
                if (data.error) {
                    if (data.completed) {
                        isCompleted = true;
                        fetchAndShowCompletedState();
                    }
                    else if (data.waiting) { setTimeout(loadQuestion, 1000); }
                    return;
                }
                if (data.question) {
                    if (data.already_answered) {
                        if (isReconnecting) showReconnectionNotice();
                        isAnswered = true;
                        hasAnsweredCurrent = true;
                        renderAnsweredQuestion(data, { already_answered: true, answer: data.answer, correct: data.correct, explanation: data.explanation });
                        clearInterval(timerInterval);
                        const display = document.getElementById('timerDisplay'); if (display) { display.textContent = '--'; display.className = 'question-timer stopped'; }
                        saveStateToStorage();
                        return;
                    }
                    questionData = data;
                    currentQuestionIndex = data.index;
                    isAnswered = false;
                    hasAnsweredCurrent = false;
                    renderQuestion(data);
                    startTimer();
                    const progressBar = document.getElementById('progressBar'); if (progressBar) progressBar.style.width = ((data.index / data.total) * 100) + '%';
                    saveStateToStorage();
                }
            })
            .catch(function(err) { console.warn('Load question error:', err); setTimeout(loadQuestion, 2000); });
    }

    function buildReactionBarHtml(questionId) {
        const statusUrl = '/live-quiz/interaction/' + quizId + '/status';
        const likeUrl   = '/live-quiz/interaction/' + quizId + '/like';
        const saveUrl   = '/live-quiz/interaction/' + quizId + '/save';
        const reportUrl = '/live-quiz/interaction/' + quizId + '/report';
        return `
            <div class="nuun-reactions nuun-reactions--live"
                 data-question-id="${questionId}"
                 data-status-url="${statusUrl}"
                 data-like-url="${likeUrl}"
                 data-save-url="${saveUrl}"
                 data-report-url="${reportUrl}"
                 data-save-locked="false">
                <button type="button" class="nuun-reaction-btn nuun-reaction-btn--like" data-action="like" aria-pressed="false">
                    <span class="nuun-reaction-icon">♡</span>
                    <span class="nuun-reaction-label" data-label-default="${I18N.reactions_like}" data-label-active="${I18N.reactions_liked}">${I18N.reactions_like}</span>
                </button>
                <button type="button" class="nuun-reaction-btn nuun-reaction-btn--save" data-action="save" aria-pressed="false">
                    <span class="nuun-reaction-icon">🔖</span>
                    <span class="nuun-reaction-label" data-label-default="${I18N.reactions_save}" data-label-active="${I18N.reactions_saved}">${I18N.reactions_save}</span>
                </button>
                <button type="button" class="nuun-reaction-btn nuun-reaction-btn--report" data-action="report" aria-pressed="false">
                    <span class="nuun-reaction-icon">⚑</span>
                    <span class="nuun-reaction-label" data-label-default="${I18N.reactions_report}" data-label-active="${I18N.reactions_reported}">${I18N.reactions_report}</span>
                </button>
            </div>`;
    }

    function attachNextButton(buttonId, questionId) {
        const nextBtn = document.getElementById(buttonId);
        if (!nextBtn) return;
        nextBtn.addEventListener('click', function() {
            nextBtn.disabled = true;
            fetchWithTimeout('/live-quiz/advance', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                body: JSON.stringify({ quiz_id: quizId, question_id: questionId })
            }, 8000)
            .then(r => r.json())
            .then(d => {
                if (d.success) {
                    questionData = null;
                    isAnswered = false;
                    hasAnsweredCurrent = false;
                    saveStateToStorage();
                    if (d.completed) {
                        isCompleted = true;
                        fetchAndShowCompletedState();
                    } else {
                        setTimeout(loadQuestion, 400);
                    }
                } else {
                    nextBtn.disabled = false;
                    showToast(d.error || I18N.toast_network, 'error');
                }
            })
            .catch(function() {
                nextBtn.disabled = false;
                showToast(I18N.toast_network, 'error');
            });
        });
    }

    // ---------- Render question (pre-answer) ----------
    function renderQuestion(data) {
        const question = data.question; const index = data.index; const total = data.total;
        const currentQ = document.getElementById('currentQ'); const totalQ = document.getElementById('totalQ');
        if (currentQ) currentQ.textContent = index + 1; if (totalQ) totalQ.textContent = total;
        const container = document.getElementById('questionContainer'); if (!container) return;
        container.innerHTML = `
            <div class="question-text">${question.question_text}</div>
            <div class="options-container" id="optionsContainer">
                <button class="option-btn" data-value="A" onclick="selectOption('A')"><span class="letter">A.</span><span>${question.options.A}</span></button>
                <button class="option-btn" data-value="B" onclick="selectOption('B')"><span class="letter">B.</span><span>${question.options.B}</span></button>
                <button class="option-btn" data-value="C" onclick="selectOption('C')"><span class="letter">C.</span><span>${question.options.C}</span></button>
            </div>
            <div class="nuun-advance-slot">
                <button class="nuun-advance-btn nuun-advance-btn--skip" onclick="skipQuestion()" id="skipBtn">
                    <span class="nuun-advance-btn__icon">⏭️</span>
                    <span>${I18N.skip_btn}</span>
                </button>
            </div>
            <div id="feedbackContainer"></div>`;
        timer = timePerQuestion; updateTimerDisplay();
    }

    // ---------- Render answered question ----------
    function renderAnsweredQuestion(data, state) {
        const question = data.question; const index = data.index; const total = data.total;
        const currentQ = document.getElementById('currentQ'); const totalQ = document.getElementById('totalQ');
        if (currentQ) currentQ.textContent = index + 1; if (totalQ) totalQ.textContent = total;
        const container = document.getElementById('questionContainer'); if (!container) return;
        const answer = state.answer; const isCorrect = state.correct;
        const correctAnswer = question.correct_answer;
        const explanation = question.explanation || '';
        let optionsHtml = ''; const letters = ['A', 'B', 'C'];
        letters.forEach(letter => {
            let classes = 'option-btn';
            if (letter === correctAnswer) classes += ' correct';
            if (letter === answer && !isCorrect) classes += ' wrong';
            const checked = letter === answer ? '✓ ' : '';
            optionsHtml += `<button class="${classes}" disabled><span class="letter">${letter}.</span><span>${checked}${question.options[letter]}</span></button>`;
        });
        container.innerHTML = `
            <div class="question-text">${question.question_text}</div>
            <div class="options-container">${optionsHtml}</div>
            <div id="feedbackContainer">
                <div class="feedback-area ${isCorrect ? 'success' : 'error'}">
                    <div class="feedback-title">${isCorrect ? I18N.feedback_correct : I18N.feedback_wrong}</div>
                    ${explanation ? `<div style="font-size: 14px; margin-top: 4px; color: var(--text-secondary);">${I18N.explanation_prefix} ${explanation}</div>` : ''}
                </div>
                <div id="liveReactionSlot">${buildReactionBarHtml(question.id)}</div>
                <div class="nuun-advance-slot">
                    <button class="nuun-advance-btn nuun-advance-btn--next" id="liveNextBtn">
                        <span class="nuun-advance-btn__icon">➡️</span>
                        <span>${I18N.next_question}</span>
                    </button>
                </div>
            </div>`;
        const slot = document.getElementById('liveReactionSlot');
        if (slot && window.NuunReactions) window.NuunReactions.init(slot);
        attachNextButton('liveNextBtn', question.id);
    }

    function startTimer() {
        clearInterval(timerInterval); timer = timePerQuestion; updateTimerDisplay();
        timerInterval = setInterval(() => {
            timer--; updateTimerDisplay();
            if (timer <= 0) {
                clearInterval(timerInterval);
                if (!isAnswered) skipQuestion();
            }
        }, 1000);
    }
    function updateTimerDisplay() {
        const display = document.getElementById('timerDisplay'); if (!display) return;
        display.textContent = timer; display.className = 'question-timer';
        if (timer <= 5 && !isAnswered) display.classList.add('danger');
        else if (timer <= 10 && !isAnswered) display.classList.add('warning');
        else if (isAnswered) display.classList.add('stopped');
    }

    window.selectOption = function(value) {
        if (isAnswered) return;
        if (!questionData) { showToast(I18N.toast_no_question, 'warning'); return; }
        isAnswered = true; hasAnsweredCurrent = true;
        const buttons = document.querySelectorAll('.option-btn'); const skipBtn = document.getElementById('skipBtn');
        buttons.forEach(b => b.disabled = true); if (skipBtn) skipBtn.disabled = true;
        const selectedBtn = document.querySelector(`.option-btn[data-value="${value}"]`);
        const originalText = selectedBtn ? selectedBtn.innerHTML : '';
        if (selectedBtn) selectedBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

        fetchWithTimeout('/live-quiz/submit-answer', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({ quiz_id: quizId, question_id: questionData.question.id, answer: value })
        }, 8000)
        .then(response => response.json())
        .then(data => {
            answerData = data;
            buttons.forEach(b => {
                b.disabled = true;
                if (b.dataset.value === data.correct_answer) b.classList.add('correct');
                if (b.dataset.value === value && !data.correct) b.classList.add('wrong');
                const letter = b.querySelector('.letter');
                if (letter) { const text = b.textContent.replace(letter.textContent, '').trim(); const checked = b.dataset.value === value ? '✓ ' : ''; b.innerHTML = `<span class="letter">${letter.textContent}</span><span>${checked}${text}</span>`; }
            });
            const container = document.getElementById('feedbackContainer');
            if (container) {
                container.innerHTML = `
                    <div class="feedback-area ${data.correct ? 'success' : 'error'}">
                        <div class="feedback-title">${data.correct ? I18N.feedback_correct : I18N.feedback_wrong}</div>
                        ${data.explanation ? `<div style="font-size: 14px; margin-top: 4px; color: var(--text-secondary);">${I18N.explanation_prefix} ${data.explanation}</div>` : ''}
                    </div>
                    <div id="liveReactionSlot">${buildReactionBarHtml(questionData.question.id)}</div>
                    <div class="nuun-advance-slot">
                        <button class="nuun-advance-btn nuun-advance-btn--next" id="liveNextBtn">
                            <span class="nuun-advance-btn__icon">➡️</span>
                            <span>${I18N.next_question}</span>
                        </button>
                    </div>`;
                const slot = document.getElementById('liveReactionSlot');
                if (slot && window.NuunReactions) window.NuunReactions.init(slot);
                attachNextButton('liveNextBtn', questionData.question.id);
            }
            if (selectedBtn) selectedBtn.innerHTML = originalText;
            updateTimerDisplay(); saveStateToStorage();
        })
        .catch(function(error) {
            console.error('Submit answer error:', error);
            buttons.forEach(b => { b.disabled = false; if (b === selectedBtn) b.innerHTML = originalText; });
            if (skipBtn) skipBtn.disabled = false;
            isAnswered = false; hasAnsweredCurrent = false;
            showToast(I18N.toast_answer_error, 'error');
        });
    };

    window.skipQuestion = function() {
        if (isAnswered) return;
        isAnswered = true; hasAnsweredCurrent = true;
        const buttons = document.querySelectorAll('.option-btn'); const skipBtn = document.getElementById('skipBtn');
        buttons.forEach(b => b.disabled = true); if (skipBtn) skipBtn.disabled = true;

        fetchWithTimeout('/live-quiz/skip-question', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({ quiz_id: quizId, question_id: questionData.question.id })
        }, 8000)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showToast(data.error, 'error');
                buttons.forEach(b => b.disabled = false);
                if (skipBtn) skipBtn.disabled = false;
                isAnswered = false; hasAnsweredCurrent = false;
                return;
            }
            return fetchWithTimeout('/live-quiz/advance', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                body: JSON.stringify({ quiz_id: quizId, question_id: questionData.question.id })
            }, 8000).then(r => r.json());
        })
        .then(d => {
            if (!d) return;
            clearInterval(timerInterval);
            currentQuestionIndex += 1;
            if (d.completed || currentQuestionIndex >= totalQuestions) {
                isCompleted = true;
                saveStateToStorage();
                fetchAndShowCompletedState();
            } else {
                questionData = null; hasAnsweredCurrent = false;
                saveStateToStorage();
                setTimeout(() => loadQuestion(), 400);
            }
        })
        .catch(function(error) {
            console.error('Skip error:', error);
            showToast(I18N.toast_skip_error, 'error');
            isAnswered = false; hasAnsweredCurrent = false;
        });
    };

    function fetchAndShowCompletedState() {
        fetchWithTimeout('/live-quiz/quiz-state/' + quizId + '?t=' + Date.now(), {}, 8000)
            .then(response => response.json())
            .then(data => {
                if (data.is_completed) showCompletedState(data);
                else { showBasicCompletedState(); setTimeout(fetchAndShowCompletedState, 1000); }
            })
            .catch(() => { showBasicCompletedState(); setTimeout(fetchAndShowCompletedState, 2000); });
    }

    function showBasicCompletedState() {
        const container = document.getElementById('questionContainer');
        if (!container) return;
        if (container.querySelector('.completed-state')) return;
        container.innerHTML = `
            <div class="completed-state">
                <span class="icon">🎉</span>
                <h3>${I18N.completed_all}</h3>
                <p class="sub">${I18N.loading_progress}</p>
                <div style="text-align: center; padding: 20px;">
                    <i class="fas fa-spinner fa-spin" style="font-size: 32px; color: var(--primary);"></i>
                </div>
            </div>`;
    }

    function showCompletedState(data) {
        if (!data) return;
        const container = document.getElementById('questionContainer');
        if (!container) return;
        completedParticipantsCount = data.completed_count || 0;
        totalParticipantsCount = data.total_participants || 0;
        const remaining = data.remaining_time || 0;
        const pct = totalParticipantsCount > 0 ? (completedParticipantsCount / totalParticipantsCount * 100) : 0;
        const circumference = 339.292;
        const offset = circumference * (1 - pct / 100);
        const waitingCount = totalParticipantsCount - completedParticipantsCount;
        const waitingText = waitingCount === 1 ? I18N.waiting_more_one : I18N.waiting_more_many.replace('{n}', waitingCount);

        container.innerHTML = `
            <div class="completed-state">
                <span class="icon">🎉</span>
                <h3>${I18N.completed_all}</h3>
                <p class="sub">${waitingText}</p>
                <div class="waiting-dashboard">
                    <div class="overall-stats">
                        <div class="progress-circle">
                            <svg viewBox="0 0 120 120">
                                <circle class="bg" cx="60" cy="60" r="54" stroke="var(--border)" />
                                <circle class="fg" cx="60" cy="60" r="54" stroke="var(--primary)"
                                    stroke-dasharray="${circumference}" stroke-dashoffset="${offset}" id="progressCircle" />
                            </svg>
                            <div class="center-text" id="overallPercentage">${Math.round(pct)}%</div>
                        </div>
                        <div class="timer-display">
                            <span class="timer-label">${I18N.time_remaining}</span>
                            <span class="timer-value" id="waitingTimerLarge">${formatTime(remaining)}</span>
                        </div>
                    </div>
                    <div class="participant-progress-list" id="participantProgressList">
                        <div style="text-align: center; color: var(--text-muted); padding: 8px 0;">${I18N.loading_participant}</div>
                    </div>
                    <div class="motivation" id="motivationMessage">
                        <span class="emoji">💪</span> ${I18N.motivation_ahead}
                    </div>
                </div>
            </div>`;

        if (remaining > 0) {
            let remain = remaining;
            const timerDisplay = document.getElementById('waitingTimerLarge');
            if (timerDisplay) {
                const interval = setInterval(() => {
                    remain--;
                    timerDisplay.textContent = formatTime(Math.max(0, remain));
                    timerDisplay.className = 'timer-value';
                    if (remain < 60) timerDisplay.classList.add('danger');
                    else if (remain < 300) timerDisplay.classList.add('warning');
                    if (remain <= 0) { clearInterval(interval); timerDisplay.textContent = '00:00'; }
                }, 1000);
                if (window._completedTimerInterval) clearInterval(window._completedTimerInterval);
                window._completedTimerInterval = interval;
            }
        }

        if (data.participant_progress) updateCompletedProgress(data.participant_progress, data.completed_count, data.total_participants);
        else fetchLatestProgress();
        launchConfetti();
    }

    function fetchLatestProgress() {
        fetchWithTimeout('/live-quiz/quiz-state/' + quizId + '?t=' + Date.now(), {}, 3000)
            .then(response => response.json())
            .then(data => {
                if (data.participant_progress) {
                    updateCompletedProgress(data.participant_progress, data.completed_count, data.total_participants);
                }
            })
            .catch(() => {});
    }

    function updateCompletedProgress(progressData, completedCount, totalCount) {
        const list = document.getElementById('participantProgressList');
        const circle = document.getElementById('progressCircle');
        const percentageText = document.getElementById('overallPercentage');
        if (!list || !progressData) return;
        completedParticipantsCount = completedCount || 0;
        totalParticipantsCount = totalCount || 0;
        const pct = totalParticipantsCount > 0 ? (completedParticipantsCount / totalParticipantsCount * 100) : 0;
        if (percentageText) percentageText.textContent = Math.round(pct) + '%';
        if (circle) {
            const circumference = 339.292;
            circle.style.strokeDashoffset = circumference * (1 - pct / 100);
        }
        const mot = document.getElementById('motivationMessage');
        if (mot) {
            const left = totalParticipantsCount - completedParticipantsCount;
            if (left === 0) mot.innerHTML = '<span class="emoji">🏆</span> ' + I18N.motivation_everyone;
            else if (left === 1) mot.innerHTML = '<span class="emoji">🔥</span> ' + I18N.motivation_only_one;
            else if (left <= 2) mot.innerHTML = '<span class="emoji">🔥</span> ' + I18N.motivation_only_many.replace('{n}', left);
            else if (left <= 5) mot.innerHTML = '<span class="emoji">⏳</span> ' + I18N.motivation_some_many.replace('{n}', left);
            else mot.innerHTML = '<span class="emoji">💪</span> ' + I18N.motivation_default;
        }
        let html = '';
        progressData.forEach(p => {
            const pctDone = totalQuestions > 0 ? Math.round((p.current_question_index / totalQuestions) * 100) : 0;
            let statusClass = 'status-playing', statusText = '⏳ ' + I18N.status_playing;
            if (p.status === 'left') { statusClass = 'status-left'; statusText = '🔴 ' + I18N.status_left; }
            else if (p.current_question_index >= totalQuestions) { statusClass = 'status-done'; statusText = '✅ ' + I18N.status_done; }
            const isYou = String(p.user_id) === sessionUserId;
            html += `
                <div class="pp-item">
                    <span class="name">${p.name}${isYou ? '<span class="you-badge">' + I18N.you_label + '</span>' : ''}</span>
                    <div class="bar-wrap"><div class="fill" style="width: ${pctDone}%;"></div></div>
                    <span class="status ${statusClass}">${statusText}</span>
                </div>`;
        });
        list.innerHTML = html;
    }

    function launchConfetti() {
        const container = document.createElement('div');
        container.className = 'confetti-container';
        document.body.appendChild(container);
        const colors = ['#FF3138', '#FCD34D', '#10B981', '#3B82F6', '#8B5CF6', '#FF6B6B'];
        for (let i = 0; i < 60; i++) {
            const el = document.createElement('div');
            el.className = 'confetti';
            el.style.left = Math.random() * 100 + '%';
            el.style.background = colors[Math.floor(Math.random() * colors.length)];
            el.style.width = (5 + Math.random() * 8) + 'px';
            el.style.height = (5 + Math.random() * 8) + 'px';
            el.style.animationDuration = (2 + Math.random() * 3) + 's';
            el.style.animationDelay = (Math.random() * 2) + 's';
            el.style.borderRadius = Math.random() > 0.5 ? '50%' : '2px';
            container.appendChild(el);
        }
        setTimeout(() => container.remove(), 5000);
    }

    function updateLeaderboard() {
        if (leaderboardTimer) { clearTimeout(leaderboardTimer); leaderboardTimer = null; }
        fetchWithTimeout('/live-quiz/leaderboard/' + quizId + '?t=' + Date.now(), {}, 8000)
            .then(response => response.json())
            .then(data => {
                const list = document.getElementById('leaderboardList');
                const top5 = data.leaderboard || [];
                const rankBadge = document.getElementById('userRank');
                if (rankBadge) rankBadge.textContent = data.user_rank || '-';
                if (!list) return;
                if (top5.length === 0) { list.innerHTML = '<div style="text-align: center; color: var(--text-muted); font-size: 13px; padding: 8px 0;">' + I18N.leaderboard_empty + '</div>'; leaderboardTimer = setTimeout(updateLeaderboard, leaderboardInterval); return; }
                let html = '';
                top5.forEach((item, i) => {
                    const isYou = String(item.user_id) === sessionUserId;
                    const rankDisplay = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : '#' + (i + 1);
                    html += `<div class="lb-item ${isYou ? 'you' : ''}"><span class="rank">${rankDisplay}</span><span class="name">${item.name || I18N.player_unknown}${isYou ? ' (' + I18N.you_label + ')' : ''}</span><span class="score">${item.score} ${I18N.pts_label}</span></div>`;
                });
                list.innerHTML = html;
                leaderboardTimer = setTimeout(updateLeaderboard, leaderboardInterval);
            })
            .catch(function(err) { console.warn('Leaderboard update error:', err); leaderboardTimer = setTimeout(updateLeaderboard, 5000); });
    }

    function resetPollTimer() {
        if (pollTimer) clearTimeout(pollTimer);
        pollTimer = setTimeout(() => pollQuizState(), pollInterval);
    }

    restoreStateFromStorage();
    if (isCompleted) fetchAndShowCompletedState();
    resetPollTimer();
    setTimeout(pollQuizState, 100);
    leaderboardTimer = setTimeout(updateLeaderboard, 3000);

    window.addEventListener('beforeunload', function() {
        if (pollTimer) clearTimeout(pollTimer);
        if (leaderboardTimer) clearTimeout(leaderboardTimer);
        if (timerInterval) clearInterval(timerInterval);
        if (window._completedTimerInterval) clearInterval(window._completedTimerInterval);
    });

    window.selectOption = window.selectOption;
    window.skipQuestion = window.skipQuestion;
    window.loadQuestion = loadQuestion;
    window.pollQuizState = pollQuizState;
});