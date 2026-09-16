# ============================================================
# blueprints/admin/content_bp.py
# Content domain — questions, PDFs (library / intake / staging).
#
# PDF flow (unified in the admin system):
#   Telegram bot uploads  →  pending_pdfs (bot.db)
#   Admin processes       →  pdfs         (bot.db)  ← "staging"
#   Super admin publishes →  pdfs         (main db) ← "library"
#
# Routes:
#   GET  /admin/questions                            → list
#   GET  /admin/questions/new                        → new form
#   POST /admin/questions/new                        → create
#   GET  /admin/questions/<id>/edit                  → edit form
#   POST /admin/questions/<id>/edit                  → update
#   POST /admin/questions/<id>/delete                → archive
#   GET  /admin/questions/broken                     → broken PDF links
#   GET  /admin/questions/hard                       → high miss-rate
#   POST /admin/questions/pdf-info                   → AJAX PDF lookup
#   GET  /admin/bulk-import                          → bulk import page
#   POST /admin/bulk-import                          → apply import
#   GET  /admin/bulk-template                        → download template
#   POST /admin/bulk-preview                         → AJAX preview
#
#   GET  /admin/pdfs                                 → workspace (library/intake/staging)
#   GET  /admin/pdfs/<id>/edit                       → edit library PDF
#   POST /admin/pdfs/<id>/edit                       → update library PDF
#   POST /admin/pdfs/<id>/delete                     → delete library PDF
#   GET  /admin/pdfs/broken                          → missing PDF codes
#   GET  /admin/pdfs/intake/<id>/process             → process pending
#   POST /admin/pdfs/intake/<id>/process             → fulfil pending
#   GET  /admin/pdfs/intake/<id>/preview             → stream pending file
#   GET  /admin/pdfs/staging/<id>/edit               → edit staged PDF
#   POST /admin/pdfs/staging/<id>/edit               → save staged PDF
#   POST /admin/pdfs/staging/<id>/delete             → delete staged PDF
#   POST /admin/pdfs/staging/publish                 → publish selected
#   POST /admin/pdfs/staging/publish-all             → publish all staged
# ============================================================
#
# PDF flow (unified in the admin system):
#   Telegram bot uploads  →  pending_pdfs (bot.db)
#   Admin processes       →  pdfs         (bot.db)  ← "staging"
#   Super admin publishes →  pdfs         (main db) ← "library"
# ============================================================

#
# PDF flow (unified in the admin system):
#   Telegram bot uploads  →  pending_pdfs (bot.db)
#   Admin processes       →  pdfs         (bot.db)  ← "staging"
#   Super admin publishes →  pdfs         (main db) ← "library"
#
# Super-admin fast path (Bulk Direct Publish):
#   pending_pdfs  →  [bulk edit page]  →  pdfs (bot.db) + pdfs (main db)
#   + tracking row in unverified_pdfs (main db) for later review.
# ============================================================

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort, jsonify, Response, send_file,
)
import io
import json
import logging
import re
import secrets
import string
import time

from urllib.parse import urlencode

from config import Config
from db import (
    execute_with_retry,
    get_question_by_id,
    create_question,
    update_question,
    delete_question,
    get_questions_paginated,
    get_question_stats,
    get_questions_filter_options,
    check_pdf_codes_exist,
    check_question_exists,
    bulk_create_questions,
    get_all_pdfs,
    get_pdf_by_id,
    get_pdf_by_code,
    get_main_pdf_count,
    create_main_pdf,
    delete_main_pdf,
    get_pdf_distinct_subjects,
    get_pdf_distinct_classes,
    get_pdf_distinct_curricula,
    publish_bot_pdf_to_main,
)
from subjects_config import get_all_subjects, get_subject, get_all_subject_codes
from utils import validate_csrf, get_somali_time_db
from services.admin.guards import admin_can
from services.admin.audit import write_audit
from services.pdf_naming import suggest_from_filename

logger = logging.getLogger(__name__)

admin_content_bp = Blueprint('admin_content', __name__, url_prefix='/admin')


# ============================================================
# HELPERS
# ============================================================

_PDF_CODE_RE = re.compile(r'^[A-Z0-9]{4}-[A-Z0-9]{4}$')
_PDF_SIZE_CAP_BYTES = 20 * 1024 * 1024

_PDF_SIZE_CACHE = {}
_PDF_SIZE_CACHE_TTL = 300


def _normalize_pdf_code(raw):
    if raw is None:
        return None
    code = str(raw).strip().upper()
    return code or None


def _validate_pdf_code_format(code):
    return bool(code) and bool(_PDF_CODE_RE.match(code))


def _normalize_pdf_page(raw):
    if raw is None or raw == '':
        return None
    try:
        page = int(raw)
    except (ValueError, TypeError):
        return None
    return page if page > 0 else None


def _get_telegram_file_size(file_id):
    if not file_id:
        return None
    now = time.time()
    cached = _PDF_SIZE_CACHE.get(file_id)
    if cached and (now - cached['fetched_at']) < _PDF_SIZE_CACHE_TTL:
        return cached['size_bytes']
    try:
        from bot.utils import get_bot
        bot = get_bot()
        file_info = bot.get_file(file_id)
        size = getattr(file_info, 'file_size', None)
        if size:
            size = int(size)
            _PDF_SIZE_CACHE[file_id] = {'size_bytes': size, 'fetched_at': now}
            return size
        return None
    except Exception as e:
        logger.warning(f"Could not get Telegram file size: {e}")
        return None


def _csrf_ok():
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    return bool(token) and token == session.get('csrf_token')


def _int_or_none(raw):
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _generate_staging_pdf_code():
    """Generate a unique PDF code not used in either DB."""
    from bot.db import get_bot_pdf_by_code
    chars = string.ascii_uppercase + '123456789'
    for _ in range(50):
        code = ''.join(secrets.choice(chars) for _ in range(4)) + '-' + \
               ''.join(secrets.choice(chars) for _ in range(4))
        if not get_bot_pdf_by_code(code) and not get_pdf_by_code(code):
            return code
    return ''.join(secrets.choice(chars) for _ in range(4)) + '-' + \
           ''.join(secrets.choice(chars) for _ in range(4))


def _count_unverified_pdfs(show_confirmed=False):
    """Count unverified_pdfs rows. By default only confirmed=0."""
    try:
        if show_confirmed:
            row = execute_with_retry(
                "SELECT COUNT(*) AS cnt FROM unverified_pdfs"
            ).fetchone()
        else:
            row = execute_with_retry(
                "SELECT COUNT(*) AS cnt FROM unverified_pdfs WHERE confirmed = 0"
            ).fetchone()
        return row['cnt'] if row else 0
    except Exception:
        return 0


def _load_unverified_pdfs(show_confirmed=False, limit=100, offset=0):
    """
    Return unverified_pdfs joined with their library metadata.
    Only rows whose pdf_id still exists in the library render.
    """
    query = """
        SELECT up.id            AS tracking_id,
               up.pdf_id,
               up.published_at,
               up.confirmed,
               p.code, p.title, p.subject, p.curriculum,
               p.class, p.chapter, p.tags, p.is_premium,
               p.view_count, p.uploaded_at
        FROM unverified_pdfs up
        INNER JOIN pdfs p ON p.id = up.pdf_id
    """
    params = []
    if not show_confirmed:
        query += " WHERE up.confirmed = 0"
    query += " ORDER BY up.published_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    try:
        cursor = execute_with_retry(query, params)
        rows = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error(f"_load_unverified_pdfs failed: {e}")
        return []

    for r in rows:
        subj = get_subject(r['subject'])
        r['subject_name'] = subj['name'] if subj else r['subject']

    return rows


# ============================================================
# QUESTIONS — LIST
# ============================================================

