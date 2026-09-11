# blueprints/admin_platform_bp.py
# Admin platform-monitoring dashboard.
# Renders stats, timelines, distributions, and an activity feed.

import logging
from flask import (
    Blueprint, render_template, request, session, jsonify,
    flash, redirect, url_for, abort,
)

from db import is_admin
from platform_activity import (
    get_platform_stats,
    get_signups_series,
    get_quizzes_series,
    get_live_quizzes_series,
    get_tier_distribution,
    get_location_distribution,
    get_top_subjects,
    get_top_performers,
    get_activity_feed,
    broadcast_to_admins,
)
from utils import validate_csrf
from functools import wraps

logger = logging.getLogger(__name__)

admin_platform_bp = Blueprint('admin_platform', __name__, url_prefix='/admin/platform')


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first.', 'error')
            return redirect(url_for('auth.login'))
        if not is_admin(session['user_id']):
            flash('Access denied. Admin only.', 'error')
            return redirect(url_for('dashboard.home'))
        return f(*args, **kwargs)
    return decorated


# ============================================
# MAIN PAGE
# ============================================

@admin_platform_bp.route('/')
@admin_required
def index():
    days = int(request.args.get('days', 30))
    if days not in (7, 14, 30, 90):
        days = 30

    stats = get_platform_stats()
    signups = get_signups_series(days=days)
    quizzes = get_quizzes_series(days=days)
    live_quizzes = get_live_quizzes_series(days=days)
    tier_dist = get_tier_distribution()
    location_dist = get_location_distribution()
    top_subjects = get_top_subjects(limit=5)
    top_performers = get_top_performers(limit=5)
    feed = get_activity_feed(limit=40)

    return render_template(
        'dashboard/admin/platform.html',
        stats=stats,
        days=days,
        signups=signups,
        quizzes=quizzes,
        live_quizzes=live_quizzes,
        tier_dist=tier_dist,
        location_dist=location_dist,
        top_subjects=top_subjects,
        top_performers=top_performers,
        feed=feed,
    )


# ============================================
# JSON APIs (for live refresh)
# ============================================

@admin_platform_bp.route('/api/stats')
@admin_required
def api_stats():
    return jsonify(get_platform_stats())


@admin_platform_bp.route('/api/feed')
@admin_required
def api_feed():
    limit = min(100, int(request.args.get('limit', 40)))
    source = request.args.get('source', 'all')
    return jsonify({'events': get_activity_feed(limit=limit, source=source)})


# ============================================
# BROADCAST TO ALL ADMINS
# ============================================

@admin_platform_bp.route('/broadcast', methods=['POST'])
@admin_required
def broadcast():
    if not validate_csrf():
        abort(403)

    title = (request.form.get('title') or '').strip()
    body = (request.form.get('body') or '').strip()
    link = (request.form.get('link') or '/admin/platform').strip()
    icon = (request.form.get('icon') or '📡').strip()[:4]

    if not title or not body:
        flash('Title and message are required.', 'error')
        return redirect(url_for('admin_platform.index'))

    sent = broadcast_to_admins(
        title=title,
        body=body,
        link=link,
        icon=icon,
        exclude_admin_id=session['user_id'],  # don't notify yourself
    )

    flash(f'Broadcast sent to {sent} admin(s).', 'success')
    return redirect(url_for('admin_platform.index'))