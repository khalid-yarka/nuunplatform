# blueprints/live_quiz_bp.py
# Live quiz lifecycle — grade-aware, with join-grade gating for free users.

import json
import random
import threading
import logging
import time
from datetime import datetime, timezone, timedelta
from flask import (
    Blueprint, render_template, request, session, flash, redirect,
    url_for, jsonify, Response,
)
from functools import wraps
from io import StringIO
import csv
import os

from db import (
    get_questions_by_subject,
    get_question_by_id,
    get_live_quiz_by_id,
    get_live_quiz_by_code,
    get_live_quiz_with_subject,
    get_live_quiz_participant,
    get_live_quiz_participants,
    get_live_quiz_participants_with_names,
    get_live_quiz_count,
    get_live_quiz_completed_count,
    get_question_ids_for_quiz,
    get_questions_by_ids,
    get_live_quiz_creator_id,
    get_active_live_quiz,
    get_student_by_id,
    get_live_quizzes_lobby,
    get_live_quiz_stats,
    can_join_live_quiz,
    leave_live_quiz as db_leave_live_quiz,
    rejoin_live_quiz as db_rejoin_live_quiz,
    get_active_participants,
    delete_live_quiz as db_delete_live_quiz,
    get_user_active_quiz,
    is_admin,
    create_live_quiz,
    update_live_quiz,
    add_live_quiz_participant,
    update_live_quiz_participant,
    update_participant_ready,
    get_user_subject_list,
    create_live_quiz_with_participant,
    generate_unique_join_code,
    execute_with_retry,
)

from config import Config
from utils import (
    get_somali_time_db,
    get_somali_time_display,
    format_somali_time,
    validate_csrf,
    ensure_csrf_token,
)
from subjects_config import get_subject, get_all_subjects
from services.tier_service import (
    can_create_live_quiz,
    can_schedule_live_quiz,
    can_create_private_live_quiz,
    has_feature,
    get_current_user_tier,
    get_saved_content_limit,
)
from services.notification_service import send_notification
from question_utils import (
    VALID_GRADES, UI_GRADES, DEFAULT_GRADE, grade_label, normalize_grade,
)

from live_quiz_state import get_live_quiz_state_manager
from cache import get_cache_manager, InvalidationHelper, make_key
from history_logger import add_history_entry

logger = logging.getLogger(__name__)

live_quiz_bp = Blueprint('live_quiz', __name__, url_prefix='/live-quiz')

MAX_PARTICIPANTS = Config.LIVE_QUIZ_MAX_PARTICIPANTS
TIME_PER_QUESTION = Config.LIVE_QUIZ_TIME_PER_QUESTION
RATING_TIME = Config.RATING_TIME

CACHE_TTL = getattr(Config, 'CACHE_TTL', {}).get('quiz', {})
QUIZ_STATE_TTL = CACHE_TTL.get('state', 60)


# ============================================
# SINGLE-WORKER SAFETY
# ============================================

def check_single_worker():
    try:
        if 'GUNICORN_WORKER' in os.environ:
            logger.warning(
                "Multiple Gunicorn workers detected. Live Quiz state "
                "is per-process and will be inconsistent across workers. "
                "Please set --workers=1."
            )
    except Exception:
        pass


check_single_worker()


# ============================================
# SHARED HELPERS
# ============================================

def get_state_manager():
    return get_live_quiz_state_manager()


def invalidate_quiz_cache(quiz_id: int):
    try:
        InvalidationHelper.invalidate_quiz(quiz_id)
        cache = get_cache_manager()
        cache.invalidate_pattern(f"quiz:*:{quiz_id}:*")
    except Exception:
        pass


def get_questions_for_subject(subject_code, limit, grade=None):
    questions = get_questions_by_subject(subject_code, limit, grade=grade)
    return questions, len(questions)


def _profile_grade(user_id):
    """
    Return the user-facing grade (F4 / F3). Anything else — G8, G7,
    empty, malformed — is treated as F4.
    """
    user = get_student_by_id(user_id) or {}
    g = (user.get('grade') or '').strip().upper()
    return g if g in UI_GRADES else UI_GRADES[0]


def _grade_choices():
    """User-facing pill choices: F4 and F3 only."""
    return [{'code': g, 'label': grade_label(g)} for g in UI_GRADES]


def _is_grade_locked_for_viewer(quiz_row, user_id, user_tier):
    """
    True when the viewer is free and the quiz grade doesn't match their
    profile grade. Premium/pro always get False.
    """
    if user_tier in ('premium', 'pro'):
        return False
    quiz_grade = normalize_grade(quiz_row.get('grade') if quiz_row else None)
    # normalize_grade defaults to F4 for anything not in VALID_GRADES;
    # compare against the user's UI grade so G8-stored rows align with F4.
    if quiz_grade not in UI_GRADES:
        quiz_grade = UI_GRADES[0]
    return quiz_grade != _profile_grade(user_id)


def _enrich_active_quiz(active_quiz: dict, user_id: int) -> dict:
    if not active_quiz:
        return active_quiz
    active_quiz['is_creator'] = (active_quiz.get('creator_id') == user_id)
    subj = get_subject(active_quiz.get('subject_code'))
    active_quiz['subject_name'] = subj['name'] if subj else active_quiz.get('subject_code')
    active_quiz['subject_icon'] = subj.get('icon', '📚') if subj else '📚'
    if not active_quiz.get('grade'):
        active_quiz['grade'] = DEFAULT_GRADE
    return active_quiz


