# blueprints/settings_bp.py
# Tier model: free / premium only. No pro.
# Language is not gated — every user can switch freely.

from flask import Blueprint, render_template, request, session, jsonify
from functools import wraps
from werkzeug.security import check_password_hash, generate_password_hash
from services.settings_service import SettingsService
from services.settings_registry import SETTINGS_REGISTRY, get_all_categories
from services.tier_service import get_current_user_tier, can_create_live_quiz, is_tier_at_least
from services import entitlement_service
from services.settings_content import (
    get_feature_copy, get_category_label, get_category_order,
)
from utils import validate_csrf
from db import get_user_subject_list
import logging

logger = logging.getLogger(__name__)

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Please login first.'}), 401
        return f(*args, **kwargs)
    return decorated


def _build_tier_features(user_id, tier):
    """
    Return every feature the user's current tier includes, grouped
    and ordered for the settings page.

    Source of truth is the entitlement policy — this list changes
    automatically when an admin toggles a feature on the tier. Copy
    comes from settings_content.FEATURE_COPY when present, falling
    back to the seed's display_name/description otherwise.
    """
    try:
        all_features = entitlement_service.list_features()
    except Exception as e:
        logger.warning(f"tier_features: list_features failed: {e}")
        return []

    out = []
    for f in all_features:
        if not f.get('is_global_active'):
            continue

        policy = (f.get('policies') or {}).get(tier) or {}
        if not policy.get('is_enabled'):
            continue

        key = f.get('feature_key')
        if not key:
            continue

        copy = get_feature_copy(key)
        category = f.get('category', 'general')

        out.append({
            'key':            key,
            'label':          copy.get('name') or f.get('display_name', key),
            'description':    copy.get('description') or f.get('description', ''),
            'icon':           copy.get('icon', '⚙️'),
            'category':       category,
            'category_label': get_category_label(category),
            'available':      True,
            'tier_required':  None,
            'order':          f.get('sort_order', 500),
        })

    out.sort(key=lambda x: (get_category_order(x['category']), x['order'], x['key']))
    return out


@settings_bp.route('/')
@login_required
def index():
    user_id = session['user_id']
    SettingsService.ensure_migrated(user_id)

    tier = get_current_user_tier()
    settings = SettingsService.get_all(user_id)
    categories = get_all_categories()
    can_create = can_create_live_quiz()
    user_subjects = get_user_subject_list(user_id)

    language_allowed = True

    # Features of the user's current tier, grouped + ordered.
    # Replaces the old SETTINGS_REGISTRY-derived list, which showed
    # settings toggles (font size, theme, etc.) as if they were tier
    # features.
    tier_features = _build_tier_features(user_id, tier)

    # ── Telegram broadcast state ──
    public_id = (session.get('public_id') or '').strip()
    telegram_linked = False
    telegram_broadcast_active = False
    if public_id:
        try:
            from bot.db import get_broadcast_state_by_public_id
            linked, subscribed, _blocked = get_broadcast_state_by_public_id(public_id)
            telegram_linked = bool(linked)
            telegram_broadcast_active = bool(linked and subscribed)
        except Exception as e:
            logger.warning(f"telegram broadcast state lookup failed: {e}")

    return render_template('settings/index.html',
                           tier=tier,
                           settings=settings,
                           categories=categories,
                           can_create_live=can_create,
                           user_subjects=user_subjects,
                           tier_features=tier_features,
                           language_allowed=language_allowed,
                           telegram_linked=telegram_linked,
                           telegram_broadcast_active=telegram_broadcast_active)


@settings_bp.route('/api', methods=['GET'])
@login_required
def api_get():
    user_id = session['user_id']
    category = request.args.get('category')
    settings = SettingsService.get_all(user_id)
    if category:
        category_keys = {k for k, v in SETTINGS_REGISTRY.items()
                         if v.get('category') == category}
        settings = {k: v for k, v in settings.items() if k in category_keys}
    return jsonify(settings)


@settings_bp.route('/api', methods=['PATCH'])
@login_required
def api_patch():
    if not validate_csrf():
        logger.warning(f"CSRF validation failed for user {session['user_id']}")
        return jsonify({'error': 'CSRF validation failed. Please refresh the page and try again.'}), 403

    user_id = session['user_id']
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    try:
        updated = SettingsService.update(user_id, data)
        return jsonify({'success': True, 'settings': updated})
    except ValueError as e:
        logger.warning(f"Validation error for user {user_id}: {e}")
        return jsonify({'error': str(e)}), 400
    except PermissionError as e:
        logger.warning(f"Permission error for user {user_id}: {e}")
        return jsonify({'error': str(e)}), 403
    except RuntimeError as e:
        logger.error(f"Runtime error for user {user_id}: {e}", exc_info=True)
        return jsonify({'error': 'Internal error: ' + str(e)}), 500
    except Exception as e:
        logger.error(f"Unexpected error for user {user_id}: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@settings_bp.route('/api/reset', methods=['POST'])
@login_required
def api_reset():
    if not validate_csrf():
        return jsonify({'error': 'CSRF validation failed'}), 403
    user_id = session['user_id']
    data = request.get_json()
    key = data.get('key')
    if not key:
        return jsonify({'error': 'Missing key'}), 400
    try:
        updated = SettingsService.reset(user_id, key)
        return jsonify({'success': True, 'settings': updated})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except Exception as e:
        logger.error(f"Unexpected error in settings reset: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@settings_bp.route('/api/password', methods=['POST'])
@login_required
def api_password():
    if not validate_csrf():
        return jsonify({'error': 'CSRF validation failed'}), 403

    user_id = session['user_id']
    data = request.get_json() or {}
    current = data.get('current_password', '')
    new = data.get('new_password', '')
    confirm = data.get('confirm_password', '')

    if not current or not new or not confirm:
        return jsonify({'error': 'All fields are required.'}), 400
    if new != confirm:
        return jsonify({'error': 'Passwords do not match.'}), 400
    if len(new) < 8:
        return jsonify({'error': 'Password must be at least 8 characters.'}), 400
    if new == current:
        return jsonify({'error': 'New password must differ from the current one.'}), 400

    from db import get_student_by_id, execute_with_retry

    student = get_student_by_id(user_id)
    if not student:
        return jsonify({'error': 'Account not found.'}), 404

    if not check_password_hash(student.get('password', ''), current):
        logger.warning(f"Password change failed: wrong current password (user {user_id})")
        return jsonify({'error': 'Current password is incorrect.'}), 400

    new_hash = generate_password_hash(new)

    try:
        execute_with_retry(
            "UPDATE students "
            "SET password = ?, "
            "    session_version = COALESCE(session_version, 0) + 1 "
            "WHERE id = ?",
            (new_hash, user_id),
            commit=True
        )
    except Exception as e:
        logger.error(f"Password update failed for user {user_id}: {e}", exc_info=True)
        return jsonify({'error': 'Could not update password. Please try again.'}), 500

    logger.info(f"Password changed for user {user_id} — session_version bumped")
    return jsonify({
        'success': True,
        'message': 'Password changed. Please log in again.',
    })


@settings_bp.route('/test-save', methods=['GET'])
@login_required
def test_save():
    """
    DEBUG ONLY. Gated behind Config.DEBUG so it can never be reached
    in production.
    """
    from config import Config
    if not Config.DEBUG:
        return jsonify({'error': 'Not available'}), 404

    user_id = session['user_id']
    try:
        result = SettingsService.update(user_id, {'appearance.theme': 'dark'})
        return jsonify({'status': 'ok', 'result': result})
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500