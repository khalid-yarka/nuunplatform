# blueprints/upgrade_bp.py
# Upgrade request system – API + Admin

import csv
import io
import secrets
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Blueprint, render_template, request, session, jsonify, flash,
    redirect, url_for, abort, Response,
)

from db import get_student_by_id, is_admin, execute_with_retry, get_somali_time_db
from services.tier_service import set_user_tier
from utils import validate_csrf, get_somali_time, SOMALI_TIMEZONE
from config import Config

logger = logging.getLogger(__name__)

upgrade_bp = Blueprint('upgrade', __name__, url_prefix='/upgrade')


# ============================================
# DECORATORS
# ============================================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Please login first.'}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Admin-only guard.
    - Not logged in → redirect to /login?next=<original URL>.
    - Logged in but not admin → 403.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login', next=request.url))
        if not is_admin(session['user_id']):
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ============================================
# PRICING DATA
# ============================================

PRICES = {
    'premium': {'monthly': 1.25, 'term': 3.00, 'yearly': 5.00},
    'pro':     {'monthly': 2.00, 'term': 4.50, 'yearly': 7.00},
}

# Accepted tier values for upgrade requests
UPGRADE_TIERS = ('premium', 'pro')
UPGRADE_DURATIONS = ('monthly', 'term', 'yearly')


# ============================================
# HELPERS
# ============================================

def _parse_db_datetime(dt_str):
    """Parse a datetime string from DB; attach Somali TZ if naive."""
    if not dt_str:
        return None
    try:
        normalized = dt_str.replace(' ', 'T')
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SOMALI_TIMEZONE)
        return dt
    except (ValueError, TypeError):
        return None


def generate_request_id():
    """Generate request ID: UR-YYYYMMDD-NNN using Somali time."""
    today = get_somali_time().strftime('%Y%m%d')
    prefix = f'UR-{today}-'
    cursor = execute_with_retry(
        "SELECT MAX(CAST(SUBSTR(request_id, -3) AS INTEGER)) AS max_num "
        "FROM upgrade_requests WHERE request_id LIKE ?",
        (prefix + '%',),
    )
    row = cursor.fetchone()
    next_num = (row['max_num'] or 0) + 1 if row else 1
    return f"UR-{today}-{next_num:03d}"


def calculate_expiry(duration):
    now = get_somali_time()
    if duration == 'monthly':
        return (now + timedelta(days=30)).isoformat()
    elif duration == 'term':
        return (now + timedelta(days=120)).isoformat()
    elif duration == 'yearly':
        return (now + timedelta(days=365)).isoformat()
    return now.isoformat()


def _discount_message(discount_type, discount_value):
    if discount_type == 'percentage':
        return f"{discount_value}% OFF applied!"
    return f"${discount_value / 100:.2f} OFF applied!"


def _get_pending_count():
    try:
        cursor = execute_with_retry(
            "SELECT COUNT(*) AS cnt FROM upgrade_requests WHERE status = 'pending'"
        )
        row = cursor.fetchone()
        return row['cnt'] if row else 0
    except Exception:
        return 0


def _get_request_stats():
    try:
        cursor = execute_with_retry("""
            SELECT
                COUNT(CASE WHEN status = 'pending'  THEN 1 END) AS pending,
                COUNT(CASE WHEN status = 'approved' THEN 1 END) AS approved,
                COUNT(CASE WHEN status = 'rejected' THEN 1 END) AS rejected,
                COUNT(CASE WHEN status = 'cancelled' THEN 1 END) AS cancelled,
                COALESCE(SUM(CASE WHEN status = 'approved' THEN final_price_cents ELSE 0 END), 0) AS revenue_cents,
                COUNT(CASE WHEN created_at >= datetime('now', '-7 days') THEN 1 END) AS this_week
            FROM upgrade_requests
        """)
        row = cursor.fetchone()
        if not row:
            return {
                'pending': 0, 'approved': 0, 'rejected': 0,
                'cancelled': 0, 'revenue_cents': 0, 'this_week': 0,
                'revenue_dollars': 0.0,
            }
        data = dict(row)
        data['revenue_dollars'] = round((data.get('revenue_cents') or 0) / 100, 2)
        return data
    except Exception:
        return {
            'pending': 0, 'approved': 0, 'rejected': 0,
            'cancelled': 0, 'revenue_cents': 0, 'this_week': 0,
            'revenue_dollars': 0.0,
        }