def _get_active_quiz_for_user(user_id: int):
    active_id = get_user_active_quiz(user_id)
    if not active_id:
        return None
    quiz = get_live_quiz_by_id(active_id)
    if not quiz:
        return None
    return _enrich_active_quiz(quiz, user_id)


def finalize_live_quiz(quiz_id: int) -> dict:
    manager = get_state_manager()
    quiz_state = manager.get_quiz(quiz_id)
    if not quiz_state:
        return {'error': 'Quiz not in memory'}

    final_data = quiz_state.finalize()
    if 'error' in final_data:
        return final_data

    try:
        update_live_quiz(quiz_id, {
            'status': 'finished',
            'ended_at': final_data['ended_at'],
        })

        for pdata in final_data['participants']:
            db_p = get_live_quiz_participant(quiz_id, pdata['user_id'])
            if db_p:
                update_live_quiz_participant(db_p['id'], {
                    'score': pdata['score'],
                    'correct_count': pdata['correct_count'],
                    'wrong_count': pdata['wrong_count'],
                    'skipped_count': pdata['skipped_count'],
                    'answers': pdata['answers'],
                    'ratings': pdata['ratings'],
                    'ranking': pdata['rank'],
                    'status': pdata['status'],
                })

            try:
                from services.interaction_service import flush_quiz_reactions
                flush_quiz_reactions(
                    pdata['user_id'],
                    pdata.get('likes') or [],
                    pdata.get('saves') or [],
                )
            except Exception as e:
                logger.error(
                    f"flush_quiz_reactions failed for user {pdata['user_id']} "
                    f"in quiz {quiz_id}: {e}"
                )

            add_history_entry(
                user_id=pdata['user_id'],
                entry_type='live_quiz',
                action='completed',
                metadata={
                    'subject': quiz_state.metadata.get('subject_code', 'Unknown'),
                    'grade': quiz_state.metadata.get('grade', DEFAULT_GRADE),
                    'score': pdata['score'],
                    'rank': pdata.get('rank'),
                    'total_questions': len(quiz_state.question_ids),
                },
            )

        manager.enqueue_event({
            'quiz_id': quiz_id,
            'event_type': 'COMPLETE',
            'payload': json.dumps(final_data),
        })

        quiz_state.mark_finalized()

        participants = quiz_state.get_all_participants()
        for p in participants:
            send_notification(
                user_id=p['student_id'],
                notification_type='live_quiz_result',
                title='🏆 Quiz Complete!',
                body=(
                    f'"{quiz_state.metadata.get("title", "Quiz")}" finished. '
                    f'Your rank: #{p.get("rank", "N/A")}, '
                    f'Score: {p.get("score", 0)}'
                ),
                link=f'/live-quiz/results/{quiz_id}',
                icon='🏅',
            )

        invalidate_quiz_cache(quiz_id)
        logger.info(f"Finalized quiz {quiz_id}")
        return {'success': True, 'final_data': final_data}
    except Exception as e:
        logger.error(f"Finalization error for quiz {quiz_id}: {e}", exc_info=True)
        return {'error': str(e)}


# ============================================
# ROUTES
# ============================================

@live_quiz_bp.route('/')
def index():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))
    return redirect(url_for('live_quiz.lobby'))


@live_quiz_bp.route('/lobby')
def lobby():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    user_tier = get_current_user_tier()

    status_filter = request.args.get('status', '')
    subject_filter = request.args.get('subject', '')
    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = 20

    subject_code = None
    if subject_filter:
        all_subjects = get_all_subjects()
        if any(s['code'] == subject_filter for s in all_subjects):
            subject_code = subject_filter

    cache = get_cache_manager()
    subjects_key = make_key('subject', 'list', 'all')
    subjects = cache.get(subjects_key)
    if subjects is None:
        subjects = get_all_subjects()
        cache.set(subjects_key, subjects, ttl=3600)

    quizzes, total = get_live_quizzes_lobby(
        user_id=user_id,
        status_filter=status_filter if status_filter else None,
        subject_filter=subject_code,
        search=search if search else None,
        page=page,
        per_page=per_page,
    )

    # Grade-lock enrichment: per-viewer flag on each quiz.
    for q in quizzes:
        q['grade'] = q.get('grade') or DEFAULT_GRADE
        q['grade_locked'] = _is_grade_locked_for_viewer(q, user_id, user_tier)

    stats_key = make_key('quiz', 'stats', 'global')
    stats = cache.get(stats_key)
    if stats is None:
        stats = get_live_quiz_stats()
        cache.set(stats_key, stats, ttl=30)

    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    can_create = can_create_live_quiz()

    return render_template(
        'dashboard/live_quiz/lobby.html',
        quizzes=quizzes,
        stats=stats,
        subjects=subjects,
        status_filter=status_filter,
        subject_filter=subject_filter,
        search=search,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        user_tier=user_tier,
        can_create=can_create,
    )


