# ============================================================
# blueprints/admin/community_bp.py
# Community domain — groups, reports, achievements, leaderboard.
#
# Tier model: free / premium only. No pro.
#
# NOTE ON NAMING
#   `groups.location`  — holds '' / 'SO' / 'PL' / 'SL'
#   `groups.stream`    — holds '' / 'general' / 'science' / 'arts'
#                        (only meaningful when location == 'PL')
#   The admin UI labels the location field "Curriculum" because
#   that's the word students recognise. Under the hood, the field
#   stores location codes and the form sends them as `location`.
# ============================================================

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort, jsonify,
)
import logging

from db import (
    execute_with_retry,
    get_group_by_id,
    get_group_categories_with_count,
    get_group_platforms_with_count,
    get_leaderboard,
)
from services.group_service import (
    get_admin_group_list,
    get_group_stats,
    get_group_audit_log,
    create_group,
    update_group,
    delete_group,
    toggle_active,
    toggle_featured,
    VALID_LOCATIONS,
    VALID_STREAMS,
    STREAM_REQUIRED_LOCATION,
)
from services.interaction_service import (
    get_pending_reports,
    get_all_reports,
    count_reports,
    resolve_report,
    dismiss_report,
    get_report_by_id,
)
from services.achievement_service import (
    get_all_achievements,
    ensure_achievements_seeded,
)
from utils import validate_csrf
from services.admin.guards import admin_required, admin_can
from services.admin.audit import write_audit

logger = logging.getLogger(__name__)

admin_community_bp = Blueprint('admin_community', __name__, url_prefix='/admin')


# ============================================================
# HELPERS
# ============================================================

def _csrf_ok():
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    return bool(token) and token == session.get('csrf_token')


# Labels shown to admins. The section is labelled "Curriculum"
# in the UI, but the values are location codes.
LOCATION_OPTIONS = [
    {'code': '',   'label': 'All curricula', 'flag': '🌍'},
    {'code': 'SO', 'label': 'Somalia',       'flag': '🇸🇴'},
    {'code': 'PL', 'label': 'Puntland',      'flag': '🇸🇱'},
    {'code': 'SL', 'label': 'Somaliland',    'flag': '🇮🇷'},
]

STREAM_OPTIONS = [
    {'code': '',        'label': 'Any stream', 'icon': '•'},
    {'code': 'general', 'label': 'General',    'icon': '📘'},
    {'code': 'science', 'label': 'Science',    'icon': '🔬'},
    {'code': 'arts',    'label': 'Arts',       'icon': '🎨'},
]

TIER_OPTIONS = [
    {'code': 'free',    'label': 'Free',    'icon': '🔓'},
    {'code': 'premium', 'label': 'Premium', 'icon': '🔑'},
]

_VALID_PLATFORMS = ('whatsapp', 'telegram')
_VALID_TIERS     = ('free', 'premium')


def _form_bool(name):
    """Checkbox present → 1, absent → 0."""
    return 1 if request.form.get(name) else 0


def _extract_group_form():
    """
    Read the group_edit.html form into a dict suitable for
    create_group / update_group. Returns (data, errors).
    """
    location = (request.form.get('location') or '').strip().upper()
    stream   = (request.form.get('stream') or '').strip().lower()

    # Stream only meaningful for PL groups. Force-clear otherwise.
    if location != STREAM_REQUIRED_LOCATION:
        stream = ''

    data = {
        'name':             (request.form.get('name') or '').strip(),
        'category':         (request.form.get('category') or '').strip(),
        'icon':             (request.form.get('icon') or '📚').strip()[:8],
        'description':      (request.form.get('description') or '').strip(),
        'platform':         (request.form.get('platform') or 'whatsapp').strip(),
        'invite_link':      (request.form.get('invite_link') or '').strip(),
        'location':         location,
        'stream':           stream,
        'tier_required':    (request.form.get('tier_required') or 'free').strip(),
        'requires_verified': _form_bool('requires_verified'),
        'is_visible':       _form_bool('is_visible'),
        'is_active':        _form_bool('is_active'),
        'is_featured':      _form_bool('is_featured'),
        'subjects':         (request.form.get('subjects') or '').strip(),
    }

    try:
        data['display_order'] = int(request.form.get('display_order') or 0)
    except (TypeError, ValueError):
        data['display_order'] = 0

    errors = []
    if not data['name']:
        errors.append('Group name is required.')
    if not data['invite_link']:
        errors.append('Invite link is required.')
    if data['platform'] not in _VALID_PLATFORMS:
        errors.append('Invalid platform.')
    if location not in VALID_LOCATIONS:
        errors.append('Invalid curriculum.')
    if stream not in VALID_STREAMS:
        errors.append('Invalid stream.')
    if data['tier_required'] not in _VALID_TIERS:
        errors.append('Invalid tier.')

    return data, errors


