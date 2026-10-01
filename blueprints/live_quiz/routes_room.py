# ============================================================
# blueprints/live_quiz/routes_room.py
# ============================================================
# Waiting room: participants list, chat (fetch + send), ping
# send, and the ready toggle. All quiz_ids are <int:quiz_id>.

import json
import logging

from flask import request, session, flash, redirect, url_for, jsonify, render_template

from db import (
    get_live_quiz_by_id,
    get_live_quiz_with_subject,
    get_live_quiz_participant,
    get_active_participants,
    count_chat_messages,
    insert_chat_message,
    get_chat_messages_since,
    update_participant_ready,
)
from config import Config
from utils import validate_csrf, format_somali_time
from services.tier_service import get_current_user_tier

from datetime import datetime, timezone

from . import live_quiz_bp
from ._shared import get_state_manager
from .chat import (
    _chat_rate_ok,
    _can_send_chat,
    _ping_cooldown_remaining,
    _mark_ping_sent,
    _dispatch_ping_push,
)

logger = logging.getLogger(__name__)


@live_quiz_bp.route('/waiting-room/<int:quiz_id>')
def waiting_room(quiz_id):
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    quiz = get_live_quiz_with_subject(quiz_id)
    if not quiz:
        flash('Quiz not found.', 'error')
        return redirect(url_for('live_quiz.lobby'))

    if quiz['status'] in ('waiting', 'scheduled'):
        try:
            existing = get_live_quiz_participant(quiz_id, user_id)
            if existing and existing.get('status') == 'left':
                from ._shared import _rejoin_if_left
                _rejoin_if_left(quiz_id, user_id)
        except Exception as e:
            logger.warning(
                f"auto-rejoin attempt failed for user {user_id} in quiz {quiz_id}: {e}"
            )

    # Ensure the in-memory state knows about this participant. If
    # the memory was rebuilt from a checkpoint that predates the
    # JOIN, this hydrates them from the DB so /quiz-state polls no
    # longer return "not a participant".
    try:
        manager = get_state_manager()
        manager.ensure_participant_in_memory(quiz_id, user_id)
    except Exception as e:
        logger.warning(
            f"ensure_participant_in_memory failed for quiz {quiz_id} "
            f"user {user_id}: {e}"
        )

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
        active_count = quiz_state.get_active_count()
    else:
        participants = get_active_participants(quiz_id)
        active_count = len(participants)

    participant_count = active_count

    scheduled_start = quiz.get('scheduled_start')
    starts_in_seconds = 0
    scheduled_start_display = None
    if scheduled_start and quiz['status'] == 'scheduled':
        try:
            start_dt = datetime.fromisoformat(scheduled_start.replace('Z', '+00:00'))
            now_dt = datetime.now(timezone.utc)
            diff = (start_dt - now_dt).total_seconds()
            starts_in_seconds = max(0, int(diff))
            scheduled_start_display = format_somali_time(start_dt)
        except Exception:
            pass

    chat_max_length = Config.LIVE_QUIZ_CHAT_MAX_LENGTH
    chat_can_send   = (quiz['status'] in ('waiting', 'scheduled')
                       and _can_send_chat(user_id))
    chat_count      = count_chat_messages(quiz_id)

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
        chat_max_length=chat_max_length,
        chat_can_send=chat_can_send,
        chat_count=chat_count,
    )


