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
import threading
import time

from db import (
    create_notification,
    create_notification_for_all_users as db_create_all,
    execute_with_retry,
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
    'new_live_quiz',
})


# ---------------------------------------------------------------------
# Types whose per-type toggle defaults to ON.
# Everything else defaults to OFF and must be enabled by the user.
# ---------------------------------------------------------------------
_DEFAULT_ON_PUSH_TYPES = frozenset({
    'new_live_quiz',
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
    'new_live_quiz':      'notifications.new_live_quiz',
}


# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------
def _user_wants_push(user_id: int) -> bool:
    """True if the user's master push toggle is on."""
    try:
        from user_settings import get_user_setting
        return bool(get_user_setting(user_id, 'notifications.push_enabled', 1))
    except Exception:
        return True


def _user_wants_push_type(user_id: int, notification_type: str) -> bool:
    """
    True if the user's per-type push preference allows this type.

    The default when no preference has ever been stored depends on
    the type:
        · Types in _DEFAULT_ON_PUSH_TYPES → default True
        · Everything else                  → default False
    """
    key = _PER_TYPE_KEY.get(notification_type)
    if not key:
        return False
    default = 1 if notification_type in _DEFAULT_ON_PUSH_TYPES else 0
    try:
        from user_settings import get_user_setting
        return bool(get_user_setting(user_id, key, default))
    except Exception:
        return notification_type in _DEFAULT_ON_PUSH_TYPES


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
    """
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

    try:
        from services import push_service
        if not push_service.is_available():
            push_result['skipped'] = 'push_disabled'
            return {'in_app': inapp_count, 'push': push_result}
    except Exception:
        logger.exception("broadcast_announcement: push_service unavailable")
        push_result['skipped'] = 'push_unavailable'
        return {'in_app': inapp_count, 'push': push_result}

    user_ids = _query_push_eligible_users('admin_announcement', max_recipients)
    if not user_ids:
        push_result['skipped'] = 'no_recipients'
        return {'in_app': inapp_count, 'push': push_result}

    if len(user_ids) >= max_recipients:
        push_result['capped'] = True
        logger.warning(
            "broadcast_announcement: capped at %d recipients", max_recipients,
        )

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

            if i + _BROADCAST_CHUNK_SIZE < len(user_ids):
                time.sleep(_BROADCAST_CHUNK_DELAY)
    except Exception:
        logger.exception("broadcast_announcement: push delivery crashed")
        push_result['skipped'] = 'delivery_error'

    return {'in_app': inapp_count, 'push': push_result}


# ---------------------------------------------------------------------
# Broadcast — new public quiz created (PUSH-ONLY)
# ---------------------------------------------------------------------
# This is the only notification path that does not touch the
# notifications table. The recipient sees a fleeting OS notification
# and nothing else — no bell row, no unread badge bump. If they swipe
# it away, it is gone for good.
#
# Runs in a background daemon thread so the create-quiz request returns
# immediately even when there are hundreds of subscribers to reach.
# ---------------------------------------------------------------------

def broadcast_new_quiz(quiz_title, creator_name, join_url, creator_id):
    """
    Push-only announcement that a new public quiz exists.

    Every subscribed user who has not opted out receives a Somali
    OS notification. No DB rows are written.

    Returns immediately — the actual fan-out happens on a daemon thread.
    """
    threading.Thread(
        target=_do_broadcast_new_quiz,
        args=(quiz_title, creator_name, join_url, creator_id),
        daemon=True,
        name='new-quiz-push',
    ).start()
    return True


def _do_broadcast_new_quiz(quiz_title, creator_name, join_url, creator_id):
    """
    The actual push fan-out. Runs in a daemon thread.
    Never raises — every failure is logged and swallowed.
    """
    try:
        from services import push_service
        if not push_service.is_available():
            logger.debug("broadcast_new_quiz: push not configured; nothing sent")
            return 0

        # ── Find users with subscriptions, excluding the creator ──
        try:
            cursor = execute_with_retry(
                "SELECT DISTINCT user_id FROM push_subscriptions WHERE user_id != ?",
                (creator_id,),
            )
            candidate_ids = [row['user_id'] for row in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"broadcast_new_quiz: subscriber query failed: {e}")
            return 0

        if not candidate_ids:
            logger.info("broadcast_new_quiz: no subscribers to notify")
            return 0

        # ── Filter by opt-in ──
        eligible = [
            uid for uid in candidate_ids
            if _user_wants_push(uid)
            and _user_wants_push_type(uid, 'new_live_quiz')
        ]
        if not eligible:
            logger.info("broadcast_new_quiz: all subscribers opted out")
            return 0

        # ── Build Somali text ──
        creator_name = (creator_name or '').strip() or 'NuunPlatform'
        first_name = creator_name.split()[0] if creator_name else 'Nuun'

        title = f'🎯 Tartan cusub - {first_name}'
        body = (
            f'"{quiz_title}" — {creator_name}\n'
            f'Taabo si aad ugu qayb gasho tartanka.'
        )

        # ── Per-quiz tag so two announcements stack ──
        code = (join_url or '').rstrip('/').rsplit('/', 1)[-1] or 'quiz'
        tag = f'new-quiz-{code}'

        # ── Push in chunks ──
        total_reached = 0
        chunk_size = 50
        for i in range(0, len(eligible), chunk_size):
            chunk = eligible[i:i + chunk_size]
            try:
                r = push_service.deliver_batch(
                    chunk,
                    title=title,
                    body=body,
                    url=join_url,
                    icon=None,
                    tag=tag,
                    data={
                        'type': 'new_live_quiz',
                        'url': join_url,
                        'quiz_code': code,
                    },
                )
                total_reached += r.get('users_reached', 0)
            except Exception as e:
                logger.warning(f"broadcast_new_quiz: chunk failed: {e}")

            if i + chunk_size < len(eligible):
                time.sleep(0.5)

        logger.info(
            f"broadcast_new_quiz: pushed to {total_reached}/{len(eligible)} "
            f"subscribers for quiz '{quiz_title}'"
        )
        return total_reached

    except Exception as e:
        logger.error(
            f"broadcast_new_quiz worker crashed: {e}", exc_info=True,
        )
        return 0