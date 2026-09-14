# blueprints/interactions_bp.py
# Like / Save / Report endpoints.
#
# Reports dispatch to super admins on Telegram as:
#   1. Short HTML message with summary rows
#   2. Full markdown .md report attached
#
# The DB notification (create_notification_for_all_users) is kept as
# a secondary best-effort so admins still see the report in the in-app
# bell even if Telegram is down.

from flask import Blueprint, request, session, jsonify
from functools import wraps
import logging
import traceback

from db import (
    get_question_by_id,
    get_student_by_id,
    create_notification_for_all_users,
    execute_with_retry,
)
from utils import validate_csrf
from services.tier_service import get_saved_content_limit
from history_logger import add_history_entry
from config import Config

logger = logging.getLogger(__name__)

interactions_bp = Blueprint('interactions', __name__, url_prefix='/api/interaction')


# ============================================
# GUARDS / HELPERS
# ============================================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Please login first.'}), 401
        return f(*args, **kwargs)
    return decorated


def ensure_quiz_session():
    if 'quiz' not in session:
        session['quiz'] = {
            'reactions': {'likes': [], 'saves': [], 'reports': {}},
        }
        session.modified = True
    elif 'reactions' not in session['quiz']:
        session['quiz']['reactions'] = {'likes': [], 'saves': [], 'reports': {}}
        session.modified = True


# ============================================
# TELEGRAM DISPATCH FOR REPORTS
# ============================================

_REASON_LABELS = {
    'incorrect': 'Incorrect Answer',
    'inappropriate': 'Inappropriate Content',
    'spam': 'Spam or Irrelevant',
    'duplicate': 'Duplicate Question',
    'other': 'Other',
}


def _reason_label(code: str) -> str:
    return _REASON_LABELS.get((code or '').lower(), code or 'Unspecified')


def _dispatch_report_to_telegram(
    report_id,
    user_id,
    user_name,
    user_public_id,
    question_id,
    question_text,
    subject_code,
    reason_code,
    comment,
):
    """
    Send the report to super admins on Telegram.
    Never raises. Returns True if delivered to at least one recipient.
    """
    try:
        from services.telegram_notify import (
            notify_super_admins,
            build_markdown_document,
            make_report_filename,
            summary_row,
            truncate,
        )
    except Exception as e:
        logger.error(f"report telegram: import failed: {e}")
        return False

    base_url = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')
    admin_url = f"{base_url}/admin/reports" if base_url else None

    reason_label = _reason_label(reason_code)

    meta = {
        'type': 'question-report',
        'report_id': report_id,
        'question_id': question_id,
        'subject_code': subject_code,
        'reason': reason_code,
        'reporter_user_id': user_id,
        'reporter_public_id': user_public_id,
    }

    sections = []

    # Reporter
    reporter_table = '\n'.join([
        '| Field | Value |',
        '|:--|:--|',
        f'| Name | {user_name or "Unknown"} |',
        f'| Public ID | `{user_public_id or "----"}` |',
        f'| User ID | `{user_id or "-"}` |',
    ])
    sections.append(('👤 Reporter', reporter_table))

    # Report details
    details_table = '\n'.join([
        '| Field | Value |',
        '|:--|:--|',
        f'| Report ID | `{report_id if report_id is not None else "-"}` |',
        f'| Reason | `{reason_label}` |',
        f'| Question ID | `{question_id}` |',
        f'| Subject | `{subject_code or "-"}` |',
    ])
    sections.append(('🎯 Report Details', details_table))

    # Question text
    q_text = (question_text or '(question not found)').strip()
    sections.append(('📝 Question', f'```text\n{q_text}\n```'))

    # Comment
    if comment:
        sections.append(('💭 Reporter Comment', f'> {comment}'))

    # Suggested actions
    actions = [
        f'- [ ] Review question `#{question_id}`',
        f'- [ ] Verify the report: **{reason_label}**',
        '- [ ] Resolve or dismiss in the admin panel',
    ]
    if admin_url:
        actions.append(f'- [ ] [Open Reports panel]({admin_url})')
    sections.append(('🛠️ Suggested Actions', '\n'.join(actions)))

    try:
        md_body = build_markdown_document(
            title=f'Question Report — {reason_label}',
            severity='warning',
            meta=meta,
            sections=sections,
            footer_id=f'REP-{report_id}' if report_id is not None else 'REP',
        )
    except Exception as e:
        logger.error(f"report telegram: markdown build failed: {e}", exc_info=True)
        return False

    filename = make_report_filename('question_report', str(report_id) if report_id is not None else None)

    summary = [
        summary_row('🚩', 'Reason', reason_label),
        summary_row('📚', 'Subject', subject_code or '-'),
        summary_row('👤', 'Reporter', f'{user_name or "Unknown"} (#{user_public_id or "----"})'),
    ]
    if comment:
        summary.append(summary_row('💭', 'Comment', truncate(comment, 120)))

    try:
        result = notify_super_admins(
            event_type='question_report',
            title=f'Question Report — {reason_label}',
            md_body=md_body,
            md_filename=filename,
            summary=summary,
            primary_url=admin_url,
            primary_url_label='Open Reports panel',
            severity='warning',
            reference_id=f'REP-{report_id}' if report_id is not None else None,
        )
        return result.get('sent', 0) > 0
    except Exception as e:
        logger.error(f"report telegram: dispatch failed: {e}", exc_info=True)
        return False


# ============================================
# ENDPOINTS
# ============================================

