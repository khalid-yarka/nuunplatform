# ============================================================
# blueprints/live_quiz/routes_lobby.py
# ============================================================
# Lobby index, quiz listing, and the JSON join endpoint that the
# lobby cards use. Every quiz_id in the URL is declared as
# <int:quiz_id> so the state manager receives ints — not strings.

import logging

from flask import request, session, flash, redirect, url_for, jsonify, render_template

from db import (
    get_live_quiz_by_id,
    get_live_quiz_participant,
    get_live_quizzes_lobby,
    get_live_quiz_stats,
    can_join_live_quiz,
    get_user_active_quiz,
)
from utils import validate_csrf
from subjects_config import get_all_subjects
from services.tier_service import (
    can_create_live_quiz,
    get_current_user_tier,
)
from question_utils import DEFAULT_GRADE, normalize_grade
from cache import get_cache_manager, make_key

from . import live_quiz_bp
from ._shared import (
    _atomic_add_participant,
    _rejoin_if_left,
    is_grade_locked_for_viewer,
    ensure_participant_in_state,
    notify_creator_of_join,
)

logger = logging.getLogger(__name__)


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

    for q in quizzes:
        q['grade'] = q.get('grade') or DEFAULT_GRADE
        q['grade_locked'] = is_grade_locked_for_viewer(q, user_id, user_tier)

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


@live_quiz_bp.route('/lobby/join/<int:quiz_id>', methods=['POST'])
def lobby_join(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Please login first'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    if is_grade_locked_for_viewer(quiz, user_id, get_current_user_tier()):
        return jsonify({
            'error': 'This quiz is for a different grade. '
                     'Upgrade to join quizzes of any grade.',
            'reason': 'grade_mismatch',
            'required_tier': 'premium',
            'required_grade': normalize_grade(quiz.get('grade')),
        }), 403

    existing = get_live_quiz_participant(quiz_id, user_id)
    if existing:
        if existing.get('status') == 'left':
            if _rejoin_if_left(quiz_id, user_id):
                return jsonify({
                    'success': True,
                    'redirect': url_for('live_quiz.waiting_room', quiz_id=quiz_id),
                })
            return jsonify({'error': 'Failed to rejoin quiz'}), 500
        return jsonify({
            'success': True,
            'redirect': url_for('live_quiz.waiting_room', quiz_id=quiz_id),
        })

    can_join, reason = can_join_live_quiz(quiz_id, user_id)
    if not can_join:
        return jsonify({'error': reason}), 400

    # Atomic add: count + insert inside one transaction.
    question_ids = quiz.get('question_ids', []) or []
    max_participants = quiz.get('max_participants', 50)
    ok, code = _atomic_add_participant(quiz_id, user_id, question_ids, max_participants)
    if not ok:
        if code == 'full':
            return jsonify({'error': 'This quiz is full.'}), 400
        if code == 'already_joined':
            return jsonify({
                'success': True,
                'redirect': url_for('live_quiz.waiting_room', quiz_id=quiz_id),
            })
        return jsonify({'error': 'Failed to join quiz'}), 500

    # Bring the user into the in-memory state. If either step
    # fails we surface a real error rather than redirecting into a
    # state that does not know the user exists.
    if not ensure_participant_in_state(quiz_id, user_id):
        return jsonify({'error': 'Quiz state could not be loaded'}), 500

    creator_id = quiz.get('creator_id')
    if creator_id and creator_id != user_id:
        cname = session.get('user_name') or 'Participant'
        qtitle = quiz.get('title') or 'Live Quiz'
        notify_creator_of_join(creator_id, cname, qtitle, quiz['id'])

    return jsonify({
        'success': True,
        'redirect': url_for('live_quiz.waiting_room', quiz_id=quiz_id),
    })


@live_quiz_bp.route('/available-count')
def available_count():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    from question_utils import VALID_GRADES
    from db import execute_with_retry

    subject_code = (request.args.get('subject') or '').strip()
    grade = (request.args.get('grade') or '').strip().upper()
    if grade not in VALID_GRADES:
        grade = DEFAULT_GRADE

    if not subject_code:
        return jsonify({
            'count': 0, 'grade': grade, 'subject': '',
            'breakdown': {}, 'total_subject': 0, 'error': None,
        })

    breakdown = {}
    total_subject = 0
    count = 0
    err_text = None

    try:
        cursor = execute_with_retry(
            "SELECT COUNT(*) AS c FROM questions "
            "WHERE subject_code = ? AND status = 'active' AND grade = ?",
            (subject_code, grade),
        )
        row = cursor.fetchone()
        count = int(row['c']) if row else 0
    except Exception as e:
        err_text = str(e)
        logger.warning(f"available_count exact query failed: {e}")

    try:
        cur = execute_with_retry(
            "SELECT grade, COUNT(*) AS c FROM questions "
            "WHERE subject_code = ? AND status = 'active' "
            "GROUP BY grade ORDER BY grade",
            (subject_code,),
        )
        for r in cur.fetchall():
            g = r['grade'] or '(empty)'
            n = int(r['c'])
            breakdown[g] = n
            total_subject += n
    except Exception as e:
        logger.warning(f"available_count fallback query failed: {e}")
        if err_text is None:
            err_text = str(e)

    return jsonify({
        'count': count,
        'grade': grade,
        'subject': subject_code,
        'breakdown': breakdown,
        'total_subject': total_subject,
        'error': err_text,
    })