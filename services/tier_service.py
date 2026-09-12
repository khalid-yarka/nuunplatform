# services/tier_service.py
# Central tier logic for NuunPlatform.
#
# Vocabulary:  free / premium / pro
# Legacy aliases (danbe / dhexe / hore) are auto-normalized via
# tier_config.normalize_tier() so this transition is transparent.

import logging
from typing import Optional, Any, Dict
from flask import session

from db import execute_with_retry, get_student_by_id
from tier_config import (
    Tier, FEATURES, LIMITS, get_feature, get_limit, get_tier_level,
    normalize_tier,
)

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------
# Tier retrieval
# -------------------------------------------------------------------

def get_user_tier(user_id: int) -> str:
    """Get the tier for a user. Auto-normalizes legacy values."""
    student = get_student_by_id(user_id)
    if not student:
        return Tier.FREE
    return normalize_tier(student.get('tier', Tier.FREE))


def get_current_user_tier() -> str:
    """Get tier of the currently logged-in user. Auto-normalizes."""
    user_id = session.get('user_id')
    if not user_id:
        return Tier.FREE
    return get_user_tier(user_id)


def set_user_tier(user_id: int, new_tier: str,
                  admin_id: Optional[int] = None) -> bool:
    """
    Set a user's tier. Accepts canonical or legacy input; stores canonical.
    Also updates tier_updated_at.
    """
    new_tier = normalize_tier(new_tier)
    if new_tier not in (Tier.FREE, Tier.PREMIUM, Tier.PRO):
        return False
    from datetime import datetime
    now = datetime.now().isoformat()
    execute_with_retry(
        "UPDATE students SET tier = ?, tier_updated_at = ? WHERE id = ?",
        (new_tier, now, user_id),
        commit=True,
    )
    return True


# -------------------------------------------------------------------
# Feature checks
# -------------------------------------------------------------------

def has_feature(feature_code: str, user_id: Optional[int] = None) -> bool:
    """Check if a user has a boolean feature (permission)."""
    if user_id is None:
        user_id = session.get('user_id')
    tier = get_user_tier(user_id) if user_id else Tier.FREE
    return get_feature(feature_code, tier) or False


def get_feature_level(feature_code: str, user_id: Optional[int] = None) -> int:
    """Get the level (0–3) for a feature."""
    if user_id is None:
        user_id = session.get('user_id')
    tier = get_user_tier(user_id) if user_id else Tier.FREE
    return get_feature(feature_code, tier) or 0


def get_feature_limit(limit_code: str, user_id: Optional[int] = None) -> Optional[int]:
    """Get a numeric limit for a feature (None = unlimited)."""
    if user_id is None:
        user_id = session.get('user_id')
    tier = get_user_tier(user_id) if user_id else Tier.FREE
    return get_limit(limit_code, tier)


# -------------------------------------------------------------------
# Convenience wrappers
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
    return limit if limit is not None else 999


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
# Quota system
# -------------------------------------------------------------------

def get_remaining_quota(user_id: int, metric_code: str) -> int:
    """
    Get remaining quota for a metric.
    Returns 999 for unlimited features (kept for backward compatibility).
    """
    from datetime import date
    today = date.today().isoformat()
    limit = get_feature_limit(metric_code + "_limit", user_id)
    if limit is None:
        return 999
    cursor = execute_with_retry(
        "SELECT usage_count FROM user_usage "
        "WHERE user_id = ? AND metric_code = ? AND period_start = ?",
        (user_id, metric_code, today),
    )
    row = cursor.fetchone()
    used = row['usage_count'] if row else 0
    return max(0, limit - used)


def check_and_consume_quota(user_id: int, metric_code: str) -> bool:
    """
    Atomically consume one unit of quota.
    Returns True if consumed, False if limit already reached.
    """
    from datetime import date
    today = date.today().isoformat()
    limit = get_feature_limit(metric_code + "_limit", user_id)
    if limit is None:
        return True

    try:
        from db import get_db
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_usage
                (user_id, metric_code, period_start, usage_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, metric_code, period_start) DO UPDATE SET
                usage_count = usage_count + 1,
                updated_at = datetime('now', 'localtime')
            WHERE usage_count < ?
        """, (user_id, metric_code, today, limit))
        if cursor.rowcount > 0:
            conn.commit()
            return True
        conn.rollback()
        return False
    except Exception as e:
        logger.error(f"Quota consumption error: {e}")
        return False


# -------------------------------------------------------------------
# Quota convenience wrappers
# -------------------------------------------------------------------

def get_resource_downloads_remaining(user_id: int) -> int:
    return get_remaining_quota(user_id, "resource_download")


def get_quiz_attempts_remaining(user_id: int) -> int:
    return get_remaining_quota(user_id, "quiz_attempt")


def consume_quiz_attempt(user_id: int) -> bool:
    return check_and_consume_quota(user_id, "quiz_attempt")


def consume_resource_download(user_id: int) -> bool:
    return check_and_consume_quota(user_id, "resource_download")


# -------------------------------------------------------------------
# Saved content helpers
# -------------------------------------------------------------------

def get_saved_content_count(user_id: int) -> int:
    cursor = execute_with_retry(
        "SELECT COUNT(*) as count FROM saved_content WHERE user_id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    return row['count'] if row else 0


def can_save_content(user_id: int) -> bool:
    limit = get_saved_content_limit(user_id)
    if limit is None:
        return True
    return get_saved_content_count(user_id) < limit


# -------------------------------------------------------------------
# Tier comparison
# -------------------------------------------------------------------

def is_tier_at_least(tier: str, required_tier: str) -> bool:
    return get_tier_level(tier) >= get_tier_level(required_tier)


# -------------------------------------------------------------------
# Question-count tier helpers
# -------------------------------------------------------------------

def get_allowed_question_counts(user_id: Optional[int] = None) -> Dict[str, bool]:
    if user_id is None:
        user_id = session.get('user_id')
    tier = get_user_tier(user_id) if user_id else Tier.FREE

    if tier == Tier.FREE:
        return {'10': True, '20': False, '30': False, 'custom': False}
    elif tier == Tier.PREMIUM:
        return {'10': True, '20': True, '30': True, 'custom': False}
    else:
        return {'10': True, '20': True, '30': True, 'custom': True}


def is_custom_question_count_allowed(user_id: Optional[int] = None) -> bool:
    if user_id is None:
        user_id = session.get('user_id')
    tier = get_user_tier(user_id) if user_id else Tier.FREE
    return tier == Tier.PRO


def validate_question_count(user_id: int, count: int) -> bool:
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


# -------------------------------------------------------------------
# History tier helpers
# -------------------------------------------------------------------

def get_history_retention_days(user_id: int) -> Optional[int]:
    return get_feature_limit("history_retention_days", user_id)


def get_history_max_entries(user_id: int) -> Optional[int]:
    return get_feature_limit("history_max_entries", user_id)


def can_search_history(user_id: int) -> bool:
    return has_feature("history_search", user_id)


def can_export_history(user_id: int) -> bool:
    return has_feature("history_export", user_id)


def can_see_trends(user_id: int) -> bool:
    return has_feature("history_trends", user_id)


def can_delete_history(user_id: int) -> bool:
    return has_feature("history_delete", user_id)