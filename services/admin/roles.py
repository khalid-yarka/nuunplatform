# ============================================================
# services/admin/roles.py
# Role detection: super admin, admin, non-admin.
#
# Super admin is derived from SUPER_ADMIN_PHONE on every request.
# Never cached in the session. Never stored in the DB.
# ============================================================

import re
import logging

from flask import session
from config import Config

logger = logging.getLogger(__name__)


# ============================================================
# PHONE NORMALIZATION
# ============================================================

def normalize_phone(raw) -> str:
    """
    Strip to a 9-digit national number.

    All of these normalize to '612345678':
        +252612345678
        252612345678
        0612345678
        612345678
        +252 61 234 5678
        +252-61-234-5678
    """
    if not raw:
        return ''
    digits = re.sub(r'\D', '', str(raw))
    if digits.startswith('252'):
        digits = digits[3:]
    if digits.startswith('0'):
        digits = digits[1:]
    return digits


# Resolve once at import time. The env value cannot change while
# the process is running, so this is safe.
_ENV_SUPER = normalize_phone(Config.SUPER_ADMIN_PHONE)

# Emit a startup warning if the env is set but no user will match.
# The actual user check happens on first request (below).
if not _ENV_SUPER:
    logger.info("SUPER_ADMIN_PHONE is not set. No super admin will be available.")
else:
    logger.info(f"Super admin phone configured (normalized length: {len(_ENV_SUPER)}).")


# ============================================================
# ROLE CHECKS
# ============================================================

def is_super_admin() -> bool:
    """True only if the current session's phone matches the env var."""
    if not _ENV_SUPER:
        return False
    session_phone = session.get('user_phone')
    if not session_phone:
        return False
    return normalize_phone(session_phone) == _ENV_SUPER


def is_admin() -> bool:
    """True if the current session is a regular admin (is_admin = 1)."""
    return bool(session.get('is_admin'))


def is_any_admin() -> bool:
    """True if the current session is any kind of admin."""
    return is_super_admin() or is_admin()


def current_actor_role() -> str:
    """
    Return 'super', 'admin', or 'user'.
    Used by the audit writer to label rows.
    """
    if is_super_admin():
        return 'super'
    if is_admin():
        return 'admin'
    return 'user'


# ============================================================
# STARTUP DIAGNOSTICS
# ============================================================

def diagnose_super_admin() -> list[str]:
    """
    Called at startup. Returns a list of warning strings.
    Never raises. Never blocks startup.
    """
    warnings = []

    if not _ENV_SUPER:
        try:
            from db import execute_with_retry
            row = execute_with_retry(
                "SELECT COUNT(*) AS c FROM students WHERE is_admin = 1"
            ).fetchone()
            admin_count = row['c'] if row else 0
            if admin_count > 0:
                warnings.append(
                    f"SUPER_ADMIN_PHONE is not set. {admin_count} admin(s) "
                    f"exist, but no user will have advanced capabilities."
                )
        except Exception as e:
            warnings.append(f"Could not verify admin count: {e}")
        return warnings

    # Env is set. Check whether any user matches.
    try:
        from db import execute_with_retry
        # Match by suffix so any format in the DB works.
        row = execute_with_retry(
            "SELECT id, phone_number FROM students "
            "WHERE REPLACE(REPLACE(REPLACE(phone_number, '+', ''), '-', ''), ' ', '') "
            "LIKE ?",
            (f'%{_ENV_SUPER}',)
        ).fetchone()
        if not row:
            warnings.append(
                "SUPER_ADMIN_PHONE is set, but no registered user matches it. "
                "Super admin access will be impossible."
            )
    except Exception as e:
        warnings.append(f"Could not verify super admin phone: {e}")

    return warnings