@admin_content_bp.route('/questions', methods=['GET'], endpoint='questions')
@admin_can('questions.view')
def questions():
    search = (request.args.get('search') or '').strip()
    subject_code = (request.args.get('subject') or '').strip()
    pdf_filter = (request.args.get('pdf') or '').strip()
    pdf_code = (request.args.get('pdf_code') or '').strip().upper()
    status_filter = (request.args.get('status') or '').strip()
    chapter = (request.args.get('chapter') or '').strip()
    interactions = (request.args.get('interactions') or '').strip()
    miss_filter = (request.args.get('miss') or '').strip()
    date_from = (request.args.get('from') or '').strip()
    date_to = (request.args.get('to') or '').strip()
    sort = (request.args.get('sort') or 'newest').strip()

    difficulty_min = _int_or_none(request.args.get('difficulty_min'))
    difficulty_max = _int_or_none(request.args.get('difficulty_max'))

    page = max(1, int(request.args.get('page') or 1))
    per_page = 20

    questions_list, total = get_questions_paginated(
        search=search,
        subject_code=subject_code,
        pdf_filter=pdf_filter,
        pdf_code=pdf_code,
        status_filter=status_filter,
        difficulty_min=difficulty_min,
        difficulty_max=difficulty_max,
        chapter=chapter,
        date_from=date_from,
        date_to=date_to,
        interactions=interactions,
        miss_filter=miss_filter,
        sort=sort,
        page=page,
        per_page=per_page,
    )

    stats = get_question_stats()
    filter_options = get_questions_filter_options()
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    codes_in_page = [q['pdf_code'] for q in questions_list if q.get('pdf_code')]
    pdf_map = check_pdf_codes_exist(codes_in_page) if codes_in_page else {}

    active_filters = _build_active_filters(
        search=search, subject_code=subject_code,
        pdf_filter=pdf_filter, pdf_code=pdf_code,
        status_filter=status_filter, chapter=chapter,
        interactions=interactions, miss_filter=miss_filter,
        difficulty_min=difficulty_min, difficulty_max=difficulty_max,
        date_from=date_from, date_to=date_to,
    )

    return render_template(
        'dashboard/admin/content/questions.html',
        questions=questions_list,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        stats=stats,
        subjects=get_all_subjects(),
        filter_options=filter_options,
        pdf_map=pdf_map,
        search=search,
        subject_code=subject_code,
        pdf_filter=pdf_filter,
        pdf_code=pdf_code,
        status_filter=status_filter,
        chapter=chapter,
        interactions=interactions,
        miss_filter=miss_filter,
        difficulty_min=difficulty_min,
        difficulty_max=difficulty_max,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        active_filters=active_filters,
    )


def _build_active_filters(**kw):
    chips = []

    def add(param, label, value, remove_keys=None, extra_params=None):
        remove = set(remove_keys) if remove_keys else {param}
        remaining = [(k, v) for k, v in request.args.items(multi=True) if k not in remove]
        if extra_params:
            remaining.extend(extra_params)
        qs = urlencode(remaining)
        clear_url = ('?' + qs) if qs else url_for('admin_content.questions')
        chips.append({
            'param': param,
            'label': label,
            'value': str(value),
            'clear_url': clear_url,
        })

    if kw.get('search'):
        add('search', 'Search', kw['search'])
    if kw.get('subject_code'):
        subj = get_subject(kw['subject_code'])
        add('subject', 'Subject', subj['name'] if subj else kw['subject_code'])
    if kw.get('pdf_filter'):
        label = 'PDF linked' if kw['pdf_filter'] == 'linked' else 'PDF unlinked'
        add('pdf', 'PDF', label)
    if kw.get('pdf_code'):
        add('pdf_code', 'PDF code', kw['pdf_code'])
    if kw.get('status_filter'):
        add('status', 'Status', kw['status_filter'].capitalize())
    if kw.get('chapter'):
        add('chapter', 'Chapter', kw['chapter'])
    if kw.get('interactions'):
        labels = {
            'has_reports': 'Has reports',
            'has_saves':   'Has saves',
            'has_likes':   'Has likes',
            'clean':       'No interactions',
        }
        add('interactions', 'Interactions',
            labels.get(kw['interactions'], kw['interactions']))
    if kw.get('miss_filter'):
        labels = {
            'high':   'High miss rate',
            'medium': 'Medium miss rate',
            'low':    'Low miss rate',
            'any':    'Has any miss',
            'never':  'Never attempted',
        }
        add('miss', 'Miss rate',
            labels.get(kw['miss_filter'], kw['miss_filter']))
    dmin = kw.get('difficulty_min')
    dmax = kw.get('difficulty_max')
    if dmin is not None:
        add('difficulty_min', 'Min difficulty', f'⭐ {dmin}')
    if dmax is not None:
        add('difficulty_max', 'Max difficulty', f'⭐ {dmax}')
    if kw.get('date_from'):
        add('from', 'From', kw['date_from'])
    if kw.get('date_to'):
        add('to', 'To', kw['date_to'])
    return chips


# ============================================================
# QUESTIONS — NEW
# ============================================================

@admin_content_bp.route('/questions/new', methods=['GET'], endpoint='question_new')
@admin_can('questions.create')
def question_new():
    return render_template(
        'dashboard/admin/content/question_edit.html',
        question=None,
        subjects=get_all_subjects(),
    )


@admin_content_bp.route('/questions/new', methods=['POST'], endpoint='question_create')
@admin_can('questions.create')
def question_create():
    if not validate_csrf():
        abort(403)

    subject_code = (request.form.get('subject_code') or '').strip()
    question_text = (request.form.get('question_text') or '').strip()
    option_a = (request.form.get('option_a') or '').strip()
    option_b = (request.form.get('option_b') or '').strip()
    option_c = (request.form.get('option_c') or '').strip()
    option_d = (request.form.get('option_d') or '').strip()
    option_e = (request.form.get('option_e') or '').strip()
    option_f = (request.form.get('option_f') or '').strip()
    correct_answer = (request.form.get('correct_answer') or '').strip().upper()
    difficulty_raw = request.form.get('difficulty') or '1'
    chapter = (request.form.get('chapter') or '').strip()
    tags = (request.form.get('tags') or '').strip()
    explanation = (request.form.get('explanation') or '').strip()
    status = (request.form.get('status') or 'active').strip()

    pdf_code = _normalize_pdf_code(request.form.get('pdf_code'))
    pdf_page = _normalize_pdf_page(request.form.get('pdf_page'))

    errors = []
    if not subject_code or not get_subject(subject_code):
        errors.append('Invalid or missing subject.')
    if not question_text:
        errors.append('Question text is required.')
    if not option_a or not option_b or not option_c:
        errors.append('Options A, B, and C are required.')
    if correct_answer not in ('A', 'B', 'C', 'D', 'E', 'F'):
        errors.append('Please select the correct answer.')
    if pdf_code and not _validate_pdf_code_format(pdf_code):
        errors.append('Invalid PDF code format. Expected XXXX-XXXX.')
    if pdf_page and not pdf_code:
        pdf_page = None
        flash('Page number was ignored because no PDF code was provided.', 'warning')

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('admin_content.question_new'))

    try:
        difficulty = int(difficulty_raw)
    except ValueError:
        difficulty = 1

    options = {'A': option_a, 'B': option_b, 'C': option_c}
    if option_d: options['D'] = option_d
    if option_e: options['E'] = option_e
    if option_f: options['F'] = option_f

    data = {
        'subject_code': subject_code,
        'question_text': question_text,
        'options': options,
        'correct_answer': correct_answer,
        'difficulty': difficulty,
        'chapter': chapter,
        'tags': tags,
        'explanation': explanation,
        'pdf_code': pdf_code,
        'pdf_page': pdf_page,
        'status': status,
        'created_by': session.get('user_id'),
        'updated_by': session.get('user_id'),
    }

    if create_question(data):
        write_audit(
            action='question.create',
            target_type='question',
            before=None,
            after={'subject_code': subject_code, 'pdf_code': pdf_code,
                   'difficulty': difficulty, 'status': status},
            severity='info',
        )
        flash('Question added successfully.', 'success')
        return redirect(url_for('admin_content.questions'))

    flash('Error adding question.', 'error')
    return redirect(url_for('admin_content.question_new'))


# ============================================================
# QUESTIONS — EDIT
# ============================================================

@admin_content_bp.route('/questions/<int:question_id>/edit', methods=['GET'],
                        endpoint='question_edit')
@admin_can('questions.edit')
def question_edit(question_id):
    question = get_question_by_id(question_id)
    if not question:
        abort(404)

    pdf_info = None
    if question.get('pdf_code'):
        lookup = check_pdf_codes_exist([question['pdf_code']])
        pdf_info = lookup.get(question['pdf_code'])

    return render_template(
        'dashboard/admin/content/question_edit.html',
        question=question,
        subjects=get_all_subjects(),
        pdf_info=pdf_info,
    )


@admin_content_bp.route('/questions/<int:question_id>/edit', methods=['POST'],
                        endpoint='question_update')
