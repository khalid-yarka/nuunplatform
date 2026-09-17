# blueprints/push_bp.py
"""
Push notification routes.

    GET  /push/vapid-key     public — returns the VAPID public key
    POST /push/subscribe     auth   — store a new subscription
    POST /push/unsubscribe   auth   — remove a subscription
    POST /push/test          auth   — deliver a test push to the caller
    GET  /push/status        auth   — report enabled + subscription count

All POSTs require a valid CSRF token in the X-CSRF-Token header.
Subscribe and test are rate-limited per-IP / per-user.
"""

import logging
import time
from functools import wraps

from flask import Blueprint, jsonify, request, session

from config import Config
from utils import validate_csrf
from services import push_service

logger = logging.getLogger(__name__)

push_bp = Blueprint('push', __name__, url_prefix='/push')


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
_rate_buckets = {}


def _rate_limit(key: str, max_calls: int, window_seconds: int) -> bool:
    """In-memory sliding-window limiter. Mirrors the auth_bp pattern."""
    now = time.time()
    bucket = _rate_buckets.setdefault(key, [])
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= max_calls:
        return False
    bucket.append(now)
    return True


def _client_ip() -> str:
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@push_bp.route('/vapid-key', methods=['GET'])
def vapid_key():
    """
    Public. The client calls this before subscribing.

    Returns 404 if push isn't configured — the client interprets that
    as "push unavailable" and shows the unsupported state.
    """
    if not Config.VAPID_PUBLIC_KEY:
        return jsonify({'error': 'Push is not configured'}), 404
    return jsonify({'key': Config.VAPID_PUBLIC_KEY})


@push_bp.route('/subscribe', methods=['POST'])
@login_required
def subscribe():
    if not validate_csrf():
        return jsonify({'error': 'Invalid CSRF token'}), 403

    if not _rate_limit(f'push-sub:{_client_ip()}', 10, 3600):
        return jsonify({'error': 'Too many requests'}), 429

    data = request.get_json(silent=True) or {}
    endpoint = (data.get('endpoint') or '').strip()
    keys = data.get('keys') or {}
    p256dh = (keys.get('p256dh') or '').strip()
    auth = (keys.get('auth') or '').strip()

    if not endpoint or not p256dh or not auth:
        return jsonify({'error': 'Missing subscription fields'}), 400

    ok = push_service.save_subscription(
        session['user_id'], endpoint, p256dh, auth,
    )
    if not ok:
        return jsonify({'error': 'Could not save subscription'}), 500

    # Flip the master setting on so the notification_service fan-out
    # will actually call us. This is a no-op if it's already on.
    try:
        from user_settings import update_user_settings
        update_user_settings(
            session['user_id'], {'notifications.push_enabled': 1},
        )
    except Exception:
        logger.warning("Could not update push_enabled setting", exc_info=True)

    return jsonify({'success': True})


@push_bp.route('/unsubscribe', methods=['POST'])
@login_required
def unsubscribe():
    if not validate_csrf():
        return jsonify({'error': 'Invalid CSRF token'}), 403

    data = request.get_json(silent=True) or {}
    endpoint = (data.get('endpoint') or '').strip()
    if not endpoint:
        return jsonify({'error': 'Missing endpoint'}), 400

    push_service.remove_subscription(session['user_id'], endpoint)

    # If that was the last subscription, flip the master setting off.
    try:
        from user_settings import update_user_settings
        remaining = push_service.count_subscriptions(session['user_id'])
        if remaining == 0:
            update_user_settings(
                session['user_id'], {'notifications.push_enabled': 0},
            )
    except Exception:
        logger.warning("Could not update push_enabled setting", exc_info=True)

    return jsonify({'success': True})


@push_bp.route('/test', methods=['POST'])
@login_required
def test():
    if not validate_csrf():
        return jsonify({'error': 'Invalid CSRF token'}), 403

    if not _rate_limit(f'push-test:{session["user_id"]}', 5, 3600):
        return jsonify({'error': 'Too many test pushes. Try again later.'}), 429

    if not push_service.is_available():
        return jsonify({'error': 'Push is not configured on the server'}), 503

    result = push_service.deliver(
        session['user_id'],
        title='NuunPlatform',
        body='Test notification \u2014 if you can see this, push works.',
        url='/settings/#notifications',
        tag='nuun-test',
        data={'type': 'test'},
    )

    if result['sent'] == 0:
        # Either there's no subscription for this browser yet, or
        # every endpoint died. Either way, the client should re-subscribe.
        return jsonify({
            'error': 'No active subscription on this device',
            'result': result,
        }), 400

    return jsonify({'success': True, 'result': result})


@push_bp.route('/status', methods=['GET'])
@login_required
def status():
    """Report server-side push state for the settings UI."""
    return jsonify({
        'enabled': push_service.is_available(),
        'subscription_count': push_service.count_subscriptions(session['user_id']),
    })