# services/notification_service.py
"""
Notification service.

Four channels exist for a notification:
    1. In-app         — always written to the notifications table (source of truth)
    2. Web Push       — best-effort, gated on user's master + per-type toggles
    3. Browser API    — live-quiz tab only (not handled here)
    4. Telegram       — not part of push v1

Push is only fanned out for types listed in PUSH_ELIGIBLE_TYPES, AND only
when the user's master push toggle is on, AND only when the user's per-type
preference for that specific type is on.

Everything in the push path is wrapped so it can never break the existing
notification flow. If push fails, the in-app row is still written and the
caller still returns True.
"""

import logging
import time

from db import (
    create_notification,
    create_notification_for_all_users as db_create_all,
)
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Notification types worth pushing to the device.
# ---------------------------------------------------------------------
PUSH_ELIGIBLE_TYPES = frozenset({
    'live_quiz_start',
    'live_quiz_result',
    'participant_joined',
    'quiz_complete',
    'admin_announcement',
})


# ---------------------------------------------------------------------
# Per-type preference key map
# ---------------------------------------------------------------------
_PER_TYPE_KEY = {
    'live_quiz_start':    'notifications.push_live_quiz_start',
    'live_quiz_result':   'notifications.push_live_quiz_result',
    'participant_joined': 'notifications.push_participant_joined',
    'admin_announcement': 'notifications.push_admin_announcement',
    'quiz_complete':      'notifications.push_quiz_complete',
}


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


def _user_wants_push_type(user_id: int, notification_type: str) -> bool:
    """True if the user's per-type push preference allows this type."""
    key = _PER_TYPE_KEY.get(notification_type)
    if not key:
        return False
    try:
        from user_settings import get_user_setting
        return bool(get_user_setting(user_id, key, 0))
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
    Best-effort. Never raises.
    """
    if notification_type not in PUSH_ELIGIBLE_TYPES:
        return
    try:
        from services import push_service
        if not push_service.is_available():
            return
        if not _user_wants_push(user_id):
            return
        if not _user_wants_push_type(user_id, notification_type):
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
    Respects per-type preference unless force=True.
    Fans out to push if the type is eligible and all gates pass.
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
    Send a notification to all users. Returns count of in-app rows written.
    Does NOT fan out push — see broadcast_announcement() for that.
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


# ---------------------------------------------------------------------
# Broadcast — in-app to everyone, push to opted-in users
# ---------------------------------------------------------------------
_MAX_BROADCAST_RECIPIENTS = 500
_BROADCAST_CHUNK_SIZE = 50
_BROADCAST_CHUNK_DELAY = 1.0  # seconds


def _query_push_eligible_users(
    notification_type: str,
    max_recipients: int = _MAX_BROADCAST_RECIPIENTS,
) -> list:
    """
    Return user_ids who:
      * have at least one push_subscriptions row
      * have notifications.push_enabled = 1
      * have the per-type toggle on for notification_type

    Capped at max_recipients.
    """
    from db import execute_with_retry

    # Over-fetch: some candidates will fail the settings check below.
    cursor = execute_with_retry(
        "SELECT DISTINCT user_id FROM push_subscriptions LIMIT ?",
        (max_recipients * 3,),
    )
    candidates = [row['user_id'] for row in cursor.fetchall()]

    eligible = []
    for uid in candidates:
        if not _user_wants_push(uid):
            continue
        if not _user_wants_push_type(uid, notification_type):
            continue
        eligible.append(uid)
        if len(eligible) >= max_recipients:
            break
    return eligible


def broadcast_announcement(
    title: str,
    body: str,
    link: str = '',
    icon: str = '',
    also_push: bool = True,
    max_recipients: int = _MAX_BROADCAST_RECIPIENTS,
) -> dict:
    """
    Send an admin announcement.

    * Always writes the in-app notification row for every user.
    * If also_push is True, delivers Web Push to users who have opted in.

    Returns:
        {
          'in_app': int,              # rows written to notifications
          'push': {
            'sent': int,
            'failed': int,
            'pruned': int,
            'recipients': int,
            'capped': bool,           # True if max_recipients was hit
            'skipped': str | None,    # reason push was skipped
          }
        }
    """
    # 1. In-app — synchronous, fast, always happens
    inapp_count = send_notification_to_all(
        'admin_announcement', title, body, link, icon, force=True,
    )

    push_result = {
        'sent': 0, 'failed': 0, 'pruned': 0,
        'recipients': 0, 'capped': False, 'skipped': None,
    }

    if not also_push:
        push_result['skipped'] = 'not_requested'
        return {'in_app': inapp_count, 'push': push_result}

    # 2. Push — check service availability first
    try:
        from services import push_service
        if not push_service.is_available():
            push_result['skipped'] = 'push_disabled'
            return {'in_app': inapp_count, 'push': push_result}
    except Exception:
        logger.exception("broadcast_announcement: push_service unavailable")
        push_result['skipped'] = 'push_unavailable'
        return {'in_app': inapp_count, 'push': push_result}

    # 3. Find who should receive push
    user_ids = _query_push_eligible_users('admin_announcement', max_recipients)
    if not user_ids:
        push_result['skipped'] = 'no_recipients'
        return {'in_app': inapp_count, 'push': push_result}

    if len(user_ids) >= max_recipients:
        push_result['capped'] = True
        logger.warning(
            "broadcast_announcement: capped at %d recipients", max_recipients,
        )

    # 4. Deliver in chunks
    try:
        for i in range(0, len(user_ids), _BROADCAST_CHUNK_SIZE):
            chunk = user_ids[i:i + _BROADCAST_CHUNK_SIZE]
            r = push_service.deliver_batch(
                chunk,
                title=title,
                body=body,
                url=link or '/',
                icon=icon or None,
                tag='nuun-admin-announcement',
                data={'type': 'admin_announcement'},
            )
            push_result['sent']       += r.get('sent', 0)
            push_result['failed']     += r.get('failed', 0)
            push_result['pruned']     += r.get('pruned', 0)
            push_result['recipients'] += r.get('users_reached', 0)

            # Pause between chunks (rate-limit friendly), but not after last.
            if i + _BROADCAST_CHUNK_SIZE < len(user_ids):
                time.sleep(_BROADCAST_CHUNK_DELAY)
    except Exception:
        logger.exception("broadcast_announcement: push delivery crashed")
        push_result['skipped'] = 'delivery_error'

    return {'in_app': inapp_count, 'push': push_result}