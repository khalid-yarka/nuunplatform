# ============================================================
# blueprints/admin/safety_bp.py
# Super-admin viewer for the safety shadow database.
#
# The mirror is populated automatically by safe_db.py on every
# INSERT/UPDATE that flows through db.execute_with_retry /
# execute_many_with_retry. This blueprint is strictly read-only.
#
# Routes:
#   GET /admin/safety                 → overview + table picker
#   GET /admin/safety/table/<name>    → paginated rows + search
# ============================================================

from flask import (
    Blueprint, render_template, request, abort,
)
import logging

from safe_db import (
    stats as safety_stats,
    list_tables as safety_list_tables,
    table_columns as safety_table_columns,
    table_rows as safety_table_rows,
    MIRROR_TABLES,
)
from services.admin.guards import admin_can

logger = logging.getLogger(__name__)

admin_safety_bp = Blueprint(
    'admin_safety',
    __name__,
    url_prefix='/admin/safety',
)


# ============================================================
# OVERVIEW
# ============================================================

@admin_safety_bp.route('/', methods=['GET'], endpoint='index')
@admin_can('safety.view')
def index():
    """
    Landing page: shows mirror health, table list, and row counts.
    The actual row viewer lives at /table/<name>.
    """
    overview = safety_stats()          # {path, exists, size_mb, tables, total_rows}
    tables   = overview.get('tables', [])

    return render_template(
        'dashboard/admin/ops/safety.html',
        overview=overview,
        tables=tables,
        active_table=None,
        columns=[],
        rows=[],
        total_rows=0,
        page=1,
        total_pages=1,
        search='',
        per_page=50,
    )


# ============================================================
# TABLE VIEWER
# ============================================================

@admin_safety_bp.route('/table/<table_name>', methods=['GET'],
                       endpoint='table')
@admin_can('safety.view')
def table(table_name):
    """
    Paginated, searchable read-only view of one mirrored table.
    Only tables in safe_db.MIRROR_TABLES are reachable.
    """
    if table_name not in MIRROR_TABLES:
        abort(404)

    try:
        page = max(1, int(request.args.get('page') or 1))
    except (TypeError, ValueError):
        page = 1

    try:
        per_page = int(request.args.get('per_page') or 50)
    except (TypeError, ValueError):
        per_page = 50
    per_page = max(10, min(per_page, 200))

    search = (request.args.get('search') or '').strip()

    columns = safety_table_columns(table_name)
    rows, total = safety_table_rows(
        table_name,
        limit=per_page,
        offset=(page - 1) * per_page,
        search=search,
    )
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    overview = safety_stats()
    tables   = overview.get('tables', [])

    return render_template(
        'dashboard/admin/ops/safety.html',
        overview=overview,
        tables=tables,
        active_table=table_name,
        columns=columns,
        rows=rows,
        total_rows=total,
        page=page,
        total_pages=total_pages,
        search=search,
        per_page=per_page,
    )