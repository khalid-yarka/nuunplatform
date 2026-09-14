# ============================================================
# blueprints/admin/policy_bp.py
# Policy domain — entitlement feature catalog, per-feature
# policy editor, audit trail, and JSON import/export.
#
# Routes:
#   GET  /admin/entitlements                            → catalog
#   GET  /admin/entitlements/<feature_key>              → detail editor
#   POST /admin/entitlements/<feature_key>              → save
#   POST /admin/entitlements/<feature_key>/reset        → reset to registry default
#   GET  /admin/entitlements/audit                      → audit list
#   GET  /admin/entitlements/import                     → import page
#   GET  /admin/entitlements/export                     → download JSON
#   POST /admin/entitlements/import/preview             → AJAX diff
#   POST /admin/entitlements/import                     → apply import
# ============================================================

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort, jsonify, Response,
)
import json
import logging

from db import get_student_by_id
from utils import validate_csrf
from services.admin.guards import admin_can
from services import entitlement_service
from services.admin.entitlements import (
    build_feature_catalog,
    build_feature_detail,
    save_feature_detail,
    recent_entitlement_audit,
    clear_all_overrides_for_feature,
)

logger = logging.getLogger(__name__)

admin_policy_bp = Blueprint('admin_policy', __name__, url_prefix='/admin')

VALID_TIERS = ('free', 'premium', 'pro')


# ============================================================
# CATALOG — /admin/entitlements
# ============================================================

@admin_policy_bp.route('/entitlements', methods=['GET'], endpoint='entitlements')
@admin_can('entitlements.read')
def entitlements():
    features = build_feature_catalog()

    # Filtering
    search = (request.args.get('search') or '').strip().lower()
    category_filter = (request.args.get('category') or '').strip().lower()
    modified_filter = (request.args.get('modified') or '').strip()

    if search:
        features = [
            f for f in features
            if search in f['key'].lower()
            or search in f['display_name'].lower()
            or search in (f.get('description') or '').lower()
        ]

    if category_filter:
        features = [f for f in features if f['category'] == category_filter]

    if modified_filter == '1':
        features = [f for f in features if f['modified']]
    elif modified_filter == '0':
        features = [f for f in features if not f['modified']]

    # Category list for filter dropdown
    all_categories = sorted({f['category'] for f in build_feature_catalog()})

    stats = {
        'total':      len(build_feature_catalog()),
        'active':     sum(1 for f in build_feature_catalog() if f['is_global_active']),
        'modified':   sum(1 for f in build_feature_catalog() if f['modified']),
        'categories': len(all_categories),
    }

    return render_template(
        'dashboard/admin/policy/entitlements.html',
        features=features,
        categories=all_categories,
        stats=stats,
        search=search,
        category_filter=category_filter,
        modified_filter=modified_filter,
    )


# ============================================================
# DETAIL — /admin/entitlements/<feature_key>
# ============================================================

@admin_policy_bp.route('/entitlements/<feature_key>', methods=['GET'],
                       endpoint='entitlement_detail')
@admin_can('entitlements.read')
def entitlement_detail(feature_key):
    detail = build_feature_detail(feature_key)
    if not detail:
        abort(404)

    return render_template(
        'dashboard/admin/policy/entitlement_detail.html',
        feature=detail,
    )


@admin_policy_bp.route('/entitlements/<feature_key>', methods=['POST'],
                       endpoint='entitlement_save')
