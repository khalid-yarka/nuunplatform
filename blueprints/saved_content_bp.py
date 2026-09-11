# blueprints/saved_content_bp.py

from flask import Blueprint, request, session, jsonify, abort, render_template
from functools import wraps
from db import execute_with_retry, get_student_by_id
from services.tier_service import can_save_content, get_saved_content_count, get_saved_content_limit, get_current_user_tier
from utils import ensure_csrf_token, validate_csrf
from history_logger import add_history_entry   # NEW

saved_content_bp = Blueprint('saved_content', __name__, url_prefix='/saved')


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            abort(401)
        return f(*args, **kwargs)
    return decorated


@saved_content_bp.route('/')
@login_required
def index():
    """List saved items."""
    user_id = session['user_id']
    cursor = execute_with_retry(
        "SELECT content_type, content_id, saved_at FROM saved_content WHERE user_id = ? ORDER BY saved_at DESC",
        (user_id,)
    )
    saved = [dict(row) for row in cursor.fetchall()]
    limit = get_saved_content_limit(user_id)
    total = get_saved_content_count(user_id)
    remaining = None if limit is None else max(0, limit - total)
    tier = get_current_user_tier()
    return render_template('dashboard/saved_content.html', saved=saved, limit=limit, total=total, remaining=remaining, tier=tier)


@saved_content_bp.route('/save', methods=['POST'])
@login_required
def save():
    if not validate_csrf():
        abort(403)
    user_id = session['user_id']
    data = request.get_json()
    content_type = data.get('content_type')
    content_id = data.get('content_id')
    if not content_type or not content_id:
        return jsonify({'error': 'Missing content_type or content_id'}), 400

    if not can_save_content(user_id):
        limit = get_saved_content_limit(user_id)
        return jsonify({'error': f'Cannot save more than {limit if limit is not None else "unlimited"} items'}), 429

    try:
        execute_with_retry(
            "INSERT INTO saved_content (user_id, content_type, content_id) VALUES (?, ?, ?)",
            (user_id, content_type, content_id), commit=True
        )
        # ***** ADD HISTORY ENTRY *****
        add_history_entry(
            user_id=user_id,
            entry_type='save',
            action='saved',
            metadata={
                'content_type': content_type,
                'content_id': content_id
            }
        )
        return jsonify({'success': True})
    except Exception as e:
        if 'UNIQUE constraint failed' in str(e):
            return jsonify({'error': 'Already saved'}), 409
        return jsonify({'error': str(e)}), 500


@saved_content_bp.route('/unsave', methods=['POST'])
@login_required
def unsave():
    if not validate_csrf():
        abort(403)
    user_id = session['user_id']
    data = request.get_json()
    content_type = data.get('content_type')
    content_id = data.get('content_id')
    if not content_type or not content_id:
        return jsonify({'error': 'Missing content_type or content_id'}), 400

    execute_with_retry(
        "DELETE FROM saved_content WHERE user_id = ? AND content_type = ? AND content_id = ?",
        (user_id, content_type, content_id), commit=True
    )
    # ***** ADD HISTORY ENTRY *****
    add_history_entry(
        user_id=user_id,
        entry_type='save',
        action='unsaved',
        metadata={
            'content_type': content_type,
            'content_id': content_id
        }
    )
    return jsonify({'success': True})