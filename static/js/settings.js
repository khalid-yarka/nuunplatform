// static/js/settings.js
document.addEventListener('DOMContentLoaded', function() {
    // ============================================
    // UNSAVED CHANGES TRACKING
    // ============================================
    let unsavedChanges = false;
    let originalFormData = {};
    const indicator = document.getElementById('unsavedIndicator');
    const saveNowBtn = document.getElementById('unsavedSaveNow');
    const discardBtn = document.getElementById('unsavedDiscard');

    function showUnsavedIndicator() {
        if (indicator) {
            indicator.classList.add('show');
        }
        unsavedChanges = true;
    }

    function hideUnsavedIndicator() {
        if (indicator) {
            indicator.classList.remove('show');
        }
        unsavedChanges = false;
    }

    function trackFormChanges(form) {
        const inputs = form.querySelectorAll('input, select, textarea');
        originalFormData = {};
        inputs.forEach(input => {
            if (input.type === 'checkbox' || input.type === 'radio') {
                originalFormData[input.id || input.name] = input.checked;
            } else {
                originalFormData[input.id || input.name] = input.value;
            }
        });

        inputs.forEach(input => {
            input.addEventListener('change', function() {
                let changed = false;
                const currentValue = this.type === 'checkbox' || this.type === 'radio' ? this.checked : this.value;
                const key = this.id || this.name;
                if (originalFormData[key] !== currentValue) {
                    changed = true;
                }
                if (changed) {
                    showUnsavedIndicator();
                } else {
                    // Check if any other fields are changed
                    let anyChanged = false;
                    inputs.forEach(inp => {
                        const k = inp.id || inp.name;
                        const val = inp.type === 'checkbox' || inp.type === 'radio' ? inp.checked : inp.value;
                        if (originalFormData[k] !== val) {
                            anyChanged = true;
                        }
                    });
                    if (!anyChanged) {
                        hideUnsavedIndicator();
                    }
                }
            });
        });

        form.addEventListener('submit', function() {
            hideUnsavedIndicator();
        });
    }

    // Track all settings forms
    document.querySelectorAll('.settings-form').forEach(form => {
        trackFormChanges(form);
    });

    // Save now button
    if (saveNowBtn) {
        saveNowBtn.addEventListener('click', function() {
            // Find the nearest form with changes
            const form = document.querySelector('.settings-form:not([style*="display: none"])');
            if (form) {
                form.dispatchEvent(new Event('submit'));
            } else {
                // If no visible form, try to save all
                document.querySelectorAll('.settings-form').forEach(f => {
                    if (f.style.display !== 'none') {
                        f.dispatchEvent(new Event('submit'));
                    }
                });
            }
        });
    }

    // Discard button
    if (discardBtn) {
        discardBtn.addEventListener('click', function() {
            if (confirm('Discard all unsaved changes?')) {
                location.reload();
            }
        });
    }

    // Before unload warning
    window.addEventListener('beforeunload', function(e) {
        if (unsavedChanges) {
            e.preventDefault();
            e.returnValue = 'You have unsaved changes. Are you sure you want to leave?';
            return e.returnValue;
        }
    });

    // ============================================
    // TIER-LOCKED ACCENT COLOURS
    // ============================================
    const userTier = document.body.dataset.tier || 'danbe';
    const tierLevels = { 'danbe': 0, 'dhexe': 1, 'hore': 2 };
    const userTierLevel = tierLevels[userTier] || 0;
    const accentTiers = {
        'red': 'danbe',
        'blue': 'dhexe',
        'green': 'dhexe',
        'purple': 'dhexe',
        'orange': 'hore'
    };

    document.querySelectorAll('.accent-option').forEach(btn => {
        const accent = btn.dataset.accent;
        const requiredTier = accentTiers[accent] || 'danbe';
        const requiredLevel = tierLevels[requiredTier] || 0;

        if (requiredLevel > userTierLevel) {
            btn.classList.add('locked');
            btn.title = `Upgrade to ${requiredTier.charAt(0).toUpperCase() + requiredTier.slice(1)} to use this accent.`;
            btn.addEventListener('click', function(e) {
                if (this.classList.contains('locked')) {
                    e.preventDefault();
                    e.stopPropagation();
                    if (typeof window.openSafkaPreview === 'function') {
                        window.openSafkaPreview({ feature: 'accent_color', requiredTier: requiredTier });
                    }
                }
            });
        }
    });

    // ============================================
    // LOGOUT CONFIRMATION
    // ============================================
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', function(e) {
            e.preventDefault();
            if (confirm('Are you sure you want to log out?')) {
                window.location.href = this.href;
            }
        });
    }

    // ============================================
    // TOAST HELPER (if not already defined)
    // ============================================
    if (typeof window.showToast !== 'function') {
        window.showToast = function(message, type) {
            alert(message);
        };
    }
});