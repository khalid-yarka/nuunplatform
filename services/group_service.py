# services/group_service.py
# Updated functions with Hore bypass and lock reason

import logging
from typing import Optional, Dict, List, Any
from db import (
    get_group_by_id,
    create_group_advanced,
    update_group_advanced,
    delete_group_advanced,
    toggle_group_active,
    toggle_group_featured,
    log_group_audit,
    get_group_audit_log,
    get_group_stats,
    get_featured_groups,
    get_active_groups,
    get_student_by_id,
    execute_with_retry
)
from services.tier_service import is_tier_at_least, get_current_user_tier
from subjects_config import get_subject, get_all_subjects

logger = logging.getLogger(__name__)


def get_curriculum_label(curriculum):
    """Get display label for curriculum."""
    labels = {
        'PL': '🇸🇴 Puntland',
        'SO': '🇸🇴 Somalia',
        'SL': '🇸🇴 Somaliland'
    }
    return labels.get(curriculum, curriculum or 'All')


def get_user_groups(user_id: Optional[int] = None):
    """
    Get all active groups with join eligibility.
    - Hore tier can join any group regardless of curriculum/tier.
    - For others: curriculum and tier must match.
    """
    from db import get_active_groups, get_student_by_id
    from services.tier_service import is_tier_at_least

    user_curriculum = None
    user_tier = 'danbe'

    if user_id:
        student = get_student_by_id(user_id)
        if student:
            user_curriculum = student.get('curriculum')
            user_tier = student.get('tier', 'danbe')

    all_groups = get_active_groups()
    visible_groups = []

    for group in all_groups:
        group_curriculum = group.get('curriculum', '')
        required_tier = group.get('tier_required', 'danbe')

        # Hore bypass: all groups are joinable
        if user_tier == 'hore':
            can_join = True
            locked = False
            join_block_reason = None
            curriculum_match = True
            tier_match = True
        else:
            curriculum_match = (not group_curriculum) or (group_curriculum == user_curriculum)
            tier_match = is_tier_at_least(user_tier, required_tier)
            can_join = curriculum_match and tier_match
            locked = not can_join
            if not curriculum_match:
                join_block_reason = 'curriculum'
            elif not tier_match:
                join_block_reason = 'tier'
            else:
                join_block_reason = None

        group['can_join'] = can_join
        group['locked'] = locked
        group['join_block_reason'] = join_block_reason
        group['required_tier'] = required_tier
        group['curriculum_match'] = curriculum_match
        group['tier_match'] = tier_match

        visible_groups.append(group)

    return visible_groups


def get_featured_for_user(user_id: Optional[int] = None):
    """Get featured groups with join eligibility (same logic as above)."""
    if not user_id:
        return []
    student = get_student_by_id(user_id)
    if not student:
        return []

    user_curriculum = student.get('curriculum')
    user_tier = student.get('tier', 'danbe')

    all_featured = get_featured_groups(limit=10)
    filtered = []
    for group in all_featured:
        group_curriculum = group.get('curriculum', '')
        required_tier = group.get('tier_required', 'danbe')

        if user_tier == 'hore':
            can_join = True
            locked = False
            join_block_reason = None
        else:
            curriculum_match = (not group_curriculum) or (group_curriculum == user_curriculum)
            tier_match = is_tier_at_least(user_tier, required_tier)
            can_join = curriculum_match and tier_match
            locked = not can_join
            if not curriculum_match:
                join_block_reason = 'curriculum'
            elif not tier_match:
                join_block_reason = 'tier'
            else:
                join_block_reason = None

        group['can_join'] = can_join
        group['locked'] = locked
        group['join_block_reason'] = join_block_reason
        group['required_tier'] = required_tier

        filtered.append(group)

    return filtered[:5]


def create_group(admin_id: int, data: Dict) -> tuple:
    """Create a new group with audit logging."""
    try:
        success = create_group_advanced(data)
        if success:
            cursor = execute_with_retry(
                "SELECT id FROM groups ORDER BY id DESC LIMIT 1"
            )
            result = cursor.fetchone()
            if result:
                group_id = result['id']
                log_group_audit(group_id, admin_id, 'create', None)
                return True, group_id
        return False, None
    except Exception as e:
        logger.error(f"Group creation error: {e}")
        return False, None