# ============================================================
# GROUPS — LIST
# ============================================================

@admin_community_bp.route('/groups', methods=['GET'], endpoint='groups')
@admin_can('groups.view')
def groups():
    search   = (request.args.get('search') or '').strip()
    platform = (request.args.get('platform') or '').strip()
    category = (request.args.get('category') or '').strip()
    status   = (request.args.get('status') or '').strip()
    page     = max(1, int(request.args.get('page') or 1))
    per_page = 20

    groups_list, total = get_admin_group_list(
        search=search,
        platform=platform,
        category=category,
        status=status,
        page=page,
        per_page=per_page,
    )

    stats = get_group_stats()
    categories = get_group_categories_with_count()
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    return render_template(
        'dashboard/admin/community/groups.html',
        groups=groups_list,
        stats=stats,
        categories=categories,
        search=search,
        platform=platform,
        category=category,
        status=status,
        page=page,
        total_pages=total_pages,
        total=total,
    )


# ============================================================
# GROUPS — CREATE (GET form + POST handler)
# ============================================================

@admin_community_bp.route('/groups/new', methods=['GET', 'POST'],
                          endpoint='group_new')
@admin_can('groups.create')
def group_new():
    if request.method == 'POST':
        if not validate_csrf():
            abort(403)

        data, errors = _extract_group_form()
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template(
                'dashboard/admin/community/group_edit.html',
                group=data,
                is_new=True,
                location_options=LOCATION_OPTIONS,
                stream_options=STREAM_OPTIONS,
                tier_options=TIER_OPTIONS,
                categories=get_group_categories_with_count(),
            )

        data['created_by'] = session.get('user_id')
        success, group_id = create_group(session.get('user_id'), data)

        if not success:
            flash('Failed to create group. Please check the fields and retry.',
                  'error')
            return render_template(
                'dashboard/admin/community/group_edit.html',
                group=data,
                is_new=True,
                location_options=LOCATION_OPTIONS,
                stream_options=STREAM_OPTIONS,
                tier_options=TIER_OPTIONS,
                categories=get_group_categories_with_count(),
            )

        write_audit(
            action='group.create',
            target_type='group',
            target_id=group_id,
            before=None,
            after={
                'name':     data['name'],
                'platform': data['platform'],
                'location': data['location'],
                'stream':   data['stream'],
            },
            severity='info',
        )
        flash('Group created.', 'success')
        return redirect(url_for('admin_community.groups'))

    # GET
    return render_template(
        'dashboard/admin/community/group_edit.html',
        group=None,
        is_new=True,
        location_options=LOCATION_OPTIONS,
        stream_options=STREAM_OPTIONS,
        tier_options=TIER_OPTIONS,
        categories=get_group_categories_with_count(),
    )


# ============================================================
# GROUPS — EDIT (GET form + POST handler)
# ============================================================

@admin_community_bp.route('/groups/<int:group_id>/edit',
                          methods=['GET', 'POST'],
                          endpoint='group_edit')
