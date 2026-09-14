# ============================================
# CENTRAL ERROR HANDLING SYSTEM
# ============================================
# Every unhandled error and every user-submitted bug report is
# dispatched to super admins on Telegram as:
#   1. A short HTML message (parse_mode='HTML')
#   2. A full markdown .md report attached as a document
#
# SMTP is no longer used for error notification.
# Somali time format is used everywhere a timestamp is shown to a
# human. ISO timestamps appear only inside the markdown frontmatter.
# ============================================

import traceback
import sys
import time
import hashlib
import logging
from datetime import datetime
from typing import Dict, Optional, Any

from flask import request, session, g, jsonify, render_template
from functools import wraps

from config import Config
from error_models import (
    store_error_log,
    get_error_log_by_request_id,
    get_error_stats,
)
from utils import get_somali_time

logger = logging.getLogger(__name__)


# ============================================
# SEVERITY LEVELS
# ============================================

SEVERITY_CRITICAL = 'CRITICAL'
SEVERITY_ERROR = 'ERROR'
SEVERITY_WARNING = 'WARNING'


# ============================================
# DEDUP CACHE (avoid spamming Telegram)
# ============================================

_dispatch_cache: Dict[str, float] = {}
_last_cache_cleanup = time.time()


def _should_dispatch(error_hash: str, severity: str) -> bool:
    """Return True if this error should trigger a Telegram dispatch."""
    global _dispatch_cache, _last_cache_cleanup

    now = time.time()
    if now - _last_cache_cleanup > 300:
        _dispatch_cache = {}
        _last_cache_cleanup = now

    if severity == SEVERITY_CRITICAL:
        window = 300        # 5 minutes
    elif severity == SEVERITY_ERROR:
        window = 900        # 15 minutes
    else:
        window = 3600       # 1 hour

    last = _dispatch_cache.get(error_hash)
    if last is not None and (now - last) < window:
        return False
    _dispatch_cache[error_hash] = now
    return True


# ============================================
# TELEGRAM REPORTING
# ============================================

def _severity_to_token(severity: str) -> str:
    s = (severity or 'ERROR').upper()
    if s == SEVERITY_CRITICAL:
        return 'critical'
    if s == SEVERITY_WARNING:
        return 'warning'
    return 'error'


def _build_error_markdown(error_data: Dict) -> str:
    """
    Build the full markdown report for one error.
    Uses services.telegram_notify.build_markdown_document.
    """
    from services.telegram_notify import (
        build_markdown_document,
        somali_now_long,
    )

    severity_token = _severity_to_token(error_data.get('severity'))
    error_type = error_data.get('error_type') or 'Unknown'
    request_id = error_data.get('request_id') or 'no-req'

    # ----- frontmatter -----
    meta = {
        'type': 'error-report',
        'severity': severity_token,
        'request_id': request_id,
        'status_code': error_data.get('status_code'),
        'url': error_data.get('url'),
        'method': error_data.get('method'),
        'user_id': error_data.get('user_id'),
        'ip_address': error_data.get('ip_address'),
        'occurrence_count': error_data.get('occurrence_count', 1),
        'error_type': error_type,
        'error_hash': error_data.get('error_hash'),
    }

    # ----- sections -----
    sections = []

    # Incident summary table
    summary_table = '\n'.join([
        '| Field | Value |',
        '|:--|:--|',
        f'| Severity | `{(error_data.get("severity") or "ERROR")}` |',
        f'| Status Code | `{error_data.get("status_code") or "-"}` |',
        f'| Error Type | `{error_type}` |',
        f'| Occurrences | `{error_data.get("occurrence_count") or 1}` |',
        f'| Request ID | `{request_id}` |',
    ])
    sections.append(('🎯 Incident Summary', summary_table))

    # Reporter / request
    ua = ''
    try:
        ua = (request.headers.get('User-Agent') or '') if request else ''
    except Exception:
        ua = ''

    reporter_rows = [
        '| Field | Value |',
        '|:--|:--|',
        f'| User ID | `{error_data.get("user_id") or "anonymous"}` |',
        f'| IP Address | `{error_data.get("ip_address") or "-"}` |',
        f'| User Agent | `{ua[:180] if ua else "-"}` |',
    ]
    sections.append(('👤 Reporter', '\n'.join(reporter_rows)))

    # Request
    method = error_data.get('method') or 'GET'
    url = error_data.get('url') or '-'
    base_url = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')
    error_id = error_data.get('id')
    admin_link = f"{base_url}/admin/errors/{error_id}" if (base_url and error_id) else None

    req_lines = [
        '```http',
        f'{method} {url}',
        '```',
    ]
    if admin_link:
        req_lines.append('')
        req_lines.append(f'🔗 **[Open in admin panel →]({admin_link})**')
    sections.append(('🌐 Request', '\n'.join(req_lines)))

    # Error message
    msg = error_data.get('error_message') or 'No message provided.'
    sections.append(('💬 Error Message', f'```text\n{msg}\n```'))

    # Stack trace (collapsible)
    trace = error_data.get('stack_trace') or ''
    if trace:
        trace_lines = trace.count('\n') + 1
        stack_block = (
            f'<details>\n'
            f'<summary>▶ Click to expand ({trace_lines} lines)</summary>\n\n'
            f'```python\n{trace}\n```\n\n'
            f'</details>'
        )
        sections.append(('📚 Stack Trace', stack_block))

    # User description (from 500 page report form)
    description = (error_data.get('user_description') or '').strip()
    if description:
        sections.append(('💭 User Description', f'> {description}'))

    # Suggested actions
    actions = [
        f'- [ ] Inspect the handler for `{error_data.get("url") or "-"}`',
        f'- [ ] Reproduce the error locally (request id `{request_id}`)',
        f'- [ ] Verify the fix and re-check `/admin/errors/{error_id or ""}`',
    ]
    sections.append(('🛠️ Suggested Actions', '\n'.join(actions)))

    return build_markdown_document(
        title=f'{error_data.get("severity") or "ERROR"} — {error_type}',
        severity=severity_token,
        meta=meta,
        sections=sections,
        footer_id=request_id,
    )


