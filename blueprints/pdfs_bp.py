# blueprints/pdfs_bp.py
import os
import re
import secrets
import random as _random
from urllib.parse import quote
from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort, send_file, Response, jsonify,
)
from db import (
    get_all_pdfs, get_all_pdfs_shuffled, get_main_pdf_count,
    get_pdf_by_code, get_pdf_by_id,
    increment_pdf_view, increment_pdf_view_by_code,
    get_pdf_distinct_subjects, get_pdf_distinct_classes,
    get_pdf_distinct_curricula,
    save_pdf_for_user, unsave_pdf_for_user, is_pdf_saved,
    get_user_saved_pdf_ids, count_user_saved_pdfs,
    create_pdf_report, user_reported_pdf, get_user_reported_pdf_ids,
)
from services.tier_service import (
    can_access_premium_resources, get_user_tier, get_feature_level,
    get_saved_content_limit, get_resource_downloads_remaining,
    get_resource_download_limit, consume_resource_download,
)
from bot.utils import get_bot
from bot.db import get_bot_pdf_by_code, get_bot_pdfs_by_codes
from config import Config
from history_logger import add_history_entry
from utils import SOMALI_TIMEZONE
import requests
import logging

logger = logging.getLogger(__name__)

pdfs_bp = Blueprint('pdfs', __name__, url_prefix='/pdfs')

PER_PAGE = 50
_VALID_SORTS = {'shuffle', 'newest', 'oldest', 'popular', 'title_asc', 'title_desc'}
_DEFAULT_SORT = 'shuffle'

# Session key for the shuffle seed. It persists across pagination but is
# regenerated on every fresh visit (no page param) or explicit reseed.
_SHUF_SEED_KEY = 'pdf_shuffle_seed'


# ============================================================
# SHUFFLE SEED HELPERS
# ============================================================

def _ensure_shuffle_seed():
    """Return the session's shuffle seed, generating one on first use."""
    seed = session.get(_SHUF_SEED_KEY)
    if not seed:
        seed = secrets.token_hex(4)
        session[_SHUF_SEED_KEY] = seed
        session.modified = True
    return seed


def _reseed_shuffle():
    """Force a new shuffle seed for the current session."""
    seed = secrets.token_hex(4)
    session[_SHUF_SEED_KEY] = seed
    session.modified = True
    return seed


# ============================================================
# HELPERS
# ============================================================

def _log_guest_attempt(action, pdf=None, pdf_code=None):
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


def _current_path_with_query():
    path = request.path or '/'
    qs = request.query_string.decode() if request.query_string else ''
    return path + ('?' + qs if qs else '')


def _normalise_remaining(raw):
    if raw is None or raw >= 999 or raw < 0:
        return None
    return raw


def _seconds_to_quota_reset():
    now = datetime.now(SOMALI_TIMEZONE)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return max(0, int((tomorrow - now).total_seconds()))


def _format_reset_time(seconds):
    if seconds <= 0:
        return "0s"
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _safe_filename(title, fallback='document'):
    base = re.sub(r'[^\w\-_. ]+', '_', (title or fallback)).strip() or fallback
    return (base[:120] + '.pdf') if not base.lower().endswith('.pdf') else base[:120]


# ============================================================
# LIST
# ============================================================

