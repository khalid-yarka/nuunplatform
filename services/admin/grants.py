# ============================================================
# services/admin/grants.py
# Cache and version management for capability grants
# and per-admin overrides.
#
# Architecture:
#   Level 1 — process-memory caches (_global_cache, _per_admin_cache)
#   Level 2 — SQLite tables (grants, overrides, version)
#
# Invalidation:
#   A single version counter in the DB is read on every request.
#   If the version differs from cached, the cache reloads.
#   A 300-second TTL is a fallback safety net.
# ============================================================

import time
import threading
import logging
from typing import Optional

from db import execute_with_retry
from services.admin.registry import REGISTRY_KEYS, WRITABLE_KEYS

logger = logging.getLogger(__name__)


# ============================================================
# CACHE STATE
# ============================================================

_lock = threading.RLock()

_global_cache = {
    'version': 0,
    'enabled': frozenset(),
    'fetched_at': 0.0,
}

_per_admin_cache: dict[int, dict] = {}

_TTL = 300.0  # seconds


# ============================================================
# DB READS
# ============================================================

def _read_version() -> int:
    try:
        row = execute_with_retry(
            "SELECT version FROM admin_capability_version WHERE id = 1"
        ).fetchone()
        return int(row['version']) if row else 1
    except Exception as e:
        logger.warning(f"Could not read capability version: {e}")
        return 0


def _read_global_enabled() -> frozenset[str]:
    try:
        rows = execute_with_retry(
            "SELECT capability_key FROM admin_capability_grants WHERE is_enabled = 1"
        ).fetchall()
        # Filter stale keys (removed from registry)
        return frozenset(
            r['capability_key'] for r in rows
            if r['capability_key'] in REGISTRY_KEYS
        )
    except Exception as e:
        logger.warning(f"Could not read global grants: {e}")
        return frozenset()


def _read_admin_overrides(admin_id: int) -> dict[str, bool]:
    try:
        rows = execute_with_retry(
            "SELECT capability_key, is_enabled FROM admin_capability_overrides "
            "WHERE admin_id = ?",
            (admin_id,)
        ).fetchall()
        return {
            r['capability_key']: bool(r['is_enabled'])
            for r in rows
            if r['capability_key'] in REGISTRY_KEYS
        }
    except Exception as e:
        logger.warning(f"Could not read overrides for admin {admin_id}: {e}")
        return {}


# ============================================================
# CACHE REFRESH
# ============================================================

def _refresh_global_if_needed(current_version: int, now: float):
    if (
        current_version == _global_cache['version']
        and (now - _global_cache['fetched_at']) < _TTL
    ):
        return
    _global_cache['enabled'] = _read_global_enabled()
    _global_cache['version'] = current_version
    _global_cache['fetched_at'] = now


def _refresh_admin_if_needed(admin_id: int, current_version: int, now: float):
    bucket = _per_admin_cache.get(admin_id)
    if (
        bucket
        and bucket['version'] == current_version
        and (now - bucket['fetched_at']) < _TTL
    ):
        return
    _per_admin_cache[admin_id] = {
        'version': current_version,
        'overrides': _read_admin_overrides(admin_id),
        'fetched_at': now,
    }


# ============================================================
# CACHE INVALIDATION
# ============================================================

def invalidate_capability_cache():
    """Clear all caches. Called after any write to grants or overrides."""
    with _lock:
        _global_cache['version'] = 0
        _global_cache['enabled'] = frozenset()
        _global_cache['fetched_at'] = 0.0
        _per_admin_cache.clear()
    logger.debug("Capability cache invalidated.")


def bump_capability_version():
    """
    Increment the DB version. Must be called in the same logical
    operation as the write. Callers typically do:
        write_grant()
        bump_capability_version()
        invalidate_capability_cache()
    """
    try:
        execute_with_retry(
            "UPDATE admin_capability_version "
            "SET version = version + 1 WHERE id = 1",
            commit=True
        )
    except Exception as e:
        logger.error(f"Failed to bump capability version: {e}")


# ============================================================
# PUBLIC READERS
# ============================================================

def get_cached_version() -> int:
    return _read_version()


def get_global_grant(capability_key: str) -> Optional[bool]:
    """Return the global enabled state for a capability, or None if unknown."""
    if capability_key not in REGISTRY_KEYS:
        return None
    now = time.monotonic()
    version = _read_version()
    with _lock:
        _refresh_global_if_needed(version, now)
        return capability_key in _global_cache['enabled']