@admin_can('questions.edit')
def question_update(question_id):
    if not validate_csrf():
        abort(403)

    question = get_question_by_id(question_id)
    if not question:
        abort(404)

    subject_code = (request.form.get('subject_code') or '').strip()
    question_text = (request.form.get('question_text') or '').strip()
    option_a = (request.form.get('option_a') or '').strip()
    option_b = (request.form.get('option_b') or '').strip()
    option_c = (request.form.get('option_c') or '').strip()
    option_d = (request.form.get('option_d') or '').strip()
    option_e = (request.form.get('option_e') or '').strip()
    option_f = (request.form.get('option_f') or '').strip()
    correct_answer = (request.form.get('correct_answer') or '').strip().upper()
    difficulty_raw = request.form.get('difficulty') or '1'
    chapter = (request.form.get('chapter') or '').strip()
    tags = (request.form.get('tags') or '').strip()
    explanation = (request.form.get('explanation') or '').strip()
    status = (request.form.get('status') or 'active').strip()

    pdf_code = _normalize_pdf_code(request.form.get('pdf_code'))
    pdf_page = _normalize_pdf_page(request.form.get('pdf_page'))

    errors = []
    if not subject_code or not get_subject(subject_code):
        errors.append('Invalid or missing subject.')
    if not question_text:
        errors.append('Question text is required.')
    if not option_a or not option_b or not option_c:
        errors.append('Options A, B, and C are required.')
    if correct_answer not in ('A', 'B', 'C', 'D', 'E', 'F'):
        errors.append('Please select the correct answer.')
    if pdf_code and not _validate_pdf_code_format(pdf_code):
        errors.append('Invalid PDF code format.')
    if pdf_page and not pdf_code:
        pdf_page = None
        flash('Page number was ignored because no PDF code was provided.', 'warning')

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('admin_content.question_edit',
                                question_id=question_id))

    try:
        difficulty = int(difficulty_raw)
    except ValueError:
        difficulty = 1

    options = {'A': option_a, 'B': option_b, 'C': option_c}
    if option_d: options['D'] = option_d
    if option_e: options['E'] = option_e
    if option_f: options['F'] = option_f

    data = {
        'subject_code': subject_code,
        'question_text': question_text,
        'options': options,
        'correct_answer': correct_answer,
        'difficulty': difficulty,
        'chapter': chapter,
        'tags': tags,
        'explanation': explanation,
        'pdf_code': pdf_code,
        'pdf_page': pdf_page,
        'status': status,
        'updated_by': session.get('user_id'),
    }

    if update_question(question_id, data):
        write_audit(
            action='question.update',
            target_type='question',
            target_id=question_id,
            before={'subject_code': question.get('subject_code'),
                    'pdf_code': question.get('pdf_code'),
                    'status': question.get('status')},
            after={'subject_code': subject_code,
                   'pdf_code': pdf_code,
                   'status': status},
            severity='info',
        )
        flash('Question updated.', 'success')
        return redirect(url_for('admin_content.questions'))

    flash('Error updating question.', 'error')
    return redirect(url_for('admin_content.question_edit',
                            question_id=question_id))


# ============================================================
# QUESTIONS — ARCHIVE
# ============================================================

@admin_content_bp.route('/questions/<int:question_id>/delete', methods=['POST'],
                        endpoint='question_archive')
@admin_can('questions.archive')
def question_archive(question_id):
    if not validate_csrf():
        abort(403)

    question = get_question_by_id(question_id)
    if not question:
        abort(404)

    if delete_question(question_id):
        write_audit(
            action='question.archive',
            target_type='question',
            target_id=question_id,
            before={'status': question.get('status')},
            after={'status': 'archived'},
            severity='warning',
        )
        flash('Question archived.', 'success')
    else:
        flash('Error archiving question.', 'error')

    return redirect(url_for('admin_content.questions'))


# ============================================================
# QUESTIONS — BROKEN PDF LINKS
# ============================================================

@admin_content_bp.route('/questions/broken', methods=['GET'],
                        endpoint='questions_broken')
@admin_can('questions.view')
def questions_broken():
    cursor = execute_with_retry("""
        SELECT id, question_text, subject_code, pdf_code, created_at
        FROM questions
        WHERE status = 'active'
          AND pdf_code IS NOT NULL
          AND pdf_code != ''
    """)
    candidates = [dict(r) for r in cursor.fetchall()]

    codes = list({c['pdf_code'] for c in candidates})
    pdf_map = check_pdf_codes_exist(codes) if codes else {}

    rows = []
    for c in candidates:
        info = pdf_map.get(c['pdf_code'], {})
        if not info.get('exists'):
            subj = get_subject(c['subject_code'])
            c['subject_name'] = subj['name'] if subj else c['subject_code']
            rows.append(c)

    return render_template(
        'dashboard/admin/content/question_broken.html',
        rows=rows,
    )


# ============================================================
# QUESTIONS — HIGH MISS-RATE
# ============================================================

@admin_content_bp.route('/questions/hard', methods=['GET'],
                        endpoint='questions_hard')
@admin_can('questions.view')
def questions_hard():
    rows = []
    try:
        cursor = execute_with_retry("""
            SELECT q.id, q.question_text, q.subject_code, q.difficulty,
                   qms.total_attempts AS attempts,
                   qms.total_misses   AS misses,
                   qms.miss_rate
            FROM question_miss_stats qms
            JOIN questions q ON q.id = qms.question_id
            WHERE qms.total_misses >= 3
            ORDER BY qms.miss_rate DESC, qms.total_misses DESC
            LIMIT 100
        """)
        for row in cursor.fetchall():
            r = dict(row)
            subj = get_subject(r['subject_code'])
            r['subject_name'] = subj['name'] if subj else r['subject_code']
            rows.append(r)
    except Exception as e:
        logger.warning(f"questions_hard query failed: {e}")

    return render_template(
        'dashboard/admin/content/question_hard.html',
        rows=rows,
    )


# ============================================================
# PDF INFO LOOKUP (AJAX)
# ============================================================

@admin_content_bp.route('/questions/pdf-info', methods=['POST'],
                        endpoint='pdf_info')
@admin_can('questions.view')
def pdf_info():
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    data = request.get_json(silent=True) or {}
    code = _normalize_pdf_code(data.get('code'))

    out = {
        'code': code or '',
        'valid': False,
        'exists': False,
        'source': None,
        'title': '',
        'is_premium': False,
        'size_mb': None,
        'can_preview': False,
        'preview_url': None,
        'reason': None,
    }

    if not code:
        out['reason'] = 'no_code'
        return jsonify(out)
    if not _validate_pdf_code_format(code):
        out['reason'] = 'invalid_format'
        return jsonify(out)

    out['valid'] = True

    main_pdf = get_pdf_by_code(code)
    if main_pdf:
        out['exists'] = True
        out['source'] = 'main'
        out['title'] = main_pdf.get('title') or code
        out['is_premium'] = bool(main_pdf.get('is_premium', 0))
        file_url = main_pdf.get('file_url')
        if file_url:
            out['can_preview'] = True
            out['preview_url'] = file_url
            return jsonify(out)
        try:
            from bot.db import get_bot_pdf_by_code
            bot_pdf = get_bot_pdf_by_code(code)
            if bot_pdf:
                size = _get_telegram_file_size(bot_pdf['file_id'])
                if size:
                    out['size_mb'] = round(size / (1024 * 1024), 2)
                    if size <= _PDF_SIZE_CAP_BYTES:
                        out['can_preview'] = True
                        out['preview_url'] = url_for('pdfs.preview_telegram',
                                                     code=code)
                    else:
                        out['reason'] = 'too_large'
                else:
                    out['reason'] = 'size_unknown'
            else:
                out['reason'] = 'no_file'
        except Exception as e:
            logger.warning(f"pdf_info main→bot fallback failed: {e}")
            out['reason'] = 'lookup_error'
        return jsonify(out)

    try:
        from bot.db import get_bot_pdf_by_code
        bot_pdf = get_bot_pdf_by_code(code)
        if bot_pdf:
            out['exists'] = True
            out['source'] = 'bot'
            out['title'] = bot_pdf.get('title') or code
            out['is_premium'] = bool(bot_pdf.get('is_premium', 0))
            size = _get_telegram_file_size(bot_pdf['file_id'])
            if size:
                out['size_mb'] = round(size / (1024 * 1024), 2)
                if size <= _PDF_SIZE_CAP_BYTES:
                    out['can_preview'] = True
                    out['preview_url'] = url_for('pdfs.preview_telegram',
                                                 code=code)
                else:
                    out['reason'] = 'too_large'
            else:
                out['reason'] = 'size_unknown'
        else:
            out['reason'] = 'not_found'
    except Exception as e:
        logger.warning(f"pdf_info bot lookup failed: {e}")
        out['reason'] = 'lookup_error'

    return jsonify(out)


# ============================================================
# BULK IMPORT
# ============================================================

@admin_content_bp.route('/bulk-import', methods=['GET'], endpoint='bulk_import')
@admin_can('questions.bulk_import')
def bulk_import():
    return render_template('dashboard/admin/content/bulk_import.html')