@admin_can('groups.edit')
def group_edit(group_id):
    group = get_group_by_id(group_id)
    if not group:
        abort(404)

    if request.method == 'POST':
        if not validate_csrf():
            abort(403)

        data, errors = _extract_group_form()
        if errors:
            for e in errors:
                flash(e, 'error')
            merged = dict(group)
            merged.update(data)
            return render_template(
                'dashboard/admin/community/group_edit.html',
                group=merged,
                is_new=False,
                location_options=LOCATION_OPTIONS,
                stream_options=STREAM_OPTIONS,
                tier_options=TIER_OPTIONS,
                categories=get_group_categories_with_count(),
            )

        if not update_group(session.get('user_id'), group_id, data):
            flash('Failed to update group. Please retry.', 'error')
            return redirect(url_for('admin_community.group_edit',
                                    group_id=group_id))

        write_audit(
            action='group.update',
            target_type='group',
            target_id=group_id,
            before={
                'name':              group.get('name'),
                'platform':          group.get('platform'),
                'location':          group.get('location'),
                'stream':            group.get('stream'),
                'tier_required':     group.get('tier_required'),
                'requires_verified': group.get('requires_verified'),
                'is_active':         group.get('is_active'),
                'is_featured':       group.get('is_featured'),
                'is_visible':        group.get('is_visible'),
            },
            after={
                'name':              data['name'],
                'platform':          data['platform'],
                'location':          data['location'],
                'stream':            data['stream'],
                'tier_required':     data['tier_required'],
                'requires_verified': data['requires_verified'],
                'is_active':         data['is_active'],
                'is_featured':       data['is_featured'],
                'is_visible':        data['is_visible'],
            },
            severity='info',
        )
        flash('Group updated.', 'success')
        return redirect(url_for('admin_community.groups'))

    # GET
    return render_template(
        'dashboard/admin/community/group_edit.html',
        group=group,
        is_new=False,
        location_options=LOCATION_OPTIONS,
        stream_options=STREAM_OPTIONS,
        tier_options=TIER_OPTIONS,
        categories=get_group_categories_with_count(),
    )


# ============================================================
# GROUPS — ANALYTICS
# ============================================================

@admin_community_bp.route('/groups/analytics', methods=['GET'],
                          endpoint='groups_analytics')
@admin_can('groups.view')
def groups_analytics():
    stats = get_group_stats()
    groups_list, _ = get_admin_group_list(per_page=10)
    top_groups = sorted(
        groups_list,
        key=lambda x: x.get('click_count', 0),
        reverse=True,
    )[:10]
    platforms = get_group_platforms_with_count()

    return render_template(
        'dashboard/admin/community/groups_analytics.html',
        stats=stats,
        top_groups=top_groups,
        platforms=platforms,
    )


# ============================================================
# GROUPS — AUDIT
# ============================================================

@admin_community_bp.route('/groups/audit', methods=['GET'],
                          endpoint='groups_audit')
@admin_can('groups.view')
def groups_audit():
    group_id = request.args.get('group_id', type=int)
    admin_id = request.args.get('admin_id', type=int)
    action_filter = (request.args.get('action') or '').strip()
    search = (request.args.get('search') or '').strip()

    logs = get_group_audit_log(group_id=group_id, admin_id=admin_id, limit=200)

    for log in logs:
        g = get_group_by_id(log['group_id']) if log.get('group_id') else None
        log['group_name'] = g['name'] if g else None

    if action_filter:
        logs = [l for l in logs if l.get('action') == action_filter]

    if search:
        s = search.lower()
        logs = [
            l for l in logs
            if s in (l.get('group_name') or '').lower()
            or s in (l.get('first_name') or '').lower()
            or s in (l.get('last_name') or '').lower()
            or s in (l.get('action') or '').lower()
        ]

    return render_template(
        'dashboard/admin/community/groups_audit.html',
        logs=logs,
        search=search,
        action_filter=action_filter,
        group_id=group_id,
        admin_id=admin_id,
    )


# ============================================================
# GROUPS — FORM-POST (used by admin/groups.html)
# ============================================================

@admin_community_bp.route('/groups/bulk', methods=['POST'],
                          endpoint='group_bulk')
