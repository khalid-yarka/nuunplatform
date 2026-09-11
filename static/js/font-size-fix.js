// static/js/font-size-fix.js
(function() {
    'use strict';

    function applyFontSize(size) {
        const sizes = { small: '14px', medium: '16px', large: '18px' };
        const pixelSize = sizes[size] || '16px';
        
        const existing = document.getElementById('nuun-font-size-style');
        if (existing) existing.remove();
        
        const rules = [
            '* { font-size: ' + pixelSize + ' !important; }',
            'h1 { font-size: calc(' + pixelSize + ' * 1.75) !important; }',
            'h2 { font-size: calc(' + pixelSize + ' * 1.5) !important; }',
            'h3 { font-size: calc(' + pixelSize + ' * 1.25) !important; }',
            'h4 { font-size: calc(' + pixelSize + ' * 1.1) !important; }',
            'h5 { font-size: calc(' + pixelSize + ' * 1) !important; }',
            'h6 { font-size: calc(' + pixelSize + ' * 0.9) !important; }',
            'small { font-size: calc(' + pixelSize + ' * 0.85) !important; }',
            '.btn { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.btn-sm { font-size: calc(' + pixelSize + ' * 0.75) !important; }',
            '.btn-lg { font-size: calc(' + pixelSize + ' * 1) !important; }',
            '.form-control { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.form-label { font-size: calc(' + pixelSize + ' * 0.8125) !important; }',
            '.form-hint { font-size: calc(' + pixelSize + ' * 0.75) !important; }',
            '.badge { font-size: calc(' + pixelSize + ' * 0.75) !important; }',
            '.stat-number { font-size: calc(' + pixelSize + ' * 1.5) !important; }',
            '.card-title { font-size: calc(' + pixelSize + ' * 1.125) !important; }',
            '.settings-page-title { font-size: calc(' + pixelSize + ' * 1.625) !important; }',
            '.modal-title { font-size: calc(' + pixelSize + ' * 1.125) !important; }',
            '.toast { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.flash-message { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.nav-item { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.breadcrumb { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.page-title { font-size: calc(' + pixelSize + ' * 1.125) !important; }',
            '.sidebar-brand .brand-text { font-size: calc(' + pixelSize + ' * 1) !important; }',
            '.sidebar-user .user-name { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.sidebar-user .user-id { font-size: calc(' + pixelSize + ' * 0.6875) !important; }',
            '.top-navbar .navbar-left .page-title { font-size: calc(' + pixelSize + ' * 1.125) !important; }',
            '.top-navbar .navbar-right .user-name-sm { font-size: calc(' + pixelSize + ' * 0.8125) !important; }',
            '.wr-title { font-size: calc(' + pixelSize + ' * 1.25) !important; }',
            '.wr-code { font-size: calc(' + pixelSize * 1.25) !important; }',
            '.question-text { font-size: calc(' + pixelSize + ' * 1.125) !important; }',
            '.option-btn { font-size: calc(' + pixelSize + ' * 0.9375) !important; }',
            '.feedback-title { font-size: calc(' + pixelSize + ' * 1) !important; }',
            '.feedback-explanation { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.result-hero .score { font-size: calc(' + pixelSize * 3.5) !important; }',
            '.result-hero .percentage { font-size: calc(' + pixelSize + ' * 1.5) !important; }',
            '.result-stats .stat .number { font-size: calc(' + pixelSize + ' * 1.75) !important; }',
            '.result-stats .stat .label { font-size: calc(' + pixelSize + ' * 0.75) !important; }',
            '.leaderboard-full .header { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.leaderboard-full .row { font-size: calc(' + pixelSize + ' * 0.8125) !important; }',
            '.lobby-stat .number { font-size: calc(' + pixelSize + ' * 1.5) !important; }',
            '.lobby-stat .label { font-size: calc(' + pixelSize + ' * 0.6875) !important; }',
            '.quiz-card .body .info .title { font-size: calc(' + pixelSize + ' * 1.125) !important; }',
            '.quiz-card .body .info .meta { font-size: calc(' + pixelSize + ' * 0.8125) !important; }',
            '.tier-badge { font-size: calc(' + pixelSize + ' * 0.8125) !important; }',
            '.feature-name { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.feature-desc { font-size: calc(' + pixelSize + ' * 0.75) !important; }',
            '.toggle-text { font-size: calc(' + pixelSize + ' * 0.9375) !important; }',
            '.toggle-desc { font-size: calc(' + pixelSize + ' * 0.75) !important; }',
            '.settings-category-card .card-content h3 { font-size: calc(' + pixelSize + ' * 1) !important; }',
            '.settings-category-card .card-content p { font-size: calc(' + pixelSize + ' * 0.8125) !important; }',
            '.settings-card .card-header h3 { font-size: calc(' + pixelSize + ' * 1.125) !important; }',
            '.settings-card .card-header p { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.theme-preview .theme-header { font-size: calc(' + pixelSize + ' * 1) !important; }',
            '.theme-preview .theme-body { font-size: calc(' + pixelSize + ' * 0.8125) !important; }',
            '.password-match { font-size: calc(' + pixelSize + ' * 0.75) !important; }',
            '.locked-hint { font-size: calc(' + pixelSize + ' * 0.75) !important; }',
            '.hint { font-size: calc(' + pixelSize + ' * 0.75) !important; }',
            '.text-muted { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.text-secondary { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.footer-links a { font-size: calc(' + pixelSize + ' * 0.875) !important; }',
            '.footer-copy { font-size: calc(' + pixelSize + ' * 0.8125) !important; }'
        ];
        
        const style = document.createElement('style');
        style.id = 'nuun-font-size-style';
        style.textContent = rules.join('\n');
        document.head.appendChild(style);
        
        localStorage.setItem('preferred-font-size', size);
    }

    function initFontSize() {
        try {
            let size = 'medium';
            if (window.NuunSettings && window.NuunSettings.settings) {
                size = window.NuunSettings.settings['appearance.font_size'] || 'medium';
            } else {
                size = localStorage.getItem('preferred-font-size') || 'medium';
            }
            applyFontSize(size);
            console.log('✅ Font size applied:', size);
        } catch (e) {
            console.warn('Failed to apply font size:', e);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initFontSize);
    } else {
        initFontSize();
    }

    document.addEventListener('DOMContentLoaded', function() {
        if (window.NuunSettings && window.NuunSettings.initialized) {
            var size = NuunSettings.settings['appearance.font_size'] || 'medium';
            applyFontSize(size);
        }
        
        var originalApplyAll = window.NuunSettings ? window.NuunSettings.applyAll : null;
        if (window.NuunSettings) {
            window.NuunSettings.applyAll = function() {
                if (originalApplyAll) originalApplyAll();
                var size = this.settings['appearance.font_size'] || 'medium';
                applyFontSize(size);
            };
        }
    });

    window.applyFontSize = applyFontSize;
    
    window.addEventListener('storage', function(e) {
        if (e.key === 'preferred-font-size' && e.newValue) {
            applyFontSize(e.newValue);
        }
    });

})();