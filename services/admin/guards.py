# ============================================================
# services/admin/guards.py
# Route decorators for admin authorization.
#
# Two decorators only:
#   @admin_required         — any admin (super or regular)
#   @admin_can('cap.key')   — admin with a specific capability
#
# Both use silent denial: 404, indistinguishable from a
# nonexistent route.
# ============================================================

import logging
from functools import wraps

from flask import abort, request, session, current_app

from services.admin.roles import is_any_admin
from services.admin.capabilities import admin_can as _admin_can

logger = logging.getLogger(__name__)


# ============================================================
# SILENT DENIAL
# ============================================================

def silent_deny(reason: str):
    """
    Log the reason server-side, return a generic 404 to the client.

    The 404 is indistinguishable from a nonexistent route:
        - Same status code (404)
        - Same body (handled by the app's 404 handler)
        - Same headers
        - No mention of the missing capability in the response
    """
    try:
        current_app.logger.warning(
            "admin.deny",
            extra={
                'reason':     reason,
                'user_id':    session.get('user_id'),
                'path':       request.path,
                'method':     request.method,
                'ip_address': request.remote_addr,
            }
        )
    except Exception:
        # Logging must never break the request
        pass
    abort(404)


# ============================================================
# DECORATOR: admin_required
# ============================================================

def admin_required(f):
    """
    Requires: is_any_admin()
    Denies with: silent 404
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not is_any_admin():
            silent_deny('not_admin')
        return f(*args, **kwargs)
    return wrapper


# ============================================================
# DECORATOR: admin_can(capability_key)
# ============================================================

def admin_can(capability_key: str):
    """
    Requires: is_any_admin() AND admin_can(capability_key)
    Denies with: silent 404

    Ordering note: when combined with @admin_action, this decorator
    must be applied FIRST (closest to the function is last applied).
    Example:
        @route(...)
        @admin_can('users.delete')     # deny check runs first
        @admin_action(action='...')    # audit wrap runs second
        def handler(...): ...
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not is_any_admin():
                silent_deny('not_admin')
            if not _admin_can(capability_key):
                silent_deny(f'missing_capability:{capability_key}')
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ============================================================
# DECORATOR: super_admin_required
# ============================================================
#
# Not used directly by route guards (super admin is expressed
# through capabilities that only the super admin has), but
# exposed for the handful of endpoints that are hard-bound to
# super admin regardless of capability state (e.g. safety
# hooks in the permission management pages themselves).
# ============================================================

def super_admin_required(f):
    """Requires: is_super_admin(). Denies with: silent 404."""
    from services.admin.roles import is_super_admin

    @wraps(f)
    def wrapper(*args, **kwargs):
        if not is_super_admin():
            silent_deny('not_super_admin')
        return f(*args, **kwargs)
    return wrapper