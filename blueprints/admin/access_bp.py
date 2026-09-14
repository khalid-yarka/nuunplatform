# ============================================================
# blueprints/admin/access_bp.py
# Access domain — admin roster, capability management, audit,
# and impersonation.
#
# Routes:
#   GET  /admin/admins                                → roster
#   GET  /admin/admins/permissions                    → global defaults
#   POST /admin/admins/permissions                    → save global
#   POST /admin/admins/permissions/reset              → reset global
#   GET  /admin/admins/<id>/permissions               → per-admin overrides
#   POST /admin/admins/<id>/permissions               → save overrides
#   POST /admin/admins/<id>/permissions/clear         → clear all overrides
#   GET  /admin/admins/audit                          → unified audit log
#   GET  /admin/impersonate/<user_id>                 → confirm page
#   POST /admin/impersonate/<user_id>                 → start impersonation
#   POST /admin/impersonate/stop                      → stop impersonation
# ============================================================

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort,
)
import logging
import secrets
import time

from config import Config
from db import execute_with_retry, get_student_by_id
from utils import validate_csrf
from services.admin.roles import normalize_phone
from services.admin.guards import admin_can
from services.admin.permissions import (
    build_global_grid,
    apply_global_changes,
    reset_global_to_defaults,
    build_admin_grid,
    apply_admin_overrides,
    clear_all_overrides,
    build_admin_roster,
)
from services.admin.audit import write_audit, audit_stats
from services.admin.registry import DEFAULT_ENABLED_KEYS, REGISTRY_KEYS

logger = logging.getLogger(__name__)

admin_access_bp = Blueprint('admin_access', __name__, url_prefix='/admin')


# ============================================================
# HELPERS
# ============================================================

def _find_super_admin_id():
    """
    Return the id of the current super admin, or None.
    Matched by normalizing every user's phone against SUPER_ADMIN_PHONE.
    """
    target = normalize_phone(Config.SUPER_ADMIN_PHONE)
    if not target:
        return None
    try:
        cursor = execute_with_retry("SELECT id, phone_number FROM students")
        for row in cursor.fetchall():
            if normalize_phone(row['phone_number']) == target:
                return row['id']
    except Exception as e:
        logger.warning(f"_find_super_admin_id failed: {e}")
    return None


# ============================================================
# ROSTER — /admin/admins
# ============================================================

@admin_access_bp.route('/admins', methods=['GET'], endpoint='roster')
@admin_can('admins.manage')
def roster():
    # Fetch every admin (is_admin = 1)
    cursor = execute_with_retry("""
        SELECT id, public_id, first_name, middle_name, last_name,
               phone_number, is_admin
        FROM students
        WHERE is_admin = 1
        ORDER BY first_name, last_name
    """)
    admin_rows = [dict(r) for r in cursor.fetchall()]

    # Identify the super admin
    super_admin_id = _find_super_admin_id()

    # If the super admin isn't in the is_admin=1 set, prepend their row
    if super_admin_id and not any(r['id'] == super_admin_id for r in admin_rows):
        cursor = execute_with_retry("""
            SELECT id, public_id, first_name, middle_name, last_name,
                   phone_number, is_admin
            FROM students WHERE id = ?
        """, (super_admin_id,))
        row = cursor.fetchone()
        if row:
            admin_rows.insert(0, dict(row))

    enriched = build_admin_roster(admin_rows, super_admin_id=super_admin_id)

    stats = {
        'total_admins': len(enriched),
        'total_overrides': sum(a.get('override_count', 0) for a in enriched),
        'admins_with_overrides': sum(1 for a in enriched if a.get('override_count', 0) > 0),
        'global_default_count': len(DEFAULT_ENABLED_KEYS),
        'total_capabilities': len(REGISTRY_KEYS),
    }

    return render_template(
        'dashboard/admin/access/admins.html',
        admins=enriched,
        stats=stats,
    )


# ============================================================
# GLOBAL DEFAULTS — /admin/admins/permissions
# ============================================================

@admin_access_bp.route('/admins/permissions', methods=['GET'],
                       endpoint='global_permissions')
@admin_can('admins.manage')
def global_permissions():
    groups = build_global_grid()
    return render_template(
        'dashboard/admin/access/global_permissions.html',
        groups=groups,
    )


@admin_access_bp.route('/admins/permissions', methods=['POST'],
                       endpoint='global_permissions_save')
@admin_can('admins.manage')
def global_permissions_save():
    if not validate_csrf():
        abort(403)

    submitted = set(request.form.getlist('enabled'))
    note = (request.form.get('note') or '').strip()

    result = apply_global_changes(
        submitted_keys=submitted,
        actor_id=session.get('user_id'),
        note=note,
    )

    parts = []
    if result.get('added'):
        parts.append(f"{result['added']} enabled")
    if result.get('removed'):
        parts.append(f"{result['removed']} disabled")
    if not parts:
        parts.append("no changes")

    flash(f"Global defaults saved: {', '.join(parts)}.", 'success')
    return redirect(url_for('admin_access.global_permissions'))


@admin_access_bp.route('/admins/permissions/reset', methods=['POST'],
                       endpoint='global_permissions_reset')