@admin_content_bp.route('/bulk-import', methods=['POST'],
                        endpoint='bulk_import_apply')
@admin_can('questions.bulk_import')
def bulk_import_apply():
    if not validate_csrf():
        flash('Invalid session. Please refresh and try again.', 'error')
        return redirect(url_for('admin_content.bulk_import'))

    input_method = request.form.get('input_method', 'paste')
    if input_method == 'file':
        file_data = request.files.get('json_file')
        if not file_data or not file_data.filename:
            flash('Please upload a JSON file.', 'error')
            return redirect(url_for('admin_content.bulk_import'))
        try:
            raw_text = file_data.read().decode('utf-8')
        except Exception as e:
            flash(f'Error reading file: {e}', 'error')
            return redirect(url_for('admin_content.bulk_import'))
    else:
        raw_text = (request.form.get('json_data') or '').strip()

    if not raw_text:
        flash('Please paste JSON or upload a file.', 'error')
        return redirect(url_for('admin_content.bulk_import'))

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        flash(f'Invalid JSON: {e}', 'error')
        return redirect(url_for('admin_content.bulk_import'))

    if not isinstance(data, dict):
        flash('JSON root must be an object.', 'error')
        return redirect(url_for('admin_content.bulk_import'))

    metadata = data.get('metadata') or {}
    subject_code = (metadata.get('subject_code') or '').strip()
    chapter = (metadata.get('chapter') or '').strip()

    all_subject_codes = get_all_subject_codes()
    if not subject_code:
        flash('subject_code is required in metadata.', 'error')
        return redirect(url_for('admin_content.bulk_import'))
    if subject_code not in all_subject_codes:
        flash(f'Unknown subject code: "{subject_code}"', 'error')
        return redirect(url_for('admin_content.bulk_import'))

    questions_raw = data.get('questions') or []
    if not isinstance(questions_raw, list) or not questions_raw:
        flash('"questions" must be a non-empty array.', 'error')
        return redirect(url_for('admin_content.bulk_import'))

    pdf_code_raw = (request.form.get('pdf_code') or '').strip().upper()
    pdf_code = pdf_code_raw or None
    if pdf_code and not _validate_pdf_code_format(pdf_code):
        flash(f'Invalid PDF code format: "{pdf_code}"', 'error')
        return redirect(url_for('admin_content.bulk_import'))

    questions_to_import = []
    errors = []
    duplicates = []
    warnings = []

    for idx, q in enumerate(questions_raw, 1):
        if not isinstance(q, dict):
            errors.append({'index': idx, 'question': '—',
                           'error': 'Question must be an object'})
            continue
        q_text = (q.get('question') or '').strip()
        if not q_text:
            errors.append({'index': idx, 'question': '—',
                           'error': 'Question text is required'})
            continue
        opts = q.get('options')
        if not isinstance(opts, list) or len(opts) < 3 or len(opts) > 6:
            errors.append({'index': idx, 'question': q_text[:50],
                           'error': '3–6 options required'})
            continue
        correct_idx = q.get('correct')
        if not isinstance(correct_idx, int) or not (1 <= correct_idx <= len(opts)):
            errors.append({'index': idx, 'question': q_text[:50],
                           'error': 'Invalid "correct" index'})
            continue
        try:
            difficulty = int(q.get('difficulty', 1))
        except (ValueError, TypeError):
            difficulty = 1
        if not (1 <= difficulty <= 5):
            difficulty = 3

        if check_question_exists(q_text, subject_code):
            duplicates.append({'index': idx, 'question': q_text,
                               'error': 'Duplicate question'})
            continue

        pdf_page = _normalize_pdf_page(q.get('pdf_page'))
        if pdf_page and not pdf_code:
            warnings.append({'index': idx,
                             'message': f'Q{idx}: pdf_page set but no PDF code — dropped'})
            pdf_page = None

        labels = ['A', 'B', 'C', 'D', 'E', 'F']
        options_dict = {labels[i]: str(opts[i]).strip() for i in range(len(opts))}

        questions_to_import.append({
            'subject_code': subject_code,
            'question_text': q_text,
            'options': options_dict,
            'correct_answer': labels[correct_idx - 1],
            'difficulty': difficulty,
            'chapter': chapter,
            'tags': ','.join(q.get('tags', [])) if isinstance(q.get('tags'), list)
                    else str(q.get('tags', '') or ''),
            'explanation': (q.get('explanation') or '').strip(),
            'pdf_code': pdf_code,
            'pdf_page': pdf_page,
            'created_by': session.get('user_id'),
            'updated_by': session.get('user_id'),
        })

    if errors or duplicates:
        return render_template(
            'dashboard/admin/content/bulk_import.html',
            preview=True,
            valid_questions=questions_to_import,
            errors=errors, duplicates=duplicates, warnings=warnings,
            subject_code=subject_code, chapter=chapter,
            pdf_code=pdf_code, total_questions=len(questions_raw),
        )

    if not questions_to_import:
        flash('No valid questions to import.', 'error')
        return redirect(url_for('admin_content.bulk_import'))

    try:
        result = bulk_create_questions(questions_to_import, session.get('user_id'))
    except Exception as e:
        logger.error(f"bulk_create_questions raised: {e}", exc_info=True)
        flash(f'Import crashed: {e}', 'error')
        return redirect(url_for('admin_content.bulk_import'))

    imported = result.get('imported', 0)
    failed = result.get('errors') or []

    if imported > 0:
        write_audit(
            action='question.bulk_import',
            target_type='question',
            before=None,
            after={'subject_code': subject_code,
                   'imported': imported,
                   'pdf_code': pdf_code},
            severity='info',
        )
        flash(f'✅ {imported} questions imported successfully!', 'success')
        if warnings:
            flash(f'⚠️ {len(warnings)} warning(s) — some fields were dropped.',
                  'warning')
        if failed:
            flash(f'⚠️ {len(failed)} question(s) failed to insert.', 'error')
        return redirect(url_for('admin_content.questions'))

    detail = failed[0].get('error') if failed else 'unknown error'
    flash(f'❌ Import failed — nothing inserted. Reason: {detail}', 'error')
    return redirect(url_for('admin_content.bulk_import'))


@admin_content_bp.route('/bulk-template', methods=['GET'], endpoint='bulk_template')
@admin_can('questions.bulk_import')
def bulk_template():
    template = {
        "metadata": {"subject_code": "geography",
                     "chapter": "Chapter 1: Introduction"},
        "questions": [
            {"tags": ["geography", "capitals"], "difficulty": 2,
             "question": "What is the capital of Somalia?",
             "options": ["Mogadishu", "Hargeisa", "Kismayo"],
             "correct": 1,
             "explanation": "Mogadishu has been the capital since 1960.",
             "pdf_page": 12},
        ]
    }
    body = json.dumps(template, indent=2, ensure_ascii=False)
    return Response(
        body,
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=bulk_template.json'},
    )


