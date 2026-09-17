// static/js/docs-router.js
// ============================================================
// Keyboard navigation for the docs system.
//
//   ← / →  prev / next step (whichever exists on the page)
//   Esc    return to /docs/
//
// Also auto-scrolls the active step into view on load, and
// handles the "/" shortcut to jump to search.
// ============================================================

(function () {
    'use strict';

    function isTyping(target) {
        if (!target) return false;
        var tag = target.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
        if (target.isContentEditable) return true;
        return false;
    }

    function findNav(selector) {
        return document.querySelector(selector);
    }

    function go(url) {
        if (!url) return;
        window.location.href = url;
    }

    function onKey(e) {
        if (isTyping(e.target)) return;
        if (e.ctrlKey || e.metaKey || e.altKey) return;

        // Left / Right — prev / next step
        if (e.key === 'ArrowLeft') {
            var prev = findNav('.docs-step__nav--prev:not(.is-disabled)');
            if (prev && prev.href) {
                e.preventDefault();
                go(prev.href);
            }
            return;
        }
        if (e.key === 'ArrowRight') {
            var next = findNav('.docs-step__nav--next');
            if (next && next.href) {
                e.preventDefault();
                go(next.href);
            }
            return;
        }

        // Escape — back to docs home
        if (e.key === 'Escape') {
            var home = document.querySelector('.docs-sidebar__home');
            if (home && home.href && window.location.pathname !== '/docs/') {
                e.preventDefault();
                go(home.href);
            }
            return;
        }

        // "/" — jump to search
        if (e.key === '/') {
            var input = document.querySelector('.docs-search__input');
            if (input) {
                e.preventDefault();
                input.focus();
                input.select && input.select();
            }
        }
    }

    function scrollSidebarActiveIntoView() {
        var active = document.querySelector('.docs-sidebar__item.is-active');
        if (!active) return;
        var sidebar = document.getElementById('docsSidebar');
        if (!sidebar) return;
        // Only scroll within the sidebar, not the page.
        var rect = active.getBoundingClientRect();
        var sRect = sidebar.getBoundingClientRect();
        if (rect.top < sRect.top || rect.bottom > sRect.bottom) {
            active.scrollIntoView({ block: 'center', behavior: 'smooth' });
        }
    }

    function init() {
        document.addEventListener('keydown', onKey);
        scrollSidebarActiveIntoView();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();