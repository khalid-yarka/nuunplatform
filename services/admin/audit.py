# ============================================================
# services/admin/audit.py
# Unified audit trail.
#
# Every admin write goes through @admin_action, which:
#   1. Captures the "before" state (via a caller-provided callable)
#   2. Runs the handler
#   3. Captures the "after" state (via a caller-provided callable)
#   4. Writes one row to admin_audit_log
#
# The write is best-effort: if the audit insert fails, the
# handler's return value is still returned. The failure is
# logged. Business logic must not break because audit failed.
# ============================================================

import json
import logging
from functools import wraps
from typing import Callable, Optional, Any

from flask import request, session

from db import execute_with_retry
from services.admin.roles import current_actor_role

logger = logging.getLogger(__name__)


# ============================================================
# WRITE PRIMITIVE
# ============================================================

def _json_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        return json.dumps({'repr': repr(value)}, ensure_ascii=False)


def write_audit(
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    before: Any = None,
    after: Any = None,
    note: str = '',
    severity: str = 'info',
) -> bool:
    """
    Write one row to admin_audit_log.
    Returns True on success, False on failure (never raises).
    """
    try:
        actor_id = session.get('user_id')
        actor_role = current_actor_role()

        if actor_role == 'user':
            # Non-admin tried to write an audit row. Skip silently;
            # this indicates a programming error but should not
            # break the request.
            logger.warning(
                f"write_audit called with non-admin role for action={action}"
            )
            return False

        try:
            ip = request.remote_addr
            ua = request.headers.get('User-Agent', '')[:300]
        except RuntimeError:
            ip = None
            ua = ''

        execute_with_retry("""
            INSERT INTO admin_audit_log (
                actor_id, actor_role, actor_phone,
                action, target_type, target_id,
                before_value, after_value, note,
                ip_address, user_agent, severity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            actor_id,
            actor_role,
            session.get('user_phone'),
            action,
            target_type,
            target_id,
            _json_or_none(before),
            _json_or_none(after),
            note or '',
            ip,
            ua,
            severity if severity in ('info', 'warning', 'critical') else 'info',
        ), commit=True)
        return True
    except Exception as e:
        logger.error(f"Failed to write audit row for {action}: {e}")
        return False


# ============================================================
# DECORATOR
# ============================================================

def admin_action(
    action: str,
    target_type: Optional[str] = None,
    severity: str = 'info',
    capture_before: Optional[Callable[..., Any]] = None,
    capture_after: Optional[Callable[..., Any]] = None,
    target_id_kwarg: str = 'target_id',
):
    """
    Wrap a handler so that every successful call produces one
    audit row.

    Parameters
    ----------
    action : str
        Dot-separated verb, e.g. 'user.set_tier'.
    target_type : str, optional
        Entity type, e.g. 'user', 'question'.
    severity : str
        'info' | 'warning' | 'critical'.
    capture_before / capture_after : callable, optional
        Called with the same args/kwargs as the handler.
        Return value is JSON-serialized into the audit row.
    target_id_kwarg : str
        Name of the kwarg that holds the target's ID. Defaults to
        'target_id'. Falls back to 'user_id', 'question_id', etc.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            before = None
            if capture_before is not None:
                try:
                    before = capture_before(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"capture_before failed for {action}: {e}")

            result = f(*args, **kwargs)

            after = None
            if capture_after is not None:
                try:
                    after = capture_after(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"capture_after failed for {action}: {e}")

            # Try to extract a target_id from kwargs
            target_id = (
                kwargs.get(target_id_kwarg)
                or kwargs.get('user_id')
                or kwargs.get('question_id')
                or kwargs.get('pdf_id')
                or kwargs.get('group_id')
                or kwargs.get('report_id')
                or kwargs.get('error_id')
            )
            try:
                target_id = int(target_id) if target_id is not None else None
            except (TypeError, ValueError):
                target_id = None

            write_audit(
                action=action,
                target_type=target_type,
                target_id=target_id,
                before=before,
                after=after,
                severity=severity,
            )
            return result
        return wrapper
    return decorator


# ============================================================
# READERS
# ============================================================

def recent_audit(limit: int = 50, offset: int = 0) -> list[dict]:
    """Most recent audit rows across the whole platform."""
    try:
        rows = execute_with_retry("""
            SELECT a.*,
                   s.first_name AS actor_first_name,
                   s.last_name  AS actor_last_name,
                   s.public_id  AS actor_public_id
            FROM admin_audit_log a
            LEFT JOIN students s ON s.id = a.actor_id
            ORDER BY a.created_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"recent_audit failed: {e}")
        return []


def recent_audit_for_target(
    target_type: str,
    target_id: int,
    limit: int = 20,
) -> list[dict]:
    """Audit rows for a specific entity, most recent first."""
    try:
        rows = execute_with_retry("""
            SELECT a.*,
                   s.first_name AS actor_first_name,
                   s.last_name  AS actor_last_name,
                   s.public_id  AS actor_public_id
            FROM admin_audit_log a
            LEFT JOIN students s ON s.id = a.actor_id
            WHERE a.target_type = ? AND a.target_id = ?
            ORDER BY a.created_at DESC
            LIMIT ?
        """, (target_type, target_id, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"recent_audit_for_target failed: {e}")
        return []


def audit_stats(days: int = 30) -> dict:
    """Aggregate stats for the audit dashboard."""
    try:
        row = execute_with_retry("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END) AS critical,
                SUM(CASE WHEN severity = 'warning'  THEN 1 ELSE 0 END) AS warning,
                SUM(CASE WHEN severity = 'info'     THEN 1 ELSE 0 END) AS info,
                COUNT(DISTINCT actor_id) AS unique_actors
            FROM admin_audit_log
            WHERE created_at >= datetime('now', ? || ' days')
        """, (f'-{days}',)).fetchone()
        return dict(row) if row else {
            'total': 0, 'critical': 0, 'warning': 0, 'info': 0, 'unique_actors': 0
        }
    except Exception as e:
        logger.error(f"audit_stats failed: {e}")
        return {'total': 0, 'critical': 0, 'warning': 0, 'info': 0, 'unique_actors': 0}