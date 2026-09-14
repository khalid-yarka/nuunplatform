# ============================================================
# services/admin/permissions.py
# High-level operations for the permission management pages.
#
# This module is the ONLY place that knows how the global
# defaults and per-admin overrides are combined for the UI.
# It hides the cache/version dance behind simple operations.
# ============================================================

import logging
from typing import Optional

from services.admin.registry import (
    GROUPS,
    CAPABILITY_REGISTRY,
    REGISTRY_KEYS,
    RESERVED_KEYS,
    WRITABLE_KEYS,
    DEFAULT_ENABLED_KEYS,
    get_capabilities_by_group,
)
from services.admin.grants import (
    list_global_grants,
    list_admin_overrides,
    list_capability_overrides,
    set_global_grant,
    set_admin_override,
    clear_admin_overrides,
    bump_capability_version,
    invalidate_capability_cache,
)
from services.admin.audit import write_audit

logger = logging.getLogger(__name__)


# ============================================================
# GLOBAL DEFAULTS — READ MODEL
# ============================================================

def build_global_grid() -> list[dict]:
    """
    Return the data the global permissions page renders.

    Structure:
        [
            {
                'name': 'Users',
                'capabilities': [
                    {
                        'key': 'users.view',
                        'label': 'View user list',
                        'description': '...',
                        'global_enabled': True,
                        'default_for_admin': True,
                        'reserved': False,
                        'override_count': 2,
                        'override_items': [ {admin_id, name, public_id, is_enabled}, ... ],
                    },
                    ...
                ],
            },
            ...
        ]
    """
    grants = list_global_grants()
    by_group = get_capabilities_by_group()

    result: list[dict] = []
    for group_name in GROUPS:
        caps = by_group.get(group_name, [])
        if not caps:
            continue

        items = []
        for cap in caps:
            override_items = []
            if not cap.reserved:
                override_items = list_capability_overrides(cap.key)

            items.append({
                'key':                 cap.key,
                'label':               cap.label,
                'description':         cap.description,
                'global_enabled':      grants.get(cap.key, False),
                'default_for_admin':   cap.default_for_admin,
                'reserved':            cap.reserved,
                'override_count':      len(override_items),
                'override_items':      override_items,
            })

        result.append({'name': group_name, 'capabilities': items})
    return result


# ============================================================
# GLOBAL DEFAULTS — WRITE MODEL
# ============================================================

def apply_global_changes(
    submitted_keys: set[str],
    actor_id: int,
    note: str = '',
) -> dict:
    """
    Compute the diff between current grants and submitted_keys,
    apply the changes, audit each one, bump the version, and
    invalidate the cache.

    Reserved capabilities are filtered out before any write.

    Returns: {added: int, removed: int, unchanged: int}
    """
    current = list_global_grants()
    submitted = set(submitted_keys) & WRITABLE_KEYS

    added = 0
    removed = 0
    unchanged = 0

    to_enable = submitted - {k for k, v in current.items() if v}
    to_disable = {k for k, v in current.items() if v} - submitted

    for key in to_enable:
        if set_global_grant(key, True, actor_id):
            write_audit(
                action='capability.grant_global',
                target_type='capability',
                target_id=None,
                before={'capability_key': key, 'is_enabled': 0},
                after={'capability_key': key, 'is_enabled': 1},
                note=note,
                severity='warning',
            )
            added += 1

    for key in to_disable:
        if set_global_grant(key, False, actor_id):
            write_audit(
                action='capability.revoke_global',
                target_type='capability',
                target_id=None,
                before={'capability_key': key, 'is_enabled': 1},
                after={'capability_key': key, 'is_enabled': 0},
                note=note,
                severity='warning',
            )
            removed += 1

    unchanged = len(submitted & {k for k, v in current.items() if v})

    if added or removed:
        bump_capability_version()
        invalidate_capability_cache()

    return {'added': added, 'removed': removed, 'unchanged': unchanged}


def reset_global_to_defaults(actor_id: int) -> dict:
    """
    Reset every global grant to the registry's default_for_admin value.
    Does NOT touch per-admin overrides.
    """
    current = list_global_grants()
    changed = 0

    for cap in CAPABILITY_REGISTRY:
        if cap.reserved:
            continue
        target = cap.default_for_admin
        if current.get(cap.key) == target:
            continue
        if set_global_grant(cap.key, target, actor_id):
            write_audit(
                action='capability.reset_global',
                target_type='capability',
                before={'capability_key': cap.key, 'is_enabled': current.get(cap.key)},
                after={'capability_key': cap.key, 'is_enabled': target},
                note='Reset to registry default',
                severity='warning',
            )
            changed += 1

    if changed:
        bump_capability_version()
        invalidate_capability_cache()

    return {'changed': changed}


# ============================================================
# PER-ADMIN OVERRIDES — READ MODEL
# ============================================================

