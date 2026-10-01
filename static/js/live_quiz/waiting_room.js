/* ============================================================
   static/js/live_quiz/waiting_room.js
   ============================================================
   Extracted verbatim from templates/dashboard/live_quiz/waiting_room.html.
   Bridge objects expected on window:
     window.__LQ_I18N   — translations
     window.__LQ        — quizId, userId, isCreator, startsInSeconds,
                          chatCanSend, quizStatus, joinCode, quizTitle,
                          subjectCode, quizGrade
   ============================================================ */

const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

const NOTIFY_T60_TITLE = "🚀 1 daqiiqo ka hartay";
const NOTIFY_T60_BODY_TAIL = " wuu soo dhowaaday. Taabo si aad ugu soo biirto. Haderbu bilaban.";

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
    const userId = String(window.__LQ.userId);
    const isCreator = !!window.__LQ.isCreator;
    const chatPollMs = 2000;
    const chatPollMsHidden = 20000;

    const JOIN_CODE = window.__LQ.joinCode;
    const SHARE_URL = window.location.origin + '/live-quiz/j/' + JOIN_CODE;
    const QUIZ_TITLE = window.__LQ.quizTitle;
    const SUBJECT_CODE = (window.__LQ.subjectCode || '').toUpperCase();
    const QUIZ_GRADE = window.__LQ.quizGrade || '';

    function shortTitle() {
        var t = QUIZ_TITLE;
        if (t.length > 30) t = t.substring(0, 28) + '…';
        return t;
    }

    if (!quizId || quizId === 'None') {
        window.location.href = '/live-quiz/lobby';
        return;
    }

    // ============================================================
    // PARTICIPANTS
    // ============================================================
    let participantList = [];
    let isPolling = false;
    let pollTimer = null;
    let countdownInterval = null;
    let notificationSent = false;
    let isRedirecting = false;

    const participantListEl = document.getElementById('participantList');
    const participantCountEl = document.getElementById('participantCount');
    const participantCountLabel = document.getElementById('participantCountLabel');
    const tabParticipantsCount = document.getElementById('tabParticipantsCount');
    const waitingCountEl = document.getElementById('waitingParticipantCount');
    const startBtn = document.getElementById('startQuizBtn');
    const statusMsg = document.getElementById('statusMessage');
    const leaveBtn = document.getElementById('leaveBtn');
    const rejoinBtn = document.getElementById('rejoinBtn');

    let initialStartsIn = parseInt(window.__LQ.startsInSeconds, 10) || 0;

    // ============================================================
    // TABS
    // ============================================================
    const tabParticipantsBtn = document.getElementById('tabParticipants');
    const tabChatBtn         = document.getElementById('tabChat');
    const panelParticipants  = document.getElementById('panel-participants');
    const panelChat          = document.getElementById('panel-chat');
    const chatUnreadDot      = document.getElementById('chatUnreadDot');

    let activeTab = 'participants';
    const TAB_KEY = 'nuun_lq_tab_' + quizId;

    function switchTab(name) {
        activeTab = name;
        const isChat = (name === 'chat');
        tabParticipantsBtn.classList.toggle('is-active', !isChat);
        tabChatBtn.classList.toggle('is-active', isChat);
        panelParticipants.hidden = isChat;
        panelChat.hidden = !isChat;
        try { sessionStorage.setItem(TAB_KEY, name); } catch (e) {}
        if (isChat) {
            chatAtBottom = true;
            requestAnimationFrame(() => {
                chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
                hideUnreadDot();
                hideNewMessagePill();
            });
        }
    }
    tabParticipantsBtn.addEventListener('click', () => switchTab('participants'));
    tabChatBtn.addEventListener('click', () => switchTab('chat'));
    try {
        const saved = sessionStorage.getItem(TAB_KEY);
        if (saved === 'chat') switchTab('chat');
    } catch (e) {}
    function showUnreadDot() { if (chatUnreadDot) chatUnreadDot.hidden = false; }
    function hideUnreadDot() { if (chatUnreadDot) chatUnreadDot.hidden = true; }

    // ============================================================
    // SWIPE
    // ============================================================
    (function setupSwipeGesture() {
        var SWIPE_MIN_DX = 60, SWIPE_MAX_DT = 600, SWIPE_RATIO = 1.5;
        var startX = 0, startY = 0, startT = 0, armed = false;
        function isInteractive(el) {
            while (el && el !== document.body) {
                if (!el.tagName) { el = el.parentElement; continue; }
                var tag = el.tagName.toUpperCase();
                if (tag === 'BUTTON' || tag === 'A' || tag === 'INPUT' ||
                    tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'LABEL') return true;
                if (el.hasAttribute) {
                    if (el.hasAttribute('onclick') || el.hasAttribute('data-copy') ||
                        el.getAttribute('role') === 'button') return true;
                }
                el = el.parentElement;
            }
            return false;
        }
        function modalOpen() {
            var cm = document.getElementById('quizCancelModal');
            if (cm && cm.classList.contains('active')) return true;
            var ps = document.getElementById('pingSheet');
            if (ps && !ps.hidden) return true;
            return false;
        }
        var container = document.querySelector('.waiting-room-container');
        if (!container) return;
        container.addEventListener('touchstart', function(e) {
            if (e.touches.length !== 1) { armed = false; return; }
            if (modalOpen()) { armed = false; return; }
            if (isInteractive(e.target)) { armed = false; return; }
            var t = e.touches[0];
            startX = t.clientX; startY = t.clientY;
            startT = Date.now(); armed = true;
        }, { passive: true });
        container.addEventListener('touchend', function(e) {
            if (!armed) return;
            armed = false;
            if (e.changedTouches.length !== 1) return;
            var t = e.changedTouches[0];
            var dx = t.clientX - startX, dy = t.clientY - startY, dt = Date.now() - startT;
            if (dt > SWIPE_MAX_DT) return;
            if (Math.abs(dx) < SWIPE_MIN_DX) return;
            if (Math.abs(dx) < Math.abs(dy) * SWIPE_RATIO) return;
            if (dx < 0 && activeTab !== 'chat') switchTab('chat');
            else if (dx > 0 && activeTab !== 'participants') switchTab('participants');
        }, { passive: true });
        container.addEventListener('touchcancel', function() { armed = false; }, { passive: true });
    })();

    // ============================================================
    // SHARE
    // ============================================================
    (function setupShare() {
        var chip = document.getElementById('shareChip');
        var chipText = document.getElementById('shareChipText');
        var toggle = document.getElementById('shareToggle');
        var toggleIcon = document.getElementById('shareToggleIcon');
        var actions = document.getElementById('shareActions');
        var waBtn = document.getElementById('shareWhatsAppBtn');
        var tgBtn = document.getElementById('shareTelegramBtn');
        var codeBtn = document.getElementById('shareCodeBtn');
        var codeLabel = document.getElementById('shareCodeLabel');
        if (!chip || !toggle || !actions) return;

        var chipLabel = SHARE_URL.replace(/^https?:\/\//, '').replace(/\/$/, '');
        chipText.textContent = chipLabel;
        var originalChipLabel = chipLabel;
        var chipT = null, codeT = null;

        function copy(text) {
            if (navigator.clipboard && navigator.clipboard.writeText)
                return navigator.clipboard.writeText(text);
            return new Promise(function(res, rej) {
                try {
                    var ta = document.createElement('textarea');
                    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
                    document.body.appendChild(ta); ta.select();
                    document.execCommand('copy'); document.body.removeChild(ta);
                    res();
                } catch (e) { rej(e); }
            });
        }

        chip.addEventListener('click', function() {
            copy(SHARE_URL).then(function() {
                chip.classList.add('is-copied');
                chipText.textContent = '✓ ' + I18N.link_copied;
                if (chipT) clearTimeout(chipT);
                chipT = setTimeout(function() {
                    chip.classList.remove('is-copied');
                    chipText.textContent = originalChipLabel;
                }, 1500);
            }).catch(function() {});
        });

        var expanded = false;
        function setExpanded(v) {
            expanded = !!v;
            actions.hidden = !expanded;
            toggleIcon.className = expanded ? 'fas fa-times' : 'fas fa-ellipsis-h';
            toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        }
        toggle.addEventListener('click', function() { setExpanded(!expanded); });
        document.addEventListener('click', function(e) {
            if (!expanded) return;
            var sec = document.getElementById('wrShareSection');
            if (sec && !sec.contains(e.target)) setExpanded(false);
        });

        function buildWhatsAppText() {
            var lines = ['🎯 *' + I18N.share_cta + '*', ''];
            var meta = '📚 *' + QUIZ_TITLE + '*';
            if (SUBJECT_CODE) meta += ' [' + SUBJECT_CODE + ']';
            if (QUIZ_GRADE) meta += '  ·  🎓 ' + QUIZ_GRADE;
            lines.push(meta); lines.push('');
            lines.push('🔗 ' + SHARE_URL);
            return lines.join('\n');
        }
        function buildTelegramText() {
            var lines = ['🎯 *' + I18N.share_cta + '*', ''];
            var meta = '📚 *' + QUIZ_TITLE + '*';
            if (SUBJECT_CODE) meta += ' [' + SUBJECT_CODE + ']';
            if (QUIZ_GRADE) meta += '  ·  🎓 ' + QUIZ_GRADE;
            lines.push(meta); lines.push('');
            lines.push('🔗 [' + I18N.share_link_label + '](' + SHARE_URL + ').');
            return lines.join('\n');
        }
        waBtn.addEventListener('click', function() {
            window.open('https://wa.me/?text=' + encodeURIComponent(buildWhatsAppText()), '_blank', 'noopener,noreferrer');
        });
        tgBtn.addEventListener('click', function() {
            window.open('https://t.me/share/url?text=' + encodeURIComponent(buildTelegramText()), '_blank', 'noopener,noreferrer');
        });
        var originalCodeLabel = codeLabel.textContent;
        codeBtn.addEventListener('click', function() {
            copy(JOIN_CODE).then(function() {
                codeBtn.classList.add('is-copied');
                codeLabel.textContent = '✓';
                if (codeT) clearTimeout(codeT);
                codeT = setTimeout(function() {
                    codeBtn.classList.remove('is-copied');
                    codeLabel.textContent = originalCodeLabel;
                }, 1500);
            });
        });
    })();

    // ============================================================
    // PING BANNER
    // ============================================================
    const pingBanner = document.getElementById('pingBanner');
    const pingBannerName = document.getElementById('pingBannerName');
    const pingBannerTime = document.getElementById('pingBannerTime');
    const pingBannerText = document.getElementById('pingBannerText');
    const pingBannerProgress = document.getElementById('pingBannerProgress');
    let pingBannerTimer = null;

    window.dismissPingBanner = function() {
        if (!pingBanner) return;
        pingBanner.classList.remove('is-visible');
        if (pingBannerTimer) clearTimeout(pingBannerTimer);
        setTimeout(function() { pingBanner.hidden = true; }, 250);
    };

    function showPingBanner(name, time, text) {
        if (!pingBanner) return;
        pingBannerName.textContent = name;
        pingBannerTime.textContent = time;
        pingBannerText.textContent = text;
        pingBanner.hidden = false;
        void pingBanner.offsetWidth;
        pingBanner.classList.add('is-visible');
        if (pingBannerProgress) {
            pingBannerProgress.style.animation = 'none';
            void pingBannerProgress.offsetWidth;
            pingBannerProgress.style.animation = 'pingProgress 8s linear forwards';
        }
        if (pingBannerTimer) clearTimeout(pingBannerTimer);
        pingBannerTimer = setTimeout(window.dismissPingBanner, 8000);
    }

    // ============================================================
    // AUDIO
    // ============================================================
    let _audioCtx = null;
    function getAudioCtx() {
        if (!_audioCtx) {
            try { _audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
            catch (e) { return null; }
        }
        if (_audioCtx.state === 'suspended') _audioCtx.resume().catch(function(){});
        return _audioCtx;
    }
    function playChatSound() {
        const ctx = getAudioCtx(); if (!ctx) return;
        const now = ctx.currentTime;
        [660, 880].forEach(function(freq, i) {
            const osc = ctx.createOscillator(), gain = ctx.createGain();
            osc.connect(gain); gain.connect(ctx.destination);
            osc.frequency.value = freq; osc.type = 'sine';
            const start = now + i * 0.07;
            gain.gain.setValueAtTime(0, start);
            gain.gain.linearRampToValueAtTime(0.22, start + 0.015);
            gain.gain.exponentialRampToValueAtTime(0.01, start + 0.12);
            osc.start(start); osc.stop(start + 0.15);
        });
    }
    function playPingSound() {
        const ctx = getAudioCtx(); if (!ctx) return;
        const now = ctx.currentTime;
        [660, 880, 1100].forEach(function(freq, i) {
            const osc = ctx.createOscillator(), gain = ctx.createGain();
            osc.connect(gain); gain.connect(ctx.destination);
            osc.frequency.value = freq; osc.type = 'sine';
            const start = now + i * 0.13;
            gain.gain.setValueAtTime(0, start);
            gain.gain.linearRampToValueAtTime(0.35, start + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.01, start + 0.28);
            osc.start(start); osc.stop(start + 0.32);
        });
    }

    let chatSoundMuted = false;
    let lastChatSoundAt = 0;
    const CHAT_SOUND_THROTTLE_MS = 1500;
    try { chatSoundMuted = sessionStorage.getItem('nuun_chat_muted') === '1'; } catch (e) {}
    function shouldPlayChatSound() {
        if (chatSoundMuted) return false;
        if (document.activeElement === chatInputEl) return false;
        const now = Date.now();
        if (now - lastChatSoundAt < CHAT_SOUND_THROTTLE_MS) return false;
        lastChatSoundAt = now;
        return true;
    }

    // ============================================================
    // BROWSER NOTIFICATION — SW path first, in-page fallback
    // ============================================================
    // The in-page `new Notification()` silently fails in background
    // tabs on Android Chrome. The service worker path works in both
    // foreground and background, and its notificationclick handler
    // (already in sw.js) opens the correct URL on tap.
    // ============================================================
    function sendBrowserNotification(title, body, url, kind) {
        if (!('Notification' in window)) return;
        if (Notification.permission !== 'granted') return;

        var isPing = (kind === 'ping');
        var isChat = (kind === 'chat');

        var opts = {
            body: body,
            icon: '/static/images/logo.png',
            badge: '/static/images/badge-nuun.png',
            tag: isPing ? ('live-quiz-ping-' + quizId)
                 : isChat ? ('live-quiz-chat-' + quizId)
                 : ('live-quiz-start-' + quizId),
            requireInteraction: isPing,
            silent: false,
            renotify: isPing || isChat,
            data: { url: url }
        };
        if (isPing) opts.vibrate = [200, 100, 200, 100, 200];
        if (isChat) opts.vibrate = [80, 40, 80];

        // Prefer service worker path — reliable in background tabs.
        if ('serviceWorker' in navigator && navigator.serviceWorker.ready) {
            navigator.serviceWorker.ready.then(function (reg) {
                return reg.showNotification(title, opts);
            }).catch(function () {
                // Fall back to the in-page API if SW path fails.
                try {
                    var n = new Notification(title, opts);
                    n.onclick = function () {
                        try { window.focus(); } catch (e) {}
                        if (url) window.location.href = url;
                        n.close();
                    };
                    if (!isPing) setTimeout(function () { try { n.close(); } catch (e) {} }, 20000);
                } catch (e) {}
            });
            return;
        }

        // No SW available — in-page only.
        try {
            var n = new Notification(title, opts);
            n.onclick = function () {
                try { window.focus(); } catch (e) {}
                if (url) window.location.href = url;
                n.close();
            };
            if (!isPing) setTimeout(function () { try { n.close(); } catch (e) {} }, 20000);
        } catch (e) {}
    }

    // ============================================================
    // CHAT
    // ============================================================
    const chatMessagesEl = document.getElementById('chatMessages');
    const chatEmptyEl = document.getElementById('chatEmpty');
    const chatFormEl = document.getElementById('chatForm');
    const chatInputEl = document.getElementById('chatInput');
    const chatSendBtn = document.getElementById('chatSendBtn');
    const chatLockedEl = document.getElementById('chatLocked');

    const chatMuteBtn = document.getElementById('chatMuteBtn');
    const chatMuteIcon = document.getElementById('chatMuteIcon');
    function renderMuteIcon() {
        if (!chatMuteIcon) return;
        chatMuteIcon.className = chatSoundMuted ? 'fas fa-volume-mute' : 'fas fa-volume-up';
    }
    renderMuteIcon();
    if (chatMuteBtn) {
        chatMuteBtn.addEventListener('click', function() {
            chatSoundMuted = !chatSoundMuted;
            try { sessionStorage.setItem('nuun_chat_muted', chatSoundMuted ? '1' : '0'); } catch (e) {}
            renderMuteIcon();
        });
    }

    let chatSinceId = 0;
    let chatPollTimer = null;
    let chatSendingNow = false;
    let chatAtBottom = true;
    let chatServerAllows = !!window.__LQ.chatCanSend;
    let chatServerOpen = !!window.__LQ.chatServerOpen;
    let newMessagePill = null;
    let lastShownPingId = 0;
    let pollsCompleted = 0;
    const renderedChatIds = new Set();

    // WhatsApp-style: track unread count while tab is hidden.
    // The OS notification body shows either the single message or
    // "N new messages · Latest from Name: text". Same tag → replaces.
    let hiddenUnreadCount = 0;
    let hiddenLastSender = '';
    let hiddenLastBody = '';
    let lastChatNotifyAt = 0;
    const CHAT_NOTIFY_THROTTLE_MS = 1000;

    function maybeNotifyChat(senderName, body) {
        if (chatSoundMuted) return;
        if (document.visibilityState !== 'hidden') return;

        hiddenUnreadCount += 1;
        hiddenLastSender = senderName || 'Participant';
        hiddenLastBody = body || '';

        var now = Date.now();
        if (now - lastChatNotifyAt < CHAT_NOTIFY_THROTTLE_MS) return;
        lastChatNotifyAt = now;

        var title, bodyText;
        if (hiddenUnreadCount === 1) {
            title = '💬 ' + hiddenLastSender;
            bodyText = hiddenLastBody;
            if (bodyText.length > 120) bodyText = bodyText.substring(0, 118) + '…';
        } else {
            title = '💬 ' + hiddenUnreadCount + ' fariin cusub';
            bodyText = 'Ugu dambeysay ' + hiddenLastSender + ': ';
            var shortBody = hiddenLastBody;
            if (shortBody.length > 80) shortBody = shortBody.substring(0, 78) + '…';
            bodyText += shortBody;
        }

        sendBrowserNotification(
            title,
            bodyText,
            '/live-quiz/waiting-room/' + quizId,
            'chat'
        );
    }

    function resetHiddenUnread() {
        hiddenUnreadCount = 0;
        hiddenLastSender = '';
        hiddenLastBody = '';
        // Close the chat notification if it exists
        if ('serviceWorker' in navigator && navigator.serviceWorker.ready) {
            navigator.serviceWorker.ready.then(function (reg) {
                return reg.getNotifications({ tag: 'live-quiz-chat-' + quizId });
            }).then(function (list) {
                list.forEach(function (n) { try { n.close(); } catch (e) {} });
            }).catch(function () {});
        }
    }

    function formatTime12h(raw) {
        if (!raw) return '';
        var s = String(raw).trim();
        var match = s.match(/(\d{1,2}):(\d{2})/);
        if (!match) return '';
        var h = parseInt(match[1], 10), m = match[2];
        if (isNaN(h)) return '';
        var lower = s.toLowerCase(), ampm;
        if (lower.indexOf('pm') !== -1) ampm = 'pm';
        else if (lower.indexOf('am') !== -1) ampm = 'am';
        else ampm = (h >= 12) ? 'pm' : 'am';
        if (h === 0) h = 12; else if (h > 12) h -= 12;
        return h + ':' + m + ' ' + ampm;
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, function(c) {
            return { '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c];
        });
    }

    function makeNonce() {
        if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
        return 'n' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
    }

    function autogrowTextarea() {
        if (!chatInputEl) return;
        chatInputEl.style.height = 'auto';
        var next = Math.min(chatInputEl.scrollHeight, 120);
        chatInputEl.style.height = next + 'px';
    }

    function ensureNewMessagePill() {
        if (newMessagePill) return newMessagePill;
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'wr-chat-new-pill';
        btn.textContent = '↓ ' + I18N.chat_new_pill;
        btn.addEventListener('click', function() {
            chatAtBottom = true;
            chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
            hideNewMessagePill();
        });
        panelChat.appendChild(btn);
        newMessagePill = btn;
        return btn;
    }
    function showNewMessagePill() { ensureNewMessagePill().classList.add('is-visible'); }
    function hideNewMessagePill() { if (newMessagePill) newMessagePill.classList.remove('is-visible'); }

    function buildOtherMessageEl(m) {
        var wrap = document.createElement('div');
        wrap.className = 'wr-chat-msg is-other';
        if (m.is_deleted) {
            wrap.classList.add('is-deleted');
            wrap.innerHTML = '<div class="wr-chat-body"><em>' + escapeHtml(I18N.chat_deleted) + '</em></div>';
            return wrap;
        }
        var name = escapeHtml(m.name || 'Participant');
        var pid = escapeHtml(m.public_id || '----');
        var time = escapeHtml(formatTime12h(m.created_at));
        var body = escapeHtml(m.body || '').replace(/\n/g, '<br>');
        wrap.innerHTML =
            '<div class="wr-chat-header">' +
                '<div class="wr-chat-idwrap">' +
                    '<span class="wr-chat-name">' + name + '</span>' +
                    '<button type="button" class="wr-chat-pid" data-copy="' + escapeHtml(m.public_id || '') + '" title="Copy ID">#' + pid + '</button>' +
                '</div>' +
                '<span class="wr-chat-time">' + time + '</span>' +
            '</div>' +
            '<div class="wr-chat-body">' + body + '</div>';
        return wrap;
    }
    function buildMineMessageEl(m) {
        var wrap = document.createElement('div');
        wrap.className = 'wr-chat-msg is-mine';
        if (m.is_deleted) {
            wrap.classList.add('is-deleted');
            wrap.innerHTML = '<div class="wr-chat-body"><em>' + escapeHtml(I18N.chat_deleted) + '</em></div>';
            return wrap;
        }
        var time = escapeHtml(formatTime12h(m.created_at));
        var body = escapeHtml(m.body || '').replace(/\n/g, '<br>');
        wrap.innerHTML = '<div class="wr-chat-body">' + body + '</div>' +
                         '<span class="wr-chat-time">' + time + '</span>';
        return wrap;
    }
    function buildPingMessageEl(m) {
        var wrap = document.createElement('div');
        var mine = String(m.user_id) === String(userId);
        wrap.className = 'wr-chat-msg is-ping' + (mine ? ' is-ping-mine' : '');
        if (m.is_deleted) {
            wrap.classList.add('is-deleted');
            wrap.innerHTML = '<div class="wr-chat-body"><em>' + escapeHtml(I18N.chat_deleted) + '</em></div>';
            return wrap;
        }
        var name = escapeHtml(m.name || 'Participant');
        var time = escapeHtml(formatTime12h(m.created_at));
        var body = escapeHtml(m.body || '').replace(/\n/g, '<br>');
        wrap.innerHTML =
            '<div class="wr-chat-ping-header">' +
                '<span class="wr-chat-ping-icon">📣</span>' +
                '<span class="wr-chat-ping-label">PING · ' + name + '</span>' +
                '<span class="wr-chat-ping-time">' + time + '</span>' +
            '</div>' +
            '<div class="wr-chat-body">' + body + '</div>';
        return wrap;
    }

    function renderChatMessages(msgs) {
        if (!msgs || !msgs.length) return;
        if (chatEmptyEl && chatEmptyEl.parentNode) chatEmptyEl.parentNode.removeChild(chatEmptyEl);
        var isFirst = (pollsCompleted === 0);
        var frag = document.createDocumentFragment();
        msgs.forEach(function(m) {
            if (renderedChatIds.has(m.id)) return;
            renderedChatIds.add(m.id);
            if (m.message_type === 'ping') {
                frag.appendChild(buildPingMessageEl(m));
                if (isFirst) {
                    if (m.id > lastShownPingId) lastShownPingId = m.id;
                } else if (m.id > lastShownPingId) {
                    lastShownPingId = m.id;
                    showPingBanner(m.name, formatTime12h(m.created_at), m.body);
                    if (String(m.user_id) !== String(userId)) {
                        playPingSound();
                        sendBrowserNotification(
                            '📣 ' + (m.name || 'Participant'),
                            m.body,
                            '/live-quiz/waiting-room/' + quizId,
                            'ping'
                        );
                    }
                }
            } else {
                var isMine = String(m.user_id) === String(userId);
                frag.appendChild(isMine ? buildMineMessageEl(m) : buildOtherMessageEl(m));
                if (!isMine && !m.is_deleted) {
                    if (document.visibilityState === 'hidden') {
                        maybeNotifyChat(m.name || 'Participant', m.body || '');
                    } else if (shouldPlayChatSound()) {
                        playChatSound();
                    }
                }
            }
        });
        chatMessagesEl.appendChild(frag);
        if (chatAtBottom) chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
        else if (activeTab === 'chat') showNewMessagePill();
    }

    chatMessagesEl.addEventListener('click', function(e) {
        var target = e.target.closest('[data-copy]');
        if (!target) return;
        var value = target.getAttribute('data-copy');
        if (!value) return;
        navigator.clipboard.writeText('#' + value).then(function() {
            if (typeof window.showToast === 'function') window.showToast(I18N.chat_id_copied, 'success');
        }).catch(function() {
            var ta = document.createElement('textarea');
            ta.value = '#' + value;
            document.body.appendChild(ta); ta.select();
            try { document.execCommand('copy'); } catch (err) {}
            document.body.removeChild(ta);
            if (typeof window.showToast === 'function') window.showToast(I18N.chat_id_copied, 'success');
        });
    });

    chatMessagesEl.addEventListener('scroll', function() {
        var gap = chatMessagesEl.scrollHeight - chatMessagesEl.scrollTop - chatMessagesEl.clientHeight;
        chatAtBottom = (gap < 40);
        if (chatAtBottom) hideNewMessagePill();
    });

    function updateChatPermissionUI(data) {
        if (!data) return;
        var open = !!data.chat_open;
        var canSend = !!data.can_send;
        chatServerOpen = open;
        chatServerAllows = canSend;
        var showForm = open && canSend;
        var showLocked = open && !canSend;
        chatFormEl.hidden = !showForm;
        chatLockedEl.hidden = !showLocked;
        if (showForm) {
            chatInputEl.disabled = false;
            chatSendBtn.disabled = chatSendingNow || !(chatInputEl.value || '').trim();
        }
        if (!open) {
            chatInputEl.disabled = true;
            chatSendBtn.disabled = true;
        }
        if (isCreator && typeof data.ping_cooldown_remaining === 'number') {
            if (data.ping_cooldown_remaining > 0) startPingCooldown(data.ping_cooldown_remaining);
        }
    }

    function scheduleChatPoll() {
        var delay = (document.visibilityState === 'hidden') ? chatPollMsHidden : chatPollMs;
        chatPollTimer = setTimeout(pollChat, delay);
    }

    function pollChat(oneShot) {
        fetch('/live-quiz/waiting-room/' + quizId + '/chat?since=' + chatSinceId + '&t=' + Date.now())
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data && Array.isArray(data.messages) && data.messages.length) {
                    renderChatMessages(data.messages);
                    var lastId = data.messages[data.messages.length - 1].id;
                    if (lastId > chatSinceId) chatSinceId = lastId;
                    if (activeTab !== 'chat') showUnreadDot();
                }
                updateChatPermissionUI(data);
                pollsCompleted++;
                if (!oneShot) scheduleChatPoll();
            })
            .catch(function() {
                if (!oneShot) {
                    var delay = (document.visibilityState === 'hidden') ? chatPollMsHidden * 2 : chatPollMs * 2;
                    chatPollTimer = setTimeout(pollChat, delay);
                }
            });
    }

    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'visible') {
            if (chatPollTimer) { clearTimeout(chatPollTimer); chatPollTimer = null; }
            pollChat(false);
            // Reset the unread counter and close any chat notification
            resetHiddenUnread();
        }
    });

    function updateSendBtnState() {
        if (chatSendingNow) return;
        var has = (chatInputEl.value || '').trim().length > 0;
        chatSendBtn.disabled = !has;
    }
    chatInputEl.addEventListener('input', function() { autogrowTextarea(); updateSendBtnState(); });
    autogrowTextarea();
    updateSendBtnState();
    chatInputEl.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) return;
    });
    chatInputEl.addEventListener('focus', function() {
        setTimeout(function() {
            try { chatFormEl.scrollIntoView({ block: 'end', behavior: 'smooth' }); }
            catch (e) { try { chatFormEl.scrollIntoView(false); } catch (e2) {} }
        }, 300);
    });
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', function() {
            if (document.activeElement === chatInputEl) {
                setTimeout(function() { chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight; }, 50);
            }
        });
    }

    function sendChatMessage() {
        if (chatSendingNow) return;
        if (!chatServerOpen || !chatServerAllows) return;
        var text = (chatInputEl.value || '').trim();
        if (!text) return;
        chatSendingNow = true;
        chatSendBtn.disabled = true;
        var nonce = makeNonce();
        fetch('/live-quiz/waiting-room/' + quizId + '/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({ body: text, nonce: nonce })
        })
        .then(function(r) { return r.json().then(function(j) { return { ok: r.ok, body: j }; }); })
        .then(function(res) {
            if (res.ok && res.body.success) {
                chatInputEl.value = '';
                autogrowTextarea();
                chatAtBottom = true;
                pollChat(true);
            } else {
                var reason = res.body && res.body.reason;
                var msg = (res.body && res.body.error) || I18N.chat_send_error;
                if (reason === 'rate_limited') msg = I18N.chat_rate_limited;
                if (typeof window.showToast === 'function') window.showToast(msg, 'error');
                if (reason === 'sending_locked') updateChatPermissionUI({ chat_open: true, can_send: false });
            }
        })
        .catch(function() {
            if (typeof window.showToast === 'function') window.showToast(I18N.chat_send_error, 'error');
        })
        .finally(function() {
            chatSendingNow = false;
            if (chatServerOpen && chatServerAllows) updateSendBtnState();
        });
    }
    chatSendBtn.addEventListener('click', function(e) { e.preventDefault(); e.stopPropagation(); sendChatMessage(); });
    chatLockedEl.addEventListener('click', function() {
        if (typeof window.openUpgradeSheet === 'function') {
            window.openUpgradeSheet({
                context: 'live_quiz_chat',
                title: I18N.chat_locked_title,
                message: I18N.chat_locked_sub
            });
        }
    });

    // ============================================================
    // PING UI
    // ============================================================
    const pingOpenBtn = document.getElementById('pingOpenBtn');
    const pingOpenSub = document.getElementById('pingOpenSub');
    const pingSheet = document.getElementById('pingSheet');
    const pingCustomText = document.getElementById('pingCustomText');
    const pingCustomCount = document.getElementById('pingCustomCount');
    const pingSendBtn = document.getElementById('pingSendBtn');
    let pingSelectedMessage = '';
    let pingSelectedReason = '';
    let pingCooldownTimer = null;
    let pingCooldownRemaining = 0;

    window.openPingSheet = function() {
        if (pingCooldownRemaining > 0) return;
        if (!pingSheet) return;
        pingSheet.hidden = false;
        pingCustomText.value = '';
        pingCustomCount.textContent = '0';
        pingSendBtn.disabled = true;
        pingSelectedMessage = '';
        pingSelectedReason = '';
        document.querySelectorAll('.ping-preset').forEach(function(b) { b.classList.remove('is-selected'); });
        setTimeout(function() { pingCustomText.focus(); }, 100);
    };
    window.closePingSheet = function() { if (pingSheet) pingSheet.hidden = true; };
    document.querySelectorAll('.ping-preset').forEach(function(btn) {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.ping-preset').forEach(function(b) { b.classList.remove('is-selected'); });
            btn.classList.add('is-selected');
            pingCustomText.value = '';
            pingCustomCount.textContent = '0';
            pingSelectedMessage = btn.getAttribute('data-message') || '';
            pingSelectedReason = btn.getAttribute('data-reason') || 'custom';
            pingSendBtn.disabled = !pingSelectedMessage;
        });
    });
    if (pingCustomText) {
        pingCustomText.addEventListener('input', function() {
            var v = pingCustomText.value || '';
            pingCustomCount.textContent = v.length;
            if (v.trim()) {
                document.querySelectorAll('.ping-preset').forEach(function(b) { b.classList.remove('is-selected'); });
                pingSelectedMessage = v.trim();
                pingSelectedReason = 'custom';
                pingSendBtn.disabled = false;
            } else {
                pingSendBtn.disabled = !pingSelectedMessage;
            }
        });
    }
    function startPingCooldown(seconds) {
        if (!pingOpenSub || !pingOpenBtn) return;
        if (pingCooldownTimer) clearInterval(pingCooldownTimer);
        pingCooldownRemaining = seconds;
        pingOpenBtn.classList.add('is-cooldown');
        updatePingSubText();
        pingCooldownTimer = setInterval(function() {
            pingCooldownRemaining--;
            if (pingCooldownRemaining <= 0) {
                clearInterval(pingCooldownTimer);
                pingCooldownTimer = null;
                pingCooldownRemaining = 0;
                pingOpenBtn.classList.remove('is-cooldown');
                pingOpenSub.textContent = I18N.ping_open_sub;
            } else updatePingSubText();
        }, 1000);
    }
    function updatePingSubText() {
        if (!pingOpenSub) return;
        var tpl = I18N.ping_cooldown_fmt || 'Next ping available in {s}s';
        pingOpenSub.textContent = tpl.replace('{s}', pingCooldownRemaining);
    }
    if (pingOpenBtn) pingOpenBtn.addEventListener('click', window.openPingSheet);
    if (pingSendBtn) {
        pingSendBtn.addEventListener('click', function() {
            var message = (pingSelectedMessage || '').trim();
            if (!message) return;
            pingSendBtn.disabled = true;
            fetch('/live-quiz/waiting-room/' + quizId + '/ping', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                body: JSON.stringify({ body: message, reason: pingSelectedReason || 'custom' })
            })
            .then(function(r) { return r.json().then(function(j) { return { ok: r.ok, body: j }; }); })
            .then(function(res) {
                if (res.ok && res.body.success) {
                    window.closePingSheet();
                    var n = res.body.recipients || 0;
                    var tpl = I18N.ping_sent_fmt || '📣 {n}';
                    if (typeof window.showToast === 'function') window.showToast(tpl.replace('{n}', n), 'success');
                    startPingCooldown(res.body.cooldown || 30);
                    pollChat(true);
                } else {
                    var reason = res.body && res.body.reason;
                    var msg = (res.body && res.body.error) || I18N.ping_error;
                    if (reason === 'cooldown' && res.body.remaining) startPingCooldown(res.body.remaining);
                    if (typeof window.showToast === 'function') window.showToast(msg, 'error');
                    pingSendBtn.disabled = false;
                }
            })
            .catch(function() {
                if (typeof window.showToast === 'function') window.showToast(I18N.ping_error, 'error');
                pingSendBtn.disabled = false;
            });
        });
    }

    // ============================================================
    // COUNTDOWN
    // ============================================================
    function startCountdown(seconds) {
        if (countdownInterval) clearInterval(countdownInterval);
        var container = document.querySelector('.wr-scheduled-info');
        if (!container || seconds <= 0) return;
        var circle = document.getElementById('countdownCircle');
        var center = document.getElementById('countdownCenter');
        if (!circle || !center) return;
        var circumference = 283;
        var remaining = seconds, total = seconds;
        center.textContent = formatTime(remaining);
        circle.style.strokeDashoffset = circumference * (1 - remaining / total);
        countdownInterval = setInterval(function() {
            remaining--;
            center.textContent = formatTime(remaining);
            var progress = remaining / total;
            circle.style.strokeDashoffset = circumference * (1 - progress);
            if (remaining <= 60 && remaining > 55 && !notificationSent) {
                notificationSent = true;
                sendBrowserNotification(
                    NOTIFY_T60_TITLE,
                    '"' + shortTitle() + '"' + NOTIFY_T60_BODY_TAIL,
                    '/live-quiz/waiting-room/' + quizId,
                    'system'
                );
                playNotificationSound();
            }
            if (remaining <= 0) {
                clearInterval(countdownInterval);
                var alertEl = document.getElementById('quizStartedAlert');
                if (alertEl) alertEl.classList.add('show');
            }
        }, 1000);
    }
    function formatTime(seconds) {
        if (seconds < 0) return '00:00';
        var m = Math.floor(seconds / 60), s = seconds % 60;
        return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
    }
    if (initialStartsIn > 0) startCountdown(initialStartsIn);
    function playNotificationSound() {
        try {
            var ctx = new (window.AudioContext || window.webkitAudioContext)();
            var osc = ctx.createOscillator(), gain = ctx.createGain();
            osc.connect(gain); gain.connect(ctx.destination);
            osc.frequency.value = 880; osc.type = 'sine';
            gain.gain.setValueAtTime(0.3, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
            osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.3);
        } catch (e) {}
    }

    // ============================================================
    // PARTICIPANTS
    // ============================================================
    function fetchParticipants() {
        if (isPolling) return;
        isPolling = true;
        fetch('/live-quiz/waiting-room/participants/' + quizId + '?t=' + Date.now())
            .then(function(r) { return r.json(); })
            .then(function(data) {
                renderParticipants(data.participants);
                updateCounts(data);
                isPolling = false;
            })
            .catch(function() { isPolling = false; });
    }
    function renderParticipants(participants) {
        participantList = participants || [];
        if (!participantListEl) return;
        if (participantList.length === 0) {
            participantListEl.innerHTML =
                '<div class="wr-empty-participants"><div class="wr-empty-icon">👥</div>' +
                '<span>' + I18N.no_participants + '</span></div>';
            return;
        }
        var html = '';
        participantList.forEach(function(p) {
            var isYou = p.student_id == userId;
            var isP = p.is_creator;
            var isLeft = p.status === 'left';
            var isReady = (isP && p.status !== 'left') ? true : p.is_ready;
            var badge = '';
            if (isP) badge += '<span class="wr-badge creator">' + I18N.badge_creator + '</span>';
            if (isYou) badge += '<span class="wr-badge you">' + I18N.badge_you + '</span>';
            if (isLeft) badge += '<span class="wr-badge left">' + I18N.badge_left + '</span>';
            var readyHand = (isReady && !isLeft) ? '<span class="wr-ready-hand">✋</span>' : '';
            var statusDot = '<span class="wr-status-dot ' + (isLeft ? 'left' : 'active') + '"></span>';
            var itemClass = 'wr-participant-item';
            if (isLeft) itemClass += ' left';
            if (isReady && !isLeft) itemClass += ' ready';
            html += '<div class="' + itemClass + '">' + statusDot +
                    '<span class="wr-participant-name">' + (p.name || 'Unknown') + ' ' + badge + '</span>' +
                    '<span class="wr-participant-id">#' + (p.public_id || '----') + '</span>' +
                    readyHand + '</div>';
        });
        participantListEl.innerHTML = html;
    }
    function updateCounts(data) {
        var activeCount = (typeof data === 'number') ? data : (data && data.count) || 0;
        if (participantCountEl) participantCountEl.textContent = activeCount;
        if (participantCountLabel) participantCountLabel.textContent = activeCount + ' joined';
        if (tabParticipantsCount) tabParticipantsCount.textContent = activeCount;
        if (waitingCountEl) waitingCountEl.textContent = activeCount;
        if (isCreator) {
            if (statusMsg) {
                if (activeCount >= 2) {
                    statusMsg.innerHTML = I18N.status_ready_html.replace('{n}', activeCount);
                } else {
                    var needHtml = I18N.status_need_html;
                    if (activeCount === 1) needHtml += I18N.status_share_hint_html;
                    statusMsg.innerHTML = needHtml;
                }
            }
            if (startBtn) startBtn.disabled = activeCount < 2;
        }
    }
    function toggleReady() {
        if (isCreator) return;
        var btn = document.querySelector('.ready-btn');
        if (!btn) return;
        var isActive = btn.classList.contains('active');
        var newState = !isActive;
        fetch('/live-quiz/toggle-ready/' + quizId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({ is_ready: newState })
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) btn.classList.toggle('active', newState);
            else if (typeof window.showToast === 'function') window.showToast(I18N.ready_updated_err, 'error');
        })
        .catch(function() {
            if (typeof window.showToast === 'function') window.showToast(I18N.ready_updated_err, 'error');
        });
    }
    function startQuiz() {
        if (!isCreator || startBtn.disabled) return;
        startBtn.disabled = true;
        startBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ' + I18N.starting;
        fetch('/live-quiz/start/' + quizId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({})
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) {
                isRedirecting = true;
                quizExiting = true;
                window.location.replace('/live-quiz/play/' + quizId);
            } else {
                if (typeof window.showToast === 'function') window.showToast(I18N.start_error + ' ' + (data.error || ''), 'error');
                startBtn.disabled = false;
                startBtn.innerHTML = '<i class="fas fa-play"></i> ' + I18N.start_btn;
            }
        })
        .catch(function() {
            if (typeof window.showToast === 'function') window.showToast(I18N.start_error, 'error');
            startBtn.disabled = false;
            startBtn.innerHTML = '<i class="fas fa-play"></i> ' + I18N.start_btn;
        });
    }
    function leaveQuiz() {
        if (!confirm(I18N.leave_confirm)) return;
        if (leaveBtn) {
            leaveBtn.disabled = true;
            leaveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ' + I18N.leaving;
        }
        fetch('/live-quiz/leave/' + quizId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({})
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) {
                quizExiting = true;
                window.location.href = data.redirect || '/live-quiz/lobby';
            } else {
                if (typeof window.showToast === 'function') window.showToast(I18N.leave_error + ' ' + (data.error || ''), 'error');
                if (leaveBtn) {
                    leaveBtn.disabled = false;
                    leaveBtn.innerHTML = '<i class="fas fa-sign-out-alt"></i> ' + I18N.leave_btn;
                }
            }
        })
        .catch(function() {
            if (typeof window.showToast === 'function') window.showToast(I18N.leave_error, 'error');
            if (leaveBtn) {
                leaveBtn.disabled = false;
                leaveBtn.innerHTML = '<i class="fas fa-sign-out-alt"></i> ' + I18N.leave_btn;
            }
        });
    }
    function rejoinQuiz() {
        if (rejoinBtn) {
            rejoinBtn.disabled = true;
            rejoinBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ' + I18N.rejoining;
        }
        fetch('/live-quiz/rejoin/' + quizId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({})
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) window.location.href = data.redirect || '/live-quiz/waiting-room/' + quizId;
            else {
                if (typeof window.showToast === 'function') window.showToast(I18N.rejoin_error + ' ' + (data.error || ''), 'error');
                if (rejoinBtn) {
                    rejoinBtn.disabled = false;
                    rejoinBtn.innerHTML = '<i class="fas fa-undo"></i> ' + I18N.rejoin_btn;
                }
            }
        })
        .catch(function() {
            if (typeof window.showToast === 'function') window.showToast(I18N.rejoin_error, 'error');
            if (rejoinBtn) {
                rejoinBtn.disabled = false;
                rejoinBtn.innerHTML = '<i class="fas fa-undo"></i> ' + I18N.rejoin_btn;
            }
        });
    }

    // ============================================================
    // STATE POLL — also handles quiz cancellation
    // ============================================================
    function pollQuizState() {
        fetch('/live-quiz/quiz-state/' + quizId + '?t=' + Date.now())
            .then(function(response) {
                // 404 means the quiz was deleted by a super admin.
                if (response.status === 404) {
                    if (!isRedirecting) {
                        isRedirecting = true;
                        quizExiting = true;
                        try {
                            if (typeof window.showToast === 'function') {
                                window.showToast(I18N.cancelled_toast, 'info');
                            }
                        } catch (e) {}
                        window.location.replace('/live-quiz/lobby');
                    }
                    return null;
                }
                return response.json();
            })
            .then(function(data) {
                if (!data) return;
                if (data.redirect_url && !isRedirecting) {
                    isRedirecting = true;
                    quizExiting = true;
                    window.location.replace(data.redirect_url);
                    return;
                }
                if (data.status === 'active' && !isRedirecting) {
                    isRedirecting = true;
                    quizExiting = true;
                    var alertEl = document.getElementById('quizStartedAlert');
                    if (alertEl) alertEl.classList.add('show');
                    window.location.replace('/live-quiz/play/' + quizId);
                    return;
                }
                if (data.status === 'scheduled' && data.starts_in_seconds !== undefined) {
                    if (data.starts_in_seconds > 0 && data.starts_in_seconds !== initialStartsIn) {
                        initialStartsIn = data.starts_in_seconds;
                        startCountdown(initialStartsIn);
                    }
                }
                if (data.status === 'scheduled' && initialStartsIn > 0 && !countdownInterval) {
                    startCountdown(initialStartsIn);
                }
                setTimeout(pollQuizState, 3000);
            })
            .catch(function() {
                setTimeout(pollQuizState, 5000);
            });
    }
    function forceRedirectIfNeeded() {
        if (isRedirecting) return;
        var alert = document.getElementById('quizStartedAlert');
        if (alert && alert.classList.contains('show') && !isRedirecting) {
            quizExiting = true;
            window.location.replace('/live-quiz/play/' + quizId);
        }
    }
    setInterval(forceRedirectIfNeeded, 2000);

    // ============================================================
    // BOOT
    // ============================================================
    fetchParticipants();
    pollTimer = setInterval(fetchParticipants, 2000);
    pollQuizState();
    pollChat();
    if (startBtn) startBtn.addEventListener('click', startQuiz);
    if (leaveBtn) leaveBtn.addEventListener('click', leaveQuiz);
    if (rejoinBtn) rejoinBtn.addEventListener('click', rejoinQuiz);

    if (!isCreator) {
        var actionsDiv = document.querySelector('.wr-actions');
        if (actionsDiv && !document.querySelector('.ready-btn')) {
            var readyBtn = document.createElement('button');
            readyBtn.className = 'ready-btn';
            readyBtn.innerHTML = '<span class="hand-icon">✋</span><span class="check-badge">✓</span>';
            readyBtn.title = I18N.ready_tooltip;
            readyBtn.addEventListener('click', toggleReady);
            var waitingMsg = actionsDiv.querySelector('.wr-waiting-message');
            if (waitingMsg) actionsDiv.insertBefore(readyBtn, waitingMsg);
            else actionsDiv.prepend(readyBtn);
        }
    }

    window.addEventListener('beforeunload', function() {
        if (pollTimer) clearInterval(pollTimer);
        if (countdownInterval) clearInterval(countdownInterval);
        if (chatPollTimer) clearTimeout(chatPollTimer);
    });
});