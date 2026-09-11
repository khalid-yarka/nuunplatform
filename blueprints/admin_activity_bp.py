# admin_activity_bp.py
from flask import Blueprint, render_template, request, session, jsonify, abort
from functools import wraps
from db import is_admin, execute_with_retry
from activity_logger import log_activity

admin_activity_bp = Blueprint('admin_activity', __name__, url_prefix='/admin/activity')

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or not is_admin(session['user_id']):
            abort(403)
        return f(*args, **kwargs)
    return decorated

@admin_activity_bp.route('/')
@admin_required
def index():
    return render_template('dashboard/admin/activity.html')

@admin_activity_bp.route('/feed')
@admin_required
def feed():
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    activity_type = request.args.get('type', '')
    severity = request.args.get('severity', '')
    user_id = request.args.get('user_id', '')
    search = request.args.get('search', '').strip()

    query = "SELECT * FROM activity_logs WHERE 1=1"
    count_query = "SELECT COUNT(*) as total FROM activity_logs WHERE 1=1"
    params, count_params = [], []

    if activity_type:
        query += " AND activity_type = ?"; count_query += " AND activity_type = ?"
        params.append(activity_type); count_params.append(activity_type)
    if severity:
        query += " AND severity = ?"; count_query += " AND severity = ?"
        params.append(severity); count_params.append(severity)
    if user_id:
        query += " AND user_id = ?"; count_query += " AND user_id = ?"
        params.append(user_id); count_params.append(user_id)
    if search:
        like = f"%{search}%"
        query += " AND (message LIKE ? OR activity_type LIKE ?)"
        count_query += " AND (message LIKE ? OR activity_type LIKE ?)"
        params.extend([like, like]); count_params.extend([like, like])

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor = execute_with_retry(query, params)
    logs = [dict(row) for row in cursor.fetchall()]
    total_row = execute_with_retry(count_query, count_params).fetchone()
    total = total_row['total'] if total_row else 0

    types_cursor = execute_with_retry("SELECT DISTINCT activity_type FROM activity_logs ORDER BY activity_type")
    types = [row['activity_type'] for row in types_cursor.fetchall()]

    return jsonify({'logs': logs, 'total': total, 'types': types, 'limit': limit, 'offset': offset})