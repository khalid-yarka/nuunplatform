# ============================================================
# blueprints/live_quiz/routes_join.py
# ============================================================
# Join by code form (POST) and direct join link (GET /j/<code>).
# Both share the same eligibility checks: grade gate, active-quiz
# conflict, existing participant, atomic add, state sync.
#
# get_active_live_quiz() only returns quizzes that are joinable
# (waiting / scheduled). A finished or active quiz comes back as
# None, so we fall back to a raw lookup that ignores status and
# branch on the real value:
#
#   · code not found           → join_unavailable.html (reason=not_found)
#   · status finished          → redirect to results
#   · status active            → join_unavailable.html (reason=active)
#   · status scheduled/waiting → join_unavailable.html (reason=full)
#                                 (only reached when the quiz is full)
#   · status anything else     → join_unavailable.html (reason=full)
#
# Both entry points — the POST form and the GET direct link — now
# behave identically. No path bounces the user back to the join
# form with a toast; every non-joinable code lands on a real page.

import logging

from flask import request, session, flash, redirect, url_for, render_template

from db import (
    get_active_live_quiz,
    get_live_quiz_participant,
    get_user_active_quiz,
    execute_with_retry,
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

_UNAVAILABLE_TEMPLATE = 'dashboard/live_quiz/join_unavailable.html'
_JOIN_TEMPLATE = 'dashboard/live_quiz/join.html'


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _lookup_quiz_any_status(code):
    """
    Return the quiz row for `code` regardless of status, or None.

    Uses SELECT * so it works against any live_quizzes schema.
    question_ids is decoded from JSON when present.
    """
    if not code:
        return None
    try:
        row = execute_with_retry(
            "SELECT * FROM live_quizzes WHERE join_code = ? LIMIT 1",
            (code,),
        ).fetchone()
    except Exception as e:
        logger.warning(f"_lookup_quiz_any_status failed for code={code}: {e}")
        return None
    if row is None:
        return None
    try:
        quiz = dict(row)
    except Exception:
        return None
    try:
        from db import from_json
        quiz['question_ids'] = from_json(quiz.get('question_ids'))
    except Exception:
        pass
    return quiz


def _count_participants(quiz_id):
    try:
        row = execute_with_retry(
            "SELECT COUNT(*) AS n FROM live_quiz_participants WHERE quiz_id = ?",
            (quiz_id,),
        ).fetchone()
        return int(row['n']) if row else 0
    except Exception:
        return 0


def _decorate_for_unavailable(quiz):
    """
    Fill in the fields join_unavailable.html expects, using safe
    defaults when the raw row doesn't carry them.
    """
    if not quiz:
        return quiz
    if 'participant_count' not in quiz:
        quiz['participant_count'] = _count_participants(quiz.get('id'))
    quiz.setdefault('subject_icon', '📚')
    quiz.setdefault('subject_name', quiz.get('subject_code') or '')
    quiz.setdefault('creator_first_name', '')
    quiz.setdefault('creator_last_name', '')
    return quiz


def _render_unavailable(quiz, reason, attempted_code=None):
    return render_template(
        _UNAVAILABLE_TEMPLATE,
        quiz=_decorate_for_unavailable(quiz) if quiz else None,
        reason=reason,
        attempted_code=attempted_code,
    )


def _resolve_non_joinable(code):
    """
    Given a code that get_active_live_quiz() rejected, decide what
    the user should see. Never returns None — every code, known or
    unknown, gets a real page.
    """
    fallback = _lookup_quiz_any_status(code)

    if not fallback:
        return _render_unavailable(None, 'not_found', attempted_code=code)

    status = (fallback.get('status') or '').strip().lower()

    if status == 'finished':
        # The quiz is over. Show a dedicated page so the user
        # understands why the link did not join them, and offer a
        # clear "view results" button as the next step.
        return _render_unavailable(fallback, 'finished')

    if status == 'active':
        return _render_unavailable(fallback, 'active')

    return _render_unavailable(fallback, 'full')

# ============================================================
# ROUTES
# ============================================================

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
            return render_template(_JOIN_TEMPLATE)

        join_code = request.form.get('join_code', '').strip().upper()
        if not join_code:
            flash('Please enter a join code.', 'error')
            return render_template(_JOIN_TEMPLATE)

        join_code = join_code.replace(' ', '')

        quiz = get_active_live_quiz(join_code)
        if not quiz:
            return _resolve_non_joinable(join_code)

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
            return render_template(_JOIN_TEMPLATE)

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
                fallback = _lookup_quiz_any_status(join_code) or quiz
                return _render_unavailable(fallback, 'full')
            if code == 'already_joined':
                flash('You have already joined this quiz.', 'info')
                return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))
            flash('Failed to join quiz.', 'error')
            return render_template(_JOIN_TEMPLATE)

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

    return render_template(_JOIN_TEMPLATE)


@live_quiz_bp.route('/j/<code>', methods=['GET'])
def direct_join(code):
    """
    Direct join link: /live-quiz/j/<code>

    Not logged in → bounce to /login with next=<this URL>.

    States
    ------
    · code unknown            → join_unavailable.html (not_found)
    · quiz finished           → redirect to results
    · quiz active, not member → join_unavailable.html (active)
    · quiz full               → join_unavailable.html (full)
    · grade-locked            → upgrade sheet (unchanged)
    · normal join             → waiting room
    """
    if 'user_id' not in session:
        return redirect(url_for('auth.login', next=request.url))

    code_norm = (code or '').strip().upper().replace(' ', '')
    if not code_norm:
        flash('Invalid join code.', 'error')
        return redirect(url_for('live_quiz.join'))

    user_id = session['user_id']
    user_tier = get_current_user_tier()

    # ── Try the joinable lookup first (happy path). ──
    quiz = get_active_live_quiz(code_norm)

    if not quiz:
        return _resolve_non_joinable(code_norm)

    quiz_status = (quiz.get('status') or '').strip().lower()
    
    # ── Finished: get_active_live_quiz only returns waiting /
    #    scheduled, so this branch is defensive. Some deployments
    #    do return finished rows; handle it the same way as the
    #    fallback path — show the page, not a redirect. ──
    if quiz_status == 'finished':
        return _render_unavailable(quiz, 'finished')

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
        p_status = participant.get('status')
        if p_status == 'left':
            if quiz_status in ('waiting', 'scheduled'):
                if _rejoin_if_left(quiz['id'], user_id):
                    return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))
                flash('Failed to rejoin. Please try again.', 'error')
                return redirect(url_for('live_quiz.lobby'))
            flash('Cannot rejoin an active quiz.', 'error')
            return redirect(url_for('live_quiz.lobby'))
        return redirect(url_for('live_quiz.waiting_room', quiz_id=quiz['id']))

    if quiz_status == 'active':
        return _render_unavailable(quiz, 'active')

    try:
        current_count = int(quiz.get('participant_count') or 0)
    except (TypeError, ValueError):
        current_count = 0
    try:
        max_count = int(quiz.get('max_participants') or 50)
    except (TypeError, ValueError):
        max_count = 50
    if current_count >= max_count:
        return _render_unavailable(quiz, 'full')

    # ── Regular join flow ──
    question_ids = quiz.get('question_ids', []) or []
    ok, reason_code = _atomic_add_participant(
        quiz['id'], user_id, question_ids, max_count,
    )
    if not ok:
        if reason_code == 'full':
            fallback = _lookup_quiz_any_status(code_norm) or quiz
            return _render_unavailable(fallback, 'full')
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