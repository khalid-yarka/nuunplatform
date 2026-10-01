# ============================================================
# blueprints/live_quiz/routes_react.py
# ============================================================
# Per-question reactions (like / save / report) and the live
# leaderboard. All routes are quiz-scoped with <int:quiz_id>.

import json
import logging

from flask import request, session, jsonify

from services.tier_service import get_saved_content_limit
from utils import validate_csrf

from . import live_quiz_bp
from ._shared import get_state_manager

logger = logging.getLogger(__name__)


@live_quiz_bp.route('/interaction/<int:quiz_id>/status', methods=['GET'])
def interaction_status(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    qid_raw = request.args.get('question_id')
    try:
        question_id = int(qid_raw)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid question_id'}), 400

    manager = get_state_manager()
    manager.ensure_quiz_in_memory(quiz_id)
    quiz_state = manager.get_quiz(quiz_id)
    if not quiz_state:
        return jsonify({'liked': False, 'saved': False, 'reported': False})

    return jsonify(quiz_state.get_reaction_status(session['user_id'], question_id))


@live_quiz_bp.route('/interaction/<int:quiz_id>/like', methods=['POST'])
def interaction_like(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    data = request.get_json() or {}
    try:
        question_id = int(data.get('question_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid question_id'}), 400

    manager = get_state_manager()
    if not manager.ensure_quiz_in_memory(quiz_id):
        return jsonify({'error': 'Quiz not active'}), 404
    quiz_state = manager.get_quiz(quiz_id)
    if not quiz_state:
        return jsonify({'error': 'Quiz not active'}), 404

    ok = quiz_state.toggle_like(user_id, question_id)
    if not ok:
        return jsonify({'error': 'Could not toggle like'}), 400

    p = quiz_state.get_participant(user_id)
    liked = (question_id in p.likes) if p else False
    return jsonify({'liked': liked})


@live_quiz_bp.route('/interaction/<int:quiz_id>/save', methods=['POST'])
def interaction_save(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    data = request.get_json() or {}
    try:
        question_id = int(data.get('question_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid question_id'}), 400

    manager = get_state_manager()
    if not manager.ensure_quiz_in_memory(quiz_id):
        return jsonify({'error': 'Quiz not active'}), 404
    quiz_state = manager.get_quiz(quiz_id)
    if not quiz_state:
        return jsonify({'error': 'Quiz not active'}), 404

    limit = get_saved_content_limit(user_id)
    p = quiz_state.get_participant(user_id)
    already_saved = (p is not None and question_id in p.saves)

    if limit is not None and limit < 999 and not already_saved:
        try:
            from services.interaction_service import count_user_saves
            db_count = count_user_saves(user_id)
        except Exception:
            db_count = 0
        session_count = len(p.saves) if p else 0
        current = db_count + session_count
        if current >= limit:
            return jsonify({
                'error': 'Save limit reached',
                'limit': limit,
                'current': current,
            }), 429

    ok = quiz_state.toggle_save(user_id, question_id)
    if not ok:
        return jsonify({'error': 'Could not toggle save'}), 400

    p = quiz_state.get_participant(user_id)
    saved = (question_id in p.saves) if p else False
    return jsonify({'saved': saved})


@live_quiz_bp.route('/interaction/<int:quiz_id>/report', methods=['POST'])
def interaction_report(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    data = request.get_json() or {}
    try:
        question_id = int(data.get('question_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid question_id'}), 400

    reason = (data.get('reason') or '').strip()
    comment = (data.get('comment') or '').strip()
    if not reason:
        return jsonify({'error': 'Reason is required'}), 400

    manager = get_state_manager()
    if not manager.ensure_quiz_in_memory(quiz_id):
        return jsonify({'error': 'Quiz not active'}), 404
    quiz_state = manager.get_quiz(quiz_id)
    if not quiz_state:
        return jsonify({'error': 'Quiz not active'}), 404

    try:
        from services.interaction_service import submit_report
        result = submit_report(user_id, question_id, reason, comment)
    except Exception as e:
        logger.error(f"live interaction_report failed: {e}", exc_info=True)
        return jsonify({'error': 'Report could not be submitted'}), 500

    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Report failed')}), 400

    quiz_state.add_report(user_id, question_id, reason, comment)
    return jsonify({'success': True})


@live_quiz_bp.route('/leaderboard/<int:quiz_id>')
def get_leaderboard(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    user_id = session['user_id']
    manager = get_state_manager()
    manager.ensure_quiz_in_memory(quiz_id)
    quiz_state = manager.get_quiz(quiz_id)

    if not quiz_state:
        return jsonify({'error': 'Quiz not active'}), 404

    leaderboard = quiz_state.get_leaderboard(limit=10)
    user_rank = quiz_state.get_user_rank(user_id)

    return jsonify({'leaderboard': leaderboard, 'user_rank': user_rank})