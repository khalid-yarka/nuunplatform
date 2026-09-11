# tier_config.py
# Single source of truth for Nuunqalin 3‑tier system.

from enum import Enum
from typing import Dict, Any, Optional

class Tier(str, Enum):
    DANBE = "danbe"
    DHEXE = "dhexe"
    HORE = "hore"

# -------------------------------------------------------------------
# FEATURE DEFINITIONS
# For permission features: True/False
# For level features: 0=unavailable, 1=basic, 2=advanced, 3=complete
# For content access: True/False (e.g., premium resources)
# -------------------------------------------------------------------

FEATURES: Dict[str, Dict[str, Any]] = {
    # ----- Permission-based (boolean) -----
    "create_live_quiz": {
        Tier.DANBE: False,
        Tier.DHEXE: True,
        Tier.HORE: True,
    },
    "private_live_quiz": {
        Tier.DANBE: False,
        Tier.DHEXE: True,
        Tier.HORE: True,
    },
    "scheduled_live_quiz": {
        Tier.DANBE: False,
        Tier.DHEXE: True,
        Tier.HORE: True,
    },
    "live_quiz_analytics": {
        Tier.DANBE: False,
        Tier.DHEXE: True,
        Tier.HORE: True,
    },
    "premium_resources": {
        Tier.DANBE: False,
        Tier.DHEXE: True,
        Tier.HORE: True,
    },
    # ----- Level-based (0-3) -----
    "achievement_history": {
        Tier.DANBE: 1,   # limited/recent
        Tier.DHEXE: 2,   # expanded
        Tier.HORE: 3,    # full
    },
    "achievements": {
        Tier.DANBE: 1,   # limited
        Tier.DHEXE: 2,   # expanded
        Tier.HORE: 3,    # complete
    },
    "answer_review": {
        Tier.DANBE: 0,   # disabled
        Tier.DHEXE: 1,   # limited (correct/incorrect only)
        Tier.HORE: 2,    # full (includes correct answer, explanation)
    },
    "badge_showcase": {
        Tier.DANBE: 1,
        Tier.DHEXE: 2,
        Tier.HORE: 3,
    },
    "basic_statistics": {
        Tier.DANBE: 1,   # basic
        Tier.DHEXE: 2,   # advanced
        Tier.HORE: 3,    # complete
    },
    "correct_answer_explanations": {
        Tier.DANBE: 0,   # disabled
        Tier.DHEXE: 1,   # detailed
        Tier.HORE: 2,    # detailed + extra insight
    },
    "detailed_ranking_stats": {
        Tier.DANBE: 1,   # basic
        Tier.DHEXE: 2,   # percentile/details
        Tier.HORE: 3,    # full
    },
    "notification_settings": {
        Tier.DANBE: 1,   # basic
        Tier.DHEXE: 2,   # expanded
        Tier.HORE: 3,    # advanced
    },
    "performance_charts": {
        Tier.DANBE: 0,   # disabled
        Tier.DHEXE: 2,   # advanced
        Tier.HORE: 3,    # full + comparison
    },
    "personal_learning_insights": {
        Tier.DANBE: 0,   # disabled
        Tier.DHEXE: 2,   # subject/progress
        Tier.HORE: 3,    # personalized/full
    },
    "profile_customization": {
        Tier.DANBE: 1,   # basic
        Tier.DHEXE: 2,   # expanded
        Tier.HORE: 3,    # full
    },
    "progress_analytics": {
        Tier.DANBE: 1,
        Tier.DHEXE: 2,
        Tier.HORE: 3,
    },
    "quiz_analytics": {
        Tier.DANBE: 1,
        Tier.DHEXE: 2,
        Tier.HORE: 3,
    },
    "resource_search": {
        Tier.DANBE: 0,   # locked
        Tier.DHEXE: 1,   # subject filters
        Tier.HORE: 2,    # advanced filters
    },
    "subject_analytics": {
        Tier.DANBE: 0,   # disabled
        Tier.DHEXE: 2,   # detailed
        Tier.HORE: 3,    # full trends
    },
    # ----- HISTORY FEATURES (NEW) -----
    "history_search": {
        Tier.DANBE: False,
        Tier.DHEXE: False,   # locked
        Tier.HORE: True,
    },
    "history_export": {
        Tier.DANBE: False,
        Tier.DHEXE: True,    # limited (100 rows)
        Tier.HORE: True,
    },
    "history_trends": {
        Tier.DANBE: False,
        Tier.DHEXE: False,
        Tier.HORE: True,
    },
    "history_delete": {
        Tier.DANBE: False,
        Tier.DHEXE: False,
        Tier.HORE: True,     # future
    },
}

# -------------------------------------------------------------------
# LIMIT DEFINITIONS
# None = unlimited
# -------------------------------------------------------------------

LIMITS: Dict[str, Dict[str, Optional[int]]] = {
    "quiz_questions_limit": {
        Tier.DANBE: 10,
        Tier.DHEXE: 20,
        Tier.HORE: None,   # 30+ / custom
    },
    "quiz_attempt_limit": {
        Tier.DANBE: 10,
        Tier.DHEXE: 30,
        Tier.HORE: None,
    },
    "resource_download_limit": {
        Tier.DANBE: 3,
        Tier.DHEXE: 20,
        Tier.HORE: None,
    },
    "saved_content_limit": {
        Tier.DANBE: 0,     # no access
        Tier.DHEXE: 50,
        Tier.HORE: None,
    },
    # ----- HISTORY LIMITS (NEW) -----
    "history_retention_days": {
        Tier.DANBE: 30,
        Tier.DHEXE: 180,
        Tier.HORE: None,   # unlimited
    },
    "history_max_entries": {
        Tier.DANBE: 50,
        Tier.DHEXE: 500,
        Tier.HORE: None,   # unlimited
    },
}

# -------------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------------

def get_feature(feature_code: str, tier: str) -> Any:
    """Get the value for a feature for a given tier."""
    if feature_code in FEATURES:
        return FEATURES[feature_code].get(tier)
    return None

def get_limit(limit_code: str, tier: str) -> Optional[int]:
    """Get the limit value for a given tier. Returns None for unlimited."""
    if limit_code in LIMITS:
        return LIMITS[limit_code].get(tier)
    return None

def get_tier_level(tier: str) -> int:
    """Return numeric level for comparison: danbe=0, dhexe=1, hore=2."""
    if tier == Tier.DANBE:
        return 0
    elif tier == Tier.DHEXE:
        return 1
    elif tier == Tier.HORE:
        return 2
    return 0