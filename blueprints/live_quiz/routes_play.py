# ============================================================
# blueprints/live_quiz/routes_play.py
# ============================================================
# In-quiz: start, state polling, question fetch, submit/skip/
# advance/rating. quiz_id in the URL is <int:quiz_id>. In JSON
# bodies, quiz_id and question_id are coerced to int so the state
# manager receives ints even if the client sends strings.

import json
import logging
from datetime import datetime, timezone

from flask import request, session, jsonify, url_for

from db import get_live_quiz_by_id, update_live_quiz
from config import Config
from utils import validate_csrf, get_somali_time_db

from . import live_quiz_bp
from ._shared import (
    get_state_manager,
    RATING_TIME,
)
from .finalize import finalize_live_quiz, _maybe_auto_start_scheduled
from .notify import notify_quiz_started

logger = logging.getLogger(__name__)


@live_quiz_bp.route('/start/<int:quiz_id>', methods=['POST'])
def start_quiz(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404
    if quiz['creator_id'] != user_id:
        return jsonify({'error': 'Only the creator can start the quiz'}), 403
    if quiz['status'] not in ['waiting', 'scheduled']:
        return jsonify({'error': 'Quiz already started or finished'}), 400

    manager = get_state_manager()
    if not manager.ensure_quiz_in_memory(quiz_id):
        return jsonify({'error': 'Quiz state not in memory and could not be recovered'}), 500

    quiz_state = manager.get_quiz(quiz_id)
    if not quiz_state:
        return jsonify({'error': 'Quiz state not in memory'}), 500

    active_count = quiz_state.get_active_count()
    if active_count < 2:
        return jsonify({'error': 'Need at least 2 active participants to start'}), 400

    success = quiz_state.start()
    if not success:
        return jsonify({'error': 'Failed to start quiz'}), 500

    update_live_quiz(quiz_id, {
        'status': 'active',
        'started_at': get_somali_time_db(),
        'scheduled_start': None,
    })

    manager.enqueue_event({
        'quiz_id': quiz_id,
        'event_type': 'START',
        'payload': json.dumps({}),
    })

    # Identical text and priority to the scheduled auto-start path.
    participants = quiz_state.get_all_participants()
    for p in participants:
        try:
            notify_quiz_started(p['student_id'], quiz)
        except Exception as e:
            logger.warning(
                f"quiz-start notify failed for user {p.get('student_id')} "
                f"in quiz {quiz_id}: {e}"
            )

    return jsonify({
        'success': True,
        'quiz_id': quiz_id,
        'redirect_url': url_for('live_quiz.play', quiz_id=quiz_id),
    })


@live_quiz_bp.route('/quiz-state/<int:quiz_id>')
def quiz_state_endpoint(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    # Re-fetch with subject view for template fields
    from db import get_live_quiz_with_subject
    quiz_view = get_live_quiz_with_subject(quiz_id) or quiz

    # ── Auto-start / auto-flip for scheduled quizzes ──
    if quiz.get('status') == 'scheduled' and quiz.get('scheduled_start'):
        try:
            _maybe_auto_start_scheduled(quiz)
            quiz = get_live_quiz_by_id(quiz_id) or quiz
            quiz_view = get_live_quiz_with_subject(quiz_id) or quiz
        except Exception as e:
            logger.warning(f"auto-start check failed for {quiz_id}: {e}")

    manager = get_state_manager()
    manager.ensure_quiz_in_memory(quiz_id)
    quiz_state = manager.get_quiz(quiz_id)

    if not quiz_state:
        if quiz_view['status'] == 'finished':
            return jsonify({
                'status': 'finished',
                'redirect_url': url_for('live_quiz.results', quiz_id=quiz_id),
            })
        return jsonify({
            'status': quiz_view['status'],
            'error': 'Quiz not active',
        })

    p = quiz_state.get_participant(user_id)
    if not p and quiz_view['creator_id'] != user_id:
        return jsonify({
            'error': 'Not a participant',
            'reason': 'not_synced',
        }), 409

    total_questions = len(quiz_state.question_ids)
    current_index = p.current_question_index if p else 0
    score = p.score if p else 0
    answers = p.answers if p else {}

    if quiz_state.is_finished():
        return jsonify({
            'status': 'finished',
            'redirect_url': url_for('live_quiz.results', quiz_id=quiz_id),
        })

    all_completed = quiz_state.is_completed()
    if all_completed and quiz_state.status == 'active':
        finalize_live_quiz(quiz_id)
        return jsonify({
            'status': 'finished',
            'redirect_url': url_for('live_quiz.results', quiz_id=quiz_id),
        })

    remaining_time = None
    if quiz_state.status == 'active' and quiz_state.started_at:
        try:
            total_duration = (
                quiz_state.metadata.get('question_count', 10)
                * (quiz_state.metadata.get('time_per_question', 30) + RATING_TIME)
            )
            started = datetime.fromisoformat(quiz_state.started_at.replace('Z', '+00:00'))
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            remaining = max(0, total_duration - elapsed)
            remaining_time = int(remaining)
            if remaining_time == 0:
                finalize_live_quiz(quiz_id)
                return jsonify({
                    'status': 'finished',
                    'redirect_url': url_for('live_quiz.results', quiz_id=quiz_id),
                })
        except Exception as e:
            logger.error(f"Error calculating remaining time: {e}")
            remaining_time = 0

    response = {
        'status': quiz_state.status,
        'current_question_index': current_index,
        'total_questions': total_questions,
        'score': score,
        'is_completed': current_index >= total_questions,
        'all_completed': all_completed,
        'completed_count': sum(
            1 for pp in quiz_state.participants.values()
            if pp.current_question_index >= total_questions
        ),
        'total_participants': len([
            pp for pp in quiz_state.participants.values()
            if pp.status != 'left'
        ]),
        'remaining_time': remaining_time,
    }

    if current_index < total_questions:
        qid = quiz_state.question_ids[current_index]
        if str(qid) in answers:
            response['current_question_answered'] = True
            response['current_question_answer'] = answers[str(qid)].get('answer')
            response['current_question_correct'] = answers[str(qid)].get('correct', False)

    progress = []
    with quiz_state.lock:
        for uid, pp in quiz_state.participants.items():
            progress.append({
                'user_id': uid,
                'name': pp.name,
                'current_question_index': pp.current_question_index,
                'total_questions': total_questions,
                'status': pp.status,
                'score': pp.score,
            })
    response['participant_progress'] = progress

    return jsonify(response)


@live_quiz_bp.route('/get-question/<int:quiz_id>')
def get_question(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    user_id = session['user_id']
    manager = get_state_manager()
    if not manager.ensure_quiz_in_memory(quiz_id):
        return jsonify({'error': 'Quiz not active'}), 404

    quiz_state = manager.get_quiz(quiz_id)
    if not quiz_state:
        return jsonify({'error': 'Quiz not active'}), 404

    p = quiz_state.get_participant(user_id)
    if not p:
        return jsonify({'error': 'Not a participant'}), 404

    if quiz_state.status != 'active':
        return jsonify({'error': 'Quiz not active'}), 400

    q_data = quiz_state.get_current_question_for_participant(user_id)
    if not q_data:
        return jsonify({'completed': True})

    qid = q_data['id']
    total = len(quiz_state.question_ids)
    current_index = p.current_question_index

    if str(qid) in p.answers:
        answer_data = p.answers[str(qid)]
        return jsonify({
            'question': q_data,
            'index': current_index,
            'total': total,
            'already_answered': True,
            'answer': answer_data.get('answer'),
            'correct': answer_data.get('correct', False),
            'correct_answer': q_data['correct_answer'],
            'explanation': q_data.get('explanation', ''),
        })

    return jsonify({
        'question': q_data,
        'index': current_index,
        'total': total,
        'already_answered': False,
    })


@live_quiz_bp.route('/submit-answer', methods=['POST'])
def submit_answer():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    data = request.get_json() or {}
    try:
        quiz_id = int(data.get('quiz_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid quiz_id'}), 400
    try:
        question_id = int(data.get('question_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid question_id'}), 400
    answer = data.get('answer')
    if not answer:
        return jsonify({'error': 'Missing required fields'}), 400

    manager = get_state_manager()
    if not manager.ensure_quiz_in_memory(quiz_id):
        return jsonify({'error': 'Quiz not active'}), 404
    quiz_state = manager.get_quiz(quiz_id)
    if not quiz_state:
        return jsonify({'error': 'Quiz not active'}), 404

    success, result = quiz_state.submit_answer(user_id, question_id, answer)
    if not success:
        return jsonify({'error': result.get('error', 'Submission failed')}), 400

    manager.enqueue_event({
        'quiz_id': quiz_id,
        'user_id': user_id,
        'question_id': question_id,
        'event_type': 'ANSWER',
        'payload': json.dumps({'answer': answer}),
    })

    return jsonify({
        'correct': result['correct'],
        'correct_answer': result['correct_answer'],
        'explanation': result['explanation'],
        'new_score': result['new_score'],
    })


@live_quiz_bp.route('/skip-question', methods=['POST'])
def skip_question():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    data = request.get_json() or {}
    try:
        quiz_id = int(data.get('quiz_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid quiz_id'}), 400
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

    success, msg = quiz_state.skip_question(user_id, question_id)
    if not success:
        return jsonify({'error': msg}), 400

    manager.enqueue_event({
        'quiz_id': quiz_id,
        'user_id': user_id,
        'question_id': question_id,
        'event_type': 'SKIP',
        'payload': json.dumps({}),
    })

    return jsonify({'success': True})


@live_quiz_bp.route('/advance', methods=['POST'])
def advance_question():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    data = request.get_json() or {}
    try:
        quiz_id = int(data.get('quiz_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid quiz_id'}), 400
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

    success, msg = quiz_state.advance_question(user_id, question_id)
    if not success:
        return jsonify({'error': msg}), 400

    manager.enqueue_event({
        'quiz_id': quiz_id,
        'user_id': user_id,
        'question_id': question_id,
        'event_type': 'ADVANCE',
        'payload': json.dumps({}),
    })

    p = quiz_state.get_participant(user_id)
    total = len(quiz_state.question_ids)
    completed = (p.current_question_index >= total) if p else False
    return jsonify({'success': True, 'completed': completed})


@live_quiz_bp.route('/submit-rating', methods=['POST'])
def submit_rating_shim():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    data = request.get_json() or {}
    try:
        quiz_id = int(data.get('quiz_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid quiz_id'}), 400
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

    success, msg = quiz_state.advance_question(session['user_id'], question_id)
    if not success:
        return jsonify({'error': msg}), 400

    manager.enqueue_event({
        'quiz_id': quiz_id,
        'user_id': session['user_id'],
        'question_id': question_id,
        'event_type': 'ADVANCE',
        'payload': json.dumps({}),
    })

    p = quiz_state.get_participant(session['user_id'])
    total = len(quiz_state.question_ids)
    completed = (p.current_question_index >= total) if p else False
    return jsonify({'success': True, 'completed': completed})