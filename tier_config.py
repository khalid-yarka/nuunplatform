# tier_config.py
# ---------------------------------------------------------------
# DEPRECATED — will be deleted in Phase 6 (cleanup).
#
# Kept during the entitlement migration so that existing imports
# from services/tier_service.py continue to work. Do NOT add new
# business logic here. All new feature/policy logic belongs in
# services/entitlement_service.py (Phase 2+).
#
# Vocabulary:
#   free  / premium / pro     (current)
#   danbe / dhexe   / hore    (legacy aliases, removed in Phase 6)
# ---------------------------------------------------------------

from enum import Enum
from typing import Dict, Any, Optional


class Tier(str, Enum):
    FREE = "free"
    PREMIUM = "premium"
    PRO = "pro"
    # Legacy aliases — same underlying values, so they compare equal
    # and hash equal. Kept for one release only.
    DANBE = "free"
    DHEXE = "premium"
    HORE = "pro"


# ---------------------------------------------------------------
# FEATURES
#
#   Permission : True / False
#   Level      : 0 = unavailable, 1 = basic, 2 = advanced, 3 = full
#   Content    : True / False
# ---------------------------------------------------------------
FEATURES: Dict[str, Dict[Any, Any]] = {
    # ----- Permission-based (boolean) -----
    "create_live_quiz":      {Tier.FREE: False, Tier.PREMIUM: True,  Tier.PRO: True},
    "private_live_quiz":     {Tier.FREE: False, Tier.PREMIUM: True,  Tier.PRO: True},
    "scheduled_live_quiz":   {Tier.FREE: False, Tier.PREMIUM: True,  Tier.PRO: True},
    "live_quiz_analytics":   {Tier.FREE: False, Tier.PREMIUM: True,  Tier.PRO: True},
    "premium_resources":     {Tier.FREE: False, Tier.PREMIUM: True,  Tier.PRO: True},
    "group_join":            {Tier.FREE: True,  Tier.PREMIUM: True,  Tier.PRO: True},
    "curriculum_access":     {Tier.FREE: False, Tier.PREMIUM: False, Tier.PRO: True},
    "pdf_direct_view":       {Tier.FREE: False, Tier.PREMIUM: False, Tier.PRO: True},
    "pdf_direct_download":   {Tier.FREE: False, Tier.PREMIUM: False, Tier.PRO: True},
    "pdf_telegram_preview":  {Tier.FREE: False, Tier.PREMIUM: True,  Tier.PRO: True},
    "quiz_auto_advance":     {Tier.FREE: False, Tier.PREMIUM: True,  Tier.PRO: True},
    "language_somali":       {Tier.FREE: False, Tier.PREMIUM: False, Tier.PRO: True},
    "question_pdf_link":     {Tier.FREE: False, Tier.PREMIUM: False, Tier.PRO: False},
    "history_search":        {Tier.FREE: False, Tier.PREMIUM: False, Tier.PRO: True},
    "history_export":        {Tier.FREE: False, Tier.PREMIUM: True,  Tier.PRO: True},
    "history_trends":        {Tier.FREE: False, Tier.PREMIUM: False, Tier.PRO: True},
    "history_delete":        {Tier.FREE: False, Tier.PREMIUM: False, Tier.PRO: True},

    # ----- Level-based (0–3) -----
    "achievement_history":          {Tier.FREE: 1, Tier.PREMIUM: 2, Tier.PRO: 3},
    "achievements":                 {Tier.FREE: 1, Tier.PREMIUM: 2, Tier.PRO: 3},
    "answer_review":                {Tier.FREE: 0, Tier.PREMIUM: 1, Tier.PRO: 2},
    "badge_showcase":               {Tier.FREE: 1, Tier.PREMIUM: 2, Tier.PRO: 3},
    "basic_statistics":             {Tier.FREE: 1, Tier.PREMIUM: 2, Tier.PRO: 3},
    "correct_answer_explanations":  {Tier.FREE: 0, Tier.PREMIUM: 1, Tier.PRO: 2},
    "detailed_ranking_stats":       {Tier.FREE: 1, Tier.PREMIUM: 2, Tier.PRO: 3},
    "notification_settings":        {Tier.FREE: 1, Tier.PREMIUM: 2, Tier.PRO: 3},
    "performance_charts":           {Tier.FREE: 0, Tier.PREMIUM: 2, Tier.PRO: 3},
    "personal_learning_insights":   {Tier.FREE: 0, Tier.PREMIUM: 2, Tier.PRO: 3},
    "profile_customization":        {Tier.FREE: 1, Tier.PREMIUM: 2, Tier.PRO: 3},
    "progress_analytics":           {Tier.FREE: 1, Tier.PREMIUM: 2, Tier.PRO: 3},
    "quiz_analytics":               {Tier.FREE: 1, Tier.PREMIUM: 2, Tier.PRO: 3},
    "resource_search":              {Tier.FREE: 0, Tier.PREMIUM: 1, Tier.PRO: 2},
    "subject_analytics":            {Tier.FREE: 0, Tier.PREMIUM: 2, Tier.PRO: 3},
    "appearance_customization":     {Tier.FREE: 0, Tier.PREMIUM: 1, Tier.PRO: 2},
    "public_id_management":         {Tier.FREE: 0, Tier.PREMIUM: 1, Tier.PRO: 2},
}


