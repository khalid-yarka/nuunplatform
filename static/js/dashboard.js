// ============================================
// DASHBOARD JAVASCRIPT
// ============================================

document.addEventListener('DOMContentLoaded', function() {

    // ============================================
    // SIDEBAR TOGGLE (Mobile)
    // ============================================

    const menuToggle = document.getElementById('menuToggle');
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebarOverlay');

    function toggleSidebar() {
        sidebar.classList.toggle('open');
        if (overlay) {
            overlay.classList.toggle('active');
        }
        document.body.style.overflow = sidebar.classList.contains('open') ? 'hidden' : '';
    }

    function closeSidebar() {
        sidebar.classList.remove('open');
        if (overlay) {
            overlay.classList.remove('active');
        }
        document.body.style.overflow = '';
    }

    if (menuToggle) {
        menuToggle.addEventListener('click', toggleSidebar);
    }

    if (overlay) {
        overlay.addEventListener('click', closeSidebar);
    }

    // Close sidebar on Escape
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && sidebar.classList.contains('open')) {
            closeSidebar();
        }
    });

    // Auto-close sidebar when resizing from mobile to desktop
    window.addEventListener('resize', function() {
        if (window.innerWidth > 768 && sidebar.classList.contains('open')) {
            closeSidebar();
        }
    });

    // ============================================
    // SIDEBAR COLLAPSE (Desktop only)
    // ============================================

    const collapseBtn = document.getElementById('sidebarCollapseBtn');

    if (collapseBtn) {
        // Sync initial state — class may already be set by anti-flash script
        const isCollapsed = document.documentElement.classList.contains('sidebar-collapsed');
        collapseBtn.setAttribute('aria-expanded', !isCollapsed);
        collapseBtn.setAttribute('aria-label', isCollapsed ? 'Expand sidebar' : 'Collapse sidebar');

        collapseBtn.addEventListener('click', function() {
            const collapsed = document.documentElement.classList.toggle('sidebar-collapsed');
            try {
                localStorage.setItem('sidebar-collapsed', collapsed ? '1' : '0');
            } catch (e) {
                // localStorage unavailable (private mode) — ignore
            }
            collapseBtn.setAttribute('aria-expanded', !collapsed);
            collapseBtn.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
        });
    }

    // ============================================
    // SIDEBAR LOGOUT
    // ============================================

    const sidebarLogoutBtn = document.getElementById('sidebarLogoutBtn');
    if (sidebarLogoutBtn) {
        sidebarLogoutBtn.addEventListener('click', function(e) {
            e.preventDefault();
            if (typeof openSidebarLogoutModal === 'function') {
                openSidebarLogoutModal();
            } else if (confirm('Are you sure you want to log out? Any unsaved changes will be lost.')) {
                window.location.href = '/logout';
            }
        });
    }

    // ============================================
    // ACTIVE NAV LINK — fallback safety net
    // ============================================

    const currentPath = window.location.pathname;
    const navItems = document.querySelectorAll('.nav-item');

    navItems.forEach(function(item) {
        const href = item.getAttribute('href');
        if (href && href !== '#' && !item.classList.contains('active')) {
            // exact match OR prefix match (excluding root)
            if (currentPath === href || (href !== '/' && currentPath.startsWith(href + '/'))) {
                item.classList.add('active');
            }
        }
        if (item.classList.contains('active')) {
            item.setAttribute('aria-current', 'page');
        }
    });

    // ============================================
    // THEME TOGGLE — uses NuunSettings API
    // ============================================
    (function() {
        const toggleBtn = document.getElementById('themeToggle');
        const icon = document.getElementById('themeIcon');
        if (!toggleBtn || !icon) return;

        const themes = ['light', 'dark', 'system'];
        const themeIcons = {
            light: 'fa-sun',
            dark: 'fa-moon',
            system: 'fa-desktop'
        };

        function getCurrentTheme() {
            return document.documentElement.getAttribute('data-theme') || 'system';
        }

        function applyTheme(theme) {
            if (typeof NuunSettings !== 'undefined' && NuunSettings.initialized) {
                NuunSettings.set('appearance.theme', theme);
            } else {
                if (theme === 'system') {
                    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
                    document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
                } else {
                    document.documentElement.setAttribute('data-theme', theme);
                }
                localStorage.setItem('preferred-theme', theme);
            }
            icon.className = 'fas ' + (themeIcons[theme] || 'fa-sun');
        }

        function cycleTheme() {
            const current = getCurrentTheme();
            let idx = themes.indexOf(current);
            if (idx === -1) idx = 2;
            const next = themes[(idx + 1) % themes.length];
            applyTheme(next);
        }

        const initial = getCurrentTheme();
        icon.className = 'fas ' + (themeIcons[initial] || 'fa-sun');

        toggleBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            cycleTheme();
        });

        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
            const current = document.documentElement.getAttribute('data-theme');
            if (current === 'system') {
                document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light');
            }
        });
    })();
});