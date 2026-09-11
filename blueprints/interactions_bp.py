# blueprints/interactions_bp.py

from flask import Blueprint, request, session, jsonify
from functools import wraps
import logging
import traceback
from db import get_question_by_id, create_notification_for_all_users, execute_with_retry
from utils import validate_csrf, get_somali_time_db
from services.tier_service import get_saved_content_limit
from history_logger import add_history_entry   # NEW

logger = logging.getLogger(__name__)

interactions_bp = Blueprint('interactions', __name__, url_prefix='/api/interaction')

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Please login first.'}), 401
        return f(*args, **kwargs)
    return decorated

def ensure_quiz_session():
    if 'quiz' not in session:
        session['quiz'] = {
            'reactions': {'likes': [], 'saves': [], 'reports': {}}
        }
        session.modified = True
    elif 'reactions' not in session['quiz']:
        session['quiz']['reactions'] = {'likes': [], 'saves': [], 'reports': {}}
        session.modified = True


@interactions_bp.route('/like', methods=['POST'])
@login_required
def like():
    try:
        if not validate_csrf():
            return jsonify({'error': 'CSRF token missing or invalid'}), 403

        data = request.get_json()
        question_id = data.get('question_id')
        if not question_id:
            return jsonify({'error': 'Missing question_id'}), 400

        q = get_question_by_id(question_id)
        if not q:
            return jsonify({'error': 'Question not found'}), 404

        ensure_quiz_session()

        likes = session['quiz']['reactions']['likes']
        if question_id in likes:
            likes.remove(question_id)
            liked = False
        else:
            likes.append(question_id)
            liked = True
        session['quiz']['reactions']['likes'] = likes
        session.modified = True

        # ***** ADD HISTORY ENTRY *****
        add_history_entry(
            user_id=session['user_id'],
            entry_type='like',
            action='liked' if liked else 'unliked',
            entry_id=question_id,
            metadata={'question_id': question_id}
        )

        return jsonify({'liked': liked})
    except Exception as e:
        logger.error(f"Like endpoint error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Internal server error. Please try again later.'}), 500


@interactions_bp.route('/save', methods=['POST'])
@login_required
def save():
    try:
        if not validate_csrf():
            return jsonify({'error': 'CSRF token missing or invalid'}), 403

        data = request.get_json()
        question_id = data.get('question_id')
        if not question_id:
            return jsonify({'error': 'Missing question_id'}), 400

        q = get_question_by_id(question_id)
        if not q:
            return jsonify({'error': 'Question not found'}), 404

        limit = get_saved_content_limit(session['user_id'])
        if limit is not None:
            pass

        ensure_quiz_session()

        saves = session['quiz']['reactions']['saves']
        if question_id in saves:
            saves.remove(question_id)
            saved = False
        else:
            saves.append(question_id)
            saved = True
        session['quiz']['reactions']['saves'] = saves
        session.modified = True

        return jsonify({'saved': saved})
    except Exception as e:
        logger.error(f"Save endpoint error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Internal server error. Please try again later.'}), 500


@interactions_bp.route('/report', methods=['POST'])
@login_required
def report():
    try:
        if not validate_csrf():
            return jsonify({'error': 'CSRF token missing or invalid'}), 403

        data = request.get_json()
        question_id = data.get('question_id')
        reason = data.get('reason')
        comment = data.get('comment', '').strip()

        if not question_id or not reason:
            return jsonify({'error': 'Missing question_id or reason'}), 400

        q = get_question_by_id(question_id)
        if not q:
            return jsonify({'error': 'Question not found'}), 404

        ensure_quiz_session()

        reports = session['quiz']['reactions']['reports']
        if str(question_id) in reports:
            return jsonify({'error': 'You have already reported this question.'}), 400

        execute_with_retry("""
            INSERT INTO question_interactions
            (user_id, question_id, interaction_type, report_reason, report_comment, report_status)
            VALUES (?, ?, 'report', ?, ?, 'pending')
        """, (session['user_id'], question_id, reason, comment), commit=True)

        reports[question_id] = {'reason': reason, 'comment': comment}
        session['quiz']['reactions']['reports'] = reports
        session.modified = True

        # ***** ADD HISTORY ENTRY *****
        add_history_entry(
            user_id=session['user_id'],
            entry_type='report',
            action='reported',
            entry_id=question_id,
            metadata={'question_id': question_id, 'reason': reason}
        )

        question_text = q.get('question_text', 'Unknown')[:50]
        create_notification_for_all_users(
            type='admin_report',
            title='⚠️ New Report',
            body=f'User #{session["user_id"]} reported: "{question_text}"',
            link='/admin/reports',
            icon='⚠️'
        )

        return jsonify({'success': True, 'message': 'Report submitted. We will review it shortly.'})

    except Exception as e:
        logger.error(f"Report endpoint error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Internal server error. Please try again later.'}), 500