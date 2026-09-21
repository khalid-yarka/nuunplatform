/* ============================================================
   static/js/prompt-cards.js
   Floating prompt cards: notification permission + PWA install.

   Behaviour:
     • One card at a time. Install shown first if available.
     • Never auto-asks for permission — only on user click.
     • "Hadda maya" and ✕ dismiss the card for 24 hours.
     • Escape dismisses the visible card.
     • Subscribe to push if window.NuunPush exposes a subscribe helper.
   ============================================================ */

(function () {
    'use strict';

    var DISMISS_MS = 24 * 60 * 60 * 1000;
    var STORAGE_NOTIF   = 'nuun.prompt.notif.dismissed_until';
    var STORAGE_INSTALL = 'nuun.prompt.install.dismissed_until';
    var SHOW_DELAY_MS   = 2000;
    var CHAIN_DELAY_MS  = 3400;

    var deferredInstall = null;
    var installCard, notifCard, installBtn, notifBtn;

    function getDismissUntil(key) {
        try { var v = localStorage.getItem(key); return v ? parseInt(v, 10) : 0; }
        catch (e) { return 0; }
    }
    function setDismissUntil(key) {
        try { localStorage.setItem(key, String(Date.now() + DISMISS_MS)); }
        catch (e) {}
    }
    function isDismissed(key) { return Date.now() < getDismissUntil(key); }

    function isStandalone() {
        try {
            if (window.matchMedia('(display-mode: standalone)').matches) return true;
            if (window.navigator.standalone === true) return true;
        } catch (e) {}
        return false;
    }

    function canAskNotifications() {
        if (!('Notification' in window)) return false;
        if (Notification.permission !== 'default') return false;
        return true;
    }

    function isIOSNonStandalone() {
        var ua = window.navigator.userAgent || '';
        var isIOS = /iPad|iPhone|iPod/.test(ua) && !window.MSStream;
        return isIOS && !isStandalone();
    }

    function showCard(card) {
        if (!card || !card.hidden) return;
        card.hidden = false;
        card.classList.remove('is-leaving');
        void card.offsetWidth;
        card.classList.add('is-visible');
    }

    function hideCard(card, cb) {
        if (!card || card.hidden) { if (cb) cb(); return; }
        card.classList.remove('is-visible');
        card.classList.add('is-leaving');
        setTimeout(function () {
            card.classList.remove('is-leaving');
            card.hidden = true;
            if (cb) cb();
        }, 220);
    }

    function toast(msg) {
        if (typeof window.showToast === 'function') {
            try { window.showToast(msg, 'info'); return; } catch (e) {}
        }
        var el = document.createElement('div');
        el.className = 'nuun-prompt-toast';
        el.textContent = msg;
        document.body.appendChild(el);
        setTimeout(function () { el.remove(); }, 3200);
    }

    function subscribePush() {
        if (window.NuunPush && typeof window.NuunPush.subscribe === 'function') {
            try { window.NuunPush.subscribe(); return; } catch (e) {}
        }
        if (window.NuunPush && typeof window.NuunPush.init === 'function') {
            try { window.NuunPush.init(); return; } catch (e) {}
        }
    }

    // ─── Notification card ───
    function dismissNotif() {
        setDismissUntil(STORAGE_NOTIF);
        hideCard(notifCard);
    }

    function handleNotifEnable() {
        if (notifBtn) {
            notifBtn.disabled = true;
            notifBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Waa la daaraa…';
        }
        setDismissUntil(STORAGE_NOTIF);

        if (!('Notification' in window)) {
            hideCard(notifCard);
            toast('Browser-kaagu ma taageero notifications.');
            return;
        }

        if (Notification.permission === 'granted') {
            subscribePush();
            hideCard(notifCard);
            toast('✅ Notifications waa horey u daarnaa.');
            return;
        }

        if (Notification.permission === 'denied') {
            hideCard(notifCard);
            toast('Notifications waa la joojiyay. Waxaad ka beddeli kartaa browser-kaaga.');
            return;
        }

        Notification.requestPermission().then(function (perm) {
            hideCard(notifCard);
            if (perm === 'granted') {
                subscribePush();
                toast('✅ Mahadsanid! Hadda waxaad heli doontaa notifications.');
            } else if (perm === 'denied') {
                toast('Notifications waa la joojiyay. Waxaad ka beddeli kartaa browser-kaaga.');
            }
        }).catch(function () {
            hideCard(notifCard);
        });
    }

    function tryShowNotif() {
        if (!canAskNotifications()) return false;
        if (isDismissed(STORAGE_NOTIF)) return false;
        if (isIOSNonStandalone()) return false;
        showCard(notifCard);
        return true;
    }

    // ─── Install card ───
    function dismissInstall() {
        setDismissUntil(STORAGE_INSTALL);
        hideCard(installCard);
    }

    function handleInstall() {
        if (!deferredInstall) { dismissInstall(); return; }
        if (installBtn) {
            installBtn.disabled = true;
            installBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Installing…';
        }
        setDismissUntil(STORAGE_INSTALL);

        deferredInstall.prompt();
        deferredInstall.userChoice.then(function (choice) {
            if (choice && choice.outcome === 'accepted') {
                hideCard(installCard);
                toast('✅ NuunPlatform waa la rakibay.');
            } else {
                hideCard(installCard);
            }
            deferredInstall = null;
        }).catch(function () {
            hideCard(installCard);
            deferredInstall = null;
        });
    }

    function tryShowInstall() {
        if (!deferredInstall) return false;
        if (isDismissed(STORAGE_INSTALL)) return false;
        if (isStandalone()) return false;
        showCard(installCard);
        return true;
    }

    // ─── Sequencing ───
    function beginSequence() {
        if (tryShowInstall()) {
            var watched = false;
            var iv = setInterval(function () {
                if (!installCard.hidden) return;
                if (watched) return;
                watched = true;
                clearInterval(iv);
                setTimeout(function () { tryShowNotif(); }, CHAIN_DELAY_MS);
            }, 400);
            setTimeout(function () { clearInterval(iv); }, 180000);
            return;
        }
        tryShowNotif();
    }

    function wire() {
        installCard = document.getElementById('nuunInstallCard');
        notifCard   = document.getElementById('nuunNotifCard');
        installBtn  = document.getElementById('nuunInstallBtn');
        notifBtn    = document.getElementById('nuunNotifBtn');

        if (!installCard || !notifCard) return;

        document.querySelectorAll('[data-prompt-dismiss]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var which = btn.getAttribute('data-prompt-dismiss');
                if (which === 'install') dismissInstall();
                else if (which === 'notif') dismissNotif();
            });
        });

        if (installBtn) installBtn.addEventListener('click', handleInstall);
        if (notifBtn)   notifBtn.addEventListener('click', handleNotifEnable);

        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') return;
            var a = document.activeElement;
            if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable)) return;
            if (!notifCard.hidden)        dismissNotif();
            else if (!installCard.hidden) dismissInstall();
        });

        window.addEventListener('beforeinstallprompt', function (e) {
            e.preventDefault();
            deferredInstall = e;
            if (installCard.hidden && notifCard.hidden && !isDismissed(STORAGE_INSTALL)) {
                tryShowInstall();
            }
        });

        window.addEventListener('appinstalled', function () {
            deferredInstall = null;
            setDismissUntil(STORAGE_INSTALL);
            hideCard(installCard);
        });

        setTimeout(beginSequence, SHOW_DELAY_MS);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', wire);
    } else {
        wire();
    }
})();