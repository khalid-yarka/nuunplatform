# ============================================================
# blueprints/live_quiz/chat.py
# ============================================================
# Chat rate limiting, send permission, ping cooldown and
# ping push delivery. All module-level state lives HERE, not
# in the route modules, so the limits are process-wide.

import time
import threading
import logging

from db import get_student_by_id, get_live_quiz_participants

from config import Config

logger = logging.getLogger(__name__)


# ============================================================
# CHAT RATE LIMITER
# ============================================================
_chat_rate_lock = threading.Lock()
_chat_rate = {}


def _chat_rate_ok(user_id, per_minute):
    """In-process sliding-window rate limiter, per user."""
    now_ts = time.time()
    cutoff = now_ts - 60.0
    with _chat_rate_lock:
        times = [t for t in _chat_rate.get(user_id, []) if t > cutoff]
        if len(times) >= per_minute:
            _chat_rate[user_id] = times
            return False
        times.append(now_ts)
        _chat_rate[user_id] = times
    return True


def _can_send_chat(user_id):
    """True when the user's tier allows SENDING. Reading is never gated."""
    try:
        from services import entitlement_service
        return entitlement_service.check(user_id, 'live_quiz_chat_send')
    except Exception:
        return False


# ============================================================
# PING COOLDOWN
# ============================================================
PING_COOLDOWN_SECONDS = 30

_ping_lock = threading.Lock()
_ping_last_sent = {}


def _ping_cooldown_remaining(quiz_id):
    with _ping_lock:
        last = _ping_last_sent.get(quiz_id, 0)
    remaining = int(PING_COOLDOWN_SECONDS - (time.time() - last))
    return max(0, remaining)


def _mark_ping_sent(quiz_id):
    with _ping_lock:
        _ping_last_sent[quiz_id] = time.time()


# ============================================================
# PING PUSH DISPATCH
# ============================================================
# Best-effort push delivery to active participants + creator.
# On hosts without VAPID configured, the push layer is a no-op;
# the in-page Notification API on the client delivers instead.
#
# Returns the number of active participants — the honest count of
# people who will see the ping, not a fake "sent" number.

def _dispatch_ping_push(quiz, creator_id, body):
    parts = get_live_quiz_participants(quiz['id']) or []
    active_ids = [p['student_id'] for p in parts if p.get('status') != 'left']
    if creator_id not in active_ids:
        active_ids.append(creator_id)

    total = len(active_ids)
    if total == 0:
        return 0

    try:
        from services import push_service
        if push_service.is_available() and active_ids:
            creator = get_student_by_id(creator_id) or {}
            cname = f"{creator.get('first_name','')} {creator.get('last_name','')}".strip() or 'Host'
            qtitle = quiz.get('title') or 'Live Quiz'
            push_service.deliver_batch(
                active_ids,
                title=f"📣 {cname} · {qtitle}",
                body=body,
                url=f"/live-quiz/waiting-room/{quiz['id']}",
                icon='/static/images/icon-192.png',
                tag=f'live-quiz-ping-{quiz["id"]}',
                data={
                    'type': 'live_quiz_ping',
                    'quiz_id': quiz['id'],
                },
            )
    except Exception as e:
        logger.debug(f"ping push skipped: {e}")

    return total