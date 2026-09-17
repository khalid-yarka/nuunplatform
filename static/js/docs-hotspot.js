// static/js/docs-hotspot.js
// ============================================================
// Runtime hotspot overlay for the mockup canvas.
//
// Reads [data-docs-anchor="name"] inside the current mockup,
// draws a pulsing ring over each named anchor, and wires
// click-to-zoom on the mockup viewport.
//
// Never crashes: if an anchor is missing, it logs a warning
// (dev only) and continues.
//
// Zoom-aware: ring positions are divided by the current CSS
// scale factor so they stay locked to their anchors even when
// the mockup is zoomed via `transform: scale()`.
// ============================================================

(function () {
    'use strict';

    var DEBUG = /[?&]docs_debug=1/.test(window.location.search);

    function warn() {
        if (!DEBUG) return;
        var args = Array.prototype.slice.call(arguments);
        args.unshift('[docs-hotspot]');
        console.warn.apply(console, args);
    }

    function parseList(raw) {
        if (!raw) return [];
        return String(raw)
            .split(',')
            .map(function (s) { return s.trim(); })
            .filter(Boolean);
    }

    function getScale(inner) {
        // Extract the current scale factor from the computed
        // transform matrix. Returns 1 when unset or unparseable.
        try {
            var tx = window.getComputedStyle(inner).transform;
            if (!tx || tx === 'none') return 1;
            var m = tx.match(/matrix\(([^)]+)\)/);
            if (!m) return 1;
            var s = parseFloat(m[1].split(',')[0]);
            if (!isNaN(s) && s > 0) return s;
        } catch (e) { /* silent */ }
        return 1;
    }

    function setupZoomButton(mockup) {
        var btn = document.querySelector('.docs-canvas__zoom-btn');
        if (!btn) return;

        // Clone-and-replace so we never stack duplicate handlers
        // across redraws.
        var fresh = btn.cloneNode(true);
        btn.parentNode.replaceChild(fresh, btn);

        fresh.addEventListener('click', function () {
            var isZoomed = mockup.classList.toggle('is-zoomed');
            fresh.classList.toggle('is-active', isZoomed);
            fresh.setAttribute('aria-pressed', isZoomed ? 'true' : 'false');
            var label = fresh.querySelector('[data-zoom-label]');
            if (label) {
                label.textContent = isZoomed ? 'Zoom out' : 'Zoom in';
            }
        });
    }

    function wireViewportClickZoom(mockup) {
        var viewport = mockup.querySelector('.docs-mockup__viewport');
        if (!viewport || viewport.dataset.docsZoomWired === '1') return;
        viewport.dataset.docsZoomWired = '1';

        viewport.addEventListener('click', function (e) {
            var tag = (e.target.tagName || '').toLowerCase();
            if (tag === 'a' || tag === 'button' || tag === 'input') return;

            var isZoomed = mockup.classList.toggle('is-zoomed');
            var btn = document.querySelector('.docs-canvas__zoom-btn');
            if (btn) {
                btn.classList.toggle('is-active', isZoomed);
                btn.setAttribute('aria-pressed', isZoomed ? 'true' : 'false');
            }

            if (isZoomed) {
                var first = mockup.querySelector('.docs-hotspot');
                if (first) {
                    first.scrollIntoView({
                        block: 'center',
                        behavior: 'smooth',
                    });
                }
            }
        });
    }

    function drawRings() {
        var mockup = document.querySelector('.docs-mockup');
        if (!mockup) return;

        var overlay = mockup.querySelector('.docs-mockup__overlay');
        if (!overlay) return;

        var inner = mockup.querySelector('.docs-mockup__inner');
        if (!inner) return;

        // Clear previous rings.
        overlay.innerHTML = '';

        var names = parseList(mockup.getAttribute('data-hotspots'));
        if (!names.length) return;

        var innerRect = inner.getBoundingClientRect();

        // Zoom-aware ring positioning.
        var scale = getScale(inner);

        names.forEach(function (name, i) {
            var anchor = mockup.querySelector('[data-docs-anchor="' + name + '"]');
            if (!anchor) {
                warn('anchor not found:', name);
                if (DEBUG) {
                    var miss = document.createElement('div');
                    miss.className = 'docs-hotspot docs-hotspot--missing';
                    miss.style.left = '8px';
                    miss.style.top = (8 + i * 30) + 'px';
                    miss.style.width = '24px';
                    miss.style.height = '24px';
                    overlay.appendChild(miss);
                }
                return;
            }

            var rect = anchor.getBoundingClientRect();

            // Divide by scale so positions are in the inner's
            // local (unscaled) coordinate space.
            var x = (rect.left - innerRect.left) / scale;
            var y = (rect.top - innerRect.top) / scale;
            var w = rect.width / scale;
            var h = rect.height / scale;

            var ring = document.createElement('div');
            ring.className = 'docs-hotspot' + (i === 0 ? ' docs-hotspot--primary' : '');
            ring.style.left = Math.max(0, x - 4) + 'px';
            ring.style.top = Math.max(0, y - 4) + 'px';
            ring.style.width = (w + 8) + 'px';
            ring.style.height = (h + 8) + 'px';
            ring.setAttribute('aria-hidden', 'true');
            overlay.appendChild(ring);
        });

        setupZoomButton(mockup);
        wireViewportClickZoom(mockup);
    }

    var resizeTimer = null;
    function scheduleRedraw() {
        if (resizeTimer) clearTimeout(resizeTimer);
        resizeTimer = setTimeout(drawRings, 120);
    }

    function init() {
        if (!document.querySelector('.docs-mockup')) return;

        if (document.fonts && document.fonts.ready) {
            document.fonts.ready.then(drawRings).catch(drawRings);
        } else {
            setTimeout(drawRings, 60);
        }

        setTimeout(drawRings, 0);

        window.addEventListener('resize', scheduleRedraw);
        window.addEventListener('orientationchange', scheduleRedraw);

        // Redraw whenever the zoom class toggles so rings stay
        // aligned with their anchors.
        var mockup = document.querySelector('.docs-mockup');
        if (mockup && window.MutationObserver) {
            var observer = new MutationObserver(function (mutations) {
                mutations.forEach(function (m) {
                    if (m.type === 'attributes' && m.attributeName === 'class') {
                        scheduleRedraw();
                    }
                });
            });
            observer.observe(mockup, { attributes: true, attributeFilter: ['class'] });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();