# ---------------------------------------------------------------
# LIMITS
#   None = unlimited
# ---------------------------------------------------------------
LIMITS: Dict[str, Dict[Any, Optional[int]]] = {
    "quiz_questions_limit":     {Tier.FREE: 10,  Tier.PREMIUM: 20,  Tier.PRO: None},
    "quiz_attempt_limit":       {Tier.FREE: 10,  Tier.PREMIUM: 30,  Tier.PRO: None},
    "resource_download_limit":  {Tier.FREE: 3,   Tier.PREMIUM: 20,  Tier.PRO: None},
    "saved_content_limit":      {Tier.FREE: 0,   Tier.PREMIUM: 50,  Tier.PRO: None},
    "history_retention_days":   {Tier.FREE: 30,  Tier.PREMIUM: 180, Tier.PRO: None},
    "history_max_entries":      {Tier.FREE: 50,  Tier.PREMIUM: 500, Tier.PRO: None},
}


# ---------------------------------------------------------------
# Ordinal map — the ONLY place tier names appear as strings.
# ---------------------------------------------------------------
_TIER_LEVEL: Dict[str, int] = {
    "free":    0,
    "premium": 1,
    "pro":     2,
    # Legacy aliases (removed in Phase 6)
    "danbe":   0,
    "dhexe":   1,
    "hore":    2,
}


# ---------------------------------------------------------------
# Normalization — the single entry point for tier string conversion
# ---------------------------------------------------------------
_LEGACY_MAP: Dict[str, str] = {
    "danbe": "free",
    "dhexe": "premium",
    "hore":  "pro",
}


def normalize_tier(raw) -> str:
    """
    Convert any tier value (canonical, legacy, or None) to canonical form.
    Accepted inputs: 'free'/'premium'/'pro' (returned as-is),
                     'danbe'/'dhexe'/'hore' (mapped to new),
                     None or empty (defaults to 'free').
    Never raises.
    """
    if not raw:
        return "free"
    raw_lower = str(raw).lower()
    return _LEGACY_MAP.get(raw_lower, raw_lower)


def get_tier_level(tier: str) -> int:
    """Numeric level for comparison: free=0, premium=1, pro=2."""
    if tier is None:
        return 0
    return _TIER_LEVEL.get(str(tier).lower(), 0)


def get_feature(feature_code: str, tier: str) -> Any:
    """Get the value of a feature for a given tier."""
    if feature_code in FEATURES:
        return FEATURES[feature_code].get(tier)
    return None


def get_limit(limit_code: str, tier: str) -> Optional[int]:
    """Get the limit value for a given tier. Returns None for unlimited."""
    if limit_code in LIMITS:
        return LIMITS[limit_code].get(tier)
    return None