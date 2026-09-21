# blueprints/quiz_bp.py
# Regular quiz blueprint — grade-aware, ID-based session storage.
#
# Session layout (small — stays under the 4KB cookie limit):
#   session['quiz'] = {
#       'subject_code': str,
#       'grade': 'F4'|'F3',
#       'question_ids': [int, int, ...],
#       'current_index': int,
#       'score': int,
#       'answers': [ {question_id, answer, correct}, ... ],
#       'ratings': [],
#       'reactions': {'likes': [], 'saves': [], 'reports': {}},
#   }
#
# Actual question rows are re-fetched from the DB on demand via
# db.get_questions_by_ids(). This removes the 4.5KB cookie payload.

import json
import logging
from flask import Blueprint, render_template, request, session, flash, redirect, url_for, jsonify
from db import (
    get_questions_by_subject, get_questions_by_ids,
    save_quiz_attempt,
    get_user_quiz_history, update_student_points, get_student_by_id,
    get_leaderboard, get_user_subject_list, execute_with_retry
)
from utils import validate_csrf
from services.tier_service import (
    get_quiz_questions_limit,
    get_remaining_quota,
    check_and_consume_quota,
    get_answer_review_level,
    get_explanation_level,
    get_current_user_tier,
    get_feature_level,
    get_user_tier,
    get_allowed_question_counts,
    validate_question_count,
    is_custom_question_count_allowed
)
from services.achievement_service import check_and_award_achievements
from services.settings_service import SettingsService
from history_logger import add_history_entry
from question_utils import UI_GRADES, grade_label

logger = logging.getLogger(__name__)

quiz_bp = Blueprint('quiz', __name__, url_prefix='/quiz')


# ============================================
# HELPERS
# ============================================

def _empty_reactions():
    return {'likes': [], 'saves': [], 'reports': {}}


def _flush_reactions_safe(user_id, reactions):
    try:
        from services.interaction_service import flush_quiz_reactions
        flush_quiz_reactions(
            user_id,
            reactions.get('likes') or [],
            reactions.get('saves') or [],
        )
    except Exception as e:
        logger.error(f"_flush_reactions_safe failed for user {user_id}: {e}", exc_info=True)


def _profile_grade(user_id):
    """
    Return the student's grade clamped to the user-facing set (F4 / F3).
    Anything else (G8, G7, empty, malformed) becomes F4.
    """
    user = get_student_by_id(user_id) or {}
    g = (user.get('grade') or '').strip().upper()
    return g if g in UI_GRADES else UI_GRADES[0]


def _fetch_question(qid):
    """Fetch one question by id. Returns None if it no longer exists."""
    fetched = get_questions_by_ids([qid])
    return fetched[0] if fetched else None


# ============================================
# SETUP / START
# ============================================

@quiz_bp.route('/')
def index():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    quiz_data = session.get('quiz')
    if quiz_data and quiz_data.get('question_ids'):
        flash('Resuming your quiz...', 'info')
        return redirect(url_for('quiz.play'))

    user_id = session['user_id']
    subjects = get_user_subject_list(user_id)
    if not subjects:
        flash('Please set your location and curriculum in your profile to access quizzes.',
              'error')
        return redirect(url_for('dashboard.profile'))

    settings = session.get('settings', {})
    default_subject = settings.get('quiz.default_subject', '')
    default_question_count = settings.get('quiz.default_question_count', 10)
    default_difficulty = settings.get('quiz.default_difficulty', 1)

    allowed_counts = get_allowed_question_counts(user_id)
    tier = get_current_user_tier()
    remaining_attempts = get_remaining_quota(user_id, 'quiz_attempt')

    user_grade = _profile_grade(user_id)
    can_change_grade = tier in ('premium', 'pro')
    grade_choices = [
        {'code': g, 'label': grade_label(g)} for g in UI_GRADES
    ]

    return render_template(
        'dashboard/quiz/setup.html',
        subjects=subjects,
        allowed_counts=allowed_counts,
        tier=tier,
        remaining_attempts=remaining_attempts,
        is_custom_allowed=is_custom_question_count_allowed(user_id),
        default_subject=default_subject,
        default_question_count=default_question_count,
        default_difficulty=default_difficulty,
        user_grade=user_grade,
        can_change_grade=can_change_grade,
        grade_choices=grade_choices,
    )