@admin_content_bp.route('/bulk-preview', methods=['POST'], endpoint='bulk_preview')
@admin_can('questions.bulk_import')
def bulk_preview():
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    raw = (request.form.get('json_data') or '').strip()
    if not raw:
        return jsonify({'error': 'No JSON data provided'}), 400

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Invalid JSON: {e}'}), 400

    if not isinstance(data, dict):
        return jsonify({'error': 'JSON root must be an object'}), 400
    if 'metadata' not in data:
        return jsonify({'error': 'Missing metadata section'}), 400
    if 'questions' not in data or not isinstance(data['questions'], list) or not data['questions']:
        return jsonify({'error': 'Missing or empty questions array'}), 400

    metadata = data.get('metadata') or {}
    subject_code = (metadata.get('subject_code') or '').strip()
    chapter = (metadata.get('chapter') or '').strip()

    if not subject_code or subject_code not in get_all_subject_codes():
        return jsonify({'error': f'Unknown or missing subject_code'}), 400

    pdf_code_raw = (request.form.get('pdf_code') or '').strip().upper()
    pdf_code = pdf_code_raw or None
    pdf_code_valid = True
    pdf_info_payload = None

    if pdf_code:
        if _validate_pdf_code_format(pdf_code):
            lookup = check_pdf_codes_exist([pdf_code])
            info = lookup.get(pdf_code, {})
            pdf_info_payload = {
                'code': pdf_code, 'valid': True,
                'exists': info.get('exists', False),
                'source': info.get('source'),
                'title': info.get('title', ''),
                'is_premium': info.get('is_premium', False),
            }
        else:
            pdf_code_valid = False
            pdf_info_payload = {'code': pdf_code, 'valid': False,
                                'exists': False, 'reason': 'invalid_format'}

    preview = []
    unknown_codes = set()
    linked_count = 0
    total = len(data['questions'])

    for idx, q in enumerate(data['questions'], 1):
        if not isinstance(q, dict):
            preview.append({'index': idx, 'question': '(invalid entry)',
                            'difficulty': 1, 'options_count': 0,
                            'has_explanation': False, 'tags': '',
                            'pdf_code': '', 'pdf_page': None,
                            'pdf_exists': False, 'pdf_title': '',
                            'pdf_source': None,
                            'orphan': False, 'invalid_code': False})
            continue

        q_text = (q.get('question') or '') or ''
        q_short = q_text[:70] + ('…' if len(q_text) > 70 else '')
        page = _normalize_pdf_page(q.get('pdf_page'))
        orphan = bool(page and not pdf_code)
        if orphan:
            page = None

        entry = {
            'index': idx, 'question': q_short,
            'difficulty': int(q.get('difficulty', 1) or 1)
                          if str(q.get('difficulty', 1)).isdigit() else 1,
            'options_count': len(q.get('options', []) or []),
            'has_explanation': bool((q.get('explanation') or '').strip()),
            'tags': ', '.join(q.get('tags', []))[:40]
                    if isinstance(q.get('tags'), list)
                    else str(q.get('tags', ''))[:40],
            'pdf_code': pdf_code or '',
            'pdf_page': page,
            'pdf_exists': False, 'pdf_title': '', 'pdf_source': None,
            'orphan': orphan, 'invalid_code': not pdf_code_valid,
        }

        if pdf_code and pdf_info_payload:
            entry['pdf_exists'] = pdf_info_payload.get('exists', False)
            entry['pdf_title'] = pdf_info_payload.get('title', '')
            entry['pdf_source'] = pdf_info_payload.get('source')
            if not entry['pdf_exists']:
                unknown_codes.add(pdf_code)
            linked_count += 1

        preview.append(entry)

    return jsonify({
        'subject_code': subject_code, 'chapter': chapter,
        'pdf_code': pdf_code or '',
        'pdf_code_valid': pdf_code_valid,
        'pdf_info': pdf_info_payload,
        'total': total, 'linked_count': linked_count,
        'unknown_codes': sorted(unknown_codes),
        'preview': preview[:30], 'truncated': total > 30,
    })


# ============================================================
# PDFs — WORKSPACE
# ============================================================

