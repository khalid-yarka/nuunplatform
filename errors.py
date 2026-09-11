# ============================================
# CENTRAL ERROR HANDLING SYSTEM
# ============================================

import traceback
import sys
import time
import hashlib
import logging
from datetime import datetime
from typing import Dict, Optional, Any
from flask import request, session, g, jsonify, render_template, current_app
from functools import wraps
from config import Config
from error_models import store_error_log, get_error_log_by_request_id, get_error_stats

logger = logging.getLogger(__name__)

# ============================================
# ERROR SEVERITY LEVELS
# ============================================

SEVERITY_CRITICAL = 'CRITICAL'
SEVERITY_ERROR = 'ERROR'
SEVERITY_WARNING = 'WARNING'

# ============================================
# EMAIL REPORTING
# ============================================

_email_cache = {}
_last_email_cleanup = time.time()


def should_send_email(error_hash: str, severity: str) -> bool:
    global _email_cache, _last_email_cleanup
    if time.time() - _last_email_cleanup > 300:
        _email_cache = {}
        _last_email_cleanup = time.time()
    if severity == 'CRITICAL':
        window = 300
    elif severity == 'ERROR':
        window = 900
    else:
        window = 3600
    if error_hash in _email_cache:
        last_sent = _email_cache[error_hash]
        if time.time() - last_sent < window:
            return False
    _email_cache[error_hash] = time.time()
    return True


def send_error_email(error_data: Dict) -> bool:
    if not Config.EMAIL_ENABLED:
        return False
    error_hash = error_data.get('error_hash', '')
    severity = error_data.get('severity', 'ERROR')
    if not should_send_email(error_hash, severity):
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        subject = f"[NUUNQALIN] {severity} - {error_data.get('error_type', 'Unknown Error')}"
        body = f"""
========================================
NUUNQALIN ERROR REPORT
========================================

Request ID: {error_data.get('request_id', 'N/A')}
Time: {error_data.get('timestamp', datetime.now().isoformat())}
URL: {error_data.get('url', 'N/A')}
Method: {error_data.get('method', 'N/A')}
User ID: {error_data.get('user_id', 'N/A')}
IP: {error_data.get('ip_address', 'N/A')}

Error Type: {error_data.get('error_type', 'Unknown')}
Error Message: {error_data.get('error_message', 'No message')}

Occurrences: {error_data.get('occurrence_count', 1)}

User Description:
{error_data.get('user_description', 'None provided')}

Stack Trace:
{error_data.get('stack_trace', 'No trace available')}

========================================
"""
        msg = MIMEMultipart()
        msg['From'] = Config.SMTP_FROM
        msg['To'] = Config.SMTP_TO
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        smtp = smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=10)
        smtp.starttls()
        smtp.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
        smtp.send_message(msg)
        smtp.quit()
        logger.info(f"Error email sent: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send error email: {e}")
        return False


def handle_error(
    error: Exception,
    status_code: int = 500,
    severity: str = SEVERITY_ERROR,
    user_description: str = None
):
    request_id = getattr(g, 'request_id', 'no-req')
    error_type = type(error).__name__
    error_message = str(error)
    stack_trace = traceback.format_exc()
    user_id = session.get('user_id')
    url = request.url if request else 'N/A'
    method = request.method if request else 'N/A'
    ip = request.remote_addr if request else 'N/A'

    error_data = {
        'request_id': request_id,
        'timestamp': datetime.now().isoformat(),
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
        'occurrence_count': 1
    }

    import hashlib
    key = f"{error_type}|{error_message[:100]}|{url}|{method}"
    error_data['error_hash'] = hashlib.sha256(key.encode()).hexdigest()[:32]

    log_level = logging.CRITICAL if severity == SEVERITY_CRITICAL else logging.ERROR
    logger.log(
        log_level,
        f"[{request_id}] {severity}: {error_type} - {error_message}\n"
        f"URL: {url}\nMethod: {method}\nUser: {user_id}\n"
        f"Trace: {stack_trace}"
    )

    try:
        error_id = store_error_log(error_data)
        if error_id:
            error_data['id'] = error_id
    except Exception as e:
        logger.error(f"Failed to store error log: {e}")

    if severity == SEVERITY_CRITICAL or (status_code == 500 and severity == SEVERITY_ERROR):
        try:
            send_error_email(error_data)
        except Exception as e:
            logger.error(f"Failed to send error email: {e}")

    return error_data


