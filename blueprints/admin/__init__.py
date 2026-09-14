# ============================================================
# blueprints/admin/__init__.py
# Package initializer for the admin sub-blueprints.
#
# Exposes:
#   - Each sub-blueprint by name
#   - ADMIN_BLUEPRINTS — ordered tuple for registration
#   - register_admin_blueprints(app) — single-call registration
#
# Usage in app.py:
#     from blueprints.admin import register_admin_blueprints
#     register_admin_blueprints(app)
# ============================================================

import logging

# ── Core modular blueprints ──
from blueprints.admin._legacy_shim  import admin_shim_bp
from blueprints.admin.users_bp      import admin_users_bp
from blueprints.admin.access_bp     import admin_access_bp
from blueprints.admin.policy_bp     import admin_policy_bp
from blueprints.admin.content_bp    import admin_content_bp
from blueprints.admin.community_bp  import admin_community_bp
from blueprints.admin.revenue_bp    import upgrade_bp
from blueprints.admin.ops_bp        import admin_ops_bp
from blueprints.admin.system_bp     import admin_system_bp

# ── Moved specialist blueprints (originally at blueprints/admin_*.py) ──
from blueprints.admin.activity_bp   import admin_activity_bp
from blueprints.admin.backup_bp     import admin_backup_bp
from blueprints.admin.errors_bp     import admin_errors_bp
from blueprints.admin.platform_bp   import admin_platform_bp

logger = logging.getLogger(__name__)


# ============================================================
# REGISTRATION ORDER
# ============================================================
# The shim is registered FIRST so its endpoint name `admin.dashboard`
# is available to url_for() everywhere in the app. It sits at
# /admin/_legacy/dashboard, which never collides with real routes.
# ============================================================

ADMIN_BLUEPRINTS = (
    # legacy endpoint preservation — must be first
    admin_shim_bp,

    # core modular blueprints
    admin_users_bp,
    admin_access_bp,
    admin_policy_bp,
    admin_content_bp,
    admin_community_bp,
    upgrade_bp,
    admin_ops_bp,
    admin_system_bp,

    # specialist blueprints (moved from blueprints/admin_*.py)
    admin_activity_bp,
    admin_backup_bp,
    admin_errors_bp,
    admin_platform_bp,
)


def register_admin_blueprints(app):
    """
    Register every admin sub-blueprint on the given Flask app.

    Idempotent: if a blueprint is already registered under the same
    name, registration is skipped with a warning instead of raising.
    """
    already = set(app.blueprints.keys())

    registered = []
    skipped = []

    for bp in ADMIN_BLUEPRINTS:
        if bp.name in already:
            skipped.append(bp.name)
            logger.warning(
                f"Blueprint '{bp.name}' already registered. Skipping."
            )
            continue
        app.register_blueprint(bp)
        registered.append(bp.name)

    if registered:
        logger.info(
            f"Registered {len(registered)} admin blueprint(s): "
            f"{', '.join(registered)}"
        )
    if skipped:
        logger.info(
            f"Skipped {len(skipped)} already-registered blueprint(s): "
            f"{', '.join(skipped)}"
        )

    return registered


__all__ = [
    'admin_shim_bp',
    'admin_users_bp',
    'admin_access_bp',
    'admin_policy_bp',
    'admin_content_bp',
    'admin_community_bp',
    'upgrade_bp',
    'admin_ops_bp',
    'admin_system_bp',
    'admin_activity_bp',
    'admin_backup_bp',
    'admin_errors_bp',
    'admin_platform_bp',
    'ADMIN_BLUEPRINTS',
    'register_admin_blueprints',
]