@live_quiz_bp.route('/lobby/join/<quiz_id>', methods=['POST'])
def lobby_join(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Please login first'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    # ── Grade gate (only blocks free users with a mismatched profile grade)
    if _is_grade_locked_for_viewer(quiz, user_id, get_current_user_tier()):
        return jsonify({
            'error': 'This quiz is for a different grade. '
                     'Upgrade to join quizzes of any grade.',
            'reason': 'grade_mismatch',
            'required_tier': 'premium',
            'required_grade': normalize_grade(quiz.get('grade')),
        }), 403

    can_join, reason = can_join_live_quiz(quiz_id, user_id)
    if not can_join:
        return jsonify({'error': reason}), 400

    success = add_live_quiz_participant(quiz_id, user_id)
    if not success:
        return jsonify({'error': 'Failed to join quiz'}), 500

    manager = get_state_manager()
    if not manager.ensure_quiz_in_memory(quiz_id):
        logger.warning(f"Quiz {quiz_id} not in memory after join; attempting to recover again.")
        if not manager.ensure_quiz_in_memory(quiz_id):
            return jsonify({'error': 'Quiz state could not be loaded'}), 500

    quiz_state = manager.get_quiz(quiz_id)
    if quiz_state:
        user = get_student_by_id(user_id)
        name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'Participant'
        quiz_state.add_participant(user_id, name, user.get('public_id', '----'))
        manager.enqueue_event({
            'quiz_id': quiz_id,
            'user_id': user_id,
            'event_type': 'JOIN',
            'payload': json.dumps({'name': name}),
        })
    else:
        return jsonify({'error': 'Quiz state not available'}), 500

    creator_id = quiz.get('creator_id')
    if creator_id and creator_id != user_id:
        user = get_student_by_id(user_id)
        user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'Participant'
        try:
            send_notification(
                user_id=creator_id,
                notification_type='participant_joined',
                title='👋 New Participant!',
                body=f'{user_name} joined your quiz "{quiz.get("title", "Live Quiz")}"',
                link=f'/live-quiz/waiting-room/{quiz_id}',
                icon='👤',
            )
        except Exception as e:
            logger.warning(f"participant_joined notification failed: {e}")

    return jsonify({
        'success': True,
        'redirect': url_for('live_quiz.waiting_room', quiz_id=quiz_id),
    })


@live_quiz_bp.route('/available-count')
def available_count():
    """
    AJAX: how many active questions exist for a subject + grade.
    Any logged-in user can call this — it powers the qualitative hint
    on both the quiz setup page and the live quiz create page.
    """
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    subject_code = (request.args.get('subject') or '').strip()
    grade = (request.args.get('grade') or '').strip().upper()
    if grade not in VALID_GRADES:
        grade = DEFAULT_GRADE

    if not subject_code:
        return jsonify({'count': 0, 'grade': grade})

    try:
        cursor = execute_with_retry(
            "SELECT COUNT(*) AS c FROM questions "
            "WHERE subject_code = ? AND status = 'active' AND grade = ?",
            (subject_code, grade),
        )
        row = cursor.fetchone()
        count = int(row['c']) if row else 0
        return jsonify({'count': count, 'grade': grade, 'subject': subject_code})
    except Exception as e:
        logger.warning(f"available_count failed: {e}")
        return jsonify({'count': 0, 'grade': grade, 'subject': subject_code})


@live_quiz_bp.route('/create', methods=['GET', 'POST'])
def create():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    user_subjects = get_user_subject_list(user_id)

    if not user_subjects:
        flash(
            'You need to set your location and curriculum in your profile '
            'before creating a quiz.',
            'error',
        )
        return redirect(url_for('dashboard.profile'))

    if not can_create_live_quiz():
        flash('Upgrade to Premium or Pro to create live quizzes.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    settings = session.get('settings', {})
    default_time = settings.get('live_quiz.default_time_per_question', 30)
    default_max_participants = settings.get('live_quiz.default_max_participants', 50)
    default_privacy = settings.get('live_quiz.default_privacy', 1)

    user_grade = _profile_grade(user_id)
    grades = _grade_choices()

    active_quiz = _get_active_quiz_for_user(user_id)

    if request.method == 'GET':
        ensure_csrf_token()
        return render_template(
            'dashboard/live_quiz/create.html',
            subjects=user_subjects,
            user_grade=user_grade,
            grades=grades,
            default_time=default_time,
            default_max_participants=default_max_participants,
            default_privacy=default_privacy,
            active_quiz=active_quiz,
        )

    if not validate_csrf():
        flash('Invalid CSRF token. Please try again.', 'error')
        return redirect(url_for('live_quiz.create'))

    if active_quiz:
        role = 'creator' if active_quiz.get('is_creator') else 'participant'
        flash(
            f'You are already in an unfinished quiz as a {role}. '
            f'Please finish, leave, or abandon it before creating a new one.',
            'error',
        )
        return redirect(url_for('live_quiz.create'))

    # ── Grade ───────────────────────────────────────────
    form_grade = (request.form.get('grade') or '').strip().upper()
    if form_grade not in UI_GRADES:
        form_grade = user_grade
    effective_grade = form_grade

    def _render_error(**extra):
        return render_template(
            'dashboard/live_quiz/create.html',
            subjects=user_subjects,
            user_grade=effective_grade,
            grades=grades,
            default_time=default_time,
            default_max_participants=default_max_participants,
            default_privacy=default_privacy,
            active_quiz=None,
            **extra,
        )

    subject_code = request.form.get('subject_code', '').strip()
    allowed_codes = [s['code'] for s in user_subjects]
    if subject_code not in allowed_codes:
        flash('Subject not available for your location/curriculum.', 'error')
        return _render_error()

    try:
        question_count = int(request.form.get('question_count', 10))
    except ValueError:
        question_count = 10
    if question_count < 5 or question_count > 30:
        flash('Number of questions must be between 5 and 30.', 'error')
        return _render_error(
            subject_code=subject_code,
            title=request.form.get('title', '').strip(),
            is_public=request.form.get('is_public', default_privacy),
        )

    title = request.form.get('title', '').strip()
    if not title:
        flash('Please give your quiz a title before creating it.', 'error')
        return _render_error(subject_code=subject_code, title='')
    if len(title) > 100:
        flash('Title is too long (max 100 characters).', 'error')
        return _render_error(subject_code=subject_code, title=title)

    time_per_question = request.form.get('time_per_question')
    if time_per_question is None:
        time_per_question = default_time
    else:
        try:
            time_per_question = int(time_per_question)
        except ValueError:
            time_per_question = default_time

    max_participants = request.form.get('max_participants')
    if max_participants is None:
        max_participants = default_max_participants
    else:
        try:
            max_participants = int(max_participants)
        except ValueError:
            max_participants = default_max_participants

    privacy = request.form.get('is_public')
    if privacy is None:
        privacy = default_privacy
    else:
        try:
            privacy = int(privacy)
        except ValueError:
            privacy = default_privacy
    if privacy not in (0, 1):
        privacy = 1

    if privacy == 0 and not can_create_private_live_quiz():
        flash('Upgrade to Premium or Pro to create private live quizzes.', 'error')
        return _render_error(
            subject_code=subject_code, title=title, is_public=1,
        )

    try:
        schedule_minutes = int(request.form.get('schedule_minutes', 0))
    except ValueError:
        schedule_minutes = 0
    if schedule_minutes < 0:
        schedule_minutes = 0

    if schedule_minutes > 0 and not can_schedule_live_quiz():
        flash('Upgrade to Premium or Pro to schedule live quizzes.', 'error')
        return _render_error(
            subject_code=subject_code, title=title, is_public=privacy,
        )

    try:
        questions, available = get_questions_for_subject(
            subject_code, question_count, grade=effective_grade,
        )
    except Exception as e:
        logger.error(f"Error fetching questions for {subject_code} grade {effective_grade}: {e}",
                     exc_info=True)
        flash('Error fetching questions. Please try again.', 'error')
        return _render_error(
            subject_code=subject_code, title=title, is_public=privacy,
        )

    if available == 0:
        flash(
            f'No {grade_label(effective_grade)} questions for this subject. '
            f'Try a different grade or subject.',
            'error',
        )
        return _render_error(subject_code=subject_code, title=title, is_public=privacy)

    if available < question_count:
        return _render_error(
            subject_code=subject_code,
            title=title,
            is_public=privacy,
            not_enough=True,
            available=available,
            requested=question_count,
            effective_grade=effective_grade,
        )

    question_ids = [q['id'] for q in questions]
    questions_cache = {q['id']: q for q in questions}

    quiz_data = {
        'creator_id': user_id,
        'title': title,
        'subject_code': subject_code,
        'grade': effective_grade,
        'question_count': question_count,
        'max_participants': max_participants,
        'time_per_question': time_per_question,
        'current_question_index': 0,
        'question_ids': question_ids,
        'is_public': privacy,
    }

    if schedule_minutes > 0:
        quiz_data['status'] = 'scheduled'
        scheduled_time = datetime.now(timezone.utc) + timedelta(minutes=schedule_minutes)
        quiz_data['scheduled_start'] = scheduled_time.isoformat()
    else:
        quiz_data['status'] = 'waiting'
        quiz_data['scheduled_start'] = None

    quiz, error = create_live_quiz_with_participant(quiz_data, user_id)

    if error or not quiz:
        flash(f'Failed to create quiz: {error or "Unknown error"}', 'error')
        return _render_error(
            subject_code=subject_code, title=title, is_public=privacy,
        )

    if not quiz.get('id'):
        logger.error(f"Quiz created but missing 'id': {quiz}")
        flash('Quiz created but missing ID. Please contact support.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    manager = get_state_manager()
    quiz_state = manager.create_quiz(quiz['id'], quiz, question_ids, questions_cache)

    user = get_student_by_id(user_id)
    name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'Participant'
    quiz_state.add_participant(user_id, name, user.get('public_id', '----'))
    quiz_state.set_participant_ready(user_id, True)

    manager.enqueue_event({
        'quiz_id': quiz['id'],
        'user_id': user_id,
        'event_type': 'JOIN',
        'payload': json.dumps({'name': name}),
    })

    flash('Quiz created successfully! Share the join code.', 'success')
    return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))


@live_quiz_bp.route('/create-with-available', methods=['POST'])
def create_with_available():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    if not validate_csrf():
        flash('Invalid CSRF token. Please try again.', 'error')
        return redirect(url_for('live_quiz.create'))

    if not can_create_live_quiz():
        flash('Upgrade to Premium or Pro to create live quizzes.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    user_id = session['user_id']

    active_quiz = _get_active_quiz_for_user(user_id)
    if active_quiz:
        role = 'creator' if active_quiz.get('is_creator') else 'participant'
        flash(
            f'You are already in an unfinished quiz as a {role}. '
            f'Please finish, leave, or abandon it before creating a new one.',
            'error',
        )
        return redirect(url_for('live_quiz.create'))

    form_grade = (request.form.get('grade') or '').strip().upper()
    if form_grade not in UI_GRADES:
        form_grade = _profile_grade(user_id)
    effective_grade = form_grade

    subject_code = request.form.get('subject_code', '').strip()
    try:
        question_count = int(request.form.get('question_count', 10))
    except ValueError:
        question_count = 10
    if question_count < 5 or question_count > 30:
        flash('Number of questions must be between 5 and 30.', 'error')
        return redirect(url_for('live_quiz.create'))

    title = request.form.get('title', '').strip()
    if not title:
        flash('Please give your quiz a title before creating it.', 'error')
        return redirect(url_for('live_quiz.create'))
    if len(title) > 100:
        flash('Title is too long (max 100 characters).', 'error')
        return redirect(url_for('live_quiz.create'))

    try:
        is_public = int(request.form.get('is_public', 1))
    except ValueError:
        is_public = 1
    if is_public not in (0, 1):
        is_public = 1

    if is_public == 0 and not can_create_private_live_quiz():
        flash('Upgrade to Premium or Pro to create private live quizzes.', 'error')
        return redirect(url_for('live_quiz.create'))

    user_subjects = get_user_subject_list(user_id)
    allowed_codes = [s['code'] for s in user_subjects]
    if subject_code not in allowed_codes:
        flash('Subject not available.', 'error')
        return redirect(url_for('live_quiz.create'))

    questions, available = get_questions_for_subject(
        subject_code, question_count, grade=effective_grade,
    )
    if available == 0:
        flash('No questions available for this grade and subject.', 'error')
        return redirect(url_for('live_quiz.create'))

    question_ids = [q['id'] for q in questions]
    questions_cache = {q['id']: q for q in questions}

    quiz_data = {
        'creator_id': user_id,
        'title': title,
        'subject_code': subject_code,
        'grade': effective_grade,
        'question_count': available,
        'max_participants': 50,
        'time_per_question': 30,
        'current_question_index': 0,
        'question_ids': question_ids,
        'is_public': is_public,
        'status': 'waiting',
        'scheduled_start': None,
    }

    quiz, error = create_live_quiz_with_participant(quiz_data, user_id)
    if error or not quiz:
        flash(f'Failed to create quiz: {error or "Unknown error"}', 'error')
        return redirect(url_for('live_quiz.create'))

    manager = get_state_manager()
    quiz_state = manager.create_quiz(quiz['id'], quiz, question_ids, questions_cache)
    user = get_student_by_id(user_id)
    name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'Participant'
    quiz_state.add_participant(user_id, name, user.get('public_id', '----'))
    quiz_state.set_participant_ready(user_id, True)

    manager.enqueue_event({
        'quiz_id': quiz['id'],
        'user_id': user_id,
        'event_type': 'JOIN',
        'payload': json.dumps({'name': name}),
    })

    flash(f'Quiz created with {available} questions!', 'success')
    return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))