def update_group(admin_id: int, group_id: int, data: Dict) -> bool:
    """Update a group with audit logging."""
    try:
        old_group = get_group_by_id(group_id)
        if not old_group:
            return False

        changes = {}
        for key, value in data.items():
            if key in old_group and old_group[key] != value:
                changes[key] = {'old': old_group[key], 'new': value}

        success = update_group_advanced(group_id, data)
        if success and changes:
            log_group_audit(
                group_id,
                admin_id,
                'edit',
                str(changes)
            )
        elif success:
            log_group_audit(group_id, admin_id, 'edit', None)

        return success
    except Exception as e:
        logger.error(f"Group update error: {e}")
        return False


def delete_group(admin_id: int, group_id: int) -> bool:
    """Delete a group with audit logging."""
    try:
        log_group_audit(group_id, admin_id, 'delete', None)
        return delete_group_advanced(group_id)
    except Exception as e:
        logger.error(f"Group delete error: {e}")
        return False


def toggle_active(admin_id: int, group_id: int) -> bool:
    """Toggle group active status with audit logging."""
    try:
        group = get_group_by_id(group_id)
        if not group:
            return False
        new_status = 0 if group['is_active'] else 1
        success = toggle_group_active(group_id)
        if success:
            log_group_audit(
                group_id,
                admin_id,
                'activate' if new_status else 'deactivate',
                None
            )
        return success
    except Exception as e:
        logger.error(f"Toggle active error: {e}")
        return False


def toggle_featured(admin_id: int, group_id: int) -> bool:
    """Toggle group featured status with audit logging."""
    try:
        group = get_group_by_id(group_id)
        if not group:
            return False
        new_status = 0 if group['is_featured'] else 1
        success = toggle_group_featured(group_id)
        if success:
            log_group_audit(
                group_id,
                admin_id,
                'feature' if new_status else 'unfeature',
                None
            )
        return success
    except Exception as e:
        logger.error(f"Toggle featured error: {e}")
        return False


def get_admin_group_list(
    search: str = '',
    platform: str = '',
    category: str = '',
    status: str = '',
    page: int = 1,
    per_page: int = 20
) -> tuple:
    """Get groups for admin panel with filters."""
    offset = (page - 1) * per_page

    query = "SELECT * FROM groups WHERE 1=1"
    params = []

    if search:
        query += " AND (name LIKE ? OR description LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like])

    if platform:
        query += " AND platform = ?"
        params.append(platform)

    if category:
        query += " AND category = ?"
        params.append(category)

    if status == 'active':
        query += " AND is_active = 1"
    elif status == 'inactive':
        query += " AND is_active = 0"

    query += " ORDER BY display_order ASC, created_at DESC LIMIT ? OFFSET ?"
    params.extend([per_page, offset])

    cursor = execute_with_retry(query, params)
    groups = [dict(row) for row in cursor.fetchall()]

    count_query = "SELECT COUNT(*) as total FROM groups WHERE 1=1"
    count_params = []
    if search:
        count_query += " AND (name LIKE ? OR description LIKE ?)"
        count_params.extend([like, like])
    if platform:
        count_query += " AND platform = ?"
        count_params.append(platform)
    if category:
        count_query += " AND category = ?"
        count_params.append(category)
    if status == 'active':
        count_query += " AND is_active = 1"
    elif status == 'inactive':
        count_query += " AND is_active = 0"

    cursor = execute_with_retry(count_query, count_params)
    result = cursor.fetchone()
    total = result['total'] if result else 0

    return groups, total


def get_curriculum_subjects(curriculum):
    """Get subjects available for a curriculum."""
    from subjects_config import get_subjects_for_user
    return get_subjects_for_user(curriculum)


def track_join(group_id: int, user_id: int) -> bool:
    """Track when a user joins a group."""
    try:
        track_group_click(group_id)
        return True
    except Exception as e:
        logger.error(f"Error tracking join: {e}")
        return False