# ============================================================
# services/admin/entitlements.py
# Admin-side operations for the entitlement system.
#
# ── FIX: save_feature_detail() now compares each submitted value
#    against the SEED policy (unmerged) rather than the effective
#    policy. This lets the writer detect three distinct states:
#
#      submitted == seed        → clear any override (revert to inherit)
#      submitted != seed        → set/update the override
#      submitted == effective   → no-op (already in the right state)
#
#    Previously it compared against the effective policy, which
#    meant an admin who reverted a field to seed value left a dead
#    override behind. Now the override is cleaned up automatically.
# ============================================================

import json
import logging
from typing import Optional, Any

from db import execute_with_retry
from services.entitlement_service import (
    get_policy,
    get_seed_feature,
    reload_policy_cache,
)
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
    Return the full detail for one feature, including the effective
    policy per tier and the overrides applied.
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

def _norm_field(field: str, value: Any) -> Any:
    """Normalize a submitted or seed value into its canonical type."""
    if field == 'is_enabled':
        return bool(value)
    if field in ('level_value', 'limit_value'):
        if value is None or value == '':
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return value


def save_feature_detail(
    feature_key: str,
    payload: dict,
    actor_id: int,
    note: str = '',
) -> dict:
    """
    Save one feature's policy grid.

    Comparison is done against the SEED policy, not the effective policy:
        submitted == seed   → clear any override on this field (inherit)
        submitted != seed   → set / update the override
    """
    policy = get_policy()
    feature = policy.get(feature_key)
    if not feature:
        return {'error': 'Unknown feature'}

    seed = get_seed_feature(feature_key)
    if not seed:
        # Extremely defensive: fall back to the effective policy as a
        # comparison base if the seed read fails.
        seed = feature

    existing_overrides = list_overrides_for_feature(feature_key)
    changed = 0

    # ---------- is_global_active ----------
    if 'is_global_active' in payload:
        new_active = bool(payload['is_global_active'])
        old_active = bool(feature.get('is_global_active'))
        if new_active != old_active:
            execute_with_retry(
                "UPDATE entitlement_features SET is_global_active = ? "
                "WHERE feature_key = ?",
                (1 if new_active else 0, feature_key), commit=True
            )
            write_audit(
                action='entitlement.toggle_active',
                target_type='entitlement',
                before={'feature_key': feature_key, 'is_global_active': old_active},
                after={'feature_key': feature_key, 'is_global_active': new_active},
                note=note,
                severity='warning',
            )
            changed += 1

    # ---------- Per-tier fields ----------
    for tier in ('free', 'premium', 'pro'):
        submitted_tier = payload.get('tiers', {}).get(tier)
        if not submitted_tier:
            continue

        seed_tier = seed.get('policies', {}).get(tier, {})

        for field in ('is_enabled', 'level_value', 'limit_value', 'limit_unit'):
            if field not in submitted_tier:
                continue

            submitted_value = _norm_field(field, submitted_tier[field])
            seed_value = _norm_field(field, seed_tier.get(field))

            has_override = (tier, field) in existing_overrides
            current_override = existing_overrides.get((tier, field))

            if submitted_value == seed_value:
                # Should inherit — clear any existing override.
                if has_override:
                    if clear_override(feature_key, tier, field):
                        write_audit(
                            action='entitlement.clear_override',
                            target_type='entitlement',
                            before={
                                'feature_key': feature_key,
                                'tier': tier,
                                'field': field,
                                'value': current_override,
                            },
                            after={
                                'feature_key': feature_key,
                                'tier': tier,
                                'field': field,
                                'value': None,
                            },
                            note=note,
                            severity='warning',
                        )
                        changed += 1
                continue

            # submitted differs from seed — set / update override.
            if has_override and _norm_field(field, current_override) == submitted_value:
                # Override already holds the requested value. No-op.
                continue

            if set_override(feature_key, tier, field, submitted_value, actor_id):
                write_audit(
                    action='entitlement.update_policy',
                    target_type='entitlement',
                    before={
                        'feature_key': feature_key,
                        'tier': tier,
                        'field': field,
                        'value': current_override if has_override else seed_value,
                    },
                    after={
                        'feature_key': feature_key,
                        'tier': tier,
                        'field': field,
                        'value': submitted_value,
                    },
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