@admin_can('admins.manage')
def global_permissions_reset():
    if not validate_csrf():
        abort(403)

    result = reset_global_to_defaults(actor_id=session.get('user_id'))
    changed = result.get('changed', 0)
    flash(
        f"Reset {changed} capability(ies) to registry defaults." if changed
        else "Nothing to reset — already at defaults.",
        'success' if changed else 'info',
    )
    return redirect(url_for('admin_access.global_permissions'))


# ============================================================
# PER-ADMIN OVERRIDES — /admin/admins/<id>/permissions
# ============================================================

@admin_access_bp.route('/admins/<int:admin_id>/permissions', methods=['GET'],
                       endpoint='admin_overrides')
@admin_can('admins.manage')
def admin_overrides(admin_id):
    admin_user = get_student_by_id(admin_id)
    if not admin_user:
        abort(404)

    # Must be an admin (or super admin by phone)
    super_admin_id = _find_super_admin_id()
    if not admin_user.get('is_admin') and admin_id != super_admin_id:
        abort(404)

    grid = build_admin_grid(admin_id)

    summary = {
        'override_count':  grid['override_count'],
        'effective_count': grid['effective_count'],
        'default_count':   grid['default_count'],
        'grants_count':    grid['grants_count'],
        'revokes_count':   grid['revokes_count'],
        'total_writable':  grid['total_writable'],
    }

    return render_template(
        'dashboard/admin/access/admin_overrides.html',
        admin=admin_user,
        groups=grid['groups'],
        summary=summary,
        recent=grid['recent'],
    )


@admin_access_bp.route('/admins/<int:admin_id>/permissions', methods=['POST'],
                       endpoint='admin_overrides_save')
@admin_can('admins.manage')
def admin_overrides_save(admin_id):
    if not validate_csrf():
        abort(403)

    admin_user = get_student_by_id(admin_id)
    if not admin_user:
        abort(404)

    # Collect "cap__<key>" radio values from the form
    submitted = {}
    for field_name in request.form:
        if field_name.startswith('cap__'):
            cap_key = field_name[5:]
            submitted[cap_key] = request.form[field_name]

    note = (request.form.get('note') or '').strip()

    result = apply_admin_overrides(
        admin_id=admin_id,
        submitted=submitted,
        actor_id=session.get('user_id'),
        note=note,
    )

    changed = result.get('changed', 0)
    flash(
        f"Saved {changed} override(s)." if changed else "No changes.",
        'success' if changed else 'info',
    )
    return redirect(url_for('admin_access.admin_overrides', admin_id=admin_id))


@admin_access_bp.route('/admins/<int:admin_id>/permissions/clear', methods=['POST'],
                       endpoint='admin_overrides_clear')
@admin_can('admins.manage')
def admin_overrides_clear(admin_id):
    if not validate_csrf():
        abort(403)

    admin_user = get_student_by_id(admin_id)
    if not admin_user:
        abort(404)

    removed = clear_all_overrides(admin_id=admin_id, actor_id=session.get('user_id'))
    flash(
        f"Cleared {removed} override(s)." if removed else "No overrides to clear.",
        'success' if removed else 'info',
    )
    return redirect(url_for('admin_access.admin_overrides', admin_id=admin_id))


# ============================================================
# UNIFIED AUDIT — /admin/admins/audit
# ============================================================

