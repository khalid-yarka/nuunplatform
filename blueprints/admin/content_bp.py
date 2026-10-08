# ============================================================
# blueprints/admin/content_bp.py
# Content domain — questions, PDFs (library / intake / staging),
# PDF event history, and PDF admin analytics.
# ============================================================

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort, jsonify, Response, send_file,
)
import io
import json
import logging
import os
import re
import secrets
import string
import time
from datetime import datetime as _dt

from urllib.parse import urlencode, quote as _urlquote

from config import Config
from db import (
    execute_with_retry,
    get_question_by_id,
    create_question,
    update_question,
    delete_question,
    unarchive_question,
    get_questions_paginated,
    get_question_stats,
    get_questions_filter_options,
    get_question_counts_by_subject,
    check_pdf_codes_exist,
    check_question_exists,
    bulk_create_questions,
    get_all_pdfs,
    get_all_pdfs_shuffled,
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
from services.pdf_naming import suggest_from_filename, suggest_full
from question_utils import VALID_GRADES, DEFAULT_GRADE, normalize_grade
from services.question_validity import (
    find_duplicate_questions,
    to_payload as duplicates_to_payload,
    dismiss_duplicate_pair,
)
from services.pdf_history import (
    log_admin_event,
    log_system_event,
    get_pdf_event_summary,
    get_recent_events_for_pdf,
    get_pdf_events,
    get_global_events,
    get_today_summary as get_pdf_today_summary,
    count_global_events,
    group_events_by_day,
)

logger = logging.getLogger(__name__)

admin_content_bp = Blueprint('admin_content', __name__, url_prefix='/admin')


# ============================================================
# CONSTANTS
# ============================================================

_PDF_CURRICULA = (
    ('PL', 'Puntland'),
    ('SO', 'Somalia'),
    ('SL', 'Somaliland'),
)

_PDF_CLASSES = ('F4', 'F3', 'G8', 'G7')

_VALID_PDF_SORTS = (
    'shuffle', 'newest', 'oldest', 'popular', 'title_asc', 'title_desc',
)
_DEFAULT_PDF_SORT = 'shuffle'

_ADMIN_SHUF_SEED_KEY = 'admin_pdf_shuffle_seed'


# ============================================================
# SHUFFLE SEED HELPERS
# ============================================================

def _ensure_admin_shuffle_seed():
    seed = session.get(_ADMIN_SHUF_SEED_KEY)
    if not seed:
        seed = secrets.token_hex(4)
        session[_ADMIN_SHUF_SEED_KEY] = seed
        session.modified = True
    return seed


def _reseed_admin_shuffle():
    seed = secrets.token_hex(4)
    session[_ADMIN_SHUF_SEED_KEY] = seed
    session.modified = True
    return seed


# ============================================================
# HELPERS
# ============================================================

_PDF_CODE_RE = re.compile(r'^[A-Z0-9]{4}-[A-Z0-9]{4}$')
_PDF_SIZE_CAP_BYTES = int(
    getattr(Config, 'PDF_DIRECT_DOWNLOAD_MAX_BYTES', 19 * 1024 * 1024)
)

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
        return None, False

    try:
        from bot.db import _get_connection as bot_conn
        conn = bot_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT file_size FROM pdfs WHERE file_id = ? LIMIT 1",
                (file_id,),
            )
            row = cur.fetchone()
            if row is not None:
                size = row[0] if not hasattr(row, 'keys') else row['file_size']
                if size is not None:
                    size = int(size)
                    if size > 0:
                        return size, False
                    if size == -2:
                        return None, True
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"_get_telegram_file_size: DB lookup failed: {e}")

    now = time.time()
    cached = _PDF_SIZE_CACHE.get(file_id)
    if cached and (now - cached['fetched_at']) < _PDF_SIZE_CACHE_TTL:
        return cached['size_bytes'], cached.get('too_large', False)

    try:
        from bot.utils import get_bot
        bot = get_bot()
        file_info = bot.get_file(file_id)
        size = getattr(file_info, 'file_size', None)
        if size:
            size = int(size)
            _PDF_SIZE_CACHE[file_id] = {
                'size_bytes': size,
                'too_large': False,
                'fetched_at': now,
            }
            return size, False
        return None, False
    except Exception as e:
        msg = str(e).lower()
        too_large = any(tok in msg for tok in ('too big', 'too large', '413'))
        if too_large:
            _PDF_SIZE_CACHE[file_id] = {
                'size_bytes': None,
                'too_large': True,
                'fetched_at': now,
            }
            return None, True
        logger.warning(f"Could not get Telegram file size: {e}")
        return None, False


def _ascii_filename(raw, fallback='document.pdf'):
    if not raw:
        return fallback
    s = str(raw)
    s = ''.join(
        ch if (ord(ch) < 128 and (ch.isalnum() or ch in ' -_().')) else '_'
        for ch in s
    )
    s = re.sub(r'_+', '_', s).strip('_ ')
    if not s:
        return fallback
    return s[:120]


def _encode_content_disposition(disposition, filename):
    ascii_name = _ascii_filename(filename)
    header = f'{disposition}; filename="{ascii_name}"'
    try:
        if any(ord(c) >= 128 for c in str(filename or '')):
            encoded = _urlquote(str(filename), safe='')
            header += f"; filename*=UTF-8''{encoded}"
    except Exception:
        pass
    return header


def _pdf_response(data: bytes, disposition_header: str) -> Response:
    if data is None:
        data = b''
    length = len(data)
    return Response(
        data,
        status=200,
        mimetype='application/pdf',
        headers={
            'Content-Length': str(length),
            'Content-Disposition': disposition_header,
            'Cache-Control': 'private, max-age=300',
        },
    )


def _pdf_preview_response(data: bytes, disposition_header: str) -> Response:
    if data is None:
        data = b''
    buf = io.BytesIO(data)
    buf.seek(0)
    resp = send_file(
        buf,
        mimetype='application/pdf',
        as_attachment=False,
        conditional=True,
    )
    resp.headers['Content-Disposition'] = disposition_header
    resp.headers['Cache-Control'] = 'private, max-age=300'
    return resp


def _fetch_telegram_pdf_bytes(code: str):
    if not code:
        return None, 'No PDF code'
    try:
        from bot.db import get_bot_pdf_by_code
        bot_pdf = get_bot_pdf_by_code(code)
    except Exception as e:
        return None, f'Bot lookup failed: {e}'

    if not bot_pdf:
        return None, 'PDF not found in Telegram staging'

    file_id = bot_pdf.get('file_id')
    if not file_id:
        return None, 'No Telegram file_id stored for this PDF'

    try:
        from bot.utils import get_bot
        bot = get_bot()
        tg_file = bot.get_file(file_id)
        data = bot.download_file(tg_file.file_path)
        if not data:
            return None, 'Telegram returned an empty file'
        return data, None
    except Exception as e:
        logger.error(f"Telegram download failed for code {code}: {e}", exc_info=True)
        return None, f'Telegram download failed: {e}'


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


def _coerce_bool(raw):
    if raw is None:
        return 0
    if isinstance(raw, bool):
        return 1 if raw else 0
    if isinstance(raw, int):
        return 1 if raw else 0
    s = str(raw).strip().lower()
    return 1 if s in ('1', 'true', 'yes', 'on') else 0


def _generate_staging_pdf_code():
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
            'param': param, 'label': label,
            'value': str(value), 'clear_url': clear_url,
        })

    if kw.get('search'):
        add('search', 'Search', kw['search'])
    if kw.get('subject_code'):
        subj = get_subject(kw['subject_code'])
        add('subject', 'Subject', subj['name'] if subj else kw['subject_code'])
    if kw.get('grade_filter'):
        add('grade', 'Grade', kw['grade_filter'])
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
# PDF DASHBOARD ANALYTICS
# ============================================================

