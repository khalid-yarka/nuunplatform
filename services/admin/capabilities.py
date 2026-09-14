# ============================================================
# services/admin/capabilities.py
# The resolver. Every authorization decision in the admin
# system ultimately calls this module.
#
# Resolution chain:
#   1. Registry check (unknown capability → False)
#   2. Super admin bypass (True)
#   3. Admin check (non-admin → False)
#   4. Two-layer policy resolution (override → global)
# ============================================================

import logging
from typing import Iterable

from flask import g, has_request_context, session
from services.admin.registry import REGISTRY_KEYS
from services.admin.roles import is_super_admin, is_admin
from services.admin.grants import get_global_grant, get_effective_override

logger = logging.getLogger(__name__)


# ============================================================
# PER-REQUEST MEMOIZATION
# ============================================================

def _memo() -> dict:
    if not has_request_context():
        return {}
    if not hasattr(g, '_admin_cap_memo'):
        g._admin_cap_memo = {}
    return g._admin_cap_memo


def invalidate_request_cache():
    """Clear the per-request memo (rarely needed)."""
    if has_request_context() and hasattr(g, '_admin_cap_memo'):
        g._admin_cap_memo.clear()


# ============================================================
# CORE RESOLVER
# ============================================================

def _resolve(capability_key: str) -> bool:
    # Step 1 — Registry check
    if capability_key not in REGISTRY_KEYS:
        return False

    # Step 2 — Super admin bypass
    if is_super_admin():
        return True

    # Step 3 — Admin check
    if not is_admin():
        return False

    # Step 4 — Two-layer policy resolution
    admin_id = session.get('user_id')
    if not admin_id:
        return False

    override = get_effective_override(int(admin_id), capability_key)
    if override is not None:
        return override

    global_value = get_global_grant(capability_key)
    return bool(global_value)


def admin_can(capability_key: str) -> bool:
    """Check a single capability. Memoized per request."""
    memo = _memo()
    if capability_key not in memo:
        memo[capability_key] = _resolve(capability_key)
    return memo[capability_key]


def admin_can_any(*capability_keys: str) -> bool:
    """True if the admin has at least one of the given capabilities."""
    return any(admin_can(k) for k in capability_keys)


def admin_can_all(*capability_keys: str) -> bool:
    """True if the admin has every one of the given capabilities."""
    return all(admin_can(k) for k in capability_keys)


# ============================================================
# BULK RESOLUTION (for permission grid rendering)
# ============================================================

def resolve_many(capability_keys: Iterable[str]) -> dict[str, bool]:
    """
    Resolve a batch of capabilities at once.
    Used by the permission management pages.
    """
    return {k: admin_can(k) for k in capability_keys}