@admin_access_bp.route('/admins/audit', methods=['GET'], endpoint='audit')
@admin_can('audit.view')
def audit():
    page = max(1, int(request.args.get('page') or 1))
    per_page = 50
    offset = (page - 1) * per_page

    severity_filter = (request.args.get('severity') or '').strip()
    actor_filter = (request.args.get('actor') or '').strip()
    search = (request.args.get('search') or '').strip()

    where = ["1=1"]
    params = []

    if severity_filter in ('info', 'warning', 'critical'):
        where.append("a.severity = ?")
        params.append(severity_filter)

    if actor_filter and actor_filter.isdigit():
        where.append("a.actor_id = ?")
        params.append(int(actor_filter))

    if search:
        like = f"%{search}%"
        where.append("(a.action LIKE ? OR a.note LIKE ? OR a.target_type LIKE ?)")
        params.extend([like, like, like])

    where_sql = " AND ".join(where)

    count_cursor = execute_with_retry(
        f"SELECT COUNT(*) AS c FROM admin_audit_log a WHERE {where_sql}",
        params,
    )
    total = count_cursor.fetchone()['c']

    cursor = execute_with_retry(f"""
        SELECT a.*,
               s.first_name AS actor_first_name,
               s.last_name  AS actor_last_name,
               s.public_id  AS actor_public_id
        FROM admin_audit_log a
        LEFT JOIN students s ON s.id = a.actor_id
        WHERE {where_sql}
        ORDER BY a.created_at DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    entries = [dict(r) for r in cursor.fetchall()]

    # Actor options for filter dropdown
    cursor = execute_with_retry("""
        SELECT DISTINCT s.id, s.first_name, s.last_name
        FROM students s
        JOIN admin_audit_log a ON a.actor_id = s.id
        ORDER BY s.first_name, s.last_name
    """)
    actor_options = [dict(r) for r in cursor.fetchall()]

    stats = audit_stats(days=30)
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    return render_template(
        'dashboard/admin/access/admin_audit.html',
        entries=entries,
        stats=stats,
        actor_options=actor_options,
        severity_filter=severity_filter,
        actor_filter=actor_filter,
        search=search,
        page=page,
        total_pages=total_pages,
    )


# ============================================================
# IMPERSONATION — /admin/impersonate/*
# ============================================================

@admin_access_bp.route('/impersonate/<int:user_id>', methods=['GET'],
                       endpoint='impersonate')
@admin_can('users.impersonate')
def impersonate(user_id):
    if user_id == session.get('user_id'):
        flash('You cannot impersonate yourself.', 'error')
        return redirect(url_for('admin.admin_users'))

    target = get_student_by_id(user_id)
    if not target:
        abort(404)

    return render_template(
        'dashboard/admin/access/impersonate.html',
        user=target,
    )


@admin_access_bp.route('/impersonate/<int:user_id>', methods=['POST'],
                       endpoint='impersonate_start')
@admin_can('users.impersonate')
def impersonate_start(user_id):
    if not validate_csrf():
        abort(403)

    if user_id == session.get('user_id'):
        flash('You cannot impersonate yourself.', 'error')
        return redirect(url_for('admin.admin_users'))

    target = get_student_by_id(user_id)
    if not target:
        abort(404)

    reason = (request.form.get('reason') or '').strip()

    # Snapshot the current (admin) session so we can restore it later.
    impersonator_snapshot = {
        'user_id':         session.get('user_id'),
        'user_name':       session.get('user_name'),
        'user_phone':      session.get('user_phone'),
        'public_id':       session.get('public_id'),
        'is_admin':        session.get('is_admin'),
        'is_verified':     session.get('is_verified'),
        'tier':            session.get('tier'),
        'tier_expires_at': session.get('tier_expires_at'),
        'curriculum':      session.get('curriculum'),
        'settings':        session.get('settings'),
    }

    # Write an audit row before the session changes.
    write_audit(
        action='user.impersonate.start',
        target_type='user',
        target_id=user_id,
        before=None,
        after={
            'target_user_id': user_id,
            'target_name': f"{target.get('first_name', '')} {target.get('last_name', '')}".strip(),
            'target_public_id': target.get('public_id'),
        },
        note=reason,
        severity='warning',
    )

    # Replace the session with the target user's identity.
    session.clear()
    session['impersonator'] = impersonator_snapshot
    session['is_impersonating'] = True

    session['user_id'] = target['id']
    session['public_id'] = target.get('public_id') or '----'
    session['user_name'] = target.get('first_name') or 'User'
    session['user_phone'] = target.get('phone_number') or ''
    session['is_admin'] = bool(target.get('is_admin', 0))
    session['is_verified'] = int(target.get('is_verified', 0))
    session['curriculum'] = target.get('curriculum')
    session['tier'] = target.get('tier') or 'free'
    session['tier_expires_at'] = target.get('tier_expires_at')
    session['user_state_loaded_at'] = time.time()
    session['csrf_token'] = secrets.token_hex(32)
    session.permanent = True
    session.modified = True

    # Load target's user settings into the session (best effort).
    try:
        from user_settings import get_user_settings
        session['settings'] = get_user_settings(target['id'])
    except Exception:
        session['settings'] = {}

    flash(f"Now viewing as {target.get('first_name', 'User')}.", 'info')
    return redirect(url_for('dashboard.home'))


@admin_access_bp.route('/impersonate/stop', methods=['POST'],
                       endpoint='impersonate_stop')
def impersonate_stop():
    """
    No capability guard on this route by design.

    Once impersonation has begun, the session no longer belongs to an admin,
    so admin_can() would return False and we could never stop.
    We only need the presence of the `impersonator` snapshot in the session.
    """
    if not validate_csrf():
        abort(403)

    impersonator = session.get('impersonator')
    if not impersonator or not impersonator.get('user_id'):
        flash('No active impersonation session.', 'error')
        return redirect(url_for('auth.login'))

    # Audit the stop (attributed to the impersonator we are restoring).
    try:
        # Temporarily pretend we're the impersonator so write_audit
        # can identify them correctly.
        current_uid = session.get('user_id')
        session['user_id'] = impersonator['user_id']
        session['user_phone'] = impersonator.get('user_phone')
        session['is_admin'] = impersonator.get('is_admin')

        write_audit(
            action='user.impersonate.stop',
            target_type='user',
            target_id=current_uid,
            before={'impersonated_user_id': current_uid},
            after={'restored_admin_id': impersonator['user_id']},
            severity='info',
        )
    except Exception as e:
        logger.warning(f"impersonate_stop audit failed: {e}")

    # Restore the admin session.
    session.clear()
    session.update(impersonator)
    session['csrf_token'] = secrets.token_hex(32)
    session['user_state_loaded_at'] = time.time()
    session.permanent = True
    session.modified = True

    flash('Impersonation stopped. Welcome back.', 'success')
    return redirect(url_for('admin_access.roster'))