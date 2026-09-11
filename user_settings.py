# user_settings.py
# Default settings and persistence helpers.
#
# FIX: DEFAULT_SETTINGS now uses the same dot-notation keys that
# the settings UI reads (appearance.theme, notifications.quiz_complete, ...).
# Previously the defaults used the old flat keys, so brand-new users
# saw an empty settings page and could accidentally disable every
# notification by saving once.

import json
import logging
from typing import Any, Dict
from db import execute_with_retry

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    # ----- Appearance -----
    "appearance.theme": "system",
    "appearance.accent": "red",
    "appearance.font_size": "medium",
    "appearance.compact_mode": 0,
    "appearance.reduced_motion": 0,

    # ----- Quiz -----
    "quiz.default_question_count": 10,
    "quiz.default_difficulty": 1,
    "quiz.default_subject": "",
    "quiz.show_correct_immediately": 1,
    "quiz.auto_skip_enabled": 0,

    # ----- Notifications -----
    "notifications.quiz_complete": 1,
    "notifications.live_quiz_start": 1,
    "notifications.live_quiz_result": 1,
    "notifications.admin_announcement": 1,
    "notifications.participant_joined": 1,
    "notifications.new_pdf": 1,
    "notifications.daily_digest": 0,
    "notifications.achievement_unlock": 1,
    "notifications.live_quiz_reminder": 1,
    "notifications.weekly_summary": 0,

    # ----- Privacy -----
    "privacy.show_on_leaderboard": 1,
    "privacy.show_public_id": 1,

    # ----- Live Quiz Defaults -----
    "live_quiz.default_time_per_question": 30,
    "live_quiz.default_max_participants": 50,
    "live_quiz.default_privacy": 1,
}

MIGRATION_VERSION = 1


def get_raw_settings(user_id: int) -> Dict[str, Any]:
    """Return the stored settings dict as-is (no defaults merged)."""
    cursor = execute_with_retry(
        "SELECT settings FROM user_settings WHERE user_id = ?", (user_id,)
    )
    row = cursor.fetchone()
    if row and row['settings']:
        try:
            return json.loads(row['settings'])
        except json.JSONDecodeError:
            return {}
    return {}


def get_migration_version(user_id: int) -> int:
    raw = get_raw_settings(user_id)
    return raw.get('migration_version', 0)


def set_migration_version(user_id: int, version: int) -> None:
    raw = get_raw_settings(user_id)
    raw['migration_version'] = version
    update_user_settings(user_id, raw)


def get_user_settings(user_id: int) -> Dict[str, Any]:
    """
    Return effective settings: stored values override defaults.
    Uses dot-notation keys consistently with the settings UI.
    """
    stored = get_raw_settings(user_id)
    merged = DEFAULT_SETTINGS.copy()
    # Strip internal metadata before merging.
    stored_clean = {k: v for k, v in stored.items() if k != 'migration_version'}
    merged.update(stored_clean)
    return merged


def get_user_setting(user_id: int, key: str, default: Any = None) -> Any:
    settings = get_user_settings(user_id)
    return settings.get(key, default)


def update_user_settings(user_id: int, updates: Dict[str, Any]) -> bool:
    """
    Merge `updates` into the stored settings and persist.
    Internal keys like 'migration_version' are preserved.
    """
    try:
        raw = get_raw_settings(user_id)
        if 'migration_version' not in raw:
            raw['migration_version'] = 0
        for key, value in updates.items():
            if key == 'migration_version':
                continue
            raw[key] = value
        execute_with_retry(
            """
            INSERT INTO user_settings (user_id, settings, updated_at)
            VALUES (?, ?, datetime('now', 'localtime'))
            ON CONFLICT(user_id) DO UPDATE SET
                settings = excluded.settings,
                updated_at = excluded.updated_at
            """,
            (user_id, json.dumps(raw)),
            commit=True
        )
        return True
    except Exception as e:
        logger.error(f"Failed to update settings for user {user_id}: {e}", exc_info=True)
        return False


def apply_user_theme(user_id: int) -> str:
    return get_user_settings(user_id).get('appearance.theme', 'system')