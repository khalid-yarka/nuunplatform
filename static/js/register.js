// ============================================
// REGISTRATION — MULTI-STEP WIZARD
// ============================================

document.addEventListener('DOMContentLoaded', function () {

    // ---------- DOM ----------
    const form             = document.getElementById('registerForm');
    const panels           = form.querySelectorAll('.wizard-panel');
    const stepIndicators   = document.querySelectorAll('.wizard-step');
    const stepLines        = document.querySelectorAll('.wizard-step-line .line-fill');
    const backBtn          = document.getElementById('backBtn');
    const nextBtn          = document.getElementById('nextBtn');
    const submitBtn        = document.getElementById('submitBtn');

    const phone            = document.getElementById('phone');
    const phoneStatus      = document.getElementById('phoneStatus');
    const phoneTakenNotice = document.getElementById('phoneTakenNotice');

    const password         = document.getElementById('password');
    const confirmPassword  = document.getElementById('confirmPassword');
    const firstName        = document.getElementById('firstName');
    const middleName       = document.getElementById('middleName');
    const lastName         = document.getElementById('lastName');

    const curriculumGroup  = document.getElementById('curriculumGroup');
    const city             = document.getElementById('city');

    const schoolInput      = document.getElementById('schoolInput');
    const schoolSuggestions = document.getElementById('schoolSuggestions');

    const gradeRadios      = form.querySelectorAll('input[name="grade"]');
    const passwordStrength = document.getElementById('passwordStrength');

    const TOTAL_STEPS = 5;
    let currentStep = 1;

    // ---------- State ----------
    const fieldStates = {
        phone: false, password: false, confirm: false,
        firstName: false, middleName: false, lastName: false,
        location: false, curriculum: true, city: false,
        school: false, grade: false,
    };

    let phoneTaken      = false;
    let formLocked      = false;
    let isSubmitting    = false;
    let phoneCheckTimer = null;
    let lastCheckedPhone = '';

    const LOCATION_LABELS = { 'SO': 'Somalia', 'PL': 'Puntland', 'SL': 'Somaliland' };
    const CURRICULUM_LABELS = { 'general': 'General', 'science': 'Science', 'arts': 'Arts' };
    const GRADE_LABELS = { 'G7': 'Grade 7', 'G8': 'Grade 8', 'F3': 'Form 3', 'F4': 'Form 4' };

    // ============================================
    // LOCK / UNLOCK
    // ============================================

    function lockForm() {
        if (formLocked) return;
        formLocked = true;
        form.classList.add('form-locked');
        form.querySelectorAll('input, select, button').forEach(function (el) {
            if (el.name === 'csrf_token') return;
            if (el === phone) return;
            if (el.type === 'hidden') return;
            el.disabled = true;
        });
        document.querySelectorAll('.card-option, .card-option input').forEach(function (el) {
            el.style.pointerEvents = 'none';
        });
    }

    function unlockForm() {
        if (!formLocked) return;
        formLocked = false;
        form.classList.remove('form-locked');
        form.querySelectorAll('input, select, button').forEach(function (el) {
            if (el.name === 'csrf_token') return;
            el.disabled = false;
        });
        document.querySelectorAll('.card-option, .card-option input').forEach(function (el) {
            el.style.pointerEvents = '';
        });
    }

    // ============================================
    // HELPERS
    // ============================================

    function showErrorState(element, errorEl, valid, message) {
        const wrapper = element.closest('.input-wrapper');
        if (!wrapper) return;
        if (!valid) {
            wrapper.classList.add('error');
            if (errorEl) { errorEl.textContent = message; errorEl.classList.add('visible'); }
        } else {
            wrapper.classList.remove('error');
            if (errorEl) errorEl.classList.remove('visible');
        }
    }

    function getSelectedLocation() {
        const r = form.querySelector('input[name="location"]:checked');
        return r ? r.value : '';
    }

    function getSelectedCurriculum() {
        const r = form.querySelector('input[name="curriculum"]:checked');
        return r ? r.value : '';
    }

    function getSelectedGrade() {
        const r = form.querySelector('input[name="grade"]:checked');
        return r ? r.value : '';
    }

    // ============================================
    // VALIDATORS
    // ============================================

    function validatePhone() {
        const val = phone.value.replace(/\D/g, '');
        const valid = val.length === 9;
        showErrorState(phone, document.getElementById('phoneError'), valid,
                       'Must be exactly 9 digits (e.g., 612345678)');
        fieldStates.phone = valid;

        if (valid) {
            schedulePhoneCheck(val);
        } else {
            phoneTaken = false;
            phoneTakenNotice.style.display = 'none';
            phoneStatus.innerHTML = '';
            phoneStatus.className = 'input-status';
            unlockForm();
        }
        return valid;
    }

    function validatePassword() {
        const val = password.value;
        const valid = val.length >= 8;
        showErrorState(password, document.getElementById('passwordError'), valid, 'Minimum 8 characters');
        fieldStates.password = valid;
        updatePasswordStrength(val);
        if (confirmPassword.value.length > 0) validateConfirm();
        return valid;
    }

    function validateConfirm() {
        const valid = confirmPassword.value === password.value && password.value.length >= 8;
        showErrorState(confirmPassword, document.getElementById('confirmError'), valid, 'Passwords do not match');
        fieldStates.confirm = valid;
        return valid;
    }

    function validateFirstName() {
        const val = firstName.value.trim();
        const valid = val.length >= 4 && /^[A-Za-z]+$/.test(val);
        showErrorState(firstName, document.getElementById('firstNameError'), valid,
                       'Must be at least 4 letters, no numbers');
        fieldStates.firstName = valid;
        return valid;
    }

    function validateMiddleName() {
        const val = middleName.value.trim();
        const valid = val.length >= 4 && /^[A-Za-z]+$/.test(val);
        showErrorState(middleName, document.getElementById('middleNameError'), valid,
                       'Must be at least 4 letters, no numbers');
        fieldStates.middleName = valid;
        return valid;
    }

    function validateLastName() {
        const val = lastName.value.trim();
        const valid = val.length >= 4 && /^[A-Za-z]+$/.test(val);
        showErrorState(lastName, document.getElementById('lastNameError'), valid,
                       'Must be at least 4 letters, no numbers');
        fieldStates.lastName = valid;
        return valid;
    }

    function validateLocation() {
        const selected = form.querySelector('input[name="location"]:checked');
        const valid = !!selected;
        const errorEl = document.getElementById('locationError');
        if (valid) errorEl.classList.remove('visible');
        else errorEl.classList.add('visible');
        fieldStates.location = valid;
        return valid;
    }

    function validateCurriculum() {
        const selectedLoc = form.querySelector('input[name="location"]:checked');
        const isPL = selectedLoc && selectedLoc.value === 'PL';
        const errorEl = document.getElementById('curriculumError');
        if (!isPL) {
            fieldStates.curriculum = true;
            errorEl.classList.remove('visible');
            return true;
        }
        const selected = form.querySelector('input[name="curriculum"]:checked');
        const valid = !!selected;
        if (valid) errorEl.classList.remove('visible');
        else errorEl.classList.add('visible');
        fieldStates.curriculum = valid;
        return valid;
    }

    function validateCity() {
        const val = city.value.trim();
        const valid = val.length >= 5 && /^[A-Za-z\s]+$/.test(val);
        showErrorState(city, document.getElementById('cityError'), valid,
                       'Must be at least 5 letters, only letters');
        fieldStates.city = valid;
        return valid;
    }

    function validateSchool() {
        const val = schoolInput.value.trim();
        const words = val.split(/\s+/).filter(w => w.length > 0);
        const valid = words.length >= 2;
        showErrorState(schoolInput, document.getElementById('schoolError'),
                       valid, 'Enter at least two words');
        fieldStates.school = valid;
        return valid;
    }

    function validateGrade() {
        const checked = form.querySelector('input[name="grade"]:checked');
        const valid = !!checked;
        const errorEl = document.getElementById('gradeError');
        if (valid) errorEl.classList.remove('visible');
        else errorEl.classList.add('visible');
        fieldStates.grade = valid;
        return valid;
    }

    // ============================================
    // PHONE AJAX CHECK
    // ============================================

    function schedulePhoneCheck(digits) {
        if (phoneCheckTimer) clearTimeout(phoneCheckTimer);
        if (digits === lastCheckedPhone && !phoneTaken) return;
        phoneCheckTimer = setTimeout(function () { checkPhone(digits); }, 400);
    }

    function checkPhone(digits) {
        lastCheckedPhone = digits;
        phoneStatus.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>';
        phoneStatus.className = 'input-status checking';

        const csrf = document.querySelector('input[name="csrf_token"]')?.value || '';

        fetch('/auth/check-phone', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: JSON.stringify({ phone: digits })
        })
        .then(r => r.json())
        .then(data => {
            if (data.taken) {
                phoneTaken = true;
                fieldStates.phone = false;
                phoneStatus.innerHTML = '<i class="fas fa-times-circle"></i>';
                phoneStatus.className = 'input-status taken';
                phoneTakenNotice.style.display = 'flex';
                document.getElementById('phoneError').classList.remove('visible');
                lockForm();
            } else {
                phoneTaken = false;
                phoneStatus.innerHTML = '<i class="fas fa-check-circle"></i>';
                phoneStatus.className = 'input-status ok';
                phoneTakenNotice.style.display = 'none';
                unlockForm();
            }
        })
        .catch(function () {
            phoneStatus.innerHTML = '';
            phoneStatus.className = 'input-status';
        });
    }

    // ============================================
    // SCHOOL AUTOCOMPLETE (DB-driven)
    // ============================================

    let schoolReqToken = 0;
    let schoolDebounce = null;

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function hideSchoolSuggestions() {
        if (schoolSuggestions) {
            schoolSuggestions.innerHTML = '';
            schoolSuggestions.classList.remove('visible');
        }
    }

    function renderSchoolSuggestions(list) {
        if (!list || !list.length) {
            hideSchoolSuggestions();
            return;
        }
        const html = list.map(function (s) {
            return '<div class="school-suggestion" data-value="' +
                   escapeHtml(s) + '">' + escapeHtml(s) + '</div>';
        }).join('');
        schoolSuggestions.innerHTML = html;
        schoolSuggestions.classList.add('visible');

        schoolSuggestions.querySelectorAll('.school-suggestion').forEach(function (el) {
            el.addEventListener('click', function () {
                schoolInput.value = this.dataset.value;
                hideSchoolSuggestions();
                validateSchool();
                schoolInput.focus();
            });
        });
    }

    function fetchSchoolSuggestions() {
        const q = (schoolInput.value || '').trim();
        const loc = getSelectedLocation();

        if (q.length < 3 || !loc) {
            hideSchoolSuggestions();
            return;
        }

        const token = ++schoolReqToken;

        fetch('/auth/school-suggestions?q=' +
              encodeURIComponent(q) +
              '&location=' + encodeURIComponent(loc))
            .then(r => r.json())
            .then(data => {
                if (token !== schoolReqToken) return;
                renderSchoolSuggestions(data.suggestions || []);
            })
            .catch(function () {
                if (token !== schoolReqToken) return;
                hideSchoolSuggestions();
            });
    }

    if (schoolInput) {
        schoolInput.addEventListener('input', function () {
            validateSchool();
            clearTimeout(schoolDebounce);
            schoolDebounce = setTimeout(fetchSchoolSuggestions, 300);
        });
        schoolInput.addEventListener('focus', function () {
            if ((schoolInput.value || '').trim().length >= 3) {
                fetchSchoolSuggestions();
            }
        });
        schoolInput.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') hideSchoolSuggestions();
        });
    }

    document.addEventListener('click', function (e) {
        if (!schoolSuggestions) return;
        if (!schoolSuggestions.contains(e.target) && e.target !== schoolInput) {
            hideSchoolSuggestions();
        }
    });

    // ============================================
    // STEP VALIDATION
    // ============================================

    function isStepValid(step) {
        if (formLocked) return false;
        switch (step) {
            case 1: return validatePhone() && validatePassword() && validateConfirm() && !phoneTaken;
            case 2: return validateFirstName() && validateMiddleName() && validateLastName();
            case 3: return validateLocation() && validateCurriculum() && validateCity();
            case 4: return validateSchool() && validateGrade();
            case 5: return true; // review panel — no inputs to validate
            default: return false;
        }
    }

    // ============================================
    // REVIEW POPULATION (Step 5)
    // ============================================

    function populateReview() {
        const phoneVal = (phone.value || '').replace(/\D/g, '');
        const loc = getSelectedLocation();
        const curr = getSelectedCurriculum();
        const grade = getSelectedGrade();

        const set = function (key, value) {
            const el = document.querySelector('[data-review="' + key + '"]');
            if (el) el.textContent = value && value.length ? value : '—';
        };

        set('phone', phoneVal ? '+252 ' + phoneVal : '');
        set('first_name', firstName.value.trim());
        set('middle_name', middleName.value.trim());
        set('last_name', lastName.value.trim());
        set('location', LOCATION_LABELS[loc] || loc || '');
        set('curriculum', curr ? (CURRICULUM_LABELS[curr] || curr) : '');
        set('city', city.value.trim());
        set('school', schoolInput.value.trim());
        set('grade', GRADE_LABELS[grade] || grade || '');

        // Hide the curriculum row when not Puntland
        const currRow = document.querySelector('[data-review-row="curriculum"]');
        if (currRow) {
            currRow.style.display = (loc === 'PL') ? '' : 'none';
        }
    }

    // ============================================
    // NAVIGATION
    // ============================================

    function showStep(step) {
        panels.forEach(function (p) {
            p.classList.toggle('active', parseInt(p.dataset.step, 10) === step);
        });
        stepIndicators.forEach(function (ind) {
            const s = parseInt(ind.dataset.step, 10);
            ind.classList.toggle('active', s === step);
            ind.classList.toggle('completed', s < step);
        });
        stepLines.forEach(function (line, i) {
            line.style.width = (step > i + 1) ? '100%' : '0%';
        });
        backBtn.style.display = (step === 1) ? 'none' : 'inline-flex';
        nextBtn.style.display = (step === TOTAL_STEPS) ? 'none' : 'inline-flex';
        submitBtn.style.display = (step === TOTAL_STEPS) ? 'inline-flex' : 'none';

        // Populate review when entering step 5
        if (step === TOTAL_STEPS) {
            populateReview();
        }

        setTimeout(function () {
            const first = form.querySelector(
                `.wizard-panel[data-step="${step}"] input:not([type="hidden"]):not([disabled])`
            );
            if (first) first.focus();
        }, 80);

        currentStep = step;
        const top = form.getBoundingClientRect().top + window.scrollY - 80;
        window.scrollTo({ top: top, behavior: 'smooth' });
    }

    function goNext() {
        if (!isStepValid(currentStep)) {
            const invalid = form.querySelector(
                `.wizard-panel[data-step="${currentStep}"] .input-wrapper.error input`
            );
            if (invalid) invalid.focus();
            return;
        }
        if (currentStep < TOTAL_STEPS) showStep(currentStep + 1);
    }

    function goBack() {
        if (currentStep > 1) showStep(currentStep - 1);
    }

    // Review "Edit" buttons jump back to their step
    document.querySelectorAll('.review-edit').forEach(function (btn) {
        btn.addEventListener('click', function () {
            const target = parseInt(this.dataset.backTo, 10);
            if (!isNaN(target)) showStep(target);
        });
    });

    // ============================================
    // LOCATION CHANGE
    // ============================================

    function onLocationChange() {
        const loc = getSelectedLocation();

        if (loc === 'PL') {
            curriculumGroup.classList.add('visible');
        } else {
            curriculumGroup.classList.remove('visible');
            const c = form.querySelector('input[name="curriculum"]:checked');
            if (c) c.checked = false;
        }

        // Location changed → the previously-entered school (and any
        // suggestions shown) are no longer scoped correctly.
        if (schoolInput.value.trim().length > 0) {
            schoolInput.value = '';
            fieldStates.school = false;
            const wrapper = schoolInput.closest('.input-wrapper');
            if (wrapper) wrapper.classList.remove('error');
            const errEl = document.getElementById('schoolError');
            if (errEl) errEl.classList.remove('visible');
        }
        hideSchoolSuggestions();

        validateLocation();
        validateCurriculum();
    }

    // ============================================
    // PASSWORD STRENGTH
    // ============================================

    function updatePasswordStrength(value) {
        if (!passwordStrength) return;
        const fill = passwordStrength.querySelector('.strength-fill');
        const text = passwordStrength.querySelector('.strength-text');
        if (!value) {
            fill.style.width = '0%';
            fill.className = 'strength-fill';
            text.textContent = '';
            return;
        }
        let score = 0;
        if (value.length >= 8) score++;
        if (value.length >= 12) score++;
        if (/[A-Z]/.test(value)) score++;
        if (/[a-z]/.test(value)) score++;
        if (/\d/.test(value)) score++;
        if (/[^A-Za-z0-9]/.test(value)) score++;

        let label, cls;
        if (score <= 2)      { label = 'Weak';   cls = 'weak'; }
        else if (score <= 4) { label = 'Fair';   cls = 'medium'; }
        else                 { label = 'Strong'; cls = 'strong'; }

        fill.className = 'strength-fill ' + cls;
        fill.style.width = (cls === 'weak' ? '33%' : cls === 'medium' ? '66%' : '100%');
        text.textContent = label;
        text.className = 'strength-text ' + cls;
    }

    // ============================================
    // IMMEDIATE ERROR CLEARING
    // ============================================

    function clearWhenValid(input, validator) {
        input.addEventListener('input', function () {
            const wrapper = input.closest('.input-wrapper');
            if (!wrapper || !wrapper.classList.contains('error')) return;
            validator();
        });
    }

    clearWhenValid(phone, validatePhone);
    clearWhenValid(password, validatePassword);
    clearWhenValid(confirmPassword, validateConfirm);
    clearWhenValid(firstName, validateFirstName);
    clearWhenValid(middleName, validateMiddleName);
    clearWhenValid(lastName, validateLastName);
    clearWhenValid(city, validateCity);
    clearWhenValid(schoolInput, validateSchool);

    // ============================================
    // PHONE INPUT — ALWAYS UNLOCK ON EDIT
    // ============================================

    phone.addEventListener('input', function () {
        this.value = this.value.replace(/\D/g, '').slice(0, 9);

        if (formLocked) unlockForm();

        if (this.value !== lastCheckedPhone) {
            phoneTaken = false;
            phoneTakenNotice.style.display = 'none';
            phoneStatus.innerHTML = '';
            phoneStatus.className = 'input-status';
        }
    });

    // ============================================
    // EVENT BINDINGS
    // ============================================

    phone.addEventListener('blur', validatePhone);
    password.addEventListener('blur', validatePassword);
    confirmPassword.addEventListener('blur', validateConfirm);
    firstName.addEventListener('blur', validateFirstName);
    middleName.addEventListener('blur', validateMiddleName);
    lastName.addEventListener('blur', validateLastName);
    city.addEventListener('blur', validateCity);
    schoolInput.addEventListener('blur', function () {
        // Delay so a suggestion click can fire first
        setTimeout(function () {
            validateSchool();
            hideSchoolSuggestions();
        }, 180);
    });
    gradeRadios.forEach(function (r) { r.addEventListener('change', validateGrade); });

    document.querySelectorAll('#locationCards input[type="radio"]').forEach(function (r) {
        r.addEventListener('change', onLocationChange);
    });
    document.querySelectorAll('#curriculumCards input[type="radio"]').forEach(function (r) {
        r.addEventListener('change', validateCurriculum);
    });

    nextBtn.addEventListener('click', goNext);
    backBtn.addEventListener('click', goBack);

    // ============================================
    // KEYBOARD
    // ============================================

    form.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') {
            if (currentStep < TOTAL_STEPS) { e.preventDefault(); goNext(); }
        }
        if (e.key === 'Escape' && currentStep > 1) { e.preventDefault(); goBack(); }
    });

    // ============================================
    // FINAL SUBMIT
    // ============================================

    form.addEventListener('submit', function (e) {
        if (isSubmitting) { e.preventDefault(); return; }
        if (formLocked) { e.preventDefault(); return; }

        for (let s = 1; s <= TOTAL_STEPS; s++) {
            if (!isStepValid(s)) {
                e.preventDefault();
                showStep(s);
                setTimeout(function () {
                    const el = form.querySelector(
                        `.wizard-panel[data-step="${s}"] .input-wrapper.error input`
                    );
                    if (el) el.focus();
                }, 120);
                return;
            }
        }

        isSubmitting = true;
        submitBtn.classList.add('submitting');
        submitBtn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Creating account...';

        setTimeout(function () {
            submitBtn.disabled = true;
        }, 0);

        setTimeout(function () {
            if (isSubmitting) {
                isSubmitting = false;
                submitBtn.classList.remove('submitting');
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="fas fa-rocket"></i> Create Account';
            }
        }, 8000);
    });

    // ============================================
    // INIT
    // ============================================

    showStep(1);
});

// ============================================
// GLOBAL — password toggle
// ============================================

function togglePassword(fieldId) {
    const input = document.getElementById(fieldId);
    const icon = document.getElementById(fieldId + 'Icon');
    if (input.type === 'password') {
        input.type = 'text';
        icon.className = 'fas fa-eye-slash';
    } else {
        input.type = 'password';
        icon.className = 'fas fa-eye';
    }
}