@admin_can('groups.bulk')
def group_bulk():
    if not validate_csrf():
        abort(403)

    action = (request.form.get('action') or '').strip()
    raw_ids = request.form.getlist('group_ids')

    if not action or not raw_ids:
        flash('No action or groups selected.', 'error')
        return redirect(url_for('admin_community.groups'))

    clean_ids = []
    for raw in raw_ids:
        try:
            clean_ids.append(int(raw))
        except (TypeError, ValueError):
            continue

    if not clean_ids:
        flash('No valid groups selected.', 'error')
        return redirect(url_for('admin_community.groups'))

    if action not in ('activate', 'deactivate', 'feature',
                      'unfeature', 'delete'):
        flash('Invalid bulk action.', 'error')
        return redirect(url_for('admin_community.groups'))

    if action == 'delete' and not admin_can('groups.delete'):
        flash('Delete permission required.', 'error')
        return redirect(url_for('admin_community.groups'))

    admin_id = session.get('user_id')
    succeeded = 0
    failed = 0

    for gid in clean_ids:
        try:
            if action == 'activate':
                ok = update_group(admin_id, gid, {'is_active': 1})
            elif action == 'deactivate':
                ok = update_group(admin_id, gid, {'is_active': 0})
            elif action == 'feature':
                ok = update_group(admin_id, gid, {'is_featured': 1})
            elif action == 'unfeature':
                ok = update_group(admin_id, gid, {'is_featured': 0})
            elif action == 'delete':
                ok = delete_group(admin_id, gid)
            else:
                ok = False

            if ok:
                succeeded += 1
            else:
                failed += 1
        except Exception as e:
            logger.warning(f"bulk {action} failed for group {gid}: {e}")
            failed += 1

    write_audit(
        action=f'group.bulk_{action}',
        target_type='group',
        before=None,
        after={
            'action':    action,
            'succeeded': succeeded,
            'failed':    failed,
            'ids':       clean_ids,
        },
        severity='warning',
    )

    msg = f'{succeeded} group{"s" if succeeded != 1 else ""} updated.'
    if failed:
        msg += f' {failed} failed.'
    flash(msg, 'success' if succeeded else 'error')

    return redirect(url_for('admin_community.groups'))


@admin_community_bp.route('/groups/<int:group_id>/delete', methods=['POST'],
                          endpoint='group_delete')
@admin_can('groups.delete')
def group_delete(group_id):
    if not validate_csrf():
        abort(403)

    before = get_group_by_id(group_id)
    if not before:
        abort(404)

    if delete_group(session.get('user_id'), group_id):
        write_audit(
            action='group.delete',
            target_type='group',
            target_id=group_id,
            before={'name': before.get('name'),
                    'platform': before.get('platform')},
            after=None,
            severity='warning',
        )
        flash('Group deleted.', 'success')
    else:
        flash('Failed to delete group.', 'error')

    return redirect(url_for('admin_community.groups'))


# ============================================================
# GROUPS — API (JSON)
# ============================================================

@admin_community_bp.route('/groups/api', methods=['POST'],
                          endpoint='group_api_create')
@admin_can('groups.create')
def group_api_create():
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    data = request.get_json(silent=True) or {}
    for field in ('name', 'platform', 'invite_link'):
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400

    if data.get('platform') not in ('whatsapp', 'telegram'):
        return jsonify({'error': 'Invalid platform'}), 400

    data['created_by'] = session.get('user_id')
    success, group_id = create_group(session.get('user_id'), data)

    if success:
        write_audit(
            action='group.create',
            target_type='group',
            target_id=group_id,
            before=None,
            after={'name': data.get('name'), 'platform': data.get('platform')},
            severity='info',
        )
        return jsonify({
            'success': True,
            'message': 'Group created',
            'group_id': group_id,
            'redirect': url_for('admin_community.groups'),
        })

    return jsonify({'error': 'Failed to create group'}), 500


@admin_community_bp.route('/groups/api/<int:group_id>', methods=['PUT', 'POST'],
                          endpoint='group_api_update')