# ============================================
# ROOT REDIRECTS
# ============================================

@upgrade_bp.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('auth.login', next=request.url))
    if is_admin(session['user_id']):
        return redirect(url_for('upgrade.admin_list'))
    return redirect(url_for('dashboard.home'))


@upgrade_bp.route('/admin/')
@admin_required
def admin_root():
    return redirect(url_for('upgrade.admin_list'))


# ============================================
# API: Validate Discount Code
# ============================================

@upgrade_bp.route('/api/validate-discount', methods=['POST'])
@login_required
def validate_discount():
    data = request.get_json() or {}
    code = (data.get('code') or '').strip().upper()
    tier = data.get('tier')
    duration = data.get('duration')

    if not code or not tier or not duration:
        return jsonify({'valid': False, 'message': 'Missing parameters'}), 400

    cursor = execute_with_retry(
        "SELECT * FROM discount_codes WHERE code = ? AND is_active = 1",
        (code,),
    )
    row = cursor.fetchone()
    if not row:
        return jsonify({'valid': False, 'message': 'Invalid discount code'})

    if row['expires_at']:
        expiry = _parse_db_datetime(row['expires_at'])
        if expiry is not None and expiry < get_somali_time():
            return jsonify({'valid': False, 'message': 'Discount code expired'})

    if row['max_uses'] is not None and row['used_count'] >= row['max_uses']:
        return jsonify({'valid': False, 'message': 'Discount code limit reached'})

    applies = row['applies_to']
    if applies != 'all' and applies != tier:
        return jsonify({'valid': False, 'message': f'This code does not apply to {tier}'})

    if tier not in PRICES or duration not in PRICES[tier]:
        return jsonify({'valid': False, 'message': 'Invalid tier or duration'})

    original = PRICES[tier][duration]
    if row['discount_type'] == 'percentage':
        discount_amount = original * (row['discount_value'] / 100)
    else:
        discount_amount = row['discount_value'] / 100
    final_price = max(0, original - discount_amount)

    return jsonify({
        'valid': True,
        'discount_amount': round(discount_amount, 2),
        'final_price': round(final_price, 2),
        'code_id': row['id'],
        'message': _discount_message(row['discount_type'], row['discount_value']),
    })


# ============================================
# API: Submit Upgrade Request
# ============================================

