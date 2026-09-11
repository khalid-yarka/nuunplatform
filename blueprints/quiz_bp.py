# blueprints/quiz_bp.py
# Complete updated file with fixed leaderboard

import json
from flask import Blueprint, render_template, request, session, flash, redirect, url_for, jsonify
from db import (
    get_questions_by_subject, save_quiz_attempt,
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

quiz_bp = Blueprint('quiz', __name__, url_prefix='/quiz')


@quiz_bp.route('/')
def index():
    """Unified setup page: choose subject and question count."""
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    quiz_data = session.get('quiz')
    if quiz_data and quiz_data.get('questions'):
        flash('Resuming your quiz...', 'info')
        return redirect(url_for('quiz.play'))

    user_id = session['user_id']
    subjects = get_user_subject_list(user_id)
    if not subjects:
        flash('Please set your location and curriculum in your profile to access quizzes.', 'error')
        return redirect(url_for('dashboard.profile'))

    # Load user settings
    settings = session.get('settings', {})
    default_subject = settings.get('quiz.default_subject', '')
    default_question_count = settings.get('quiz.default_question_count', 10)
    default_difficulty = settings.get('quiz.default_difficulty', 1)

    allowed_counts = get_allowed_question_counts(user_id)
    tier = get_current_user_tier()
    remaining_attempts = get_remaining_quota(user_id, 'quiz_attempt')

    return render_template('dashboard/quiz/setup.html',
                           subjects=subjects,
                           allowed_counts=allowed_counts,
                           tier=tier,
                           remaining_attempts=remaining_attempts,
                           is_custom_allowed=is_custom_question_count_allowed(user_id),
                           default_subject=default_subject,
                           default_question_count=default_question_count,
                           default_difficulty=default_difficulty)


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

    questions = get_questions_by_subject(subject_code, question_count)
    if not questions:
        flash('No questions available for this subject yet.', 'error')
        return redirect(url_for('quiz.index'))

    if not check_and_consume_quota(user_id, 'quiz_attempt'):
        flash('Failed to start quiz. Try again.', 'error')
        return redirect(url_for('quiz.index'))

    session['quiz'] = {
        'subject_code': subject_code,
        'question_count': len(questions),
        'questions': questions,
        'current_index': 0,
        'score': 0,
        'answers': [],
        'ratings': [],
        'reactions': {
            'likes': [],
            'saves': [],
            'reports': {}
        }
    }

    return redirect(url_for('quiz.play'))


@quiz_bp.route('/play')
def play():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    quiz_data = session.get('quiz')
    if not quiz_data or not quiz_data.get('questions'):
        flash('No quiz in progress. Start a new quiz.', 'error')
        return redirect(url_for('quiz.index'))

    questions = quiz_data['questions']
    current_index = quiz_data['current_index']
    if current_index >= len(questions):
        return redirect(url_for('quiz.results'))

    question = questions[current_index]
    total = len(questions)
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
                           user_settings={'auto_skip_enabled': auto_skip_enabled, 'show_correct_immediately': show_correct_immediately},
                           user_tier=get_user_tier(user_id))


@quiz_bp.route('/submit_answer', methods=['POST'])
def submit_answer():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    quiz_data = session.get('quiz')
    if not quiz_data:
        return jsonify({'error': 'No quiz in progress'}), 400

    questions = quiz_data['questions']
    current_index = quiz_data['current_index']
    if current_index >= len(questions):
        return jsonify({'error': 'Quiz already completed'}), 400

    answer = request.json.get('answer', '')
    question = questions[current_index]
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

    user_id = session['user_id']
    settings = session.get('settings', {})
    show_correct_immediately = settings.get('quiz.show_correct_immediately', True)

    response = {
        'correct': is_correct,
        'correct_answer': question['correct_answer'],
        'current': current_index,
        'total': len(questions),
        'score': quiz_data['score']
    }

    if show_correct_immediately:
        response['feedback'] = is_correct
        response['explanation'] = question.get('explanation', '')
    else:
        response['feedback'] = None
        response['explanation'] = None

    return jsonify(response)


@quiz_bp.route('/submit_rating', methods=['POST'])
def submit_rating():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    quiz_data = session.get('quiz')
    if not quiz_data:
        return jsonify({'error': 'No quiz in progress'}), 400

    questions = quiz_data['questions']
    current_index = quiz_data['current_index']
    if current_index >= len(questions):
        return jsonify({'error': 'Quiz already completed'}), 400

    rating = request.json.get('rating', '')
    ratings = quiz_data['ratings']
    ratings.append({
        'question_id': questions[current_index]['id'],
        'rating': rating
    })
    quiz_data['ratings'] = ratings
    quiz_data['current_index'] += 1
    session['quiz'] = quiz_data
    session.modified = True

    if quiz_data['current_index'] >= len(questions):
        user_id = session['user_id']
        score = quiz_data['score']
        total = len(questions)
        check_and_award_achievements(user_id, 'quiz_completed', {'score': score, 'total': total})
        return jsonify({'complete': True})

    return jsonify({'complete': False, 'next': quiz_data['current_index']})