def send_error_telegram(error_data: Dict) -> bool:
    """
    Dispatch one error to super admins on Telegram.

    Returns True if at least one recipient received the message.
    """
    if not _should_dispatch(
        error_data.get('error_hash') or error_data.get('request_id') or 'unknown',
        error_data.get('severity') or SEVERITY_ERROR,
    ):
        logger.debug("send_error_telegram: suppressed by dedup window")
        return False

    try:
        from services.telegram_notify import (
            notify_super_admins,
            make_report_filename,
            summary_row,
            truncate,
        )
    except Exception as e:
        logger.error(f"send_error_telegram: could not import telegram_notify: {e}")
        return False

    try:
        md_body = _build_error_markdown(error_data)
    except Exception as e:
        logger.error(f"send_error_telegram: markdown build failed: {e}", exc_info=True)
        return False

    severity_token = _severity_to_token(error_data.get('severity'))
    request_id = error_data.get('request_id') or 'no-req'
    filename = make_report_filename('error', request_id)

    base_url = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')
    error_id = error_data.get('id')
    admin_url = f"{base_url}/admin/errors/{error_id}" if (base_url and error_id) else None

    method = error_data.get('method') or 'GET'
    url_path = (error_data.get('url') or '')
    try:
        from urllib.parse import urlparse
        path = urlparse(url_path).path or url_path
    except Exception:
        path = url_path

    summary = [
        summary_row('📍', 'Where', f'{method} {truncate(path, 80)}'),
        summary_row('⚠️', 'Type', error_data.get('error_type') or 'Unknown'),
        summary_row('💬', 'Message', truncate(error_data.get('error_message') or '-', 140)),
    ]
    if error_data.get('user_id'):
        summary.append(summary_row('👤', 'User ID', error_data.get('user_id')))

    try:
        result = notify_super_admins(
            event_type='error',
            title=f'{error_data.get("severity") or "ERROR"} — {error_data.get("error_type") or "Unknown"}',
            md_body=md_body,
            md_filename=filename,
            summary=summary,
            primary_url=admin_url,
            primary_url_label='Open error in admin panel',
            severity=severity_token,
            reference_id=request_id,
        )
        return result.get('sent', 0) > 0
    except Exception as e:
        logger.error(f"send_error_telegram: dispatch failed: {e}", exc_info=True)
        return False


# ============================================
# CORE ERROR HANDLER
# ============================================

def handle_error(
    error: Exception,
    status_code: int = 500,
    severity: str = SEVERITY_ERROR,
    user_description: Optional[str] = None,
):
    """
    Capture an error: log it, store it in the DB, and dispatch it
    to super admins on Telegram. Never raises.
    """
    request_id = getattr(g, 'request_id', 'no-req')
    error_type = type(error).__name__
    error_message = str(error)
    stack_trace = traceback.format_exc()
    user_id = session.get('user_id') if session else None
    url = request.url if request else 'N/A'
    method = request.method if request else 'N/A'
    ip = request.remote_addr if request else 'N/A'

    error_data = {
        'request_id': request_id,
        'timestamp': get_somali_time().isoformat(),
        'severity': severity,
        'status_code': status_code,
        'url': url,
        'method': method,
        'user_id': user_id,
        'ip_address': ip,
        'error_type': error_type,
        'error_message': error_message[:1000],
        'stack_trace': stack_trace[:5000],
        'user_description': user_description or '',
        'occurrence_count': 1,
    }

    key = f"{error_type}|{error_message[:100]}|{url}|{method}"
    error_data['error_hash'] = hashlib.sha256(key.encode()).hexdigest()[:32]

    log_level = logging.CRITICAL if severity == SEVERITY_CRITICAL else logging.ERROR
    logger.log(
        log_level,
        f"[{request_id}] {severity}: {error_type} - {error_message}\n"
        f"URL: {url}\nMethod: {method}\nUser: {user_id}\n"
        f"Trace: {stack_trace}",
    )

    try:
        error_id = store_error_log(error_data)
        if error_id:
            error_data['id'] = error_id
    except Exception as e:
        logger.error(f"Failed to store error log: {e}")

    # Dispatch to Telegram
    try:
        send_error_telegram(error_data)
    except Exception as e:
        logger.error(f"send_error_telegram raised: {e}", exc_info=True)

    return error_data


