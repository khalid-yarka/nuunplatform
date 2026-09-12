# blueprints/focus_bp.py
# ---------------------------------------------------------------
# Focus page -- where wrong answers become next study session.
# ---------------------------------------------------------------

from flask import Blueprint, render_template, session, redirect, url_for, flash

from services import entitlement_service
from services.focus_service import (
    get_suggested_sources,
    get_analytics_data,
    get_focus_tips,
)


focus_bp = Blueprint('focus', __name__, url_prefix='/focus')


@focus_bp.route('/')
def index():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']

    suggestion_limit = entitlement_service.get_limit(user_id, 'focus_suggestions')
    analytics_level = entitlement_service.get_level(user_id, 'focus_analytics')

    # suggestion_limit: None = unlimited, 0 = disabled, N = cap
    has_suggestions = (suggestion_limit is None) or (suggestion_limit > 0)
    has_analytics = analytics_level > 0
    has_access = has_suggestions or has_analytics

    suggestions = []
    total_suggestions = 0
    more_available = 0

    if has_suggestions:
        all_sources = get_suggested_sources(user_id, None)  # unlimited
        total_suggestions = len(all_sources)
        if suggestion_limit is None:
            suggestions = all_sources
        else:
            suggestions = all_sources[:suggestion_limit]
            more_available = max(0, total_suggestions - suggestion_limit)

    analytics = {}
    if has_analytics:
        analytics = get_analytics_data(user_id, analytics_level)

    tips = []
    if analytics_level >= 3:
        tips = get_focus_tips(user_id)

    return render_template(
        'dashboard/focus.html',
        has_access=has_access,
        has_suggestions=has_suggestions,
        has_analytics=has_analytics,
        suggestion_limit=suggestion_limit,
        analytics_level=analytics_level,
        suggestions=suggestions,
        total_suggestions=total_suggestions,
        more_available=more_available,
        analytics=analytics,
        tips=tips,
    )