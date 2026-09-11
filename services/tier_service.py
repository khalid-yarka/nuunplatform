# services/tier_service.py
# Central tier logic for Nuunqalin.

import time
import logging
from typing import Optional, Any, Dict
from flask import session
from db import execute_with_retry, get_student_by_id
from tier_config import Tier, FEATURES, LIMITS, get_feature, get_limit, get_tier_level

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Tier Retrieval
# -------------------------------------------------------------------

def get_user_tier(user_id: int) -> str:
    """Get the tier for a user. Defaults to 'danbe' if not found."""
    student = get_student_by_id(user_id)
    if not student:
        return Tier.DANBE
    return student.get('tier', Tier.DANBE)

def get_current_user_tier() -> str:
    """Get tier of the currently logged-in user."""
    user_id = session.get('user_id')
    if not user_id:
        return Tier.DANBE
    return get_user_tier(user_id)

def set_user_tier(user_id: int, new_tier: str, admin_id: Optional[int] = None) -> bool:
    """Set a user's tier. Also updates tier_updated_at and logs if admin_id provided."""
    if new_tier not in [Tier.DANBE, Tier.DHEXE, Tier.HORE]:
        return False
    from db import execute_with_retry
    from datetime import datetime
    now = datetime.now().isoformat()
    execute_with_retry(
        "UPDATE students SET tier = ?, tier_updated_at = ? WHERE id = ?",
        (new_tier, now, user_id),
        commit=True
    )
    return True

# -------------------------------------------------------------------
# Feature Checks
# -------------------------------------------------------------------

def has_feature(feature_code: str, user_id: Optional[int] = None) -> bool:
    """Check if a user has a boolean feature (permission)."""
    if user_id is None:
        user_id = session.get('user_id')
    tier = get_user_tier(user_id) if user_id else Tier.DANBE
    return get_feature(feature_code, tier) or False

def get_feature_level(feature_code: str, user_id: Optional[int] = None) -> int:
    """Get the level (0-3) for a feature."""
    if user_id is None:
        user_id = session.get('user_id')
    tier = get_user_tier(user_id) if user_id else Tier.DANBE
    return get_feature(feature_code, tier) or 0

def get_feature_limit(limit_code: str, user_id: Optional[int] = None) -> Optional[int]:
    """Get a numeric limit for a feature (None = unlimited)."""
    if user_id is None:
        user_id = session.get('user_id')
    tier = get_user_tier(user_id) if user_id else Tier.DANBE
    return get_limit(limit_code, tier)

# -------------------------------------------------------------------
# Convenience functions for the 24 features
# -------------------------------------------------------------------

def can_create_live_quiz(user_id: Optional[int] = None) -> bool:
    return has_feature("create_live_quiz", user_id)

def can_schedule_live_quiz(user_id: Optional[int] = None) -> bool:
    return has_feature("scheduled_live_quiz", user_id)

def can_create_private_live_quiz(user_id: Optional[int] = None) -> bool:
    return has_feature("private_live_quiz", user_id)

def can_access_premium_resources(user_id: Optional[int] = None) -> bool:
    return has_feature("premium_resources", user_id)

def get_quiz_questions_limit(user_id: Optional[int] = None) -> int:
    limit = get_feature_limit("quiz_questions_limit", user_id)
    return limit if limit is not None else 999  # effectively unlimited

def get_quiz_attempt_limit(user_id: Optional[int] = None) -> Optional[int]:
    return get_feature_limit("quiz_attempt_limit", user_id)

def get_resource_download_limit(user_id: Optional[int] = None) -> Optional[int]:
    return get_feature_limit("resource_download_limit", user_id)

def get_saved_content_limit(user_id: Optional[int] = None) -> Optional[int]:
    return get_feature_limit("saved_content_limit", user_id)

def get_analytics_level(user_id: Optional[int] = None) -> int:
    return get_feature_level("basic_statistics", user_id)

def get_answer_review_level(user_id: Optional[int] = None) -> int:
    return get_feature_level("answer_review", user_id)

def get_explanation_level(user_id: Optional[int] = None) -> int:
    return get_feature_level("correct_answer_explanations", user_id)

def get_achievement_history_level(user_id: Optional[int] = None) -> int:
    return get_feature_level("achievement_history", user_id)

def get_badge_showcase_level(user_id: Optional[int] = None) -> int:
    return get_feature_level("badge_showcase", user_id)

def get_resource_search_level(user_id: Optional[int] = None) -> int:
    return get_feature_level("resource_search", user_id)

# -------------------------------------------------------------------
# Quota System
# -------------------------------------------------------------------

def get_remaining_quota(user_id: int, metric_code: str) -> int:
    """Get remaining quota for a metric (quiz_attempt, resource_download)."""
    from datetime import date
    today = date.today().isoformat()
    limit = get_feature_limit(metric_code + "_limit", user_id)
    if limit is None:
        return 999  # unlimited
    cursor = execute_with_retry(
        "SELECT usage_count FROM user_usage WHERE user_id = ? AND metric_code = ? AND period_start = ?",
        (user_id, metric_code, today)
    )
    row = cursor.fetchone()
    used = row['usage_count'] if row else 0
    return max(0, limit - used)

