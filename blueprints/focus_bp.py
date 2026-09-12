# blueprints/focus_bp.py
# ---------------------------------------------------------------
# Focus page -- where activity becomes next study session.
#
# Sections:
#   - Suggested Sources (from wrong answers)
#   - Bookmarks (saved + liked questions)
#   - Analytics (charts)
#   - Tips (level 3)
# ---------------------------------------------------------------

from flask import Blueprint, render_template, session, redirect, url_for, flash

from services import entitlement_service
from services.focus_service import (
    get_suggested_sources,
    get_bookmarks,
    get_analytics_data,
    get_focus_tips,
)


focus_bp = Blueprint('focus', __name__, url_prefix='/focus')

# Default number of items shown per section before "Show all" is needed.
DEFAULT_EXPAND = 5


@focus_bp.route('/')
def index():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']

    # ---------- Entitlements ----------
    sources_quota = entitlement_service.get_limit(user_id, 'focus_suggestions')
    bookmarks_quota = entitlement_service.get_limit(user_id, 'focus_bookmarks')
    analytics_level = entitlement_service.get_level(user_id, 'focus_analytics')

    sources_unlimited = sources_quota is None
    bookmarks_unlimited = bookmarks_quota is None

    has_sources_access = sources_unlimited or (sources_quota or 0) > 0
    has_bookmarks_access = bookmarks_unlimited or (bookmarks_quota or 0) > 0
    has_analytics_access = analytics_level > 0
    has_any_access = has_sources_access or has_bookmarks_access or has_analytics_access

    # ---------- Suggested Sources ----------
    sources = []
    sources_total = 0
    sources_more_count = 0
    if has_sources_access:
        all_sources = get_suggested_sources(user_id, None)
        sources_total = len(all_sources)
        if sources_unlimited:
            sources = all_sources
        else:
            sources = all_sources[:sources_quota]
            sources_more_count = max(0, sources_total - sources_quota)

    # ---------- Bookmarks ----------
    bookmarks = []
    bookmark_stats = {'total': 0, 'saved_count': 0, 'liked_count': 0}
    bookmarks_more_count = 0
    if has_bookmarks_access:
        effective_limit = None if bookmarks_unlimited else bookmarks_quota
        result = get_bookmarks(user_id, effective_limit)
        bookmarks = result['items']
        bookmark_stats = {
            'total': result['total'],
            'saved_count': result['saved_count'],
            'liked_count': result['liked_count'],
        }
        bookmarks_more_count = result['more_count']

    # ---------- Analytics ----------
    analytics = {}
    if has_analytics_access:
        analytics = get_analytics_data(user_id, analytics_level)

    # ---------- Tips ----------
    tips = []
    if analytics_level >= 3:
        tips = get_focus_tips(user_id)

    # ---------- Display thresholds ----------
    sources_displayed = min(DEFAULT_EXPAND, len(sources))
    bookmarks_displayed = min(DEFAULT_EXPAND, len(bookmarks))

    return render_template(
        'dashboard/focus.html',
        has_any_access=has_any_access,
        has_sources_access=has_sources_access,
        has_bookmarks_access=has_bookmarks_access,
        has_analytics_access=has_analytics_access,

        sources=sources,
        sources_total=sources_total,
        sources_displayed=sources_displayed,
        sources_more_count=sources_more_count,
        sources_quota=sources_quota,
        sources_unlimited=sources_unlimited,

        bookmarks=bookmarks,
        bookmark_stats=bookmark_stats,
        bookmarks_displayed=bookmarks_displayed,
        bookmarks_more_count=bookmarks_more_count,
        bookmarks_quota=bookmarks_quota,
        bookmarks_unlimited=bookmarks_unlimited,

        analytics=analytics,
        analytics_level=analytics_level,
        tips=tips,
    )