@live_quiz_bp.route('/abandon/<quiz_id>', methods=['POST'])
def abandon_quiz(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    if quiz['status'] == 'active':
        return jsonify({
            'error': 'This quiz is currently active. You cannot abandon it until it finishes.',
        }), 400

    if quiz['status'] not in ('waiting', 'scheduled'):
        return jsonify({'error': 'Quiz is not in an abandonable state.'}), 400

    manager = get_state_manager()

    if quiz['creator_id'] == user_id:
        try:
            update_live_quiz(quiz_id, {
                'status': 'finished',
                'ended_at': get_somali_time_db(),
                'scheduled_start': None,
            })
            execute_with_retry(
                "UPDATE live_quiz_participants SET status = 'left' "
                "WHERE quiz_id = ? AND status != 'left'",
                (quiz_id,), commit=True,
            )
            try:
                manager.delete_quiz(quiz_id)
            except Exception:
                pass
            invalidate_quiz_cache(quiz_id)

            logger.info(f"User {user_id} abandoned own quiz {quiz_id} (soft-closed)")
            return jsonify({
                'success': True,
                'action': 'closed',
                'redirect': url_for('live_quiz.create'),
            })
        except Exception as e:
            logger.error(f"abandon_quiz (creator) failed for {quiz_id}: {e}", exc_info=True)
            return jsonify({'error': 'Could not abandon the quiz.'}), 500

    success = db_leave_live_quiz(quiz_id, user_id)
    if not success:
        return jsonify({'error': 'Failed to leave the quiz.'}), 500

    try:
        manager.ensure_quiz_in_memory(quiz_id)
        quiz_state = manager.get_quiz(quiz_id)
        if quiz_state:
            quiz_state.remove_participant(user_id)
            manager.enqueue_event({
                'quiz_id': quiz_id,
                'user_id': user_id,
                'event_type': 'LEAVE',
                'payload': json.dumps({}),
            })
    except Exception as e:
        logger.warning(f"abandon_quiz (participant) memory cleanup failed: {e}")

    invalidate_quiz_cache(quiz_id)
    logger.info(f"User {user_id} abandoned participation in quiz {quiz_id}")
    return jsonify({
        'success': True,
        'action': 'left',
        'redirect': url_for('live_quiz.create'),
    })


@live_quiz_bp.route('/join', methods=['GET', 'POST'])
def join():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    user_tier = get_current_user_tier()

    if request.method == 'POST':
        if not validate_csrf():
            flash('Invalid CSRF token. Please try again.', 'error')
            return render_template('dashboard/live_quiz/join.html')

        join_code = request.form.get('join_code', '').strip().upper()
        if not join_code:
            flash('Please enter a join code.', 'error')
            return render_template('dashboard/live_quiz/join.html')

        join_code = join_code.replace(' ', '')
        quiz = get_active_live_quiz(join_code)
        if not quiz:
            flash('Invalid join code or quiz has already started.', 'error')
            return render_template('dashboard/live_quiz/join.html')

        # Grade gate
        if _is_grade_locked_for_viewer(quiz, user_id, user_tier):
            flash(
                'This quiz is for a different grade. '
                'Upgrade to join quizzes of any grade.',
                'error',
            )
            return redirect(url_for('live_quiz.join') + '?grade_locked=1')

        active_quiz = get_user_active_quiz(user_id)
        if active_quiz and active_quiz != quiz['id']:
            flash('You are already in another quiz. Please leave that quiz first.', 'error')
            return render_template('dashboard/live_quiz/join.html')

        participant = get_live_quiz_participant(quiz['id'], user_id)
        if participant:
            if participant.get('status') == 'left':
                if quiz['status'] in ['waiting', 'scheduled']:
                    success = db_rejoin_live_quiz(quiz['id'], user_id)
                    if success:
                        manager = get_state_manager()
                        manager.ensure_quiz_in_memory(quiz['id'])
                        quiz_state = manager.get_quiz(quiz['id'])
                        if quiz_state:
                            quiz_state.set_participant_ready(user_id, False)
                            quiz_state.remove_participant(user_id)
                            user = get_student_by_id(user_id)
                            name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'Participant'
                            quiz_state.add_participant(user_id, name, user.get('public_id', '----'))
                        flash('You have rejoined the quiz!', 'success')
                        return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))
                    else:
                        flash('Failed to rejoin. Please try again.', 'error')
                else:
                    flash('Cannot rejoin an active quiz.', 'error')
                return redirect(url_for('live_quiz.lobby'))
            else:
                flash('You have already joined this quiz.', 'info')
                return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))

        participant_count = get_live_quiz_count(quiz['id'])
        if participant_count >= quiz.get('max_participants', 50):
            flash('This quiz is full.', 'error')
            return render_template('dashboard/live_quiz/join.html')

        add_live_quiz_participant(quiz['id'], user_id)

        manager = get_state_manager()
        manager.ensure_quiz_in_memory(quiz['id'])
        quiz_state = manager.get_quiz(quiz['id'])
        if quiz_state:
            user = get_student_by_id(user_id)
            name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'Participant'
            quiz_state.add_participant(user_id, name, user.get('public_id', '----'))
            manager.enqueue_event({
                'quiz_id': quiz['id'],
                'user_id': user_id,
                'event_type': 'JOIN',
                'payload': json.dumps({'name': name}),
            })

        creator_id = quiz.get('creator_id')
        if creator_id and creator_id != user_id:
            user = get_student_by_id(user_id)
            user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'Participant'
            try:
                send_notification(
                    user_id=creator_id,
                    notification_type='participant_joined',
                    title='👋 New Participant!',
                    body=f'{user_name} joined your quiz "{quiz.get("title", "Live Quiz")}"',
                    link=f'/live-quiz/waiting-room/{quiz["id"]}',
                    icon='👤',
                )
            except Exception as e:
                logger.warning(f"participant_joined notification failed: {e}")

        flash('You have joined the quiz!', 'success')
        return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))

    return render_template('dashboard/live_quiz/join.html')


