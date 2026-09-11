# blueprints/groups_bp.py
# Complete file with all groups visible, join restricted by curriculum and tier

from flask import Blueprint, render_template, request, session, flash, redirect, url_for, jsonify, abort
from db import (
    track_group_click, get_group_by_id, get_group_categories_with_count,
    get_group_platforms_with_count, get_available_curricula, get_student_by_id
)
from services.group_service import (
    get_user_groups, get_featured_for_user, track_join
)
from services.tier_service import get_current_user_tier
from subjects_config import LOCATION_CURRICULA, get_subject, get_all_subjects
from config import Config
import json
import logging

logger = logging.getLogger(__name__)

groups_bp = Blueprint('groups', __name__, url_prefix='/groups')


@groups_bp.route('/')
def list_groups():
    """Display all active groups with join eligibility based on curriculum and tier."""
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    user_tier = get_current_user_tier()

    # Get all groups with eligibility info
    groups = get_user_groups(user_id)

    # Get featured groups with eligibility info
    featured_groups = get_featured_for_user(user_id)

    # Get curricula with counts (for tabs)
    curricula_data = get_available_curricula()
    curricula = []
    for code in curricula_data:
        label = get_curriculum_label(code)
        count = sum(1 for g in groups if g.get('curriculum') == code or (not g.get('curriculum') and not code))
        curricula.append({'code': code, 'name': label, 'count': count, 'icon': '📚'})

    total_groups = len(groups)

    # Get platforms with counts (based on all groups)
    platforms = get_group_platforms_with_count()
    categories = get_group_categories_with_count()

    # Apply platform filter if present
    platform_filter = request.args.get('platform', '')
    if platform_filter:
        groups = [g for g in groups if g.get('platform') == platform_filter]

    # Apply category filter if present
    category_filter = request.args.get('category', '')
    if category_filter:
        groups = [g for g in groups if g.get('category') == category_filter]

    # Pagination
    page = int(request.args.get('page', 1))
    per_page = 20
    total = len(groups)
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    start = (page - 1) * per_page
    end = start + per_page
    groups_page = groups[start:end]

    return render_template('dashboard/groups.html',
                         groups=groups_page,
                         featured_groups=featured_groups,
                         curricula=curricula,
                         total_groups=total_groups,
                         platforms=platforms,
                         categories=categories,
                         page=page,
                         total_pages=total_pages,
                         platform_filter=platform_filter,
                         category_filter=category_filter,
                         user_tier=user_tier,
                         join_rules=Config.GROUP_JOIN_RULES)


def get_curriculum_label(curriculum):
    """Get display label for curriculum."""
    labels = {
        'PL': '🇸🇴 Puntland',
        'SO': '🇸🇴 Somalia',
        'SL': '🇸🇴 Somaliland'
    }
    return labels.get(curriculum, curriculum or 'All')


@groups_bp.route('/details/<int:group_id>')
def group_details(group_id):
    """Get group details for modal."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    group = get_group_by_id(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404

    return jsonify({
        'id': group['id'],
        'name': group['name'],
        'platform': group['platform'],
        'category': group.get('category'),
        'clicks': group.get('click_count', 0),
        'description': group.get('description', ''),
        'invite_link': group.get('invite_link'),
        'is_active': group.get('is_active', 1)
    })


@groups_bp.route('/track-click/<int:group_id>', methods=['POST'])
def track_group_click_route(group_id):
    """Track when a user clicks the Join button."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    success = track_group_click(group_id)
    if success:
        return jsonify({'success': True})
    return jsonify({'error': 'Failed to track click'}), 500