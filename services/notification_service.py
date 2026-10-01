# services/notification_service.py
"""
Notification service.

Four channels exist for a notification:
    1. In-app         — always written to the notifications table (source of truth)
    2. Web Push       — best-effort, gated on user's master + per-type toggles
    3. Browser API    — live-quiz tab only (not handled here)
    4. Telegram       — see broadcast_new_quiz below

Everything in the push path is wrapped so it can never break the existing
notification flow.
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


PUSH_ELIGIBLE_TYPES = frozenset({
    'live_quiz_start',
    'live_quiz_result',
    'participant_joined',
    'quiz_complete',
    'admin_announcement',
    'new_live_quiz',
})

_DEFAULT_ON_PUSH_TYPES = frozenset({
    'new_live_quiz',
})

_PER_TYPE_KEY = {
    'live_quiz_start':    'notifications.push_live_quiz_start',
    'live_quiz_result':   'notifications.push_live_quiz_result',
    'participant_joined': 'notifications.push_participant_joined',
    'admin_announcement': 'notifications.push_admin_announcement',
    'quiz_complete':      'notifications.push_quiz_complete',
    'new_live_quiz':      'notifications.new_live_quiz',
}


def _user_wants_push(user_id: int) -> bool:
    try:
        from user_settings import get_user_setting
        return bool(get_user_setting(user_id, 'notifications.push_enabled', 1))
    except Exception:
        return True


def _user_wants_push_type(user_id: int, notification_type: str) -> bool:
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


def send_notification(
    user_id: int,
    notification_type: str,
    title: str,
    body: str,
    link: str = '',
    icon: str = '',
    force: bool = False,
) -> bool:
    if not force:
        if not SettingsService.get_notification_preference(user_id, notification_type):
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
# Broadcast — admin announcements (in-app + optional push)
# ---------------------------------------------------------------------

_MAX_BROADCAST_RECIPIENTS = 500
_BROADCAST_CHUNK_SIZE = 50
_BROADCAST_CHUNK_DELAY = 1.0


def _query_push_eligible_users(
    notification_type: str,
    max_recipients: int = _MAX_BROADCAST_RECIPIENTS,
) -> list:
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
        push_result['skipped'] = 'push_unavailable'
        return {'in_app': inapp_count, 'push': push_result}

    user_ids = _query_push_eligible_users('admin_announcement', max_recipients)
    if not user_ids:
        push_result['skipped'] = 'no_recipients'
        return {'in_app': inapp_count, 'push': push_result}

    if len(user_ids) >= max_recipients:
        push_result['capped'] = True

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
        push_result['skipped'] = 'delivery_error'

    return {'in_app': inapp_count, 'push': push_result}


# ---------------------------------------------------------------------
# Broadcast — new public quiz (Telegram ONLY)
# ---------------------------------------------------------------------
# This is the channel the platform uses to notify users about new
# competitions. It targets the bot_contacts table — every user who
# has ever fetched a PDF via the Telegram bot. No DB row is written
# to the notifications table. No push_service call is made.
#
# Runs in a background daemon thread so the create request returns
# immediately even when there are hundreds of recipients.
# ---------------------------------------------------------------------

_BROADCAST_SEND_DELAY = 0.05   # seconds between Telegram sends
_BROADCAST_LOG_EVERY  = 25     # log progress every N sends


def broadcast_new_quiz(quiz_title, creator_name, join_url, creator_id):
    """
    Broadcast a new public quiz via Telegram to every user who has
    fetched at least one PDF from the bot.

    Spawns a daemon thread. Returns immediately.
    """
    threading.Thread(
        target=_do_telegram_broadcast,
        args=(quiz_title, creator_name, join_url, creator_id),
        daemon=True,
        name='new-quiz-broadcast',
    ).start()
    return True


def _do_telegram_broadcast(quiz_title, creator_name, join_url, creator_id):
    """
    The actual Telegram fan-out. Runs in a daemon thread.
    Never raises — every failure is logged and swallowed.
    """
    try:
        from bot.db import (
            get_broadcast_recipients,
            mark_bot_contact_blocked,
        )
        from bot.utils import get_bot
        from telebot import types
    except Exception as e:
        logger.error(f"telegram broadcast import failed: {e}")
        return 0

    try:
        recipients = get_broadcast_recipients()
    except Exception as e:
        logger.warning(f"telegram broadcast: recipient query failed: {e}")
        return 0

    if not recipients:
        logger.info("telegram broadcast: no recipients — skipping")
        return 0

    # ── Build the message ──
    creator_name = (creator_name or '').strip() or 'NuunPlatform'
    creator_first = creator_name.split()[0] if creator_name else 'Nuun'

    title = f'🎯 Tartan cusub - {creator_first}'
    body = (
        f'"{quiz_title}" — {creator_name}\n'
        f'Taabo si aad ugu qayb gasho tartanka.'
    )
    full_text = f'<b>{title}</b>\n\n{body}'

    # Telegram requires the URL to be absolute
    try:
        from config import Config
        base = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')
    except Exception:
        base = ''
    join_link = join_url if join_url.startswith('http') else (base + join_url)

    # ── Build the inline button ──
    try:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "🚀 Ku biir Tartanka",
                url=join_link,
            )
        )
    except Exception as e:
        logger.warning(f"telegram broadcast: markup build failed: {e}")
        markup = None

    # ── Send loop ──
    bot = get_bot()
    sent = 0
    blocked = 0
    failed = 0

    for i, chat_id in enumerate(recipients):
        try:
            bot.send_message(
                chat_id,
                full_text,
                parse_mode='HTML',
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception as e:
            err = str(e).lower()
            if 'bot was blocked' in err or 'forbidden' in err or '403' in err:
                try:
                    mark_bot_contact_blocked(chat_id)
                except Exception:
                    pass
                blocked += 1
            else:
                failed += 1
                logger.debug(
                    f"telegram broadcast send failed for {chat_id}: {e}"
                )

        if (i + 1) % _BROADCAST_LOG_EVERY == 0:
            logger.info(
                f"telegram broadcast: {i+1}/{len(recipients)} "
                f"(sent={sent} blocked={blocked} failed={failed})"
            )

        time.sleep(_BROADCAST_SEND_DELAY)

    logger.info(
        f"telegram broadcast complete: sent={sent} blocked={blocked} "
        f"failed={failed} total={len(recipients)} for '{quiz_title}'"
    )
    return sent