# services/push_service.py
"""
Web Push delivery service.

Single point of contact between the app and the browser push services
(FCM, Mozilla autopush, APNs). Wraps pywebpush for VAPID signing and
delivery, and owns all reads/writes for the push_subscriptions table.

Contract:
  * If VAPID keys are missing, or pywebpush isn't installed, every
    public function is a safe no-op. The in-app notification center
    is unaffected.
  * Every public function is best-effort: failures are logged, never
    raised. Callers can wrap in try/except but shouldn't have to.
  * Dead subscriptions (HTTP 404 / 410) are pruned automatically.
  * Payload size is capped before encryption; over-long bodies are
    truncated silently.
"""

import json
import logging
from typing import Dict, List, Optional

from config import Config
from db import execute_with_retry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Optional dependency — pywebpush may not be installed
# ---------------------------------------------------------------------
try:
    from pywebpush import webpush, WebPushException
    _PYWEBPUSH_AVAILABLE = True
except ImportError:
    webpush = None

    class WebPushException(Exception):
        response = None

    _PYWEBPUSH_AVAILABLE = False


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------
# The encrypted payload must stay under 4 KB. We cap the plaintext at
# 3500 bytes to leave headroom for the AES-GCM envelope + JWT header.
_MAX_PAYLOAD_BYTES = 3500
_MAX_BODY_CHARS = 200
_MAX_ENDPOINT_CHARS = 2048

_DEFAULT_ICON = '/static/images/icon-192.png'
_DEFAULT_TAG = 'nuun-default'


# ---------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------
def is_available() -> bool:
    """True when VAPID keys are set AND pywebpush imported cleanly."""
    return bool(
        _PYWEBPUSH_AVAILABLE
        and Config.VAPID_PUBLIC_KEY
        and Config.VAPID_PRIVATE_KEY
    )


# ---------------------------------------------------------------------
# Subscription CRUD
# ---------------------------------------------------------------------
def save_subscription(
    user_id: int,
    endpoint: str,
    p256dh: str,
    auth: str,
) -> bool:
    """
    Insert or update a push subscription for the given user.

    Idempotent: re-subscribing with the same endpoint (which happens
    after a browser reload) just updates the keys and the timestamp.
    """
    if not (user_id and endpoint and p256dh and auth):
        return False

    if len(endpoint) > _MAX_ENDPOINT_CHARS:
        logger.warning(
            "Rejecting oversized push endpoint (%d chars) for user %s",
            len(endpoint), user_id,
        )
        return False

    try:
        execute_with_retry(
            """
            INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, endpoint) DO UPDATE SET
                p256dh = excluded.p256dh,
                auth   = excluded.auth
            """,
            (user_id, endpoint, p256dh, auth),
            commit=True,
        )
        return True
    except Exception:
        logger.exception("Failed to save push subscription for user %s", user_id)
        return False


def remove_subscription(user_id: int, endpoint: str) -> bool:
    """Delete a single subscription. Idempotent."""
    if not (user_id and endpoint):
        return False
    try:
        execute_with_retry(
            "DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?",
            (user_id, endpoint),
            commit=True,
        )
        return True
    except Exception:
        logger.exception("Failed to remove push subscription for user %s", user_id)
        return False


def _delete_by_endpoint(endpoint: str) -> None:
    """Prune a dead subscription without knowing the user (used on 410)."""
    try:
        execute_with_retry(
            "DELETE FROM push_subscriptions WHERE endpoint = ?",
            (endpoint,),
            commit=True,
        )
    except Exception:
        logger.exception("Failed to prune dead subscription")


def get_subscriptions(user_id: int) -> List[Dict]:
    """Return all active subscriptions for the user."""
    try:
        cursor = execute_with_retry(
            "SELECT id, endpoint, p256dh, auth "
            "FROM push_subscriptions WHERE user_id = ?",
            (user_id,),
        )
        return [dict(row) for row in cursor.fetchall()]
    except Exception:
        logger.exception("Failed to load push subscriptions for user %s", user_id)
        return []


def count_subscriptions(user_id: int) -> int:
    """Number of devices the user has enabled push on."""
    try:
        cursor = execute_with_retry(
            "SELECT COUNT(*) AS n FROM push_subscriptions WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        return int(row['n']) if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------
def _build_payload(
    title: str,
    body: str,
    url: Optional[str],
    icon: Optional[str],
    tag: Optional[str],
    data: Optional[Dict],
) -> str:
    """Build a payload JSON string, hard-capped at _MAX_PAYLOAD_BYTES."""
    if body and len(body) > _MAX_BODY_CHARS:
        body = body[: _MAX_BODY_CHARS - 1] + '\u2026'

    payload = {
        'title': title or 'NuunPlatform',
        'body':  body or '',
        'url':   url or '/',
        'icon':  icon or _DEFAULT_ICON,
        'badge': _DEFAULT_ICON,
        'tag':   tag or _DEFAULT_TAG,
        'data':  data or {},
    }

    raw = json.dumps(payload, ensure_ascii=False)

    if len(raw.encode('utf-8')) > _MAX_PAYLOAD_BYTES:
        payload['body'] = payload['body'][:80]
        raw = json.dumps(payload, ensure_ascii=False)

    return raw


def deliver(
    user_id: int,
    title: str,
    body: str,
    url: Optional[str] = None,
    icon: Optional[str] = None,
    tag: Optional[str] = None,
    data: Optional[Dict] = None,
) -> Dict[str, int]:
    """
    Send a push to every active subscription the user has.

    Returns {'sent': int, 'failed': int, 'pruned': int}.
    Never raises. If push isn't configured, returns all zeros.
    """
    result = {'sent': 0, 'failed': 0, 'pruned': 0}

    if not is_available():
        return result

    subscriptions = get_subscriptions(user_id)
    if not subscriptions:
        return result

    payload = _build_payload(title, body, url, icon, tag, data)
    vapid_claims = {'sub': Config.VAPID_SUBJECT}

    for sub in subscriptions:
        endpoint = sub['endpoint']
        try:
            webpush(
                subscription_info={
                    'endpoint': endpoint,
                    'keys': {
                        'p256dh': sub['p256dh'],
                        'auth':   sub['auth'],
                    },
                },
                data=payload,
                vapid_private_key=Config.VAPID_PRIVATE_KEY,
                vapid_claims=dict(vapid_claims),
                timeout=10,
            )
            result['sent'] += 1

        except WebPushException as exc:
            status = getattr(exc.response, 'status_code', None)

            if status in (404, 410):
                # Endpoint revoked or expired — prune and move on.
                _delete_by_endpoint(endpoint)
                result['pruned'] += 1
                logger.info(
                    "Pruned dead push subscription (status=%s) for user %s",
                    status, user_id,
                )
            else:
                result['failed'] += 1
                logger.warning(
                    "Push delivery failed (status=%s) for user %s",
                    status, user_id,
                )

        except Exception:
            result['failed'] += 1
            logger.exception(
                "Unexpected push delivery error for user %s", user_id,
            )

    return result