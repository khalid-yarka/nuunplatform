"""
Central registry for all user-configurable settings.

Each entry describes:
    type          — 'enum' | 'integer' | 'boolean' | 'string'
    default       — fallback value
    allowed_values — for enum / integer / string
    category      — grouping (for UI)
    label         — human label
    description   — short help text
    tier_required — legacy gate: 'premium' | None
    feature_key   — entitlement gate (preferred). When set, the
                    setting is controlled by the entitlement system
                    and can be re-configured by admins at any time.
    live          — whether applying takes effect without a full reload
    sensitive     — whether the setting is privacy-sensitive
"""

from typing import Dict, Any, Optional, List


SETTINGS_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ----- Appearance -----
    "appearance.theme": {
        "type": "enum",
        "default": "system",
        "allowed_values": ["light", "dark", "system"],
        "category": "appearance",
        "label": "Theme",
        "description": "Choose your preferred theme.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "appearance.accent": {
        "type": "enum",
        "default": "red",
        "allowed_values": ["red", "blue", "green", "purple", "orange"],
        "category": "appearance",
        "label": "Accent Colour",
        "description": "Choose a primary colour for the interface.",
        "tier_required": "premium",
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "appearance.font_size": {
        "type": "enum",
        "default": "medium",
        "allowed_values": ["small", "medium", "large"],
        "category": "appearance",
        "label": "Font Size",
        "description": "Adjust the text size across the platform.",
        "tier_required": "premium",
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "appearance.font_family": {
        "type": "enum",
        "default": "default",
        "allowed_values": ["default", "serif", "sans", "mono", "dyslexic"],
        "category": "appearance",
        "label": "Font Family",
        "description": "Choose the typeface for the interface.",
        "tier_required": "premium",
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "appearance.compact_mode": {
        "type": "boolean",
        "default": False,
        "category": "appearance",
        "label": "Compact Mode",
        "description": "Reduce padding and margins for a denser layout.",
        "tier_required": "premium",
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "appearance.reduced_motion": {
        "type": "boolean",
        "default": False,
        "category": "accessibility",
        "label": "Reduced Motion",
        "description": "Minimise animations and transitions.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    # Language: gated by the entitlement feature `language_somali`.
    # The seed enables this feature for both free and premium, so every
    # user can switch freely. Admins can still restrict it per tier by
    # editing the feature policy — no code change required.
    "appearance.language": {
        "type": "enum",
        "default": "en",
        "allowed_values": ["en", "so"],
        "category": "appearance",
        "label": "Language",
        "description": "Choose the display language.",
        "feature_key": "language_somali",
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },

    # ----- Practice -----
    "quiz.default_question_count": {
        "type": "integer",
        "default": 10,
        "allowed_values": [5, 10, 15, 20, 25, 30],
        "category": "quiz",
        "label": "Default Question Count",
        "description": "Default number of questions per practice.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "quiz.default_difficulty": {
        "type": "integer",
        "default": 1,
        "allowed_values": [1, 2, 3, 4, 5],
        "category": "quiz",
        "label": "Default Difficulty",
        "description": "Default difficulty level for new practices.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "quiz.default_subject": {
        "type": "string",
        "default": "",
        "category": "quiz",
        "label": "Default Subject",
        "description": "Pre-select a subject when starting a new practice.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "quiz.show_correct_immediately": {
        "type": "boolean",
        "default": True,
        "category": "quiz",
        "label": "Show Correct Answer Immediately",
        "description": "If off, you'll see the correct answer only at the end.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "quiz.auto_skip_enabled": {
        "type": "boolean",
        "default": False,
        "category": "quiz",
        "label": "Auto-advance After Answer",
        "description": "Smoothly move to the next question after answering.",
        "tier_required": "premium",
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },

    # ----- Notifications -----
    "notifications.quiz_complete": {
        "type": "boolean",
        "default": True,
        "category": "notifications",
        "label": "Practice Complete",
        "description": "When you finish a practice.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "notifications.live_quiz_start": {
        "type": "boolean",
        "default": True,
        "category": "notifications",
        "label": "Competition Starts",
        "description": "When a competition you're in begins.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "notifications.live_quiz_result": {
        "type": "boolean",
        "default": True,
        "category": "notifications",
        "label": "Competition Results",
        "description": "When a competition ends.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "notifications.admin_announcement": {
        "type": "boolean",
        "default": True,
        "category": "notifications",
        "label": "Admin Announcements",
        "description": "Important platform announcements.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "notifications.participant_joined": {
        "type": "boolean",
        "default": True,
        "category": "notifications",
        "label": "Participant Joined Your Competition",
        "description": "When someone joins a competition you host.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "notifications.new_pdf": {
        "type": "boolean",
        "default": True,
        "category": "notifications",
        "label": "New PDF Uploaded",
        "description": "When a new PDF is added.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },

    "notifications.new_live_quiz": {
        "type": "boolean",
        "default": True,
        "category": "notifications",
        "label": "New Competition Announcements",
        "description": "Get a push notification when someone creates a new public competition.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },

    # Telegram broadcast subscription. Distinct from the in-app
    # new_live_quiz preference — this one controls whether the
    # Telegram bot sends new-quiz messages to the linked chat.
    # Toggling it here updates bot_data.db via the settings save
    # hook (see services/settings_service.py).
    "notifications.telegram_broadcast": {
        "type": "boolean",
        "default": True,
        "category": "notifications",
        "label": "Telegram Quiz Announcements",
        "description": "Get a message on Telegram when a new public competition is created.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },

    "notifications.daily_digest": {
        "type": "boolean",
        "default": False,
        "category": "notifications",
        "label": "Daily Digest",
        "description": "Receive a daily summary of platform activity.",
        "tier_required": "premium",
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "notifications.achievement_unlock": {
        "type": "boolean",
        "default": True,
        "category": "notifications",
        "label": "Achievement Unlock",
        "description": "Get notified when you earn a new badge.",
        "tier_required": "premium",
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "notifications.live_quiz_reminder": {
        "type": "boolean",
        "default": True,
        "category": "notifications",
        "label": "Competition Reminder",
        "description": "5-minute reminder before a scheduled competition.",
        "tier_required": "premium",
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    # Feature not implemented anywhere yet — preference-only storage.
    # Kept ungated so users can toggle it without hitting a dead tier gate.
    "notifications.weekly_summary": {
        "type": "boolean",
        "default": False,
        "category": "notifications",
        "label": "Weekly Summary",
        "description": "Get a weekly wrap-up of your progress.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },

    # ----- Privacy -----
    "privacy.show_on_leaderboard": {
        "type": "boolean",
        "default": True,
        "category": "privacy",
        "label": "Show on Leaderboard",
        "description": "If off, your name appears as 'Anonymous'.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "privacy.show_public_id": {
        "type": "boolean",
        "default": True,
        "category": "privacy",
        "label": "Show Public ID",
        "description": "If off, your public ID is hidden on your profile.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },

    # ----- Competition (defaults for the create form) -----
    "live_quiz.default_time_per_question": {
        "type": "integer",
        "default": 30,
        "allowed_values": [30, 45, 60],
        "category": "live_quiz",
        "label": "Default Time per Question (seconds)",
        "description": "Default time allowed per question when creating a competition.",
        "tier_required": "premium",
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "live_quiz.default_max_participants": {
        "type": "integer",
        "default": 50,
        "allowed_values": [20, 50, 100],
        "category": "live_quiz",
        "label": "Default Max Participants",
        "description": "Default maximum number of participants.",
        "tier_required": "premium",
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "live_quiz.default_privacy": {
        "type": "integer",
        "default": 1,
        "allowed_values": [0, 1],
        "category": "live_quiz",
        "label": "Default Privacy",
        "description": "0 = private, 1 = public.",
        "tier_required": "premium",
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
    "onboarding.telegram_prompt_dismissed": {
        "type": "boolean",
        "default": False,
        "category": "notifications",
        "label": "Telegram Prompt Dismissed",
        "description": "Internal flag. Set to true when the user dismisses the Telegram join prompt on the lobby.",
        "tier_required": None,
        "live": True,
        "requires_confirmation": False,
        "sensitive": False,
    },
}


def get_setting(key: str) -> Optional[Dict]:
    return SETTINGS_REGISTRY.get(key)


def get_settings_by_category(category: str) -> Dict[str, Dict]:
    return {k: v for k, v in SETTINGS_REGISTRY.items()
            if v.get("category") == category}


def get_all_categories() -> List[str]:
    return sorted({v["category"] for v in SETTINGS_REGISTRY.values()})


def get_default(key: str):
    return SETTINGS_REGISTRY.get(key, {}).get("default")