@quiz_bp.route('/start', methods=['POST'])
def start_quiz():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    if not validate_csrf():
        flash('Invalid CSRF token. Please try again.', 'error')
        return redirect(url_for('quiz.index'))

    user_id = session['user_id']
    subject_code = request.form.get('subject_code', '').strip()
    question_count_str = request.form.get('question_count', '').strip()
    custom_count_str = request.form.get('custom_count', '').strip()

    user_subjects = get_user_subject_list(user_id)
    if subject_code not in [s['code'] for s in user_subjects]:
        flash('Invalid subject selected.', 'error')
        return redirect(url_for('quiz.index'))

    if question_count_str == 'custom' and custom_count_str:
        try:
            question_count = int(custom_count_str)
        except ValueError:
            flash('Please enter a valid number.', 'error')
            return redirect(url_for('quiz.index'))
    else:
        try:
            question_count = int(question_count_str)
        except ValueError:
            flash('Invalid question count.', 'error')
            return redirect(url_for('quiz.index'))

    if not validate_question_count(user_id, question_count):
        flash('Question count not allowed for your tier.', 'error')
        return redirect(url_for('quiz.index'))

    remaining = get_remaining_quota(user_id, 'quiz_attempt')
    if remaining <= 0:
        flash('You have used all your quiz attempts for today. Come back tomorrow!', 'error')
        return redirect(url_for('quiz.index'))

    # ── Grade resolution ─────────────────────────────────
    # The form only ever emits F4 / F3 (UI_GRADES). Anything else is
    # rejected and the profile grade is used instead.
    profile_grade = _profile_grade(user_id)
    form_grade = (request.form.get('grade') or '').strip().upper()
    tier = get_current_user_tier()

    if tier in ('premium', 'pro') and form_grade in UI_GRADES:
        effective_grade = form_grade
    else:
        effective_grade = profile_grade

    questions = get_questions_by_subject(subject_code, question_count,
                                         grade=effective_grade)
    if not questions:
        flash('No questions available for this subject yet.', 'error')
        return redirect(url_for('quiz.index'))

    if not check_and_consume_quota(user_id, 'quiz_attempt'):
        flash('Failed to start quiz. Try again.', 'error')
        return redirect(url_for('quiz.index'))

    session['quiz'] = {
        'subject_code': subject_code,
        'grade': effective_grade,
        'question_ids': [q['id'] for q in questions],
        'current_index': 0,
        'score': 0,
        'answers': [],
        'ratings': [],
        'reactions': _empty_reactions(),
    }
    session.modified = True

    return redirect(url_for('quiz.play'))


# ============================================
# PLAY
# ============================================

@quiz_bp.route('/play')
def play():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    quiz_data = session.get('quiz')
    if not quiz_data or not quiz_data.get('question_ids'):
        flash('No quiz in progress. Start a new quiz.', 'error')
        return redirect(url_for('quiz.index'))

    if 'reactions' not in quiz_data:
        quiz_data['reactions'] = _empty_reactions()
        session['quiz'] = quiz_data
        session.modified = True

    question_ids = quiz_data['question_ids']
    current_index = quiz_data['current_index']
    if current_index >= len(question_ids):
        return redirect(url_for('quiz.results'))

    qid = question_ids[current_index]
    question = _fetch_question(qid)
    if not question:
        # Question was archived or deleted mid-quiz — advance past it
        quiz_data['current_index'] = current_index + 1
        session['quiz'] = quiz_data
        session.modified = True
        if quiz_data['current_index'] >= len(question_ids):
            return redirect(url_for('quiz.results'))
        return redirect(url_for('quiz.play'))

    total = len(question_ids)
    score = quiz_data['score']

    user_id = session['user_id']
    settings = session.get('settings', {})
    auto_skip_enabled = settings.get('quiz.auto_skip_enabled', False)
    show_correct_immediately = settings.get('quiz.show_correct_immediately', True)

    return render_template('dashboard/quiz/play.html',
                           question=question,
                           current=current_index,
                           total=total,
                           score=score,
                           user_settings={'auto_skip_enabled': auto_skip_enabled,
                                          'show_correct_immediately': show_correct_immediately},
                           user_tier=get_user_tier(user_id))