@quiz_bp.route('/skip_rating', methods=['POST'])
def skip_rating():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    quiz_data = session.get('quiz')
    if not quiz_data:
        return jsonify({'error': 'No quiz in progress'}), 400

    questions = quiz_data['questions']
    current_index = quiz_data['current_index']
    if current_index >= len(questions):
        return jsonify({'error': 'Quiz already completed'}), 400

    quiz_data['current_index'] += 1
    session['quiz'] = quiz_data
    session.modified = True

    if quiz_data['current_index'] >= len(questions):
        user_id = session['user_id']
        score = quiz_data['score']
        total = len(questions)
        check_and_award_achievements(user_id, 'quiz_completed', {'score': score, 'total': total})
        return jsonify({'complete': True})

    return jsonify({'complete': False, 'next': quiz_data['current_index']})


@quiz_bp.route('/results')
def results():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    quiz_data = session.pop('quiz', None)
    if not quiz_data or not quiz_data.get('questions'):
        flash('No quiz completed.', 'error')
        return redirect(url_for('quiz.index'))

    questions = quiz_data['questions']
    answers = quiz_data['answers']
    score = quiz_data['score']
    total = len(questions)
    subject_code = quiz_data['subject_code']
    ratings = quiz_data['ratings']
    reactions = quiz_data['reactions']

    save_quiz_attempt(
        session['user_id'],
        subject_code,
        score,
        total,
        answers,
        ratings,
        reactions
    )

    add_history_entry(
        user_id=session['user_id'],
        entry_type='quiz_attempt',
        action='completed',
        metadata={
            'subject': subject_code,
            'score': score,
            'total': total,
            'percentage': round((score/total)*100, 1) if total > 0 else 0
        }
    )

    student = get_student_by_id(session['user_id'])
    if student:
        current_points = student.get('total_points', 0)
        new_points = current_points + score
        update_student_points(session['user_id'], new_points)

    return render_template('dashboard/quiz/results.html',
                         score=score,
                         total=total,
                         percentage=round((score/total)*100) if total > 0 else 0,
                         answers=answers,
                         ratings=ratings,
                         reactions=reactions,
                         questions=questions)


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

    # Privacy filter:
    #   show_on_leaderboard=0 -> user excluded entirely
    #   show_public_id=0     -> user included but public_id blanked
    query = """
        SELECT
            CASE
                WHEN json_extract(us.settings, '$.privacy.show_public_id') = 0
                    THEN '----'
                ELSE s.public_id
            END AS public_id,
            s.first_name, s.middle_name, s.last_name, s.total_points, s.school
        FROM students s
        LEFT JOIN user_settings us ON s.id = us.user_id
        WHERE (
            us.settings IS NULL
            OR json_extract(us.settings, '$.privacy.show_on_leaderboard') IS NULL
            OR json_extract(us.settings, '$.privacy.show_on_leaderboard') = 1
        )
        ORDER BY s.total_points DESC
        LIMIT 50
    """
    cursor = execute_with_retry(query)
    leaders = [dict(row) for row in cursor.fetchall()]

    user_rank = None
    # NOTE: don't compare by public_id here anymore — it may be masked.
    # Use the logged-in user's id to find their rank.
    cursor = execute_with_retry(
        "SELECT id FROM students WHERE id = ?",
        (session['user_id'],)
    )
    row = cursor.fetchone()
    if row:
        # Count how many visible users have more points than the current user.
        rank_cursor = execute_with_retry("""
            SELECT COUNT(*) AS c
            FROM students s
            LEFT JOIN user_settings us ON s.id = us.user_id
            WHERE (
                us.settings IS NULL
                OR json_extract(us.settings, '$.privacy.show_on_leaderboard') IS NULL
                OR json_extract(us.settings, '$.privacy.show_on_leaderboard') = 1
            )
              AND s.total_points > (
                SELECT total_points FROM students WHERE id = ?
              )
        """, (session['user_id'],))
        rank_row = rank_cursor.fetchone()
        if rank_row:
            user_rank = rank_row['c'] + 1

    level = get_feature_level("detailed_ranking_stats", session['user_id'])

    return render_template('dashboard/quiz/leaderboard.html',
                         leaders=leaders,
                         user_rank=user_rank,
                         ranking_level=level)