def register_error_handlers(app):
    @app.errorhandler(400)
    def bad_request(e):
        handle_error(e, 400, SEVERITY_WARNING)
        if request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json':
            return jsonify({'error': 'Bad Request', 'request_id': getattr(g, 'request_id', 'no-req')}), 400
        return render_template('400.html', request_id=getattr(g, 'request_id', 'no-req')), 400

    @app.errorhandler(401)
    def unauthorized(e):
        handle_error(e, 401, SEVERITY_WARNING)
        if request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json':
            return jsonify({'error': 'Unauthorized', 'request_id': getattr(g, 'request_id', 'no-req')}), 401
        return render_template('401.html', request_id=getattr(g, 'request_id', 'no-req')), 401

    @app.errorhandler(403)
    def forbidden(e):
        handle_error(e, 403, SEVERITY_WARNING)
        if request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json':
            return jsonify({'error': 'Forbidden', 'request_id': getattr(g, 'request_id', 'no-req')}), 403
        return render_template('403.html', request_id=getattr(g, 'request_id', 'no-req')), 403

    @app.errorhandler(404)
    def not_found(e):
        handle_error(e, 404, SEVERITY_WARNING)
        if request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json':
            return jsonify({'error': 'Not Found', 'request_id': getattr(g, 'request_id', 'no-req')}), 404
        return render_template('404.html', request_id=getattr(g, 'request_id', 'no-req')), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        handle_error(e, 405, SEVERITY_WARNING)
        if request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json':
            return jsonify({'error': 'Method Not Allowed', 'request_id': getattr(g, 'request_id', 'no-req')}), 405
        return render_template('405.html', request_id=getattr(g, 'request_id', 'no-req')), 405

    @app.errorhandler(413)
    def too_large(e):
        handle_error(e, 413, SEVERITY_WARNING)
        return jsonify({'error': 'Request Entity Too Large', 'request_id': getattr(g, 'request_id', 'no-req')}), 413

    @app.errorhandler(429)
    def rate_limited(e):
        handle_error(e, 429, SEVERITY_WARNING)
        if request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json':
            return jsonify({'error': 'Rate Limited', 'request_id': getattr(g, 'request_id', 'no-req')}), 429
        return render_template('429.html', request_id=getattr(g, 'request_id', 'no-req')), 429

    @app.errorhandler(500)
    def internal_server_error(e):
        severity = SEVERITY_CRITICAL if 'database' in str(e).lower() or 'sqlite' in str(e).lower() else SEVERITY_ERROR
        error_data = handle_error(e, 500, severity)
        is_admin = session.get('is_admin', False)
        request_id = getattr(g, 'request_id', 'no-req')

        if request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json':
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
                user_id=session.get('user_id')
            ), 500

        # User‑friendly page with hidden password trick
        return render_template(
            '500_user.html',
            request_id=request_id,
            is_admin=is_admin  # pass to template
        ), 500

    @app.errorhandler(Exception)
    def unhandled_exception(e):
        error_data = handle_error(e, 500, SEVERITY_CRITICAL)
        is_admin = session.get('is_admin', False)
        request_id = getattr(g, 'request_id', 'no-req')

        if request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json':
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
                user_id=session.get('user_id')
            ), 500

        return render_template(
            '500_user.html',
            request_id=request_id,
            is_admin=is_admin
        ), 500


def catch_errors(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            raise
    return decorated