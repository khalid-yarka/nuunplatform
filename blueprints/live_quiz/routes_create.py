# ============================================================
# blueprints/live_quiz/routes_create.py
# ============================================================
# Quiz creation and abandon. create() builds a new live quiz and
# seeds the creator as its first participant. abandon() soft-closes
# the user's own waiting quiz (creator) or flips their own row to
# 'left' (participant).

import json
import logging
from datetime import datetime, timezone, timedelta

from flask import request, session, flash, redirect, url_for, jsonify, render_template

from db import (
    get_live_quiz_by_id,
    get_student_by_id,
    get_user_subject_list,
    create_live_quiz_with_participant,
    update_live_quiz,
    update_participant_ready,
    leave_live_quiz as db_leave_live_quiz,
    execute_with_retry,
)
from utils import validate_csrf, ensure_csrf_token, get_somali_time_db
from question_utils import UI_GRADES, DEFAULT_GRADE, grade_label
from services.tier_service import (
    can_create_live_quiz,
    can_create_private_live_quiz,
    can_schedule_live_quiz,
)

from . import live_quiz_bp
from ._shared import (
    get_state_manager,
    get_active_quiz_for_user,
    profile_grade,
    grade_choices,
    invalidate_quiz_cache,
)

logger = logging.getLogger(__name__)


def _get_questions_for_subject(subject_code, limit, grade=None):
    from db import get_questions_by_subject
    questions = get_questions_by_subject(subject_code, limit, grade=grade)
    return questions, len(questions)


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
        flash('You do not have permission to create competitions.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    settings = session.get('settings', {})
    default_time = settings.get('live_quiz.default_time_per_question', 30)
    default_max_participants = settings.get('live_quiz.default_max_participants', 50)
    default_privacy = settings.get('live_quiz.default_privacy', 1)

    user_grade = profile_grade(user_id)
    grades = grade_choices()

    active_quiz = get_active_quiz_for_user(user_id)

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
        flash('Upgrade to Premium to create private competitions.', 'error')
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
        flash('Upgrade to Premium to schedule competitions.', 'error')
        return _render_error(
            subject_code=subject_code, title=title, is_public=privacy,
        )

    try:
        questions, available = _get_questions_for_subject(
            subject_code, question_count, grade=effective_grade,
        )
    except Exception as e:
        logger.error(
            f"Error fetching questions for {subject_code} grade {effective_grade}: {e}",
            exc_info=True,
        )
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
    update_participant_ready(quiz['id'], user_id, True)

    manager.enqueue_event({
        'quiz_id': quiz['id'],
        'user_id': user_id,
        'event_type': 'JOIN',
        'payload': json.dumps({'name': name}),
    })

    try:
        if (quiz.get('is_public') and quiz.get('status') == 'waiting'):
            from services.notification_service import broadcast_new_quiz
            join_url = '/live-quiz/j/' + quiz['join_code']
            cname = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'NuunPlatform'
            broadcast_new_quiz(
                quiz_title=quiz.get('title') or 'Live Quiz',
                creator_name=cname,
                join_url=join_url,
                creator_id=user_id,
            )
    except Exception as e:
        logger.warning(f"new-quiz broadcast failed: {e}")

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
        flash('You do not have permission to create competitions.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    user_id = session['user_id']

    active_quiz = get_active_quiz_for_user(user_id)
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
        form_grade = profile_grade(user_id)
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
        flash('Upgrade to Premium to create private competitions.', 'error')
        return redirect(url_for('live_quiz.create'))

    user_subjects = get_user_subject_list(user_id)
    allowed_codes = [s['code'] for s in user_subjects]
    if subject_code not in allowed_codes:
        flash('Subject not available.', 'error')
        return redirect(url_for('live_quiz.create'))

    questions, available = _get_questions_for_subject(
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
    update_participant_ready(quiz['id'], user_id, True)

    manager.enqueue_event({
        'quiz_id': quiz['id'],
        'user_id': user_id,
        'event_type': 'JOIN',
        'payload': json.dumps({'name': name}),
    })

    try:
        if (quiz.get('is_public') and quiz.get('status') == 'waiting'):
            from services.notification_service import broadcast_new_quiz
            join_url = '/live-quiz/j/' + quiz['join_code']
            cname = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'NuunPlatform'
            broadcast_new_quiz(
                quiz_title=quiz.get('title') or 'Live Quiz',
                creator_name=cname,
                join_url=join_url,
                creator_id=user_id,
            )
    except Exception as e:
        logger.warning(f"new-quiz broadcast failed: {e}")

    flash(f'Quiz created with {available} questions!', 'success')
    return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))


@live_quiz_bp.route('/abandon/<int:quiz_id>', methods=['POST'])
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