@live_quiz_bp.route('/waiting-room/<quiz_id>')
def waiting_room(quiz_id):
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        flash('Quiz not found.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    participant = get_live_quiz_participant(quiz_id, user_id)
    if not participant and quiz['creator_id'] != user_id:
        flash('You are not a participant in this quiz.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    is_creator = quiz['creator_id'] == user_id
    user_participant_status = participant.get('status') if participant else None

    manager = get_state_manager()
    manager.ensure_quiz_in_memory(quiz_id)
    quiz_state = manager.get_quiz(quiz_id)
    if quiz_state:
        participants = quiz_state.get_all_participants()
        participant_count = len(participants)
        active_count = quiz_state.get_active_count()
    else:
        participants = get_active_participants(quiz_id)
        participant_count = len(participants)
        active_count = participant_count

    scheduled_start = quiz.get('scheduled_start')
    starts_in_seconds = None
    scheduled_start_display = None
    if scheduled_start and quiz['status'] == 'scheduled':
        try:
            start_dt = datetime.fromisoformat(scheduled_start.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            diff = (start_dt - now).total_seconds()
            starts_in_seconds = max(0, int(diff))
            scheduled_start_display = format_somali_time(start_dt)
        except Exception:
            pass

    return render_template(
        'dashboard/live_quiz/waiting_room.html',
        quiz=quiz,
        is_creator=is_creator,
        participants=participants,
        participant_count=participant_count,
        active_participant_count=active_count,
        user_participant_status=user_participant_status,
        starts_in_seconds=starts_in_seconds,
        scheduled_start_display=scheduled_start_display,
    )


@live_quiz_bp.route('/waiting-room/participants/<quiz_id>')
def waiting_room_participants(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    manager = get_state_manager()
    manager.ensure_quiz_in_memory(quiz_id)
    quiz_state = manager.get_quiz(quiz_id)
    if quiz_state:
        participants = quiz_state.get_all_participants()
        return jsonify({'participants': participants, 'count': len(participants)})

    db_participants = get_active_participants(quiz_id)
    formatted = []
    for p in db_participants:
        formatted.append({
            'student_id': p['student_id'],
            'name': f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or 'Unknown',
            'public_id': p.get('public_id', '----'),
            'status': p.get('status', 'active'),
            'is_ready': p.get('is_ready', False),
        })
    return jsonify({'participants': formatted, 'count': len(formatted)})


@live_quiz_bp.route('/toggle-ready/<quiz_id>', methods=['POST'])
def toggle_ready(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    data = request.get_json()
    is_ready = data.get('is_ready', False)

    manager = get_state_manager()
    manager.ensure_quiz_in_memory(quiz_id)
    quiz_state = manager.get_quiz(quiz_id)
    if quiz_state:
        success = quiz_state.set_participant_ready(user_id, is_ready)
        if success:
            update_participant_ready(quiz_id, user_id, is_ready)
            return jsonify({'success': True, 'is_ready': is_ready})

    return jsonify({'error': 'Failed to update ready status'}), 500


@live_quiz_bp.route('/start/<quiz_id>', methods=['POST'])
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

    participants = quiz_state.get_all_participants()
    for p in participants:
        send_notification(
            user_id=p['student_id'],
            notification_type='live_quiz_start',
            title='🚀 Quiz Started!',
            body=f'"{quiz.get("title", "Live Quiz")}" has started! Join now!',
            link=f'/live-quiz/play/{quiz_id}',
            icon='⚡',
        )

    return jsonify({
        'success': True,
        'quiz_id': quiz_id,
        'redirect_url': url_for('live_quiz.play', quiz_id=quiz_id),
    })


@live_quiz_bp.route('/quiz-state/<quiz_id>')
def quiz_state_endpoint(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    user_id = session['user_id']
    quiz = get_live_quiz_with_subject(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    manager = get_state_manager()
    manager.ensure_quiz_in_memory(quiz_id)
    quiz_state = manager.get_quiz(quiz_id)

    if not quiz_state:
        if quiz['status'] == 'finished':
            return jsonify({
                'status': 'finished',
                'redirect_url': url_for('live_quiz.results', quiz_id=quiz_id),
            })
        return jsonify({'status': 'waiting', 'error': 'Quiz not active'})

    p = quiz_state.get_participant(user_id)
    if not p and quiz['creator_id'] != user_id:
        return jsonify({'error': 'Not a participant'}), 404

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


@live_quiz_bp.route('/get-question/<quiz_id>')
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
    data = request.get_json()
    quiz_id = data.get('quiz_id')
    question_id = data.get('question_id')
    answer = data.get('answer')

    if not quiz_id or not question_id or not answer:
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
    data = request.get_json()
    quiz_id = data.get('quiz_id')
    question_id = data.get('question_id')

    if not quiz_id or not question_id:
        return jsonify({'error': 'Missing required fields'}), 400

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
    quiz_id = data.get('quiz_id')
    question_id = data.get('question_id')

    if not quiz_id or not question_id:
        return jsonify({'error': 'Missing required fields'}), 400

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
    quiz_id = data.get('quiz_id')
    question_id = data.get('question_id')

    if not quiz_id or not question_id:
        return jsonify({'error': 'Missing required fields'}), 400

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


# ============================================
# REACTIONS
# ============================================

@live_quiz_bp.route('/interaction/<quiz_id>/status', methods=['GET'])
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


@live_quiz_bp.route('/interaction/<quiz_id>/like', methods=['POST'])
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


@live_quiz_bp.route('/interaction/<quiz_id>/save', methods=['POST'])
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

    if limit is not None and not already_saved:
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


@live_quiz_bp.route('/interaction/<quiz_id>/report', methods=['POST'])
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


@live_quiz_bp.route('/leaderboard/<quiz_id>')
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


@live_quiz_bp.route('/play/<quiz_id>')
def play(quiz_id):
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        flash('Quiz not found.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    participant = get_live_quiz_participant(quiz_id, user_id)
    if not participant and quiz['creator_id'] != user_id:
        flash('You are not a participant in this quiz.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    if quiz['status'] != 'active':
        flash('Quiz is not active.', 'error')
        return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz_id))

    return render_template('dashboard/live_quiz/play.html', quiz=quiz)


@live_quiz_bp.route('/leave/<quiz_id>', methods=['POST'])
def leave_quiz(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    if quiz['status'] == 'finished':
        return jsonify({'error': 'Quiz already finished'}), 400

    participant = get_live_quiz_participant(quiz_id, user_id)
    if not participant:
        return jsonify({'error': 'Not a participant'}), 404

    if participant.get('status') == 'left':
        return jsonify({'error': 'Already left this quiz'}), 400

    if quiz['creator_id'] == user_id:
        return jsonify({'error': 'Creator cannot leave the quiz'}), 400

    success = db_leave_live_quiz(quiz_id, user_id)
    if success:
        manager = get_state_manager()
        manager.ensure_quiz_in_memory(quiz_id)
        quiz_state = manager.get_quiz(quiz_id)
        if quiz_state:
            quiz_state.remove_participant(user_id)
            manager.enqueue_event({
                'quiz_id': quiz_id,
                'user_id': user_id,
                'event_type': 'LEAVE',
                'payload': json.dumps({}),
            })
        invalidate_quiz_cache(quiz_id)
        return jsonify({
            'success': True,
            'message': 'You have left the quiz',
            'redirect': url_for('live_quiz.lobby'),
        })

    return jsonify({'error': 'Failed to leave quiz'}), 500


@live_quiz_bp.route('/rejoin/<quiz_id>', methods=['POST'])
def rejoin_quiz(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    if quiz['status'] not in ['waiting', 'scheduled']:
        return jsonify({'error': 'Quiz is not open for rejoining'}), 400

    # Grade gate on rejoin too
    if _is_grade_locked_for_viewer(quiz, user_id, get_current_user_tier()):
        return jsonify({
            'error': 'This quiz is for a different grade.',
            'reason': 'grade_mismatch',
        }), 403

    participant = get_live_quiz_participant(quiz_id, user_id)
    if not participant or participant.get('status') != 'left':
        return jsonify({'error': 'You are not eligible to rejoin'}), 400

    success = db_rejoin_live_quiz(quiz_id, user_id)
    if success:
        manager = get_state_manager()
        manager.ensure_quiz_in_memory(quiz_id)
        quiz_state = manager.get_quiz(quiz_id)
        if quiz_state:
            quiz_state.remove_participant(user_id)
            user = get_student_by_id(user_id)
            name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'Participant'
            quiz_state.add_participant(user_id, name, user.get('public_id', '----'))
            manager.enqueue_event({
                'quiz_id': quiz_id,
                'user_id': user_id,
                'event_type': 'JOIN',
                'payload': json.dumps({'name': name}),
            })
        invalidate_quiz_cache(quiz_id)
        return jsonify({
            'success': True,
            'message': 'You have rejoined the quiz',
            'redirect': url_for('live_quiz.waiting_room', quiz_id=quiz_id),
        })

    return jsonify({'error': 'Failed to rejoin quiz'}), 500


@live_quiz_bp.route('/delete/<quiz_id>', methods=['POST'])
def delete_quiz(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    if quiz['creator_id'] != user_id and not is_admin(user_id):
        return jsonify({'error': 'Permission denied'}), 403

    manager = get_state_manager()
    manager.delete_quiz(quiz_id)

    success = db_delete_live_quiz(quiz_id)
    if success:
        invalidate_quiz_cache(quiz_id)
        return jsonify({'success': True, 'message': 'Quiz deleted'})

    return jsonify({'error': 'Failed to delete quiz'}), 500


@live_quiz_bp.route('/results/<quiz_id>')
def results(quiz_id):
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        flash('Quiz not found.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    if quiz['status'] != 'finished':
        finalize_live_quiz(quiz_id)
        quiz = get_live_quiz_with_subject(quiz_id)

    is_creator = quiz['creator_id'] == user_id
    all_participants = get_live_quiz_participants_with_names(quiz_id)

    sorted_participants = sorted(
        all_participants, key=lambda x: x.get('score', 0), reverse=True,
    )
    for i, p in enumerate(sorted_participants, 1):
        if p.get('ranking') != i:
            update_live_quiz_participant(p['id'], {'ranking': i})
            p['ranking'] = i

    user_participant = None
    for p in sorted_participants:
        if p['student_id'] == user_id:
            user_participant = p
            break

    return render_template(
        'dashboard/live_quiz/results.html',
        quiz=quiz,
        is_creator=is_creator,
        participants=sorted_participants,
        user_participant=user_participant,
    )


@live_quiz_bp.route('/analysis/<quiz_id>')
def analysis(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    if quiz['creator_id'] != user_id:
        return jsonify({'error': 'Only the creator can view analysis'}), 403

    if not has_feature("live_quiz_analytics"):
        return jsonify({'error': 'Upgrade to Premium or Pro to access host analytics.'}), 403

    question_ids = quiz.get('question_ids', [])
    participants = get_live_quiz_participants(quiz_id)

    analysis_data = []
    for i, qid in enumerate(question_ids):
        correct_count = 0
        total_count = 0
        for p in participants:
            answers = p.get('answers', {})
            if str(qid) in answers:
                total_count += 1
                if answers[str(qid)].get('correct', False):
                    correct_count += 1
        question = get_question_by_id(qid)
        q_text = question.get('question_text', 'Unknown') if question else 'Unknown'
        correct_rate = round((correct_count / total_count) * 100) if total_count > 0 else 0
        wrong_rate = 100 - correct_rate
        analysis_data.append({
            'index': i,
            'text': q_text,
            'correct_rate': correct_rate,
            'wrong_rate': wrong_rate,
            'total_answers': total_count,
        })

    most_correct = sorted(analysis_data, key=lambda x: x['correct_rate'], reverse=True)[:3]
    most_wrong = sorted(analysis_data, key=lambda x: x['wrong_rate'], reverse=True)[:3]

    return jsonify({'most_correct': most_correct, 'most_wrong': most_wrong})


@live_quiz_bp.route('/export/<quiz_id>')
def export_results(quiz_id):
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        flash('Quiz not found.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    if quiz['creator_id'] != user_id:
        flash('Only the creator can export results.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    participants = get_live_quiz_participants_with_names(quiz_id)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Rank', 'Name', 'Public ID', 'Score',
        'Correct', 'Wrong', 'Skipped', 'Status',
    ])

    sorted_participants = sorted(
        participants, key=lambda x: x.get('score', 0), reverse=True,
    )
    for i, p in enumerate(sorted_participants, 1):
        student = p.get('student', {})
        name = f"{student.get('first_name', '')} {student.get('last_name', '')}".strip() or 'Unknown'
        writer.writerow([
            i,
            name,
            student.get('public_id', '----'),
            p.get('score', 0),
            p.get('correct_count', 0),
            p.get('wrong_count', 0),
            p.get('skipped_count', 0),
            p.get('status', 'active'),
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename=quiz_{quiz_id}_results.csv',
        },
    )


# ============================================
# ADMIN UTILITY
# ============================================

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