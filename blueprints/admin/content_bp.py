# ============================================================
# blueprints/admin/content_bp.py
# Content domain — questions, PDFs, bulk import, intake.
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
#   GET  /admin/pdfs                                 → library grid
#   GET  /admin/pdfs/<id>/edit                       → edit form
#   POST /admin/pdfs/<id>/edit                       → update
#   POST /admin/pdfs/<id>/delete                     → delete
#   GET  /admin/pdfs/broken                          → missing PDF codes
# ============================================================

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort, jsonify, Response,
)
import json
import logging
import re
import time

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
)
from subjects_config import get_all_subjects, get_subject, get_all_subject_codes
from utils import validate_csrf
from services.admin.guards import admin_can
from services.admin.audit import write_audit

logger = logging.getLogger(__name__)

admin_content_bp = Blueprint('admin_content', __name__, url_prefix='/admin')


# ============================================================
# HELPERS
# ============================================================

_PDF_CODE_RE = re.compile(r'^[A-Z0-9]{4}-[A-Z0-9]{4}$')
_PDF_SIZE_CAP_BYTES = 20 * 1024 * 1024  # 20 MB cap for Telegram preview

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


# ============================================================
# QUESTIONS — LIST
# ============================================================

@admin_content_bp.route('/questions', methods=['GET'],
                        endpoint='questions')
@admin_can('questions.view')
def questions():
    search = (request.args.get('search') or '').strip()
    subject_code = (request.args.get('subject') or '').strip()
    pdf_filter = (request.args.get('pdf') or '').strip()
    status_filter = (request.args.get('status') or '').strip()
    sort = (request.args.get('sort') or 'newest').strip()
    page = max(1, int(request.args.get('page') or 1))
    per_page = 20

    questions_list, total = get_questions_paginated(
        search=search,
        subject_code=subject_code,
        pdf_filter=pdf_filter,
        status_filter=status_filter,
        sort=sort,
        page=page,
        per_page=per_page,
    )

    stats = get_question_stats()
    filter_options = get_questions_filter_options()
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    codes_in_page = [q['pdf_code'] for q in questions_list if q.get('pdf_code')]
    pdf_map = check_pdf_codes_exist(codes_in_page) if codes_in_page else {}

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
        status_filter=status_filter,
        sort=sort,
    )


# ============================================================
# QUESTIONS — NEW
# ============================================================

@admin_content_bp.route('/questions/new', methods=['GET'],
                        endpoint='question_new')
@admin_can('questions.create')
def question_new():
    return render_template(
        'dashboard/admin/content/question_edit.html',
        question=None,
        subjects=get_all_subjects(),
    )