@interactions_bp.route('/like', methods=['POST'])
@login_required
def like():
    try:
        if not validate_csrf():
            return jsonify({'error': 'CSRF token missing or invalid'}), 403

        data = request.get_json() or {}
        question_id = data.get('question_id')
        if not question_id:
            return jsonify({'error': 'Missing question_id'}), 400

        q = get_question_by_id(question_id)
        if not q:
            return jsonify({'error': 'Question not found'}), 404

        ensure_quiz_session()

        likes = session['quiz']['reactions']['likes']
        if question_id in likes:
            likes.remove(question_id)
            liked = False
        else:
            likes.append(question_id)
            liked = True
        session['quiz']['reactions']['likes'] = likes
        session.modified = True

        add_history_entry(
            user_id=session['user_id'],
            entry_type='like',
            action='liked' if liked else 'unliked',
            entry_id=question_id,
            metadata={'question_id': question_id},
        )

        return jsonify({'liked': liked})

    except Exception as e:
        logger.error(f"Like endpoint error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Internal server error. Please try again later.'}), 500


@interactions_bp.route('/save', methods=['POST'])
@login_required
def save():
    try:
        if not validate_csrf():
            return jsonify({'error': 'CSRF token missing or invalid'}), 403

        data = request.get_json() or {}
        question_id = data.get('question_id')
        if not question_id:
            return jsonify({'error': 'Missing question_id'}), 400

        q = get_question_by_id(question_id)
        if not q:
            return jsonify({'error': 'Question not found'}), 404

        # ---- Tier-based save quota ----
        # Only enforce when we are ADDING a new save (not removing).
        limit = get_saved_content_limit(session['user_id'])
        if limit is not None:
            existing = execute_with_retry(
                "SELECT id FROM question_interactions "
                "WHERE user_id = ? AND question_id = ? AND interaction_type = 'save'",
                (session['user_id'], question_id),
            ).fetchone()
            if not existing:
                cursor = execute_with_retry(
                    "SELECT COUNT(*) AS c FROM question_interactions "
                    "WHERE user_id = ? AND interaction_type = 'save'",
                    (session['user_id'],),
                )
                row = cursor.fetchone()
                current = int(row['c']) if row and row['c'] is not None else 0
                if current >= limit:
                    return jsonify({
                        'error': 'Save limit reached',
                        'limit': limit,
                        'current': current,
                    }), 429

        ensure_quiz_session()

        saves = session['quiz']['reactions']['saves']
        if question_id in saves:
            saves.remove(question_id)
            saved = False
        else:
            saves.append(question_id)
            saved = True
        session['quiz']['reactions']['saves'] = saves
        session.modified = True

        return jsonify({'saved': saved})

    except Exception as e:
        logger.error(f"Save endpoint error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Internal server error. Please try again later.'}), 500


@interactions_bp.route('/report', methods=['POST'])
@login_required
def report():
    try:
        if not validate_csrf():
            return jsonify({'error': 'CSRF token missing or invalid'}), 403

        data = request.get_json() or {}
        question_id = data.get('question_id')
        reason = (data.get('reason') or '').strip()
        comment = (data.get('comment') or '').strip()

        if not question_id or not reason:
            return jsonify({'error': 'Missing question_id or reason'}), 400

        q = get_question_by_id(question_id)
        if not q:
            return jsonify({'error': 'Question not found'}), 404

        ensure_quiz_session()

        reports = session['quiz']['reactions']['reports']
        if str(question_id) in reports:
            return jsonify({'error': 'You have already reported this question.'}), 400

        # ---- Persist ----
        cursor = execute_with_retry(
            """
            INSERT INTO question_interactions
                (user_id, question_id, interaction_type,
                 report_reason, report_comment, report_status)
            VALUES (?, ?, 'report', ?, ?, 'pending')
            """,
            (session['user_id'], question_id, reason, comment),
            commit=True,
        )
        report_id = getattr(cursor, 'lastrowid', None)

        reports[question_id] = {'reason': reason, 'comment': comment}
        session['quiz']['reactions']['reports'] = reports
        session.modified = True

        # ---- History ----
        add_history_entry(
            user_id=session['user_id'],
            entry_type='report',
            action='reported',
            entry_id=question_id,
            metadata={'question_id': question_id, 'reason': reason},
        )

        # ---- Gather reporter info ----
        student = get_student_by_id(session['user_id']) or {}
        user_name = (
            f"{student.get('first_name', '')} {student.get('last_name', '')}".strip()
            or 'Unknown'
        )
        user_public_id = student.get('public_id') or '----'
        question_text = (q.get('question_text') or '')[:400]
        subject_code = q.get('subject_code') or ''

        # ---- Telegram (primary) ----
        _dispatch_report_to_telegram(
            report_id=report_id,
            user_id=session['user_id'],
            user_name=user_name,
            user_public_id=user_public_id,
            question_id=question_id,
            question_text=question_text,
            subject_code=subject_code,
            reason_code=reason,
            comment=comment,
        )

        # ---- In-app DB notification (secondary, best-effort) ----
        try:
            create_notification_for_all_users(
                type='admin_report',
                title='⚠️ New Report',
                body=f'{user_name} reported a {subject_code} question: {question_text[:60]}',
                link='/admin/reports',
                icon='⚠️',
            )
        except Exception as e:
            logger.warning(f"report: DB notification failed (non-fatal): {e}")

        return jsonify({
            'success': True,
            'message': 'Report submitted. We will review it shortly.',
        })

    except Exception as e:
        logger.error(f"Report endpoint error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Internal server error. Please try again later.'}), 500