def _pdfs_dashboard_analytics():
    """
    Compute every analytic panel the PDF dashboard needs.

    Includes:
      · KPIs (live/pending/staged/flagged/premium/broken/uploads)
      · Today's activity summary
      · Class distribution (forced to exactly F4, F3, G8, G7)
      · Subject distribution (all subjects, zeros filled)
      · Uploads by day (30-day sparkline)
      · Top viewed (all-time top 5)
      · Most downloaded (all-time top 5)
      · Most Telegram-fetched (all-time top 5)
      · Trending PDFs (7-day top 5)
      · Top contributors (broadened query, 180 days)
      · Recent edits (last 12 admin edits)
      · Upload sources breakdown
      · Storage health (local/telegram/both/orphan)
      · Coverage (questions linked to PDFs)
      · Health score (0-100 composite)
    """
    from datetime import date as _date, timedelta as _td

    out = {
        'kpis': {
            'live': 0, 'pending': 0, 'staged': 0, 'flagged': 0,
            'premium': 0, 'premium_pct': 0,
            'broken': 0, 'uploads_30d': 0, 'live_trend': 0,
        },
        'class_split': [],
        'class_unclassified': 0,
        'class_split_total': 0,
        'subject_split_full': [],
        'uploads_by_day': [],
        'top_viewed': [],
        'most_downloaded': [],
        'most_tg_fetched': [],
        'trending_pdfs': [],
        'top_contributors': [],
        'recent_edits': [],
        'upload_sources': {},
        'storage': {'local': 0, 'telegram': 0, 'both': 0, 'orphan': 0},
        'today_activity': {
            'views': 0, 'downloads': 0, 'telegram_fetches': 0,
            'saves': 0, 'reports': 0, 'quota_hits': 0, 'total_today': 0,
        },
        'coverage': {
            'questions_total': 0, 'questions_linked': 0,
            'pdfs_with_links': 0, 'coverage_pct': 0,
        },
        'health_score': 100,
        'health_breakdown': {},
    }

    # ── Today's PDF activity ────────────────────────────
    try:
        out['today_activity'] = get_pdf_today_summary()
    except Exception:
        pass

    # ── Library counts ──────────────────────────────────
    try:
        row = execute_with_retry("SELECT COUNT(*) AS n FROM pdfs").fetchone()
        out['kpis']['live'] = row['n'] if row else 0
    except Exception:
        pass

    try:
        row = execute_with_retry(
            "SELECT COUNT(*) AS n FROM pdfs WHERE is_premium = 1"
        ).fetchone()
        out['kpis']['premium'] = row['n'] if row else 0
    except Exception:
        pass

    if out['kpis']['live'] > 0:
        out['kpis']['premium_pct'] = round(
            out['kpis']['premium'] / out['kpis']['live'] * 100
        )

    # ── Flagged queue ───────────────────────────────────
    try:
        row = execute_with_retry(
            "SELECT COUNT(*) AS n FROM unverified_pdfs WHERE confirmed = 0"
        ).fetchone()
        out['kpis']['flagged'] = row['n'] if row else 0
    except Exception:
        pass

    # ── Pending + Staged (bot DB) ───────────────────────
    try:
        from bot.db import count_pending_pdfs, count_bot_pdfs
        out['kpis']['pending'] = count_pending_pdfs()
        out['kpis']['staged'] = count_bot_pdfs(published_filter=False)
    except Exception:
        pass

    # ── Upload trends ───────────────────────────────────
    try:
        row = execute_with_retry(
            "SELECT COUNT(*) AS n FROM pdfs "
            "WHERE uploaded_at >= datetime('now', '-7 days')"
        ).fetchone()
        out['kpis']['live_trend'] = row['n'] if row else 0

        row = execute_with_retry(
            "SELECT COUNT(*) AS n FROM pdfs "
            "WHERE uploaded_at >= datetime('now', '-30 days')"
        ).fetchone()
        out['kpis']['uploads_30d'] = row['n'] if row else 0
    except Exception:
        pass

    # ── Broken codes ────────────────────────────────────
    try:
        cursor = execute_with_retry("""
            SELECT pdf_code, COUNT(*) AS n
            FROM questions
            WHERE status = 'active'
              AND pdf_code IS NOT NULL AND pdf_code != ''
            GROUP BY pdf_code
        """)
        candidates = [dict(r) for r in cursor.fetchall()]
        if candidates:
            codes = [c['pdf_code'] for c in candidates]
            lookup = check_pdf_codes_exist(codes)
            out['kpis']['broken'] = sum(
                1 for c in candidates
                if not lookup.get(c['pdf_code'], {}).get('exists')
            )
    except Exception:
        pass

    # ── CLASS DISTRIBUTION — forced to exactly 4 classes ──
    try:
        cursor = execute_with_retry("""
            SELECT class, COUNT(*) AS n FROM pdfs
            WHERE class IN ('F4','F3','G8','G7')
            GROUP BY class
        """)
        counts = {r['class']: r['n'] for r in cursor.fetchall()}

        cursor = execute_with_retry("""
            SELECT COUNT(*) AS n FROM pdfs
            WHERE class IS NULL OR class = '' OR class NOT IN ('F4','F3','G8','G7')
        """)
        out['class_unclassified'] = cursor.fetchone()['n']

        total = sum(counts.values())
        out['class_split_total'] = total
        out['class_split'] = [
            {'code': c, 'count': counts.get(c, 0)}
            for c in ('F4', 'F3', 'G8', 'G7')
        ]
    except Exception as e:
        logger.warning(f"class_split query failed: {e}")
        out['class_split'] = [{'code': c, 'count': 0} for c in ('F4', 'F3', 'G8', 'G7')]

    # ── SUBJECT DISTRIBUTION — all subjects with zeros ──
    try:
        cursor = execute_with_retry("""
            SELECT subject, COUNT(*) AS n FROM pdfs
            WHERE subject IS NOT NULL AND subject != ''
            GROUP BY subject
        """)
        counts = {r['subject']: r['n'] for r in cursor.fetchall()}

        all_subjects = get_all_subjects()
        out['subject_split_full'] = sorted(
            [
                {
                    'code': s['code'],
                    'name': s['name'],
                    'icon': s.get('icon', '📚'),
                    'count': counts.get(s['code'], 0),
                }
                for s in all_subjects
            ],
            key=lambda x: -x['count'],
        )
    except Exception as e:
        logger.warning(f"subject_split_full query failed: {e}")

    # ── Uploads by day (30d) ────────────────────────────
    try:
        cursor = execute_with_retry("""
            SELECT DATE(uploaded_at) AS d, COUNT(*) AS n
            FROM pdfs
            WHERE uploaded_at >= DATE('now', '-30 days')
            GROUP BY d
            ORDER BY d
        """)
        rows = {r['d']: r['n'] for r in cursor.fetchall()}
        today = _date.today()
        for i in range(29, -1, -1):
            d = (today - _td(days=i)).isoformat()
            out['uploads_by_day'].append({
                'date': d[5:],
                'count': rows.get(d, 0),
            })
    except Exception:
        pass

    # ── Most viewed (all-time) ──────────────────────────
    try:
        cursor = execute_with_retry("""
            SELECT id, title, code, view_count, subject
            FROM pdfs
            ORDER BY view_count DESC
            LIMIT 5
        """)
        out['top_viewed'] = [dict(r) for r in cursor.fetchall()]
    except Exception:
        pass

    # ── Most downloaded (all-time) ──────────────────────
    try:
        cursor = execute_with_retry("""
            SELECT pdf_id, pdf_code, pdf_title, COUNT(*) AS n
            FROM pdf_events
            WHERE event_type = 'download' AND pdf_id IS NOT NULL
            GROUP BY pdf_id
            ORDER BY n DESC
            LIMIT 5
        """)
        out['most_downloaded'] = [dict(r) for r in cursor.fetchall()]
    except Exception:
        pass

    # ── Most Telegram-fetched (all-time) ────────────────
    try:
        cursor = execute_with_retry("""
            SELECT pdf_id, pdf_code, pdf_title, COUNT(*) AS n
            FROM pdf_events
            WHERE event_type = 'telegram_fetch' AND pdf_id IS NOT NULL
            GROUP BY pdf_id
            ORDER BY n DESC
            LIMIT 5
        """)
        out['most_tg_fetched'] = [dict(r) for r in cursor.fetchall()]
    except Exception:
        pass

    # ── Trending PDFs (last 7 days) ─────────────────────
    try:
        cursor = execute_with_retry("""
            SELECT pdf_id, pdf_code, pdf_title, COUNT(*) AS hits
            FROM pdf_events
            WHERE event_type IN ('view', 'download', 'telegram_fetch')
              AND created_at >= datetime('now', '-7 days')
              AND pdf_id IS NOT NULL
            GROUP BY pdf_id
            ORDER BY hits DESC
            LIMIT 5
        """)
        out['trending_pdfs'] = [dict(r) for r in cursor.fetchall()]
    except Exception:
        pass

    # ── TOP CONTRIBUTORS — broadened query ──────────────
    try:
        cursor = execute_with_retry("""
            SELECT actor_name, COUNT(*) AS n
            FROM pdf_events
            WHERE event_category = 'admin'
              AND actor_name IS NOT NULL
              AND actor_name != ''
              AND created_at >= datetime('now', '-180 days')
            GROUP BY actor_name
            ORDER BY n DESC
            LIMIT 8
        """)
        rows = [dict(r) for r in cursor.fetchall()]

        # Fallback to admin_audit_log if pdf_events yields nothing
        if not rows:
            try:
                cursor = execute_with_retry("""
                    SELECT
                        COALESCE(s.first_name || ' ' || s.last_name,
                                 'Admin #' || aal.actor_id) AS actor_name,
                        COUNT(*) AS n
                    FROM admin_audit_log aal
                    LEFT JOIN students s ON s.id = aal.actor_id
                    WHERE aal.action LIKE 'pdf.%'
                      AND aal.created_at >= datetime('now', '-180 days')
                    GROUP BY aal.actor_id
                    ORDER BY n DESC
                    LIMIT 8
                """)
                rows = [dict(r) for r in cursor.fetchall()]
            except Exception:
                pass

        out['top_contributors'] = rows
    except Exception as e:
        logger.warning(f"top_contributors query failed: {e}")

    # ── Recent edits ────────────────────────────────────
    try:
        cursor = execute_with_retry("""
            SELECT pdf_id, pdf_code, pdf_title, actor_name,
                   event_type, created_at
            FROM pdf_events
            WHERE event_category = 'admin'
              AND event_type IN ('edit', 'verify', 'code_change',
                                 'premium_on', 'premium_off', 'delete',
                                 'publish', 'create')
            ORDER BY created_at DESC
            LIMIT 12
        """)
        out['recent_edits'] = [dict(r) for r in cursor.fetchall()]
    except Exception:
        pass

    # ── Upload sources ──────────────────────────────────
    try:
        cursor = execute_with_retry("""
            SELECT
                COALESCE(NULLIF(actor_role, ''), 'other') AS src,
                COUNT(*) AS n
            FROM pdf_events
            WHERE event_type = 'create'
            GROUP BY src
        """)
        for r in cursor.fetchall():
            label = {
                'admin': 'Admin upload',
                'super_admin': 'Super admin upload',
                'system': 'System',
                'other': 'Script / other',
            }.get(r['src'], r['src'].replace('_', ' ').title())
            out['upload_sources'][label] = r['n']
    except Exception:
        pass

    # ── Storage health ──────────────────────────────────
    try:
        cursor = execute_with_retry("""
            SELECT
                SUM(CASE WHEN file_url IS NOT NULL AND file_url != '' THEN 1 ELSE 0 END) AS has_local,
                SUM(CASE WHEN file_id  IS NOT NULL AND file_id  != '' THEN 1 ELSE 0 END) AS has_telegram,
                SUM(CASE WHEN (file_url IS NULL OR file_url = '')
                          AND (file_id  IS NULL OR file_id  = '') THEN 1 ELSE 0 END) AS neither,
                COUNT(*) AS total
            FROM pdfs
        """)
        row = cursor.fetchone()
        if row:
            total = row['total'] or 0
            has_local = row['has_local'] or 0
            has_tg = row['has_telegram'] or 0
            neither = row['neither'] or 0
            out['storage'] = {
                'local': has_local,
                'telegram': has_tg,
                'both': max(0, has_local + has_tg - total),
                'orphan': neither,
            }
    except Exception:
        pass

    # ── Coverage ────────────────────────────────────────
    try:
        row = execute_with_retry(
            "SELECT COUNT(*) AS n FROM questions WHERE status = 'active'"
        ).fetchone()
        out['coverage']['questions_total'] = row['n'] if row else 0

        row = execute_with_retry(
            "SELECT COUNT(*) AS n FROM questions "
            "WHERE status = 'active' AND pdf_code IS NOT NULL AND pdf_code != ''"
        ).fetchone()
        out['coverage']['questions_linked'] = row['n'] if row else 0

        if out['coverage']['questions_total'] > 0:
            out['coverage']['coverage_pct'] = round(
                100.0 * out['coverage']['questions_linked']
                / out['coverage']['questions_total'], 1
            )

        row = execute_with_retry("""
            SELECT COUNT(DISTINCT p.id) AS n
            FROM pdfs p
            JOIN questions q ON q.pdf_code = p.code AND q.status = 'active'
        """).fetchone()
        out['coverage']['pdfs_with_links'] = row['n'] if row else 0
    except Exception:
        pass

    # ── Health score ────────────────────────────────────
    deductions = {}
    score = 100

    broken = out['kpis']['broken']
    if broken > 0:
        d = min(25, broken * 3)
        deductions['Broken links'] = -d
        score -= d

    orphan = out['storage'].get('orphan', 0)
    if orphan > 0:
        d = min(20, orphan * 2)
        deductions['Orphan files'] = -d
        score -= d

    flagged = out['kpis']['flagged']
    if flagged > 10:
        deductions['Flagged backlog'] = -15
        score -= 15
    elif flagged > 0:
        deductions['Flagged queue'] = -5
        score -= 5

    pending = out['kpis']['pending']
    if pending > 20:
        deductions['Intake backlog'] = -10
        score -= 10

    cov = out['coverage']['coverage_pct']
    if 0 < cov < 50:
        deductions['Low link coverage'] = -15
        score -= 15
    elif 0 < cov < 80:
        deductions['Partial link coverage'] = -5
        score -= 5

    out['health_score'] = max(0, score)
    out['health_breakdown'] = deductions

    return out


def _pdfs_heatmap_weeks():
    """
    12-week activity heatmap for the People tab.
    Returns a list of weeks; each week is 7 day-dicts
    {date, count, level}. Level 0 = no activity, 1-4 = intensity.
    """
    from datetime import date as _date, timedelta as _td

    try:
        cursor = execute_with_retry("""
            SELECT DATE(created_at) AS d, COUNT(*) AS n
            FROM pdf_events
            WHERE event_category = 'admin'
              AND created_at >= datetime('now', '-12 weeks')
            GROUP BY d
        """)
        counts = {r['d']: r['n'] for r in cursor.fetchall()}
    except Exception:
        counts = {}

    if not counts:
        return []

    max_n = max(counts.values()) or 1

    today = _date.today()
    start = today - _td(days=today.weekday()) - _td(weeks=11)

    weeks = []
    for w in range(12):
        week = []
        for d in range(7):
            day = start + _td(weeks=w, days=d)
            iso = day.isoformat()
            n = counts.get(iso, 0)
            if n == 0:
                level = 0
            elif n <= max(1, max_n * 0.25):
                level = 1
            elif n <= max_n * 0.5:
                level = 2
            elif n <= max_n * 0.75:
                level = 3
            else:
                level = 4
            week.append({'date': iso, 'count': n, 'level': level})
        weeks.append(week)
    return weeks


# ============================================================
# PDF EDIT-ROOM HELPERS
# ============================================================

def _load_linked_questions(pdf_code, limit=20):
    if not pdf_code:
        return []
    try:
        cursor = execute_with_retry(
            "SELECT id, question_text, pdf_page, status "
            "FROM questions WHERE pdf_code = ? "
            "ORDER BY id DESC LIMIT ?",
            (pdf_code, limit)
        )
        return [dict(r) for r in cursor.fetchall()]
    except Exception:
        return []


def _load_chapters_in_subject(subject_code, limit=30):
    if not subject_code:
        return []
    try:
        cursor = execute_with_retry(
            "SELECT DISTINCT chapter FROM pdfs "
            "WHERE subject = ? AND chapter IS NOT NULL AND chapter != '' "
            "ORDER BY chapter LIMIT ?",
            (subject_code, limit)
        )
        return [r['chapter'] for r in cursor.fetchall()]
    except Exception:
        return []


def _load_top_tags(limit=20):
    try:
        cursor = execute_with_retry(
            "SELECT tags FROM pdfs WHERE tags IS NOT NULL AND tags != '' LIMIT 500"
        )
        tag_counter = {}
        for r in cursor.fetchall():
            for t in (r['tags'] or '').split(','):
                t = t.strip()
                if t:
                    tag_counter[t] = tag_counter.get(t, 0) + 1
        return [t for t, _ in sorted(tag_counter.items(), key=lambda kv: -kv[1])[:limit]]
    except Exception:
        return []


def _build_preview_info(pdf, file_url=None):
    """Compute the { available, source, size_mb, too_large, ... } dict."""
    info = {
        'available': False, 'source': None,
        'size_mb': None, 'size_status': 'missing',
        'too_large': False, 'has_local_file': False,
        'has_telegram': False,
    }

    if file_url and os.path.exists(file_url):
        info['has_local_file'] = True
        info['source'] = 'local'
        try:
            size_bytes = os.path.getsize(file_url)
            info['size_mb'] = round(size_bytes / (1024 * 1024), 2)
            if size_bytes > _PDF_SIZE_CAP_BYTES:
                info['too_large'] = True
                info['size_status'] = 'too_large'
            else:
                info['available'] = True
                info['size_status'] = 'ok'
        except Exception:
            info['size_status'] = 'unknown'
        return info

    try:
        from bot.db import get_bot_pdf_by_code
        bot_pdf = get_bot_pdf_by_code((pdf or {}).get('code') or '')
        if bot_pdf and bot_pdf.get('file_id'):
            info['has_telegram'] = True
            raw_size = bot_pdf.get('file_size')
            known_too_large = False
            if raw_size is not None:
                try:
                    raw_size = int(raw_size)
                except (TypeError, ValueError):
                    raw_size = None
            if raw_size == -2:
                known_too_large = True
                size_bytes = None
            elif raw_size is not None and raw_size > 0:
                size_bytes = raw_size
            else:
                size_bytes, known_too_large = _get_telegram_file_size(
                    bot_pdf['file_id']
                )
            if known_too_large:
                info['too_large'] = True
                info['size_status'] = 'too_large'
            elif size_bytes:
                info['size_mb'] = round(size_bytes / (1024 * 1024), 2)
                if size_bytes > _PDF_SIZE_CAP_BYTES:
                    info['too_large'] = True
                    info['size_status'] = 'too_large'
                else:
                    info['available'] = True
                    info['source'] = 'telegram'
                    info['size_status'] = 'ok'
            else:
                info['size_status'] = 'unknown'
    except Exception as e:
        logger.warning(f"_build_preview_info: bot lookup failed: {e}")

    return info


# ============================================================
# BATCH AUTO-CREATION HELPERS
# ============================================================

def _auto_create_questions_batch(subject_code, grade, imported,
                                 admin_id, before_max_id):
    if not imported or imported <= 0:
        return None
    try:
        from db import create_batch, add_batch_items
    except Exception as e:
        logger.warning(f"batch helpers unavailable: {e}")
        return None

    try:
        cursor = execute_with_retry(
            "SELECT id FROM questions "
            "WHERE id > ? AND created_by = ? "
            "ORDER BY id DESC LIMIT ?",
            (before_max_id, admin_id, imported),
        )
        new_ids = [r['id'] for r in cursor.fetchall()]
        if not new_ids:
            return None

        name = (
            f"{subject_code} {grade} — "
            f"{_dt.now().strftime('%b %d')} · {len(new_ids)} Q"
        )
        batch_id = create_batch(
            name=name,
            kind='questions',
            admin_id=admin_id,
            notes=(
                f"Auto-created from bulk import on "
                f"{_dt.now().strftime('%Y-%m-%d')}"
            ),
        )
        if not batch_id:
            return None

        add_batch_items(batch_id, 'question', new_ids)
        return batch_id
    except Exception as e:
        logger.warning(f"auto-batch for questions failed: {e}")
        return None


def _auto_create_pdfs_batch(pdf_ids, admin_id, source_label='bot bulk publish'):
    if not pdf_ids:
        return None
    try:
        from db import create_batch, add_batch_items
    except Exception as e:
        logger.warning(f"batch helpers unavailable: {e}")
        return None

    try:
        name = (
            f"PDFs — {_dt.now().strftime('%b %d')} · "
            f"{len(pdf_ids)} file{'s' if len(pdf_ids) != 1 else ''}"
        )
        batch_id = create_batch(
            name=name,
            kind='pdfs',
            admin_id=admin_id,
            notes=(
                f"Auto-created from {source_label} on "
                f"{_dt.now().strftime('%Y-%m-%d')}"
            ),
        )
        if not batch_id:
            return None

        add_batch_items(batch_id, 'pdf', pdf_ids)
        return batch_id
    except Exception as e:
        logger.warning(f"auto-batch for PDFs failed: {e}")
        return None


# ============================================================
# QUESTIONS — LIST
# ============================================================