@admin_can('groups.edit')
def group_api_update(group_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    before = get_group_by_id(group_id)
    if not before:
        return jsonify({'error': 'Group not found'}), 404

    if update_group(session.get('user_id'), group_id, data):
        write_audit(
            action='group.update',
            target_type='group',
            target_id=group_id,
            before={'name': before.get('name')},
            after={'name': data.get('name', before.get('name'))},
            severity='info',
        )
        return jsonify({
            'success': True,
            'message': 'Group updated',
            'redirect': url_for('admin_community.groups'),
        })

    return jsonify({'error': 'Failed to update group'}), 500


@admin_community_bp.route('/groups/api/<int:group_id>', methods=['DELETE'],
                          endpoint='group_api_delete')
@admin_can('groups.delete')
def group_api_delete(group_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    before = get_group_by_id(group_id)
    if not before:
        return jsonify({'error': 'Group not found'}), 404

    if delete_group(session.get('user_id'), group_id):
        write_audit(
            action='group.delete',
            target_type='group',
            target_id=group_id,
            before={'name': before.get('name')},
            after=None,
            severity='warning',
        )
        return jsonify({'success': True, 'message': 'Group deleted'})

    return jsonify({'error': 'Failed to delete group'}), 500


@admin_community_bp.route('/groups/api/<int:group_id>', methods=['GET'],
                          endpoint='group_api_get')
@admin_can('groups.view')
def group_api_get(group_id):
    group = get_group_by_id(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
    return jsonify(group)


@admin_community_bp.route('/groups/api/<int:group_id>/toggle-active',
                          methods=['POST'],
                          endpoint='group_api_toggle_active')
@admin_can('groups.edit')
def group_api_toggle_active(group_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    before = get_group_by_id(group_id)
    if not before:
        return jsonify({'error': 'Group not found'}), 404

    if toggle_active(session.get('user_id'), group_id):
        after = get_group_by_id(group_id)
        write_audit(
            action='group.toggle_active',
            target_type='group',
            target_id=group_id,
            before={'is_active': before.get('is_active')},
            after={'is_active': after.get('is_active') if after else None},
            severity='info',
        )
        return jsonify({
            'success': True,
            'is_active': after.get('is_active') if after else None,
            'message': 'Status toggled',
        })

    return jsonify({'error': 'Failed to toggle status'}), 500


@admin_community_bp.route('/groups/api/<int:group_id>/toggle-featured',
                          methods=['POST'],
                          endpoint='group_api_toggle_featured')
@admin_can('groups.feature')
def group_api_toggle_featured(group_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    before = get_group_by_id(group_id)
    if not before:
        return jsonify({'error': 'Group not found'}), 404

    if toggle_featured(session.get('user_id'), group_id):
        after = get_group_by_id(group_id)
        write_audit(
            action='group.toggle_featured',
            target_type='group',
            target_id=group_id,
            before={'is_featured': before.get('is_featured')},
            after={'is_featured': after.get('is_featured') if after else None},
            severity='info',
        )
        return jsonify({
            'success': True,
            'is_featured': after.get('is_featured') if after else None,
            'message': 'Featured toggled',
        })

    return jsonify({'error': 'Failed to toggle featured'}), 500


@admin_community_bp.route('/groups/api/bulk', methods=['POST'],
                          endpoint='group_api_bulk')
@admin_can('groups.bulk')
def group_api_bulk():
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    data = request.get_json(silent=True) or {}
    action = data.get('action')
    group_ids = data.get('group_ids', [])

    if not action or not group_ids:
        return jsonify({'error': 'Missing action or group IDs'}), 400

    results = {'success': 0, 'failed': 0}

    for gid in group_ids:
        try:
            if action == 'activate':
                ok = update_group(session.get('user_id'), gid, {'is_active': 1})
            elif action == 'deactivate':
                ok = update_group(session.get('user_id'), gid, {'is_active': 0})
            elif action == 'feature':
                ok = update_group(session.get('user_id'), gid, {'is_featured': 1})
            elif action == 'unfeature':
                ok = update_group(session.get('user_id'), gid, {'is_featured': 0})
            elif action == 'delete':
                ok = delete_group(session.get('user_id'), gid)
            else:
                return jsonify({'error': 'Invalid action'}), 400

            results['success' if ok else 'failed'] += 1
        except Exception:
            results['failed'] += 1

    write_audit(
        action=f'group.bulk_{action}',
        target_type='group',
        before=None,
        after={
            'action': action,
            'succeeded': results['success'],
            'failed': results['failed'],
        },
        severity='warning',
    )

    return jsonify({
        'success': True,
        'message': (
            f'Completed: {results["success"]} succeeded, '
            f'{results["failed"]} failed'
        ),
    })


# ============================================================
# REPORTS
# ============================================================

@admin_community_bp.route('/reports', methods=['GET'], endpoint='reports')
@admin_can('reports.view')
def reports():
    status = (request.args.get('status') or 'pending').strip()
    page = max(1, int(request.args.get('page') or 1))
    per_page = 20
    offset = (page - 1) * per_page

    if status == 'pending':
        reports_list = get_pending_reports(limit=per_page, offset=offset)
        total = count_reports('pending')
    else:
        reports_list = get_all_reports(
            limit=per_page,
            offset=offset,
            status=status if status != 'all' else None,
        )
        total = count_reports(status if status != 'all' else None)

    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    return render_template(
        'dashboard/admin/community/reports.html',
        reports=reports_list,
        status=status,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@admin_community_bp.route('/reports/<int:report_id>/resolve', methods=['POST'],
                          endpoint='report_resolve')
@admin_can('reports.resolve')
def report_resolve(report_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    report = get_report_by_id(report_id)
    if not report:
        return jsonify({'error': 'Report not found'}), 404

    reply = (request.form.get('reply') or '').strip()

    if resolve_report(report_id, session.get('user_id'), reply):
        if reply and report.get('user_id'):
            try:
                from db import create_notification
                create_notification(
                    report['user_id'],
                    'admin_reply',
                    'Report Update',
                    f'Admin replied: {reply[:100]}',
                    '/quiz',
                    '📬',
                )
            except Exception:
                pass

        write_audit(
            action='report.resolve',
            target_type='report',
            target_id=report_id,
            before={'status': 'pending'},
            after={'status': 'resolved', 'reply': reply[:200]},
            severity='info',
        )
        return jsonify({'success': True})

    return jsonify({'error': 'Failed to resolve report'}), 500


@admin_community_bp.route('/reports/<int:report_id>/dismiss', methods=['POST'],
                          endpoint='report_dismiss')
@admin_can('reports.dismiss')
def report_dismiss(report_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    report = get_report_by_id(report_id)
    if not report:
        return jsonify({'error': 'Report not found'}), 404

    reply = (request.form.get('reply') or '').strip()

    if dismiss_report(report_id, session.get('user_id'), reply):
        write_audit(
            action='report.dismiss',
            target_type='report',
            target_id=report_id,
            before={'status': 'pending'},
            after={'status': 'dismissed', 'reply': reply[:200]},
            severity='info',
        )
        return jsonify({'success': True})

    return jsonify({'error': 'Failed to dismiss report'}), 500


# ============================================================
# ACHIEVEMENTS
# ============================================================

@admin_community_bp.route('/achievements', methods=['GET'],
                          endpoint='achievements')
@admin_required
def achievements():
    ensure_achievements_seeded()
    all_ach = get_all_achievements()

    total_unlocks = 0
    for a in all_ach:
        cursor = execute_with_retry(
            "SELECT COUNT(*) AS c FROM user_achievements WHERE achievement_id = ?",
            (a['id'],),
        )
        row = cursor.fetchone()
        a['unlock_count'] = row['c'] if row else 0
        total_unlocks += a['unlock_count']

    return render_template(
        'dashboard/admin/community/achievements.html',
        achievements=all_ach,
        total_unlocks=total_unlocks,
    )


@admin_community_bp.route('/achievements/new', methods=['GET'],
                          endpoint='achievement_new')
@admin_required
def achievement_new():
    return render_template(
        'dashboard/admin/community/achievement_edit.html',
        achievement=None,
    )


@admin_community_bp.route('/achievements/new', methods=['POST'],
                          endpoint='achievement_create')
@admin_required
def achievement_create():
    if not validate_csrf():
        abort(403)

    name = (request.form.get('name') or '').strip()
    description = (request.form.get('description') or '').strip()
    icon = (request.form.get('icon') or '🏆').strip()
    tier_required = (request.form.get('tier_required') or 'free').strip()
    unlock_condition = (request.form.get('unlock_condition') or '').strip()

    if not name:
        flash('Achievement name is required.', 'error')
        return redirect(url_for('admin_community.achievement_new'))

    if tier_required not in ('free', 'premium'):
        tier_required = 'free'

    try:
        execute_with_retry("""
            INSERT INTO achievements
                (name, description, icon, tier_required, unlock_condition)
            VALUES (?, ?, ?, ?, ?)
        """, (name, description, icon, tier_required, unlock_condition),
        commit=True)

        write_audit(
            action='achievement.create',
            target_type='achievement',
            before=None,
            after={'name': name, 'tier_required': tier_required},
            severity='info',
        )
        flash('Achievement created.', 'success')
        return redirect(url_for('admin_community.achievements'))

    except Exception as e:
        logger.error(f"achievement_create failed: {e}")
        flash('Error creating achievement.', 'error')
        return redirect(url_for('admin_community.achievement_new'))


@admin_community_bp.route('/achievements/<int:achievement_id>/edit',
                          methods=['GET'],
                          endpoint='achievement_edit')
@admin_required
def achievement_edit(achievement_id):
    cursor = execute_with_retry(
        "SELECT * FROM achievements WHERE id = ?",
        (achievement_id,),
    )
    row = cursor.fetchone()
    if not row:
        abort(404)

    return render_template(
        'dashboard/admin/community/achievement_edit.html',
        achievement=dict(row),
    )


@admin_community_bp.route('/achievements/<int:achievement_id>/edit',
                          methods=['POST'],
                          endpoint='achievement_update')
@admin_required
def achievement_update(achievement_id):
    if not validate_csrf():
        abort(403)

    cursor = execute_with_retry(
        "SELECT * FROM achievements WHERE id = ?",
        (achievement_id,),
    )
    before_row = cursor.fetchone()
    if not before_row:
        abort(404)
    before = dict(before_row)

    name = (request.form.get('name') or '').strip()
    description = (request.form.get('description') or '').strip()
    icon = (request.form.get('icon') or '🏆').strip()
    tier_required = (request.form.get('tier_required') or 'free').strip()
    unlock_condition = (request.form.get('unlock_condition') or '').strip()

    if not name:
        flash('Achievement name is required.', 'error')
        return redirect(url_for('admin_community.achievement_edit',
                                achievement_id=achievement_id))

    if tier_required not in ('free', 'premium'):
        tier_required = 'free'

    try:
        execute_with_retry("""
            UPDATE achievements SET
                name = ?, description = ?, icon = ?,
                tier_required = ?, unlock_condition = ?
            WHERE id = ?
        """, (name, description, icon, tier_required,
              unlock_condition, achievement_id),
        commit=True)

        write_audit(
            action='achievement.update',
            target_type='achievement',
            target_id=achievement_id,
            before={'name': before.get('name'),
                    'tier_required': before.get('tier_required')},
            after={'name': name, 'tier_required': tier_required},
            severity='info',
        )
        flash('Achievement updated.', 'success')
        return redirect(url_for('admin_community.achievements'))

    except Exception as e:
        logger.error(f"achievement_update failed: {e}")
        flash('Error updating achievement.', 'error')
        return redirect(url_for('admin_community.achievement_edit',
                                achievement_id=achievement_id))


@admin_community_bp.route('/achievements/<int:achievement_id>/delete',
                          methods=['POST'],
                          endpoint='achievement_delete')
@admin_required
def achievement_delete(achievement_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    cursor = execute_with_retry(
        "SELECT * FROM achievements WHERE id = ?",
        (achievement_id,),
    )
    row = cursor.fetchone()
    if not row:
        return jsonify({'error': 'Achievement not found'}), 404

    before = dict(row)

    try:
        execute_with_retry(
            "DELETE FROM achievements WHERE id = ?",
            (achievement_id,),
            commit=True,
        )
        write_audit(
            action='achievement.delete',
            target_type='achievement',
            target_id=achievement_id,
            before={'name': before.get('name')},
            after=None,
            severity='warning',
        )
        return jsonify({'success': True, 'message': 'Achievement deleted'})
    except Exception as e:
        logger.error(f"achievement_delete failed: {e}")
        return jsonify({'error': 'Delete failed'}), 500


# ============================================================
# LEADERBOARD
# ============================================================

@admin_community_bp.route('/leaderboard', methods=['GET'],
                          endpoint='leaderboard')
@admin_required
def leaderboard():
    rows = []
    try:
        cursor = execute_with_retry("""
            SELECT
                s.id, s.public_id, s.first_name, s.middle_name, s.last_name,
                s.total_points, s.school
            FROM students s
            ORDER BY s.total_points DESC
            LIMIT 50
        """)
        rows = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.warning(f"leaderboard query failed: {e}")

    return render_template(
        'dashboard/admin/community/leaderboard_admin.html',
        leaders=rows,
    )


@admin_community_bp.route('/leaderboard/reset-points/<int:user_id>',
                          methods=['POST'],
                          endpoint='leaderboard_reset_points')
@admin_required
def leaderboard_reset_points(user_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    cursor = execute_with_retry(
        "SELECT id, first_name, last_name, total_points FROM students WHERE id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    if not row:
        return jsonify({'error': 'User not found'}), 404

    old_points = row['total_points'] or 0

    try:
        execute_with_retry(
            "UPDATE students SET total_points = 0 WHERE id = ?",
            (user_id,),
            commit=True,
        )
        write_audit(
            action='user.reset_points',
            target_type='user',
            target_id=user_id,
            before={'total_points': old_points},
            after={'total_points': 0},
            severity='warning',
        )
        return jsonify({'success': True, 'message': 'Points reset to 0'})
    except Exception as e:
        logger.error(f"leaderboard_reset_points failed: {e}")
        return jsonify({'error': 'Reset failed'}), 500