@admin_can('entitlements.write')
def entitlement_save(feature_key):
    if not validate_csrf():
        abort(403)

    detail = build_feature_detail(feature_key)
    if not detail:
        abort(404)

    # Build the payload from the form
    payload = {
        'is_global_active': request.form.get('is_global_active') == '1',
        'tiers': {},
        'description': detail.get('description', ''),
    }

    for tier in VALID_TIERS:
        tier_payload = {}

        # is_enabled — checkbox, present only when checked
        enabled_key = f'tier_{tier}_is_enabled'
        tier_payload['is_enabled'] = (request.form.get(enabled_key) == '1')

        # level_value — optional, only for level features
        if detail['policy_type'] == 'level':
            raw = request.form.get(f'tier_{tier}_level_value', '').strip()
            if raw == '':
                tier_payload['level_value'] = None
            else:
                try:
                    tier_payload['level_value'] = int(raw)
                except ValueError:
                    tier_payload['level_value'] = None

        # limit_value + limit_unit — only for quota features
        if detail['policy_type'] == 'quota':
            raw = request.form.get(f'tier_{tier}_limit_value', '').strip()
            if raw == '':
                tier_payload['limit_value'] = None
            else:
                try:
                    tier_payload['limit_value'] = int(raw)
                except ValueError:
                    tier_payload['limit_value'] = None

            unit = request.form.get(f'tier_{tier}_limit_unit', '').strip()
            tier_payload['limit_unit'] = unit or None

        payload['tiers'][tier] = tier_payload

    result = save_feature_detail(
        feature_key=feature_key,
        payload=payload,
        actor_id=session.get('user_id'),
        note='',
    )

    if result.get('error'):
        flash(result['error'], 'error')
    else:
        changed = result.get('changed', 0)
        if changed:
            flash(f"Saved {changed} change(s).", 'success')
        else:
            flash("No changes.", 'info')

    return redirect(url_for('admin_policy.entitlement_detail',
                            feature_key=feature_key))


@admin_policy_bp.route('/entitlements/<feature_key>/reset', methods=['POST'],
                       endpoint='entitlement_reset')
@admin_can('entitlements.write')
def entitlement_reset(feature_key):
    if not validate_csrf():
        abort(403)

    detail = build_feature_detail(feature_key)
    if not detail:
        abort(404)

    count = clear_all_overrides_for_feature(
        feature_key=feature_key,
        actor_id=session.get('user_id'),
    )

    flash(
        f"Reset {count} override(s). Feature reverted to registry default."
        if count else "Nothing to reset — already at default.",
        'success' if count else 'info',
    )
    return redirect(url_for('admin_policy.entitlement_detail',
                            feature_key=feature_key))


# ============================================================
# AUDIT — /admin/entitlements/audit
# ============================================================

@admin_policy_bp.route('/entitlements/audit', methods=['GET'],
                       endpoint='entitlements_audit')
@admin_can('entitlements.read')
def entitlements_audit():
    entries = recent_entitlement_audit(limit=200)

    # Filtering
    search = (request.args.get('search') or '').strip().lower()
    if search:
        entries = [
            e for e in entries
            if search in (e.get('action') or '').lower()
            or search in (e.get('note') or '').lower()
            or search in (e.get('actor_first_name') or '').lower()
            or search in (e.get('actor_last_name') or '').lower()
        ]

    return render_template(
        'dashboard/admin/policy/entitlement_audit.html',
        entries=entries,
        search=search,
    )


# ============================================================
# EXPORT — /admin/entitlements/export
# ============================================================

@admin_policy_bp.route('/entitlements/export', methods=['GET'],
                       endpoint='entitlements_export')
@admin_can('entitlements.read')
def entitlements_export():
    data = entitlement_service.export_to_json()
    body = json.dumps(data, indent=2, ensure_ascii=False)

    # Log the export action
    try:
        from services.admin.audit import write_audit
        write_audit(
            action='entitlement.export',
            target_type='entitlement',
            before=None,
            after={'feature_count': len(data.get('features', []))},
            severity='info',
        )
    except Exception as e:
        logger.warning(f"Export audit failed: {e}")

    return Response(
        body,
        mimetype='application/json',
        headers={
            'Content-Disposition': 'attachment; filename=entitlements.json'
        },
    )


# ============================================================
# IMPORT — /admin/entitlements/import
# ============================================================