@live_quiz_bp.route('/waiting-room/participants/<int:quiz_id>')
def waiting_room_participants(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    manager = get_state_manager()
    manager.ensure_quiz_in_memory(quiz_id)
    quiz_state = manager.get_quiz(quiz_id)
    if quiz_state:
        participants = quiz_state.get_all_participants()
        active_count = quiz_state.get_active_count()
        return jsonify({
            'participants': participants,
            'count': active_count,
            'total': len(participants),
        })

    db_participants = get_active_participants(quiz_id)
    quiz_row = get_live_quiz_by_id(quiz_id)
    creator_id = quiz_row.get('creator_id') if quiz_row else None
    formatted = []
    for p in db_participants:
        formatted.append({
            'student_id': p['student_id'],
            'name': f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or 'Unknown',
            'public_id': p.get('public_id', '----'),
            'status': p.get('status', 'active'),
            'is_ready': p.get('is_ready', False),
            'is_creator': (p['student_id'] == creator_id),
        })
    return jsonify({
        'participants': formatted,
        'count': len(formatted),
        'total': len(formatted),
    })


@live_quiz_bp.route('/waiting-room/<int:quiz_id>/chat', methods=['GET'])
def waiting_room_chat_fetch(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    participant = get_live_quiz_participant(quiz_id, user_id)
    if not participant and quiz['creator_id'] != user_id:
        return jsonify({'error': 'Not a participant'}), 403

    try:
        since = int(request.args.get('since', 0))
    except (TypeError, ValueError):
        since = 0

    messages = get_chat_messages_since(quiz_id, since_id=since)

    chat_open = quiz['status'] in ('waiting', 'scheduled')
    can_send  = chat_open and _can_send_chat(user_id)
    is_creator = (quiz['creator_id'] == user_id)
    cooldown = _ping_cooldown_remaining(quiz_id) if is_creator else 0

    return jsonify({
        'messages':  messages,
        'chat_open': chat_open,
        'can_send':  can_send,
        'is_creator': is_creator,
        'ping_cooldown_remaining': cooldown,
        'total':     count_chat_messages(quiz_id),
    })


@live_quiz_bp.route('/waiting-room/<int:quiz_id>/chat', methods=['POST'])
def waiting_room_chat_send(quiz_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    participant = get_live_quiz_participant(quiz_id, user_id)
    if not participant and quiz['creator_id'] != user_id:
        return jsonify({'error': 'Not a participant'}), 403

    if quiz['status'] not in ('waiting', 'scheduled'):
        return jsonify({
            'error':  'Chat is closed — the quiz has started.',
            'reason': 'chat_closed',
        }), 409

    if not _can_send_chat(user_id):
        return jsonify({
            'error':  'Sending is disabled for your account.',
            'reason': 'sending_locked',
        }), 403

    data  = request.get_json(silent=True) or {}
    body  = (data.get('body') or '').strip()
    nonce = (data.get('nonce') or '').strip() or None

    if not body:
        return jsonify({'error': 'Empty message'}), 400
    if len(body) > Config.LIVE_QUIZ_CHAT_MAX_LENGTH:
        return jsonify({
            'error': f'Message too long (max {Config.LIVE_QUIZ_CHAT_MAX_LENGTH} chars)',
        }), 400

    if not _chat_rate_ok(user_id, Config.LIVE_QUIZ_CHAT_RATE_PER_MIN):
        return jsonify({
            'error':  'Slow down a bit.',
            'reason': 'rate_limited',
        }), 429

    msg_id = insert_chat_message(
        quiz_id, user_id, body, nonce=nonce,
        message_type='chat', ping_reason='',
    )
    if not msg_id:
        return jsonify({'error': 'Failed to save message'}), 500

    return jsonify({'success': True, 'id': msg_id})


@live_quiz_bp.route('/waiting-room/<int:quiz_id>/ping', methods=['POST'])
def waiting_room_ping_send(quiz_id):
    """Creator-only broadcast ping. Delivered via push + in-page banner."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not validate_csrf():
        return jsonify({'error': 'CSRF token missing or invalid'}), 403

    user_id = session['user_id']
    quiz = get_live_quiz_by_id(quiz_id)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    if quiz['creator_id'] != user_id:
        return jsonify({'error': 'Only the creator can ping'}), 403

    if quiz['status'] not in ('waiting', 'scheduled'):
        return jsonify({
            'error':  'Ping is only available in the waiting room.',
            'reason': 'chat_closed',
        }), 409

    remaining = _ping_cooldown_remaining(quiz_id)
    if remaining > 0:
        return jsonify({
            'error':  f'Please wait {remaining}s before the next ping.',
            'reason': 'cooldown',
            'remaining': remaining,
        }), 429

    data = request.get_json(silent=True) or {}
    body = (data.get('body') or '').strip()
    reason = (data.get('reason') or 'custom').strip()[:32]

    if not body:
        return jsonify({'error': 'Empty message'}), 400
    if len(body) > 120:
        return jsonify({'error': 'Ping message too long (max 120)'}), 400

    msg_id = insert_chat_message(
        quiz_id, user_id, body,
        nonce=None,
        message_type='ping',
        ping_reason=reason,
    )
    if not msg_id:
        return jsonify({'error': 'Failed to save ping'}), 500

    _mark_ping_sent(quiz_id)
    recipients = _dispatch_ping_push(quiz, user_id, body)

    return jsonify({
        'success': True,
        'id': msg_id,
        'recipients': recipients,
        'cooldown': 30,
    })


@live_quiz_bp.route('/toggle-ready/<int:quiz_id>', methods=['POST'])
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