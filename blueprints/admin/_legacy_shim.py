# ============================================================
# blueprints/admin/_legacy_shim.py
# Backwards-compatibility shim.
#
# Templates outside the admin folder (dashboard_base.html,
# dashboard/home.html, base.html, etc.) still call
# url_for('admin.dashboard'). That endpoint belonged to the
# retired blueprints/admin_bp.py.
#
# This registers a tiny blueprint named 'admin' whose only job
# is to keep that endpoint name alive. It redirects to the real
# admin landing page (admin_system.dashboard).
#
# Nothing outside this file needs to change.
# ============================================================

from flask import Blueprint, redirect, url_for

admin_shim_bp = Blueprint(
    'admin',                 # preserves url_for('admin.*')
    __name__,
    url_prefix='/admin/_legacy',
)


@admin_shim_bp.route('/dashboard', endpoint='dashboard')
def dashboard():
    """Legacy endpoint. Redirects to the real admin home."""
    return redirect(url_for('admin_system.dashboard'))


# If other legacy endpoints are ever needed, add them here as
# redirects to their modular equivalents. Keeping them all in
# one place makes the compatibility surface easy to audit.