# ============================================
# SUBMIT ANSWER
# ============================================

@quiz_bp.route('/submit_answer', methods=['POST'])
def submit_answer():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    quiz_data = session.get('quiz')
    if not quiz_data or not quiz_data.get('question_ids'):
        return jsonify({'error': 'No quiz in progress'}), 400

    question_ids = quiz_data['question_ids']
    current_index = quiz_data['current_index']
    if current_index >= len(question_ids):
        return jsonify({'error': 'Quiz already completed'}), 400

    qid = question_ids[current_index]
    question = _fetch_question(qid)
    if not question:
        return jsonify({'error': 'Question no longer available'}), 400

    answer = (request.json or {}).get('answer', '')
    is_correct = answer == question['correct_answer']

    answers = quiz_data['answers']
    answers.append({
        'question_id': question['id'],
        'answer': answer,
        'correct': is_correct
    })
    quiz_data['answers'] = answers

    if is_correct:
        quiz_data['score'] += 1

    session['quiz'] = quiz_data
    session.modified = True

    settings = session.get('settings', {})
    show_correct_immediately = settings.get('quiz.show_correct_immediately', True)

    response = {
        'correct': is_correct,
        'correct_answer': question['correct_answer'],
        'current': current_index,
        'total': len(question_ids),
        'score': quiz_data['score']
    }

    if show_correct_immediately:
        response['feedback'] = is_correct
        response['explanation'] = question.get('explanation', '')
    else:
        response['feedback'] = None
        response['explanation'] = None

    return jsonify(response)


# ============================================
# ADVANCE (was skip_rating)
# ============================================

@quiz_bp.route('/skip_rating', methods=['POST'])
def skip_rating():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    quiz_data = session.get('quiz')
    if not quiz_data or not quiz_data.get('question_ids'):
        return jsonify({'error': 'No quiz in progress'}), 400

    question_ids = quiz_data['question_ids']
    current_index = quiz_data['current_index']
    if current_index >= len(question_ids):
        return jsonify({'error': 'Quiz already completed'}), 400

    quiz_data['current_index'] += 1
    session['quiz'] = quiz_data
    session.modified = True

    if quiz_data['current_index'] >= len(question_ids):
        user_id = session['user_id']
        score = quiz_data['score']
        total = len(question_ids)
        check_and_award_achievements(user_id, 'quiz_completed', {'score': score, 'total': total})
        return jsonify({'complete': True})

    return jsonify({'complete': False, 'next': quiz_data['current_index']})


# ============================================
# END QUIZ
# ============================================

@quiz_bp.route('/end', methods=['POST'])
def end_quiz():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    quiz_data = session.get('quiz')
    if not quiz_data or not quiz_data.get('question_ids'):
        return jsonify({'error': 'No quiz in progress'}), 400

    user_id = session['user_id']
    question_ids = quiz_data['question_ids']
    answers = quiz_data.get('answers', [])
    score = quiz_data.get('score', 0)
    total = len(question_ids)
    subject_code = quiz_data['subject_code']
    reactions = quiz_data.get('reactions') or _empty_reactions()

    try:
        save_quiz_attempt(
            user_id, subject_code, score, total, answers,
            [], reactions, ended_early=True,
        )
    except Exception as e:
        logger.error(f"end_quiz: save_quiz_attempt failed for user {user_id}: {e}", exc_info=True)

    _flush_reactions_safe(user_id, reactions)

    try:
        student = get_student_by_id(user_id)
        if student:
            current_points = student.get('total_points', 0) or 0
            update_student_points(user_id, current_points + score)
    except Exception as e:
        logger.error(f"end_quiz: update points failed: {e}")

    try:
        add_history_entry(
            user_id=user_id, entry_type='quiz_attempt', action='completed',
            metadata={
                'subject': subject_code,
                'score': score,
                'total': total,
                'percentage': round((score / total) * 100, 1) if total > 0 else 0,
                'ended_early': True,
            },
        )
    except Exception as e:
        logger.error(f"end_quiz: history entry failed: {e}")

    try:
        check_and_award_achievements(user_id, 'quiz_completed', {'score': score, 'total': total})
    except Exception:
        pass

    session.pop('quiz', None)
    session.modified = True

    return jsonify({
        'success': True,
        'redirect': url_for('quiz.index'),
        'score': score,
        'total': total,
    })


