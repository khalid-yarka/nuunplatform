# ============================================================
# services/admin/entitlements.py
# Admin-side operations for the entitlement system.
#
# This module wraps the existing entitlement_service with
# admin-oriented read/write helpers: grid building, per-feature
# detail, override diffs, and audit integration.
#
# The registry-override pattern: registry (code) defines the
# default policy per feature per tier; entitlement_overrides
# (DB) stores only what the super admin has changed.
# ============================================================

import json
import logging
from typing import Optional, Any

from db import execute_with_retry
from services.entitlement_service import get_policy, reload_policy_cache
from services.admin.audit import write_audit

logger = logging.getLogger(__name__)


# ============================================================
# VERSION + CACHE
# ============================================================

def _read_entitlement_version() -> int:
    try:
        row = execute_with_retry(
            "SELECT version FROM entitlement_version WHERE id = 1"
        ).fetchone()
        return int(row['version']) if row else 1
    except Exception:
        return 0


def bump_entitlement_version():
    try:
        execute_with_retry(
            "UPDATE entitlement_version SET version = version + 1 WHERE id = 1",
            commit=True
        )
    except Exception as e:
        logger.error(f"Failed to bump entitlement version: {e}")


# ============================================================
# OVERRIDE STORAGE
# ============================================================

def list_overrides_for_feature(feature_key: str) -> dict[tuple[str, str], Any]:
    """
    Return {(tier, field): value} for all overrides of a feature.
    """
    try:
        rows = execute_with_retry(
            "SELECT tier, field, value FROM entitlement_overrides "
            "WHERE feature_key = ?",
            (feature_key,)
        ).fetchall()
        result = {}
        for r in rows:
            try:
                v = json.loads(r['value']) if r['value'] is not None else None
            except Exception:
                v = r['value']
            result[(r['tier'], r['field'])] = v
        return result
    except Exception as e:
        logger.error(f"list_overrides_for_feature failed: {e}")
        return {}


def set_override(feature_key: str, tier: str, field: str,
                 value: Any, actor_id: int) -> bool:
    try:
        serialized = json.dumps(value)
        execute_with_retry("""
            INSERT INTO entitlement_overrides
                (feature_key, tier, field, value, updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(feature_key, tier, field) DO UPDATE SET
                value = excluded.value,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
        """, (feature_key, tier, field, serialized, actor_id), commit=True)
        return True
    except Exception as e:
        logger.error(f"set_override failed {feature_key}/{tier}/{field}: {e}")
        return False


def clear_override(feature_key: str, tier: str, field: str) -> bool:
    try:
        execute_with_retry(
            "DELETE FROM entitlement_overrides "
            "WHERE feature_key = ? AND tier = ? AND field = ?",
            (feature_key, tier, field), commit=True
        )
        return True
    except Exception as e:
        logger.error(f"clear_override failed {feature_key}/{tier}/{field}: {e}")
        return False


def clear_all_overrides_for_feature(feature_key: str, actor_id: int) -> int:
    try:
        cursor = execute_with_retry(
            "DELETE FROM entitlement_overrides WHERE feature_key = ?",
            (feature_key,), commit=True
        )
        count = cursor.rowcount or 0
        if count:
            write_audit(
                action='entitlement.reset_feature',
                target_type='entitlement',
                before={'feature_key': feature_key, 'override_count': count},
                after={'feature_key': feature_key, 'override_count': 0},
                note='Reset feature to registry default',
                severity='warning',
            )
            bump_entitlement_version()
            reload_policy_cache()
        return count
    except Exception as e:
        logger.error(f"clear_all_overrides_for_feature failed: {e}")
        return 0


# ============================================================
# READ MODEL — Feature Catalog
# ============================================================

def build_feature_catalog() -> list[dict]:
    """
    Return one dict per feature for the entitlements list page.
    """
    policy = get_policy()
    features_out = []

    for key, feature in sorted(
        policy.items(),
        key=lambda kv: (kv[1].get('sort_order', 999), kv[0])
    ):
        overrides = list_overrides_for_feature(key)
        modified = len(overrides) > 0

        # Compact per-tier summary: "10 / 30 / ∞"
        summary_parts = []
        for tier in ('free', 'premium', 'pro'):
            tp = feature['policies'].get(tier, {})
            if not tp.get('is_enabled'):
                summary_parts.append('—')
            elif feature['policy_type'] == 'quota':
                lv = tp.get('limit_value')
                summary_parts.append('∞' if lv is None else str(lv))
            elif feature['policy_type'] == 'level':
                summary_parts.append(f"L{tp.get('level_value', 0)}")
            else:
                summary_parts.append('✓')

        features_out.append({
            'key':            key,
            'display_name':   feature['display_name'],
            'description':    feature['description'],
            'category':       feature['category'],
            'policy_type':    feature['policy_type'],
            'unit_hint':      feature.get('unit_hint') or '',
            'is_global_active': bool(feature.get('is_global_active')),
            'sort_order':     feature.get('sort_order', 0),
            'modified':       modified,
            'summary':        ' / '.join(summary_parts),
        })

    return features_out


