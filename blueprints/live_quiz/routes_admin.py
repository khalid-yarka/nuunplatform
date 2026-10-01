# ============================================================
# blueprints/live_quiz/routes_admin.py
# ============================================================
# Admin-only utility endpoints for the live quiz cache and state.

import logging

from flask import request, session, jsonify

from db import is_admin
from utils import validate_csrf

from . import live_quiz_bp
from ._shared import get_state_manager, invalidate_quiz_cache

logger = logging.getLogger(__name__)


@live_quiz_bp.route('/flush-cache', methods=['POST'])
def flush_cache_endpoint():
    if 'user_id' not in session or not is_admin(session['user_id']):
        return jsonify({'error': 'Unauthorized'}), 403
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    try:
        invalidate_quiz_cache('*')
        return jsonify({'success': True, 'message': 'Quiz cache flushed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@live_quiz_bp.route('/cache-stats')
def cache_stats():
    if 'user_id' not in session or not is_admin(session['user_id']):
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        manager = get_state_manager()
        return jsonify({
            'active_quizzes': len(manager._quizzes),
            'participants': sum(
                len(q.participants) for q in manager._quizzes.values()
            ),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500