def check_and_consume_quota(user_id: int, metric_code: str) -> bool:
    """
    Atomically consume one unit of quota for the given metric.
    Returns True if quota was consumed, False if limit already reached.
    Uses atomic INSERT ... ON CONFLICT ... WHERE usage_count < limit.
    """
    from datetime import date
    today = date.today().isoformat()
    limit = get_feature_limit(metric_code + "_limit", user_id)
    if limit is None:
        # Unlimited – no tracking needed
        return True

    try:
        from db import get_db
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_usage (user_id, metric_code, period_start, usage_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, metric_code, period_start) DO UPDATE SET
                usage_count = usage_count + 1,
                updated_at = datetime('now', 'localtime')
            WHERE usage_count < ?
        """, (user_id, metric_code, today, limit))
        if cursor.rowcount > 0:
            conn.commit()
            return True
        else:
            conn.rollback()
            return False
    except Exception as e:
        logger.error(f"Quota consumption error: {e}")
        return False

# -------------------------------------------------------------------
# Wrappers for specific quotas
# -------------------------------------------------------------------

def get_resource_downloads_remaining(user_id: int) -> int:
    """Get remaining resource downloads for today."""
    return get_remaining_quota(user_id, "resource_download")

def get_quiz_attempts_remaining(user_id: int) -> int:
    """Get remaining quiz attempts for today."""
    return get_remaining_quota(user_id, "quiz_attempt")

def consume_quiz_attempt(user_id: int) -> bool:
    """Consume one quiz attempt atomically."""
    return check_and_consume_quota(user_id, "quiz_attempt")

def consume_resource_download(user_id: int) -> bool:
    """Consume one resource download atomically."""
    return check_and_consume_quota(user_id, "resource_download")

# -------------------------------------------------------------------
# Saved Content Helpers
# -------------------------------------------------------------------

def get_saved_content_count(user_id: int) -> int:
    """Get the number of saved items for a user."""
    cursor = execute_with_retry(
        "SELECT COUNT(*) as count FROM saved_content WHERE user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()
    return row['count'] if row else 0

def can_save_content(user_id: int) -> bool:
    """Check if a user can add another saved item."""
    limit = get_saved_content_limit(user_id)
    if limit is None:
        return True
    return get_saved_content_count(user_id) < limit

# -------------------------------------------------------------------
# Tier Comparison
# -------------------------------------------------------------------

def is_tier_at_least(tier: str, required_tier: str) -> bool:
    """Check if a tier is at least the required tier."""
    return get_tier_level(tier) >= get_tier_level(required_tier)
    
# ============================================
# QUESTION COUNT TIER HELPERS
# ============================================

def get_allowed_question_counts(user_id: Optional[int] = None) -> Dict[str, bool]:
    """
    Returns dict: { '10': True/False, '20': True/False, '30': True/False, 'custom': True/False }
    based on user's tier.
    """
    if user_id is None:
        user_id = session.get('user_id')
    tier = get_user_tier(user_id) if user_id else Tier.DANBE

    if tier == Tier.DANBE:
        return {'10': True, '20': False, '30': False, 'custom': False}
    elif tier == Tier.DHEXE:
        return {'10': True, '20': True, '30': True, 'custom': False}
    else:  # Hore
        return {'10': True, '20': True, '30': True, 'custom': True}


def is_custom_question_count_allowed(user_id: Optional[int] = None) -> bool:
    if user_id is None:
        user_id = session.get('user_id')
    tier = get_user_tier(user_id) if user_id else Tier.DANBE
    return tier == Tier.HORE


def validate_question_count(user_id: int, count: int) -> bool:
    """Check if the requested count is allowed for the user's tier."""
    allowed = get_allowed_question_counts(user_id)
    if count == 10 and allowed['10']:
        return True
    if count == 20 and allowed['20']:
        return True
    if count == 30 and allowed['30']:
        return True
    if count > 0 and allowed['custom']:
        return True
    return False    

# ============================================
# HISTORY TIER HELPERS
# ============================================

def get_history_retention_days(user_id: int) -> Optional[int]:
    """Get the retention period in days for a user's history."""
    return get_feature_limit("history_retention_days", user_id)


def get_history_max_entries(user_id: int) -> Optional[int]:
    """Get the maximum number of history entries for a user."""
    return get_feature_limit("history_max_entries", user_id)


def can_search_history(user_id: int) -> bool:
    """Check if a user can search their history."""
    return has_feature("history_search", user_id)


def can_export_history(user_id: int) -> bool:
    """Check if a user can export their history."""
    return has_feature("history_export", user_id)


def can_see_trends(user_id: int) -> bool:
    """Check if a user can see history trends/charts."""
    return has_feature("history_trends", user_id)


def can_delete_history(user_id: int) -> bool:
    """Check if a user can delete history entries."""
    return has_feature("history_delete", user_id)