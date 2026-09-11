// static/js/platform-settings.js
/**
 * NuunPlatform Global Settings Runtime
 * - Syncs theme/accent/font to localStorage for anti-flash.
 * - Applies settings live when changed.
 */
(function() {
    'use strict';

    const ACCENT_MAP = {
        'red':    { hex: '#FF3138', hover: '#E62B32', light: '#FFEBE8', light_dark: '#3A1A20' },
        'blue':   { hex: '#3B82F6', hover: '#2563EB', light: '#E8F0FE', light_dark: '#1A2A4A' },
        'green':  { hex: '#10B981', hover: '#059669', light: '#D1FAE5', light_dark: '#1A3A2E' },
        'purple': { hex: '#8B5CF6', hover: '#7C3AED', light: '#EDE9FE', light_dark: '#2A1A4A' },
        'orange': { hex: '#F59E0B', hover: '#D97706', light: '#FEF3C7', light_dark: '#3A2E1A' }
    };

    const NuunSettings = {
        settings: {},
        initialized: false,

        init(initialSettings) {
            if (this.initialized) return;
            this.settings = initialSettings || {};
            this.applyAll();
            this.syncToLocalStorage();
            this.initialized = true;
            console.log('✅ NuunSettings initialized with', Object.keys(this.settings).length, 'settings');
        },

        /**
         * Mirror server settings to localStorage.
         * This makes the anti-flash script work on next page load.
         */
        syncToLocalStorage() {
            try {
                if (this.settings['appearance.theme']) {
                    localStorage.setItem('preferred-theme', this.settings['appearance.theme']);
                }
                if (this.settings['appearance.accent']) {
                    localStorage.setItem('preferred-accent', this.settings['appearance.accent']);
                }
                if (this.settings['appearance.font_size']) {
                    localStorage.setItem('preferred-font-size', this.settings['appearance.font_size']);
                }
                localStorage.setItem(
                    'preferred-compact',
                    (this.settings['appearance.compact_mode'] ? '1' : '0')
                );
                localStorage.setItem(
                    'preferred-reduced-motion',
                    (this.settings['appearance.reduced_motion'] ? '1' : '0')
                );
            } catch (e) {
                // Ignore (private mode)
            }
        },

        applyAll() {
            this.applyTheme(this.settings['appearance.theme']);
            this.applyAccent(this.settings['appearance.accent']);
            this.applyFontSize(this.settings['appearance.font_size']);
            this.applyCompactMode(this.settings['appearance.compact_mode']);
            this.applyReducedMotion(this.settings['appearance.reduced_motion']);
        },

        applyTheme(theme) {
            if (!theme) return;
            const html = document.documentElement;
            let resolved;
            if (theme === 'system') {
                resolved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
            } else {
                resolved = theme;
            }
            html.setAttribute('data-theme', resolved);

            document.querySelectorAll('.theme-toggle-icon').forEach(el => {
                if (theme === 'dark') el.className = 'fas fa-moon';
                else if (theme === 'light') el.className = 'fas fa-sun';
                else el.className = 'fas fa-desktop';
            });

            try {
                localStorage.setItem('preferred-theme', theme);
            } catch (e) {}
        },

        applyAccent(color) {
            if (!color) return;
            const data = ACCENT_MAP[color] || ACCENT_MAP['red'];
            const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
            const light = isDark ? data.light_dark : data.light;

            const root = document.documentElement;
            root.style.setProperty('--primary', data.hex);
            root.style.setProperty('--primary-hover', data.hover);
            root.style.setProperty('--primary-light', light);

            try {
                localStorage.setItem('preferred-accent', color);
            } catch (e) {}
        },

        applyFontSize(size) {
            if (!size) return;
            const sizes = { small: '14px', medium: '16px', large: '18px' };
            document.documentElement.style.setProperty('--base-font-size', sizes[size] || '16px');
            try {
                localStorage.setItem('preferred-font-size', size);
            } catch (e) {}
        },

        applyCompactMode(enabled) {
            document.documentElement.classList.toggle('compact-mode', !!enabled);
            try {
                localStorage.setItem('preferred-compact', enabled ? '1' : '0');
            } catch (e) {}
        },

        applyReducedMotion(enabled) {
            document.documentElement.classList.toggle('reduced-motion', !!enabled);
            try {
                localStorage.setItem('preferred-reduced-motion', enabled ? '1' : '0');
            } catch (e) {}
        },

        get(key) {
            return this.settings[key];
        },

        set(key, value) {
            const old = this.settings[key];
            this.settings[key] = value;
            this.applyAll();
            this.syncToLocalStorage();

            return fetch('/settings/api', {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || ''
                },
                body: JSON.stringify({ [key]: value })
            })
            .then(response => {
                if (!response.ok) {
                    return response.json().then(data => {
                        throw new Error(data.error || 'Server error');
                    });
                }
                return response.json();
            })
            .then(data => {
                if (!data.success) {
                    throw new Error(data.error || 'Update failed');
                }
                if (data.settings) {
                    this.settings = data.settings;
                } else {
                    return this.refresh();
                }
                this.applyAll();
                this.syncToLocalStorage();
                if (typeof window.showToast === 'function') {
                    window.showToast('Setting updated', 'success');
                }
                return data;
            })
            .catch(err => {
                this.settings[key] = old;
                this.applyAll();
                this.syncToLocalStorage();
                if (typeof window.showToast === 'function') {
                    window.showToast('Failed to update: ' + err.message, 'error');
                } else {
                    console.error('Settings update failed:', err);
                }
                throw err;
            });
        },

        refresh() {
            return fetch('/settings/api', {
                method: 'GET',
                headers: {
                    'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || ''
                }
            })
            .then(response => response.json())
            .then(data => {
                this.settings = data;
                this.applyAll();
                this.syncToLocalStorage();
                return data;
            })
            .catch(err => {
                console.error('Failed to refresh settings:', err);
                throw err;
            });
        }
    };

    window.NuunSettings = NuunSettings;

    // React to OS-level theme changes when user prefers "system"
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
        if (NuunSettings.settings['appearance.theme'] === 'system') {
            document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light');
            const accent = NuunSettings.settings['appearance.accent'] || 'red';
            NuunSettings.applyAccent(accent);
        }
    });

})();