@admin_content_bp.route('/questions/new', methods=['POST'],
                        endpoint='question_create')
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
            after={
                'subject_code': subject_code,
                'pdf_code': pdf_code,
                'difficulty': difficulty,
                'status': status,
            },
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
            before={
                'subject_code': question.get('subject_code'),
                'pdf_code': question.get('pdf_code'),
                'status': question.get('status'),
            },
            after={
                'subject_code': subject_code,
                'pdf_code': pdf_code,
                'status': status,
            },
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

    if delete_question(question_id):  # sets status='archived'
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
                   COUNT(*) AS attempts,
                   SUM(CASE WHEN json_extract(value, '$.correct') = 0 THEN 1 ELSE 0 END) AS misses
            FROM quiz_attempts qa, json_each(qa.answers) AS value
            JOIN questions q ON q.id = CAST(json_extract(value, '$.question_id') AS INTEGER)
            WHERE qa.completed_at >= datetime('now', '-30 days')
            GROUP BY q.id
            HAVING misses >= 3
            ORDER BY misses DESC, attempts DESC
            LIMIT 50
        """)
        for row in cursor.fetchall():
            r = dict(row)
            subj = get_subject(r['subject_code'])
            r['subject_name'] = subj['name'] if subj else r['subject_code']
            r['miss_rate'] = round(100.0 * r['misses'] / r['attempts'], 1) if r['attempts'] else 0
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

    # --- Main DB ---
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

        # No file_url → try bot for a file_id
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

    # --- Bot DB only ---
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

@admin_content_bp.route('/bulk-import', methods=['GET'],
                        endpoint='bulk_import')
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

    # ---- Validation ----
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
        options_dict = {
            labels[i]: str(opts[i]).strip()
            for i in range(len(opts))
        }

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
            errors=errors,
            duplicates=duplicates,
            warnings=warnings,
            subject_code=subject_code,
            chapter=chapter,
            pdf_code=pdf_code,
            total_questions=len(questions_raw),
        )

    if not questions_to_import:
        flash('No valid questions to import.', 'error')
        return redirect(url_for('admin_content.bulk_import'))

    try:
        result = bulk_create_questions(questions_to_import,
                                        session.get('user_id'))
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
            after={
                'subject_code': subject_code,
                'imported': imported,
                'pdf_code': pdf_code,
            },
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


# ============================================================
# BULK TEMPLATE DOWNLOAD
# ============================================================

@admin_content_bp.route('/bulk-template', methods=['GET'],
                        endpoint='bulk_template')
@admin_can('questions.bulk_import')
def bulk_template():
    template = {
        "metadata": {
            "subject_code": "geography",
            "chapter": "Chapter 1: Introduction"
        },
        "questions": [
            {"tags": ["geography", "capitals"], "difficulty": 2,
             "question": "What is the capital of Somalia?",
             "options": ["Mogadishu", "Hargeisa", "Kismayo"],
             "correct": 1,
             "explanation": "Mogadishu has been the capital since 1960.",
             "pdf_page": 12},
            {"tags": ["geography", "rivers"], "difficulty": 3,
             "question": "Which river flows through Mogadishu?",
             "options": ["Shabelle", "Jubba", "Nile"],
             "correct": 1, "pdf_page": 14},
            {"tags": ["geography"], "difficulty": 1,
             "question": "How many regions does Somalia have?",
             "options": ["18", "15", "20"],
             "correct": 0},
        ]
    }
    body = json.dumps(template, indent=2, ensure_ascii=False)
    return Response(
        body,
        mimetype='application/json',
        headers={
            'Content-Disposition': 'attachment; filename=bulk_template.json'
        },
    )


# ============================================================
# BULK PREVIEW (AJAX)
# ============================================================

@admin_content_bp.route('/bulk-preview', methods=['POST'],
                        endpoint='bulk_preview')
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
                'code': pdf_code,
                'valid': True,
                'exists': info.get('exists', False),
                'source': info.get('source'),
                'title': info.get('title', ''),
                'is_premium': info.get('is_premium', False),
            }
        else:
            pdf_code_valid = False
            pdf_info_payload = {
                'code': pdf_code,
                'valid': False,
                'exists': False,
                'reason': 'invalid_format',
            }

    preview = []
    unknown_codes = set()
    linked_count = 0
    total = len(data['questions'])

    for idx, q in enumerate(data['questions'], 1):
        if not isinstance(q, dict):
            preview.append({
                'index': idx, 'question': '(invalid entry)',
                'difficulty': 1, 'options_count': 0,
                'has_explanation': False, 'tags': '',
                'pdf_code': '', 'pdf_page': None,
                'pdf_exists': False, 'pdf_title': '', 'pdf_source': None,
                'orphan': False, 'invalid_code': False,
            })
            continue

        q_text = (q.get('question') or '') or ''
        q_short = q_text[:70] + ('…' if len(q_text) > 70 else '')

        page = _normalize_pdf_page(q.get('pdf_page'))
        orphan = bool(page and not pdf_code)
        if orphan:
            page = None

        entry = {
            'index': idx,
            'question': q_short,
            'difficulty': int(q.get('difficulty', 1) or 1) if str(q.get('difficulty', 1)).isdigit() else 1,
            'options_count': len(q.get('options', []) or []),
            'has_explanation': bool((q.get('explanation') or '').strip()),
            'tags': ', '.join(q.get('tags', []))[:40] if isinstance(q.get('tags'), list) else str(q.get('tags', ''))[:40],
            'pdf_code': pdf_code or '',
            'pdf_page': page,
            'pdf_exists': False,
            'pdf_title': '',
            'pdf_source': None,
            'orphan': orphan,
            'invalid_code': not pdf_code_valid,
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
        'subject_code': subject_code,
        'chapter': chapter,
        'pdf_code': pdf_code or '',
        'pdf_code_valid': pdf_code_valid,
        'pdf_info': pdf_info_payload,
        'total': total,
        'linked_count': linked_count,
        'unknown_codes': sorted(unknown_codes),
        'preview': preview[:30],
        'truncated': total > 30,
    })


# ============================================================
# PDFs — LIBRARY GRID
# ============================================================

@admin_content_bp.route('/pdfs', methods=['GET'], endpoint='pdfs')
@admin_can('pdfs.view')
def pdfs():
    search = (request.args.get('search') or '').strip()
    subject_filter = (request.args.get('subject') or '').strip()
    curriculum_filter = (request.args.get('curriculum') or '').strip()
    class_filter = (request.args.get('class') or '').strip()

    pdf_list = get_all_pdfs(
        limit=200,
        offset=0,
        search=search,
        subject=subject_filter,
        curriculum=curriculum_filter,
        class_filter=class_filter,
    )

    return render_template(
        'dashboard/admin/content/pdfs.html',
        pdfs=pdf_list,
        subjects=get_all_subjects(),
        curricula=get_pdf_distinct_curricula(),
        classes=get_pdf_distinct_classes(),
        search=search,
        subject_filter=subject_filter,
        curriculum_filter=curriculum_filter,
        class_filter=class_filter,
    )


# ============================================================
# PDFs — EDIT
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
        """, (
            title, description, curriculum, class_filter,
            subject, chapter, tags, is_premium, pdf_id,
        ), commit=True)

        write_audit(
            action='pdf.update',
            target_type='pdf',
            target_id=pdf_id,
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


# ============================================================
# PDFs — DELETE
# ============================================================

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
        write_audit(
            action='pdf.delete',
            target_type='pdf',
            target_id=pdf_id,
            before={'title': pdf.get('title'), 'code': pdf.get('code')},
            after=None,
            severity='warning',
        )
        flash('PDF deleted.', 'success')
    else:
        flash('Error deleting PDF.', 'error')

    return redirect(url_for('admin_content.pdfs'))


# ============================================================
# PDFs — BROKEN (question links to missing PDF code)
# ============================================================

@admin_content_bp.route('/pdfs/broken', methods=['GET'],
                        endpoint='pdfs_broken')
@admin_can('pdfs.view')
def pdfs_broken():
    cursor = execute_with_retry("""
        SELECT pdf_code, COUNT(*) AS count,
               MIN(question_text) AS sample_question
        FROM questions
        WHERE status = 'active'
          AND pdf_code IS NOT NULL
          AND pdf_code != ''
        GROUP BY pdf_code
    """)
    candidates = [dict(r) for r in cursor.fetchall()]

    codes = [c['pdf_code'] for c in candidates]
    pdf_map = check_pdf_codes_exist(codes) if codes else {}

    rows = []
    for c in candidates:
        info = pdf_map.get(c['pdf_code'], {})
        if not info.get('exists'):
            rows.append(c)

    return render_template(
        'dashboard/admin/content/pdf_broken.html',
        rows=rows,
    )