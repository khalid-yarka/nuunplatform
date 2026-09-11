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

    const schoolCards      = document.getElementById('schoolCards');
    const schoolInput      = document.getElementById('school');
    const schoolManual     = document.getElementById('schoolManual');
    const schoolManualInput= document.getElementById('schoolManualInput');

    const gradeRadios      = form.querySelectorAll('input[name="grade"]');
    const passwordStrength = document.getElementById('passwordStrength');

    const TOTAL_STEPS = 4;
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

    const schoolData = {
        'SO': ['Mogadishu Secondary School','Kismayo High School','Baidoa School','Jowhar Academy'],
        'PL': ['Garowe Secondary School','Bosaso High School','Galkayo School','Qardho Academy'],
        'SL': ['Sheikh Secondary School','Amoud School','Hargeisa High School','Burco Academy']
    };


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
        const val = schoolInput.value;
        if (val === 'manual') {
            const manual = schoolManualInput.value.trim();
            const words = manual.split(/\s+/).filter(w => w.length > 0);
            const valid = words.length >= 2 &&
                          words.every(w => w.length >= 4 && /^[A-Za-z]+$/.test(w));
            showErrorState(schoolManualInput, document.getElementById('schoolManualError'),
                           valid, 'Min 2 words, each 4+ letters, no numbers');
            document.getElementById('schoolError').classList.remove('visible');
            fieldStates.school = valid;
            return valid;
        }
        const valid = val !== '';
        const errorEl = document.getElementById('schoolError');
        if (valid) errorEl.classList.remove('visible');
        else errorEl.classList.add('visible');
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
    // STEP VALIDATION
    // ============================================

    function isStepValid(step) {
        if (formLocked) return false;
        switch (step) {
            case 1: return validatePhone() && validatePassword() && validateConfirm() && !phoneTaken;
            case 2: return validateFirstName() && validateMiddleName() && validateLastName();
            case 3: return validateLocation() && validateCurriculum() && validateCity();
            case 4: return validateSchool() && validateGrade();
            default: return false;
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


    // ============================================
    // LOCATION → CURRICULUM + SCHOOL
    // ============================================

    function onLocationChange() {
        const selected = form.querySelector('input[name="location"]:checked');
        const loc = selected ? selected.value : '';

        if (loc === 'PL') {
            curriculumGroup.classList.add('visible');
        } else {
            curriculumGroup.classList.remove('visible');
            const c = form.querySelector('input[name="curriculum"]:checked');
            if (c) c.checked = false;
        }

        buildSchoolCards(loc);
        validateLocation();
        validateCurriculum();
    }

    function buildSchoolCards(loc) {
        schoolCards.innerHTML = '';

        if (!loc || !schoolData[loc] || schoolData[loc].length === 0) {
            schoolCards.innerHTML =
                '<div class="card-empty"><i class="fas fa-arrow-up"></i><span>Select a location first</span></div>';
            schoolInput.value = '';
            return;
        }

        schoolData[loc].forEach(function (school) {
            const label = document.createElement('label');
            label.className = 'card-option';
            label.innerHTML =
                '<input type="radio" name="school_choice" value="' + school + '" />' +
                '<span class="card-face">' +
                    '<span class="card-icon"><i class="fas fa-school"></i></span>' +
                    '<span class="card-title">' + school + '</span>' +
                '</span>';
            schoolCards.appendChild(label);
            label.querySelector('input').addEventListener('change', function () {
                if (this.checked) {
                    schoolInput.value = school;
                    schoolManual.classList.remove('active');
                    schoolManualInput.value = '';
                    validateSchool();
                }
            });
        });

        const manualLabel = document.createElement('label');
        manualLabel.className = 'card-option card-option-manual';
        manualLabel.innerHTML =
            '<input type="radio" name="school_choice" value="manual" />' +
            '<span class="card-face">' +
                '<span class="card-icon"><i class="fas fa-pen"></i></span>' +
                '<span class="card-title">Add manually</span>' +
            '</span>';
        schoolCards.appendChild(manualLabel);
        manualLabel.querySelector('input').addEventListener('change', function () {
            if (this.checked) {
                schoolInput.value = 'manual';
                schoolManual.classList.add('active');
                setTimeout(function () { schoolManualInput.focus(); }, 100);
                validateSchool();
            }
        });

        schoolInput.value = '';
        schoolManual.classList.remove('active');
        schoolManualInput.value = '';
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
    clearWhenValid(schoolManualInput, validateSchool);


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
    schoolManualInput.addEventListener('blur', validateSchool);
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
    // FINAL SUBMIT — FIXED (deferred disable)
    // ============================================

    form.addEventListener('submit', function (e) {

        if (isSubmitting) {
            e.preventDefault();
            return;
        }
        if (formLocked) {
            e.preventDefault();
            return;
        }

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

        // Defer disable so the browser can complete the POST
        setTimeout(function () {
            submitBtn.disabled = true;
        }, 0);

        // Safety: restore the button if the server never responds
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