# blueprints/pdfs_bp.py
import os
from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort, send_file, Response, jsonify,
)
from db import (
    get_all_pdfs, get_main_pdf_count, get_pdf_by_code, get_pdf_by_id,
    increment_pdf_view, increment_pdf_view_by_code,
    get_pdf_distinct_subjects, get_pdf_distinct_classes,
    get_pdf_distinct_curricula,
    save_pdf_for_user, unsave_pdf_for_user, is_pdf_saved,
    get_user_saved_pdf_ids, count_user_saved_pdfs,
    create_pdf_report, user_reported_pdf, get_user_reported_pdf_ids,
)
from services.tier_service import (
    can_access_premium_resources, get_user_tier, get_feature_level,
    get_saved_content_limit,
)
from bot.utils import get_bot
from bot.db import get_bot_pdf_by_code
from config import Config
from history_logger import add_history_entry
import requests
import logging

logger = logging.getLogger(__name__)

pdfs_bp = Blueprint('pdfs', __name__, url_prefix='/pdfs')

PER_PAGE = 50
_VALID_SORTS = {'newest', 'oldest', 'popular', 'title_asc', 'title_desc'}


# ============================================================
# GUEST ATTEMPT LOGGING
# ============================================================

def _log_guest_attempt(action: str, pdf=None, pdf_code=None):
    """
    Record when a logged-out user tries to perform a gated action.
    Best-effort — never raises.
    """
    try:
        from activity_logger import log_activity
        meta = {'action': action}
        if pdf:
            meta['pdf_id'] = pdf.get('id')
            meta['pdf_code'] = pdf.get('code')
            meta['title'] = pdf.get('title')
            meta['subject'] = pdf.get('subject')
        elif pdf_code:
            meta['pdf_code'] = pdf_code

        msg = f"Guest attempted: {action}"
        if pdf_code:
            msg += f" ({pdf_code})"

        log_activity(
            activity_type='pdf_guest_attempt',
            message=msg,
            severity='info',
            metadata=meta,
            ip_address=request.headers.get('X-Forwarded-For',
                                            request.remote_addr or '').split(',')[0].strip(),
            user_agent=request.headers.get('User-Agent', '')[:200],
        )
    except Exception:
        # Never let logging break the redirect
        pass


def _report_reasons():
    return [
        ('wrong_file',    'Wrong file'),
        ('wrong_metadata','Wrong title, subject, or class'),
        ('broken_file',   'Broken or unreadable file'),
        ('duplicate',     'Duplicate PDF'),
        ('inappropriate', 'Inappropriate content'),
        ('other',         'Other'),
    ]


# ============================================================
# LIST
# ============================================================

