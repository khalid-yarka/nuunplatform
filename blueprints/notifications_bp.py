from flask import Blueprint, render_template, request, session, flash, redirect, url_for, jsonify
from db import (
    get_user_notifications, get_unread_count, 
    mark_notification_read, mark_all_notifications_read,
    is_admin,
)
from functools import wraps

notifications_bp = Blueprint('notifications', __name__, url_prefix='/notifications')


def admin_required(f):
    """Decorator to require admin access"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first.', 'error')
            return redirect(url_for('auth.login'))
        if not is_admin(session['user_id']):
            flash('Access denied. Admin only.', 'error')
            return redirect(url_for('dashboard.home'))
        return f(*args, **kwargs)
    return decorated_function


# ============================================
# NOTIFICATIONS PAGE
# ============================================

@notifications_bp.route('/')
def index():
    """View all notifications"""
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))
    
    user_id = session['user_id']
    notifications = get_user_notifications(user_id, limit=50)
    unread_count = get_unread_count(user_id)
    
    return render_template('dashboard/notifications/index.html', 
                         notifications=notifications,
                         unread_count=unread_count)


# ============================================
# API ENDPOINTS (AJAX)
# ============================================

@notifications_bp.route('/api/get')
def api_get_notifications():
    """Get notifications for current user (AJAX)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    limit = request.args.get('limit', 10, type=int)
    unread_only = request.args.get('unread_only', 'false').lower() == 'true'
    
    user_id = session['user_id']
    notifications = get_user_notifications(user_id, limit, unread_only)
    unread_count = get_unread_count(user_id)
    
    return jsonify({
        'notifications': notifications,
        'unread_count': unread_count,
        'total': len(notifications)
    })

@notifications_bp.route('/api/unread-count')
def api_unread_count():
    """Get unread notification count (AJAX)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    user_id = session['user_id']
    count = get_unread_count(user_id)
    
    return jsonify({'count': count})

@notifications_bp.route('/api/mark-read', methods=['POST'])
def api_mark_read():
    """Mark a notification as read (AJAX)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.get_json()
    notification_id = data.get('id')
    
    if not notification_id:
        return jsonify({'error': 'Notification ID required'}), 400
    
    user_id = session['user_id']
    success = mark_notification_read(notification_id, user_id)
    
    if success:
        unread_count = get_unread_count(user_id)
        return jsonify({'success': True, 'unread_count': unread_count})
    
    return jsonify({'error': 'Failed to mark as read'}), 500

@notifications_bp.route('/api/mark-all-read', methods=['POST'])
def api_mark_all_read():
    """Mark all notifications as read (AJAX)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    user_id = session['user_id']
    success = mark_all_notifications_read(user_id)
    
    if success:
        return jsonify({'success': True, 'unread_count': 0})
    
    return jsonify({'error': 'Failed to mark all as read'}), 500


# ============================================
# ADMIN: SEND ANNOUNCEMENT
# ============================================

@notifications_bp.route('/admin/announcement', methods=['GET', 'POST'])
@admin_required
def admin_announcement():
    """
    Legacy path for admin announcements.

    Kept for backward compatibility (existing bookmarks, template
    links, and the admin dashboard shortcut). The canonical route is
    /admin/announcement (admin_system.announcement), which uses the
    same underlying broadcast_announcement() helper.

    Both paths support the "Also deliver as push notification" checkbox.
    """
    if request.method == 'POST':
        title = request.form.get('title', '').strip()[:100]
        body = request.form.get('body', '').strip()[:500]
        link = request.form.get('link', '').strip()[:200]
        also_push = request.form.get('also_push') == '1'

        if not title or not body:
            flash('Title and body are required.', 'error')
            return render_template('dashboard/admin/announcement.html')

        try:
            from services.notification_service import broadcast_announcement
            result = broadcast_announcement(
                title=title,
                body=body,
                link=link or '/dashboard',
                icon='📢',
                also_push=also_push,
            )
        except Exception as e:
            flash(f'Could not send the announcement: {e}', 'error')
            return render_template('dashboard/admin/announcement.html')

        inapp = result.get('in_app', 0)
        push = result.get('push', {})

        if not also_push:
            flash(f'Announcement sent to {inapp} user(s) (in-app only).', 'success')
        elif push.get('skipped') in ('push_disabled', 'push_unavailable'):
            flash(
                f'Announcement sent to {inapp} user(s). '
                f'Push is not configured on the server.',
                'warning',
            )
        elif push.get('skipped') == 'no_recipients':
            flash(
                f'Announcement sent to {inapp} user(s). '
                f'No users have push enabled for announcements.',
                'success',
            )
        else:
            msg = (
                f'Announcement sent to {inapp} user(s). '
                f'Push delivered to {push.get("recipients", 0)} device(s).'
            )
            if push.get('capped'):
                msg += ' (capped at 500 recipients)'
            flash(msg, 'success')

        return redirect(url_for('admin_system.dashboard'))

    return render_template('dashboard/admin/announcement.html')