# ============================================================
# READ MODEL — Feature Detail
# ============================================================

def build_feature_detail(feature_key: str) -> Optional[dict]:
    """
    Return the full detail for one feature, including the
    effective policy per tier and the overrides applied.
    """
    policy = get_policy()
    feature = policy.get(feature_key)
    if not feature:
        return None

    overrides = list_overrides_for_feature(feature_key)

    tiers_out = {}
    for tier in ('free', 'premium', 'pro'):
        tp = feature['policies'].get(tier, {})
        tier_out = {
            'is_enabled':  bool(tp.get('is_enabled')),
            'level_value': tp.get('level_value'),
            'limit_value': tp.get('limit_value'),
            'limit_unit':  tp.get('limit_unit'),
        }
        # Mark each field as modified if an override exists
        for field in ('is_enabled', 'level_value', 'limit_value', 'limit_unit'):
            tier_out[f'{field}_override'] = (tier, field) in overrides
        tiers_out[tier] = tier_out

    return {
        'key':            feature_key,
        'display_name':   feature['display_name'],
        'description':    feature['description'],
        'category':       feature['category'],
        'policy_type':    feature['policy_type'],
        'unit_hint':      feature.get('unit_hint') or '',
        'is_global_active': bool(feature.get('is_global_active')),
        'sort_order':     feature.get('sort_order', 0),
        'notes':          feature.get('notes') or '',
        'tiers':          tiers_out,
        'override_count': len(overrides),
    }


# ============================================================
# WRITE MODEL — Feature Detail Save
# ============================================================

def save_feature_detail(
    feature_key: str,
    payload: dict,
    actor_id: int,
    note: str = '',
) -> dict:
    """
    Save one feature's policy grid.

    payload = {
        'is_global_active': bool,
        'tiers': {
            'free':    {'is_enabled': bool, 'level_value': int|None,
                        'limit_value': int|None, 'limit_unit': str|None},
            'premium': {...},
            'pro':     {...},
        },
        'description': str,   # optional
    }

    Fields equal to the registry default are cleared (revert to inherit).
    Fields that differ are written as overrides.
    """
    policy = get_policy()
    feature = policy.get(feature_key)
    if not feature:
        return {'error': 'Unknown feature'}

    changed = 0

    # is_global_active
    if 'is_global_active' in payload:
        new_active = bool(payload['is_global_active'])
        if new_active != bool(feature.get('is_global_active')):
            execute_with_retry(
                "UPDATE entitlement_features SET is_global_active = ? "
                "WHERE feature_key = ?",
                (1 if new_active else 0, feature_key), commit=True
            )
            write_audit(
                action='entitlement.toggle_active',
                target_type='entitlement',
                before={'feature_key': feature_key, 'is_global_active': bool(feature.get('is_global_active'))},
                after={'feature_key': feature_key, 'is_global_active': new_active},
                note=note,
                severity='warning',
            )
            changed += 1

    # Per-tier fields
    for tier in ('free', 'premium', 'pro'):
        submitted = payload.get('tiers', {}).get(tier)
        if not submitted:
            continue
        current = feature['policies'].get(tier, {})

        for field in ('is_enabled', 'level_value', 'limit_value', 'limit_unit'):
            if field not in submitted:
                continue
            submitted_value = submitted[field]
            current_value = current.get(field)

            # Normalize
            if field == 'is_enabled':
                submitted_value = bool(submitted_value)
                current_value = bool(current_value)
            elif field in ('level_value', 'limit_value'):
                if submitted_value is not None:
                    try:
                        submitted_value = int(submitted_value)
                    except (TypeError, ValueError):
                        submitted_value = None
                if current_value is not None:
                    try:
                        current_value = int(current_value)
                    except (TypeError, ValueError):
                        current_value = None

            if submitted_value == current_value:
                continue

            if set_override(feature_key, tier, field, submitted_value, actor_id):
                write_audit(
                    action='entitlement.update_policy',
                    target_type='entitlement',
                    before={'feature_key': feature_key, 'tier': tier, 'field': field, 'value': current_value},
                    after={'feature_key': feature_key, 'tier': tier, 'field': field, 'value': submitted_value},
                    note=note,
                    severity='warning',
                )
                changed += 1

    if changed:
        bump_entitlement_version()
        reload_policy_cache()

    return {'changed': changed}


# ============================================================
# AUDIT READER
# ============================================================

def recent_entitlement_audit(limit: int = 100) -> list[dict]:
    try:
        rows = execute_with_retry("""
            SELECT a.*, s.first_name AS actor_first_name,
                   s.last_name AS actor_last_name,
                   s.public_id AS actor_public_id
            FROM admin_audit_log a
            LEFT JOIN students s ON s.id = a.actor_id
            WHERE a.target_type = 'entitlement'
            ORDER BY a.created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"recent_entitlement_audit failed: {e}")
        return []