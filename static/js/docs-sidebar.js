// static/js/docs-sidebar.js
// ============================================================
// Docs sidebar collapse toggle.
//
// Persists the state in localStorage under 'nuun.docs.sidebar-collapsed'.
// The anti-flash script in layout.html reads that key before first
// paint so the collapsed state applies without a flash.
//
// The icon rotation is handled by CSS (html.docs-sidebar-collapsed
// .docs-sidebar__collapse i { transform: rotate(180deg) }).
// This script only handles the click and the persistence.
// ============================================================

(function () {
    'use strict';

    var STORAGE_KEY = 'nuun.docs.sidebar-collapsed';

    function init() {
        var btn = document.getElementById('docsSidebarCollapse');
        if (!btn) return;

        // Sync initial ARIA state (the class is already applied by
        // the anti-flash script, if the user has collapsed before).
        var collapsed = document.documentElement.classList.contains('docs-sidebar-collapsed');
        btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');

        btn.addEventListener('click', function () {
            var nowCollapsed = document.documentElement.classList.toggle('docs-sidebar-collapsed');
            try {
                localStorage.setItem(STORAGE_KEY, nowCollapsed ? '1' : '0');
            } catch (e) {
                // localStorage may be unavailable (private mode).
                // The class is still applied for this session — just
                // not persisted across reloads.
            }
            btn.setAttribute('aria-expanded', nowCollapsed ? 'false' : 'true');
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();