# services/notification_service.py
"""
Notification service.

Four channels exist for a notification:
    1. In-app         — always written to the notifications table (source of truth)
    2. Web Push       — best-effort, gated on user's master toggle
    3. Browser API    — live-quiz tab only (not handled here)
    4. Telegram       — not part of push v1

Push is only fanned out for a small allowlist of types — see
PUSH_ELIGIBLE_TYPES. Other types remain in-app only.

Everything in the push path is wrapped so it can never break the
existing notification flow. If push fails, the in-app row is still
written and the caller still returns True.
"""

import logging

from db import (
    create_notification,
    create_notification_for_all_users as db_create_all,
)
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Notification types worth pushing to the device.
#
# These match the notification_type strings that the app passes to
# send_notification() — the same strings that appear as suffix keys
# in user_settings.DEFAULT_SETTINGS (notifications.live_quiz_start,
# notifications.live_quiz_result, notifications.participant_joined).
# ---------------------------------------------------------------------
PUSH_ELIGIBLE_TYPES = frozenset({
    'live_quiz_start',
    'live_quiz_result',
    'participant_joined',
})


# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------
def _user_wants_push(user_id: int) -> bool:
    """True if the user's master push toggle is on."""
    try:
        from user_settings import get_user_setting
        return bool(get_user_setting(user_id, 'notifications.push_enabled', 0))
    except Exception:
        return False


def _fanout_push(
    user_id: int,
    notification_type: str,
    title: str,
    body: str,
    link: str,
    icon: str,
) -> None:
    """
    Deliver a push to every device the user has enabled.

    Best-effort: never raises, never blocks the caller. If push isn't
    configured, or the user hasn't opted in, this is a no-op.
    """
    if notification_type not in PUSH_ELIGIBLE_TYPES:
        return

    try:
        from services import push_service

        if not push_service.is_available():
            return

        if not _user_wants_push(user_id):
            return

        push_service.deliver(
            user_id,
            title=title,
            body=body,
            url=link or '/',
            icon=icon or None,
            tag=f'nuun-{notification_type}',
            data={'type': notification_type},
        )

    except Exception:
        logger.exception(
            "Push fan-out failed for user %s (type=%s)",
            user_id, notification_type,
        )


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------
def send_notification(
    user_id: int,
    notification_type: str,
    title: str,
    body: str,
    link: str = '',
    icon: str = '',
    force: bool = False,
) -> bool:
    """
    Send a notification to a single user.

    Respects the user's per-type preference unless `force=True`.
    Returns True if the in-app row was written.

    If the type is push-eligible, also fans out to Web Push for every
    device the user has enabled.
    """
    if not force:
        if not SettingsService.get_notification_preference(user_id, notification_type):
            logger.debug(
                "Notification '%s' disabled for user %s",
                notification_type, user_id,
            )
            return False

    ok = create_notification(user_id, notification_type, title, body, link, icon)

    if ok:
        _fanout_push(user_id, notification_type, title, body, link, icon)

    return ok


def send_notification_to_all(
    notification_type: str,
    title: str,
    body: str,
    link: str = '',
    icon: str = '',
    force: bool = False,
) -> int:
    """
    Send a notification to all users.

    Returns the number of in-app rows written.

    Push is NOT fanned out for broadcasts in v1 — a batching path with
    CPU budgeting is needed for large audiences. Broadcasts stay in-app.
    """
    if force:
        return db_create_all(notification_type, title, body, link, icon)

    from db import execute_with_retry
    cursor = execute_with_retry("SELECT id FROM students")
    users = cursor.fetchall()

    sent = 0
    for row in users:
        user_id = row['id']
        if SettingsService.get_notification_preference(user_id, notification_type):
            create_notification(user_id, notification_type, title, body, link, icon)
            sent += 1
    return sent