# blueprints/admin_errors_bp.py
# ============================================================
# Admin error dashboard.
#
# Uses the modern capability system (@admin_can) — no separate
# password-based login session. Admins reach this through the
# regular admin login. Non-admins can still use /report and
# /reveal-error (both are user-facing support endpoints).
#
# Routes:
#   GET  /admin/errors/              → list  (ops/errors.html)
#   GET  /admin/errors/<id>          → detail (ops/error_detail.html)
#   GET  /admin/errors/by-request/<request_id>  → redirect to detail
#   POST /admin/errors/<id>/resolve   → mark resolved
#   POST /admin/errors/<id>/dismiss   → mark dismissed
#   POST /admin/errors/clear-resolved → purge resolved
#   GET  /admin/errors/stats          → JSON stats
#   GET/POST /admin/errors/report     → user-facing report form
#   POST /admin/errors/reveal-error   → password-protected reveal (500 page)
#   GET  /admin/errors/login          → legacy redirect
# ============================================================

import logging
import secrets
import time

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, jsonify, abort, g,
)

from config import Config
from db import get_db, is_admin
from error_models import (
    get_error_logs,
    get_error_log_count,
    get_error_log_by_id,
    get_error_log_by_request_id,
    resolve_error_log,
    dismiss_error_log,
    clear_resolved_errors,
    get_error_stats,
    clean_old_errors,
)
from utils import validate_csrf
from services.admin.guards import admin_can, silent_deny


logger = logging.getLogger(__name__)

admin_errors_bp = Blueprint('admin_errors', __name__, url_prefix='/admin/errors')


# ============================================================
# HELPERS
# ============================================================

def _require_capability(cap_key: str) -> None:
    """Silently deny (404) if the current user lacks the capability."""
    if not admin_can(cap_key):
        silent_deny(f'missing_capability:{cap_key}')


# ============================================================
# LIST
# ============================================================

@admin_errors_bp.route('/', methods=['GET'])
@admin_errors_bp.route('', methods=['GET'])
def index():
    _require_capability('errors.view')

    severity = (request.args.get('severity') or '').strip()
    resolved_raw = (request.args.get('resolved') or '').strip()
    search = (request.args.get('search') or '').strip()

    try:
        page = max(1, int(request.args.get('page') or 1))
    except (TypeError, ValueError):
        page = 1

    per_page = 50

    resolved_filter = None
    if resolved_raw == '1':
        resolved_filter = 1
    elif resolved_raw == '0':
        resolved_filter = 0

    offset = (page - 1) * per_page

    errors = get_error_logs(
        limit=per_page,
        offset=offset,
        severity=severity or None,
        resolved=resolved_filter,
        search=search or None,
    )

    total = get_error_log_count(
        severity=severity or None,
        resolved=resolved_filter,
        search=search or None,
    )

    stats = get_error_stats()

    # Opportunistic cleanup — never blocks the page.
    try:
        cleaned = clean_old_errors()
        if cleaned > 0:
            logger.info(f"Cleaned {cleaned} old errors")
    except Exception as e:
        logger.debug(f"Error cleanup skipped: {e}")

    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    return render_template(
        'dashboard/admin/ops/errors.html',
        errors=errors,
        stats=stats,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        severity=severity,
        resolved=resolved_raw,
        search=search,
    )


# ============================================================
# DETAIL
# ============================================================

@admin_errors_bp.route('/<int:error_id>', methods=['GET'])
def detail(error_id):
    _require_capability('errors.view')

    error = get_error_log_by_id(error_id)
    if not error:
        flash('Error not found.', 'error')
        return redirect(url_for('admin_errors.index'))

    return render_template(
        'dashboard/admin/ops/error_detail.html',
        error=error,
    )


@admin_errors_bp.route('/by-request/<request_id>', methods=['GET'])
def by_request(request_id):
    _require_capability('errors.view')

    error = get_error_log_by_request_id(request_id)
    if not error:
        flash('Error not found.', 'error')
        return redirect(url_for('admin_errors.index'))

    return redirect(url_for('admin_errors.detail', error_id=error['id']))


# ============================================================
# RESOLVE
# ============================================================

@admin_errors_bp.route('/<int:error_id>/resolve', methods=['POST'])
def resolve(error_id):
    _require_capability('errors.resolve')

    if not validate_csrf():
        abort(403)

    note = (request.form.get('note') or '').strip()

    if resolve_error_log(error_id, note):
        flash('Error marked as resolved.', 'success')
    else:
        flash('Failed to resolve error.', 'error')

    return redirect(url_for('admin_errors.detail', error_id=error_id))


# ============================================================
# DISMISS
# ============================================================

@admin_errors_bp.route('/<int:error_id>/dismiss', methods=['POST'])
def dismiss(error_id):
    _require_capability('errors.dismiss')

    if not validate_csrf():
        abort(403)

    if dismiss_error_log(error_id):
        flash('Error dismissed.', 'info')
    else:
        flash('Failed to dismiss error.', 'error')

    return redirect(url_for('admin_errors.detail', error_id=error_id))


