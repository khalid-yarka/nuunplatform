# services/tier_service.py
# ------------------------------------------------------------------
# Central tier logic for NuunPlatform.
#
# PHASE 2: This module is now a thin facade over
# services.entitlement_service for every policy read. Callers keep
# their existing function signatures — nothing else needs to change.
#
# - tier_config.py remains imported ONLY for:
#     * normalize_tier()  (single place legacy → canonical mapping lives)
#     * get_tier_level()  (the ordinal map: free=0, premium=1, pro=2)
#   Both will be removed in Phase 6.
#
# - Feature keys, policy types, limits and levels are the sole property
#   of the entitlement system. This file does not know what a tier
#   "can do" — it only asks.
#
# Vocabulary: free / premium / pro
# Legacy aliases (danbe / dhexe / hore) are auto-normalized on input.
# ------------------------------------------------------------------

import logging
from datetime import datetime
from typing import Optional, Dict

from flask import session

from db import execute_with_retry
from tier_config import normalize_tier, get_tier_level
from services import entitlement_service

logger = logging.getLogger(__name__)


# ============================================
# QUOTA KEY TRANSLATION
# ============================================
# Some callers still use legacy keys (e.g. "quiz_questions_limit") or
# legacy metric codes (e.g. "quiz_attempt"). The entitlement service
# uses canonical feature keys from entitlements_seed.json.
#
# THIS DICT IS THE ONLY PLACE WHERE THE OLD AND NEW NAMING CONVENTIONS
# MEET. Feature keys themselves are NOT hardcoded anywhere else.
_QUOTA_KEY_ALIASES = {
    # Legacy "…_limit" suffix → entitlement feature_key
    'quiz_questions_limit':     'quiz_questions',
    'quiz_attempt_limit':       'quiz_attempts',
    'resource_download_limit':  'resource_downloads',
    'saved_content_limit':      'saved_content',
    'history_retention_days':   'history_retention',
    'history_max_entries':      'history_entries',
    # Legacy metric codes → entitlement feature_key
    'quiz_attempt':             'quiz_attempts',
    'resource_download':        'resource_downloads',
}


def _resolve_feature_key(key: str) -> str:
    """Translate a legacy quota key to its canonical feature_key."""
    return _QUOTA_KEY_ALIASES.get(key, key)


# ============================================
# TIER RETRIEVAL
# ============================================

def get_user_tier(user_id: Optional[int] = None) -> str:
    """Effective tier for a user (expiry-aware). Delegates to entitlement."""
    return entitlement_service.get_user_tier(user_id)


def get_current_user_tier() -> str:
    """Effective tier of the currently logged-in user."""
    user_id = session.get('user_id')
    if not user_id:
        return 'free'
    return entitlement_service.get_user_tier(user_id)


def set_user_tier(user_id: int, new_tier: str,
                  admin_id: Optional[int] = None) -> bool:
    """
    Set a user's tier. Accepts canonical or legacy input; stores canonical.
    Also flags the user's session for refresh on their next request.
    """
    new_tier = normalize_tier(new_tier)
    if new_tier not in entitlement_service.VALID_TIERS:
        return False

    execute_with_retry(
        "UPDATE students SET tier = ?, tier_updated_at = ? WHERE id = ?",
        (new_tier, datetime.now().isoformat(), user_id),
        commit=True,
    )
    # Signal session refresh (app.py's before_request picks this up)
    entitlement_service.refresh_user(user_id)
    return True


# ============================================
# FEATURE CHECKS
# ============================================

def has_feature(feature_code: str, user_id: Optional[int] = None) -> bool:
    """Check if a user has a boolean (permission) feature."""
    if user_id is None:
        user_id = session.get('user_id')
    return entitlement_service.check(user_id, feature_code)


def get_feature_level(feature_code: str, user_id: Optional[int] = None) -> int:
    """Numeric level for a level feature (0 if unavailable)."""
    if user_id is None:
        user_id = session.get('user_id')
    return entitlement_service.get_level(user_id, feature_code)


def get_feature_limit(limit_code: str,
                      user_id: Optional[int] = None) -> Optional[int]:
    """
    Numeric limit for a quota feature (None = unlimited).
    Accepts both new-style keys (``quiz_questions``) and legacy keys
    (``quiz_questions_limit``).
    """
    if user_id is None:
        user_id = session.get('user_id')
    feature_key = _resolve_feature_key(limit_code)
    return entitlement_service.get_limit(user_id, feature_key)


# ============================================
# CONVENIENCE WRAPPERS
# ============================================

def can_create_live_quiz(user_id: Optional[int] = None) -> bool:
    return has_feature("create_live_quiz", user_id)