@upgrade_bp.route('/api/request', methods=['POST'])
@login_required
def submit_request():
    if not validate_csrf():
        return jsonify({'success': False, 'message': 'CSRF validation failed'}), 403

    data = request.get_json() or {}
    tier = data.get('tier')
    duration = data.get('duration')
    discount_code = (data.get('discount_code') or '').strip().upper() or None
    note = (data.get('note') or '').strip()

    if tier not in UPGRADE_TIERS or duration not in UPGRADE_DURATIONS:
        return jsonify({'success': False, 'message': 'Invalid tier or duration'}), 400

    user_id = session['user_id']
    original_price = PRICES[tier][duration]

    discount_id = None
    discount_amount = 0
    final_price = original_price

    if discount_code:
        cursor = execute_with_retry(
            "SELECT * FROM discount_codes WHERE code = ? AND is_active = 1",
            (discount_code,),
        )
        row = cursor.fetchone()
        if row:
            expiry_ok = True
            if row['expires_at']:
                expiry = _parse_db_datetime(row['expires_at'])
                if expiry is not None and expiry < get_somali_time():
                    expiry_ok = False
            uses_ok = row['max_uses'] is None or row['used_count'] < row['max_uses']
            applies = row['applies_to']
            applies_ok = (applies == 'all' or applies == tier)

            if expiry_ok and uses_ok and applies_ok:
                discount_id = row['id']
                if row['discount_type'] == 'percentage':
                    discount_amount = original_price * (row['discount_value'] / 100)
                else:
                    discount_amount = row['discount_value'] / 100
                final_price = max(0, original_price - discount_amount)

    max_attempts = 5
    request_id = None
    last_error = None

    for attempt in range(max_attempts):
        request_id = generate_request_id()
        try:
            execute_with_retry("""
                INSERT INTO upgrade_requests (
                    request_id, user_id, requested_tier, duration,
                    original_price_cents, discount_code_id, discount_amount_cents,
                    final_price_cents, user_note, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                request_id,
                user_id,
                tier,
                duration,
                int(original_price * 100),
                discount_id,
                int(discount_amount * 100),
                int(final_price * 100),
                note,
                'pending',
                get_somali_time_db(),
            ), commit=True)
            return jsonify({'success': True, 'request_id': request_id})

        except sqlite3.IntegrityError as e:
            last_error = e
            msg = str(e).lower()
            if 'unique' in msg and 'request_id' in msg and attempt < max_attempts - 1:
                time.sleep(0.05 * (attempt + 1))
                continue
            logger.error(f"Upgrade request integrity error: {e}", exc_info=True)
            return jsonify({'success': False, 'message': 'Could not save request. Please try again.'}), 500

        except Exception as e:
            last_error = e
            logger.error(f"Upgrade request error: {e}", exc_info=True)
            return jsonify({'success': False, 'message': 'Internal error. Please try again.'}), 500

    logger.error(f"Upgrade request failed after {max_attempts} attempts: {last_error}", exc_info=True)
    return jsonify({'success': False, 'message': 'Could not generate a unique request ID. Please try again.'}), 500


# ============================================
# ADMIN: List Requests
# ============================================

@upgrade_bp.route('/admin/upgrade-requests')
@admin_required
def admin_list():
    status_filter = request.args.get('status', '')
    search = request.args.get('search', '').strip()
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = 20
    offset = (page - 1) * per_page

    query = """
        SELECT r.*, s.first_name, s.last_name, s.public_id, s.phone_number,
               s.tier AS current_tier
        FROM upgrade_requests r
        LEFT JOIN students s ON r.user_id = s.id
        WHERE 1=1
    """
    params = []
    if status_filter:
        query += " AND r.status = ?"
        params.append(status_filter)
    if search:
        query += " AND (r.request_id LIKE ? OR s.first_name LIKE ? OR s.last_name LIKE ? OR s.phone_number LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like, like])

    query += " ORDER BY r.created_at DESC LIMIT ? OFFSET ?"
    params.extend([per_page, offset])

    cursor = execute_with_retry(query, params)
    requests = [dict(row) for row in cursor.fetchall()]

    count_query = """
        SELECT COUNT(*) AS total
        FROM upgrade_requests r
        LEFT JOIN students s ON r.user_id = s.id
        WHERE 1=1
    """
    count_params = []
    if status_filter:
        count_query += " AND r.status = ?"
        count_params.append(status_filter)
    if search:
        count_query += " AND (r.request_id LIKE ? OR s.first_name LIKE ? OR s.last_name LIKE ? OR s.phone_number LIKE ?)"
        like = f"%{search}%"
        count_params.extend([like, like, like, like])

    cursor = execute_with_retry(count_query, count_params)
    total = cursor.fetchone()['total']
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    stats = _get_request_stats()

    return render_template(
        'dashboard/admin/upgrade_requests_list.html',
        requests=requests,
        status_filter=status_filter,
        search=search,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        total=total,
        stats=stats,
    )


# ============================================
# ADMIN: Request Detail
# ============================================

@upgrade_bp.route('/admin/upgrade-requests/<request_id>')
@admin_required
def admin_detail(request_id):
    cursor = execute_with_retry("""
        SELECT r.*, s.first_name, s.last_name, s.public_id, s.phone_number,
               s.tier AS current_tier, s.total_points
        FROM upgrade_requests r
        LEFT JOIN students s ON r.user_id = s.id
        WHERE r.request_id = ?
    """, (request_id,))
    row = cursor.fetchone()
    if not row:
        abort(404)

    request_data = dict(row)
    return render_template('dashboard/admin/upgrade_request_detail.html', upgrade_request=request_data)


# ============================================
# ADMIN: Approve Request
# ============================================

@upgrade_bp.route('/admin/upgrade-requests/<request_id>/approve', methods=['POST'])
@admin_required
def admin_approve(request_id):
    if not validate_csrf():
        abort(403)

    cursor = execute_with_retry(
        "SELECT * FROM upgrade_requests WHERE request_id = ? AND status = 'pending'",
        (request_id,),
    )
    row = cursor.fetchone()
    if not row:
        flash('Request not found or already processed.', 'error')
        return redirect(url_for('upgrade.admin_detail', request_id=request_id))

    user_id = row['user_id']
    tier = row['requested_tier']
    duration = row['duration']
    admin_note = (request.form.get('admin_note') or '').strip()

    if set_user_tier(user_id, tier, admin_id=session['user_id']):
        expiry = calculate_expiry(duration)
        execute_with_retry("""
            UPDATE upgrade_requests
            SET status = 'approved',
                admin_id = ?,
                admin_note = ?,
                expiry_date = ?,
                approved_at = ?
            WHERE request_id = ?
        """, (
            session['user_id'],
            admin_note,
            expiry,
            get_somali_time_db(),
            request_id,
        ), commit=True)

        if row['discount_code_id']:
            execute_with_retry(
                "UPDATE discount_codes SET used_count = used_count + 1 WHERE id = ?",
                (row['discount_code_id'],),
                commit=True,
            )

        try:
            from db import create_notification
            create_notification(
                user_id=user_id,
                type='tier_upgrade',
                title='🎉 Tier Upgrade Approved!',
                body=f'Your account has been upgraded to {tier.upper()}! Valid until {expiry[:10]}.',
                link='/dashboard',
                icon='⭐',
            )
        except Exception as e:
            logger.warning(f"Failed to send approval notification: {e}")

        flash('Request approved. User tier updated.', 'success')
    else:
        flash('Failed to update tier.', 'error')

    return redirect(url_for('upgrade.admin_detail', request_id=request_id))


# ============================================
# ADMIN: Reject Request
# ============================================

@upgrade_bp.route('/admin/upgrade-requests/<request_id>/reject', methods=['POST'])
@admin_required
def admin_reject(request_id):
    if not validate_csrf():
        abort(403)

    reason = (request.form.get('reason') or '').strip()

    cursor = execute_with_retry(
        "SELECT * FROM upgrade_requests WHERE request_id = ? AND status = 'pending'",
        (request_id,),
    )
    row = cursor.fetchone()
    if not row:
        flash('Request not found or already processed.', 'error')
        return redirect(url_for('upgrade.admin_detail', request_id=request_id))

    execute_with_retry("""
        UPDATE upgrade_requests
        SET status = 'rejected',
            admin_id = ?,
            admin_note = ?,
            rejected_at = ?
        WHERE request_id = ?
    """, (
        session['user_id'],
        reason,
        get_somali_time_db(),
        request_id,
    ), commit=True)

    try:
        from db import create_notification
        create_notification(
            user_id=row['user_id'],
            type='tier_upgrade',
            title='❌ Upgrade Request Rejected',
            body=f'Your request for {row["requested_tier"].upper()} was rejected. Reason: {reason or "No reason provided."}',
            link='/dashboard',
            icon='❌',
        )
    except Exception as e:
        logger.warning(f"Failed to send rejection notification: {e}")

    flash('Request rejected.', 'info')
    return redirect(url_for('upgrade.admin_detail', request_id=request_id))


# ============================================
# ADMIN: Delete Request
# ============================================

@upgrade_bp.route('/admin/upgrade-requests/<request_id>/delete', methods=['POST'])
@admin_required
def admin_delete(request_id):
    if not validate_csrf():
        abort(403)

    cursor = execute_with_retry(
        "SELECT * FROM upgrade_requests WHERE request_id = ?",
        (request_id,),
    )
    row = cursor.fetchone()
    if not row:
        flash('Request not found.', 'error')
        return redirect(url_for('upgrade.admin_list'))

    if row['status'] == 'approved':
        flash('Cannot delete an approved request.', 'error')
        return redirect(url_for('upgrade.admin_detail', request_id=request_id))

    execute_with_retry(
        "DELETE FROM upgrade_requests WHERE request_id = ?",
        (request_id,), commit=True,
    )
    flash('Request deleted.', 'info')
    return redirect(url_for('upgrade.admin_list'))


# ============================================
# ADMIN: Bulk Actions
# ============================================

@upgrade_bp.route('/admin/upgrade-requests/bulk', methods=['POST'])
@admin_required
def admin_bulk():
    if not validate_csrf():
        abort(403)

    action = request.form.get('action', '')
    request_ids = request.form.getlist('request_ids')

    if not request_ids:
        flash('No requests selected.', 'error')
        return redirect(url_for('upgrade.admin_list'))

    if action not in ('approve', 'reject', 'delete'):
        flash('Invalid bulk action.', 'error')
        return redirect(url_for('upgrade.admin_list'))

    succeeded = 0
    failed = 0
    admin_note = f'Bulk {action} by admin #{session["user_id"]}'

    for rid in request_ids:
        try:
            cursor = execute_with_retry(
                "SELECT * FROM upgrade_requests WHERE request_id = ? AND status = 'pending'",
                (rid,),
            )
            row = cursor.fetchone()
            if not row:
                failed += 1
                continue

            if action == 'approve':
                tier = row['requested_tier']
                duration = row['duration']
                if set_user_tier(row['user_id'], tier, admin_id=session['user_id']):
                    expiry = calculate_expiry(duration)
                    execute_with_retry("""
                        UPDATE upgrade_requests
                        SET status = 'approved', admin_id = ?, admin_note = ?,
                            expiry_date = ?, approved_at = ?
                        WHERE request_id = ?
                    """, (session['user_id'], admin_note, expiry,
                          get_somali_time_db(), rid), commit=True)
                    if row['discount_code_id']:
                        execute_with_retry(
                            "UPDATE discount_codes SET used_count = used_count + 1 WHERE id = ?",
                            (row['discount_code_id'],), commit=True,
                        )
                    try:
                        from db import create_notification
                        create_notification(
                            user_id=row['user_id'],
                            type='tier_upgrade',
                            title='🎉 Tier Upgrade Approved!',
                            body=f'Your account has been upgraded to {tier.upper()}! Valid until {expiry[:10]}.',
                            link='/dashboard',
                            icon='⭐',
                        )
                    except Exception:
                        pass
                    succeeded += 1
                else:
                    failed += 1

            elif action == 'reject':
                execute_with_retry("""
                    UPDATE upgrade_requests
                    SET status = 'rejected', admin_id = ?, admin_note = ?, rejected_at = ?
                    WHERE request_id = ?
                """, (session['user_id'], admin_note,
                      get_somali_time_db(), rid), commit=True)
                try:
                    from db import create_notification
                    create_notification(
                        user_id=row['user_id'],
                        type='tier_upgrade',
                        title='❌ Upgrade Request Rejected',
                        body=f'Your request for {row["requested_tier"].upper()} was rejected.',
                        link='/dashboard',
                        icon='❌',
                    )
                except Exception:
                    pass
                succeeded += 1

            elif action == 'delete':
                execute_with_retry(
                    "DELETE FROM upgrade_requests WHERE request_id = ?",
                    (rid,), commit=True,
                )
                succeeded += 1

        except Exception as e:
            logger.error(f"Bulk {action} failed for {rid}: {e}", exc_info=True)
            failed += 1

    if succeeded > 0:
        flash(f'Bulk {action}: {succeeded} succeeded.', 'success')
    if failed > 0:
        flash(f'Bulk {action}: {failed} skipped or failed.', 'error')

    return redirect(url_for('upgrade.admin_list'))


# ============================================
# ADMIN: CSV Export
# ============================================

@upgrade_bp.route('/admin/upgrade-requests/export')
@admin_required
def admin_export():
    status_filter = request.args.get('status', '')
    search = request.args.get('search', '').strip()

    query = """
        SELECT r.request_id, s.first_name, s.last_name, s.public_id,
               s.phone_number, r.requested_tier, r.duration,
               r.original_price_cents, r.discount_amount_cents, r.final_price_cents,
               r.status, r.created_at, r.approved_at, r.rejected_at, r.expiry_date,
               r.admin_note, r.user_note
        FROM upgrade_requests r
        LEFT JOIN students s ON r.user_id = s.id
        WHERE 1=1
    """
    params = []
    if status_filter:
        query += " AND r.status = ?"
        params.append(status_filter)
    if search:
        query += " AND (r.request_id LIKE ? OR s.first_name LIKE ? OR s.last_name LIKE ? OR s.phone_number LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like, like])
    query += " ORDER BY r.created_at DESC"

    cursor = execute_with_retry(query, params)
    rows = cursor.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Request ID', 'First Name', 'Last Name', 'Public ID', 'Phone',
        'Tier', 'Duration', 'Original ($)', 'Discount ($)', 'Final ($)',
        'Status', 'Created', 'Approved', 'Rejected', 'Expiry',
        'Admin Note', 'User Note',
    ])
    for row in rows:
        writer.writerow([
            row['request_id'],
            row['first_name'] or '',
            row['last_name'] or '',
            row['public_id'] or '',
            row['phone_number'] or '',
            (row['requested_tier'] or '').upper(),
            (row['duration'] or '').capitalize(),
            f"{(row['original_price_cents'] or 0) / 100:.2f}",
            f"{(row['discount_amount_cents'] or 0) / 100:.2f}",
            f"{(row['final_price_cents'] or 0) / 100:.2f}",
            (row['status'] or '').capitalize(),
            row['created_at'] or '',
            row['approved_at'] or '',
            row['rejected_at'] or '',
            row['expiry_date'] or '',
            (row['admin_note'] or '').replace('\n', ' '),
            (row['user_note'] or '').replace('\n', ' '),
        ])

    output.seek(0)
    filename = f"upgrade_requests_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )


# ============================================
# ADMIN: Discount Management
# ============================================

@upgrade_bp.route('/admin/discounts')
@admin_required
def admin_discounts():
    filter_status = request.args.get('filter', '').strip()
    search = request.args.get('search', '').strip()

    query = "SELECT * FROM discount_codes WHERE 1=1"
    params = []

    if search:
        query += " AND code LIKE ?"
        params.append(f"%{search}%")

    if filter_status == 'active':
        query += (
            " AND is_active = 1"
            " AND (expires_at IS NULL OR expires_at > datetime('now'))"
            " AND (max_uses IS NULL OR used_count < max_uses)"
        )
    elif filter_status == 'inactive':
        query += " AND is_active = 0"
    elif filter_status == 'expired':
        query += " AND expires_at IS NOT NULL AND expires_at <= datetime('now')"
    elif filter_status == 'exhausted':
        query += " AND max_uses IS NOT NULL AND used_count >= max_uses"

    query += " ORDER BY created_at DESC"

    cursor = execute_with_retry(query, params)
    discounts = [dict(row) for row in cursor.fetchall()]

    now_dt = get_somali_time()
    for d in discounts:
        expires_str = d.get('expires_at')
        is_expired = False
        if expires_str:
            try:
                exp_dt = datetime.fromisoformat(str(expires_str).replace(' ', 'T'))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=SOMALI_TIMEZONE)
                is_expired = exp_dt < now_dt
            except (ValueError, TypeError):
                is_expired = False

        is_exhausted = (
            d.get('max_uses') is not None
            and (d.get('used_count') or 0) >= d['max_uses']
        )

        if is_expired:
            d['computed_status'] = 'expired'
        elif is_exhausted:
            d['computed_status'] = 'exhausted'
        elif d.get('is_active'):
            d['computed_status'] = 'active'
        else:
            d['computed_status'] = 'inactive'

    stats = _get_discount_stats()

    return render_template(
        'dashboard/admin/discounts_list.html',
        discounts=discounts,
        stats=stats,
        filter_status=filter_status,
        search=search,
    )


def _get_discount_stats():
    try:
        cursor = execute_with_retry("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN is_active = 1
                         AND (expires_at IS NULL OR expires_at > datetime('now'))
                         AND (max_uses IS NULL OR used_count < max_uses)
                    THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN expires_at IS NOT NULL AND expires_at <= datetime('now')
                    THEN 1 ELSE 0 END) AS expired,
                SUM(CASE WHEN max_uses IS NOT NULL AND used_count >= max_uses
                    THEN 1 ELSE 0 END) AS exhausted,
                COALESCE(SUM(used_count), 0) AS total_uses
            FROM discount_codes
        """)
        row = cursor.fetchone()
        if row:
            return {
                'total': row['total'] or 0,
                'active': row['active'] or 0,
                'expired': row['expired'] or 0,
                'exhausted': row['exhausted'] or 0,
                'total_uses': row['total_uses'] or 0,
            }
    except Exception as e:
        logger.error(f"Discount stats error: {e}", exc_info=True)
    return {'total': 0, 'active': 0, 'expired': 0, 'exhausted': 0, 'total_uses': 0}


