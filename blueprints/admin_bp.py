# blueprints/admin_bp.py
# Complete admin blueprint with group management API + advanced user management.

from flask import (
    Blueprint, render_template, request, session, flash, redirect, url_for,
    jsonify, abort, Response,
)
from db import (
    is_admin, get_all_students, get_all_questions,
    toggle_admin, delete_user as db_delete_user, get_deleted_users,
    restore_deleted_user as db_restore_user, create_question, delete_question,
    create_group, delete_group, get_all_groups,
    bulk_create_questions, check_question_exists,
    create_notification_for_all_users,
    execute_with_retry,
    get_user_subject_list,
    get_student_by_id,
    get_group_by_id,
    get_group_categories_with_count,
)
from error_models import get_error_stats
from functools import wraps
import json
import secrets
from subjects_config import get_all_subjects
from services.tier_service import get_user_tier, set_user_tier, get_current_user_tier
from services.notification_service import send_notification_to_all
from services.group_service import (
    get_admin_group_list, create_group as svc_create_group,
    update_group, delete_group as svc_delete_group,
    toggle_active, toggle_featured, get_group_stats, get_group_audit_log,
    get_curriculum_subjects,
)
from activity_logger import log_admin_action

from db import (
    get_all_pdfs,
    get_pdf_by_id,
    get_pdf_by_code,
    create_main_pdf,
    delete_main_pdf,
    get_pdf_distinct_subjects,
    get_pdf_distinct_classes,
    get_pdf_distinct_curricula,
    get_main_pdf_count,
)

from services.interaction_service import (
    get_pending_reports, get_all_reports, count_reports,
    resolve_report, dismiss_report, get_report_by_id
)

# ---- Advanced user management helpers ----
from admin_users_db import (
    ensure_admin_user_schema,
    get_users_admin,
    get_users_admin_export,
    get_users_admin_stats,
    users_to_csv,
    set_user_admin_note,
    set_user_tier_admin,
    toggle_user_admin_admin,
    reset_user_password,
    force_user_logout,
    set_user_public_id,
    get_user_admin_history,
    get_user_recent_quizzes_admin,
    get_user_recent_live_quizzes,
    bulk_user_action,
    log_admin_user_action,
)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# ============================================
# DECORATORS
# ============================================

def admin_required(f):
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


def validate_csrf():
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    if not token or token != session.get('csrf_token'):
        abort(403, 'CSRF token validation failed')


# ============================================
# ADMIN DASHBOARD
# ============================================

@admin_bp.route('/')
@admin_required
def dashboard():
    users = get_all_students()
    groups = get_all_groups()
    pdfs = get_all_pdfs()
    questions = get_all_questions()
    error_stats = get_error_stats()

    try:
        from backup import BackupManager
        manager = BackupManager()
        backup_health = manager.get_backup_health_summary()
    except Exception as e:
        backup_health = {'status': 'error', 'issues': [str(e)]}

    try:
        cursor = execute_with_retry(
            "SELECT * FROM activity_logs ORDER BY created_at DESC LIMIT 10"
        )
        recent_activity = [dict(row) for row in cursor.fetchall()]
    except Exception:
        recent_activity = []

    return render_template('dashboard/admin/dashboard.html',
                         users_count=len(users),
                         groups_count=len(groups),
                         pdfs_count=len(pdfs),
                         subjects_count=len(get_all_subjects()),
                         questions_count=len(questions),
                         quiz_attempts=0,
                         error_stats=error_stats,
                         backup_health=backup_health,
                         recent_activity=recent_activity)


# ============================================
# USERS — ADVANCED LIST
# ============================================