@admin_policy_bp.route('/entitlements/import', methods=['GET'],
                       endpoint='entitlements_import')
@admin_can('entitlements.write')
def entitlements_import():
    return render_template(
        'dashboard/admin/policy/entitlement_import.html',
    )


@admin_policy_bp.route('/entitlements/import/preview', methods=['POST'],
                       endpoint='entitlements_import_preview')
@admin_can('entitlements.write')
def entitlements_import_preview():
    """
    Compute a diff between submitted JSON and the current policy.
    Never modifies the DB.
    """
    if not validate_csrf():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    data = request.get_json(silent=True) or {}
    raw = (data.get('json_data') or '').strip()
    if not raw:
        return jsonify({'error': 'No JSON data provided'}), 400

    try:
        incoming = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Invalid JSON: {e}'}), 400

    if not isinstance(incoming, dict):
        return jsonify({'error': 'JSON root must be an object'}), 400

    incoming_features = incoming.get('features') or []
    if not isinstance(incoming_features, list):
        return jsonify({'error': '"features" must be an array'}), 400

    current = entitlement_service.get_policy()
    current_keys = set(current.keys())

    changes = []
    added_count = 0
    updated_count = 0
    unknown_keys = []

    for feat in incoming_features:
        if not isinstance(feat, dict):
            continue
        key = feat.get('feature_key')
        if not key:
            continue

        if key not in current_keys:
            unknown_keys.append(key)
            continue

        current_feat = current[key]
        current_policies = current_feat.get('policies', {})
        incoming_policies = feat.get('policies') or {}

        for tier in VALID_TIERS:
            cur_p = current_policies.get(tier, {})
            new_p = incoming_policies.get(tier, {})
            if not new_p:
                continue

            field_changes = {}
            for field in ('is_enabled', 'level_value', 'limit_value', 'limit_unit'):
                if field not in new_p:
                    continue
                before = cur_p.get(field)
                after = new_p.get(field)

                # Normalize booleans from JSON
                if field == 'is_enabled':
                    before = bool(before)
                    after = bool(after)

                # Normalize numerics
                if field in ('level_value', 'limit_value'):
                    if before is not None:
                        try:
                            before = int(before)
                        except (TypeError, ValueError):
                            before = None
                    if after is not None:
                        try:
                            after = int(after)
                        except (TypeError, ValueError):
                            after = None

                if before != after:
                    field_changes[field] = {'before': before, 'after': after}

            if field_changes:
                changes.append({
                    'feature_key': key,
                    'tier': tier,
                    'kind': 'update',
                    'fields': field_changes,
                })
                updated_count += 1

    return jsonify({
        'total_changes': added_count + updated_count,
        'added': added_count,
        'updated': updated_count,
        'unknown': len(unknown_keys),
        'unknown_keys': unknown_keys,
        'changes': changes,
    })


@admin_policy_bp.route('/entitlements/import', methods=['POST'],
                       endpoint='entitlements_import_apply')
@admin_can('entitlements.write')
def entitlements_import_apply():
    if not validate_csrf():
        return jsonify({'error': 'Invalid session. Refresh the page.'}), 403

    data = request.get_json(silent=True) or {}
    raw = (data.get('json_data') or '').strip()
    if not raw:
        return jsonify({'error': 'No JSON data provided'}), 400

    try:
        incoming = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Invalid JSON: {e}'}), 400

    ok, message = entitlement_service.import_from_json(
        admin_id=session.get('user_id'),
        data=incoming,
        reason='Admin bulk import via UI',
    )

    if not ok:
        return jsonify({'error': message}), 400

    try:
        from services.admin.audit import write_audit
        write_audit(
            action='entitlement.import',
            target_type='entitlement',
            before=None,
            after={'feature_count': len(incoming.get('features', []))},
            note=message,
            severity='warning',
        )
    except Exception as e:
        logger.warning(f"Import audit failed: {e}")

    return jsonify({'success': True, 'message': message})