# blueprints/pdfs_bp.py
import os
from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort, send_file, Response, jsonify,
)
from db import (
    get_all_pdfs, get_pdf_by_code, get_pdf_by_id,
    increment_pdf_view, increment_pdf_view_by_code,
    get_pdf_distinct_subjects, get_pdf_distinct_classes,
    get_pdf_distinct_curricula,
)
from services.tier_service import (
    can_access_premium_resources, get_user_tier, get_feature_level,
)
from bot.utils import get_bot
from bot.db import get_bot_pdf_by_code
from config import Config
from history_logger import add_history_entry
import requests
import logging

logger = logging.getLogger(__name__)

pdfs_bp = Blueprint('pdfs', __name__, url_prefix='/pdfs')


@pdfs_bp.route('/')
def list_pdfs():
    """Public PDF listing – no login required."""
    subject_filter = request.args.get('subject', '')
    class_filter = request.args.get('class', '')
    curriculum_filter = request.args.get('curriculum', '')
    search_query = request.args.get('search', '').strip()

    user_id = session.get('user_id')
    if user_id:
        user_tier = get_user_tier(user_id)
        search_level = get_feature_level("resource_search", user_id=user_id)
        can_access_premium = can_access_premium_resources()
    else:
        user_tier = 'free'
        search_level = 0
        can_access_premium = False

    pdfs = get_all_pdfs(
        limit=100,
        offset=0,
        search=search_query if search_level > 0 else '',
        subject=subject_filter if search_level >= 1 else '',
        curriculum=curriculum_filter if search_level >= 2 else '',
        class_filter=class_filter if search_level >= 2 else ''
    )

    if not can_access_premium:
        pdfs = [p for p in pdfs if not p.get('is_premium', 0)]

    subjects = get_pdf_distinct_subjects() if search_level >= 1 else []
    classes = get_pdf_distinct_classes() if search_level >= 2 else []
    curricula = get_pdf_distinct_curricula() if search_level >= 2 else []

    return render_template('dashboard/pdfs.html',
                         pdfs=pdfs,
                         subjects=subjects,
                         classes=classes,
                         curricula=curricula,
                         subject_filter=subject_filter if search_level >= 1 else '',
                         class_filter=class_filter if search_level >= 2 else '',
                         curriculum_filter=curriculum_filter if search_level >= 2 else '',
                         search_query=search_query if search_level > 0 else '',
                         search_level=search_level,
                         user_tier=user_tier,
                         can_access_premium=can_access_premium,
                         is_logged_in=bool(user_id))


# ============================================================
# READ — opens the in-browser reader
# ============================================================

@pdfs_bp.route('/view/<pdf_id>')
def view_pdf(pdf_id):
    if 'user_id' not in session:
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

    # Bump the counter and reflect the fresh value on the page.
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
# DOWNLOAD — by id
# ============================================================

@pdfs_bp.route('/download/<pdf_id>')
def download_pdf(pdf_id):
    if 'user_id' not in session:
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


# ============================================================
# TELEGRAM DOWNLOAD — redirect to bot
# ============================================================

@pdfs_bp.route('/telegram/<code>')
def telegram_download(code):
    """Direct Telegram link – counts as a view since the user is
    proceeding to fetch the file from the bot."""
    pdf = get_pdf_by_code(code)
    if not pdf:
        flash('PDF not found.', 'error')
        return redirect(url_for('pdfs.list_pdfs'))

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
    """Stream a PDF from Telegram — counts as a view."""
    if 'user_id' not in session:
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

    # Count the view as soon as we know we're going to serve bytes.
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
    """Alias for /stream/<code> – used by the 'Preview' button."""
    return stream_pdf(code)