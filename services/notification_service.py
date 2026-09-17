# services/notification_service.py
"""
Notification service that respects user preferences.

Four channels exist for a notification:
    1. In-app         — always written to the notifications table
    2. Web Push       — best-effort, gated on user's master toggle
    3. Browser API    — live-quiz tab only (unchanged, not handled here)
    4. Telegram       — not part of push v1

Push is only fanned out for a small allowlist of types. See
PUSH_ELIGIBLE_TYPES below.
"""

import logging
from typing import Optional

from db import (
    create_notification,
    create_notification_for_all_users as db_create_all,
)
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


# Notification types worth interrupting the user for.
PUSH_ELIGIBLE_TYPES = frozenset({
    'live_quiz_started',
    'live_quiz_results',
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
    Best-effort: never raises, never blocks the caller.
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
    Send a notification to a single user if they have enabled it
    (or if forced). Returns True if the in-app row was written.
    """
    if not force:
        if not SettingsService.get_notification_preference(user_id, notification_type):
            logger.debug(
                "Notification '%s' disabled for user %s",
                notification_type, user_id,
            )
            return False

    ok = create_notification(user_id, notification_type, title, body, link, icon)

    # Fire-and-forget push fan-out. Runs whether or not the notification
    # was forced — forced just means "ignore the per-type preference",
    # the user still needs the master push toggle ON for us to deliver.
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
    Send a notification to all users. Returns the number of in-app rows
    written. Push is fanned out to users who have enabled it.
    """
    if force:
        count = db_create_all(notification_type, title, body, link, icon)
        # Broadcasts skip push for v1 — a separate batching path is
        # needed for large audiences on free-tier PythonAnywhere.
        return count

    from db import execute_with_retry
    cursor = execute_with_retry("SELECT id FROM students")
    users = cursor.fetchall()
    sent = 0
    for row in users:
        user_id = row['id']
        if SettingsService.get_notification_preference(user_id, notification_type):
            create_notification(user_id, notification_type, title, body, link, icon)
            _fanout_push(user_id, notification_type, title, body, link, icon)
            sent += 1
    return sent