@admin_content_bp.route('/questions', methods=['GET'], endpoint='questions')
@admin_can('questions.view')
def questions():
    search = (request.args.get('search') or '').strip()
    subject_code = (request.args.get('subject') or '').strip()
    grade_filter = (request.args.get('grade') or '').strip().upper()
    if grade_filter and grade_filter not in VALID_GRADES:
        grade_filter = ''
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
        grade_filter=grade_filter,
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
    subject_counts = get_question_counts_by_subject()
    filter_options = get_questions_filter_options()
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    codes_in_page = [q['pdf_code'] for q in questions_list if q.get('pdf_code')]
    pdf_map = check_pdf_codes_exist(codes_in_page) if codes_in_page else {}

    active_filters = _build_active_filters(
        search=search, subject_code=subject_code,
        grade_filter=grade_filter,
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
        subject_counts=subject_counts,
        filter_options=filter_options,
        pdf_map=pdf_map,
        search=search,
        subject_code=subject_code,
        grade_filter=grade_filter,
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
        grades=VALID_GRADES,
        default_grade=DEFAULT_GRADE,
        duplicates_json='[]',
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
    grade = normalize_grade(request.form.get('grade'))

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
        flash('Page number ignored — no PDF code provided.', 'warning')

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('admin_content.question_new'))

    duplicates = find_duplicate_questions(
        question_text, subject_code=subject_code, grade=grade,
    )
    confirm = request.form.get('confirm_duplicate') == '1'

    try:
        difficulty = int(difficulty_raw)
    except ValueError:
        difficulty = 1

    opts = {'A': option_a, 'B': option_b, 'C': option_c}
    if option_d: opts['D'] = option_d
    if option_e: opts['E'] = option_e
    if option_f: opts['F'] = option_f

    if duplicates and not confirm:
        return render_template(
            'dashboard/admin/content/question_edit.html',
            question={
                'subject_code': subject_code, 'question_text': question_text,
                'options': opts, 'correct_answer': correct_answer,
                'difficulty': difficulty, 'chapter': chapter, 'tags': tags,
                'explanation': explanation, 'pdf_code': pdf_code,
                'pdf_page': pdf_page, 'status': status, 'grade': grade,
            },
            subjects=get_all_subjects(),
            grades=VALID_GRADES, default_grade=DEFAULT_GRADE,
            duplicates_json=json.dumps(duplicates_to_payload(duplicates)),
        )

    data = {
        'subject_code': subject_code, 'question_text': question_text,
        'options': opts, 'correct_answer': correct_answer,
        'difficulty': difficulty, 'chapter': chapter, 'tags': tags,
        'explanation': explanation, 'pdf_code': pdf_code,
        'pdf_page': pdf_page, 'status': status, 'grade': grade,
        'created_by': session.get('user_id'),
        'updated_by': session.get('user_id'),
    }

    if create_question(data):
        write_audit(
            action='question.create', target_type='question', before=None,
            after={'subject_code': subject_code, 'pdf_code': pdf_code,
                   'difficulty': difficulty, 'status': status,
                   'grade': grade,
                   'duplicates_acknowledged': bool(duplicates and confirm)},
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
        grades=VALID_GRADES,
        default_grade=DEFAULT_GRADE,
        pdf_info=pdf_info,
        duplicates_json='[]',
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
    grade = normalize_grade(request.form.get('grade'))

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

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('admin_content.question_edit',
                                question_id=question_id))

    duplicates = find_duplicate_questions(
        question_text, subject_code=subject_code,
        grade=grade, exclude_id=question_id,
    )
    confirm = request.form.get('confirm_duplicate') == '1'

    try:
        difficulty = int(difficulty_raw)
    except ValueError:
        difficulty = 1

    opts = {'A': option_a, 'B': option_b, 'C': option_c}
    if option_d: opts['D'] = option_d
    if option_e: opts['E'] = option_e
    if option_f: opts['F'] = option_f

    if duplicates and not confirm:
        merged = dict(question)
        merged.update({
            'subject_code': subject_code, 'question_text': question_text,
            'options': opts, 'correct_answer': correct_answer,
            'difficulty': difficulty, 'chapter': chapter, 'tags': tags,
            'explanation': explanation, 'pdf_code': pdf_code,
            'pdf_page': pdf_page, 'status': status, 'grade': grade,
        })
        return render_template(
            'dashboard/admin/content/question_edit.html',
            question=merged,
            subjects=get_all_subjects(),
            grades=VALID_GRADES, default_grade=DEFAULT_GRADE,
            duplicates_json=json.dumps(duplicates_to_payload(duplicates)),
        )

    data = {
        'subject_code': subject_code, 'question_text': question_text,
        'options': opts, 'correct_answer': correct_answer,
        'difficulty': difficulty, 'chapter': chapter, 'tags': tags,
        'explanation': explanation, 'pdf_code': pdf_code,
        'pdf_page': pdf_page, 'status': status, 'grade': grade,
        'updated_by': session.get('user_id'),
    }

    if update_question(question_id, data):
        write_audit(
            action='question.update', target_type='question',
            target_id=question_id,
            before={'subject_code': question.get('subject_code'),
                    'grade': question.get('grade'),
                    'status': question.get('status')},
            after={'subject_code': subject_code, 'grade': grade,
                   'status': status,
                   'duplicates_acknowledged': bool(duplicates and confirm)},
            severity='info',
        )
        flash('Question updated.', 'success')
        return redirect(url_for('admin_content.questions'))

    flash('Error updating question.', 'error')
    return redirect(url_for('admin_content.question_edit',
                            question_id=question_id))


# ============================================================
# QUESTIONS — ARCHIVE / UNARCHIVE / BULK
# ============================================================

@admin_content_bp.route('/questions/<int:question_id>/delete', methods=['POST'],
                        endpoint='question_archive')
@admin_can('questions.archive')
def question_archive(question_id):
    wants_json = (
        request.is_json
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )

    if wants_json:
        if not _csrf_ok():
            return jsonify({'error': 'Invalid session.'}), 403
    else:
        if not validate_csrf():
            abort(403)

    question = get_question_by_id(question_id)
    if not question:
        if wants_json:
            return jsonify({'error': 'Question not found'}), 404
        abort(404)

    ok = delete_question(question_id)
    if not ok:
        if wants_json:
            return jsonify({'error': 'Archive failed'}), 500
        flash('Error archiving question.', 'error')
        return redirect(url_for('admin_content.questions'))

    write_audit(
        action='question.archive', target_type='question',
        target_id=question_id,
        before={'status': question.get('status')},
        after={'status': 'archived'},
        severity='warning',
    )

    if wants_json:
        return jsonify({'success': True, 'id': question_id})
    flash('Question archived.', 'success')
    return redirect(url_for('admin_content.questions'))


@admin_content_bp.route('/questions/<int:question_id>/unarchive',
                        methods=['POST'],
                        endpoint='question_unarchive')