@admin_content_bp.route('/pdfs', methods=['GET'], endpoint='pdfs')
@admin_can('pdfs.view')
def pdfs():
    """Unified PDF workspace: library / intake / staging / unverified."""
    can_intake  = admin_can('pdfs.intake')
    can_publish = admin_can('pdfs.publish')
    can_edit    = admin_can('pdfs.edit')
    can_delete  = admin_can('pdfs.delete')

    PER_PAGE = 100

    tab = (request.args.get('tab') or 'library').strip().lower()
    if tab not in ('library', 'intake', 'staging', 'unverified'):
        tab = 'library'
    if tab == 'intake' and not can_intake:
        tab = 'library'
    if tab == 'staging' and not (can_intake or can_publish):
        tab = 'library'
    if tab == 'unverified' and not can_intake:
        tab = 'library'

    def _page_param(name='page'):
        try:
            p = int(request.args.get(name) or 1)
        except (ValueError, TypeError):
            p = 1
        return max(1, p)

    # ==================================================
    # LIBRARY TAB
    # ==================================================
    search = (request.args.get('search') or '').strip()
    subject_filter = (request.args.get('subject') or '').strip()
    curriculum_filter = (request.args.get('curriculum') or '').strip()
    class_filter = (request.args.get('class') or '').strip()

    lib_page = _page_param()
    lib_offset = (lib_page - 1) * PER_PAGE
    lib_total = get_main_pdf_count(
        search=search,
        subject=subject_filter,
        curriculum=curriculum_filter,
        class_filter=class_filter,
    )
    lib_total_pages = max(1, (lib_total + PER_PAGE - 1) // PER_PAGE)
    if lib_page > lib_total_pages:
        lib_page = lib_total_pages
        lib_offset = (lib_page - 1) * PER_PAGE

    pdf_list = get_all_pdfs(
        limit=PER_PAGE, offset=lib_offset,
        search=search,
        subject=subject_filter,
        curriculum=curriculum_filter,
        class_filter=class_filter,
    )

    # ==================================================
    # INTAKE TAB
    # ==================================================
    q = (request.args.get('q') or '').strip()
    intake_page = _page_param()
    intake_offset = (intake_page - 1) * PER_PAGE

    pending_list = []
    pending_count = 0            # total unfiltered (for the tab badge)
    intake_filtered_count = 0    # filtered count (for "Showing X of Y")
    intake_total_pages = 1

    if can_intake:
        try:
            from bot.db import get_pending_pdf_list, count_pending_pdfs
            pending_count = count_pending_pdfs()
            intake_filtered_count = count_pending_pdfs(search=q) if q else pending_count
            intake_total_pages = max(1, (intake_filtered_count + PER_PAGE - 1) // PER_PAGE)
            if intake_page > intake_total_pages:
                intake_page = intake_total_pages
                intake_offset = (intake_page - 1) * PER_PAGE
            pending_list = get_pending_pdf_list(
                limit=PER_PAGE, offset=intake_offset, search=q
            )
        except Exception as e:
            logger.warning(f"pending list load failed: {e}")

    # ==================================================
    # STAGING TAB
    # ==================================================
    show_published = (request.args.get('show_published') == '1')
    staging_page = _page_param()
    staging_offset = (staging_page - 1) * PER_PAGE

    staging_list = []
    staging_count = 0
    staging_published_count = 0
    staging_filtered_count = 0
    staging_total_pages = 1

    if can_intake or can_publish:
        try:
            from bot.db import get_bot_pdfs, count_bot_pdfs

            staging_count = count_bot_pdfs(published_filter=False)
            staging_published_count = count_bot_pdfs(published_filter=True)

            view_filter = None if show_published else False
            staging_filtered_count = (
                staging_count + staging_published_count
                if show_published else staging_count
            )
            staging_total_pages = max(1, (staging_filtered_count + PER_PAGE - 1) // PER_PAGE)
            if staging_page > staging_total_pages:
                staging_page = staging_total_pages
                staging_offset = (staging_page - 1) * PER_PAGE

            staging_list = get_bot_pdfs(
                limit=PER_PAGE, offset=staging_offset,
                published_filter=view_filter,
            )
        except Exception as e:
            logger.warning(f"staging list load failed: {e}")

    # ==================================================
    # UNVERIFIED TAB
    # ==================================================
    show_confirmed = (request.args.get('show_confirmed') == '1')
    unverified_page = _page_param()
    unverified_offset = (unverified_page - 1) * PER_PAGE

    unverified_list = []
    unverified_count = 0
    unverified_filtered_count = 0
    unverified_total_pages = 1

    if can_intake:
        try:
            unverified_count = _count_unverified_pdfs()
            unverified_filtered_count = _count_unverified_pdfs(
                show_confirmed=show_confirmed
            ) if show_confirmed else unverified_count
            unverified_total_pages = max(1, (unverified_filtered_count + PER_PAGE - 1) // PER_PAGE)
            if unverified_page > unverified_total_pages:
                unverified_page = unverified_total_pages
                unverified_offset = (unverified_page - 1) * PER_PAGE

            unverified_list = _load_unverified_pdfs(
                show_confirmed=show_confirmed,
                limit=PER_PAGE,
                offset=unverified_offset,
            )
        except Exception as e:
            logger.warning(f"unverified list load failed: {e}")

    return render_template(
        'dashboard/admin/content/pdfs.html',
        tab=tab,
        can_intake=can_intake,
        can_publish=can_publish,
        can_edit=can_edit,
        can_delete=can_delete,

        # library
        pdfs=pdf_list,
        subjects=get_all_subjects(),
        curricula=get_pdf_distinct_curricula(),
        classes=get_pdf_distinct_classes(),
        search=search,
        subject_filter=subject_filter,
        curriculum_filter=curriculum_filter,
        class_filter=class_filter,
        lib_page=lib_page,
        lib_total=lib_total,
        lib_total_pages=lib_total_pages,

        # intake
        pending_list=pending_list,
        pending_count=pending_count,
        intake_filtered_count=intake_filtered_count,
        intake_page=intake_page,
        intake_total_pages=intake_total_pages,
        q=q,

        # staging
        staging_list=staging_list,
        staging_count=staging_count,
        staging_published_count=staging_published_count,
        staging_filtered_count=staging_filtered_count,
        staging_page=staging_page,
        staging_total_pages=staging_total_pages,
        show_published=show_published,

        # unverified
        unverified_list=unverified_list,
        unverified_count=unverified_count,
        unverified_filtered_count=unverified_filtered_count,
        unverified_page=unverified_page,
        unverified_total_pages=unverified_total_pages,
        show_confirmed=show_confirmed,
    )


# ============================================================
# PDFs — LIBRARY EDIT
# ============================================================

@admin_content_bp.route('/pdfs/<int:pdf_id>/edit', methods=['GET'],
                        endpoint='pdf_edit')
@admin_can('pdfs.edit')
def pdf_edit(pdf_id):
    pdf = get_pdf_by_id(pdf_id)
    if not pdf:
        abort(404)
    return render_template(
        'dashboard/admin/content/pdf_edit.html',
        pdf=pdf,
        subjects=get_all_subjects(),
        curricula=get_pdf_distinct_curricula(),
        classes=get_pdf_distinct_classes(),
    )


@admin_content_bp.route('/pdfs/<int:pdf_id>/edit', methods=['POST'],
                        endpoint='pdf_update')
@admin_can('pdfs.edit')
def pdf_update(pdf_id):
    if not validate_csrf():
        abort(403)
    pdf = get_pdf_by_id(pdf_id)
    if not pdf:
        abort(404)

    title = (request.form.get('title') or '').strip()
    description = (request.form.get('description') or '').strip()
    curriculum = (request.form.get('curriculum') or '').strip()
    class_filter = (request.form.get('class') or '').strip()
    subject = (request.form.get('subject') or '').strip()
    chapter = (request.form.get('chapter') or '').strip()
    tags = (request.form.get('tags') or '').strip()
    is_premium = 1 if request.form.get('is_premium') == 'on' else 0

    if not title or not subject:
        flash('Title and Subject are required.', 'error')
        return redirect(url_for('admin_content.pdf_edit', pdf_id=pdf_id))

    try:
        execute_with_retry("""
            UPDATE pdfs SET
                title = ?, description = ?, curriculum = ?, class = ?,
                subject = ?, chapter = ?, tags = ?, is_premium = ?
            WHERE id = ?
        """, (title, description, curriculum, class_filter,
              subject, chapter, tags, is_premium, pdf_id), commit=True)

        # Auto-confirm in the unverified queue (if tracked)
        try:
            execute_with_retry(
                "UPDATE unverified_pdfs SET confirmed = 1 WHERE pdf_id = ?",
                (pdf_id,), commit=True,
            )
        except Exception:
            pass

        write_audit(
            action='pdf.update', target_type='pdf', target_id=pdf_id,
            before={'title': pdf.get('title'), 'subject': pdf.get('subject')},
            after={'title': title, 'subject': subject},
            severity='info',
        )
        flash('PDF updated.', 'success')
        return redirect(url_for('admin_content.pdfs'))

    except Exception as e:
        logger.error(f"pdf_update failed: {e}")
        flash('Error updating PDF.', 'error')
        return redirect(url_for('admin_content.pdf_edit', pdf_id=pdf_id))


@admin_content_bp.route('/pdfs/<int:pdf_id>/delete', methods=['POST'],
                        endpoint='pdf_delete')
@admin_can('pdfs.delete')
def pdf_delete(pdf_id):
    if not validate_csrf():
        abort(403)
    pdf = get_pdf_by_id(pdf_id)
    if not pdf:
        abort(404)
    if delete_main_pdf(pdf_id):
        # unverified_pdfs row cascades automatically via FK
        write_audit(
            action='pdf.delete', target_type='pdf', target_id=pdf_id,
            before={'title': pdf.get('title'), 'code': pdf.get('code')},
            after=None, severity='warning',
        )
        flash('PDF deleted.', 'success')
    else:
        flash('Error deleting PDF.', 'error')
    return redirect(url_for('admin_content.pdfs'))


@admin_content_bp.route('/pdfs/broken', methods=['GET'], endpoint='pdfs_broken')
@admin_can('pdfs.view')
def pdfs_broken():
    cursor = execute_with_retry("""
        SELECT pdf_code, COUNT(*) AS count,
               MIN(question_text) AS sample_question
        FROM questions
        WHERE status = 'active'
          AND pdf_code IS NOT NULL AND pdf_code != ''
        GROUP BY pdf_code
    """)
    candidates = [dict(r) for r in cursor.fetchall()]
    codes = [c['pdf_code'] for c in candidates]
    pdf_map = check_pdf_codes_exist(codes) if codes else {}
    rows = [c for c in candidates
            if not pdf_map.get(c['pdf_code'], {}).get('exists')]
    return render_template('dashboard/admin/content/pdf_broken.html', rows=rows)


# ============================================================
# PDFs — INTAKE (pending)
# ============================================================

@admin_content_bp.route('/pdfs/intake/<int:pending_id>/process',
                        methods=['GET', 'POST'],
                        endpoint='pdf_intake_process')
@admin_can('pdfs.intake')
def pdf_intake_process(pending_id):
    from bot.db import (
        get_pending_pdf_by_id, get_bot_pdf_by_code,
        insert_bot_pdf, delete_pending_pdf,
    )

    pending = get_pending_pdf_by_id(pending_id)
    if not pending:
        flash('Pending PDF not found.', 'error')
        return redirect(url_for('admin_content.pdfs', tab='intake'))

    filename = pending.get('filename') or ''

    if request.method == 'POST':
        if not validate_csrf():
            abort(403)

        code = (request.form.get('code') or '').strip().upper()
        title = (request.form.get('title') or '').strip()
        description = (request.form.get('description') or '').strip()
        curriculum = (request.form.get('curriculum') or 'PL').strip()
        class_filter = (request.form.get('class') or '').strip()
        subject = (request.form.get('subject') or '').strip()
        chapter = (request.form.get('chapter') or '').strip()
        tags = (request.form.get('tags') or '').strip()
        is_premium = 1 if request.form.get('is_premium') == 'on' else 0

        errors = []
        if not code or not _validate_pdf_code_format(code):
            errors.append('Valid PDF code is required (format XXXX-XXXX).')
        elif get_bot_pdf_by_code(code) or get_pdf_by_code(code):
            errors.append(f'Code {code} is already taken.')
        if not title:
            errors.append('Title is required.')
        if not subject:
            errors.append('Subject is required.')
        if curriculum not in ('PL', 'SO', 'SL'):
            errors.append('Invalid curriculum.')
        if class_filter and class_filter not in ('7aad', '8aad', 'F3', 'F4'):
            errors.append('Invalid class.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return redirect(url_for('admin_content.pdf_intake_process',
                                    pending_id=pending_id))

        bot_pdf_id = insert_bot_pdf({
            'code': code,
            'title': title,
            'description': description,
            'curriculum': curriculum,
            'class': class_filter,
            'subject': subject,
            'chapter': chapter,
            'tags': tags,
            'is_premium': is_premium,
            'file_id': pending['file_id'],
            'file_unique_id': pending['file_unique_id'],
            'uploaded_by': pending['uploaded_by'],
            'original_filename': filename,
        })

        if not bot_pdf_id:
            flash('Failed to stage PDF. Please try again.', 'error')
            return redirect(url_for('admin_content.pdf_intake_process',
                                    pending_id=pending_id))

        delete_pending_pdf(pending_id)

        write_audit(
            action='pdf.intake.processed',
            target_type='pdf_staging',
            target_id=bot_pdf_id,
            before={'pending_id': pending_id, 'filename': filename},
            after={'code': code, 'title': title, 'subject': subject},
            severity='info',
        )

        flash(f'PDF staged with code {code}.', 'success')
        return redirect(url_for('admin_content.pdfs', tab='staging'))

    auto_code = _generate_staging_pdf_code()
    suggested = suggest_from_filename(filename)

    return render_template(
        'dashboard/admin/content/pdf_process.html',
        pending=pending,
        auto_code=auto_code,
        suggested=suggested,
        subjects=get_all_subjects(),
        curricula=[('PL', 'Puntland'), ('SO', 'Somalia'),
                   ('SL', 'Somaliland')],
        classes=['7aad', '8aad', 'F3', 'F4'],
    )


@admin_content_bp.route('/pdfs/intake/<int:pending_id>/preview',
                        endpoint='pdf_intake_preview')
@admin_can('pdfs.intake')
def pdf_intake_preview(pending_id):
    from bot.db import get_pending_pdf_by_id
    pending = get_pending_pdf_by_id(pending_id)
    if not pending:
        abort(404)

    file_id = pending.get('file_id')
    if not file_id:
        abort(404, 'No Telegram file_id stored for this pending upload.')

    try:
        from bot.utils import get_bot
        bot = get_bot()
        tg_file = bot.get_file(file_id)
        data = bot.download_file(tg_file.file_path)

        if not data:
            abort(502, 'Telegram returned an empty file.')

        buf = io.BytesIO(data)
        buf.seek(0)

        filename = pending.get('filename') or 'document.pdf'

        response = send_file(
            buf,
            mimetype='application/pdf',
            as_attachment=False,
            download_name=filename,
            conditional=True,
        )
        response.headers['Content-Disposition'] = (
            f'inline; filename="{filename}"'
        )
        response.headers.pop('X-Frame-Options', None)
        response.headers['Cache-Control'] = 'private, max-age=300'
        return response

    except Exception as e:
        logger.error(
            f"Preview failed for pending #{pending_id} (file_id={file_id}): {e}",
            exc_info=True,
        )
        abort(502)


# ============================================================
# PDFs — INTAKE BULK PUBLISH (super admin direct publish)
# ============================================================

@admin_content_bp.route('/pdfs/intake/publish-direct', methods=['POST'],
                        endpoint='pdf_intake_publish_direct')
@admin_can('pdfs.publish')
def pdf_intake_publish_direct():
    """
    Step 1 of direct publish: super admin selects pending PDFs.
    Renders a bulk-edit page with auto-suggested metadata.
    """
    if not validate_csrf():
        abort(403)

    pending_ids = request.form.getlist('pending_ids')
    if not pending_ids:
        flash('No PDFs selected.', 'error')
        return redirect(url_for('admin_content.pdfs', tab='intake'))

    from bot.db import get_pending_pdf_by_id

    rows = []
    skipped = 0
    for pid in pending_ids:
        try:
            pid_int = int(pid)
        except (ValueError, TypeError):
            skipped += 1
            continue
        pending = get_pending_pdf_by_id(pid_int)
        if not pending:
            skipped += 1
            continue

        filename = pending.get('filename') or ''
        suggested = suggest_from_filename(filename)
        auto_code = _generate_staging_pdf_code()

        default_title = (
            suggested.get('title')
            or filename.replace('.pdf', '').replace('_', ' ').replace('-', ' ').title()
        )

        rows.append({
            'pending_id': pid_int,
            'filename': filename,
            'file_id': pending.get('file_id', ''),
            'file_unique_id': pending.get('file_unique_id', ''),
            'uploaded_by': pending.get('uploaded_by'),
            'uploaded_at': pending.get('uploaded_at', ''),
            'code': auto_code,
            'title': default_title,
            'subject': suggested.get('subject') or '',
            'curriculum': 'PL',
            'class': '',
            'chapter': suggested.get('chapter') or '',
            'tags': suggested.get('tags') or '',
            'is_premium': 0,
        })

    if not rows:
        flash('No valid pending PDFs found.', 'error')
        return redirect(url_for('admin_content.pdfs', tab='intake'))

    if skipped:
        flash(f'{skipped} pending PDF(s) could not be loaded and were skipped.',
              'warning')

    return render_template(
        'dashboard/admin/content/pdf_intake_bulk.html',
        rows=rows,
        subjects=get_all_subjects(),
        curricula=[('PL', 'Puntland'), ('SO', 'Somalia'), ('SL', 'Somaliland')],
        classes=['7aad', '8aad', 'F3', 'F4'],
    )


@admin_content_bp.route('/pdfs/intake/publish-commit', methods=['POST'],
                        endpoint='pdf_intake_publish_commit')
@admin_can('pdfs.publish')
def pdf_intake_publish_commit():
    """
    Step 2 of direct publish: receives edited rows, stages + publishes
    each PDF directly to the library, and creates tracking rows in
    unverified_pdfs.
    """
    if not validate_csrf():
        abort(403)

    rows_json = request.form.get('rows_json') or ''
    if not rows_json:
        flash('No data submitted.', 'error')
        return redirect(url_for('admin_content.pdfs', tab='intake'))

    try:
        rows = json.loads(rows_json)
    except json.JSONDecodeError as e:
        flash(f'Invalid data: {e}', 'error')
        return redirect(url_for('admin_content.pdfs', tab='intake'))

    if not isinstance(rows, list) or not rows:
        flash('No rows to publish.', 'error')
        return redirect(url_for('admin_content.pdfs', tab='intake'))

    from bot.db import (
        get_pending_pdf_by_id, insert_bot_pdf,
        delete_pending_pdf, get_bot_pdf_by_code,
    )

    published = 0
    failed = 0
    failures = []

    for idx, row in enumerate(rows, 1):
        try:
            pending_id = int(row.get('pending_id'))
        except (ValueError, TypeError):
            failed += 1
            failures.append(f'Row {idx}: invalid pending id')
            continue

        code = (row.get('code') or '').strip().upper()
        title = (row.get('title') or '').strip()
        subject = (row.get('subject') or '').strip()
        curriculum = (row.get('curriculum') or 'PL').strip()
        class_filter = (row.get('class') or '').strip()
        chapter = (row.get('chapter') or '').strip()
        tags = (row.get('tags') or '').strip()
        is_premium = 1 if row.get('is_premium') else 0

        # ---- Validation ----
        if not code or not _validate_pdf_code_format(code):
            failed += 1
            failures.append(f'#{pending_id}: invalid code "{code}"')
            continue
        if not title:
            failed += 1
            failures.append(f'#{pending_id}: title required')
            continue
        if not subject:
            failed += 1
            failures.append(f'#{pending_id}: subject required')
            continue
        if curriculum not in ('PL', 'SO', 'SL'):
            failed += 1
            failures.append(f'#{pending_id}: invalid curriculum')
            continue
        if class_filter and class_filter not in ('7aad', '8aad', 'F3', 'F4'):
            failed += 1
            failures.append(f'#{pending_id}: invalid class')
            continue

        # ---- Code availability ----
        if get_bot_pdf_by_code(code) or get_pdf_by_code(code):
            failed += 1
            failures.append(f'#{pending_id}: code "{code}" already used')
            continue

        # ---- Fetch pending row ----
        pending = get_pending_pdf_by_id(pending_id)
        if not pending:
            failed += 1
            failures.append(f'#{pending_id}: not found')
            continue

        # ---- Stage ----
        bot_pdf_id = insert_bot_pdf({
            'code': code,
            'title': title,
            'description': '',
            'curriculum': curriculum,
            'class': class_filter,
            'subject': subject,
            'chapter': chapter,
            'tags': tags,
            'is_premium': is_premium,
            'file_id': pending['file_id'],
            'file_unique_id': pending['file_unique_id'],
            'uploaded_by': pending['uploaded_by'],
            'original_filename': pending.get('filename', ''),
        })

        if not bot_pdf_id:
            failed += 1
            failures.append(f'#{pending_id}: staging insert failed')
            continue

        # ---- Publish to main ----
        ok, msg = publish_bot_pdf_to_main(bot_pdf_id)
        if not ok:
            failed += 1
            failures.append(f'#{pending_id}: publish failed ({msg})')
            continue

        # ---- Track as unverified ----
        try:
            main_pdf = get_pdf_by_code(code)
            if main_pdf:
                execute_with_retry(
                    "INSERT OR IGNORE INTO unverified_pdfs (pdf_id) VALUES (?)",
                    (main_pdf['id'],),
                    commit=True,
                )
        except Exception as e:
            logger.warning(
                f"unverified_pdfs insert failed for code {code}: {e}"
            )

        # ---- Cleanup pending ----
        delete_pending_pdf(pending_id)
        published += 1

    # ---- Audit ----
    write_audit(
        action='pdf.intake.super_publish',
        target_type='pdf',
        before=None,
        after={
            'published': published,
            'failed': failed,
            'sample_failures': failures[:5],
        },
        severity='warning',
    )

    if published:
        flash(
            f'{published} PDF{"s" if published != 1 else ""} published '
            f'directly to the library.',
            'success',
        )
    if failed:
        first = failures[0] if failures else ''
        flash(f'{failed} failed. First: {first}', 'error')

    return redirect(url_for('admin_content.pdfs', tab='unverified'))


# ============================================================
# PDFs — STAGING (bot pdfs)
# ============================================================

@admin_content_bp.route('/pdfs/staging/<int:staging_id>/edit',
                        methods=['GET', 'POST'],
                        endpoint='pdf_staging_edit')
@admin_can('pdfs.edit')
def pdf_staging_edit(staging_id):
    from bot.db import get_bot_pdf_by_id, update_bot_pdf

    bot_pdf = get_bot_pdf_by_id(staging_id)
    if not bot_pdf:
        abort(404)

    if request.method == 'POST':
        if not validate_csrf():
            abort(403)

        data = {
            'title': (request.form.get('title') or '').strip(),
            'description': (request.form.get('description') or '').strip(),
            'curriculum': (request.form.get('curriculum') or 'PL').strip(),
            'class': (request.form.get('class') or '').strip(),
            'subject': (request.form.get('subject') or '').strip(),
            'chapter': (request.form.get('chapter') or '').strip(),
            'tags': (request.form.get('tags') or '').strip(),
            'is_premium': 1 if request.form.get('is_premium') == 'on' else 0,
        }

        if not data['title'] or not data['subject']:
            flash('Title and Subject are required.', 'error')
            return redirect(url_for('admin_content.pdf_staging_edit',
                                    staging_id=staging_id))

        if update_bot_pdf(staging_id, data):
            write_audit(
                action='pdf.staging.updated',
                target_type='pdf_staging',
                target_id=staging_id,
                before={'title': bot_pdf.get('title'),
                        'subject': bot_pdf.get('subject')},
                after={'title': data['title'], 'subject': data['subject']},
                severity='info',
            )
            flash('Staging PDF updated.', 'success')
            return redirect(url_for('admin_content.pdfs', tab='staging'))

        flash('Failed to update staging PDF.', 'error')
        return redirect(url_for('admin_content.pdf_staging_edit',
                                staging_id=staging_id))

    return render_template(
        'dashboard/admin/content/pdf_staging_edit.html',
        pdf=bot_pdf,
        subjects=get_all_subjects(),
        curricula=[('PL', 'Puntland'), ('SO', 'Somalia'),
                   ('SL', 'Somaliland')],
        classes=['7aad', '8aad', 'F3', 'F4'],
    )


@admin_content_bp.route('/pdfs/staging/<int:staging_id>/delete',
                        methods=['POST'], endpoint='pdf_staging_delete')
@admin_can('pdfs.delete')
def pdf_staging_delete(staging_id):
    if not validate_csrf():
        abort(403)

    from bot.db import get_bot_pdf_by_id, delete_bot_pdf
    bot_pdf = get_bot_pdf_by_id(staging_id)
    if not bot_pdf:
        abort(404)

    if delete_bot_pdf(staging_id):
        write_audit(
            action='pdf.staging.deleted',
            target_type='pdf_staging',
            target_id=staging_id,
            before={'code': bot_pdf.get('code'),
                    'title': bot_pdf.get('title')},
            after=None,
            severity='warning',
        )
        flash('Staging PDF deleted.', 'success')
    else:
        flash('Failed to delete staging PDF.', 'error')

    return redirect(url_for('admin_content.pdfs', tab='staging'))


# ============================================================
# PDFs — STAGING — publish to platform (SUPER ADMIN ONLY)
# ============================================================

@admin_content_bp.route('/pdfs/staging/publish',
                        methods=['POST'], endpoint='pdf_staging_publish')
@admin_can('pdfs.publish')
def pdf_staging_publish():
    if not validate_csrf():
        abort(403)

    ids = request.form.getlist('staging_ids')
    if not ids:
        flash('No PDFs selected.', 'error')
        return redirect(url_for('admin_content.pdfs', tab='staging'))

    succeeded = 0
    failed = 0
    failures = []

    for sid in ids:
        try:
            sid_int = int(sid)
        except (ValueError, TypeError):
            failed += 1
            continue
        ok, msg = publish_bot_pdf_to_main(sid_int)
        if ok:
            succeeded += 1
        else:
            failed += 1
            failures.append(f'#{sid_int}: {msg}')

    if succeeded:
        write_audit(
            action='pdf.publish_bulk',
            target_type='pdf',
            before=None,
            after={'succeeded': succeeded, 'failed': failed, 'ids': ids},
            severity='warning',
        )
        flash(f'{succeeded} PDF{"s" if succeeded != 1 else ""} '
              f'published to the platform.', 'success')
    if failed:
        first = failures[0] if failures else 'unknown error'
        flash(f'{failed} failed. First: {first}', 'error')

    return redirect(url_for('admin_content.pdfs', tab='staging'))


@admin_content_bp.route('/pdfs/staging/publish-all',
                        methods=['POST'], endpoint='pdf_staging_publish_all')
@admin_can('pdfs.publish')
def pdf_staging_publish_all():
    if not validate_csrf():
        abort(403)

    from bot.db import get_bot_pdfs
    try:
        all_pdfs = get_bot_pdfs(limit=10000, offset=0)
    except Exception as e:
        logger.error(f"publish-all load failed: {e}")
        flash('Could not load staging PDFs.', 'error')
        return redirect(url_for('admin_content.pdfs', tab='staging'))

    succeeded = 0
    failed = 0
    for p in all_pdfs:
        ok, _ = publish_bot_pdf_to_main(p['id'])
        if ok:
            succeeded += 1
        else:
            failed += 1

    if succeeded:
        write_audit(
            action='pdf.publish_all',
            target_type='pdf',
            before=None,
            after={'succeeded': succeeded, 'failed': failed},
            severity='warning',
        )
        flash(f'{succeeded} PDF{"s" if succeeded != 1 else ""} published.',
              'success')
    if failed:
        flash(f'{failed} already existed or failed to publish.', 'info')

    return redirect(url_for('admin_content.pdfs', tab='staging'))


# ============================================================
# PDFs — UNVERIFIED (review queue)
# ============================================================

@admin_content_bp.route('/pdfs/unverified/confirm', methods=['POST'],
                        endpoint='pdf_unverified_confirm')
@admin_can('pdfs.intake')
def pdf_unverified_confirm():
    """Any admin can mark unverified PDFs as reviewed."""
    if not validate_csrf():
        abort(403)

    ids = request.form.getlist('unverified_ids')
    if not ids:
        flash('No PDFs selected.', 'error')
        return redirect(url_for('admin_content.pdfs', tab='unverified'))

    clean_ids = []
    for i in ids:
        try:
            clean_ids.append(int(i))
        except (ValueError, TypeError):
            continue

    if not clean_ids:
        flash('No valid records selected.', 'error')
        return redirect(url_for('admin_content.pdfs', tab='unverified'))

    placeholders = ','.join('?' for _ in clean_ids)
    try:
        cursor = execute_with_retry(
            f"UPDATE unverified_pdfs SET confirmed = 1 "
            f"WHERE id IN ({placeholders})",
            clean_ids, commit=True,
        )
        count = cursor.rowcount or 0
        write_audit(
            action='pdf.unverified.confirm',
            target_type='unverified_pdf',
            before=None,
            after={'count': count, 'ids': clean_ids},
            severity='info',
        )
        flash(
            f'{count} PDF{"s" if count != 1 else ""} marked as reviewed.',
            'success',
        )
    except Exception as e:
        logger.error(f"unverified confirm failed: {e}")
        flash('Error updating records.', 'error')

    return redirect(url_for('admin_content.pdfs', tab='unverified'))


@admin_content_bp.route('/pdfs/unverified/delete', methods=['POST'],
                        endpoint='pdf_unverified_delete')
@admin_can('pdfs.publish')  # super admin only
def pdf_unverified_delete():
    """
    Remove tracking rows from the unverified queue.
    The PDF itself stays live in the library.
    """
    if not validate_csrf():
        abort(403)

    ids = request.form.getlist('unverified_ids')
    if not ids:
        flash('No records selected.', 'error')
        return redirect(url_for('admin_content.pdfs', tab='unverified'))

    clean_ids = []
    for i in ids:
        try:
            clean_ids.append(int(i))
        except (ValueError, TypeError):
            continue

    if not clean_ids:
        flash('No valid records selected.', 'error')
        return redirect(url_for('admin_content.pdfs', tab='unverified'))

    placeholders = ','.join('?' for _ in clean_ids)
    try:
        cursor = execute_with_retry(
            f"DELETE FROM unverified_pdfs WHERE id IN ({placeholders})",
            clean_ids, commit=True,
        )
        count = cursor.rowcount or 0
        write_audit(
            action='pdf.unverified.remove_from_queue',
            target_type='unverified_pdf',
            before={'ids': clean_ids, 'count': count},
            after=None,
            severity='warning',
        )
        flash(
            f'{count} record{"s" if count != 1 else ""} removed from the '
            f'review queue. The PDFs themselves remain in the library.',
            'info',
        )
    except Exception as e:
        logger.error(f"unverified delete failed: {e}")
        flash('Error removing records.', 'error')

    return redirect(url_for('admin_content.pdfs', tab='unverified'))