@pdfs_bp.route('/')
def list_pdfs():
    shared_pdf_id_raw = request.args.get('pdf')
    shared_pdf = None
    shared_not_found = False
    showing_shared = False

    if shared_pdf_id_raw:
        if 'user_id' not in session:
            flash('Please login to view this shared PDF.', 'info')
            return redirect(url_for('auth.login', next=_current_path_with_query()))

        try:
            shared_id_int = int(shared_pdf_id_raw)
        except (ValueError, TypeError):
            shared_id_int = 0

        if shared_id_int > 0:
            try:
                shared_pdf = get_pdf_by_id(shared_id_int)
            except Exception as e:
                logger.warning(f"shared pdf lookup failed: {e}")
                shared_pdf = None

        if shared_pdf:
            showing_shared = True
        else:
            shared_not_found = True

    subject_filter    = (request.args.get('subject') or '').strip()
    class_filter      = (request.args.get('class') or '').strip()
    curriculum_filter = (request.args.get('curriculum') or '').strip()
    search_query      = (request.args.get('search') or '').strip()
    saved_only        = request.args.get('saved') == '1'

    sort = (request.args.get('sort') or _DEFAULT_SORT).strip()
    if sort not in _VALID_SORTS:
        sort = _DEFAULT_SORT

    try:
        page = int(request.args.get('page') or 1)
    except (ValueError, TypeError):
        page = 1
    if page < 1:
        page = 1

    user_id = session.get('user_id')
    remaining_downloads = None
    downloads_unlimited = False
    download_limit = 20
    user_tier = 'free'
    search_level = 0
    can_access_premium = False

    if user_id:
        user_tier = get_user_tier(user_id)
        search_level = get_feature_level("resource_search", user_id=user_id)
        can_access_premium = can_access_premium_resources()

        try:
            raw_remaining = get_resource_downloads_remaining(user_id)
            remaining_downloads = _normalise_remaining(raw_remaining)
            downloads_unlimited = remaining_downloads is None
        except Exception as e:
            logger.warning(f"downloads remaining lookup failed: {e}")
            remaining_downloads = None
            downloads_unlimited = False

        try:
            dl_raw = get_resource_download_limit(user_id)
            if dl_raw and dl_raw < 999:
                download_limit = int(dl_raw)
        except Exception:
            pass
    else:
        saved_only = False

    effective_search     = search_query       if search_level > 0  else ''
    effective_subject    = subject_filter     if search_level >= 1 else ''
    effective_curriculum = curriculum_filter  if search_level >= 2 else ''
    effective_class      = class_filter       if search_level >= 2 else ''

    saved_ids = set()
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

    # ── Shuffle seed policy ────────────────────────────────
    #   fresh visit or refresh of base URL  → new seed
    #   explicit shuffle button (reseed=1)  → new seed, then redirect
    #                                         to page 1 without the param
    #   pagination (page param present)     → keep the current seed
    shuffle_seed = None
    if sort == 'shuffle':
        if request.args.get('reseed') == '1':
            _reseed_shuffle()
            args = request.args.to_dict(flat=True)
            args.pop('reseed', None)
            args['page'] = '1'
            return redirect(url_for('pdfs.list_pdfs', **args))
        if 'page' not in request.args:
            _reseed_shuffle()
        else:
            _ensure_shuffle_seed()
        shuffle_seed = session.get(_SHUF_SEED_KEY)

    if showing_shared:
        pdfs = [shared_pdf]
        total = 1
        total_pages = 1
        page = 1
        offset = 0
        range_start = 1
        range_end = 1
        saved_only = False
    elif saved_only:
        all_saved = _list_saved_pdfs(
            saved_ids=saved_ids, search=effective_search,
            subject=effective_subject, curriculum=effective_curriculum,
            class_filter=effective_class, sort=sort,
        )
        if sort == 'shuffle' and shuffle_seed:
            _random.Random(str(shuffle_seed)).shuffle(all_saved)
        total = len(all_saved)
        total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * PER_PAGE
        pdfs = all_saved[offset:offset + PER_PAGE]
    else:
        total = get_main_pdf_count(
            search=effective_search, subject=effective_subject,
            curriculum=effective_curriculum, class_filter=effective_class,
        )
        total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * PER_PAGE
        if sort == 'shuffle' and shuffle_seed:
            pdfs = get_all_pdfs_shuffled(
                seed=shuffle_seed, limit=PER_PAGE, offset=offset,
                search=effective_search, subject=effective_subject,
                curriculum=effective_curriculum, class_filter=effective_class,
            )
        else:
            pdfs = get_all_pdfs(
                limit=PER_PAGE, offset=offset,
                search=effective_search, subject=effective_subject,
                curriculum=effective_curriculum, class_filter=effective_class,
                sort=sort if sort != 'shuffle' else 'newest',
            )

    subjects  = get_pdf_distinct_subjects()  if search_level >= 1 else []
    classes   = get_pdf_distinct_classes()   if search_level >= 2 else []
    curricula = get_pdf_distinct_curricula() if search_level >= 2 else []

    if total == 0:
        range_start, range_end = 0, 0
    else:
        range_start = offset + 1
        range_end = min(offset + PER_PAGE, total)

    # ── Share URLs ─────────────────────────────────────────
    base_url = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')
    for p in pdfs:
        pid = p.get('id')
        p['_share_url'] = (f"{base_url}/pdfs/?pdf={pid}" if base_url else f"/pdfs/?pdf={pid}") if pid else ''

    # ── Batch lookup bot PDFs for streamability + action states ──
    codes = [p['code'] for p in pdfs if p.get('code')]
    try:
        bot_map = get_bot_pdfs_by_codes(codes)
    except Exception as e:
        logger.warning(f"bot batch lookup failed: {e}")
        bot_map = {}

    MAX_BYTES = Config.PDF_DIRECT_DOWNLOAD_MAX_BYTES

    for p in pdfs:
        bot_row = bot_map.get(p.get('code'))
        size = bot_row.get('file_size') if bot_row else None
        has_direct_url = bool(p.get('file_url'))
        has_bot_row = bot_row is not None

        # Only hide when we KNOW the file is too big. Missing size or -1
        # (getFile failed) is treated optimistically — the /direct
        # endpoint silently falls back to Telegram if the fetch fails.
        known_too_large = (
            size is not None
            and int(size) > 0
            and int(size) > MAX_BYTES
        )
        streamable = has_direct_url or (has_bot_row and not known_too_large)
        p['_is_streamable'] = streamable

        is_premium_pdf = bool(p.get('is_premium'))

        if not user_id:
            # Guest — every gated action shows a locked login button,
            # regardless of streamability (encourages sign-up).
            p['_download_state'] = 'locked_login'
            p['_view_state']     = 'locked_login'
            p['_get_state']      = 'locked_login'
        elif user_tier == 'premium' and (not is_premium_pdf or can_access_premium):
            # Premium with access
            if streamable:
                if downloads_unlimited or (remaining_downloads or 0) > 0:
                    p['_download_state'] = 'enabled'
                else:
                    p['_download_state'] = 'locked_quota'
                p['_view_state'] = 'enabled'
            else:
                p['_download_state'] = 'hidden'
                p['_view_state']     = 'hidden'
            p['_get_state'] = 'enabled'
        else:
            # Free user
            # Quota gate: once the daily Get PDF quota is exhausted,
            # both View and Get PDF lock. The user can still Share
            # and read the metadata card.
            free_quota_ok = downloads_unlimited or (remaining_downloads or 0) > 0
        
            if is_premium_pdf:
                p['_download_state'] = 'locked_premium'
                p['_view_state']     = 'locked_premium'
                p['_get_state']      = 'locked_premium'
            else:
                p['_download_state'] = 'locked_premium'   # direct download never on Free
                if free_quota_ok:
                    p['_view_state'] = 'enabled' if streamable else 'hidden'
                    p['_get_state']  = 'enabled'
                else:
                    p['_view_state'] = 'locked_quota'
                    p['_get_state']  = 'locked_quota'

    seconds_to_reset = _seconds_to_quota_reset() if user_id else 0
    reset_time_str = _format_reset_time(seconds_to_reset) if user_id else ''
    user_public_id = (session.get('public_id') or '').strip()
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
        showing_shared=showing_shared,
        shared_pdf=shared_pdf,
        shared_not_found=shared_not_found,
        base_url=base_url,
        remaining_downloads=remaining_downloads,
        downloads_unlimited=downloads_unlimited,
        download_limit=download_limit,
        seconds_to_reset=seconds_to_reset,
        reset_time_str=reset_time_str,
        user_public_id=user_public_id,
    )