# ============================================
# FLASK ERROR REGISTRATION
# ============================================

def _wants_json() -> bool:
    try:
        if request.path.startswith('/api/'):
            return True
        if request.accept_mimetypes.best == 'application/json':
            return True
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return True
    except Exception:
        pass
    return False


def register_error_handlers(app):

    @app.errorhandler(400)
    def bad_request(e):
        handle_error(e, 400, SEVERITY_WARNING)
        if _wants_json():
            return jsonify({'error': 'Bad Request', 'request_id': getattr(g, 'request_id', 'no-req')}), 400
        return render_template('404.html', request_id=getattr(g, 'request_id', 'no-req')), 400

    @app.errorhandler(401)
    def unauthorized(e):
        handle_error(e, 401, SEVERITY_WARNING)
        if _wants_json():
            return jsonify({'error': 'Unauthorized', 'request_id': getattr(g, 'request_id', 'no-req')}), 401
        return render_template('404.html', request_id=getattr(g, 'request_id', 'no-req')), 401

    @app.errorhandler(403)
    def forbidden(e):
        handle_error(e, 403, SEVERITY_WARNING)
        if _wants_json():
            return jsonify({'error': 'Forbidden', 'request_id': getattr(g, 'request_id', 'no-req')}), 403
        return render_template('403.html', request_id=getattr(g, 'request_id', 'no-req')), 403

    @app.errorhandler(404)
    def not_found(e):
        handle_error(e, 404, SEVERITY_WARNING)
        if _wants_json():
            return jsonify({'error': 'Not Found', 'request_id': getattr(g, 'request_id', 'no-req')}), 404
        return render_template('404.html', request_id=getattr(g, 'request_id', 'no-req')), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        handle_error(e, 405, SEVERITY_WARNING)
        if _wants_json():
            return jsonify({'error': 'Method Not Allowed', 'request_id': getattr(g, 'request_id', 'no-req')}), 405
        return render_template('404.html', request_id=getattr(g, 'request_id', 'no-req')), 405

    @app.errorhandler(413)
    def too_large(e):
        handle_error(e, 413, SEVERITY_WARNING)
        return jsonify({'error': 'Request Entity Too Large', 'request_id': getattr(g, 'request_id', 'no-req')}), 413

    @app.errorhandler(429)
    def rate_limited(e):
        handle_error(e, 429, SEVERITY_WARNING)
        if _wants_json():
            return jsonify({'error': 'Rate Limited', 'request_id': getattr(g, 'request_id', 'no-req')}), 429
        return render_template('404.html', request_id=getattr(g, 'request_id', 'no-req')), 429

    @app.errorhandler(500)
    def internal_server_error(e):
        msg_lower = str(e).lower()
        severity = (
            SEVERITY_CRITICAL
            if ('database' in msg_lower or 'sqlite' in msg_lower)
            else SEVERITY_ERROR
        )
        error_data = handle_error(e, 500, severity)
        is_admin = session.get('is_admin', False) if session else False
        request_id = getattr(g, 'request_id', 'no-req')

        if _wants_json():
            return jsonify({'error': 'Internal Server Error', 'request_id': request_id}), 500

        if is_admin and request.args.get('debug') == 'true':
            return render_template(
                '500_admin.html',
                request_id=request_id,
                error_type=error_data.get('error_type', 'Unknown'),
                error_message=error_data.get('error_message', ''),
                stack_trace=error_data.get('stack_trace', ''),
                url=request.url,
                method=request.method,
                user_id=session.get('user_id'),
            ), 500

        return render_template(
            '500_user.html',
            request_id=request_id,
            is_admin=is_admin,
        ), 500

    @app.errorhandler(Exception)
    def unhandled_exception(e):
        # Let HTTPException bubble up as its own code
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e

        error_data = handle_error(e, 500, SEVERITY_CRITICAL)
        is_admin = session.get('is_admin', False) if session else False
        request_id = getattr(g, 'request_id', 'no-req')

        if _wants_json():
            return jsonify({'error': 'Internal Server Error', 'request_id': request_id}), 500

        if is_admin and request.args.get('debug') == 'true':
            return render_template(
                '500_admin.html',
                request_id=request_id,
                error_type=error_data.get('error_type', 'Unknown'),
                error_message=error_data.get('error_message', ''),
                stack_trace=error_data.get('stack_trace', ''),
                url=request.url,
                method=request.method,
                user_id=session.get('user_id'),
            ), 500

        return render_template(
            '500_user.html',
            request_id=request_id,
            is_admin=is_admin,
        ), 500


# ============================================
# DECORATOR
# ============================================

def catch_errors(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated