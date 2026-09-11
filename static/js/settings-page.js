// static/js/settings-page.js
document.addEventListener('DOMContentLoaded', function() {
    const navItems = document.querySelectorAll('.settings-nav-item');
    const sections = document.querySelectorAll('.settings-section');
    const pageTitle = document.querySelector('.settings-page-title');

    function showSection(id) {
        sections.forEach(sec => sec.classList.remove('active'));
        const target = document.getElementById(id);
        if (target) target.classList.add('active');
        navItems.forEach(item => item.classList.remove('active'));
        const nav = document.querySelector(`.settings-nav-item[data-section="${id}"]`);
        if (nav) nav.classList.add('active');
        history.pushState(null, '', '#' + id);
        if (pageTitle) {
            const label = nav ? nav.textContent.trim() : 'Settings';
            pageTitle.textContent = label;
        }
    }

    let initialHash = window.location.hash.replace('#', '') || 'appearance';
    if (!document.getElementById(initialHash)) initialHash = 'appearance';
    showSection(initialHash);

    navItems.forEach(item => {
        item.addEventListener('click', function(e) {
            e.preventDefault();
            const section = this.dataset.section;
            if (section) showSection(section);
        });
    });

    function showToast(msg, type) {
        if (typeof window.showToast === 'function') {
            window.showToast(msg, type);
        } else {
            alert(msg);
        }
    }

    function submitForm(url, data, successMsg) {
        fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || ''
            },
            body: JSON.stringify(data)
        })
        .then(response => response.json())
        .then(result => {
            if (result.success) {
                showToast(result.message || successMsg || 'Saved!', 'success');
                if (result.settings) {
                    NuunSettings.settings = result.settings;
                    NuunSettings.applyAll();
                }
            } else {
                let msg = result.error || 'Error saving.';
                if (result.errors) {
                    msg = Object.values(result.errors).join(' ');
                }
                showToast(msg, 'error');
            }
        })
        .catch(() => showToast('Network error.', 'error'));
    }

    // Appearance form
    const appearanceForm = document.getElementById('appearance-form');
    if (appearanceForm) {
        appearanceForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const data = {
                'appearance.theme': document.querySelector('.theme-option.active')?.dataset.theme || 'system',
                'appearance.accent': document.querySelector('.accent-option.active')?.dataset.accent || 'red',
                'appearance.font_size': document.getElementById('font_size')?.value || 'medium',
                'appearance.compact_mode': document.getElementById('compact_mode')?.checked || false
            };
            submitForm('/settings/api', data, 'Appearance updated!');
        });
    }

    // Quiz form
    const quizForm = document.getElementById('quiz-prefs-form');
    if (quizForm) {
        quizForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const data = {
                'quiz.default_question_count': parseInt(document.getElementById('default_question_count').value),
                'quiz.default_difficulty': parseInt(document.getElementById('default_difficulty').value),
                'quiz.default_subject': document.getElementById('default_subject').value,
                'quiz.show_correct_immediately': document.getElementById('show_correct_immediately').checked,
                'quiz.skip_rating_after_quiz': document.getElementById('skip_rating_after_quiz').checked,
                'quiz.auto_skip_enabled': document.getElementById('auto_skip_enabled').checked
            };
            submitForm('/settings/api', data, 'Quiz preferences saved!');
        });
    }

    // Notifications form
    const notifForm = document.getElementById('notif-form');
    if (notifForm) {
        notifForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const toggles = notifForm.querySelectorAll('.toggle-switch input');
            const data = {};
            toggles.forEach(t => {
                data[t.id.replace('notify_', 'notifications.')] = t.checked;
            });
            submitForm('/settings/api', data, 'Notification preferences saved!');
        });
    }

    // Privacy form
    const privacyForm = document.getElementById('privacy-form');
    if (privacyForm) {
        privacyForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const data = {
                'privacy.show_on_leaderboard': document.getElementById('show_on_leaderboard').checked,
                'privacy.show_public_id': document.getElementById('show_public_id').checked,
                'privacy.show_statistics': document.getElementById('show_statistics').checked
            };
            submitForm('/settings/api', data, 'Privacy settings saved!');
        });
    }

    // Live Quiz form
    const liveQuizForm = document.getElementById('livequiz-form');
    if (liveQuizForm) {
        liveQuizForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const data = {
                'live_quiz.default_time_per_question': parseInt(document.getElementById('default_time_per_question').value),
                'live_quiz.default_max_participants': parseInt(document.getElementById('default_max_participants').value),
                'live_quiz.default_privacy': parseInt(document.querySelector('input[name="default_privacy"]:checked').value)
            };
            submitForm('/settings/api', data, 'Live quiz defaults saved!');
        });
    }

    // Password form
    const passwordForm = document.getElementById('password-form');
    if (passwordForm) {
        passwordForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const current = document.getElementById('current_password').value;
            const newPw = document.getElementById('new_password').value;
            const confirm = document.getElementById('confirm_password').value;
            if (newPw !== confirm) {
                showToast('Passwords do not match.', 'error');
                return;
            }
            if (newPw.length < 8) {
                showToast('New password must be at least 8 characters.', 'error');
                return;
            }
            if (!confirm('Change your password? You will be logged out.')) return;
            fetch('/settings/api/password', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || ''
                },
                body: JSON.stringify({ current_password: current, new_password: newPw, confirm_password: confirm })
            })
            .then(response => response.json())
            .then(result => {
                if (result.success) {
                    showToast('Password changed. Please log in again.', 'success');
                    setTimeout(() => window.location.href = '/logout', 2000);
                } else {
                    showToast(result.error || 'Error changing password.', 'error');
                }
            })
            .catch(() => showToast('Network error.', 'error'));
        });
    }

    // Theme selector preview
    const themeOptions = document.querySelectorAll('.theme-option');
    themeOptions.forEach(opt => {
        opt.addEventListener('click', function() {
            themeOptions.forEach(o => o.classList.remove('active'));
            this.classList.add('active');
            const theme = this.dataset.theme;
            if (typeof NuunSettings !== 'undefined') {
                NuunSettings.applyTheme(theme);
            }
        });
    });

    // Accent selector preview
    const accentOptions = document.querySelectorAll('.accent-option');
    accentOptions.forEach(btn => {
        btn.addEventListener('click', function() {
            accentOptions.forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            const accent = this.dataset.accent;
            if (typeof NuunSettings !== 'undefined') {
                NuunSettings.applyAccent(accent);
            }
        });
    });

    // Compact mode preview
    const compactToggle = document.getElementById('compact_mode');
    if (compactToggle) {
        compactToggle.addEventListener('change', function() {
            if (typeof NuunSettings !== 'undefined') {
                NuunSettings.applyCompactMode(this.checked);
            }
        });
    }

    // Unsaved changes
    let unsaved = false;
    document.querySelectorAll('.settings-form input, .settings-form select').forEach(el => {
        el.addEventListener('change', () => unsaved = true);
    });
    window.addEventListener('beforeunload', function(e) {
        if (unsaved) {
            e.preventDefault();
            e.returnValue = 'You have unsaved changes.';
        }
    });
    document.querySelectorAll('.settings-form').forEach(form => {
        form.addEventListener('submit', () => unsaved = false);
    });

    // Upgrade buttons for locked features
    document.querySelectorAll('.upgrade-btn').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const feature = this.dataset.feature;
            const requiredTier = this.dataset.requiredTier || 'dhexe';
            if (typeof window.openSafkaPreview === 'function') {
                window.openSafkaPreview({ feature: feature, requiredTier: requiredTier });
            }
        });
    });

    // Locked toggles
    document.querySelectorAll('.toggle-item.locked input[type="checkbox"]').forEach(cb => {
        cb.disabled = true;
        cb.closest('.toggle-item').addEventListener('click', function(e) {
            if (e.target.tagName !== 'INPUT') {
                const upgradeBtn = this.querySelector('.upgrade-btn');
                if (upgradeBtn) upgradeBtn.click();
            }
        });
    });
});