# ============================================================
# CLEAR RESOLVED
# ============================================================

@admin_errors_bp.route('/clear-resolved', methods=['POST'])
def clear_resolved():
    _require_capability('errors.clear')

    if not validate_csrf():
        abort(403)

    count = clear_resolved_errors()
    flash(f'Deleted {count} resolved error(s).', 'success')
    return redirect(url_for('admin_errors.index'))


# ============================================================
# STATS (JSON)
# ============================================================

@admin_errors_bp.route('/stats', methods=['GET'])
def stats():
    _require_capability('errors.view')
    return jsonify(get_error_stats())


# ============================================================
# USER-FACING REPORT  (no capability required)
# ============================================================

@admin_errors_bp.route('/report', methods=['GET', 'POST'])
def report():
    request_id = request.args.get('id') or getattr(g, 'request_id', None)

    if request.method == 'POST':
        description = (request.form.get('description') or '').strip()
        email = (request.form.get('email') or '').strip()
        posted_request_id = (request.form.get('request_id') or '').strip()
        url = (request.form.get('url') or '').strip()

        if not description:
            flash('Please describe what you were doing.', 'error')
            return render_template(
                'report_error.html',
                request_id=posted_request_id or request_id,
            )

        # Try to enrich an existing error log
        error_data = None
        if posted_request_id:
            error_data = get_error_log_by_request_id(posted_request_id)

        if not error_data:
            try:
                from errors import handle_error
                error_data = handle_error(
                    Exception("User Reported Error"),
                    status_code=500,
                    severity='WARNING',
                    user_description=description,
                )
            except Exception as e:
                logger.warning(f"Could not log user report: {e}")

        # Attach the description to the error row if we have one
        if error_data and error_data.get('id'):
            try:
                conn = get_db()
                conn.execute(
                    "UPDATE error_logs SET user_description = ? WHERE id = ?",
                    (description, error_data['id']),
                )
                conn.commit()
            except Exception as e:
                logger.warning(f"Failed to attach user description: {e}")

        # Best-effort email
        try:
            from errors import send_error_email
            send_error_email({
                'request_id': posted_request_id or 'no-req',
                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'severity': 'WARNING',
                'status_code': 500,
                'url': url or 'N/A',
                'method': 'REPORT',
                'user_id': session.get('user_id'),
                'ip_address': request.remote_addr,
                'error_type': 'UserReportedError',
                'error_message': description[:1000],
                'stack_trace': f'User reported: {description[:500]}\n\n'
                               f'Email: {email or "Not provided"}',
                'user_description': description,
                'occurrence_count': 1,
                'error_hash': 'user_report_' + str(int(time.time())),
            })
        except Exception as e:
            logger.debug(f"Email send skipped: {e}")

        flash('Thank you for your report. We will look into this issue.',
              'success')

        # Send the user back where they came from, or to the home page.
        referrer = request.referrer
        if referrer and referrer.startswith(request.host_url):
            return redirect(referrer)
        return redirect('/')

    # GET → render the standalone report page
    return render_template(
        'report_error.html',
        request_id=request_id,
    )


# ============================================================
# REVEAL ERROR DETAILS (500 page password trick)
# ============================================================

@admin_errors_bp.route('/reveal-error', methods=['POST'])
def reveal_error():
    """
    Reveal error details to non-admins after password verification.
    Admins may bypass the password with admin_bypass=1.
    """
    request_id = request.form.get('request_id')
    if not request_id:
        return jsonify({'error': 'Missing error ID'}), 400

    # Password may arrive under either name; the 500 page
    # sends `error-secret`, older callers sent `password`.
    password = (
        request.form.get('password')
        or request.form.get('error-secret')
        or ''
    )

    admin_bypass = request.form.get('admin_bypass') == '1'

    if admin_bypass and is_admin(session.get('user_id')):
        pass  # bypass granted
    elif not secrets.compare_digest(password, Config.ADMIN_ERROR_PASSWORD):
        return jsonify({'error': 'Invalid password'}), 403

    error = get_error_log_by_request_id(request_id)
    if not error:
        return jsonify({'error': 'Error not found'}), 404

    return jsonify({
        'error_type': error.get('error_type'),
        'error_message': error.get('error_message'),
        'stack_trace': error.get('stack_trace'),
        'url': error.get('url'),
        'method': error.get('method'),
        'user_id': error.get('user_id'),
        'timestamp': error.get('timestamp'),
        'request_id': error.get('request_id'),
    })


# ============================================================
# LEGACY LOGIN STUB
# ============================================================
# The old error dashboard had its own password-based session.
# Any bookmark or link to /admin/errors/login is now redirected
# to the normal list page (which uses the standard admin login).

@admin_errors_bp.route('/login', methods=['GET'])
def login():
    return redirect(url_for('admin_errors.index'))


@admin_errors_bp.route('/logout', methods=['GET'])
def logout():
    return redirect(url_for('admin_errors.index'))