@pdfs_bp.route('/')
def list_pdfs():
    subject_filter    = (request.args.get('subject') or '').strip()
    class_filter      = (request.args.get('class') or '').strip()
    curriculum_filter = (request.args.get('curriculum') or '').strip()
    search_query      = (request.args.get('search') or '').strip()
    saved_only        = request.args.get('saved') == '1'

    sort = (request.args.get('sort') or 'newest').strip()
    if sort not in _VALID_SORTS:
        sort = 'newest'

    try:
        page = int(request.args.get('page') or 1)
    except (ValueError, TypeError):
        page = 1
    if page < 1:
        page = 1

    user_id = session.get('user_id')
    if user_id:
        user_tier = get_user_tier(user_id)
        search_level = get_feature_level("resource_search", user_id=user_id)
        can_access_premium = can_access_premium_resources()
    else:
        user_tier = 'free'
        search_level = 0
        can_access_premium = False
        saved_only = False   # guests can't use the saved view

    effective_search     = search_query     if search_level > 0  else ''
    effective_subject    = subject_filter   if search_level >= 1 else ''
    effective_curriculum = curriculum_filter if search_level >= 2 else ''
    effective_class      = class_filter     if search_level >= 2 else ''

    # Per-user save + report state — computed up front so the saved
    # view can filter against it.
    saved_ids    = set()
    reported_ids = set()
    if user_id:
        try:
            saved_ids = get_user_saved_pdf_ids(user_id)
        except Exception:
            saved_ids = set()
        try:
            reported_ids = get_user_reported_pdf_ids(user_id)
        except Exception:
            reported_ids = set()

    # ─── Branch A: saved-only view ──────────────────────────────
    if saved_only:
        all_saved = _list_saved_pdfs(
            saved_ids=saved_ids,
            search=effective_search,
            subject=effective_subject,
            curriculum=effective_curriculum,
            class_filter=effective_class,
            sort=sort,
        )
        total = len(all_saved)
        total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * PER_PAGE
        pdfs = all_saved[offset:offset + PER_PAGE]

    # ─── Branch B: normal view (unchanged) ──────────────────────
    else:
        total = get_main_pdf_count(
            search=effective_search,
            subject=effective_subject,
            curriculum=effective_curriculum,
            class_filter=effective_class,
        )
        total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * PER_PAGE

        pdfs = get_all_pdfs(
            limit=PER_PAGE,
            offset=offset,
            search=effective_search,
            subject=effective_subject,
            curriculum=effective_curriculum,
            class_filter=effective_class,
            sort=sort,
        )

    subjects  = get_pdf_distinct_subjects()  if search_level >= 1 else []
    classes   = get_pdf_distinct_classes()   if search_level >= 2 else []
    curricula = get_pdf_distinct_curricula() if search_level >= 2 else []

    if total == 0:
        range_start, range_end = 0, 0
    else:
        range_start = offset + 1
        range_end = min(offset + PER_PAGE, total)

    return render_template(
        'dashboard/pdfs.html',
        pdfs=pdfs,
        subjects=subjects,
        classes=classes,
        curricula=curricula,
        subject_filter=effective_subject,
        class_filter=effective_class,
        curriculum_filter=effective_curriculum,
        search_query=effective_search,
        search_level=search_level,
        user_tier=user_tier,
        can_access_premium=can_access_premium,
        is_logged_in=bool(user_id),
        sort=sort,
        page=page,
        per_page=PER_PAGE,
        total=total,
        total_pages=total_pages,
        range_start=range_start,
        range_end=range_end,
        saved_pdf_ids=saved_ids,
        reported_pdf_ids=reported_ids,
        report_reasons=_report_reasons(),
        saved_filter=saved_only,
    )


def _list_saved_pdfs(saved_ids, search, subject, curriculum,
                     class_filter, sort):
    """
    Return every saved PDF belonging to the user, filtered and sorted
    the same way the normal view does. Caller paginates.

    Saved sets are tier-limited (a few dozen rows at most), so
    iterating them and hitting get_pdf_by_id() is cheap.
    """
    if not saved_ids:
        return []

    items = []
    for pid in saved_ids:
        try:
            p = get_pdf_by_id(pid)
        except Exception:
            continue
        if not p:
            continue
        if subject and (p.get('subject') or '') != subject:
            continue
        if curriculum and (p.get('curriculum') or '') != curriculum:
            continue
        if class_filter and (p.get('class') or '') != class_filter:
            continue
        if search:
            hay = ' '.join([
                p.get('title') or '',
                p.get('code') or '',
                p.get('subject') or '',
            ]).lower()
            if search.lower() not in hay:
                continue
        items.append(p)

    if sort == 'popular':
        items.sort(key=lambda x: x.get('view_count') or 0, reverse=True)
    elif sort == 'title_asc':
        items.sort(key=lambda x: (x.get('title') or '').lower())
    elif sort == 'title_desc':
        items.sort(key=lambda x: (x.get('title') or '').lower(), reverse=True)
    elif sort == 'oldest':
        items.sort(key=lambda x: x.get('uploaded_at') or '')
    else:  # newest
        items.sort(key=lambda x: x.get('uploaded_at') or '', reverse=True)

    return items


# ============================================================
# VIEW
# ============================================================

@pdfs_bp.route('/view/<pdf_id>')
def view_pdf(pdf_id):
    if 'user_id' not in session:
        _log_guest_attempt('view', pdf_code=request.args.get('code'))
        flash('Please login to view PDFs.', 'warning')
        return redirect(url_for('auth.login', next=request.url))

    user_id = session['user_id']
    pdf = get_pdf_by_id(pdf_id)
    if not pdf:
        flash('PDF not found.', 'error')
        return redirect(url_for('pdfs.list_pdfs'))

    if pdf.get('is_premium', 0) and not can_access_premium_resources():
        flash('This is a premium resource. Upgrade to access it.', 'error')
        return redirect(url_for('pdfs.list_pdfs'))

    new_count = increment_pdf_view(pdf_id)
    if new_count is not None:
        pdf['view_count'] = new_count

    add_history_entry(
        user_id=user_id,
        entry_type='pdf_view',
        action='viewed',
        metadata={
            'title': pdf['title'],
            'subject': pdf.get('subject'),
            'code': pdf['code'],
        }
    )

    user_tier = get_user_tier(user_id)
    return render_template('dashboard/pdf_view.html', pdf=pdf, user_tier=user_tier)