def _list_saved_pdfs(saved_ids, search, subject, curriculum, class_filter, sort):
    """
    Return the user's saved PDFs, filtered. Sorting is applied here
    for the deterministic sorts. The shuffle case is handled by the
    caller, which shuffles after this returns.
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
            hay = ' '.join([p.get('title') or '', p.get('code') or '',
                            p.get('subject') or '']).lower()
            if search.lower() not in hay:
                continue
        items.append(p)

    if sort == 'shuffle':
        # Order does not matter here — caller shuffles. Return as-is.
        pass
    elif sort == 'popular':
        items.sort(key=lambda x: x.get('view_count') or 0, reverse=True)
    elif sort == 'title_asc':
        items.sort(key=lambda x: (x.get('title') or '').lower())
    elif sort == 'title_desc':
        items.sort(key=lambda x: (x.get('title') or '').lower(), reverse=True)
    elif sort == 'oldest':
        items.sort(key=lambda x: x.get('uploaded_at') or '')
    else:
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
        return redirect(url_for('auth.login', next=_current_path_with_query()))

    user_id = session['user_id']
    pdf = get_pdf_by_id(pdf_id)
    if not pdf:
        flash('PDF not found.', 'error')
        return redirect(url_for('pdfs.list_pdfs'))

    if pdf.get('is_premium', 0) and not can_access_premium_resources():
        flash('This is a premium resource. Upgrade to access it.', 'error')
        return redirect(url_for('pdfs.list_pdfs'))

    # ── Free-tier quota gate ──
    # View does not consume the quota — it is quota-dependent. Once
    # a Free user's Get PDF quota hits zero, View locks as well so
    # the whole reading flow pauses until the reset. Premium users
    # are never blocked here.
    user_tier = get_user_tier(user_id)
    if user_tier != 'premium':
        try:
            remaining = get_resource_downloads_remaining(user_id)
        except Exception:
            remaining = 999
        # tier_service returns 999+ for unlimited; 0..998 for finite
        # remaining. Below 999 and <= 0 means exhausted.
        if remaining < 999 and remaining <= 0:
            flash('Daily PDF limit reached. Resets tomorrow.', 'warning')
            return redirect(url_for('pdfs.list_pdfs'))

    new_count = increment_pdf_view(pdf_id)
    if new_count is not None:
        pdf['view_count'] = new_count

    add_history_entry(
        user_id=user_id,
        entry_type='pdf_view',
        action='viewed',
        metadata={'title': pdf['title'], 'subject': pdf.get('subject'),
                  'code': pdf['code']},
    )
    return render_template('dashboard/pdf_view.html', pdf=pdf, user_tier=user_tier)

# ============================================================
# DOWNLOAD (legacy route — redirects to Telegram)
# ============================================================

@pdfs_bp.route('/download/<pdf_id>')
def download_pdf(pdf_id):
    if 'user_id' not in session:
        _log_guest_attempt('download', pdf_code=request.args.get('code'))
        flash('Please login to download PDFs.', 'warning')
        return redirect(url_for('auth.login', next=_current_path_with_query()))

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
        metadata={'title': pdf['title'], 'subject': pdf.get('subject'),
                  'code': pdf['code']},
    )

    if user_tier == 'premium' and pdf.get('file_url'):
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

    # ── Guest ──
    # Guests have no quota to consume. Log the attempt and let the
    # bot handle the login prompt on the Telegram side.
    if 'user_id' not in session:
        _log_guest_attempt('telegram_get', pdf=pdf)
        return _telegram_redirect(code)

    user_id = session['user_id']
    user_tier = get_user_tier(user_id)

    # ── Free-tier quota gate ──
    # One click = one decrement. Same atomic consume the premium
    # direct download uses. Premium users skip this branch entirely:
    # their Telegram access is unlimited by design.
    if user_tier != 'premium':
        if not consume_resource_download(user_id):
            flash('Daily PDF limit reached. Resets tomorrow.', 'warning')
            return redirect(url_for('pdfs.list_pdfs'))

    # ── Success path ──
    increment_pdf_view_by_code(code)
    add_history_entry(
        user_id=user_id,
        entry_type='pdf_download',
        action='downloaded',
        metadata={'title': pdf['title'], 'subject': pdf.get('subject'),
                  'code': pdf['code']},
    )
    return _telegram_redirect(code)


def _telegram_redirect(code):
    """Build the t.me deeplink and return a redirect to it."""
    bot_username = Config.TELEGRAM_BOT_USERNAME or 'nuunplatform_bot'
    public_id = (session.get('public_id') or '').strip()
    if public_id:
        payload = f"pdf{code}{public_id}"
    else:
        payload = code
    return redirect(f"https://t.me/{bot_username}?start={payload}")
# ============================================================
# DIRECT DOWNLOAD (streams from Telegram as attachment)
# ============================================================

@pdfs_bp.route('/direct/<code>')
def direct_download(code):
    if 'user_id' not in session:
        return redirect(url_for('auth.login', next=_current_path_with_query()))

    user_id = session['user_id']
    user_tier = get_user_tier(user_id)

    if user_tier != 'premium':
        return redirect(url_for('pdfs.telegram_download', code=code))

    main_pdf = get_pdf_by_code(code)
    if not main_pdf:
        return redirect(url_for('pdfs.list_pdfs'))

    if main_pdf.get('is_premium', 0) and not can_access_premium_resources():
        return redirect(url_for('pdfs.telegram_download', code=code))

    bot_pdf = get_bot_pdf_by_code(code)
    if not bot_pdf:
        return redirect(url_for('pdfs.telegram_download', code=code))

    size = bot_pdf.get('file_size')
    # If size is unknown (NULL or -1), still try — Telegram will reject
    # oversized files anyway, and we fall back silently.
    if size and int(size) > 0 and int(size) > Config.PDF_DIRECT_DOWNLOAD_MAX_BYTES:
        return redirect(url_for('pdfs.telegram_download', code=code))

    if not consume_resource_download(user_id):
        return redirect(url_for('pdfs.telegram_download', code=code))

    try:
        bot = get_bot()
        file_info = bot.get_file(bot_pdf['file_id'])
        file_path = file_info.file_path
        token = Config.TELEGRAM_BOT_TOKEN
        url = f"https://api.telegram.org/file/bot{token}/{file_path}"

        response = requests.get(url, stream=True, timeout=60)
        if response.status_code != 200:
            logger.error(f"Telegram fetch failed: {response.status_code}")
            return redirect(url_for('pdfs.telegram_download', code=code))

        filename = _safe_filename(main_pdf.get('title') or code)
        encoded = quote(filename)

        increment_pdf_view_by_code(code)
        add_history_entry(
            user_id=user_id,
            entry_type='pdf_download',
            action='downloaded',
            metadata={'title': main_pdf['title'],
                      'subject': main_pdf.get('subject'),
                      'code': code},
        )

        headers = {
            'Content-Disposition': f"attachment; filename=\"{encoded}\"",
            'Content-Type': 'application/pdf',
            'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff',
        }
        return Response(
            response.iter_content(chunk_size=65536),
            headers=headers,
            mimetype='application/pdf',
        )
    except Exception as e:
        logger.error(f"Direct download error: {e}")
        return redirect(url_for('pdfs.telegram_download', code=code))


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

    if user_tier != 'premium':
        return jsonify({'error': 'Upgrade to access inline streaming.'}), 403

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
        if limit is not None and limit < 999:
            current = count_user_saved_pdfs(user_id)
            if current >= limit:
                return jsonify({'error': 'Save limit reached', 'reason': 'quota',
                                'limit': limit, 'current': current}), 429

    if not save_pdf_for_user(user_id, pdf_id):
        return jsonify({'error': 'Could not save'}), 500

    if not already:
        add_history_entry(
            user_id=user_id, entry_type='save', action='saved',
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
        user_id=user_id, entry_type='save', action='unsaved',
        entry_id=pdf_id, metadata={'type': 'pdf'},
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
        user_id=user_id, entry_type='report', action='reported',
        entry_id=pdf_id,
        metadata={'title': pdf['title'], 'code': pdf['code'], 'reason': reason},
    )

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
    from db import get_student_by_id

    reporter = get_student_by_id(reporter_id) or {}
    reporter_name = (
        f"{reporter.get('first_name', '')} {reporter.get('last_name', '')}"
    ).strip() or f"User #{reporter_id}"
    reporter_public = reporter.get('public_id') or '----'
    reason_label = _REASON_LABELS.get(reason, reason)

    title = f"🚩 PDF reported: {pdf.get('title', '')[:60]}"
    body = (f"{reporter_name} (@{reporter_public}) reported "
            f"\"{pdf.get('title', '')}\" ({pdf.get('code', '')}) — {reason_label}.")
    link = f"/admin/reports?type=pdf&id={pdf.get('id')}"

    try:
        from db import execute_with_retry
        cursor = execute_with_retry("SELECT id FROM students WHERE is_admin = 1")
        admin_ids = [r['id'] for r in cursor.fetchall()]
        from services.notification_service import send_notification
        for aid in admin_ids:
            try:
                send_notification(user_id=aid, notification_type='question_report',
                                  title=title, body=body, link=link,
                                  icon='🚩', force=True)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"In-app admin notify failed: {e}")

    try:
        from services.telegram_notify import (
            notify_super_admins, build_markdown_document, make_report_filename,
            summary_row, truncate,
        )
        base_url = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')
        admin_url = f"{base_url}/admin/reports?type=pdf&id={pdf.get('id')}" if base_url else None

        meta = {'type': 'pdf_report', 'pdf_id': pdf.get('id'),
                'pdf_code': pdf.get('code'), 'reporter_id': reporter_id,
                'reporter': reporter_public, 'reason': reason}

        pdf_table = '\n'.join([
            '| Field | Value |', '|:--|:--|',
            f"| Title | {pdf.get('title') or '—'} |",
            f"| Code | `{pdf.get('code') or '—'}` |",
            f"| Subject | {pdf.get('subject') or '—'} |",
            f"| Class | {pdf.get('class') or '—'} |",
            f"| Curriculum | {pdf.get('curriculum') or '—'} |",
            f"| Views | {pdf.get('view_count') or 0} |",
        ])
        reporter_table = '\n'.join([
            '| Field | Value |', '|:--|:--|',
            f"| Name | {reporter_name} |",
            f"| Public ID | `{reporter_public}` |",
            f"| Phone | `{reporter.get('phone_number') or '—'}` |",
            f"| School | {reporter.get('school') or '—'} |",
            f"| Grade | {reporter.get('grade') or '—'} |",
        ])
        report_lines = [f"**Reason:** {reason_label}"]
        if comment:
            report_lines += ["", "**Comment:**", truncate(comment, 500)]

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
            severity='warning', meta=meta, sections=sections,
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
            md_body=md_body, md_filename=filename, summary=summary,
            primary_url=admin_url, primary_url_label='Open admin panel',
            severity='warning', reference_id=f"PDF-{pdf.get('code') or 'UNKNOWN'}",
            icon='🚩',
        )
    except Exception as e:
        logger.warning(f"Telegram admin notify failed: {e}")