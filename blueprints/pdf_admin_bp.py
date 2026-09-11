# blueprints/pdf_admin_bp.py – redesigned for new PDF workflow

import os
import logging
import secrets
import string
import time
from flask import Blueprint, render_template, request, session, flash, redirect, url_for, abort, send_file, jsonify
from functools import wraps
from config import Config
from bot.utils import get_bot
from bot.db import (
    get_pending_pdf_by_id, get_pending_pdf_list, count_pending_pdfs,
    delete_pending_pdf, insert_bot_pdf, get_bot_pdf_by_id,
    get_bot_pdfs, count_bot_pdfs, update_bot_pdf, delete_bot_pdf
)
from db import publish_bot_pdf_to_main, get_main_pdf_count
from subjects_config import get_all_subjects, LOCATION_CURRICULA
from utils import get_somali_time_display

logger = logging.getLogger(__name__)

pdf_admin_bp = Blueprint('pdf_admin', __name__, url_prefix='/pdf-admin')

# Auth config
ADMIN_PASSWORD = Config.PDF_ADMIN_PASSWORD
SUPER_ADMIN_PASSWORD = Config.PDF_SUPER_ADMIN_PASSWORD
SESSION_KEY = 'pdf_admin_logged_in'
ROLE_KEY = 'pdf_admin_role'
SESSION_TIMEOUT = Config.PDF_ADMIN_SESSION_TIMEOUT

# Warn if default passwords are used
if ADMIN_PASSWORD == 'admin123' or SUPER_ADMIN_PASSWORD == 'super123':
    logger.critical(
        "PDF_ADMIN_PASSWORD and/or PDF_SUPER_ADMIN_PASSWORD are set to default! "
        "Change them immediately in .env for security."
    )

def pdf_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get(SESSION_KEY):
            flash('Please log in to access the PDF admin panel.', 'error')
            return redirect(url_for('pdf_admin.login'))
        if session.get('pdf_admin_login_time'):
            if time.time() - session['pdf_admin_login_time'] > SESSION_TIMEOUT:
                session.clear()
                flash('Session expired. Please log in again.', 'error')
                return redirect(url_for('pdf_admin.login'))
        return f(*args, **kwargs)
    return decorated

def super_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get(SESSION_KEY) or session.get(ROLE_KEY) != 'super_admin':
            flash('Super admin access required.', 'error')
            return redirect(url_for('pdf_admin.dashboard'))
        return f(*args, **kwargs)
    return decorated

# ---- Utilities ----

def generate_pdf_code():
    """Generate a unique PDF code (format: XXXX-XXXX)."""
    chars = string.ascii_uppercase + '123456789'
    while True:
        code = ''.join(secrets.choice(chars) for _ in range(4)) + '-' + ''.join(secrets.choice(chars) for _ in range(4))
        from bot.db import get_bot_pdf_by_code
        if not get_bot_pdf_by_code(code):
            return code

# ---- Routes ----

