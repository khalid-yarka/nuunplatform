/* ============================================================
   static/js/install-manager.js
   Global PWA install capability layer.

   Public API on window.NuunInstall:
     getState()              → 'installable' | 'ios' | 'installed' | 'unsupported'
     isInstalled()           → boolean
     canInstall()            → boolean
     needsIOSInstructions()  → boolean
     isSupported()           → boolean
     trigger()               → Promise
     showIOSHelp()           → opens the instructions modal
     on(event, handler)      → subscribe
   ============================================================ */

(function () {
    'use strict';

    var DEBUG = false;   // set to true to see console logs
    function log() {
        if (!DEBUG) return;
        try { console.log.apply(console, ['[NuunInstall]'].concat([].slice.call(arguments))); }
        catch (e) {}
    }

    var deferred = null;
    var installed = false;

    // ─── Environment detection ───

    function isStandalone() {
        try {
            if (window.matchMedia('(display-mode: standalone)').matches) return true;
            if (window.matchMedia('(display-mode: fullscreen)').matches) return true;
            if (window.matchMedia('(display-mode: minimal-ui)').matches) return true;
            if (window.navigator.standalone === true) return true;
        } catch (e) {}
        return false;
    }

    function isIOS() {
        var ua = window.navigator.userAgent || '';
        if (/iPad|iPhone|iPod/.test(ua) && !window.MSStream) return true;
        if (/Macintosh/.test(ua) && 'ontouchend' in document) return true;
        return false;
    }

    function isIOSBrowser() {
        if (!isIOS()) return false;
        var ua = window.navigator.userAgent || '';
        if (/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua)) return false;
        return /Safari/.test(ua);
    }

    function isInAppBrowser() {
        var ua = window.navigator.userAgent || '';
        return /FBAN|FBAV|Instagram|WhatsApp|Line\/|TikTok|Twitter|MicroMessenger|; wv\)/.test(ua);
    }

    function isFirefox() {
        var ua = window.navigator.userAgent || '';
        return /Firefox|FxiOS/.test(ua);
    }

    function isDesktopSafari() {
        var ua = window.navigator.userAgent || '';
        if (isIOS()) return false;
        return /Safari/.test(ua) && !/Chrome|CriOS|Firefox|FxiOS|Edg|OPR/.test(ua);
    }

    // ─── Public state ───

    function getState() {
        if (isStandalone() || installed) return 'installed';
        if (isInAppBrowser()) return 'unsupported';
        if (deferred) return 'installable';
        if (isIOSBrowser()) return 'ios';
        if (isFirefox()) return 'unsupported';
        if (isDesktopSafari()) return 'unsupported';
        return 'unsupported';
    }

    function isInstalled() { return getState() === 'installed'; }
    function canInstall() { return getState() === 'installable'; }
    function needsIOSInstructions() { return getState() === 'ios'; }
    function isSupported() {
        var s = getState();
        return s === 'installable' || s === 'ios';
    }

    // ─── Events ───

    function fireCustom(name, detail) {
        try {
            document.dispatchEvent(new CustomEvent(name, { detail: detail || {} }));
        } catch (e) {}
    }

    function broadcastState() {
        var state = getState();
        var supported = isSupported();
        log('state', state, 'supported', supported);
        fireCustom('nuun-install-state-change', {
            state: state,
            canInstall: supported
        });
    }

    // ─── Actions ───

    function trigger() {
        var state = getState();
        log('trigger called — state:', state);

        if (state === 'installable' && deferred) {
            try {
                fireCustom('nuun-install-prompted');
                deferred.prompt();
                return deferred.userChoice.then(function (choice) {
                    if (choice && choice.outcome === 'accepted') {
                        fireCustom('nuun-install-accepted');
                        deferred = null;
                        broadcastState();
                    } else {
                        fireCustom('nuun-install-dismissed');
                    }
                    return choice || { outcome: 'unknown' };
                }).catch(function (err) {
                    return { outcome: 'error', error: err };
                });
            } catch (err) {
                return Promise.resolve({ outcome: 'error', error: err });
            }
        }

        if (state === 'ios') {
            showIOSHelp();
            return Promise.resolve({ outcome: 'ios_modal' });
        }

        return Promise.resolve({ outcome: 'unsupported' });
    }

    function showIOSHelp() {
        var modal = document.getElementById('nuunInstallIOSModal');
        if (!modal) {
            log('iOS modal not found in DOM');
            return;
        }

        modal.style.display = 'flex';
        void modal.offsetWidth;
        modal.classList.add('is-visible');
        document.body.style.overflow = 'hidden';
        fireCustom('nuun-install-ios-help-shown');

        function close() {
            modal.classList.remove('is-visible');
            setTimeout(function () {
                modal.style.display = 'none';
                document.body.style.overflow = '';
            }, 220);
            document.removeEventListener('keydown', onKey);
        }
        function onKey(e) {
            if (e.key === 'Escape') close();
        }

        modal.querySelectorAll('[data-ios-close]').forEach(function (el) {
            el.onclick = close;
        });
        document.addEventListener('keydown', onKey);
    }

    // ─── Button sync ───

    function syncAllButtons() {
        var state = getState();
        var supported = isSupported();
        var isIOSMode = needsIOSInstructions();
        var buttons = document.querySelectorAll('[data-install-button]');

        log('syncAllButtons — found', buttons.length, 'buttons — supported:', supported, 'state:', state);

        buttons.forEach(function (btn) {
            if (supported) {
                btn.style.display = (btn.classList.contains('nuun-install-btn--hero'))
                    ? 'inline-flex'
                    : 'inline-flex';
                btn.setAttribute('data-install-mode', isIOSMode ? 'ios' : 'native');
            } else {
                btn.style.display = 'none';
                btn.removeAttribute('data-install-mode');
            }
        });
    }

    // ─── Global listeners ───

    window.addEventListener('beforeinstallprompt', function (e) {
        e.preventDefault();
        deferred = e;
        log('beforeinstallprompt captured');
        fireCustom('nuun-install-available');
        broadcastState();
    });

    window.addEventListener('appinstalled', function () {
        installed = true;
        deferred = null;
        log('appinstalled fired');
        fireCustom('nuun-install-installed');
        broadcastState();
    });

    try {
        window.matchMedia('(display-mode: standalone)')
              .addEventListener('change', broadcastState);
    } catch (e) {}

    document.addEventListener('nuun-install-state-change', syncAllButtons);

    document.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-install-button]');
        if (!btn) return;
        e.preventDefault();
        log('button clicked');
        trigger();
    });

    function ready() {
        document.documentElement.setAttribute('data-install-manager-ready', '1');
        syncAllButtons();

        // Belt-and-braces: re-sync a few times after load to catch
        // late-arriving beforeinstallprompt events.
        setTimeout(syncAllButtons, 500);
        setTimeout(syncAllButtons, 1500);
        setTimeout(syncAllButtons, 3500);

        // Final broadcast so all components know the manager is alive.
        broadcastState();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', ready);
    } else {
        ready();
    }

    window.NuunInstall = {
        getState: getState,
        isInstalled: isInstalled,
        canInstall: canInstall,
        needsIOSInstructions: needsIOSInstructions,
        isSupported: isSupported,
        trigger: trigger,
        showIOSHelp: showIOSHelp,
        _debug: function (on) { DEBUG = !!on; },
        on: function (name, handler) {
            document.addEventListener(name, handler);
            return function off() { document.removeEventListener(name, handler); };
        }
    };
})();