# ============================================================
# DOWNLOAD
# ============================================================

@pdfs_bp.route('/download/<pdf_id>')
def download_pdf(pdf_id):
    if 'user_id' not in session:
        _log_guest_attempt('download', pdf_code=request.args.get('code'))
        flash('Please login to download PDFs.', 'warning')
        return redirect(url_for('auth.login', next=request.url))

    user_id = session['user_id']
    user_tier = get_user_tier(user_id)

    pdf = get_pdf_by_id(pdf_id)
    if not pdf:
        abort(404)

    if pdf.get('is_premium', 0) and not can_access_premium_resources():
        flash('This is a premium resource. Upgrade to access it.', 'error')
        return redirect(url_for('pdfs.list_pdfs'))

    increment_pdf_view(pdf_id)

    add_history_entry(
        user_id=user_id,
        entry_type='pdf_download',
        action='downloaded',
        metadata={
            'title': pdf['title'],
            'subject': pdf.get('subject'),
            'code': pdf['code'],
        }
    )

    if user_tier == 'pro' and pdf.get('file_url'):
        file_path = pdf['file_url']
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True,
                             download_name=pdf.get('title', 'document.pdf'))
        return redirect(pdf['file_url'])

    return redirect(url_for('pdfs.telegram_download', code=pdf['code']))


@pdfs_bp.route('/telegram/<code>')
def telegram_download(code):
    pdf = get_pdf_by_code(code)
    if not pdf:
        flash('PDF not found.', 'error')
        return redirect(url_for('pdfs.list_pdfs'))

    if 'user_id' not in session:
        _log_guest_attempt('telegram_get', pdf=pdf)

    increment_pdf_view_by_code(code)

    if 'user_id' in session:
        add_history_entry(
            user_id=session['user_id'],
            entry_type='pdf_download',
            action='downloaded',
            metadata={
                'title': pdf['title'],
                'subject': pdf.get('subject'),
                'code': pdf['code'],
            }
        )

    bot_username = Config.TELEGRAM_BOT_USERNAME or 'nuunplatform_bot'
    telegram_link = f"https://t.me/{bot_username}?start={code}"
    return redirect(telegram_link)


# ============================================================
# STREAM / PREVIEW
# ============================================================

@pdfs_bp.route('/stream/<code>')
def stream_pdf(code):
    if 'user_id' not in session:
        _log_guest_attempt('stream', pdf_code=code)
        return jsonify({'error': 'Please login first.'}), 401

    user_id = session['user_id']
    user_tier = get_user_tier(user_id)

    if user_tier not in ['premium', 'pro']:
        return jsonify({'error': 'Upgrade to access this feature.'}), 403

    main_pdf = get_pdf_by_code(code)
    if not main_pdf:
        return jsonify({'error': 'PDF not found'}), 404

    if main_pdf.get('is_premium', 0) and not can_access_premium_resources():
        return jsonify({'error': 'Premium content. Upgrade to access.'}), 403

    bot_pdf = get_bot_pdf_by_code(code)
    if not bot_pdf:
        return jsonify({'error': 'PDF not available in Telegram storage.'}), 404

    increment_pdf_view_by_code(code)

    try:
        bot = get_bot()
        file_info = bot.get_file(bot_pdf['file_id'])
        file_path = file_info.file_path
        token = Config.TELEGRAM_BOT_TOKEN
        url = f"https://api.telegram.org/file/bot{token}/{file_path}"

        response = requests.get(url, stream=True, timeout=30)
        if response.status_code != 200:
            logger.error(f"Telegram file download failed: {response.status_code}")
            return jsonify({'error': 'Failed to retrieve PDF from Telegram.'}), 502

        return Response(
            response.iter_content(chunk_size=65536),
            content_type='application/pdf',
            headers={
                'Content-Disposition':
                    f'inline; filename="{bot_pdf.get("title", "document.pdf")}"',
                'Cache-Control': 'no-store',
            }
        )
    except Exception as e:
        logger.error(f"Stream error: {e}")
        return jsonify({'error': 'Failed to stream PDF.'}), 500