@pdf_admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get(SESSION_KEY):
        return redirect(url_for('pdf_admin.dashboard'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        if not password:
            flash('Password is required.', 'error')
            return render_template('pdf_admin/login.html')

        role = None
        if password == ADMIN_PASSWORD:
            role = 'admin'
        elif password == SUPER_ADMIN_PASSWORD:
            role = 'super_admin'

        if role:
            session[SESSION_KEY] = True
            session[ROLE_KEY] = role
            session['pdf_admin_login_time'] = int(time.time())
            flash(f'Logged in as {role.replace("_", " ").title()}.', 'success')
            logger.info(f"PDF Admin login successful: {role}")
            return redirect(url_for('pdf_admin.dashboard'))
        else:
            flash('Invalid password.', 'error')
            logger.warning("PDF Admin login failed.")

    return render_template('pdf_admin/login.html')

@pdf_admin_bp.route('/logout')
def logout():
    session.pop(SESSION_KEY, None)
    session.pop(ROLE_KEY, None)
    session.pop('pdf_admin_login_time', None)
    flash('Logged out.', 'info')
    return redirect(url_for('pdf_admin.login'))

@pdf_admin_bp.route('/')
@pdf_admin_required
def dashboard():
    pending_count = count_pending_pdfs()
    pending_list = get_pending_pdf_list(limit=5)

    bot_pdfs = []
    main_count = 0
    if session.get(ROLE_KEY) == 'super_admin':
        bot_pdfs = get_bot_pdfs(limit=20, offset=0)
        main_count = get_main_pdf_count()

    return render_template('pdf_admin/dashboard.html',
                         pending_count=pending_count,
                         pending_list=pending_list,
                         is_super_admin=(session.get(ROLE_KEY) == 'super_admin'),
                         bot_pdfs=bot_pdfs,
                         main_count=main_count)

@pdf_admin_bp.route('/pending')
@pdf_admin_required
def pending_list():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    pending_list = get_pending_pdf_list(limit=per_page, offset=offset)
    total = count_pending_pdfs()
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    return render_template('pdf_admin/pending_list.html',
                         pending_list=pending_list,
                         page=page,
                         total_pages=total_pages)

@pdf_admin_bp.route('/pending/<int:pending_id>/process', methods=['GET', 'POST'])
@pdf_admin_required
def process_pending(pending_id):
    pending = get_pending_pdf_by_id(pending_id)
    if not pending:
        flash('Pending PDF not found.', 'error')
        return redirect(url_for('pdf_admin.pending_list'))

    subjects = get_all_subjects()
    curricula = list(LOCATION_CURRICULA.keys())
    classes = ['7aad', '8aad', 'F3', 'F4']

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        if not code:
            code = generate_pdf_code()
        else:
            from bot.db import get_bot_pdf_by_code
            if get_bot_pdf_by_code(code):
                flash('This code is already taken. Please generate a new one.', 'error')
                return render_template('pdf_admin/process.html', pending=pending,
                                     subjects=subjects, curricula=curricula, classes=classes,
                                     code=code, is_edit=False)

        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        curriculum = request.form.get('curriculum', 'PL')
        class_filter = request.form.get('class', '')
        subject = request.form.get('subject', '').strip()
        chapter = request.form.get('chapter', '').strip()
        tags = request.form.get('tags', '').strip()
        is_premium = 1 if request.form.get('is_premium') == 'on' else 0

        if not title or not subject:
            flash('Title and Subject are required.', 'error')
            return render_template('pdf_admin/process.html', pending=pending,
                                 subjects=subjects, curricula=curricula, classes=classes,
                                 code=code, is_edit=False)
        if curriculum not in curricula:
            flash('Invalid curriculum.', 'error')
            return render_template('pdf_admin/process.html', pending=pending,
                                 subjects=subjects, curricula=curricula, classes=classes,
                                 code=code, is_edit=False)
        if class_filter and class_filter not in classes:
            flash('Invalid class.', 'error')
            return render_template('pdf_admin/process.html', pending=pending,
                                 subjects=subjects, curricula=curricula, classes=classes,
                                 code=code, is_edit=False)

        pdf_data = {
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
            'uploaded_by': pending['uploaded_by']
        }
        bot_pdf_id = insert_bot_pdf(pdf_data)
        if bot_pdf_id:
            delete_pending_pdf(pending_id)
            flash(f'PDF fulfilled successfully! Code: {code}', 'success')
            return redirect(url_for('pdf_admin.dashboard'))
        else:
            flash('Failed to save PDF. Please try again.', 'error')
            return render_template('pdf_admin/process.html', pending=pending,
                                 subjects=subjects, curricula=curricula, classes=classes,
                                 code=code, is_edit=False)

    auto_code = generate_pdf_code()
    return render_template('pdf_admin/process.html',
                         pending=pending,
                         subjects=subjects,
                         curricula=curricula,
                         classes=classes,
                         code=auto_code,
                         is_edit=False)

@pdf_admin_bp.route('/bot-pdfs')
@super_admin_required
def bot_pdf_list():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    search = request.args.get('search', '').strip()
    subject = request.args.get('subject', '').strip()
    curriculum = request.args.get('curriculum', '').strip()
    class_filter = request.args.get('class', '').strip()

    bot_pdfs = get_bot_pdfs(limit=per_page, offset=offset,
                            search=search, subject=subject,
                            curriculum=curriculum, class_filter=class_filter)
    total = count_bot_pdfs(search=search, subject=subject,
                           curriculum=curriculum, class_filter=class_filter)
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    subjects_list = get_all_subjects()
    curricula = list(LOCATION_CURRICULA.keys())
    classes = ['7aad', '8aad', 'F3', 'F4']

    return render_template('pdf_admin/bot_pdf_list.html',
                         bot_pdfs=bot_pdfs,
                         page=page,
                         total_pages=total_pages,
                         search=search,
                         subject=subject,
                         curriculum=curriculum,
                         class_filter=class_filter,
                         subjects=subjects_list,
                         curricula=curricula,
                         classes=classes)

@pdf_admin_bp.route('/bot-pdfs/<int:pdf_id>/edit', methods=['GET', 'POST'])
@super_admin_required
def edit_bot_pdf(pdf_id):
    bot_pdf = get_bot_pdf_by_id(pdf_id)
    if not bot_pdf:
        flash('PDF not found.', 'error')
        return redirect(url_for('pdf_admin.bot_pdf_list'))

    subjects = get_all_subjects()
    curricula = list(LOCATION_CURRICULA.keys())
    classes = ['7aad', '8aad', 'F3', 'F4']

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        curriculum = request.form.get('curriculum', 'PL')
        class_filter = request.form.get('class', '')
        subject = request.form.get('subject', '').strip()
        chapter = request.form.get('chapter', '').strip()
        tags = request.form.get('tags', '').strip()
        is_premium = 1 if request.form.get('is_premium') == 'on' else 0

        if not title or not subject:
            flash('Title and Subject are required.', 'error')
            return render_template('pdf_admin/edit_bot_pdf.html',
                                 bot_pdf=bot_pdf, subjects=subjects,
                                 curricula=curricula, classes=classes)
        if curriculum not in curricula:
            flash('Invalid curriculum.', 'error')
            return render_template('pdf_admin/edit_bot_pdf.html',
                                 bot_pdf=bot_pdf, subjects=subjects,
                                 curricula=curricula, classes=classes)

        update_data = {
            'title': title,
            'description': description,
            'curriculum': curriculum,
            'class': class_filter,
            'subject': subject,
            'chapter': chapter,
            'tags': tags,
            'is_premium': is_premium
        }
        if update_bot_pdf(pdf_id, update_data):
            flash('PDF updated successfully.', 'success')
            return redirect(url_for('pdf_admin.bot_pdf_list'))
        else:
            flash('Failed to update PDF.', 'error')

    return render_template('pdf_admin/edit_bot_pdf.html',
                         bot_pdf=bot_pdf, subjects=subjects,
                         curricula=curricula, classes=classes)

@pdf_admin_bp.route('/bot-pdfs/publish', methods=['POST'])
@super_admin_required
def publish_bot_pdfs():
    pdf_ids = request.form.getlist('pdf_ids')
    if not pdf_ids:
        flash('No PDFs selected.', 'error')
        return redirect(url_for('pdf_admin.bot_pdf_list'))

    published = 0
    failed = 0
    for pdf_id in pdf_ids:
        success, msg = publish_bot_pdf_to_main(pdf_id)
        if success:
            published += 1
        else:
            failed += 1
            logger.warning(f"Publish failed for {pdf_id}: {msg}")

    if published > 0:
        flash(f'Published {published} PDFs successfully.', 'success')
    if failed > 0:
        flash(f'Failed to publish {failed} PDFs. Check logs.', 'error')
    return redirect(url_for('pdf_admin.bot_pdf_list'))

@pdf_admin_bp.route('/bot-pdfs/publish-all', methods=['POST'])
@super_admin_required
def publish_all_bot_pdfs():
    all_pdfs = get_bot_pdfs(limit=99999, offset=0)
    if not all_pdfs:
        flash('No bot PDFs to publish.', 'error')
        return redirect(url_for('pdf_admin.bot_pdf_list'))

    published = 0
    failed = 0
    for pdf in all_pdfs:
        success, msg = publish_bot_pdf_to_main(pdf['id'])
        if success:
            published += 1
        else:
            failed += 1
            logger.warning(f"Publish all failed for {pdf['id']}: {msg}")

    flash(f'Published {published} out of {len(all_pdfs)} PDFs. Failed: {failed}', 'info')
    return redirect(url_for('pdf_admin.bot_pdf_list'))

@pdf_admin_bp.route('/pending/<int:pending_id>/preview')
@pdf_admin_required
def preview_pdf(pending_id):
    pending = get_pending_pdf_by_id(pending_id)
    if not pending:
        abort(404)

    try:
        bot = get_bot()
        file_info = bot.get_file(pending['file_id'])
        downloaded = bot.download_file(file_info.file_path)
        return send_file(
            downloaded,
            mimetype='application/pdf',
            as_attachment=False,
            download_name=pending.get('filename', 'document.pdf')
        )
    except Exception as e:
        logger.error(f"Preview error: {e}")
        abort(500)

@pdf_admin_bp.route('/generate-code')
@pdf_admin_required
def generate_code_ajax():
    code = generate_pdf_code()
    return jsonify({'code': code})