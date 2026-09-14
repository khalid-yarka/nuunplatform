# ============================================================
# services/admin/__init__.py
# Admin control plane services.
# ============================================================

from services.admin.registry import (
    CAPABILITY_REGISTRY,
    REGISTRY_KEYS,
    RESERVED_KEYS,
    DEFAULT_ENABLED_KEYS,
    GROUPS,
    get_capability,
    get_capabilities_by_group,
    list_writable_capabilities,
)

from services.admin.roles import (
    is_super_admin,
    is_admin,
    is_any_admin,
    normalize_phone,
    current_actor_role,
)

from services.admin.capabilities import (
    admin_can,
    admin_can_any,
    admin_can_all,
    invalidate_request_cache,
)

from services.admin.grants import (
    invalidate_capability_cache,
    bump_capability_version,
    get_global_grant,
    get_effective_override,
    list_admin_overrides,
    list_capability_overrides,
    list_global_grants,
    get_cached_version,
)

from services.admin.guards import (
    admin_required,
    admin_can as admin_can_route,
    silent_deny,
)

from services.admin.audit import (
    admin_action,
    write_audit,
    recent_audit,
    recent_audit_for_target,
)


__all__ = [
    # Registry
    'CAPABILITY_REGISTRY',
    'REGISTRY_KEYS',
    'RESERVED_KEYS',
    'DEFAULT_ENABLED_KEYS',
    'GROUPS',
    'get_capability',
    'get_capabilities_by_group',
    'list_writable_capabilities',

    # Roles
    'is_super_admin',
    'is_admin',
    'is_any_admin',
    'normalize_phone',
    'current_actor_role',

    # Capabilities
    'admin_can',
    'admin_can_any',
    'admin_can_all',
    'invalidate_request_cache',

    # Grants
    'invalidate_capability_cache',
    'bump_capability_version',
    'get_global_grant',
    'get_effective_override',
    'list_admin_overrides',
    'list_capability_overrides',
    'list_global_grants',
    'get_cached_version',

    # Guards
    'admin_required',
    'admin_can_route',
    'silent_deny',

    # Audit
    'admin_action',
    'write_audit',
    'recent_audit',
    'recent_audit_for_target',
]