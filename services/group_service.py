# services/group_service.py
# Group listing, access rules, and admin operations.
# Two-tier model (free / premium). No pro bypass.
#
# ─── ACCESS MODEL ─────────────────────────────────────────────
# Every group is gated by four independent checks. All must pass
# for a user to join:
#
#   1. LOCATION — groups.location ('' / 'SO' / 'PL' / 'SL')
#        · empty       → open to everyone
#        · 'SO' / 'SL' → only students whose location matches
#        · 'PL'        → only students whose location is 'PL'
#      Premium does NOT bypass this gate.
#
#   2. STREAM — groups.stream ('' / 'general' / 'science' / 'arts')
#        · Only consulted when group.location == 'PL'
#        · empty       → any PL student may join
#        · set         → student's curriculum (stream) must match
#      Ignored entirely for non-PL groups.
#
#   3. TIER — groups.tier_required ('free' / 'premium')
#        · 'free'      → everyone
#        · 'premium'   → premium students only
#
#   4. VERIFIED — groups.requires_verified
#        · 0           → everyone
#        · 1           → only verified accounts
#
# Column naming note:
#   groups.location  used to be called groups.curriculum. It has
#   always held location codes (SO/PL/SL), not stream values.
#   students.curriculum holds the stream values. Despite the
#   shared word, the two columns mean different things.
# ──────────────────────────────────────────────────────────────

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
    execute_with_retry,
)
from tier_config import normalize_tier

logger = logging.getLogger(__name__)


# ============================================================
# CONSTANTS
# ============================================================

VALID_LOCATIONS = ('', 'SO', 'PL', 'SL')
VALID_STREAMS   = ('', 'general', 'science', 'arts')

# Only this location uses the stream gate.
STREAM_REQUIRED_LOCATION = 'PL'


# ============================================================
# HELPERS
# ============================================================

def get_curriculum_label(curriculum):
    """Human label for a location code. Kept for backward compat."""
    labels = {
        'PL': '🇸🇱 Puntland',
        'SO': '🇸🇴 Somalia',
        'SL': '🇮🇷 Somaliland',
    }
    return labels.get(curriculum, curriculum or 'All')


def _norm(value):
    """Normalise a string: strip whitespace, uppercase. None → ''."""
    return (value or '').strip().upper()


def _evaluate_access(group: Dict, user: Optional[Dict]) -> Dict:
    """
    Decide whether `user` may join `group`.

    Returns a dict merged onto the group row before rendering:
      can_join, locked, join_block_reason,
      location_match, stream_match, tier_match, verified_match,
      required_tier
    """
    required_tier = normalize_tier(group.get('tier_required') or 'free')
    requires_verified = bool(group.get('requires_verified', 0))

    group_location = _norm(group.get('location'))
    group_stream   = (group.get('stream') or '').strip().lower()

    # ─── Anonymous visitor ────────────────────────────────
    if user is None:
        return {
            'can_join':          False,
            'locked':            True,
            'join_block_reason': 'login',
            'location_match':    False,
            'stream_match':      False,
            'tier_match':        False,
            'verified_match':    False,
            'required_tier':     required_tier,
        }

    user_location = _norm(user.get('location'))
    user_stream   = (user.get('curriculum') or '').strip().lower()
    user_tier     = normalize_tier(user.get('tier') or 'free')
    user_verified = bool(user.get('is_verified'))

    # ─── 1. LOCATION gate ─────────────────────────────────
    #   Empty group location = "All curricula" → open to everyone.
    #   Otherwise, the student's location must match exactly.
    location_match = (not group_location) or (group_location == user_location)

    # ─── 2. STREAM gate (PL only) ─────────────────────────
    #   Only consulted when the group is scoped to Puntland.
    #   Empty stream = any PL student may join.
    if group_location == STREAM_REQUIRED_LOCATION and group_stream:
        stream_match = (group_stream == user_stream)
    else:
        stream_match = True

    # ─── 3. TIER gate ─────────────────────────────────────
    tier_match = (required_tier == 'free') or (user_tier == 'premium')

    # ─── 4. VERIFIED gate ─────────────────────────────────
    verified_match = (not requires_verified) or user_verified

    can_join = location_match and stream_match and tier_match and verified_match

    if can_join:
        block = None
    elif not location_match:
        block = 'location'
    elif not stream_match:
        block = 'stream'
    elif not tier_match:
        block = 'tier'
    elif not verified_match:
        block = 'verified'
    else:
        block = None

    return {
        'can_join':          can_join,
        'locked':            not can_join,
        'join_block_reason': block,
        'location_match':    location_match,
        'stream_match':      stream_match,
        'tier_match':        tier_match,
        'verified_match':    verified_match,
        'required_tier':     required_tier,
    }


