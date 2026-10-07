# blueprints/groups_bp.py
# Student-facing study groups.
#
# The student page reads only:
#   · groups           — every visible, active group with join eligibility
#   · featured_groups  — the highlighted rail (max 5)
#   · total_groups     — a count for the hero subtitle
#   · user_tier        — passed for analytics / future use
#   · join_rules       — the rules block inside the join modal
#
# Eligibility (locked / unlocked / block reason / required tier) is
# computed by services.group_service.get_user_groups(), which reads
# the renamed columns (groups.location, groups.stream) directly.

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, jsonify,
)
from db import (
    track_group_click, get_group_by_id,
)
from services.group_service import (
    get_user_groups, get_featured_for_user,
)
from services.tier_service import get_current_user_tier
from config import Config
import logging

logger = logging.getLogger(__name__)

groups_bp = Blueprint('groups', __name__, url_prefix='/groups')


@groups_bp.route('/')
def list_groups():
    """Display every active group with join eligibility attached."""
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    user_tier = get_current_user_tier()

    groups = get_user_groups(user_id)
    featured_groups = get_featured_for_user(user_id)

    return render_template(
        'dashboard/groups.html',
        groups=groups,
        featured_groups=featured_groups,
        total_groups=len(groups),
        user_tier=user_tier,
        join_rules=Config.GROUP_JOIN_RULES,
    )


@groups_bp.route('/details/<int:group_id>')
def group_details(group_id):
    """JSON lookup used by anything that needs a single group's metadata."""
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
        'is_active': group.get('is_active', 1),
        'location': group.get('location', ''),
        'stream': group.get('stream', ''),
    })


@groups_bp.route('/track-click/<int:group_id>', methods=['POST'])
def track_group_click_route(group_id):
    """Increment the click counter when a student opens a group."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    if track_group_click(group_id):
        return jsonify({'success': True})
    return jsonify({'error': 'Failed to track click'}), 500