def can_schedule_live_quiz(user_id: Optional[int] = None) -> bool:
    return has_feature("scheduled_live_quiz", user_id)


def can_create_private_live_quiz(user_id: Optional[int] = None) -> bool:
    return has_feature("private_live_quiz", user_id)


def can_access_premium_resources(user_id: Optional[int] = None) -> bool:
    return has_feature("premium_resources", user_id)


def get_quiz_questions_limit(user_id: Optional[int] = None) -> int:
    limit = get_feature_limit("quiz_questions", user_id)
    return limit if limit is not None else 999


def get_quiz_attempt_limit(user_id: Optional[int] = None) -> Optional[int]:
    return get_feature_limit("quiz_attempts", user_id)


def get_resource_download_limit(user_id: Optional[int] = None) -> Optional[int]:
    return get_feature_limit("resource_downloads", user_id)


def get_saved_content_limit(user_id: Optional[int] = None) -> Optional[int]:
    return get_feature_limit("saved_content", user_id)


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


# ============================================
# QUOTA SYSTEM
# ============================================

def get_remaining_quota(user_id: int, metric_code: str) -> int:
    """
    Remaining quota for a metric.
    Returns 999 for unlimited features (backward-compat with callers
    that check ``if remaining > 0``).
    """
    feature_key = _resolve_feature_key(metric_code)
    remaining = entitlement_service.get_remaining(user_id, feature_key)
    if remaining is None:
        return 999
    return remaining


def check_and_consume_quota(user_id: int, metric_code: str) -> bool:
    """Atomically consume one unit of quota. False if already exhausted."""
    feature_key = _resolve_feature_key(metric_code)
    return entitlement_service.consume(user_id, feature_key)


# ---- Quota convenience wrappers ----

def get_resource_downloads_remaining(user_id: int) -> int:
    return get_remaining_quota(user_id, "resource_download")


def get_quiz_attempts_remaining(user_id: int) -> int:
    return get_remaining_quota(user_id, "quiz_attempt")


def consume_quiz_attempt(user_id: int) -> bool:
    return check_and_consume_quota(user_id, "quiz_attempt")


def consume_resource_download(user_id: int) -> bool:
    return check_and_consume_quota(user_id, "resource_download")


# ============================================
# SAVED CONTENT HELPERS
# ============================================

def get_saved_content_count(user_id: int) -> int:
    cursor = execute_with_retry(
        "SELECT COUNT(*) AS count FROM saved_content WHERE user_id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    return row['count'] if row else 0


def can_save_content(user_id: int) -> bool:
    limit = get_saved_content_limit(user_id)
    if limit is None:
        return True
    return get_saved_content_count(user_id) < limit


# ============================================
# TIER COMPARISON
# ============================================

def is_tier_at_least(tier: str, required_tier: str) -> bool:
    return get_tier_level(tier) >= get_tier_level(required_tier)


# ============================================
# QUESTION-COUNT TIER HELPERS
# ============================================

def get_allowed_question_counts(user_id: Optional[int] = None) -> Dict[str, bool]:
    """
    Derive the allowed question-count choices from the entitlement
    policy for the ``quiz_questions`` feature.
    """
    limit = get_feature_limit("quiz_questions", user_id)
    if limit is None:
        return {'10': True, '20': True, '30': True, 'custom': True}
    return {
        '10':     limit >= 10,
        '20':     limit >= 20,
        '30':     limit >= 30,
        'custom': False,   # reserved for unlimited tiers
    }


def is_custom_question_count_allowed(user_id: Optional[int] = None) -> bool:
    return get_feature_limit("quiz_questions", user_id) is None


def validate_question_count(user_id: int, count: int) -> bool:
    """
    Return True if ``count`` is within the user's policy for
    ``quiz_questions``. A limit of None means unlimited.
    """
    if count <= 0:
        return False
    limit = get_feature_limit("quiz_questions", user_id)
    if limit is None:
        return True
    return count <= limit


# ============================================
# HISTORY TIER HELPERS
# ============================================

def get_history_retention_days(user_id: int) -> Optional[int]:
    return get_feature_limit("history_retention", user_id)


def get_history_max_entries(user_id: int) -> Optional[int]:
    return get_feature_limit("history_entries", user_id)


def can_search_history(user_id: int) -> bool:
    return has_feature("history_search", user_id)


def can_export_history(user_id: int) -> bool:
    return has_feature("history_export", user_id)


def can_see_trends(user_id: int) -> bool:
    return has_feature("history_trends", user_id)


def can_delete_history(user_id: int) -> bool:
    return has_feature("history_delete", user_id)