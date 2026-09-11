# blueprints/settings_bp.py
from flask import Blueprint, render_template, request, session, jsonify
from functools import wraps
from services.settings_service import SettingsService
from services.settings_registry import SETTINGS_REGISTRY, get_all_categories
from services.tier_service import get_current_user_tier, can_create_live_quiz, is_tier_at_least
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

    tier_features = []
    for key, definition in SETTINGS_REGISTRY.items():
        tier_required = definition.get('tier_required')
        available = tier_required is None or is_tier_at_least(tier, tier_required)
        tier_features.append({
            'key': key,
            'label': definition.get('label', key),
            'description': definition.get('description', ''),
            'icon': definition.get('icon', '⚙️'),
            'available': available,
            'tier_required': tier_required,
            'category': definition.get('category', '')
        })

    return render_template('settings/index.html',
                           tier=tier,
                           settings=settings,
                           categories=categories,
                           can_create_live=can_create,
                           user_subjects=user_subjects,
                           tier_features=tier_features)

@settings_bp.route('/api', methods=['GET'])
@login_required
def api_get():
    user_id = session['user_id']
    category = request.args.get('category')
    settings = SettingsService.get_all(user_id)
    if category:
        category_keys = {k for k, v in SETTINGS_REGISTRY.items() if v.get('category') == category}
        settings = {k: v for k, v in settings.items() if k in category_keys}
    return jsonify(settings)

@settings_bp.route('/api', methods=['PATCH'])
@login_required
def api_patch():
    logger.info(f"Settings PATCH request from user {session['user_id']}")
    
    if not validate_csrf():
        logger.warning(f"CSRF validation failed for user {session['user_id']}")
        return jsonify({'error': 'CSRF validation failed. Please refresh the page and try again.'}), 403
    
    user_id = session['user_id']
    data = request.get_json()
    
    if not data:
        logger.warning(f"Empty data from user {user_id}")
        return jsonify({'error': 'No data provided'}), 400
    
    logger.info(f"User {user_id} updating settings: {data}")
    
    try:
        updated = SettingsService.update(user_id, data)
        logger.info(f"Settings updated successfully for user {user_id}")
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
    data = request.get_json()
    current = data.get('current_password', '')
    new = data.get('new_password', '')
    confirm = data.get('confirm_password', '')
    if not current or not new or not confirm:
        return jsonify({'error': 'All fields are required.'}), 400
    if new != confirm:
        return jsonify({'error': 'Passwords do not match.'}), 400
    if len(new) < 8:
        return jsonify({'error': 'Password must be at least 8 characters.'}), 400
    from db import get_student_by_id, execute_with_retry
    student = get_student_by_id(user_id)
    if not student or student['password'] != current:
        return jsonify({'error': 'Current password is incorrect.'}), 400
    execute_with_retry(
        "UPDATE students SET password = ? WHERE id = ?",
        (new, user_id),
        commit=True
    )
    return jsonify({'success': True, 'message': 'Password changed. Please log in again.'})

# ============================================
# TEST ENDPOINT (for debugging)
# ============================================
@settings_bp.route('/test-save', methods=['GET'])
@login_required
def test_save():
    user_id = session['user_id']
    try:
        from services.settings_service import SettingsService
        result = SettingsService.update(user_id, {'appearance.theme': 'dark'})
        return jsonify({'status': 'ok', 'result': result})
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500