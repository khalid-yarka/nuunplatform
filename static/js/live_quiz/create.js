/* ============================================================
   static/js/live_quiz/create.js
   ============================================================
   Extracted verbatim from templates/dashboard/live_quiz/create.html.
   Bridge objects expected on window:
     window.__LQ        — abandonIsCreator, createUrl
     window.__LQ_I18N   — currently unused (create page has no _() in
                          the JS — all strings are inline in HTML)
   ============================================================ */

document.addEventListener('DOMContentLoaded', function() {
    var titleInput = document.getElementById('quizTitleInput');
    var titleError = document.getElementById('titleError');
    var submitBtn = document.querySelector('#createForm button[type="submit"]');
    var titleTouched = false;

    function validateTitle() {
        if (!titleInput) return true;
        var v = (titleInput.value || '').trim();
        var ok = v.length >= 3 && v.length <= 100;
        if (titleError) titleError.style.display = (!ok && titleTouched) ? 'block' : 'none';
        titleInput.style.borderColor = (!ok && titleTouched) ? '#EF4444' : 'var(--border)';
        if (submitBtn) submitBtn.disabled = !ok;
        return ok;
    }

    if (titleInput) {
        titleInput.addEventListener('input', function() { titleTouched = true; validateTitle(); });
        titleInput.addEventListener('blur', function() { titleTouched = true; validateTitle(); });
        validateTitle();
    }

    var createForm = document.getElementById('createForm');
    if (createForm) {
        createForm.addEventListener('submit', function(e) {
            titleTouched = true;
            if (!validateTitle()) {
                e.preventDefault();
                if (titleInput) titleInput.focus();
            }
        });
    }

    // ─── GRADE PILLS ───
    var gradePills = document.querySelectorAll('.grade-pill');
    gradePills.forEach(function(pill) {
        pill.addEventListener('click', function() {
            gradePills.forEach(function(p) { p.classList.remove('active'); });
            pill.classList.add('active');
            var radio = pill.querySelector('input[type="radio"]');
            if (radio) radio.checked = true;
            refreshHint();
        });
    });

    // ─── QUESTION COUNT ───
    document.querySelectorAll('.question-count-options label').forEach(function(label) {
        label.addEventListener('click', function() {
            var siblings = this.parentElement.querySelectorAll('label');
            siblings.forEach(function(s) { s.classList.remove('selected'); });
            this.classList.add('selected');
            var radio = this.querySelector('input[type="radio"]');
            if (radio) radio.checked = true;
        });
    });

    // ─── PRIVACY ───
    var publicLabel = document.getElementById('publicLabel');
    var privateLabel = document.getElementById('privateLabel');
    var publicRadio = document.querySelector('input[name="is_public"][value="1"]');
    var privateRadio = document.querySelector('input[name="is_public"][value="0"]');
    function updatePrivacySelection() {
        if (publicRadio.checked) { publicLabel.classList.add('selected'); privateLabel.classList.remove('selected'); }
        else { privateLabel.classList.add('selected'); publicLabel.classList.remove('selected'); }
    }
    publicLabel.addEventListener('click', function() { publicRadio.checked = true; updatePrivacySelection(); });
    privateLabel.addEventListener('click', function() { privateRadio.checked = true; updatePrivacySelection(); });
    updatePrivacySelection();

    // ─── SCHEDULE ───
    var scheduleLabels = document.querySelectorAll('#scheduleOptions label');
    scheduleLabels.forEach(function(label) {
        label.addEventListener('click', function() {
            scheduleLabels.forEach(function(l) { l.classList.remove('selected'); });
            this.classList.add('selected');
            var radio = this.querySelector('input[type="radio"]');
            if (radio) radio.checked = true;
        });
    });

    // ─── AVAILABILITY HINT — matches the create engine ───
    // get_questions_for_subject → get_questions_by_subject, which falls
    // back to all grades when the exact grade is empty. The hint does
    // the same thing: reports the exact count, and when that's zero
    // but the subject has questions in other grades, says so.

    var gradeHintEl = document.getElementById('gradeHint');
    var subjectSelect = document.getElementById('subjectSelect');

    var GRADE_LABELS = {
        'F4': 'Form 4',
        'F3': 'Form 3',
        'G8': 'Grade 8',
        'G7': 'Grade 7'
    };

    function currentGrade() {
        var checked = document.querySelector('input[name="grade"]:checked');
        return checked ? checked.value : 'F4';
    }

    function currentSubjectLabel() {
        if (!subjectSelect || subjectSelect.selectedIndex < 0) return '';
        var opt = subjectSelect.options[subjectSelect.selectedIndex];
        return (opt.text || '').replace(/^[^\w]+/, '').trim() || opt.value;
    }

    function setHint(state, html) {
        if (!gradeHintEl) return;
        gradeHintEl.className = 'grade-hint ' + (state || '');
        gradeHintEl.innerHTML = html;
    }

    var hintTimer = null;
    var hintSeq = 0;

    function refreshHint() {
        clearTimeout(hintTimer);
        var mySeq = ++hintSeq;

        var subject = subjectSelect ? subjectSelect.value : '';
        var grade = currentGrade();
        var gradeLabel = GRADE_LABELS[grade] || grade;
        var subjectLabel = currentSubjectLabel();

        if (!subject) {
            setHint('', '<i class="fas fa-info-circle"></i><span>Pick a subject to continue</span>');
            return;
        }

        setHint('', '<i class="fas fa-circle-notch fa-spin"></i><span>Checking ' +
                gradeLabel + ' · ' + subjectLabel + '…</span>');

        hintTimer = setTimeout(function() {
            var url = '/live-quiz/available-count'
                    + '?subject=' + encodeURIComponent(subject)
                    + '&grade=' + encodeURIComponent(grade);
            fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin',
            })
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (mySeq !== hintSeq) return;

                if (!data) {
                    setHint('info',
                        '<i class="fas fa-info-circle"></i>' +
                        '<span>Could not check availability for <strong>' +
                        gradeLabel + '</strong> · <strong>' + subjectLabel + '</strong></span>');
                    return;
                }

                if (data.error) {
                    setHint('err',
                        '<i class="fas fa-triangle-exclamation"></i>' +
                        '<span>Server error: <code>' +
                        String(data.error).slice(0, 120) + '</code></span>');
                    return;
                }

                var exact = (typeof data.count === 'number') ? data.count : 0;
                var fallback = (typeof data.total_subject === 'number') ? data.total_subject : 0;

                if (exact >= 20) {
                    setHint('ok',
                        '<i class="fas fa-check-circle"></i>' +
                        '<span>Plenty of <strong>' + gradeLabel + '</strong> questions ready</span>');
                    return;
                }
                if (exact >= 10) {
                    setHint('ok',
                        '<i class="fas fa-check-circle"></i>' +
                        '<span>Good selection — <strong>' + exact + '</strong> ' +
                        gradeLabel + ' questions</span>');
                    return;
                }
                if (exact >= 5) {
                    setHint('ok',
                        '<i class="fas fa-check-circle"></i>' +
                        '<span><strong>' + exact + '</strong> ' + gradeLabel +
                        ' questions ready</span>');
                    return;
                }
                if (exact > 0) {
                    setHint('warn',
                        '<i class="fas fa-exclamation-triangle"></i>' +
                        '<span>Only <strong>' + exact + '</strong> ' + gradeLabel +
                        ' — pick a smaller count</span>');
                    return;
                }

                if (fallback > 0) {
                    setHint('info',
                        '<i class="fas fa-circle-info"></i>' +
                        '<span>No <strong>' + gradeLabel + '</strong> questions in <strong>' +
                        subjectLabel + '</strong>. The quiz will use <strong>' +
                        fallback + '</strong> from other grades.</span>');
                    return;
                }

                setHint('err',
                    '<i class="fas fa-times-circle"></i>' +
                    '<span>No questions in <strong>' + subjectLabel +
                    '</strong> yet</span>');
            })
            .catch(function() {
                if (mySeq !== hintSeq) return;
                setHint('info',
                    '<i class="fas fa-info-circle"></i>' +
                    '<span>Could not check availability for <strong>' +
                    gradeLabel + '</strong> · <strong>' + subjectLabel + '</strong></span>');
            });
        }, 200);
    }

    if (subjectSelect) subjectSelect.addEventListener('change', refreshHint);
    refreshHint();

    // ─── ACTIVE QUIZ GUARD ───
    var abandonBtn = document.getElementById('qgAbandonBtn');
    if (abandonBtn) {
        abandonBtn.addEventListener('click', function() {
            var quizId = this.getAttribute('data-quiz-id');
            if (!quizId) return;
            var isCreator = window.__LQ.abandonIsCreator;
            var warning = isCreator
                ? 'This will soft-close your current quiz and mark all its participants as left.\n\nContinue?'
                : 'This will leave the quiz you are currently in.\n\nContinue?';
            if (!window.confirm(warning)) return;

            this.disabled = true;
            var originalHTML = this.innerHTML;
            this.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing…';

            var csrf = document.querySelector('meta[name="csrf-token"]');
            var token = csrf ? csrf.content : '';

            fetch('/live-quiz/abandon/' + quizId, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
                body: JSON.stringify({}),
            })
            .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
            .then(function(resp) {
                if (resp.ok && resp.data && resp.data.success) {
                    window.location.href = resp.data.redirect || window.__LQ.createUrl;
                } else {
                    alert((resp.data && resp.data.error) || 'Could not process the request.');
                    abandonBtn.disabled = false;
                    abandonBtn.innerHTML = originalHTML;
                }
            })
            .catch(function() {
                alert('Network error. Please try again.');
                abandonBtn.disabled = false;
                abandonBtn.innerHTML = originalHTML;
            });
        });
    }
});