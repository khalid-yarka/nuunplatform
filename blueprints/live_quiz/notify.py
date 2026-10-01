# blueprints/live_quiz/notify.py
# ============================================================
# Single source of truth for the "quiz started" notification.
#
# Both manual-start (routes_play.start_quiz) and scheduled auto-start
# (finalize._maybe_auto_start_scheduled) call into this module so the
# recipient sees identical text regardless of how the quiz began.
#
# Delivery is two layers:
#   1. In-app row — created via notification_service.send_notification
#                   (manual start) or a single bulk INSERT (auto start).
#   2. Web Push   — push_service delivers; the service worker applies
#                   priority defaults from data.type === 'live_quiz_start'
#                   (requireInteraction, renotify, vibrate).
# ============================================================

import logging

logger = logging.getLogger(__name__)


NOTIFICATION_TITLE = '🚀 Tartanka Wuu bilabmay'
NOTIFICATION_ICON  = '🚀'
BODY_FRAME         = 'Wuu socda, Yan Laga Tagin.'
TITLE_LIMIT        = 40


def _shorten_title(text, limit=TITLE_LIMIT):
    """
    Compress a quiz title for the notification body.

    Cuts at the last word boundary within `limit - 1` characters when
    that boundary falls in the last 40% of the budget; otherwise
    hard-cuts. Always ends with a single '…' glyph.
    """
    if not text:
        return ''
    text = str(text).strip()
    if len(text) <= limit:
        return text

    budget = limit - 1
    cut = text[:budget]
    last_space = cut.rfind(' ')

    if last_space >= budget * 0.6:
        return cut[:last_space].rstrip() + '…'
    return cut.rstrip() + '…'


def build_message(quiz_title):
    """Return (title, body). Pure and unit-testable."""
    short = _shorten_title(quiz_title or 'Tartan')
    body = f'"{short}" {BODY_FRAME}'
    return NOTIFICATION_TITLE, body


def notify_quiz_started(user_id, quiz):
    """
    Send the notification to a single user (manual start path).

    Writes the in-app row via notification_service.send_notification,
    which also fans out Web Push (respecting the user's master and
    per-type push toggles). Never raises.
    """
    quiz_id = quiz.get('id')
    if not quiz_id:
        return False

    title, body = build_message(quiz.get('title'))
    link = f'/live-quiz/play/{quiz_id}'

    try:
        from services.notification_service import send_notification
        send_notification(
            user_id=user_id,
            notification_type='live_quiz_start',
            title=title,
            body=body,
            link=link,
            icon=NOTIFICATION_ICON,
        )
        return True
    except Exception as e:
        logger.warning(
            f'quiz-start notification failed for user {user_id}: {e}'
        )
        return False


def notify_quiz_started_batch(user_ids, quiz):
    """
    Batched Web Push for the auto-start path. Assumes the in-app rows
    were already written by the caller (single bulk INSERT — faster
    than N individual calls).

    Filters to users who have opted in (master toggle ON and per-type
    toggle ON) and pushes to all of them in one call. Returns the
    number of users reached.
    """
    if not user_ids:
        return 0

    title, body = build_message(quiz.get('title'))
    link = f'/live-quiz/play/{quiz["id"]}'

    pushed = 0
    try:
        from services import push_service
        if not push_service.is_available():
            return 0

        from services.notification_service import (
            _user_wants_push, _user_wants_push_type,
        )
        eligible = [
            uid for uid in user_ids
            if _user_wants_push(uid)
            and _user_wants_push_type(uid, 'live_quiz_start')
        ]
        if not eligible:
            return 0

        r = push_service.deliver_batch(
            eligible,
            title=title,
            body=body,
            url=link,
            icon=None,
            tag=f'live-quiz-start-{quiz["id"]}',
            data={'type': 'live_quiz_start', 'quiz_id': quiz['id']},
        )
        pushed = r.get('users_reached', 0)
    except Exception as e:
        logger.debug(f'quiz-start batch push skipped: {e}')

    return pushed