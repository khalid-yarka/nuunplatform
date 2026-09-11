// static/js/main.js – Global utilities with logout modal handling

document.addEventListener('DOMContentLoaded', function() {
    // ============================================
    // THEME PERSISTENCE (Global)
    // ============================================

    window.applyTheme = function(theme) {
        if (theme === 'system') {
            const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
            localStorage.setItem('preferred-theme', 'system');
        } else {
            document.documentElement.setAttribute('data-theme', theme);
            localStorage.setItem('preferred-theme', theme);
        }
        const icon = document.getElementById('fabThemeIcon');
        if (icon) {
            if (theme === 'system') icon.className = 'fas fa-desktop';
            else if (theme === 'dark') icon.className = 'fas fa-moon';
            else icon.className = 'fas fa-sun';
        }
    };

    (function() {
        const savedTheme = localStorage.getItem('preferred-theme') || 'system';
        window.applyTheme(savedTheme);

        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
            const current = localStorage.getItem('preferred-theme') || 'system';
            if (current === 'system') {
                document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light');
            }
        });
    })();

    // ============================================
    // TOAST SYSTEM
    // ============================================
    window.showToast = function(message, type, duration) {
        type = type || 'info';
        duration = duration || 4000;

        const container = document.getElementById('toast-container');
        if (!container) return;

        const toast = document.createElement('div');
        toast.className = 'toast toast-' + type;
        toast.innerHTML = `
            <span class="toast-icon">
                <i class="fas ${type === 'success' ? 'fa-check-circle' : type === 'error' ? 'fa-exclamation-circle' : type === 'warning' ? 'fa-exclamation-triangle' : 'fa-info-circle'}"></i>
            </span>
            <span class="toast-message">${message}</span>
            <button class="toast-close">&times;</button>
        `;

        container.appendChild(toast);

        const timeout = setTimeout(function() {
            toast.remove();
        }, duration);

        toast.querySelector('.toast-close').addEventListener('click', function() {
            clearTimeout(timeout);
            toast.remove();
        });

        toast.addEventListener('mouseenter', function() {
            clearTimeout(timeout);
        });
        toast.addEventListener('mouseleave', function() {
            setTimeout(function() {
                toast.remove();
            }, 1500);
        });
    };

    // ============================================
    // AUTO-DISMISS FLASH MESSAGES
    // ============================================
    const flashContainer = document.getElementById('flashContainer');
    if (flashContainer) {
        setTimeout(function() {
            flashContainer.style.transition = 'opacity 0.5s ease';
            flashContainer.style.opacity = '0';
            setTimeout(function() {
                flashContainer.remove();
            }, 500);
        }, 5000);
    }

    // ============================================
    // CSRF TOKEN HELPER
    // ============================================
    window.getCsrfToken = function() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.content;
        const input = document.querySelector('input[name="csrf_token"]');
        if (input) return input.value;
        return '';
    };

    // ============================================
    // NOTIFICATION DROPDOWN TOGGLE
    // ============================================
    const notifToggle = document.getElementById('notificationToggle');
    const notifDropdown = document.getElementById('notificationDropdown');

    if (notifToggle && notifDropdown) {
        notifToggle.addEventListener('click', function(e) {
            e.stopPropagation();
            notifDropdown.classList.toggle('open');
        });

        document.addEventListener('click', function(e) {
            if (!notifDropdown.contains(e.target) && e.target !== notifToggle) {
                notifDropdown.classList.remove('open');
            }
        });
    }

    // ============================================
    // SIDEBAR LOGOUT – Fallback handler
    // ============================================
    const sidebarLogoutBtn = document.getElementById('sidebarLogoutBtn');
    if (sidebarLogoutBtn) {
        // The modal is already handled in dashboard_base.html
        // This is just a safety fallback
        sidebarLogoutBtn.addEventListener('click', function(e) {
            e.preventDefault();
            // Check if the modal function exists
            if (typeof openSidebarLogoutModal === 'function') {
                openSidebarLogoutModal();
            } else {
                // Fallback: redirect to logout directly
                if (confirm('Are you sure you want to log out? Any unsaved changes will be lost.')) {
                    window.location.href = '/logout';
                }
            }
        });
    }

    console.log('✅ NuunPlatform main.js loaded');
});

// ============================================
// DISABLED BUTTON HANDLER
// ============================================
document.addEventListener('click', function(e) {
    const btn = e.target.closest('.btn-disabled');
    if (btn && btn.disabled) {
        e.preventDefault();
        const tooltip = btn.getAttribute('data-tooltip') || 'This feature is not available for your tier.';
        if (typeof window.showToast === 'function') {
            window.showToast('🔒 ' + tooltip, 'warning');
        } else {
            alert('🔒 ' + tooltip);
        }
    }
});

// ============================================
// GLOBAL LOGOUT MODAL FUNCTIONS (fallback)
// ============================================
// These are defined in dashboard_base.html and settings/index.html,
// but we provide a fallback just in case.

if (typeof openSidebarLogoutModal === 'undefined') {
    window.openSidebarLogoutModal = function() {
        // Fallback to simple confirm
        if (confirm('Are you sure you want to log out?\n\nYou will lose any unsaved changes, including:\n• Unsaved settings\n• In-progress quizzes\n• Live quiz sessions\n• Unsaved form data\n\nThis action cannot be undone.')) {
            window.location.href = '/logout';
        }
    };
}

if (typeof closeSidebarLogoutModal === 'undefined') {
    window.closeSidebarLogoutModal = function() {
        // Nothing to close in fallback mode
    };
}