@admin_bp.route('/users')
@admin_required
def admin_users():
    """Advanced user list with search, filters, sorting, pagination."""
    ensure_admin_user_schema()

    search = (request.args.get('search') or '').strip()
    tier_filter = (request.args.get('tier') or '').strip().lower()
    location_filter = (request.args.get('location') or '').strip().upper()
    curriculum_filter = (request.args.get('curriculum') or '').strip().lower()
    only_admins = request.args.get('admins') == '1'
    only_inactive = request.args.get('inactive') == '1'
    sort = (request.args.get('sort') or 'newest').strip()
    page = max(1, int(request.args.get('page') or 1))
    per_page = 25

    users, total = get_users_admin(
        search=search,
        tier_filter=tier_filter,
        location_filter=location_filter,
        curriculum_filter=curriculum_filter,
        only_admins=only_admins,
        only_inactive=only_inactive,
        sort=sort,
        page=page,
        per_page=per_page,
    )

    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    stats = get_users_admin_stats()

    # Add tier value per user via service
    for user in users:
        user['tier'] = user.get('tier') or 'danbe'

    return render_template(
        'dashboard/admin/users.html',
        users=users,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        stats=stats,
        search=search,
        tier_filter=tier_filter,
        location_filter=location_filter,
        curriculum_filter=curriculum_filter,
        only_admins=only_admins,
        only_inactive=only_inactive,
        sort=sort,
    )