@admin_can('questions.edit')
def question_unarchive(question_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    if not unarchive_question(question_id):
        return jsonify({'error': 'Restore failed'}), 500

    write_audit(
        action='question.unarchive', target_type='question',
        target_id=question_id, before={'status': 'archived'},
        after={'status': 'active'}, severity='info',
    )
    return jsonify({'success': True, 'id': question_id})


@admin_content_bp.route('/questions/bulk-archive', methods=['POST'],
                        endpoint='questions_bulk_archive')
@admin_can('questions.archive')
def questions_bulk_archive():
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get('ids') or []
    clean = []
    for i in raw_ids:
        try:
            clean.append(int(i))
        except (TypeError, ValueError):
            continue
    if not clean:
        return jsonify({'error': 'No IDs'}), 400

    archived, failed = [], []
    for qid in clean:
        if delete_question(qid):
            archived.append(qid)
        else:
            failed.append(qid)

    write_audit(
        action='question.bulk_archive', target_type='question',
        before=None, after={'archived': archived, 'failed': failed},
        severity='warning',
    )
    return jsonify({'success': True, 'archived': archived, 'failed': failed})


# ============================================================
# QUESTIONS — INLINE UPDATE
# ============================================================

@admin_content_bp.route('/questions/<int:question_id>/inline-update',
                        methods=['POST'],
                        endpoint='question_inline_update')
@admin_can('questions.edit')
def question_inline_update(question_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    question = get_question_by_id(question_id)
    if not question:
        return jsonify({'error': 'Question not found'}), 404

    data_in = request.get_json(silent=True) or {}

    subject_code = (data_in.get('subject_code') or '').strip()
    question_text = (data_in.get('question_text') or '').strip()
    correct_answer = (data_in.get('correct_answer') or '').strip().upper()
    grade = normalize_grade(data_in.get('grade'))

    if not subject_code or not get_subject(subject_code):
        return jsonify({'error': 'Invalid subject'}), 400
    if not question_text:
        return jsonify({'error': 'Question text required'}), 400
    if correct_answer not in ('A', 'B', 'C', 'D', 'E', 'F'):
        return jsonify({'error': 'Invalid correct answer'}), 400

    opts_in = data_in.get('options') or {}
    opts = {}
    for letter in ('A', 'B', 'C', 'D', 'E', 'F'):
        v = (opts_in.get(letter) or '').strip()
        if v:
            opts[letter] = v
    if not opts.get('A') or not opts.get('B') or not opts.get('C'):
        return jsonify({'error': 'Options A, B, C required'}), 400
    if correct_answer not in opts:
        return jsonify({'error': 'Correct answer must match an option'}), 400

    try:
        difficulty = int(data_in.get('difficulty') or 1)
        difficulty = max(1, min(5, difficulty))
    except (TypeError, ValueError):
        difficulty = 1

    update_data = {
        'subject_code': subject_code,
        'question_text': question_text,
        'options': opts,
        'correct_answer': correct_answer,
        'difficulty': difficulty,
        'chapter': (data_in.get('chapter') or '').strip(),
        'tags': (data_in.get('tags') or '').strip(),
        'explanation': (data_in.get('explanation') or '').strip(),
        'pdf_code': (data_in.get('pdf_code') or '').strip().upper() or None,
        'pdf_page': data_in.get('pdf_page') or None,
        'status': (data_in.get('status') or 'active').strip(),
        'grade': grade,
        'updated_by': session.get('user_id'),
    }

    if not update_question(question_id, update_data):
        return jsonify({'error': 'Save failed'}), 500

    write_audit(
        action='question.inline_update', target_type='question',
        target_id=question_id,
        before={'grade': question.get('grade'),
                'subject_code': question.get('subject_code')},
        after={'grade': grade, 'subject_code': subject_code},
        severity='info',
    )
    return jsonify({'success': True})


# ============================================================
# QUESTIONS — DUPLICATE CHECK + DISMISS
# ============================================================

@admin_content_bp.route('/questions/check-duplicate', methods=['POST'],
                        endpoint='question_check_duplicate')
@admin_can('questions.view')
def question_check_duplicate():
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    data = request.get_json(silent=True) or {}
    question_text = (data.get('question_text') or '').strip()
    subject_code = (data.get('subject_code') or '').strip()
    grade = (data.get('grade') or '').strip().upper()

    exclude_raw = data.get('exclude_id')
    try:
        exclude_id = int(exclude_raw) if exclude_raw else None
    except (TypeError, ValueError):
        exclude_id = None

    matches = find_duplicate_questions(
        question_text, subject_code=subject_code,
        grade=grade, exclude_id=exclude_id,
    )
    return jsonify({'duplicates': duplicates_to_payload(matches)})


@admin_content_bp.route('/questions/dismiss-duplicate', methods=['POST'],
                        endpoint='question_dismiss_duplicate')
@admin_can('questions.view')
def question_dismiss_duplicate():
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    data = request.get_json(silent=True) or {}
    try:
        a = int(data.get('a_id'))
        b = int(data.get('b_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid IDs'}), 400

    if a == b:
        return jsonify({'error': 'Same question'}), 400

    ok = dismiss_duplicate_pair(a, b, session.get('user_id'))
    return jsonify({'success': bool(ok)})


# ============================================================
# QUESTIONS — BROKEN PDF LINKS
# ============================================================

@admin_content_bp.route('/questions/broken', methods=['GET'],
                        endpoint='questions_broken')
@admin_can('questions.view')
def questions_broken():
    cursor = execute_with_retry("""
        SELECT id, question_text, subject_code, grade, pdf_code, created_at
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
            SELECT q.id, q.question_text, q.subject_code, q.grade,
                   q.difficulty,
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
                size_bytes, known_too_large = _get_telegram_file_size(
                    bot_pdf['file_id']
                )
                if known_too_large:
                    out['reason'] = 'too_large'
                elif size_bytes:
                    out['size_mb'] = round(size_bytes / (1024 * 1024), 2)
                    if size_bytes <= _PDF_SIZE_CAP_BYTES:
                        out['can_preview'] = True
                        out['preview_url'] = url_for(
                            'pdfs.preview_telegram', code=code
                        )
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
            size_bytes, known_too_large = _get_telegram_file_size(
                bot_pdf['file_id']
            )
            if known_too_large:
                out['reason'] = 'too_large'
            elif size_bytes:
                out['size_mb'] = round(size_bytes / (1024 * 1024), 2)
                if size_bytes <= _PDF_SIZE_CAP_BYTES:
                    out['can_preview'] = True
                    out['preview_url'] = url_for(
                        'pdfs.preview_telegram', code=code
                    )
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
# PDF CODE AVAILABILITY CHECK (AJAX)
# ============================================================

@admin_content_bp.route('/pdfs/check-code', methods=['POST'],
                        endpoint='pdf_check_code')
@admin_can('pdfs.view')
def pdf_check_code():
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    data = request.get_json(silent=True) or {}
    code = _normalize_pdf_code(data.get('code')) or ''

    exclude_raw = data.get('exclude_id')
    try:
        exclude_id = int(exclude_raw) if exclude_raw else None
    except (TypeError, ValueError):
        exclude_id = None

    out = {
        'valid': False,
        'available': False,
        'format_ok': False,
        'owner_id': None,
        'reason': None,
    }

    if not code:
        out['reason'] = 'empty'
        return jsonify(out)

    if not _validate_pdf_code_format(code):
        out['reason'] = 'invalid_format'
        return jsonify(out)

    out['format_ok'] = True
    out['valid'] = True

    if exclude_id:
        try:
            existing = get_pdf_by_code(code)
            if existing and existing.get('id') == exclude_id:
                out['available'] = True
                return jsonify(out)
        except Exception:
            pass

    try:
        row = execute_with_retry(
            "SELECT id FROM pdfs WHERE code = ? LIMIT 1",
            (code,),
        ).fetchone()
        if row:
            out['owner_id'] = row['id']
            out['reason'] = 'taken_in_main'
            return jsonify(out)
    except Exception as e:
        logger.warning(f"pdf_check_code main lookup failed: {e}")

    try:
        from bot.db import get_bot_pdf_by_code
        bot_pdf = get_bot_pdf_by_code(code)
        if bot_pdf and not bot_pdf.get('published'):
            out['reason'] = 'taken_in_staging'
            return jsonify(out)
    except Exception as e:
        logger.warning(f"pdf_check_code bot lookup failed: {e}")

    out['available'] = True
    return jsonify(out)


# ============================================================
# BULK IMPORT (questions)
# ============================================================

@admin_content_bp.route('/bulk-import', methods=['GET'], endpoint='bulk_import')
@admin_can('questions.bulk_import')
def bulk_import():
    return render_template('dashboard/admin/content/bulk_import.html')


@admin_content_bp.route('/bulk-import', methods=['POST'],
                        endpoint='bulk_import_apply')
@admin_can('questions.bulk_import')
def bulk_import_apply():
    import_indices = None
    request_grade_raw = ''
    if request.is_json:
        body = request.get_json(silent=True) or {}
        raw_text = (body.get('json_data') or '').strip()
        pdf_code_raw = (body.get('pdf_code') or '').strip().upper()
        request_grade_raw = (body.get('grade') or '').strip()
        raw_indices = body.get('import_indices')
        if isinstance(raw_indices, list):
            import_indices = set()
            for i in raw_indices:
                try:
                    import_indices.add(int(i))
                except (TypeError, ValueError):
                    continue
    else:
        if not validate_csrf():
            flash('Invalid session. Please refresh and try again.', 'error')
            return redirect(url_for('admin_content.bulk_import'))
        raw_text = (request.form.get('json_data') or '').strip()
        pdf_code_raw = (request.form.get('pdf_code') or '').strip().upper()
        request_grade_raw = (request.form.get('grade') or '').strip()

    if not raw_text:
        if request.is_json:
            return jsonify({'error': 'No JSON data provided'}), 400
        flash('Please paste JSON or upload a file.', 'error')
        return redirect(url_for('admin_content.bulk_import'))

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        msg = f'Invalid JSON: {e.msg} (line {e.lineno}, col {e.colno})'
        if request.is_json:
            return jsonify({'error': msg}), 400
        flash(msg, 'error')
        return redirect(url_for('admin_content.bulk_import'))

    if not isinstance(data, dict):
        msg = 'JSON root must be an object.'
        if request.is_json:
            return jsonify({'error': msg}), 400
        flash(msg, 'error')
        return redirect(url_for('admin_content.bulk_import'))

    metadata = data.get('metadata') or {}
    subject_code = (metadata.get('subject_code') or '').strip()
    chapter = (metadata.get('chapter') or '').strip()

    meta_grade_raw = (metadata.get('grade') or '').strip()
    if meta_grade_raw:
        grade = normalize_grade(meta_grade_raw)
        grade_source = 'metadata'
    else:
        grade = normalize_grade(request_grade_raw or DEFAULT_GRADE)
        grade_source = 'picker'

    if subject_code not in get_all_subject_codes():
        msg = f'Unknown subject_code: "{subject_code}"'
        if request.is_json:
            return jsonify({'error': msg}), 400
        flash(msg, 'error')
        return redirect(url_for('admin_content.bulk_import'))

    questions_raw = data.get('questions') or []
    if not isinstance(questions_raw, list) or not questions_raw:
        msg = '"questions" must be a non-empty array.'
        if request.is_json:
            return jsonify({'error': msg}), 400
        flash(msg, 'error')
        return redirect(url_for('admin_content.bulk_import'))

    pdf_code = pdf_code_raw or None
    if pdf_code and not _validate_pdf_code_format(pdf_code):
        msg = f'Invalid PDF code format: "{pdf_code}"'
        if request.is_json:
            return jsonify({'error': msg}), 400
        flash(msg, 'error')
        return redirect(url_for('admin_content.bulk_import'))

    questions_to_import = []
    errors = []
    duplicates = []
    warnings = []

    labels = ['A', 'B', 'C', 'D', 'E', 'F']

    for idx, q in enumerate(questions_raw, 1):
        if import_indices is not None and idx not in import_indices:
            continue

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

        dup_matches = find_duplicate_questions(
            q_text, subject_code=subject_code, grade=grade,
        )
        if dup_matches:
            top = dup_matches[0]
            pct = int(round((top.get('similarity') or 0) * 100))
            duplicates.append({
                'index': idx, 'question': q_text,
                'error': f'Duplicate of Q#{top["id"]} ({pct}% match)',
            })
            continue

        pdf_page = _normalize_pdf_page(q.get('pdf_page'))
        if pdf_page and not pdf_code:
            warnings.append({'index': idx,
                             'message': f'Q{idx}: pdf_page set but no PDF code — dropped'})
            pdf_page = None

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
            'grade': grade,
            'created_by': session.get('user_id'),
            'updated_by': session.get('user_id'),
        })

    if not request.is_json:
        if errors or duplicates:
            return render_template(
                'dashboard/admin/content/bulk_import.html',
                preview=True,
                valid_questions=questions_to_import,
                errors=errors, duplicates=duplicates, warnings=warnings,
                subject_code=subject_code, chapter=chapter,
                grade=grade, pdf_code=pdf_code,
                total_questions=len(questions_raw),
            )

    if not questions_to_import:
        msg = 'No valid questions to import.'
        if request.is_json:
            return jsonify({
                'error': msg,
                'errors': errors,
                'duplicates': duplicates,
                'warnings': warnings,
                'imported': 0,
                'skipped_invalid': len(errors),
                'skipped_duplicate': len(duplicates),
            }), 400
        flash(msg, 'error')
        return redirect(url_for('admin_content.bulk_import'))

    try:
        cursor = execute_with_retry(
            "SELECT COALESCE(MAX(id), 0) AS max_id FROM questions"
        )
        before_max_id = cursor.fetchone()['max_id']
    except Exception:
        before_max_id = 0

    try:
        result = bulk_create_questions(questions_to_import, session.get('user_id'))
    except Exception as e:
        logger.error(f"bulk_create_questions raised: {e}", exc_info=True)
        if request.is_json:
            return jsonify({'error': f'Import crashed: {e}'}), 500
        flash(f'Import crashed: {e}', 'error')
        return redirect(url_for('admin_content.bulk_import'))

    imported = result.get('imported', 0)
    failed = result.get('errors') or []

    batch_id = None
    if imported > 0:
        write_audit(
            action='question.bulk_import', target_type='question',
            before=None,
            after={
                'subject_code': subject_code,
                'imported': imported,
                'grade': grade,
                'grade_source': grade_source,
                'pdf_code': pdf_code,
            },
            severity='info',
        )
        batch_id = _auto_create_questions_batch(
            subject_code=subject_code,
            grade=grade,
            imported=imported,
            admin_id=session.get('user_id'),
            before_max_id=before_max_id,
        )

    if request.is_json:
        if imported == 0:
            detail = failed[0].get('error') if failed else 'unknown error'
            return jsonify({
                'error': f'Nothing inserted: {detail}',
                'imported': 0,
                'failed': failed,
                'skipped_invalid': len(errors),
                'skipped_duplicate': len(duplicates),
            }), 500

        batch_url = None
        if batch_id:
            try:
                batch_url = url_for('admin_batches.detail', batch_id=batch_id)
            except Exception:
                pass

        return jsonify({
            'success': True,
            'imported': imported,
            'failed': failed,
            'warnings': warnings,
            'skipped_invalid': len(errors),
            'skipped_duplicate': len(duplicates),
            'batch_id': batch_id,
            'batch_url': batch_url,
            'grade': grade,
            'grade_source': grade_source,
        })

    if imported > 0:
        flash(f'✅ {imported} questions imported successfully!', 'success')
        if batch_id:
            try:
                link = url_for('admin_batches.detail', batch_id=batch_id)
                flash(
                    f'📦 Batch created — '
                    f'<a href="{link}" style="font-weight:700;">'
                    f'open it</a> to bulk-edit these questions later.',
                    'info',
                )
            except Exception:
                pass
        if warnings:
            flash(f'⚠️ {len(warnings)} warning(s).', 'warning')
        if failed:
            flash(f'⚠️ {len(failed)} question(s) failed.', 'error')
        return redirect(url_for('admin_content.questions'))

    detail = failed[0].get('error') if failed else 'unknown error'
    flash(f'❌ Import failed. Reason: {detail}', 'error')
    return redirect(url_for('admin_content.bulk_import'))


@admin_content_bp.route('/bulk-template', methods=['GET'], endpoint='bulk_template')
@admin_can('questions.bulk_import')
def bulk_template():
    template = {
        "metadata": {
            "subject_code": "geography",
            "chapter": "Chapter 1: Introduction",
            "grade": "F4"
        },
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

    request_grade_raw = ''
    if request.is_json:
        body = request.get_json(silent=True) or {}
        raw = (body.get('json_data') or '').strip()
        pdf_code_raw = (body.get('pdf_code') or '').strip().upper()
        request_grade_raw = (body.get('grade') or '').strip()
        try:
            fuzzy_threshold = float(body.get('fuzzy_threshold') or 0.85)
        except (TypeError, ValueError):
            fuzzy_threshold = 0.85
        scope = (body.get('scope') or 'same_grade').strip()
        auto_exact = bool(body.get('auto_exclude_exact', True))
        auto_invalid = bool(body.get('auto_exclude_invalid', True))
    else:
        raw = (request.form.get('json_data') or '').strip()
        pdf_code_raw = (request.form.get('pdf_code') or '').strip().upper()
        request_grade_raw = (request.form.get('grade') or '').strip()
        try:
            fuzzy_threshold = float(request.form.get('fuzzy_threshold') or 0.85)
        except (TypeError, ValueError):
            fuzzy_threshold = 0.85
        scope = (request.form.get('scope') or 'same_grade').strip()
        auto_exact = True
        auto_invalid = True

    if fuzzy_threshold < 0.5:
        fuzzy_threshold = 0.5
    if fuzzy_threshold > 1.0:
        fuzzy_threshold = 1.0

    if scope not in ('same_grade', 'same_subject', 'any'):
        scope = 'same_grade'

    if not raw:
        return jsonify({'error': 'No JSON data provided'}), 400

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify({
            'error': f'Invalid JSON: {e.msg} (line {e.lineno}, col {e.colno})',
            'error_type': 'json',
        }), 400

    if not isinstance(data, dict):
        return jsonify({'error': 'JSON root must be an object', 'error_type': 'structure'}), 400
    if 'metadata' not in data or not isinstance(data.get('metadata'), dict):
        return jsonify({'error': 'Missing or invalid "metadata" section', 'error_type': 'structure'}), 400
    if 'questions' not in data or not isinstance(data.get('questions'), list) or not data['questions']:
        return jsonify({'error': '"questions" must be a non-empty array', 'error_type': 'structure'}), 400

    metadata = data.get('metadata') or {}
    subject_code = (metadata.get('subject_code') or '').strip()
    chapter = (metadata.get('chapter') or '').strip()

    meta_grade_raw = (metadata.get('grade') or '').strip()
    if meta_grade_raw:
        grade = normalize_grade(meta_grade_raw)
        grade_source = 'metadata'
    else:
        grade = normalize_grade(request_grade_raw or DEFAULT_GRADE)
        grade_source = 'picker'

    if not subject_code:
        return jsonify({'error': 'metadata.subject_code is required', 'error_type': 'structure'}), 400
    if subject_code not in get_all_subject_codes():
        return jsonify({
            'error': f'Unknown subject_code: "{subject_code}"',
            'error_type': 'structure',
        }), 400

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

    questions_raw = data['questions']
    total = len(questions_raw)
    labels = ['A', 'B', 'C', 'D', 'E', 'F']

    preview = []
    for idx, q in enumerate(questions_raw, 1):
        entry = {
            'index': idx,
            'status': 'ready',
            'invalid_reason': '',
            'question_short': '',
            'question_full': '',
            'options': {},
            'correct_answer': 'A',
            'correct_answer_text': '',
            'options_count': 0,
            'difficulty': 1,
            'has_explanation': False,
            'explanation': '',
            'explanation_short': '',
            'tags': '',
            'grade': grade,
            'subject_code': subject_code,
            'chapter': chapter,
            'pdf_code': pdf_code or '',
            'pdf_page': None,
            'pdf_exists': False,
            'pdf_title': '',
            'pdf_source': None,
            'orphan': False,
            'invalid_code': not pdf_code_valid,
            'duplicates': [],
        }

        if not isinstance(q, dict):
            entry['status'] = 'invalid'
            entry['invalid_reason'] = 'Entry must be an object'
            preview.append(entry)
            continue

        q_text = (q.get('question') or '').strip()
        entry['question_full'] = q_text
        entry['question_short'] = q_text[:70] + ('…' if len(q_text) > 70 else '')

        if not q_text:
            entry['status'] = 'invalid'
            entry['invalid_reason'] = 'Question text is required'
            preview.append(entry)
            continue

        opts = q.get('options')
        if not isinstance(opts, list) or len(opts) < 3:
            entry['status'] = 'invalid'
            entry['invalid_reason'] = f'At least 3 options required (got {len(opts) if isinstance(opts, list) else 0})'
            preview.append(entry)
            continue
        if len(opts) > 6:
            entry['status'] = 'invalid'
            entry['invalid_reason'] = f'Maximum 6 options (got {len(opts)})'
            preview.append(entry)
            continue

        correct_idx = q.get('correct')
        if not isinstance(correct_idx, int) or not (1 <= correct_idx <= len(opts)):
            entry['status'] = 'invalid'
            entry['invalid_reason'] = 'Invalid "correct" index'
            preview.append(entry)
            continue

        options_dict = {labels[i]: str(opts[i]).strip() for i in range(len(opts))}
        entry['options'] = options_dict
        entry['options_count'] = len(options_dict)
        entry['correct_answer'] = labels[correct_idx - 1]
        entry['correct_answer_text'] = options_dict.get(entry['correct_answer'], '')

        try:
            difficulty = int(q.get('difficulty', 1))
            if not (1 <= difficulty <= 5):
                difficulty = 3
        except (ValueError, TypeError):
            difficulty = 1
        entry['difficulty'] = difficulty

        expl_text = (q.get('explanation') or '').strip()
        entry['has_explanation'] = bool(expl_text)
        entry['explanation'] = expl_text
        entry['explanation_short'] = (
            expl_text[:140] + ('…' if len(expl_text) > 140 else '')
        )

        if isinstance(q.get('tags'), list):
            entry['tags'] = ', '.join(str(t) for t in q['tags'])[:60]
        else:
            entry['tags'] = str(q.get('tags', '') or '')[:60]

        pdf_page = _normalize_pdf_page(q.get('pdf_page'))
        if pdf_page and not pdf_code:
            entry['orphan'] = True
            pdf_page = None
        entry['pdf_page'] = pdf_page

        if pdf_code and pdf_info_payload:
            entry['pdf_exists'] = pdf_info_payload.get('exists', False)
            entry['pdf_title'] = pdf_info_payload.get('title', '')
            entry['pdf_source'] = pdf_info_payload.get('source')

        preview.append(entry)

    try:
        from services.question_validity import find_duplicates_batch

        batch_input = [{
            'text': e.get('question_full') or '',
            'subject_code': subject_code,
            'grade': grade,
        } for e in preview]

        all_dupes = find_duplicates_batch(
            batch_input,
            fuzzy_threshold=fuzzy_threshold,
            scope=scope,
        )

        for e, dupes in zip(preview, all_dupes):
            if not dupes:
                continue
            payload = []
            for d in dupes:
                full = d.get('question_text') or ''
                payload.append({
                    'id': d['id'],
                    'match_type': d['match_type'],
                    'similarity_pct': int(round((d.get('similarity') or 0) * 100)),
                    'text': full,
                    'options': d.get('options') or {},
                    'correct_answer': d.get('correct_answer') or 'A',
                    'difficulty': d.get('difficulty') or 1,
                    'grade': d.get('grade') or '',
                    'subject_code': d.get('subject_code') or '',
                    'chapter': d.get('chapter') or '',
                })
            e['duplicates'] = payload
            if e['status'] == 'ready':
                e['status'] = 'duplicate'
    except Exception as e:
        logger.warning(f"batch duplicate check failed (non-fatal): {e}")

    ready_count = sum(1 for e in preview if e['status'] == 'ready')
    dup_count   = sum(1 for e in preview if e['status'] == 'duplicate')
    inv_count   = sum(1 for e in preview if e['status'] == 'invalid')

    unknown_codes = set()
    if pdf_code and pdf_info_payload and not pdf_info_payload.get('exists'):
        unknown_codes.add(pdf_code)

    return jsonify({
        'ok': True,
        'subject_code': subject_code,
        'chapter': chapter,
        'grade': grade,
        'grade_source': grade_source,
        'pdf_code': pdf_code or '',
        'pdf_code_valid': pdf_code_valid,
        'pdf_info': pdf_info_payload,
        'total': total,
        'ready_count': ready_count,
        'duplicate_count': dup_count,
        'invalid_count': inv_count,
        'unknown_codes': sorted(unknown_codes),
        'preview': preview,
        'fuzzy_threshold': fuzzy_threshold,
        'scope': scope,
        'auto_exclude_exact': auto_exact,
        'auto_exclude_invalid': auto_invalid,
    })


# ============================================================
# PDFs — DASHBOARD + LIST (tab-based)
# ============================================================

@admin_content_bp.route('/pdfs', methods=['GET'], endpoint='pdfs')
@admin_can('pdfs.view')
def pdfs():
    # ── Tab routing ──
    active_tab = (request.args.get('tab') or 'library').strip().lower()
    if active_tab not in ('library', 'insights', 'people', 'activity'):
        active_tab = 'library'

    # ── Library tab filters ──
    status_filter = (request.args.get('status') or 'live').strip().lower()
    if status_filter not in ('live', 'flagged', 'pending', 'staged', 'all'):
        status_filter = 'live'

    search = (request.args.get('search') or '').strip()
    subject_filter = (request.args.get('subject') or '').strip()
    curriculum_filter = (request.args.get('curriculum') or '').strip()
    class_filter = (request.args.get('class') or '').strip()
    sort = (request.args.get('sort') or 'newest').strip()

    premium_filter = (request.args.get('premium') or '').strip()
    verified_filter = (request.args.get('verified') or '').strip()
    storage_filter = (request.args.get('storage') or '').strip()
    pdf_code_filter = (request.args.get('pdf_code_filter') or '').strip().upper()
    chapter_filter = (request.args.get('chapter') or '').strip()
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()

    views_min = _int_or_none(request.args.get('views_min'))
    views_max = _int_or_none(request.args.get('views_max'))

    # ── Per-page ──
    try:
        per_page = int(request.args.get('per_page') or 100)
    except (ValueError, TypeError):
        per_page = 100
    if per_page not in (50, 100, 200, 500):
        per_page = 100

    try:
        page = max(1, int(request.args.get('page') or 1))
    except (TypeError, ValueError):
        page = 1

    # ── Activity tab filters ──
    activity_type = (request.args.get('type') or '').strip()
    activity_role = (request.args.get('role') or '').strip()
    activity_pdf_code = (request.args.get('pdf_code') or '').strip().upper()
    activity_since = (request.args.get('since') or '').strip()
    activity_until = (request.args.get('until') or '').strip()

    try:
        activity_page = max(1, int(request.args.get('page') or 1))
    except (TypeError, ValueError):
        activity_page = 1

    # ── Analytics (single pass) ──
    dash = _pdfs_dashboard_analytics()
    heatmap_weeks = _pdfs_heatmap_weeks()

    # ── Library list ──
    pdfs_list = []
    total = 0
    total_pages = 1

    if active_tab == 'library':
        if status_filter in ('pending', 'staged'):
            offset = (page - 1) * per_page
            try:
                if status_filter == 'pending':
                    from bot.db import get_pending_pdf_list, count_pending_pdfs
                    raw = get_pending_pdf_list(limit=per_page, offset=offset, search=search or '')
                    total = count_pending_pdfs(search or '')
                    for p in raw:
                        pdfs_list.append({
                            'id': p['id'],
                            'title': p.get('filename') or 'Untitled',
                            'filename': p.get('filename'),
                            'code': None, 'subject': None,
                            'curriculum': None, 'class': None,
                            'is_premium': 0, 'view_count': 0,
                            'uploaded_at': p.get('uploaded_at'),
                            'created_at': p.get('uploaded_at'),
                            'is_pending': True, 'is_staged': False, 'is_flagged': False,
                        })
                else:
                    from bot.db import get_bot_pdfs, count_bot_pdfs
                    raw = get_bot_pdfs(limit=per_page, offset=offset, published_filter=False)
                    total = count_bot_pdfs(published_filter=False)
                    for p in raw:
                        pdfs_list.append({
                            'id': p['id'],
                            'title': p.get('title') or 'Untitled',
                            'filename': p.get('original_filename'),
                            'code': p.get('code'),
                            'subject': p.get('subject'),
                            'curriculum': p.get('curriculum'),
                            'class': p.get('class'),
                            'is_premium': bool(p.get('is_premium')),
                            'view_count': 0,
                            'uploaded_at': p.get('uploaded_at'),
                            'created_at': p.get('uploaded_at'),
                            'is_pending': False, 'is_staged': True, 'is_flagged': False,
                        })
            except Exception as e:
                logger.error(f"pdfs(): {status_filter} fetch failed: {e}", exc_info=True)
            total_pages = (total + per_page - 1) // per_page if total > 0 else 1
        else:
            offset = (page - 1) * per_page

            where = ["1=1"]
            params = []
            if search:
                where.append("(title LIKE ? OR code LIKE ? OR description LIKE ? OR tags LIKE ?)")
                like = f"%{search}%"
                params.extend([like, like, like, like])
            if subject_filter:
                where.append("subject = ?"); params.append(subject_filter)
            if curriculum_filter:
                where.append("curriculum = ?"); params.append(curriculum_filter)
            if class_filter:
                where.append("class = ?"); params.append(class_filter)
            if premium_filter == '1':
                where.append("is_premium = 1")
            elif premium_filter == '0':
                where.append("is_premium = 0")
            if pdf_code_filter:
                where.append("code = ?"); params.append(pdf_code_filter)
            if chapter_filter:
                where.append("chapter LIKE ?"); params.append(f"%{chapter_filter}%")
            if views_min is not None:
                where.append("view_count >= ?"); params.append(views_min)
            if views_max is not None:
                where.append("view_count <= ?"); params.append(views_max)
            if date_from:
                where.append("uploaded_at >= ?"); params.append(date_from)
            if date_to:
                where.append("uploaded_at <= ?"); params.append(date_to + ' 23:59:59')

            if status_filter == 'flagged':
                where.append("id IN (SELECT pdf_id FROM unverified_pdfs WHERE confirmed = 0)")
            elif status_filter == 'live':
                where.append("id NOT IN (SELECT pdf_id FROM unverified_pdfs WHERE confirmed = 0)")

            order = {
                'newest': 'uploaded_at DESC, id DESC',
                'oldest': 'uploaded_at ASC, id ASC',
                'title_asc': 'title COLLATE NOCASE ASC',
                'title_desc': 'title COLLATE NOCASE DESC',
                'popular': 'view_count DESC, id DESC',
            }.get(sort, 'uploaded_at DESC, id DESC')

            where_sql = " AND ".join(where)

            try:
                cursor = execute_with_retry(
                    f"SELECT * FROM pdfs WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
                    tuple(params + [per_page, offset])
                )
                rows = [dict(r) for r in cursor.fetchall()]
            except Exception as e:
                logger.error(f"pdfs(): library fetch failed: {e}", exc_info=True)
                rows = []

            try:
                cursor = execute_with_retry(
                    f"SELECT COUNT(*) AS n FROM pdfs WHERE {where_sql}",
                    tuple(params)
                )
                row = cursor.fetchone()
                total = row['n'] if row else 0
            except Exception:
                total = len(rows)

            flagged_ids = set()
            try:
                cursor = execute_with_retry(
                    "SELECT pdf_id FROM unverified_pdfs WHERE confirmed = 0"
                )
                flagged_ids = {r['pdf_id'] for r in cursor.fetchall()}
            except Exception:
                pass

            for p in rows:
                p['is_pending'] = False
                p['is_staged'] = False
                p['is_flagged'] = p['id'] in flagged_ids

            pdfs_list = rows
            total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    # ── Activity tab data ──
    activity_events = []
    activity_total_pages = 1
    if active_tab == 'activity':
        PER_PAGE_ACT = 100

        event_types = [activity_type] if activity_type else None
        role_filter = activity_role or None

        try:
            activity_events = get_global_events(
                limit=PER_PAGE_ACT,
                offset=(activity_page - 1) * PER_PAGE_ACT,
                event_types=event_types,
                actor_role=role_filter,
                pdf_code=activity_pdf_code or None,
                since=activity_since or None,
                until=activity_until or None,
            )
        except Exception as e:
            logger.warning(f"activity events load failed: {e}")

        try:
            act_total = count_global_events(
                event_types=event_types,
                actor_role=role_filter,
                pdf_code=activity_pdf_code or None,
                since=activity_since or None,
                until=activity_until or None,
            )
            activity_total_pages = (act_total + PER_PAGE_ACT - 1) // PER_PAGE_ACT if act_total > 0 else 1
        except Exception:
            activity_total_pages = 1

    # ── Active filter chips ──
    active_filters = []
    base_clear = lambda: url_for('admin_content.pdfs', tab='library', status=status_filter)
    if search:
        active_filters.append({'label': 'Search', 'value': search, 'clear_url': base_clear()})
    if subject_filter:
        subj = get_subject(subject_filter)
        active_filters.append({'label': 'Subject',
                               'value': subj['name'] if subj else subject_filter,
                               'clear_url': base_clear()})
    if class_filter:
        active_filters.append({'label': 'Class', 'value': class_filter, 'clear_url': base_clear()})
    if curriculum_filter:
        active_filters.append({'label': 'Curriculum', 'value': curriculum_filter, 'clear_url': base_clear()})
    if premium_filter in ('0', '1'):
        label = 'Premium' if premium_filter == '1' else 'Free'
        active_filters.append({'label': 'Access', 'value': label, 'clear_url': base_clear()})
    if pdf_code_filter:
        active_filters.append({'label': 'Code', 'value': pdf_code_filter, 'clear_url': base_clear()})
    if chapter_filter:
        active_filters.append({'label': 'Chapter', 'value': chapter_filter, 'clear_url': base_clear()})

    # ── Permissions ──
    can_intake = admin_can('pdfs.intake')
    can_publish = admin_can('pdfs.publish')
    can_edit = admin_can('pdfs.edit')
    can_delete = admin_can('pdfs.delete')

    # ── Advanced panel state ──
    advanced_open = bool(
        premium_filter or verified_filter or storage_filter or
        pdf_code_filter or chapter_filter or
        views_min is not None or views_max is not None or
        date_from or date_to
    )

    ctx = {
        'active_tab': active_tab,

        # Library
        'pdfs': pdfs_list,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
        'status_filter': status_filter,
        'premium_filter': premium_filter,
        'verified_filter': verified_filter,
        'storage_filter': storage_filter,
        'pdf_code_filter': pdf_code_filter,
        'chapter_filter': chapter_filter,
        'views_min': views_min,
        'views_max': views_max,
        'date_from': date_from,
        'date_to': date_to,
        'search': search,
        'subject_filter': subject_filter,
        'curriculum_filter': curriculum_filter,
        'class_filter': class_filter,
        'sort': sort,
        'subjects': get_all_subjects(),
        'curricula': [c[0] for c in _PDF_CURRICULA],
        'classes': list(_PDF_CLASSES),
        'active_filters': active_filters,
        'advanced_open': advanced_open,

        # KPI + snapshot
        'kpis': dash['kpis'],
        'today_activity': dash.get('today_activity', {}),
        'trending_pdfs': dash.get('trending_pdfs', []),

        # Insights
        'class_split': dash['class_split'],
        'class_split_total': dash.get('class_split_total', 0),
        'class_unclassified': dash.get('class_unclassified', 0),
        'subject_split_full': dash.get('subject_split_full', []),
        'top_viewed': dash['top_viewed'],
        'most_downloaded': dash.get('most_downloaded', []),
        'most_tg_fetched': dash.get('most_tg_fetched', []),
        'storage': dash['storage'],
        'coverage': dash.get('coverage', {}),
        'health_score': dash.get('health_score', 100),
        'health_breakdown': dash.get('health_breakdown', {}),
        'uploads_by_day': dash['uploads_by_day'],

        # People
        'top_contributors': dash.get('top_contributors', []),
        'recent_edits': dash.get('recent_edits', []),
        'upload_sources': dash.get('upload_sources', {}),
        'heatmap_weeks': heatmap_weeks,

        # Activity
        'activity_events': activity_events,
        'activity_page': activity_page,
        'activity_total_pages': activity_total_pages,
        'activity_type': activity_type,
        'activity_role': activity_role,
        'activity_pdf_code': activity_pdf_code,
        'activity_since': activity_since,
        'activity_until': activity_until,

        # Permissions
        'can_intake': can_intake,
        'can_publish': can_publish,
        'can_edit': can_edit,
        'can_delete': can_delete,
        'csrf_token': session.get('csrf_token'),
    }

    return render_template('dashboard/admin/content/pdfs.html', **ctx)


# ============================================================
# PDFs — LIBRARY PREVIEW / DOWNLOAD
# ============================================================

@admin_content_bp.route('/pdfs/<int:pdf_id>/preview', methods=['GET'],
                        endpoint='pdf_library_preview')
@admin_can('pdfs.view')
def pdf_library_preview(pdf_id):
    pdf = get_pdf_by_id(pdf_id)
    if not pdf:
        abort(404)

    raw_name = (pdf.get('title') or pdf.get('code') or f'pdf-{pdf_id}') + '.pdf'
    disposition = _encode_content_disposition('inline', raw_name)

    file_url = pdf.get('file_url')
    if file_url and os.path.exists(file_url) and os.path.isfile(file_url):
        try:
            with open(file_url, 'rb') as f:
                data = f.read()
            return _pdf_preview_response(data, disposition)
        except Exception as e:
            logger.warning(f"Local file read failed for pdf {pdf_id}: {e}")

    code = pdf.get('code')
    if not code:
        abort(404, 'PDF has no code')

    data, err = _fetch_telegram_pdf_bytes(code)
    if err or data is None:
        logger.warning(f"Preview failed for pdf {pdf_id} (code {code}): {err}")
        abort(502, err or 'Could not fetch PDF')

    return _pdf_preview_response(data, disposition)


@admin_content_bp.route('/pdfs/<int:pdf_id>/download', methods=['GET'],
                        endpoint='pdf_download')
@admin_can('pdfs.view')
def pdf_download(pdf_id):
    pdf = get_pdf_by_id(pdf_id)
    if not pdf:
        abort(404)

    raw_name = (pdf.get('title') or pdf.get('code') or f'pdf-{pdf_id}') + '.pdf'
    disposition = _encode_content_disposition('attachment', raw_name)

    file_url = pdf.get('file_url')
    if file_url and os.path.exists(file_url) and os.path.isfile(file_url):
        try:
            with open(file_url, 'rb') as f:
                data = f.read()
            return _pdf_response(data, disposition)
        except Exception as e:
            logger.warning(f"Local file read failed for pdf {pdf_id}: {e}")

    code = pdf.get('code')
    if not code:
        abort(404, 'PDF has no code')

    data, err = _fetch_telegram_pdf_bytes(code)
    if err or data is None:
        logger.warning(f"Download failed for pdf {pdf_id} (code {code}): {err}")
        abort(502, err or 'Could not fetch PDF')

    return _pdf_response(data, disposition)


# ============================================================
# PDFs — REFETCH SIZE
# ============================================================

@admin_content_bp.route('/pdfs/<int:pdf_id>/refetch-size', methods=['POST'],
                        endpoint='pdf_refetch_size')
@admin_can('pdfs.edit')
def pdf_refetch_size(pdf_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    pdf = get_pdf_by_id(pdf_id)
    if not pdf:
        return jsonify({'error': 'PDF not found'}), 404

    try:
        from bot.db import get_bot_pdf_by_code, update_bot_pdf_file_size
        bot_pdf = get_bot_pdf_by_code(pdf.get('code') or '')
    except Exception as e:
        return jsonify({'error': f'Bot DB unavailable: {e}'}), 500

    if not bot_pdf or not bot_pdf.get('file_id'):
        return jsonify({
            'error': 'No Telegram file stored for this PDF.',
            'reason': 'no_file',
        }), 400

    file_id = bot_pdf['file_id']

    try:
        _PDF_SIZE_CACHE.pop(file_id, None)
    except Exception:
        pass

    try:
        from bot.utils import get_bot
        bot = get_bot()
        file_info = bot.get_file(file_id)
        size = getattr(file_info, 'file_size', None)
    except Exception as e:
        msg = str(e).lower()
        too_large = any(tok in msg for tok in ('too big', 'too large', '413'))
        sentinel = -2 if too_large else -1
        try:
            update_bot_pdf_file_size(bot_pdf['id'], sentinel)
        except Exception:
            pass
        try:
            log_admin_event(
                {'id': pdf_id, 'code': pdf.get('code'), 'title': pdf.get('title')},
                'refetch_size',
                admin={'id': session.get('user_id')},
                request=request,
                metadata={'sentinel': sentinel, 'too_large': too_large},
            )
        except Exception:
            pass
        return jsonify({
            'success': False,
            'size_mb': None,
            'sentinel': sentinel,
            'too_large': too_large,
            'message': 'File too large for direct download.'
                       if too_large else
                       'Telegram could not resolve the file — retry later.',
        })

    if not size or int(size) <= 0:
        try:
            update_bot_pdf_file_size(bot_pdf['id'], -1)
        except Exception:
            pass
        return jsonify({
            'success': False,
            'size_mb': None,
            'sentinel': -1,
            'too_large': False,
            'message': 'Telegram returned no size — retry later.',
        })

    size = int(size)
    try:
        update_bot_pdf_file_size(bot_pdf['id'], size)
    except Exception as e:
        logger.warning(f"refetch_size: DB write failed: {e}")

    size_mb = round(size / (1024 * 1024), 2)
    too_large = size > _PDF_SIZE_CAP_BYTES

    try:
        log_admin_event(
            {'id': pdf_id, 'code': pdf.get('code'), 'title': pdf.get('title')},
            'refetch_size',
            admin={'id': session.get('user_id')},
            request=request,
            metadata={'size_bytes': size, 'size_mb': size_mb},
        )
    except Exception:
        pass

    return jsonify({
        'success': True,
        'size_mb': size_mb,
        'bytes': size,
        'too_large': too_large,
        'message': f'Resolved: {size_mb} MB'
                   + (' — exceeds direct-download cap.' if too_large else ''),
    })


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

    preview_info = _build_preview_info(pdf, pdf.get('file_url'))

    batches = []
    try:
        from db import get_batches_for_item
        batches = get_batches_for_item('pdf', pdf_id)
    except Exception:
        pass

    is_verified = False
    try:
        row = execute_with_retry(
            "SELECT confirmed FROM unverified_pdfs WHERE pdf_id = ? LIMIT 1",
            (pdf_id,)
        ).fetchone()
        if row:
            is_verified = bool(row['confirmed'])
        else:
            is_verified = True
    except Exception:
        is_verified = True

    pdf_state = 'flagged' if not is_verified else 'live'

    linked_questions = _load_linked_questions(pdf.get('code'))
    chapters_in_subject = _load_chapters_in_subject(pdf.get('subject'))
    top_tags = _load_top_tags()

    try:
        recent_events = get_recent_events_for_pdf(pdf_id, limit=10)
        activity_summary = get_pdf_event_summary(pdf_id)
        activity_summary['total'] = sum(activity_summary.values())
    except Exception:
        recent_events = []
        activity_summary = {'total': 0}

    pdf_annotated = dict(pdf)
    pdf_annotated['is_verified'] = is_verified

    return render_template(
        'dashboard/admin/content/pdf_edit.html',
        pdf=pdf_annotated,
        pdf_state=pdf_state,
        preview_info=preview_info,
        batches=batches,
        linked_questions=linked_questions,
        chapters_in_subject=chapters_in_subject,
        top_tags=top_tags,
        recent_events=recent_events,
        activity_summary=activity_summary,
        subjects=get_all_subjects(),
        curricula=_PDF_CURRICULA,
        classes=_PDF_CLASSES,
        csrf_token=session.get('csrf_token'),
        can_delete_pdfs=admin_can('pdfs.delete'),
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

    new_code = _normalize_pdf_code(request.form.get('code'))

    title       = (request.form.get('title') or '').strip()
    description = (request.form.get('description') or '').strip()
    curriculum  = (request.form.get('curriculum') or '').strip()
    class_val   = (request.form.get('class') or '').strip()
    subject     = (request.form.get('subject') or '').strip()
    chapter     = (request.form.get('chapter') or '').strip()
    tags        = (request.form.get('tags') or '').strip()

    is_premium = _coerce_bool(request.form.get('is_premium'))
    mark_verified = _coerce_bool(request.form.get('mark_verified'))

    errors = []
    if not title:
        errors.append('Title is required.')
    if not subject:
        errors.append('Subject is required.')

    valid_curriculum_codes = {c[0] for c in _PDF_CURRICULA}
    if curriculum and curriculum not in valid_curriculum_codes:
        errors.append(f'Invalid curriculum: {curriculum}')
    if class_val and class_val not in _PDF_CLASSES:
        errors.append(f'Invalid class: {class_val}')
    if subject and subject not in get_all_subject_codes():
        errors.append(f'Unknown subject: {subject}')

    code_changed = (new_code and new_code != pdf.get('code'))
    if new_code:
        if not _validate_pdf_code_format(new_code):
            errors.append('PDF code must match XXXX-XXXX.')
        elif code_changed:
            try:
                clash = execute_with_retry(
                    "SELECT id FROM pdfs WHERE code = ? AND id != ? LIMIT 1",
                    (new_code, pdf_id),
                ).fetchone()
                if clash:
                    errors.append(f'Code {new_code} is already used by another PDF.')
            except Exception as e:
                logger.warning(f"pdf_update: code check failed: {e}")
                errors.append('Could not verify code availability. Try again.')
    else:
        errors.append('PDF code is required.')

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('admin_content.pdf_edit', pdf_id=pdf_id))

    try:
        execute_with_retry("""
            UPDATE pdfs SET
                code = ?,
                title = ?, description = ?,
                curriculum = ?, class = ?,
                subject = ?, chapter = ?, tags = ?,
                is_premium = ?
            WHERE id = ?
        """, (
            new_code or pdf.get('code'),
            title, description,
            curriculum, class_val,
            subject, chapter, tags,
            is_premium,
            pdf_id,
        ), commit=True)

        if mark_verified:
            try:
                execute_with_retry(
                    "UPDATE unverified_pdfs SET confirmed = 1 WHERE pdf_id = ?",
                    (pdf_id,), commit=True,
                )
            except Exception:
                pass

        try:
            from bot.db import get_bot_pdf_by_code, update_bot_pdf
            if pdf.get('code'):
                bot_row = get_bot_pdf_by_code(pdf.get('code'))
                if bot_row:
                    update_bot_pdf(bot_row['id'], {
                        'title': title,
                        'description': description,
                        'curriculum': curriculum,
                        'class': class_val,
                        'subject': subject,
                        'chapter': chapter,
                        'tags': tags,
                        'is_premium': is_premium,
                    })
        except Exception as e:
            logger.warning(f"pdf_update: bot sync failed (non-fatal): {e}")

        try:
            admin_actor = {
                'id': session.get('user_id'),
                'public_id': session.get('public_id'),
                'first_name': (session.get('user_name') or '').split()[0]
                              if session.get('user_name') else '',
                'last_name': '',
                'is_super': False,
            }
            pdf_row = {
                'id': pdf_id,
                'code': new_code or pdf.get('code'),
                'title': title,
            }
            log_admin_event(
                pdf_row, 'edit', admin=admin_actor, request=request,
                metadata={
                    'title_changed': pdf.get('title') != title,
                    'subject_changed': pdf.get('subject') != subject,
                },
            )
            if pdf.get('code') and new_code and pdf.get('code') != new_code:
                log_admin_event(
                    pdf_row, 'code_change', admin=admin_actor, request=request,
                    metadata={'old_code': pdf.get('code'), 'new_code': new_code},
                )
            if bool(pdf.get('is_premium')) != bool(is_premium):
                log_admin_event(
                    pdf_row,
                    'premium_on' if is_premium else 'premium_off',
                    admin=admin_actor, request=request,
                )
            if mark_verified:
                log_admin_event(
                    pdf_row, 'verify', admin=admin_actor, request=request,
                )
        except Exception as _e:
            logger.debug(f"pdf_update: event log failed (non-fatal): {_e}")

        write_audit(
            action='pdf.update', target_type='pdf', target_id=pdf_id,
            before={
                'code': pdf.get('code'),
                'title': pdf.get('title'),
                'subject': pdf.get('subject'),
                'is_premium': pdf.get('is_premium'),
            },
            after={
                'code': new_code or pdf.get('code'),
                'title': title,
                'subject': subject,
                'is_premium': is_premium,
                'verified': bool(mark_verified),
            },
            severity='info',
        )

        flash('PDF updated.' + (' Marked as verified.' if mark_verified else ''),
              'success')
        return redirect(url_for('admin_content.pdf_edit', pdf_id=pdf_id))

    except Exception as e:
        logger.error(f"pdf_update failed: {e}", exc_info=True)
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
        try:
            log_admin_event(
                {'id': pdf_id, 'code': pdf.get('code'), 'title': pdf.get('title')},
                'delete',
                admin={'id': session.get('user_id')},
                request=request,
            )
        except Exception:
            pass
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
# PDFs — INTAKE
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
        return redirect(url_for('admin_content.pdfs', status='pending'))

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
        is_premium = _coerce_bool(request.form.get('is_premium'))

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
        if class_filter and class_filter not in ('F4', 'F3', 'G8', 'G7'):
            errors.append('Invalid class.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return redirect(url_for('admin_content.pdf_intake_process',
                                    pending_id=pending_id))

        bot_pdf_id = insert_bot_pdf({
            'code': code, 'title': title,
            'description': description, 'curriculum': curriculum,
            'class': class_filter, 'subject': subject,
            'chapter': chapter, 'tags': tags,
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

        try:
            log_admin_event(
                {'id': bot_pdf_id, 'code': code, 'title': title},
                'create',
                admin={'id': session.get('user_id')},
                request=request,
                metadata={'pending_id': pending_id, 'filename': filename},
            )
        except Exception:
            pass

        write_audit(
            action='pdf.intake.processed',
            target_type='pdf_staging', target_id=bot_pdf_id,
            before={'pending_id': pending_id, 'filename': filename},
            after={'code': code, 'title': title, 'subject': subject},
            severity='info',
        )

        flash(f'PDF staged with code {code}.', 'success')
        return redirect(url_for('admin_content.pdfs', status='staged'))

    # GET: render edit room in "pending" mode
    auto_code = _generate_staging_pdf_code()
    suggested = suggest_from_filename(filename)

    pseudo_pdf = {
        'id': pending_id,
        'code': auto_code,
        'title': suggested.get('title') or '',
        'filename': filename,
        'description': '',
        'curriculum': 'PL',
        'class': 'F4',
        'subject': suggested.get('subject') or '',
        'chapter': suggested.get('chapter') or '',
        'tags': suggested.get('tags') or '',
        'is_premium': False,
        'view_count': 0,
        'uploaded_at': pending.get('uploaded_at'),
        'created_at': pending.get('uploaded_at'),
        'file_id': pending.get('file_id'),
        'file_url': None,
    }

    preview_info = {
        'available': True, 'source': 'telegram',
        'size_mb': None, 'size_status': 'ok',
        'too_large': False, 'has_local_file': False,
        'has_telegram': bool(pending.get('file_id')),
    }

    return render_template(
        'dashboard/admin/content/pdf_edit.html',
        pdf=pseudo_pdf,
        pdf_state='pending',
        preview_info=preview_info,
        batches=[],
        linked_questions=[],
        chapters_in_subject=[],
        top_tags=_load_top_tags(),
        recent_events=[],
        activity_summary={'total': 0},
        subjects=get_all_subjects(),
        curricula=_PDF_CURRICULA,
        classes=list(_PDF_CLASSES),
        csrf_token=session.get('csrf_token'),
        can_delete_pdfs=False,
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

    raw_name = pending.get('filename') or 'document.pdf'
    disposition = _encode_content_disposition('inline', raw_name)

    try:
        from bot.utils import get_bot
        bot = get_bot()
        tg_file = bot.get_file(file_id)
        data = bot.download_file(tg_file.file_path)
        if not data:
            abort(502, 'Telegram returned an empty file.')
    except Exception as e:
        logger.error(
            f"Preview failed for pending #{pending_id} (file_id={file_id}): {e}",
            exc_info=True,
        )
        abort(502)

    return _pdf_preview_response(data, disposition)


@admin_content_bp.route('/pdfs/intake/publish-direct', methods=['POST'],
                        endpoint='pdf_intake_publish_direct')
@admin_can('pdfs.publish')
def pdf_intake_publish_direct():
    if not validate_csrf():
        abort(403)

    pending_ids = request.form.getlist('pending_ids')
    if not pending_ids:
        flash('No PDFs selected.', 'info')
        return redirect(url_for('admin_content.pdfs', status='pending'))

    clean_ids = []
    for pid in pending_ids:
        try:
            clean_ids.append(str(int(pid)))
        except (ValueError, TypeError):
            continue

    target = url_for('admin_content.pdfs_bulk_workspace')
    if clean_ids:
        target += '?selected=' + ','.join(clean_ids)

    return redirect(target)


@admin_content_bp.route('/pdfs/intake/publish-commit', methods=['POST'],
                        endpoint='pdf_intake_publish_commit')
@admin_can('pdfs.publish')
def pdf_intake_publish_commit():
    if not validate_csrf():
        abort(403)
    flash('This workflow has moved. Use the bulk workspace.', 'info')
    return redirect(url_for('admin_content.pdfs_bulk_workspace'))


# ============================================================
# PDFs — STAGING
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
            'is_premium': _coerce_bool(request.form.get('is_premium')),
        }

        if not data['title'] or not data['subject']:
            flash('Title and Subject are required.', 'error')
            return redirect(url_for('admin_content.pdf_staging_edit',
                                    staging_id=staging_id))

        if update_bot_pdf(staging_id, data):
            try:
                log_admin_event(
                    {'id': staging_id,
                     'code': bot_pdf.get('code'),
                     'title': data['title']},
                    'staging_edit',
                    admin={'id': session.get('user_id')},
                    request=request,
                )
            except Exception:
                pass

            write_audit(
                action='pdf.staging.updated',
                target_type='pdf_staging', target_id=staging_id,
                before={'title': bot_pdf.get('title'),
                        'subject': bot_pdf.get('subject')},
                after={'title': data['title'], 'subject': data['subject']},
                severity='info',
            )
            flash('Staging PDF updated.', 'success')
            return redirect(url_for('admin_content.pdfs', status='staged'))

        flash('Failed to update staging PDF.', 'error')
        return redirect(url_for('admin_content.pdf_staging_edit',
                                staging_id=staging_id))

    pseudo_pdf = {
        'id': staging_id,
        'code': bot_pdf.get('code'),
        'title': bot_pdf.get('title') or '',
        'filename': bot_pdf.get('original_filename'),
        'description': bot_pdf.get('description') or '',
        'curriculum': bot_pdf.get('curriculum') or 'PL',
        'class': bot_pdf.get('class') or 'F4',
        'subject': bot_pdf.get('subject') or '',
        'chapter': bot_pdf.get('chapter') or '',
        'tags': bot_pdf.get('tags') or '',
        'is_premium': bool(bot_pdf.get('is_premium')),
        'view_count': 0,
        'uploaded_at': bot_pdf.get('uploaded_at'),
        'created_at': bot_pdf.get('uploaded_at'),
        'file_id': bot_pdf.get('file_id'),
        'file_url': None,
        'is_verified': False,
    }

    preview_info = _build_preview_info(pseudo_pdf, None)

    return render_template(
        'dashboard/admin/content/pdf_edit.html',
        pdf=pseudo_pdf,
        pdf_state='staged',
        preview_info=preview_info,
        batches=[],
        linked_questions=_load_linked_questions(bot_pdf.get('code')),
        chapters_in_subject=_load_chapters_in_subject(bot_pdf.get('subject')),
        top_tags=_load_top_tags(),
        recent_events=[],
        activity_summary={'total': 0},
        subjects=get_all_subjects(),
        curricula=_PDF_CURRICULA,
        classes=list(_PDF_CLASSES),
        csrf_token=session.get('csrf_token'),
        can_delete_pdfs=False,
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
            target_type='pdf_staging', target_id=staging_id,
            before={'code': bot_pdf.get('code'),
                    'title': bot_pdf.get('title')},
            after=None, severity='warning',
        )
        flash('Staging PDF deleted.', 'success')
    else:
        flash('Failed to delete staging PDF.', 'error')

    return redirect(url_for('admin_content.pdfs', status='staged'))


@admin_content_bp.route('/pdfs/staging/publish',
                        methods=['POST'], endpoint='pdf_staging_publish')
@admin_can('pdfs.publish')
def pdf_staging_publish():
    if not validate_csrf():
        abort(403)

    ids = request.form.getlist('staging_ids')
    if not ids:
        flash('No PDFs selected.', 'error')
        return redirect(url_for('admin_content.pdfs', status='staged'))

    succeeded = 0
    failed = 0
    failures = []
    published_main_ids = []

    for sid in ids:
        try:
            sid_int = int(sid)
        except (ValueError, TypeError):
            failed += 1
            continue
        ok, msg = publish_bot_pdf_to_main(sid_int)
        if ok:
            succeeded += 1
            try:
                from bot.db import get_bot_pdf_by_id
                bpdf = get_bot_pdf_by_id(sid_int)
                if bpdf and bpdf.get('code'):
                    main_row = get_pdf_by_code(bpdf['code'])
                    if main_row:
                        published_main_ids.append(main_row['id'])
                        try:
                            log_admin_event(
                                {'id': main_row['id'],
                                 'code': bpdf.get('code'),
                                 'title': bpdf.get('title')},
                                'publish',
                                admin={'id': session.get('user_id')},
                                request=request,
                            )
                        except Exception:
                            pass
            except Exception:
                pass
        else:
            failed += 1
            failures.append(f'#{sid_int}: {msg}')

    if succeeded:
        write_audit(
            action='pdf.publish_bulk', target_type='pdf', before=None,
            after={'succeeded': succeeded, 'failed': failed, 'ids': ids},
            severity='warning',
        )

        batch_id = _auto_create_pdfs_batch(
            published_main_ids,
            admin_id=session.get('user_id'),
            source_label='staging publish',
        )

        flash(f'{succeeded} PDF{"s" if succeeded != 1 else ""} '
              f'published to the platform.', 'success')
        if batch_id:
            try:
                link = url_for('admin_batches.detail', batch_id=batch_id)
                flash(
                    f'📦 Batch created — '
                    f'<a href="{link}" style="font-weight:700;">'
                    f'open it</a> to bulk-edit these PDFs later.',
                    'info',
                )
            except Exception:
                pass
    if failed:
        first = failures[0] if failures else 'unknown error'
        flash(f'{failed} failed. First: {first}', 'error')

    return redirect(url_for('admin_content.pdfs', status='staged'))


@admin_content_bp.route('/pdfs/staging/publish-all',
                        methods=['POST'], endpoint='pdf_staging_publish_all')
@admin_can('pdfs.publish')
def pdf_staging_publish_all():
    if not validate_csrf():
        abort(403)

    from bot.db import get_bot_pdfs
    try:
        all_pdfs = get_bot_pdfs(limit=10000, offset=0, published_filter=False)
    except Exception as e:
        logger.error(f"publish-all load failed: {e}")
        flash('Could not load staging PDFs.', 'error')
        return redirect(url_for('admin_content.pdfs', status='staged'))

    succeeded = 0
    failed = 0
    published_main_ids = []

    for p in all_pdfs:
        ok, _ = publish_bot_pdf_to_main(p['id'])
        if ok:
            succeeded += 1
            try:
                if p.get('code'):
                    main_row = get_pdf_by_code(p['code'])
                    if main_row:
                        published_main_ids.append(main_row['id'])
                        try:
                            log_admin_event(
                                {'id': main_row['id'],
                                 'code': p.get('code'),
                                 'title': p.get('title')},
                                'publish',
                                admin={'id': session.get('user_id')},
                                request=request,
                            )
                        except Exception:
                            pass
            except Exception:
                pass
        else:
            failed += 1

    if succeeded:
        write_audit(
            action='pdf.publish_all', target_type='pdf', before=None,
            after={'succeeded': succeeded, 'failed': failed},
            severity='warning',
        )

        batch_id = _auto_create_pdfs_batch(
            published_main_ids,
            admin_id=session.get('user_id'),
            source_label='staging publish-all',
        )

        flash(f'{succeeded} PDF{"s" if succeeded != 1 else ""} published.',
              'success')
        if batch_id:
            try:
                link = url_for('admin_batches.detail', batch_id=batch_id)
                flash(
                    f'📦 Batch created — '
                    f'<a href="{link}" style="font-weight:700;">'
                    f'open it</a> to bulk-edit these PDFs later.',
                    'info',
                )
            except Exception:
                pass
    if failed:
        flash(f'{failed} failed to publish.', 'error')

    return redirect(url_for('admin_content.pdfs', status='staged'))


# ============================================================
# PDFs — UNVERIFIED
# ============================================================

@admin_content_bp.route('/pdfs/unverified/confirm', methods=['POST'],
                        endpoint='pdf_unverified_confirm')
@admin_can('pdfs.intake')
def pdf_unverified_confirm():
    if not validate_csrf():
        abort(403)

    ids = request.form.getlist('unverified_ids')
    if not ids:
        flash('No PDFs selected.', 'error')
        return redirect(url_for('admin_content.pdfs', status='flagged'))

    clean_ids = []
    for i in ids:
        try:
            clean_ids.append(int(i))
        except (ValueError, TypeError):
            continue

    if not clean_ids:
        flash('No valid records selected.', 'error')
        return redirect(url_for('admin_content.pdfs', status='flagged'))

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
            target_type='unverified_pdf', before=None,
            after={'count': count, 'ids': clean_ids},
            severity='info',
        )
        flash(f'{count} PDF{"s" if count != 1 else ""} marked as reviewed.',
              'success')
    except Exception as e:
        logger.error(f"unverified confirm failed: {e}")
        flash('Error updating records.', 'error')

    return redirect(url_for('admin_content.pdfs', status='flagged'))


@admin_content_bp.route('/pdfs/unverified/delete', methods=['POST'],
                        endpoint='pdf_unverified_delete')
@admin_can('pdfs.publish')
def pdf_unverified_delete():
    if not validate_csrf():
        abort(403)

    ids = request.form.getlist('unverified_ids')
    if not ids:
        flash('No records selected.', 'error')
        return redirect(url_for('admin_content.pdfs', status='flagged'))

    clean_ids = []
    for i in ids:
        try:
            clean_ids.append(int(i))
        except (ValueError, TypeError):
            continue

    if not clean_ids:
        flash('No valid records selected.', 'error')
        return redirect(url_for('admin_content.pdfs', status='flagged'))

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
            after=None, severity='warning',
        )
        flash(
            f'{count} record{"s" if count != 1 else ""} removed from the '
            f'review queue. The PDFs themselves remain in the library.',
            'info',
        )
    except Exception as e:
        logger.error(f"unverified delete failed: {e}")
        flash('Error removing records.', 'error')

    return redirect(url_for('admin_content.pdfs', status='flagged'))


# ============================================================
# PDFs — BULK WORKSPACE
# ============================================================

def _load_all_existing_pdf_codes():
    codes = set()
    try:
        cursor = execute_with_retry("SELECT code FROM pdfs WHERE code IS NOT NULL")
        for row in cursor.fetchall():
            codes.add(str(row['code']).strip().upper())
    except Exception as e:
        logger.warning(f"_load_all_existing_pdf_codes: main DB read failed: {e}")

    try:
        from bot.db import _get_connection as bot_conn
        bc = bot_conn()
        try:
            cur = bc.execute("SELECT code FROM pdfs WHERE code IS NOT NULL")
            for row in cur.fetchall():
                code = row[0] if not hasattr(row, 'keys') else row['code']
                if code:
                    codes.add(str(code).strip().upper())
        finally:
            bc.close()
    except Exception as e:
        logger.warning(f"_load_all_existing_pdf_codes: bot DB read failed: {e}")

    return codes


def _generate_code_from_set(existing_codes):
    chars = string.ascii_uppercase + '123456789'
    for _ in range(200):
        code = ''.join(secrets.choice(chars) for _ in range(4)) + '-' + \
               ''.join(secrets.choice(chars) for _ in range(4))
        if code not in existing_codes:
            existing_codes.add(code)
            return code
    return ''.join(secrets.choice(chars) for _ in range(12))


def _bulk_workspace_row(pending, existing_codes):
    filename = pending.get('filename') or ''
    suggestion = suggest_full(filename)
    auto_code = _generate_code_from_set(existing_codes)

    return {
        'pending_id':     int(pending['id']),
        'filename':       filename,
        'file_id':        pending.get('file_id', ''),
        'file_unique_id': pending.get('file_unique_id', ''),
        'uploaded_by':    pending.get('uploaded_by'),
        'uploaded_at':    pending.get('uploaded_at', ''),
        'code':           auto_code,
        'title':          suggestion['title'],
        'subject':        suggestion['subject'],
        'curriculum':     'PL',
        'class':          'F4',
        'chapter':        suggestion['chapter'],
        'tags':           suggestion['tags'],
        'is_premium':     0,
        'confidence':     suggestion['confidence'],
        'needs_review':   suggestion['needs_review'],
        'warnings':       suggestion['warnings'],
    }


@admin_content_bp.route('/pdfs/bulk-workspace', methods=['GET'],
                        endpoint='pdfs_bulk_workspace')
@admin_can('pdfs.intake')
def pdfs_bulk_workspace():
    BULK_MAX_ROWS = 1000

    from bot.db import get_pending_pdf_list, count_pending_pdfs

    try:
        total_pending = count_pending_pdfs()
    except Exception:
        total_pending = 0

    try:
        raw_pending = get_pending_pdf_list(
            limit=BULK_MAX_ROWS, offset=0, search=None,
        )
    except Exception as e:
        logger.error(f"bulk workspace: could not load pending: {e}", exc_info=True)
        raw_pending = []

    existing_codes = _load_all_existing_pdf_codes()
    rows = [_bulk_workspace_row(p, existing_codes) for p in raw_pending]

    preselect_raw = (request.args.get('selected') or '').strip()
    preselect = []
    if preselect_raw:
        for token in preselect_raw.split(','):
            try:
                preselect.append(int(token))
            except (ValueError, TypeError):
                continue

    truncated = total_pending > BULK_MAX_ROWS
    can_publish = admin_can('pdfs.publish')

    return render_template(
        'dashboard/admin/content/bulk_pdf_workspace.html',
        rows=rows, preselect=preselect,
        total_pending=total_pending,
        truncated=truncated, max_rows=BULK_MAX_ROWS,
        can_publish=can_publish,
        subjects=get_all_subjects(),
        curricula=_PDF_CURRICULA,
        classes=list(_PDF_CLASSES),
    )


@admin_content_bp.route('/pdfs/bulk-workspace/commit', methods=['POST'],
                        endpoint='pdfs_bulk_workspace_commit')
@admin_can('pdfs.intake')
def pdfs_bulk_workspace_commit():
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    data = request.get_json(silent=True) or {}
    rows = data.get('rows') or []
    mode = (data.get('mode') or 'stage').strip().lower()

    if not isinstance(rows, list) or not rows:
        return jsonify({'error': 'No rows submitted'}), 400

    if mode not in ('stage', 'publish'):
        return jsonify({'error': f'Unknown mode: {mode}'}), 400

    if mode == 'publish' and not admin_can('pdfs.publish'):
        return jsonify({'error': 'Publish permission required'}), 403

    from bot.db import (
        get_pending_pdf_by_id, insert_bot_pdf,
        delete_pending_pdf, get_bot_pdf_by_code,
    )

    results = {
        'staged': 0, 'published': 0, 'failed': 0,
        'failures': [], 'skipped': [], 'batch_id': None,
    }

    published_main_ids = []

    for idx, row in enumerate(rows, start=1):
        try:
            pending_id = int(row.get('pending_id'))
        except (TypeError, ValueError):
            results['failed'] += 1
            results['failures'].append(f'row {idx}: invalid pending_id')
            continue

        code = (row.get('code') or '').strip().upper()
        title = (row.get('title') or '').strip()
        subject = (row.get('subject') or '').strip() or None
        curriculum = (row.get('curriculum') or 'PL').strip() or 'PL'
        class_val = (row.get('class') or 'F4').strip() or 'F4'
        chapter = (row.get('chapter') or '').strip()
        tags = (row.get('tags') or '').strip()
        is_premium = _coerce_bool(row.get('is_premium'))

        if not code or not _validate_pdf_code_format(code):
            results['failed'] += 1
            results['failures'].append(f'#{pending_id}: invalid code "{code}"')
            continue
        if not title:
            results['failed'] += 1
            results['failures'].append(f'#{pending_id}: title required')
            continue

        try:
            if get_bot_pdf_by_code(code) or get_pdf_by_code(code):
                results['failed'] += 1
                results['failures'].append(f'#{pending_id}: code "{code}" already used')
                continue
        except Exception as e:
            logger.warning(f"code availability check failed: {e}")

        try:
            pending = get_pending_pdf_by_id(pending_id)
        except Exception as e:
            results['failed'] += 1
            results['failures'].append(f'#{pending_id}: lookup failed: {e}')
            continue

        if not pending:
            results['failed'] += 1
            results['failures'].append(f'#{pending_id}: no longer in intake')
            continue

        staging_id = None
        try:
            staging_id = insert_bot_pdf({
                'code':              code,
                'title':             title,
                'description':       '',
                'curriculum':        curriculum,
                'class':             class_val,
                'subject':           subject,
                'chapter':           chapter,
                'tags':              tags,
                'is_premium':        is_premium,
                'file_id':           pending['file_id'],
                'file_unique_id':    pending['file_unique_id'],
                'uploaded_by':       pending['uploaded_by'],
                'original_filename': pending.get('filename', ''),
            })
        except Exception as e:
            err = str(e).lower()
            if 'unique' in err and 'file_unique_id' in err:
                try:
                    delete_pending_pdf(pending_id)
                except Exception:
                    pass
                results['skipped'].append(f'#{pending_id}: duplicate file')
                continue
            if 'locked' in err or 'busy' in err:
                results['failed'] += 1
                results['failures'].append(f'#{pending_id}: DB locked, try again')
                continue
            results['failed'] += 1
            results['failures'].append(f'#{pending_id}: insert failed: {e}')
            continue

        if not staging_id:
            results['failed'] += 1
            results['failures'].append(f'#{pending_id}: staging insert returned no id')
            continue

        results['staged'] += 1

        if mode == 'publish':
            try:
                ok, msg = publish_bot_pdf_to_main(staging_id)
            except Exception as e:
                ok, msg = False, f'exception: {e}'

            if not ok:
                results['failures'].append(
                    f'#{pending_id}: staged but publish failed ({msg})'
                )
            else:
                results['published'] += 1
                try:
                    main_pdf = get_pdf_by_code(code)
                    if main_pdf:
                        published_main_ids.append(main_pdf['id'])
                        execute_with_retry(
                            "INSERT OR IGNORE INTO unverified_pdfs (pdf_id) "
                            "VALUES (?)",
                            (main_pdf['id'],), commit=True,
                        )
                except Exception as e:
                    logger.warning(
                        f"unverified_pdfs insert failed for code {code}: {e}"
                    )

        try:
            delete_pending_pdf(pending_id)
        except Exception as e:
            logger.warning(f"delete_pending_pdf({pending_id}) failed: {e}")

    if mode == 'publish' and published_main_ids:
        batch_id = _auto_create_pdfs_batch(
            published_main_ids,
            admin_id=session.get('user_id'),
            source_label='bot bulk workspace',
        )
        if batch_id:
            results['batch_id'] = batch_id

    try:
        write_audit(
            action=f'pdf.bulk_workspace.{mode}',
            target_type='pdf', before=None,
            after={
                'submitted': len(rows),
                'staged':    results['staged'],
                'published': results['published'],
                'failed':    results['failed'],
                'batch_id':  results.get('batch_id'),
            },
            severity='info',
        )
    except Exception:
        pass

    return jsonify(results)


@admin_content_bp.route('/pdfs/bulk-action', methods=['POST'],
                        endpoint='pdfs_bulk_action')
@admin_can('pdfs.edit')
def pdfs_bulk_action():
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    data = request.get_json(silent=True) or {}
    action = (data.get('action') or '').strip()
    raw_ids = data.get('ids') or []

    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({'error': 'No IDs provided'}), 400

    clean_ids = []
    for i in raw_ids:
        try:
            clean_ids.append(int(i))
        except (TypeError, ValueError):
            continue

    if not clean_ids:
        return jsonify({'error': 'No valid IDs'}), 400

    placeholders = ','.join('?' for _ in clean_ids)

    try:
        if action == 'premium_on':
            execute_with_retry(
                f"UPDATE pdfs SET is_premium = 1 WHERE id IN ({placeholders})",
                tuple(clean_ids), commit=True,
            )
            for pid in clean_ids:
                try:
                    log_admin_event(
                        {'id': pid, 'code': None, 'title': None},
                        'premium_on',
                        admin={'id': session.get('user_id')},
                        request=request,
                        source='bulk',
                    )
                except Exception:
                    pass
            write_audit(
                action='pdf.bulk_premium_on',
                target_type='pdf', before=None,
                after={'count': len(clean_ids), 'ids': clean_ids},
                severity='info',
            )
            return jsonify({'success': True, 'affected': len(clean_ids)})

        if action == 'premium_off':
            execute_with_retry(
                f"UPDATE pdfs SET is_premium = 0 WHERE id IN ({placeholders})",
                tuple(clean_ids), commit=True,
            )
            for pid in clean_ids:
                try:
                    log_admin_event(
                        {'id': pid, 'code': None, 'title': None},
                        'premium_off',
                        admin={'id': session.get('user_id')},
                        request=request,
                        source='bulk',
                    )
                except Exception:
                    pass
            write_audit(
                action='pdf.bulk_premium_off',
                target_type='pdf', before=None,
                after={'count': len(clean_ids), 'ids': clean_ids},
                severity='info',
            )
            return jsonify({'success': True, 'affected': len(clean_ids)})

        if action == 'delete':
            if not admin_can('pdfs.delete'):
                return jsonify({'error': 'Delete permission required'}), 403
            deleted = 0
            failed = []
            for pid in clean_ids:
                if delete_main_pdf(pid):
                    deleted += 1
                    try:
                        log_admin_event(
                            {'id': pid, 'code': None, 'title': None},
                            'delete',
                            admin={'id': session.get('user_id')},
                            request=request,
                            source='bulk',
                        )
                    except Exception:
                        pass
                else:
                    failed.append(pid)
            write_audit(
                action='pdf.bulk_delete',
                target_type='pdf', before=None,
                after={'count': deleted, 'ids': clean_ids, 'failed': failed},
                severity='warning',
            )
            return jsonify({'success': True, 'affected': deleted, 'failed': failed})

        return jsonify({'error': f'Unknown action: {action}'}), 400

    except Exception as e:
        logger.exception(f"bulk action '{action}' failed")
        return jsonify({'error': str(e)}), 500


# ============================================================
# PDFs — HISTORY, ACTIVITY, EXPORT
# ============================================================

@admin_content_bp.route('/pdfs/<int:pdf_id>/history', methods=['GET'],
                        endpoint='pdf_history')
@admin_can('pdfs.view')
def pdf_history(pdf_id):
    pdf = get_pdf_by_id(pdf_id)
    if not pdf:
        abort(404)

    type_filter = (request.args.get('type') or '').strip()
    role_filter = (request.args.get('role') or '').strip()
    since = (request.args.get('since') or '').strip()
    until = (request.args.get('until') or '').strip()

    try:
        page = max(1, int(request.args.get('page') or 1))
    except (TypeError, ValueError):
        page = 1

    PER_PAGE = 50
    event_types = [type_filter] if type_filter else None

    events = get_pdf_events(
        pdf_id,
        limit=PER_PAGE,
        offset=(page - 1) * PER_PAGE,
        event_types=event_types,
        actor_role=role_filter or None,
        since=since or None,
        until=until or None,
    )

    total = count_global_events(
        pdf_id=pdf_id,
        event_types=event_types,
        actor_role=role_filter or None,
        since=since or None,
        until=until or None,
    )
    total_pages = (total + PER_PAGE - 1) // PER_PAGE if total > 0 else 1
    summary = get_pdf_event_summary(pdf_id)
    day_groups = group_events_by_day(events)

    return render_template(
        'dashboard/admin/content/pdf_history.html',
        pdf=pdf,
        events=events,
        day_groups=day_groups,
        summary=summary,
        page=page,
        total_pages=total_pages,
        type_filter=type_filter,
        role_filter=role_filter,
        since=since,
        until=until,
    )


@admin_content_bp.route('/pdfs/activity', methods=['GET'],
                        endpoint='pdfs_activity')
@admin_can('pdfs.view')
def pdfs_activity():
    type_filter = (request.args.get('type') or '').strip()
    role_filter = (request.args.get('role') or '').strip()
    pdf_code_filter = (request.args.get('pdf_code') or '').strip().upper()
    since = (request.args.get('since') or '').strip()
    until = (request.args.get('until') or '').strip()

    try:
        page = max(1, int(request.args.get('page') or 1))
    except (TypeError, ValueError):
        page = 1

    PER_PAGE = 100
    event_types = [type_filter] if type_filter else None

    events = get_global_events(
        limit=PER_PAGE,
        offset=(page - 1) * PER_PAGE,
        event_types=event_types,
        actor_role=role_filter or None,
        pdf_code=pdf_code_filter or None,
        since=since or None,
        until=until or None,
    )

    total = count_global_events(
        event_types=event_types,
        actor_role=role_filter or None,
        pdf_code=pdf_code_filter or None,
        since=since or None,
        until=until or None,
    )
    total_pages = (total + PER_PAGE - 1) // PER_PAGE if total > 0 else 1
    today = get_pdf_today_summary()

    return render_template(
        'dashboard/admin/content/pdf_activity.html',
        events=events,
        today=today,
        page=page,
        total_pages=total_pages,
        type_filter=type_filter,
        role_filter=role_filter,
        pdf_code_filter=pdf_code_filter,
        since=since,
        until=until,
    )


@admin_content_bp.route('/pdfs/activity/export', methods=['GET'],
                        endpoint='pdfs_activity_export')
@admin_can('pdfs.view')
def pdfs_activity_export():
    import csv as _csv
    from io import StringIO as _StringIO

    type_filter = (request.args.get('type') or '').strip()
    role_filter = (request.args.get('role') or '').strip()
    pdf_code_filter = (request.args.get('pdf_code') or '').strip().upper()
    since = (request.args.get('since') or '').strip()
    until = (request.args.get('until') or '').strip()

    event_types = [type_filter] if type_filter else None
    events = get_global_events(
        limit=5000, offset=0,
        event_types=event_types,
        actor_role=role_filter or None,
        pdf_code=pdf_code_filter or None,
        since=since or None,
        until=until or None,
    )

    buf = _StringIO()
    writer = _csv.writer(buf)
    writer.writerow([
        'when', 'event_type', 'category', 'actor_name', 'actor_public_id',
        'actor_role', 'pdf_code', 'pdf_title', 'source', 'ip_address',
    ])
    for e in events:
        writer.writerow([
            e.get('created_at', ''),
            e.get('event_type', ''),
            e.get('event_category', ''),
            e.get('actor_name', ''),
            e.get('actor_public_id', ''),
            e.get('actor_role', ''),
            e.get('pdf_code', ''),
            e.get('pdf_title', ''),
            e.get('source', ''),
            e.get('ip_address', ''),
        ])

    resp = Response(buf.getvalue(), mimetype='text/csv; charset=utf-8')
    resp.headers['Content-Disposition'] = (
        'attachment; filename="pdf_activity_'
        + _dt.now().strftime('%Y%m%d_%H%M')
        + '.csv"'
    )
    return resp