# ============================================================
# blueprints/live_quiz/routes_join.py
# ============================================================
# Join by code form (POST) and direct join link (GET /j/<code>).
# Both share the same eligibility checks: grade gate, active-quiz
# conflict, existing participant, atomic add, state sync.

import json
import logging

from flask import request, session, flash, redirect, url_for, render_template

from db import (
    get_active_live_quiz,
    get_live_quiz_participant,
    get_user_active_quiz,
    get_student_by_id,
)
from utils import validate_csrf
from services.tier_service import get_current_user_tier

from . import live_quiz_bp
from ._shared import (
    _atomic_add_participant,
    _rejoin_if_left,
    is_grade_locked_for_viewer,
    ensure_participant_in_state,
    notify_creator_of_join,
)

logger = logging.getLogger(__name__)


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

        if is_grade_locked_for_viewer(quiz, user_id, user_tier):
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
            status = participant.get('status')
            if status == 'left':
                if quiz['status'] in ('waiting', 'scheduled'):
                    if _rejoin_if_left(quiz['id'], user_id):
                        flash('You have rejoined the quiz!', 'success')
                        return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))
                    flash('Failed to rejoin. Please try again.', 'error')
                    return redirect(url_for('live_quiz.lobby'))
                else:
                    flash('Cannot rejoin an active quiz.', 'error')
                    return redirect(url_for('live_quiz.lobby'))
            else:
                flash('You have already joined this quiz.', 'info')
                return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))

        question_ids = quiz.get('question_ids', []) or []
        max_participants = quiz.get('max_participants', 50)
        ok, code = _atomic_add_participant(quiz['id'], user_id, question_ids, max_participants)
        if not ok:
            if code == 'full':
                flash('This quiz is full.', 'error')
                return render_template('dashboard/live_quiz/join.html')
            if code == 'already_joined':
                flash('You have already joined this quiz.', 'info')
                return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))
            flash('Failed to join quiz.', 'error')
            return render_template('dashboard/live_quiz/join.html')

        if not ensure_participant_in_state(quiz['id'], user_id):
            flash('Quiz could not be loaded. Please try again.', 'error')
            return redirect(url_for('live_quiz.lobby'))

        creator_id = quiz.get('creator_id')
        if creator_id and creator_id != user_id:
            cname = session.get('user_name') or 'Participant'
            qtitle = quiz.get('title') or 'Live Quiz'
            notify_creator_of_join(creator_id, cname, qtitle, quiz['id'])

        flash('You have joined the quiz!', 'success')
        return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))

    return render_template('dashboard/live_quiz/join.html')


@live_quiz_bp.route('/j/<code>', methods=['GET'])
def direct_join(code):
    """
    Direct join link: /live-quiz/j/<code>

    Reuses the same eligibility checks as POST /join, but reads the
    code from the URL. Works for both public and private quizzes —
    the link is the invite. Grade gating is preserved.

    Not logged in → bounce to /login with next=<this URL>. After
    logging in, the user is returned to the same URL and joined
    automatically.
    """
    if 'user_id' not in session:
        return redirect(url_for('auth.login', next=request.url))

    code_norm = (code or '').strip().upper().replace(' ', '')
    if not code_norm:
        flash('Invalid join code.', 'error')
        return redirect(url_for('live_quiz.join'))

    quiz = get_active_live_quiz(code_norm)
    if not quiz:
        flash('Invalid or expired code. Please check and try again.', 'error')
        return redirect(url_for('live_quiz.join') + '?code=' + code_norm + '&invalid=1')

    user_id = session['user_id']
    user_tier = get_current_user_tier()

    if is_grade_locked_for_viewer(quiz, user_id, user_tier):
        flash(
            'This quiz is for a different grade. '
            'Upgrade to join quizzes of any grade.',
            'error',
        )
        return redirect(url_for('live_quiz.join') + '?grade_locked=1')

    active_quiz = get_user_active_quiz(user_id)
    if active_quiz and active_quiz != quiz['id']:
        flash('You are already in another quiz. Please leave that quiz first.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    participant = get_live_quiz_participant(quiz['id'], user_id)
    if participant:
        status = participant.get('status')
        if status == 'left':
            if quiz['status'] in ('waiting', 'scheduled'):
                if _rejoin_if_left(quiz['id'], user_id):
                    return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))
                flash('Failed to rejoin. Please try again.', 'error')
                return redirect(url_for('live_quiz.lobby'))
            flash('Cannot rejoin an active quiz.', 'error')
            return redirect(url_for('live_quiz.lobby'))
        return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))

    question_ids = quiz.get('question_ids', []) or []
    max_participants = quiz.get('max_participants', 50)
    ok, reason_code = _atomic_add_participant(
        quiz['id'], user_id, question_ids, max_participants,
    )
    if not ok:
        if reason_code == 'full':
            flash('This quiz is full.', 'error')
            return redirect(url_for('live_quiz.lobby'))
        if reason_code == 'already_joined':
            return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))
        flash('Failed to join quiz. Please try again.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    if not ensure_participant_in_state(quiz['id'], user_id):
        flash('Quiz could not be loaded. Please try again.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    creator_id = quiz.get('creator_id')
    if creator_id and creator_id != user_id:
        cname = session.get('user_name') or 'Participant'
        qtitle = quiz.get('title') or 'Live Quiz'
        notify_creator_of_join(creator_id, cname, qtitle, quiz['id'])

    return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))