def get_effective_override(admin_id: int, capability_key: str) -> Optional[bool]:
    """
    Return the override state for (admin, capability).
    None means no override exists (inherit global).
    """
    if capability_key not in REGISTRY_KEYS:
        return None
    now = time.monotonic()
    version = _read_version()
    with _lock:
        _refresh_admin_if_needed(admin_id, version, now)
        bucket = _per_admin_cache.get(admin_id)
        if not bucket:
            return None
        return bucket['overrides'].get(capability_key)


def list_global_grants() -> dict[str, bool]:
    """Return {capability_key: is_enabled} for every known capability."""
    now = time.monotonic()
    version = _read_version()
    with _lock:
        _refresh_global_if_needed(version, now)
        enabled = _global_cache['enabled']
        return {k: (k in enabled) for k in REGISTRY_KEYS}


def list_admin_overrides(admin_id: int) -> dict[str, bool]:
    """Return {capability_key: is_enabled} for all overrides of one admin."""
    now = time.monotonic()
    version = _read_version()
    with _lock:
        _refresh_admin_if_needed(admin_id, version, now)
        bucket = _per_admin_cache.get(admin_id)
        return dict(bucket['overrides']) if bucket else {}


def list_capability_overrides(capability_key: str) -> list[dict]:
    """
    Return every admin who has an override for a capability.
    Each entry: {admin_id, is_enabled, first_name, last_name, public_id}.
    Used by the override chip popover.
    """
    if capability_key not in REGISTRY_KEYS:
        return []
    try:
        rows = execute_with_retry("""
            SELECT o.admin_id, o.is_enabled,
                   s.first_name, s.last_name, s.public_id
            FROM admin_capability_overrides o
            JOIN students s ON s.id = o.admin_id
            WHERE o.capability_key = ?
            ORDER BY s.first_name, s.last_name
        """, (capability_key,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Could not list overrides for {capability_key}: {e}")
        return []


# ============================================================
# WRITERS (used by the permission management pages)
# ============================================================

def set_global_grant(capability_key: str, is_enabled: bool,
                     actor_id: int) -> bool:
    """Set the global enabled state for a capability."""
    if capability_key not in WRITABLE_KEYS:
        logger.warning(f"Refusing to write reserved/unknown capability: {capability_key}")
        return False
    try:
        execute_with_retry("""
            INSERT INTO admin_capability_grants
                (capability_key, is_enabled, updated_by, updated_at)
            VALUES (?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(capability_key) DO UPDATE SET
                is_enabled = excluded.is_enabled,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
        """, (capability_key, 1 if is_enabled else 0, actor_id), commit=True)
        return True
    except Exception as e:
        logger.error(f"Failed to set global grant {capability_key}: {e}")
        return False


def set_admin_override(admin_id: int, capability_key: str,
                       is_enabled: Optional[bool],
                       actor_id: int, note: str = '') -> bool:
    """
    Set or clear an override for (admin, capability).
    is_enabled = True  → insert/update with 1
    is_enabled = False → insert/update with 0
    is_enabled = None  → delete the row (revert to inherit)
    """
    if capability_key not in WRITABLE_KEYS:
        logger.warning(f"Refusing to write reserved/unknown capability: {capability_key}")
        return False
    try:
        if is_enabled is None:
            execute_with_retry(
                "DELETE FROM admin_capability_overrides "
                "WHERE admin_id = ? AND capability_key = ?",
                (admin_id, capability_key), commit=True
            )
        else:
            execute_with_retry("""
                INSERT INTO admin_capability_overrides
                    (admin_id, capability_key, is_enabled, granted_by,
                     granted_at, note)
                VALUES (?, ?, ?, ?, datetime('now', 'localtime'), ?)
                ON CONFLICT(admin_id, capability_key) DO UPDATE SET
                    is_enabled = excluded.is_enabled,
                    granted_by = excluded.granted_by,
                    granted_at = excluded.granted_at,
                    note = excluded.note
            """, (
                admin_id, capability_key,
                1 if is_enabled else 0,
                actor_id, note or ''
            ), commit=True)
        return True
    except Exception as e:
        logger.error(f"Failed to set override {admin_id}/{capability_key}: {e}")
        return False


def clear_admin_overrides(admin_id: int, actor_id: int) -> int:
    """Delete every override for one admin. Returns the count deleted."""
    try:
        cursor = execute_with_retry(
            "DELETE FROM admin_capability_overrides WHERE admin_id = ?",
            (admin_id,), commit=True
        )
        return cursor.rowcount or 0
    except Exception as e:
        logger.error(f"Failed to clear overrides for admin {admin_id}: {e}")
        return 0