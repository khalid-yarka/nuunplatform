# ============================================================
# blueprints/live_quiz/routes_lifecycle.py
# ============================================================
# Play page, leave/rejoin, delete, results, analysis, export.
# The delete route allows creator OR any admin on non-active
# quizzes — matching the lobby template's _can_delete rule.

import json
import logging
from io import StringIO
import csv

from flask import (
    request, session, flash, redirect, url_for, jsonify, render_template, Response,
)

from db import (
    get_live_quiz_by_id,
    get_live_quiz_with_subject,
    get_live_quiz_participant,
    get_live_quiz_participants,
    get_live_quiz_participants_with_names,
    get_question_by_id,
    update_live_quiz_participant,
    leave_live_quiz as db_leave_live_quiz,
    delete_live_quiz as db_delete_live_quiz,
    execute_with_retry,
    is_admin,
)
from utils import validate_csrf, get_somali_time_db
from services.tier_service import has_feature, get_current_user_tier
from services.notification_service import send_notification

from . import live_quiz_bp
from ._shared import (
    get_state_manager,
    invalidate_quiz_cache,
    is_grade_locked_for_viewer,
    _rejoin_if_left,
)
from .finalize import finalize_live_quiz

logger = logging.getLogger(__name__)


@live_quiz_bp.route('/play/<int:quiz_id>')
def play(quiz_id):
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    quiz = get_live_quiz_with_subject(quiz_id)
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


@live_quiz_bp.route('/leave/<int:quiz_id>', methods=['POST'])
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


@live_quiz_bp.route('/rejoin/<int:quiz_id>', methods=['POST'])
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

    if is_grade_locked_for_viewer(quiz, user_id, get_current_user_tier()):
        return jsonify({
            'error': 'This quiz is for a different grade.',
            'reason': 'grade_mismatch',
        }), 403

    participant = get_live_quiz_participant(quiz_id, user_id)
    if not participant or participant.get('status') != 'left':
        return jsonify({'error': 'You are not eligible to rejoin'}), 400

    if _rejoin_if_left(quiz_id, user_id):
        return jsonify({
            'success': True,
            'message': 'You have rejoined the quiz',
            'redirect': url_for('live_quiz.waiting_room', quiz_id=quiz_id),
        })

    return jsonify({'error': 'Failed to rejoin quiz'}), 500


@live_quiz_bp.route('/delete/<int:quiz_id>', methods=['POST'])
def delete_quiz(quiz_id):
    """
    Delete a competition.

    Allowed when:
      · The caller is the creator AND the quiz is not active, OR
      · The caller is any admin (super or normal) AND the quiz is
        not active, OR
      · The caller is a super admin (any status)

    Matches the lobby template's _can_delete rule so a normal admin
    sees the button and can actually act on it — no more
    "Permission denied" for a quiz the UI clearly offered.
    """
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    is_creator = (quiz['creator_id'] == user_id)
    is_active = (quiz['status'] == 'active')

    from services.admin.roles import is_super_admin
    super_admin = is_super_admin()
    normal_admin = is_admin(user_id)

    if not super_admin:
        if not is_creator and not normal_admin:
            return jsonify({'error': 'Permission denied'}), 403
        if is_active:
            return jsonify({
                'error': 'Cannot delete an active quiz. Contact a super admin.',
            }), 403

    payload = request.get_json(silent=True) or {}
    announce = True
    if isinstance(payload, dict) and 'announce' in payload:
        announce = bool(payload.get('announce'))

    try:
        execute_with_retry("""
            INSERT INTO admin_audit_log
                (actor_id, actor_role, action, target_type, target_id,
                 before_value, note, severity, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'warning', ?)
        """, (
            user_id,
            'super' if super_admin else ('admin' if normal_admin else 'user'),
            'live_quiz.force_cancel' if (super_admin and is_active) else 'live_quiz.delete',
            'live_quiz',
            quiz_id,
            json.dumps({
                'title': quiz.get('title'),
                'status': quiz.get('status'),
                'creator_id': quiz.get('creator_id'),
                'announce': announce,
                'by_super_admin': super_admin,
                'by_normal_admin': bool(normal_admin and not super_admin),
            }),
            f'Deleted live quiz {quiz_id}',
            get_somali_time_db(),
        ), commit=True)
    except Exception as e:
        logger.warning(f"audit write for live_quiz.delete failed: {e}")

    if announce:
        try:
            title = '🚫 Tartanku waa la joojiyay'
            body = f'"{quiz.get("title") or "Live Quiz"}" waa la joojiyay. Ku soo laabo hoolka.'
            execute_with_retry("""
                INSERT INTO notifications
                    (user_id, type, title, body, link, icon, is_read, created_at)
                SELECT lqp.student_id, ?, ?, ?, ?, ?, 0, ?
                FROM live_quiz_participants lqp
                WHERE lqp.quiz_id = ? AND lqp.status != 'left'
            """, (
                'live_quiz_cancelled',
                title,
                body,
                '/live-quiz/lobby',
                '🚫',
                get_somali_time_db(),
                quiz_id,
            ), commit=True)
        except Exception as e:
            logger.warning(f"cancel notification failed for quiz {quiz_id}: {e}")

    manager = get_state_manager()
    manager.delete_quiz(quiz_id)

    try:
        execute_with_retry("DELETE FROM live_quiz_events WHERE quiz_id = ?",
                           (quiz_id,), commit=True)
        execute_with_retry("DELETE FROM live_quiz_checkpoints WHERE quiz_id = ?",
                           (quiz_id,), commit=True)
    except Exception as e:
        logger.warning(f"cascade delete for quiz {quiz_id} failed: {e}")

    success = db_delete_live_quiz(quiz_id)
    if success:
        invalidate_quiz_cache(quiz_id)
        return jsonify({
            'success': True,
            'message': 'Quiz deleted',
            'was_active': is_active,
            'announced': announce,
        })

    return jsonify({'error': 'Failed to delete quiz'}), 500


@live_quiz_bp.route('/results/<int:quiz_id>')
def results(quiz_id):
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    quiz = get_live_quiz_with_subject(quiz_id)
    if not quiz:
        flash('Quiz not found.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    if quiz['status'] != 'finished':
        finalize_live_quiz(quiz_id)
        quiz = get_live_quiz_with_subject(quiz_id)
        if not quiz:
            flash('Quiz not found.', 'error')
            return redirect(url_for('live_quiz.lobby'))

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


@live_quiz_bp.route('/analysis/<int:quiz_id>')
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
        return jsonify({'error': 'Upgrade to Premium to access host analytics.'}), 403

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


@live_quiz_bp.route('/export/<int:quiz_id>')
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