@admin_bp.route('/users/export')
@admin_required
def admin_users_export():
    """CSV export of the current filter set."""
    ensure_admin_user_schema()

    rows = get_users_admin_export(
        search=(request.args.get('search') or '').strip(),
        tier_filter=(request.args.get('tier') or '').strip().lower(),
        location_filter=(request.args.get('location') or '').strip().upper(),
        curriculum_filter=(request.args.get('curriculum') or '').strip().lower(),
        only_admins=request.args.get('admins') == '1',
        only_inactive=request.args.get('inactive') == '1',
        sort=(request.args.get('sort') or 'newest').strip(),
    )

    csv_data = users_to_csv(rows)
    filename = 'users_export.csv'
    return Response(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


# ============================================
# USERS — DETAIL PAGE
# ============================================

@admin_bp.route('/users/<int:user_id>')
@admin_required
def admin_user_detail(user_id):
    """Full detail page for a single user."""
    ensure_admin_user_schema()

    user = get_student_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin.admin_users'))

    user['tier'] = user.get('tier') or 'danbe'

    quizzes = get_user_recent_quizzes_admin(user_id, limit=20)
    live_quizzes = get_user_recent_live_quizzes(user_id, limit=10)
    history = get_user_admin_history(user_id, limit=50)

    # Simple aggregations for the overview tab
    total_quizzes = len(quizzes)
    avg_score = 0
    if quizzes:
        avg_score = round(sum(q['percentage'] for q in quizzes) / total_quizzes, 1)

    return render_template(
        'dashboard/admin/user_detail.html',
        user=user,
        quizzes=quizzes,
        live_quizzes=live_quizzes,
        history=history,
        total_quizzes=total_quizzes,
        avg_score=avg_score,
    )


# ============================================
# USERS — SINGLE ACTIONS
# ============================================

@admin_bp.route('/users/<int:user_id>/note', methods=['POST'])
@admin_required
def admin_user_set_note(user_id):
    validate_csrf()
    note = (request.form.get('note') or '').strip()
    if set_user_admin_note(user_id, note, session['user_id']):
        flash('Admin note saved.', 'success')
    else:
        flash('Failed to save note.', 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/tier', methods=['POST'])
@admin_required
def admin_user_set_tier(user_id):
    validate_csrf()
    new_tier = (request.form.get('tier') or '').strip().lower()
    if set_user_tier_admin(user_id, new_tier, session['user_id']):
        flash(f'Tier updated to {new_tier.upper()}.', 'success')
    else:
        flash('Failed to update tier.', 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/toggle-admin', methods=['POST'])
@admin_required
def admin_user_toggle_admin(user_id):
    validate_csrf()
    if user_id == session['user_id']:
        flash('You cannot change your own admin status.', 'error')
        return redirect(url_for('admin.admin_user_detail', user_id=user_id))
    new_state = toggle_user_admin_admin(user_id, session['user_id'])
    if new_state is None:
        flash('Failed to change admin status.', 'error')
    else:
        flash(f'Admin privileges {"granted" if new_state else "revoked"}.', 'success')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/notify', methods=['POST'])
@admin_required
def admin_user_notify(user_id):
    validate_csrf()
    title = (request.form.get('title') or '').strip()
    body = (request.form.get('body') or '').strip()
    if not title or not body:
        flash('Title and message are required.', 'error')
        return redirect(url_for('admin.admin_user_detail', user_id=user_id))

    from db import create_notification
    create_notification(user_id, 'admin_direct', title, body, '/dashboard', '📬')
    log_admin_user_action(session['user_id'], user_id, 'notify', None, title[:200])
    flash('Notification sent.', 'success')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def admin_user_reset_password(user_id):
    validate_csrf()
    new_pw = (request.form.get('new_password') or '').strip()
    if len(new_pw) < 8:
        flash('Password must be at least 8 characters.', 'error')
        return redirect(url_for('admin.admin_user_detail', user_id=user_id))
    if reset_user_password(user_id, new_pw, session['user_id']):
        flash('Password reset successfully.', 'success')
    else:
        flash('Failed to reset password.', 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/force-logout', methods=['POST'])
@admin_required
def admin_user_force_logout(user_id):
    validate_csrf()
    if force_user_logout(user_id, session['user_id']):
        flash('User will be logged out on next request.', 'success')
    else:
        flash('Failed to force logout.', 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/public-id', methods=['POST'])
@admin_required
def admin_user_set_public_id(user_id):
    validate_csrf()
    new_id = (request.form.get('public_id') or '').strip().upper()
    ok, msg = set_user_public_id(user_id, new_id, session['user_id'])
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def admin_user_delete(user_id):
    validate_csrf()
    if user_id == session['user_id']:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin.admin_users'))

    keep_ratings = request.form.get('keep_ratings', 'on') == 'on'
    delete_attempts = request.form.get('delete_attempts', 'on') == 'on'

    success, message = db_delete_user(user_id, session['user_id'], keep_ratings, delete_attempts)
    if success:
        try:
            log_admin_user_action(session['user_id'], user_id, 'delete')
        except Exception:
            pass
        flash('User deleted successfully.', 'success')
        return redirect(url_for('admin.admin_users'))
    flash(f'Error deleting user: {message}', 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


# ============================================
# USERS — BULK ACTIONS
# ============================================

@admin_bp.route('/users/bulk', methods=['POST'])
@admin_required
def admin_users_bulk():
    validate_csrf()
    action = (request.form.get('action') or '').strip()
    ids = request.form.getlist('user_ids')

    if not ids:
        flash('No users selected.', 'error')
        return redirect(request.referrer or url_for('admin.admin_users'))

    try:
        user_ids = [int(x) for x in ids]
    except ValueError:
        flash('Invalid user selection.', 'error')
        return redirect(request.referrer or url_for('admin.admin_users'))

    # Prevent acting on self for destructive actions
    user_ids = [u for u in user_ids if u != session['user_id'] or action not in ('delete', 'demote_admin')]

    extra = {}
    if action == 'set_tier':
        extra['tier'] = (request.form.get('bulk_tier') or '').strip().lower()
    elif action == 'notify':
        extra['title'] = (request.form.get('bulk_title') or '').strip()
        extra['body'] = (request.form.get('bulk_body') or '').strip()

    succeeded, failed = bulk_user_action(action, user_ids, session['user_id'], extra)

    if succeeded:
        flash(f'Bulk {action}: {succeeded} succeeded.', 'success')
    if failed:
        flash(f'Bulk {action}: {failed} skipped or failed.', 'error')

    return redirect(request.referrer or url_for('admin.admin_users'))


# ============================================
# OLD COMPAT: keep old endpoint name working
# ============================================

@admin_bp.route('/users/toggle_admin/<user_id>', methods=['POST'])
@admin_required
def toggle_user_admin(user_id):
    """Legacy endpoint — still supported."""
    validate_csrf()
    if user_id == session['user_id']:
        flash('You cannot change your own admin status.', 'error')
        return redirect(url_for('admin.admin_users'))

    result = toggle_admin(user_id)
    if result:
        flash('Admin status updated.', 'success')
        log_admin_action('admin.toggle', f"Toggled admin for user {user_id}", 'info')
    else:
        flash('Error updating admin status.', 'error')
    return redirect(url_for('admin.admin_users'))


# ============================================
# TIER MANAGEMENT (legacy page preserved)
# ============================================

@admin_bp.route('/users/tier/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def manage_user_tier(user_id):
    user = get_student_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin.admin_users'))

    current_tier = get_user_tier(user_id)
    admin_tier = get_current_user_tier()

    if request.method == 'POST':
        validate_csrf()
        new_tier = request.form.get('tier')
        if new_tier not in ['danbe', 'dhexe', 'hore']:
            flash('Invalid tier value.', 'error')
            return redirect(url_for('admin.manage_user_tier', user_id=user_id))

        if set_user_tier(user_id, new_tier, session['user_id']):
            log_admin_user_action(session['user_id'], user_id, 'set_tier', current_tier, new_tier)
            log_admin_action('tier.change',
                             f"Admin {session['user_id']} changed tier of {user_id} "
                             f"from {current_tier} to {new_tier}", 'warning')
            flash(f"User tier updated to {new_tier.capitalize()}.", 'success')
        else:
            flash('Failed to update tier.', 'error')
        return redirect(url_for('admin.admin_users'))

    return render_template('dashboard/admin/manage_tier.html',
                           user=user,
                           current_tier=current_tier,
                           admin_tier=admin_tier)


# ============================================
# DELETED USERS (kept)
# ============================================

@admin_bp.route('/deleted-users')
@admin_required
def deleted_users():
    deleted = get_deleted_users()
    return render_template('dashboard/admin/deleted_users.html', deleted=deleted)


@admin_bp.route('/deleted-users/restore/<deleted_id>', methods=['POST'])
@admin_required
def restore_deleted_user(deleted_id):
    validate_csrf()
    success, message = db_restore_user(deleted_id)
    if success:
        flash('User restored successfully!', 'success')
        log_admin_action('user.restore', f"Restored user from deleted_id {deleted_id}", 'info')
    else:
        flash(f'Error restoring user: {message}', 'error')
    return redirect(url_for('admin.deleted_users'))


# ============================================
# BULK IMPORT QUESTIONS
# ============================================

@admin_bp.route('/bulk-import', methods=['GET', 'POST'])
@admin_required
def bulk_import():
    from subjects_config import get_all_subject_codes
    all_subject_codes = get_all_subject_codes()

    if request.method == 'POST':
        validate_csrf()

        json_data = request.form.get('json_data', '').strip()
        file_data = request.files.get('json_file')

        if file_data and file_data.filename:
            try:
                content = file_data.read().decode('utf-8')
                data = json.loads(content)
            except Exception as e:
                flash(f'Error reading file: {str(e)}', 'error')
                return render_template('dashboard/admin/bulk_import.html')
        elif json_data:
            try:
                data = json.loads(json_data)
            except json.JSONDecodeError as e:
                flash(f'Invalid JSON format: {str(e)}', 'error')
                return render_template('dashboard/admin/bulk_import.html')
        else:
            flash('Please paste JSON or upload a file.', 'error')
            return render_template('dashboard/admin/bulk_import.html')

        if 'metadata' not in data:
            flash('Missing "metadata" section.', 'error')
            return render_template('dashboard/admin/bulk_import.html')
        if 'questions' not in data or not data['questions']:
            flash('Missing or empty "questions" array.', 'error')
            return render_template('dashboard/admin/bulk_import.html')

        subject_code = data['metadata'].get('subject_code', '').strip()
        if not subject_code:
            flash('subject_code is required.', 'error')
            return render_template('dashboard/admin/bulk_import.html')
        if subject_code not in all_subject_codes:
            available = ', '.join(all_subject_codes)
            flash(f'Subject code "{subject_code}" not found. Available: {available}', 'error')
            return render_template('dashboard/admin/bulk_import.html')

        chapter = data['metadata'].get('chapter', '').strip()
        questions_to_import = []
        errors = []
        duplicates = []

        for idx, q in enumerate(data['questions'], 1):
            if not q.get('question', '').strip():
                errors.append({'index': idx, 'question': 'Unknown', 'error': 'Question text is required'})
                continue
            if not q.get('options') or len(q['options']) < 3:
                errors.append({'index': idx, 'question': q.get('question', 'Unknown'), 'error': 'Minimum 3 options required'})
                continue
            if len(q['options']) > 6:
                errors.append({'index': idx, 'question': q.get('question', 'Unknown'), 'error': 'Maximum 6 options allowed'})
                continue
            if not q.get('correct') or q['correct'] < 1 or q['correct'] > len(q['options']):
                errors.append({'index': idx, 'question': q.get('question', 'Unknown'), 'error': 'Invalid correct answer index'})
                continue

            difficulty = q.get('difficulty', 1)
            if difficulty < 1 or difficulty > 5:
                errors.append({'index': idx, 'question': q.get('question', 'Unknown'), 'error': 'Difficulty must be 1-5'})
                continue

            question_text = q['question'].strip()
            if check_question_exists(question_text, subject_code):
                duplicates.append({'index': idx, 'question': question_text, 'error': 'Duplicate question'})
                continue

            options_dict = {}
            option_labels = ['A', 'B', 'C', 'D', 'E', 'F']
            for i, opt in enumerate(q['options']):
                if i < len(option_labels):
                    options_dict[option_labels[i]] = opt.strip()

            correct_letter = option_labels[q['correct'] - 1]

            question_data = {
                'subject_code': subject_code,
                'question_text': question_text,
                'options': options_dict,
                'correct_answer': correct_letter,
                'difficulty': difficulty,
                'chapter': chapter,
                'tags': ','.join(q.get('tags', [])),
                'explanation': q.get('explanation', '').strip(),
                'created_by': session['user_id'],
                'updated_by': session['user_id']
            }
            questions_to_import.append(question_data)

        if errors or duplicates:
            return render_template('dashboard/admin/bulk_import.html',
                                 preview=True,
                                 valid_questions=questions_to_import,
                                 errors=errors,
                                 duplicates=duplicates,
                                 subject_code=subject_code,
                                 chapter=chapter,
                                 total_questions=len(data['questions']))

        if questions_to_import:
            result = bulk_create_questions(questions_to_import, session['user_id'])
            if result['imported'] > 0:
                flash(f'✅ {result["imported"]} questions imported!', 'success')
            if result['errors']:
                flash(f'⚠️ {len(result["errors"])} questions failed.', 'error')
            return redirect(url_for('admin.admin_questions'))
        else:
            flash('No valid questions to import.', 'error')

    return render_template('dashboard/admin/bulk_import.html')


@admin_bp.route('/bulk-preview', methods=['POST'])
@admin_required
def bulk_preview():
    from subjects_config import get_all_subject_codes
    all_subject_codes = get_all_subject_codes()

    json_data = request.form.get('json_data', '').strip()
    if not json_data:
        return jsonify({'error': 'No JSON data provided'}), 400

    try:
        data = json.loads(json_data)
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Invalid JSON: {str(e)}'}), 400

    if 'metadata' not in data:
        return jsonify({'error': 'Missing metadata section'}), 400
    if 'questions' not in data or not data['questions']:
        return jsonify({'error': 'Missing or empty questions array'}), 400

    subject_code = data['metadata'].get('subject_code', '').strip()
    if not subject_code:
        return jsonify({'error': 'subject_code is required'}), 400
    if subject_code not in all_subject_codes:
        return jsonify({'error': f'Subject code "{subject_code}" not found.'}), 400

    preview = []
    for idx, q in enumerate(data['questions'], 1):
        preview.append({
            'index': idx,
            'question': q.get('question', '')[:50] + ('...' if len(q.get('question', '')) > 50 else ''),
            'difficulty': q.get('difficulty', 1),
            'options_count': len(q.get('options', [])),
            'has_explanation': bool(q.get('explanation', '').strip()),
            'tags': ', '.join(q.get('tags', []))[:30]
        })

    return jsonify({
        'subject_code': subject_code,
        'chapter': data['metadata'].get('chapter', ''),
        'total': len(data['questions']),
        'preview': preview[:10]
    })


@admin_bp.route('/bulk-template')
@admin_required
def bulk_template():
    template = {
        "metadata": {"subject_code": "geography", "chapter": "Chapter 1: Introduction"},
        "questions": [{
            "tags": ["geography", "africa", "capitals"],
            "difficulty": 2,
            "question": "What is the capital of Somalia?",
            "options": ["Mogadishu", "Hargeisa", "Kismayo", "Garowe"],
            "correct": 1,
            "explanation": "Mogadishu has been the capital since 1960."
        }]
    }
    response = jsonify(template)
    response.headers['Content-Disposition'] = 'attachment; filename=bulk_import_template.json'
    response.headers['Content-Type'] = 'application/json'
    return response


# ============================================
# GROUPS ADMIN
# ============================================

@admin_bp.route('/groups/api', methods=['POST'])
@admin_required
def api_create_group():
    validate_csrf()
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    for field in ['name', 'platform', 'invite_link']:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400
    data['created_by'] = session['user_id']
    success, group_id = svc_create_group(session['user_id'], data)
    if success:
        return jsonify({'success': True, 'message': 'Group created', 'group_id': group_id})
    return jsonify({'error': 'Failed to create group'}), 500


@admin_bp.route('/groups/api/<int:group_id>', methods=['PUT'])
@admin_required
def api_update_group(group_id):
    validate_csrf()
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    success = update_group(session['user_id'], group_id, data)
    if success:
        return jsonify({'success': True, 'message': 'Group updated'})
    return jsonify({'error': 'Failed to update group'}), 500


@admin_bp.route('/groups/api/<int:group_id>', methods=['DELETE'])
@admin_required
def api_delete_group(group_id):
    validate_csrf()
    success = svc_delete_group(session['user_id'], group_id)
    if success:
        return jsonify({'success': True, 'message': 'Group deleted'})
    return jsonify({'error': 'Failed to delete group'}), 500


@admin_bp.route('/groups/api/<int:group_id>', methods=['GET'])
@admin_required
def api_get_group(group_id):
    group = get_group_by_id(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
    return jsonify(group)


@admin_bp.route('/groups/api/<int:group_id>/toggle-active', methods=['POST'])
@admin_required
def api_toggle_active(group_id):
    validate_csrf()
    success = toggle_active(session['user_id'], group_id)
    if success:
        return jsonify({'success': True, 'message': 'Status toggled'})
    return jsonify({'error': 'Failed to toggle status'}), 500


@admin_bp.route('/groups/api/<int:group_id>/toggle-featured', methods=['POST'])
@admin_required
def api_toggle_featured(group_id):
    validate_csrf()
    success = toggle_featured(session['user_id'], group_id)
    if success:
        return jsonify({'success': True, 'message': 'Featured toggled'})
    return jsonify({'error': 'Failed to toggle featured'}), 500


@admin_bp.route('/groups/api/bulk', methods=['POST'])
@admin_required
def api_bulk_action():
    validate_csrf()
    data = request.get_json()
    action = data.get('action')
    group_ids = data.get('group_ids', [])
    if not action or not group_ids:
        return jsonify({'error': 'Missing action or group IDs'}), 400

    results = {'success': 0, 'failed': 0}
    for group_id in group_ids:
        try:
            if action == 'activate':
                success = update_group(session['user_id'], group_id, {'is_active': 1})
            elif action == 'deactivate':
                success = update_group(session['user_id'], group_id, {'is_active': 0})
            elif action == 'feature':
                success = update_group(session['user_id'], group_id, {'is_featured': 1})
            elif action == 'delete':
                success = svc_delete_group(session['user_id'], group_id)
            else:
                return jsonify({'error': 'Invalid action'}), 400
            if success:
                results['success'] += 1
            else:
                results['failed'] += 1
        except Exception:
            results['failed'] += 1

    return jsonify({
        'success': True,
        'message': f'Completed: {results["success"]} succeeded, {results["failed"]} failed'
    })


@admin_bp.route('/groups')
@admin_required
def admin_groups():
    search = request.args.get('search', '')
    platform = request.args.get('platform', '')
    category = request.args.get('category', '')
    status = request.args.get('status', '')
    page = int(request.args.get('page', 1))
    groups, total = get_admin_group_list(
        search=search, platform=platform, category=category,
        status=status, page=page, per_page=20
    )
    stats = get_group_stats()
    categories = get_group_categories_with_count()
    total_pages = (total + 20 - 1) // 20 if total > 0 else 1
    return render_template('dashboard/admin/groups.html',
                         groups=groups, stats=stats, categories=categories,
                         search=search, platform=platform, category=category,
                         status=status, page=page, total_pages=total_pages)


@admin_bp.route('/groups/analytics')
@admin_required
def groups_analytics():
    stats = get_group_stats()
    groups, _ = get_admin_group_list(per_page=10)
    top_groups = sorted(groups, key=lambda x: x.get('click_count', 0), reverse=True)[:10]
    from db import get_group_platforms_with_count
    platforms = get_group_platforms_with_count()
    return render_template('dashboard/admin/groups_analytics.html',
                         stats=stats, top_groups=top_groups, platforms=platforms)


@admin_bp.route('/groups/audit')
@admin_required
def groups_audit():
    group_id = request.args.get('group_id', type=int)
    admin_id = request.args.get('admin_id', type=int)
    logs = get_group_audit_log(group_id=group_id, admin_id=admin_id, limit=100)
    return render_template('dashboard/admin/groups_audit.html',
                         logs=logs, group_id=group_id, admin_id=admin_id)


# ============================================
# PDFS ADMIN
# ============================================

@admin_bp.route('/pdfs')
@admin_required
def admin_pdfs():
    pdfs = get_all_pdfs()
    return render_template('dashboard/admin/pdfs.html', pdfs=pdfs)


@admin_bp.route('/pdfs/add', methods=['POST'])
@admin_required
def add_pdf():
    validate_csrf()
    code = request.form.get('code', '').strip()
    if not code:
        flash('Code is required.', 'error')
        return redirect(url_for('admin.admin_pdfs'))
    if get_pdf_by_code(code):
        flash('This code already exists.', 'error')
        return redirect(url_for('admin.admin_pdfs'))

    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    curriculum = request.form.get('curriculum', 'PL')
    class_filter = request.form.get('class', '')
    subject = request.form.get('subject', '').strip()
    chapter = request.form.get('chapter', '').strip()
    tags = request.form.get('tags', '').strip()
    is_premium = 1 if request.form.get('is_premium') == 'on' else 0
    file_url = request.form.get('file_url', '').strip()
    uploaded_by = request.form.get('uploaded_by', 'NUUN')

    if not title or not subject:
        flash('Title and Subject are required.', 'error')
        return redirect(url_for('admin.admin_pdfs'))

    data = {
        'code': code, 'title': title, 'description': description,
        'curriculum': curriculum, 'class': class_filter, 'subject': subject,
        'chapter': chapter, 'tags': tags, 'is_premium': is_premium,
        'file_url': file_url if file_url else None, 'uploaded_by': uploaded_by,
    }
    if create_main_pdf(data):
        flash('PDF added successfully!', 'success')
        log_admin_action('pdf.create', f"Added PDF {title}", 'info')
    else:
        flash('Error adding PDF.', 'error')
    return redirect(url_for('admin.admin_pdfs'))


@admin_bp.route('/pdfs/delete/<pdf_id>', methods=['POST'])
@admin_required
def delete_pdf(pdf_id):
    validate_csrf()
    if delete_main_pdf(pdf_id):
        flash('PDF deleted.', 'success')
        log_admin_action('pdf.delete', f"Deleted PDF {pdf_id}", 'info')
    else:
        flash('Error deleting PDF.', 'error')
    return redirect(url_for('admin.admin_pdfs'))


# ============================================
# QUESTIONS ADMIN
# ============================================

@admin_bp.route('/questions')
@admin_required
def admin_questions():
    questions = get_all_questions()
    subjects = get_all_subjects()
    return render_template('dashboard/admin/questions.html',
                         questions=questions, subjects=subjects)


@admin_bp.route('/questions/add', methods=['POST'])
@admin_required
def add_question():
    validate_csrf()
    subject_code = request.form.get('subject_code', '').strip()
    question_text = request.form.get('question_text', '').strip()
    option_a = request.form.get('option_a', '').strip()
    option_b = request.form.get('option_b', '').strip()
    option_c = request.form.get('option_c', '').strip()
    option_d = request.form.get('option_d', '').strip()
    option_e = request.form.get('option_e', '').strip()
    correct_answer = request.form.get('correct_answer', '')
    difficulty = request.form.get('difficulty', 1)
    chapter = request.form.get('chapter', '').strip()
    tags = request.form.get('tags', '').strip()
    explanation = request.form.get('explanation', '').strip()

    if not subject_code or not question_text or not option_a or not option_b or not option_c or not correct_answer:
        flash('Subject, question, options A-C, and correct answer are required.', 'error')
        return redirect(url_for('admin.admin_questions'))

    from subjects_config import get_subject
    if not get_subject(subject_code):
        flash('Invalid subject code.', 'error')
        return redirect(url_for('admin.admin_questions'))

    options = {'A': option_a, 'B': option_b, 'C': option_c}
    if option_d: options['D'] = option_d
    if option_e: options['E'] = option_e

    data = {
        'subject_code': subject_code, 'question_text': question_text,
        'options': options, 'correct_answer': correct_answer,
        'difficulty': int(difficulty) if difficulty else 1,
        'chapter': chapter, 'tags': tags, 'explanation': explanation,
        'created_by': session['user_id'], 'updated_by': session['user_id'],
    }
    if create_question(data):
        flash('Question added!', 'success')
        log_admin_action('question.create', f"Added question for {subject_code}", 'info')
    else:
        flash('Error adding question.', 'error')
    return redirect(url_for('admin.admin_questions'))


@admin_bp.route('/questions/delete/<question_id>', methods=['POST'])
@admin_required
def delete_question_route(question_id):
    validate_csrf()
    if delete_question(question_id):
        flash('Question archived.', 'success')
        log_admin_action('question.archive', f"Archived question {question_id}", 'info')
    else:
        flash('Error archiving question.', 'error')
    return redirect(url_for('admin.admin_questions'))


# ============================================
# ANNOUNCEMENT
# ============================================

@admin_bp.route('/announcement', methods=['GET', 'POST'])
@admin_required
def admin_announcement():
    if request.method == 'POST':
        validate_csrf()
        title = request.form.get('title', '').strip()
        body = request.form.get('body', '').strip()
        link = request.form.get('link', '').strip()
        if not title or not body:
            flash('Title and body are required.', 'error')
            return render_template('dashboard/admin/announcement.html')

        send_notification_to_all(
            notification_type='admin',
            title=title, body=body,
            link=link or '/dashboard', icon='📢', force=True,
        )
        flash('✅ Announcement sent to all users!', 'success')
        log_admin_action('announcement.send', f"Sent announcement: {title}", 'info')
        return redirect(url_for('admin.dashboard'))

    return render_template('dashboard/admin/announcement.html')


# ============================================
# REPORTS
# ============================================

@admin_bp.route('/reports')
@admin_required
def reports():
    status = request.args.get('status', 'pending')
    page = int(request.args.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page

    if status == 'pending':
        reports_list = get_pending_reports(limit=per_page, offset=offset)
        total = count_reports('pending')
    else:
        reports_list = get_all_reports(
            limit=per_page, offset=offset,
            status=status if status != 'all' else None
        )
        total = count_reports(status if status != 'all' else None)

    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    return render_template('dashboard/admin/reports.html',
                         reports=reports_list, status=status,
                         page=page, total_pages=total_pages, total=total)


@admin_bp.route('/reports/<int:report_id>/resolve', methods=['POST'])
@admin_required
def resolve_report_route(report_id):
    validate_csrf()
    reply = request.form.get('reply', '').strip()
    if resolve_report(report_id, session['user_id'], reply):
        flash('Report resolved.', 'success')
        report = get_report_by_id(report_id)
        if report and reply:
            from db import create_notification
            create_notification(
                user_id=report['user_id'], type='admin_reply',
                title='Report Update',
                body=f'Admin replied: {reply[:100]}{"..." if len(reply) > 100 else ""}',
                link='/quiz', icon='📬'
            )
    else:
        flash('Failed to resolve report.', 'error')
    return redirect(url_for('admin.reports', status='pending'))


@admin_bp.route('/reports/<int:report_id>/dismiss', methods=['POST'])
@admin_required
def dismiss_report_route(report_id):
    validate_csrf()
    reply = request.form.get('reply', '').strip()
    if dismiss_report(report_id, session['user_id'], reply):
        flash('Report dismissed.', 'success')
    else:
        flash('Failed to dismiss report.', 'error')
    return redirect(url_for('admin.reports', status='pending'))