@pdfs_bp.route('/preview/<code>')
def preview_telegram(code):
    return stream_pdf(code)


# ============================================================
# SAVE / UNSAVE
# ============================================================

@pdfs_bp.route('/<int:pdf_id>/save', methods=['POST'])
def save_pdf(pdf_id):
    if 'user_id' not in session:
        _log_guest_attempt('save', pdf_code=request.form.get('code'))
        return jsonify({'error': 'Please login first.', 'reason': 'login'}), 401

    user_id = session['user_id']
    pdf = get_pdf_by_id(pdf_id)
    if not pdf:
        return jsonify({'error': 'PDF not found'}), 404

    already = is_pdf_saved(user_id, pdf_id)
    if not already:
        limit = get_saved_content_limit(user_id)
        if limit is not None:
            current = count_user_saved_pdfs(user_id)
            if current >= limit:
                return jsonify({
                    'error': 'Save limit reached',
                    'reason': 'quota',
                    'limit': limit,
                    'current': current,
                }), 429

    if not save_pdf_for_user(user_id, pdf_id):
        return jsonify({'error': 'Could not save'}), 500

    if not already:
        add_history_entry(
            user_id=user_id,
            entry_type='save',
            action='saved',
            entry_id=pdf_id,
            metadata={'title': pdf['title'], 'code': pdf['code'], 'type': 'pdf'},
        )

    return jsonify({'success': True, 'saved': True})