# ============================================================
# PUBLIC LISTING
# ============================================================

def get_user_groups(user_id: Optional[int] = None):
    """
    Return every active, visible group with join eligibility attached.
    """
    user = None
    if user_id:
        user = get_student_by_id(user_id)

    all_groups = get_active_groups()
    result = []

    for group in all_groups:
        # Visibility filter — hidden groups never appear.
        if not group.get('is_visible', 1):
            continue

        access = _evaluate_access(group, user)
        group.update(access)
        result.append(group)

    return result


def get_featured_for_user(user_id: Optional[int] = None):
    """
    Featured groups with the same access rules, capped at 5.
    """
    if not user_id:
        return []

    user = get_student_by_id(user_id)
    if not user:
        return []

    featured = get_featured_groups(limit=10)
    result = []

    for group in featured:
        if not group.get('is_visible', 1):
            continue

        access = _evaluate_access(group, user)
        group.update(access)
        result.append(group)

    return result[:5]


# ============================================================
# ADMIN OPERATIONS
# ============================================================

def create_group(admin_id: int, data: Dict) -> tuple:
    try:
        success = create_group_advanced(data)
        if success:
            cursor = execute_with_retry(
                "SELECT id FROM groups ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                group_id = row['id']
                log_group_audit(group_id, admin_id, 'create', None)
                return True, group_id
        return False, None
    except Exception as e:
        logger.error(f"Group creation error: {e}")
        return False, None


def update_group(admin_id: int, group_id: int, data: Dict) -> bool:
    try:
        old_group = get_group_by_id(group_id)
        if not old_group:
            return False

        changes = {}
        for key, value in data.items():
            if key in old_group and old_group[key] != value:
                changes[key] = {'old': old_group[key], 'new': value}

        success = update_group_advanced(group_id, data)
        if success:
            log_group_audit(
                group_id, admin_id, 'edit',
                str(changes) if changes else None,
            )

        return success
    except Exception as e:
        logger.error(f"Group update error: {e}")
        return False


def delete_group(admin_id: int, group_id: int) -> bool:
    try:
        log_group_audit(group_id, admin_id, 'delete', None)
        return delete_group_advanced(group_id)
    except Exception as e:
        logger.error(f"Group delete error: {e}")
        return False


def toggle_active(admin_id: int, group_id: int) -> bool:
    try:
        group = get_group_by_id(group_id)
        if not group:
            return False
        new_status = 0 if group['is_active'] else 1
        success = toggle_group_active(group_id)
        if success:
            log_group_audit(
                group_id, admin_id,
                'activate' if new_status else 'deactivate', None,
            )
        return success
    except Exception as e:
        logger.error(f"Toggle active error: {e}")
        return False


def toggle_featured(admin_id: int, group_id: int) -> bool:
    try:
        group = get_group_by_id(group_id)
        if not group:
            return False
        new_status = 0 if group['is_featured'] else 1
        success = toggle_group_featured(group_id)
        if success:
            log_group_audit(
                group_id, admin_id,
                'feature' if new_status else 'unfeature', None,
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
    per_page: int = 20,
) -> tuple:
    offset = (page - 1) * per_page
    query = "SELECT * FROM groups WHERE 1=1"
    params: List[Any] = []

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
    groups = [dict(r) for r in cursor.fetchall()]

    count_query = "SELECT COUNT(*) AS total FROM groups WHERE 1=1"
    count_params: List[Any] = []
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
    row = cursor.fetchone()
    total = row['total'] if row else 0
    return groups, total


def get_curriculum_subjects(curriculum):
    from subjects_config import get_subjects_for_user
    return get_subjects_for_user(curriculum)


def track_join(group_id: int, user_id: int) -> bool:
    try:
        from db import track_group_click
        track_group_click(group_id)
        return True
    except Exception as e:
        logger.error(f"Error tracking join: {e}")
        return False