@upgrade_bp.route('/admin/discounts/create', methods=['GET', 'POST'])
@admin_required
def admin_discount_create():
    if request.method == 'POST':
        if not validate_csrf():
            abort(403)

        code = (request.form.get('code') or '').strip().upper()
        discount_type = request.form.get('discount_type')
        discount_value_raw = request.form.get('discount_value', '0')
        applies_to = request.form.get('applies_to', 'all')
        max_uses = request.form.get('max_uses')
        expires_at = request.form.get('expires_at')
        is_active = 1 if request.form.get('is_active') == 'on' else 0

        try:
            discount_value = int(discount_value_raw)
        except (ValueError, TypeError):
            discount_value = 0

        if not code or discount_type not in ('percentage', 'fixed') or discount_value <= 0:
            flash('Please fill all required fields.', 'error')
            return render_template('dashboard/admin/discount_form.html')

        if discount_type == 'percentage' and discount_value > 100:
            flash('Percentage discount cannot exceed 100.', 'error')
            return render_template('dashboard/admin/discount_form.html')

        if applies_to not in ('all', 'premium', 'pro'):
            applies_to = 'all'

        max_uses_val = None
        if max_uses and str(max_uses).isdigit():
            max_uses_val = int(max_uses)

        try:
            execute_with_retry("""
                INSERT INTO discount_codes
                (code, discount_type, discount_value, applies_to, max_uses, expires_at, is_active, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                code, discount_type, discount_value, applies_to, max_uses_val,
                expires_at or None, is_active, session['user_id'],
            ), commit=True)
            flash('Discount code created.', 'success')
            return redirect(url_for('upgrade.admin_discounts'))
        except Exception as e:
            logger.error(f"Failed to create discount code: {e}", exc_info=True)
            flash('Error creating discount code. Please check the code is unique.', 'error')

    return render_template('dashboard/admin/discount_form.html')


@upgrade_bp.route('/admin/discounts/<int:discount_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_discount_edit(discount_id):
    cursor = execute_with_retry("SELECT * FROM discount_codes WHERE id = ?", (discount_id,))
    row = cursor.fetchone()
    if not row:
        abort(404)
    discount = dict(row)

    if request.method == 'POST':
        if not validate_csrf():
            abort(403)

        code = (request.form.get('code') or '').strip().upper()
        discount_type = request.form.get('discount_type')
        discount_value_raw = request.form.get('discount_value', '0')
        applies_to = request.form.get('applies_to', 'all')
        max_uses = request.form.get('max_uses')
        expires_at = request.form.get('expires_at')
        is_active = 1 if request.form.get('is_active') == 'on' else 0

        try:
            discount_value = int(discount_value_raw)
        except (ValueError, TypeError):
            discount_value = 0

        if not code or discount_type not in ('percentage', 'fixed') or discount_value <= 0:
            flash('Please fill all required fields.', 'error')
            return render_template('dashboard/admin/discount_form.html', discount=discount)

        if discount_type == 'percentage' and discount_value > 100:
            flash('Percentage discount cannot exceed 100.', 'error')
            return render_template('dashboard/admin/discount_form.html', discount=discount)

        if applies_to not in ('all', 'premium', 'pro'):
            applies_to = 'all'

        max_uses_val = None
        if max_uses and str(max_uses).isdigit():
            max_uses_val = int(max_uses)

        execute_with_retry("""
            UPDATE discount_codes SET
                code = ?, discount_type = ?, discount_value = ?, applies_to = ?,
                max_uses = ?, expires_at = ?, is_active = ?, updated_at = ?
            WHERE id = ?
        """, (
            code, discount_type, discount_value, applies_to, max_uses_val,
            expires_at or None, is_active, get_somali_time_db(), discount_id,
        ), commit=True)
        flash('Discount code updated.', 'success')
        return redirect(url_for('upgrade.admin_discounts'))

    return render_template('dashboard/admin/discount_form.html', discount=discount)


@upgrade_bp.route('/admin/discounts/<int:discount_id>/delete', methods=['POST'])
@admin_required
def admin_discount_delete(discount_id):
    if not validate_csrf():
        abort(403)
    execute_with_retry("DELETE FROM discount_codes WHERE id = ?", (discount_id,), commit=True)
    flash('Discount code deleted.', 'info')
    return redirect(url_for('upgrade.admin_discounts'))


@upgrade_bp.route('/admin/discounts/bulk', methods=['POST'])
@admin_required
def admin_discount_bulk():
    if not validate_csrf():
        abort(403)

    action = request.form.get('action', '')
    ids = request.form.getlist('discount_ids')

    if not ids:
        flash('No discount codes selected.', 'error')
        return redirect(url_for('upgrade.admin_discounts'))

    if action not in ('activate', 'deactivate', 'delete'):
        flash('Invalid bulk action.', 'error')
        return redirect(url_for('upgrade.admin_discounts'))

    succeeded = 0
    failed = 0
    for did in ids:
        try:
            if action == 'delete':
                execute_with_retry(
                    "DELETE FROM discount_codes WHERE id = ?",
                    (did,), commit=True,
                )
            else:
                new_state = 1 if action == 'activate' else 0
                execute_with_retry(
                    "UPDATE discount_codes SET is_active = ?, updated_at = ? WHERE id = ?",
                    (new_state, get_somali_time_db(), did),
                    commit=True,
                )
            succeeded += 1
        except Exception as e:
            logger.error(f"Bulk {action} failed for discount {did}: {e}", exc_info=True)
            failed += 1

    if succeeded:
        flash(f'Bulk {action}: {succeeded} succeeded.', 'success')
    if failed:
        flash(f'Bulk {action}: {failed} failed.', 'error')

    return redirect(url_for('upgrade.admin_discounts'))