@pdfs_bp.route('/<int:pdf_id>/unsave', methods=['POST'])
def unsave_pdf(pdf_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Please login first.', 'reason': 'login'}), 401

    user_id = session['user_id']
    if not unsave_pdf_for_user(user_id, pdf_id):
        return jsonify({'error': 'Could not unsave'}), 500

    add_history_entry(
        user_id=user_id,
        entry_type='save',
        action='unsaved',
        entry_id=pdf_id,
        metadata={'type': 'pdf'},
    )
    return jsonify({'success': True, 'saved': False})


# ============================================================
# REPORT
# ============================================================

@pdfs_bp.route('/<int:pdf_id>/report', methods=['POST'])
def report_pdf(pdf_id):
    if 'user_id' not in session:
        _log_guest_attempt('report', pdf_code=request.form.get('code'))
        return jsonify({'error': 'Please login first.', 'reason': 'login'}), 401

    user_id = session['user_id']
    data = request.get_json(silent=True) or {}
    reason = (data.get('reason') or '').strip()
    comment = (data.get('comment') or '').strip()[:500]

    valid = {r[0] for r in _report_reasons()}
    if reason not in valid:
        return jsonify({'error': 'Please select a valid reason'}), 400

    pdf = get_pdf_by_id(pdf_id)
    if not pdf:
        return jsonify({'error': 'PDF not found'}), 404

    if user_reported_pdf(user_id, pdf_id):
        return jsonify({'error': 'You have already reported this PDF.',
                        'reason': 'duplicate'}), 409

    if not create_pdf_report(user_id, pdf_id, reason, comment):
        return jsonify({'error': 'Could not submit report'}), 500

    add_history_entry(
        user_id=user_id,
        entry_type='report',
        action='reported',
        entry_id=pdf_id,
        metadata={'title': pdf['title'], 'code': pdf['code'], 'reason': reason},
    )

    # ── Notify admins ─────────────────────────────────────
    try:
        _notify_admins_about_report(user_id, pdf, reason, comment)
    except Exception as e:
        logger.warning(f"Failed to notify admins about pdf report: {e}")

    return jsonify({'success': True})


# ============================================================
# Admin notification helpers
# ============================================================

_REASON_LABELS = {
    'wrong_file':     'Wrong file',
    'wrong_metadata': 'Wrong title, subject, or class',
    'broken_file':    'Broken or unreadable file',
    'duplicate':      'Duplicate PDF',
    'inappropriate':  'Inappropriate content',
    'other':          'Other',
}


def _notify_admins_about_report(reporter_id, pdf, reason, comment):
    """
    Send an in-app notification to every admin, plus a Telegram DM to
    super admins with a rich markdown report.
    """
    from db import get_student_by_id

    reporter = get_student_by_id(reporter_id) or {}
    reporter_name = (
        f"{reporter.get('first_name', '')} {reporter.get('last_name', '')}"
    ).strip() or f"User #{reporter_id}"
    reporter_public = reporter.get('public_id') or '----'

    reason_label = _REASON_LABELS.get(reason, reason)

    title = f"🚩 PDF reported: {pdf.get('title', '')[:60]}"
    body = (
        f"{reporter_name} (@{reporter_public}) reported "
        f"\"{pdf.get('title', '')}\" ({pdf.get('code', '')}) — {reason_label}."
    )
    link = f"/admin/reports?type=pdf&id={pdf.get('id')}"

    # ── In-app notification to every admin ─────────────
    try:
        from db import execute_with_retry
        cursor = execute_with_retry(
            "SELECT id FROM students WHERE is_admin = 1"
        )
        admin_ids = [r['id'] for r in cursor.fetchall()]

        from services.notification_service import send_notification
        for aid in admin_ids:
            try:
                send_notification(
                    user_id=aid,
                    notification_type='question_report',  # reuse existing type
                    title=title,
                    body=body,
                    link=link,
                    icon='🚩',
                    force=True,
                )
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"In-app admin notify failed: {e}")

    # ── Telegram DM to super admins ────────────────────
    try:
        from services.telegram_notify import (
            notify_super_admins,
            build_markdown_document,
            make_report_filename,
            summary_row,
            truncate,
        )

        base_url = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')
        admin_url = None
        if base_url:
            admin_url = f"{base_url}/admin/reports?type=pdf&id={pdf.get('id')}"

        meta = {
            'type':        'pdf_report',
            'pdf_id':      pdf.get('id'),
            'pdf_code':    pdf.get('code'),
            'reporter_id': reporter_id,
            'reporter':    reporter_public,
            'reason':      reason,
        }

        # PDF details table
        pdf_table = '\n'.join([
            '| Field | Value |',
            '|:--|:--|',
            f"| Title | {pdf.get('title') or '—'} |",
            f"| Code | `{pdf.get('code') or '—'}` |",
            f"| Subject | {pdf.get('subject') or '—'} |",
            f"| Class | {pdf.get('class') or '—'} |",
            f"| Curriculum | {pdf.get('curriculum') or '—'} |",
            f"| Views | {pdf.get('view_count') or 0} |",
        ])

        # Reporter details table
        reporter_table = '\n'.join([
            '| Field | Value |',
            '|:--|:--|',
            f"| Name | {reporter_name} |",
            f"| Public ID | `{reporter_public}` |",
            f"| Phone | `{reporter.get('phone_number') or '—'}` |",
            f"| School | {reporter.get('school') or '—'} |",
            f"| Grade | {reporter.get('grade') or '—'} |",
        ])

        # Report details
        report_lines = [
            f"**Reason:** {reason_label}",
        ]
        if comment:
            report_lines.append("")
            report_lines.append(f"**Comment:**")
            report_lines.append(truncate(comment, 500))

        actions = [
            '- [ ] Open the PDF and verify the metadata',
            '- [ ] Contact the reporter if more info is needed',
            '- [ ] Fix or dismiss the report',
        ]
        if admin_url:
            actions.append(f'- [ ] [Open admin →]({admin_url})')

        sections = [
            ('📄 Reported PDF', pdf_table),
            ('👤 Reporter', reporter_table),
            ('📝 Report details', '\n'.join(report_lines)),
            ('🛠️ Next Steps', '\n'.join(actions)),
        ]

        md_body = build_markdown_document(
            title=f"PDF report — {pdf.get('code', '')}",
            severity='warning',
            meta=meta,
            sections=sections,
            footer_id=f"PDF-{pdf.get('code') or 'UNKNOWN'}",
        )

        filename = make_report_filename('pdf_report', pdf.get('code') or 'unknown')

        summary = [
            summary_row('📄', 'PDF', truncate(pdf.get('title', ''), 60)),
            summary_row('🆔', 'Code', pdf.get('code') or '—'),
            summary_row('👤', 'Reporter', reporter_name),
            summary_row('❗', 'Reason', reason_label),
        ]

        notify_super_admins(
            event_type='pdf_report',
            title=f"PDF reported: {pdf.get('code', '')}",
            md_body=md_body,
            md_filename=filename,
            summary=summary,
            primary_url=admin_url,
            primary_url_label='Open admin panel',
            severity='warning',
            reference_id=f"PDF-{pdf.get('code') or 'UNKNOWN'}",
            icon='🚩',
        )
    except Exception as e:
        logger.warning(f"Telegram admin notify failed: {e}")