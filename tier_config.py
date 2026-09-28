# tier_config.py
# ---------------------------------------------------------------
# DEPRECATED — kept during the two-tier migration so existing
# imports continue to work. Do NOT add new business logic here.
# All feature/policy logic belongs in services/entitlement_service.py.
#
# Vocabulary:
#   free / premium          (current — pro removed)
#   danbe / dhexe / hore    (legacy aliases, mapped below)
# ---------------------------------------------------------------

from enum import Enum
from typing import Dict, Any, Optional


class Tier(str, Enum):
    FREE = "free"
    PREMIUM = "premium"
    # Legacy aliases — mapped to canonical values below.
    DANBE = "free"
    DHEXE = "premium"
    HORE = "premium"   # legacy 'hore' now maps to premium (was 'pro')


# ---------------------------------------------------------------
# FEATURES
#   Permission : True / False
#   Level      : 0 = unavailable, 1 = basic, 2 = advanced
#   Content    : True / False
# ---------------------------------------------------------------
FEATURES: Dict[str, Dict[Any, Any]] = {
    # ----- Permission-based (boolean) -----
    "create_live_quiz":      {Tier.FREE: True,  Tier.PREMIUM: True},
    "private_live_quiz":     {Tier.FREE: True,  Tier.PREMIUM: True},
    "scheduled_live_quiz":   {Tier.FREE: True,  Tier.PREMIUM: True},
    "live_quiz_analytics":   {Tier.FREE: False, Tier.PREMIUM: True},
    "premium_resources":     {Tier.FREE: False, Tier.PREMIUM: True},
    "group_join":            {Tier.FREE: True,  Tier.PREMIUM: True},
    "pdf_direct_view":       {Tier.FREE: False, Tier.PREMIUM: True},
    "pdf_direct_download":   {Tier.FREE: False, Tier.PREMIUM: True},
    "pdf_telegram_preview":  {Tier.FREE: True,  Tier.PREMIUM: True},
    "quiz_auto_advance":     {Tier.FREE: True,  Tier.PREMIUM: True},
    "language_somali":       {Tier.FREE: True,  Tier.PREMIUM: True},
    "history_search":        {Tier.FREE: False, Tier.PREMIUM: True},
    "history_export":        {Tier.FREE: False, Tier.PREMIUM: True},
    "history_trends":        {Tier.FREE: False, Tier.PREMIUM: True},
    "history_delete":        {Tier.FREE: False, Tier.PREMIUM: True},

    # ----- Level-based (0–2) -----
    "achievement_history":          {Tier.FREE: 1, Tier.PREMIUM: 2},
    "achievements":                 {Tier.FREE: 1, Tier.PREMIUM: 2},
    "answer_review":                {Tier.FREE: 1, Tier.PREMIUM: 2},
    "badge_showcase":               {Tier.FREE: 0, Tier.PREMIUM: 2},
    "basic_statistics":             {Tier.FREE: 1, Tier.PREMIUM: 2},
    "correct_answer_explanations":  {Tier.FREE: 1, Tier.PREMIUM: 2},
    "detailed_ranking_stats":       {Tier.FREE: 0, Tier.PREMIUM: 2},
    "notification_settings":        {Tier.FREE: 1, Tier.PREMIUM: 2},
    "performance_charts":           {Tier.FREE: 0, Tier.PREMIUM: 2},
    "personal_learning_insights":   {Tier.FREE: 0, Tier.PREMIUM: 2},
    "profile_customization":        {Tier.FREE: 1, Tier.PREMIUM: 2},
    "progress_analytics":           {Tier.FREE: 0, Tier.PREMIUM: 2},
    "quiz_analytics":               {Tier.FREE: 0, Tier.PREMIUM: 2},
    "resource_search":              {Tier.FREE: 0, Tier.PREMIUM: 2},
    "subject_analytics":            {Tier.FREE: 0, Tier.PREMIUM: 2},
    "appearance_customization":     {Tier.FREE: 0, Tier.PREMIUM: 2},
    "public_id_management":         {Tier.FREE: 0, Tier.PREMIUM: 2},
}


# ---------------------------------------------------------------
# LIMITS
#   None = unlimited
# ---------------------------------------------------------------
LIMITS: Dict[str, Dict[Any, Optional[int]]] = {
    "quiz_questions_limit":     {Tier.FREE: None, Tier.PREMIUM: None},
    "quiz_attempt_limit":       {Tier.FREE: None, Tier.PREMIUM: None},
    "resource_download_limit":  {Tier.FREE: 3,    Tier.PREMIUM: 20},
    "saved_content_limit":      {Tier.FREE: 10,   Tier.PREMIUM: 200},
    "history_retention_days":   {Tier.FREE: 30,   Tier.PREMIUM: 180},
    "history_max_entries":      {Tier.FREE: 50,   Tier.PREMIUM: 500},
}


# ---------------------------------------------------------------
# Ordinal map — the only place tier names appear as strings.
# ---------------------------------------------------------------
_TIER_LEVEL: Dict[str, int] = {
    "free":    0,
    "premium": 1,
    # Legacy aliases
    "danbe":   0,
    "dhexe":   1,
    "hore":    1,
}


# ---------------------------------------------------------------
# Normalization — single entry point for tier string conversion
# ---------------------------------------------------------------
_LEGACY_MAP: Dict[str, str] = {
    "danbe": "free",
    "dhexe": "premium",
    "hore":  "premium",
}


def normalize_tier(raw) -> str:
    """
    Convert any tier value (canonical, legacy, or None) to canonical form.
    Accepted inputs: 'free'/'premium' (returned as-is),
                     'danbe'/'dhexe'/'hore' (mapped),
                     None or empty (defaults to 'free').
    Never raises.
    """
    if not raw:
        return "free"
    raw_lower = str(raw).lower()
    return _LEGACY_MAP.get(raw_lower, raw_lower)


def get_tier_level(tier: str) -> int:
    """Numeric level for comparison: free=0, premium=1."""
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