# ============================================
# RESULTS
# ============================================

@quiz_bp.route('/results')
def results():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    quiz_data = session.pop('quiz', None)
    if not quiz_data or not quiz_data.get('question_ids'):
        flash('No quiz completed.', 'error')
        return redirect(url_for('quiz.index'))

    question_ids = quiz_data['question_ids']
    fetched = get_questions_by_ids(question_ids)
    by_id = {q['id']: q for q in fetched}
    # Preserve original order; skip any questions that no longer exist
    questions = [by_id[qid] for qid in question_ids if qid in by_id]

    answers = quiz_data['answers']
    score = quiz_data['score']
    total = len(question_ids)
    subject_code = quiz_data['subject_code']
    ratings = quiz_data.get('ratings', [])
    reactions = quiz_data.get('reactions') or _empty_reactions()

    user_id = session['user_id']

    save_quiz_attempt(
        user_id, subject_code, score, total, answers,
        ratings, reactions, ended_early=False,
    )

    _flush_reactions_safe(user_id, reactions)

    add_history_entry(
        user_id=user_id, entry_type='quiz_attempt', action='completed',
        metadata={
            'subject': subject_code,
            'score': score,
            'total': total,
            'percentage': round((score / total) * 100, 1) if total > 0 else 0,
        }
    )

    student = get_student_by_id(user_id)
    if student:
        current_points = student.get('total_points', 0) or 0
        update_student_points(user_id, current_points + score)

    return render_template('dashboard/quiz/results.html',
                           score=score, total=total,
                           percentage=round((score / total) * 100) if total > 0 else 0,
                           answers=answers, ratings=ratings,
                           reactions=reactions, questions=questions)


# ============================================
# HISTORY / LEADERBOARD
# ============================================

@quiz_bp.route('/history')
def history():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    attempts = get_user_quiz_history(session['user_id'], 20)
    return render_template('dashboard/quiz/history.html', attempts=attempts)


@quiz_bp.route('/leaderboard')
def leaderboard():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    query = """
        SELECT
            CASE
                WHEN json_extract(us.settings, '$."privacy.show_public_id"') = 0
                    THEN '----'
                ELSE s.public_id
            END AS public_id,
            s.first_name, s.middle_name, s.last_name, s.total_points, s.school
        FROM students s
        LEFT JOIN user_settings us ON s.id = us.user_id
        WHERE (
            us.settings IS NULL
            OR json_extract(us.settings, '$."privacy.show_on_leaderboard"') IS NULL
            OR json_extract(us.settings, '$."privacy.show_on_leaderboard"') = 1
        )
        ORDER BY s.total_points DESC
        LIMIT 50
    """
    cursor = execute_with_retry(query)
    leaders = [dict(row) for row in cursor.fetchall()]

    user_rank = None
    cursor = execute_with_retry(
        "SELECT id FROM students WHERE id = ?",
        (session['user_id'],)
    )
    row = cursor.fetchone()
    if row:
        rank_cursor = execute_with_retry("""
            SELECT COUNT(*) AS c
            FROM students s
            LEFT JOIN user_settings us ON s.id = us.user_id
            WHERE (
                us.settings IS NULL
                OR json_extract(us.settings, '$."privacy.show_on_leaderboard"') IS NULL
                OR json_extract(us.settings, '$."privacy.show_on_leaderboard"') = 1
            )
              AND s.total_points > (
                SELECT total_points FROM students WHERE id = ?
              )
        """, (session['user_id'],))
        rank_row = rank_cursor.fetchone()
        if rank_row:
            user_rank = rank_row['c'] + 1

    level = get_feature_level("detailed_ranking_stats", user_id=session['user_id'])

    return render_template('dashboard/quiz/leaderboard.html',
                           leaders=leaders, user_rank=user_rank,
                           ranking_level=level)