def build_admin_grid(admin_id: int) -> dict:
    """
    Return the data the per-admin override page renders.

    Includes:
        - The admin's identity + summary
        - Per-group capabilities with three-state values
        - Recent changes for this admin
    """
    grants = list_global_grants()
    overrides = list_admin_overrides(admin_id)
    by_group = get_capabilities_by_group()

    # Summary counts
    effective_count = 0
    override_count = 0
    grants_count = 0
    revokes_count = 0

    groups_out: list[dict] = []
    for group_name in GROUPS:
        caps = by_group.get(group_name, [])
        if not caps:
            continue

        items = []
        for cap in caps:
            global_enabled = grants.get(cap.key, False)
            override_value = overrides.get(cap.key, None)
            has_override = cap.key in overrides

            if cap.reserved:
                effective = True
            elif override_value is not None:
                effective = bool(override_value)
            else:
                effective = global_enabled

            if not cap.reserved:
                effective_count += int(effective)
                if has_override:
                    override_count += 1
                    if override_value:
                        grants_count += 1
                    else:
                        revokes_count += 1

            items.append({
                'key':             cap.key,
                'label':           cap.label,
                'description':     cap.description,
                'global_enabled':  global_enabled,
                'override':        override_value,      # None | True | False
                'has_override':    has_override,
                'reserved':        cap.reserved,
                'effective':       effective,
            })

        groups_out.append({'name': group_name, 'capabilities': items})

    from services.admin.audit import recent_audit_for_target
    recent = recent_audit_for_target('admin_permission', admin_id, limit=10)

    return {
        'groups':          groups_out,
        'effective_count': effective_count,
        'override_count':  override_count,
        'grants_count':    grants_count,
        'revokes_count':   revokes_count,
        'default_count':   len(DEFAULT_ENABLED_KEYS),
        'total_writable':  len(WRITABLE_KEYS),
        'recent':          recent,
    }


# ============================================================
# PER-ADMIN OVERRIDES — WRITE MODEL
# ============================================================

def apply_admin_overrides(
    admin_id: int,
    submitted: dict[str, str],
    actor_id: int,
    note: str = '',
) -> dict:
    """
    submitted: { capability_key: 'inherit' | 'on' | 'off' }

    For each entry:
        'inherit' → delete the override row (if present)
        'on'      → insert/update with is_enabled = 1
        'off'     → insert/update with is_enabled = 0

    Reserved capabilities are silently ignored.

    Returns: {changed: int}
    """
    current = list_admin_overrides(admin_id)
    changed = 0

    for key, mode in submitted.items():
        if key not in WRITABLE_KEYS:
            continue

        current_val = current.get(key)  # None | True | False

        if mode == 'inherit':
            if current_val is None:
                continue
            if set_admin_override(admin_id, key, None, actor_id):
                write_audit(
                    action='capability.clear_override',
                    target_type='admin_permission',
                    target_id=admin_id,
                    before={'capability_key': key, 'override': current_val},
                    after={'capability_key': key, 'override': None},
                    note=note,
                    severity='warning',
                )
                changed += 1

        elif mode in ('on', 'off'):
            new_val = (mode == 'on')
            if current_val is not None and current_val == new_val:
                continue
            if set_admin_override(admin_id, key, new_val, actor_id):
                write_audit(
                    action=('capability.force_on' if new_val
                            else 'capability.force_off'),
                    target_type='admin_permission',
                    target_id=admin_id,
                    before={'capability_key': key, 'override': current_val},
                    after={'capability_key': key, 'override': new_val},
                    note=note,
                    severity='warning',
                )
                changed += 1

    if changed:
        bump_capability_version()
        invalidate_capability_cache()

    return {'changed': changed}


def clear_all_overrides(admin_id: int, actor_id: int) -> int:
    """Remove every override for one admin. Returns count deleted."""
    removed = clear_admin_overrides(admin_id, actor_id)
    if removed:
        write_audit(
            action='capability.clear_all_overrides',
            target_type='admin_permission',
            target_id=admin_id,
            before={'override_count': removed},
            after={'override_count': 0},
            note='Cleared all per-admin overrides',
            severity='warning',
        )
        bump_capability_version()
        invalidate_capability_cache()
    return removed


# ============================================================
# ROSTER — READ MODEL
# ============================================================

def build_admin_roster(admin_rows: list[dict],
                       super_admin_id: Optional[int] = None) -> list[dict]:
    """
    Enrich a list of student rows (is_admin = 1) with permission
    summaries for the admins roster page.

    admin_rows: list of { id, public_id, first_name, last_name,
                          phone_number, ... }
    """
    grants = list_global_grants()
    default_count = sum(1 for v in grants.values() if v)
    total_writable = len(WRITABLE_KEYS)

    enriched = []
    for row in admin_rows:
        is_super = (row['id'] == super_admin_id) if super_admin_id else False

        if is_super:
            enriched.append({
                **row,
                'is_super':       True,
                'effective_count': total_writable,
                'override_count':  0,
                'override_grants': 0,
                'override_revokes': 0,
            })
            continue

        overrides = list_admin_overrides(row['id'])
        grants_count = sum(1 for v in overrides.values() if v)
        revokes_count = sum(1 for v in overrides.values() if not v)

        effective = 0
        for cap in CAPABILITY_REGISTRY:
            if cap.reserved:
                continue
            if cap.key in overrides:
                effective += int(overrides[cap.key])
            else:
                effective += int(grants.get(cap.key, False))

        enriched.append({
            **row,
            'is_super':       False,
            'effective_count': effective,
            'override_count':  len(overrides),
            'override_grants': grants_count,
            'override_revokes': revokes_count,
        })

    return enriched