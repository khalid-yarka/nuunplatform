# services/entitlement_service.py
# ---------------------------------------------------------------
# Entitlement service — single source of truth for what each tier
# is allowed to do.
#
# Design:
#   - POLICY  → process-memory cache (loaded once per process)
#   - TIER    → session (fallback to DB), auto-normalized
#   - USAGE   → DB (user_usage table), only for quota features
#
# Read cost per request:
#   - permission / level / content: 0 DB queries
#   - quota check: 1 SELECT on user_usage
#   - quota consume: 1 atomic INSERT/UPDATE
#
# Every admin write goes through:
#   1. Validate
#   2. Write to DB (in a transaction)
#   3. Insert audit row
#   4. Invalidate the cache
# ---------------------------------------------------------------

import os
import json
import time
import logging
import threading
import sqlite3
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import session

from config import Config
from db import execute_with_retry, get_db, get_student_by_id
from utils import get_somali_time_db, SOMALI_TIMEZONE
from tier_config import normalize_tier

logger = logging.getLogger(__name__)


# ============================================
# CONSTANTS
# ============================================

VALID_TIERS = ('free', 'premium', 'pro')
VALID_POLICY_TYPES = ('permission', 'level', 'quota', 'content')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_STATE_FLAG = os.path.join(BASE_DIR, 'instance', 'user_state_changes.flag')


# ============================================
# PROCESS-MEMORY POLICY CACHE
# ============================================

_POLICY: Optional[Dict[str, Dict[str, Any]]] = None
_POLICY_LOCK = threading.RLock()


def _load_policy() -> Dict[str, Dict[str, Any]]:
    """Read every feature + policy from the DB into a keyed dict."""
    cache: Dict[str, Dict[str, Any]] = {}
    try:
        conn = get_db()
        cursor = conn.execute("""
            SELECT id, feature_key, display_name, description, category,
                   policy_type, unit_hint, is_global_active, sort_order, notes
            FROM entitlement_features
        """)
        features = cursor.fetchall()

        for f in features:
            feature_id = f['id']
            feature_key = f['feature_key']

            cursor2 = conn.execute("""
                SELECT tier, is_enabled, level_value, limit_value, limit_unit
                FROM entitlement_policies
                WHERE feature_id = ?
            """, (feature_id,))
            policies: Dict[str, Dict[str, Any]] = {}
            for p in cursor2.fetchall():
                policies[p['tier']] = {
                    'is_enabled': bool(p['is_enabled']),
                    'level_value': p['level_value'],
                    'limit_value': p['limit_value'],
                    'limit_unit': p['limit_unit'],
                }

            cache[feature_key] = {
                'id': feature_id,
                'feature_key': feature_key,
                'display_name': f['display_name'],
                'description': f['description'],
                'category': f['category'],
                'policy_type': f['policy_type'],
                'unit_hint': f['unit_hint'],
                'is_global_active': bool(f['is_global_active']),
                'sort_order': f['sort_order'],
                'notes': f['notes'],
                'policies': policies,
            }

        return cache
    except Exception as e:
        logger.error(f"Failed to load entitlement policy: {e}", exc_info=True)
        return {}


def get_policy() -> Dict[str, Dict[str, Any]]:
    """Return the process-memory policy cache, loading it on first access."""
    global _POLICY
    if _POLICY is None:
        with _POLICY_LOCK:
            if _POLICY is None:
                _POLICY = _load_policy()
                logger.info(f"Entitlement policy cache loaded ({len(_POLICY)} features)")
    return _POLICY


def invalidate_policy_cache() -> None:
    """Drop the cache. Next get_policy() call rebuilds it from the DB."""
    global _POLICY
    with _POLICY_LOCK:
        _POLICY = None
    logger.info("Entitlement policy cache invalidated")


def reload_policy_cache() -> None:
    """Force an immediate rebuild (used by admin writes)."""
    global _POLICY
    with _POLICY_LOCK:
        _POLICY = _load_policy()
    logger.info(f"Entitlement policy cache reloaded ({len(_POLICY)} features)")


# ============================================
# TIER RESOLUTION
# ============================================

def _resolve_tier_from_session(user_id: Optional[int]) -> Optional[str]:
    """Return session tier if it belongs to user_id, else None."""
    try:
        sid = session.get('user_id')
        if sid is None:
            return None
        if user_id is not None and sid != user_id:
            return None
        tier = session.get('tier')
        if tier:
            return normalize_tier(tier)
    except Exception:
        pass
    return None


def _resolve_tier_from_db(user_id: int) -> str:
    try:
        student = get_student_by_id(user_id)
        if student:
            return normalize_tier(student.get('tier', 'free'))
    except Exception as e:
        logger.warning(f"Tier DB lookup failed for user {user_id}: {e}")
    return 'free'


def _get_expires_at(user_id: Optional[int]) -> Optional[str]:
    """Return tier_expires_at for a user (session, else DB)."""
    try:
        sid = session.get('user_id')
        if sid is not None and (user_id is None or sid == user_id):
            exp = session.get('tier_expires_at')
            if exp:
                return exp
    except Exception:
        pass
    if user_id is None:
        return None
    try:
        student = get_student_by_id(user_id)
        if student:
            return student.get('tier_expires_at')
    except Exception:
        pass
    return None


def _effective_tier(user_id: Optional[int]) -> str:
    """
    Return the user's effective tier, treating expired paid tiers as free.
    Does not write to the DB — expiry is applied by tier_service.expire_tier().
    """
    tier = _resolve_tier_from_session(user_id)
    if tier is None:
        if user_id is None:
            try:
                user_id = session.get('user_id')
            except Exception:
                user_id = None
        if user_id is None:
            return 'free'
        tier = _resolve_tier_from_db(user_id)

    if tier == 'free':
        return 'free'

    expires = _get_expires_at(user_id)
    if not expires:
        return tier

    try:
        dt = datetime.fromisoformat(str(expires).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SOMALI_TIMEZONE)
        if dt < datetime.now(SOMALI_TIMEZONE):
            return 'free'
    except (ValueError, TypeError):
        pass
    return tier


def get_user_tier(user_id: Optional[int] = None) -> str:
    """Public helper. Returns the effective tier for a user."""
    return _effective_tier(user_id)


# ============================================
# USAGE HELPERS (quota only)
# ============================================

def _usage_today(user_id: int, metric_code: str) -> int:
    """Return today's usage count for a metric."""
    try:
        today = date.today().isoformat()
        cursor = execute_with_retry(
            "SELECT usage_count FROM user_usage "
            "WHERE user_id = ? AND metric_code = ? AND period_start = ?",
            (user_id, metric_code, today),
        )
        row = cursor.fetchone()
        return int(row['usage_count']) if row else 0
    except Exception as e:
        logger.warning(f"Usage lookup failed ({metric_code}): {e}")
        return 0


# ============================================
# READ API
# ============================================

def check(user_id: Optional[int], feature_key: str) -> bool:
    """
    Return True if the user is allowed to use this feature.
    For quota features, returns False once the daily limit is reached.
    """
    policy = get_policy().get(feature_key)
    if not policy or not policy.get('is_global_active'):
        return False

    tier = _effective_tier(user_id)
    tp = policy['policies'].get(tier)
    if not tp or not tp['is_enabled']:
        return False

    if policy['policy_type'] == 'quota':
        limit = tp['limit_value']
        if limit is None:
            return True
        # Need a concrete user_id to count usage
        uid = user_id
        if uid is None:
            try:
                uid = session.get('user_id')
            except Exception:
                uid = None
        if uid is None:
            return False
        return _usage_today(uid, feature_key) < limit

    return True


def get_level(user_id: Optional[int], feature_key: str) -> int:
    """Return the numeric level for a level feature (0 if unavailable)."""
    policy = get_policy().get(feature_key)
    if not policy or not policy.get('is_global_active'):
        return 0
    tier = _effective_tier(user_id)
    tp = policy['policies'].get(tier)
    if not tp or not tp['is_enabled']:
        return 0
    return int(tp['level_value'] or 0)


def get_limit(user_id: Optional[int], feature_key: str) -> Optional[int]:
    """Return the numeric limit for a quota feature. None = unlimited."""
    policy = get_policy().get(feature_key)
    if not policy or not policy.get('is_global_active'):
        return 0
    tier = _effective_tier(user_id)
    tp = policy['policies'].get(tier)
    if not tp or not tp['is_enabled']:
        return 0
    return tp['limit_value']


def get_remaining(user_id: Optional[int], feature_key: str) -> Optional[int]:
    """
    Return remaining usage for a quota feature.
    None  = unlimited
    int   = remaining count (0 if disabled or exhausted)
    Non-quota features return None.
    """
    policy = get_policy().get(feature_key)
    if not policy:
        return 0
    if policy['policy_type'] != 'quota':
        return None

    tier = _effective_tier(user_id)
    tp = policy['policies'].get(tier)
    if not tp or not tp['is_enabled']:
        return 0

    limit = tp['limit_value']
    if limit is None:
        return None

    uid = user_id
    if uid is None:
        try:
            uid = session.get('user_id')
        except Exception:
            uid = None
    if uid is None:
        return 0

    used = _usage_today(uid, feature_key)
    return max(0, limit - used)


def consume(user_id: Optional[int], feature_key: str) -> bool:
    """
    Atomically consume one unit of a quota feature.
    For non-quota features, this is a no-op that returns True if allowed.
    """
    policy = get_policy().get(feature_key)
    if not policy or not policy.get('is_global_active'):
        return False

    tier = _effective_tier(user_id)
    tp = policy['policies'].get(tier)
    if not tp or not tp['is_enabled']:
        return False

    if policy['policy_type'] != 'quota':
        return True

    limit = tp['limit_value']
    if limit is None:
        return True

    uid = user_id
    if uid is None:
        try:
            uid = session.get('user_id')
        except Exception:
            uid = None
    if uid is None:
        return False

    today = date.today().isoformat()
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_usage
                (user_id, metric_code, period_start, usage_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, metric_code, period_start) DO UPDATE SET
                usage_count = usage_count + 1,
                updated_at = datetime('now', 'localtime')
            WHERE usage_count < ?
        """, (uid, feature_key, today, limit))
        if cursor.rowcount > 0:
            conn.commit()
            return True
        conn.rollback()
        return False
    except Exception as e:
        logger.error(f"consume failed for {feature_key}: {e}")
        return False


# ============================================
# USER-STATE REFRESH
# ============================================

def refresh_user(user_id: int) -> bool:
    """
    Signal that user_id's tier/state may have changed.
    Touches the shared user_state_changes.flag file; app.py's
    before_request hook will pick up the change on the user's next request.
    """
    try:
        os.makedirs(os.path.dirname(USER_STATE_FLAG), exist_ok=True)
        with open(USER_STATE_FLAG, 'w') as f:
            f.write(str(time.time()))
        return True
    except Exception as e:
        logger.error(f"refresh_user failed: {e}")
        return False


# ============================================
# AUDIT LOGGING
# ============================================

def _audit(admin_id: int, action: str, feature_id: Optional[int] = None,
           feature_key: Optional[str] = None, tier: Optional[str] = None,
           old_value: Any = None, new_value: Any = None, reason: str = '') -> None:
    try:
        execute_with_retry("""
            INSERT INTO entitlement_audit
                (admin_id, action, feature_id, feature_key, tier,
                 old_value, new_value, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            admin_id, action, feature_id, feature_key, tier,
            json.dumps(old_value) if old_value is not None else None,
            json.dumps(new_value) if new_value is not None else None,
            reason or '',
            get_somali_time_db(),
        ), commit=True)
    except Exception as e:
        logger.error(f"Audit insert failed ({action} / {feature_key}): {e}")


# ============================================
# ADMIN API — READ
# ============================================

def list_features() -> List[Dict[str, Any]]:
    """Return every feature with its policies, sorted by sort_order."""
    features = list(get_policy().values())
    features.sort(key=lambda f: (f['category'], f['sort_order'], f['feature_key']))
    return features


def get_feature(feature_key: str) -> Optional[Dict[str, Any]]:
    return get_policy().get(feature_key)


def list_categories() -> List[str]:
    cats = sorted({f['category'] for f in get_policy().values()})
    return cats


# ============================================
# ADMIN API — VALIDATION
# ============================================

def _validate_feature_data(data: Dict[str, Any], require_key: bool = True) -> Tuple[bool, str]:
    if require_key:
        key = (data.get('feature_key') or '').strip()
        if not key or not key.replace('_', '').isalnum():
            return False, "feature_key must be alphanumeric with underscores"
    if not (data.get('display_name') or '').strip():
        return False, "display_name is required"
    if not (data.get('category') or '').strip():
        return False, "category is required"
    if data.get('policy_type') not in VALID_POLICY_TYPES:
        return False, f"policy_type must be one of {VALID_POLICY_TYPES}"
    return True, ''


def _validate_policy_update(policy_type: str, changes: Dict[str, Any]) -> Tuple[bool, str]:
    if 'is_enabled' in changes and not isinstance(changes['is_enabled'], (bool, int)):
        return False, "is_enabled must be boolean"
    if 'level_value' in changes and changes['level_value'] is not None:
        try:
            v = int(changes['level_value'])
            if v < 0 or v > 3:
                return False, "level_value must be between 0 and 3"
        except (ValueError, TypeError):
            return False, "level_value must be an integer or null"
    if 'limit_value' in changes and changes['limit_value'] is not None:
        try:
            v = int(changes['limit_value'])
            if v < 0:
                return False, "limit_value must be >= 0 or null"
        except (ValueError, TypeError):
            return False, "limit_value must be an integer or null"
    return True, ''


# ============================================
# ADMIN API — WRITE
# ============================================

def create_feature(admin_id: int, data: Dict[str, Any], reason: str = '') -> Tuple[bool, str]:
    """
    Create a new feature and its three per-tier policies.
    data: {
        feature_key, display_name, description, category, policy_type,
        unit_hint, sort_order, notes,
        policies: {
            free:    {is_enabled, level_value?, limit_value?, limit_unit?},
            premium: {...},
            pro:     {...}
        }
    }
    """
    ok, err = _validate_feature_data(data, require_key=True)
    if not ok:
        return False, err

    key = data['feature_key'].strip()

    # Reject duplicates
    if get_policy().get(key):
        return False, f"Feature '{key}' already exists"

    policies = data.get('policies') or {}
    for tier in VALID_TIERS:
        if tier not in policies:
            return False, f"Missing policy for tier '{tier}'"

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO entitlement_features
                (feature_key, display_name, description, category,
                 policy_type, unit_hint, is_global_active, sort_order, notes)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (
            key,
            data['display_name'].strip(),
            (data.get('description') or '').strip(),
            data['category'].strip(),
            data['policy_type'],
            data.get('unit_hint'),
            int(data.get('sort_order') or 0),
            (data.get('notes') or '').strip(),
        ))
        feature_id = cursor.lastrowid

        for tier in VALID_TIERS:
            p = policies[tier]
            cursor.execute("""
                INSERT INTO entitlement_policies
                    (feature_id, tier, is_enabled, level_value, limit_value, limit_unit)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                feature_id, tier,
                1 if p.get('is_enabled') else 0,
                p.get('level_value'),
                p.get('limit_value'),
                p.get('limit_unit'),
            ))
        conn.commit()

        _audit(admin_id, 'feature_create', feature_id, key,
               old_value=None, new_value={
                   'display_name': data['display_name'],
                   'category': data['category'],
                   'policy_type': data['policy_type'],
                   'policies': policies,
               }, reason=reason)

        reload_policy_cache()
        return True, f"Feature '{key}' created"
    except sqlite3.IntegrityError as e:
        logger.warning(f"create_feature integrity: {e}")
        return False, f"Integrity error: {e}"
    except Exception as e:
        logger.error(f"create_feature failed: {e}", exc_info=True)
        return False, f"Failed to create feature: {e}"


def update_feature(admin_id: int, feature_key: str, changes: Dict[str, Any],
                   reason: str = '') -> Tuple[bool, str]:
    """
    Update a feature's metadata (not its policies — use update_policy for those).
    Allowed keys: display_name, description, category, unit_hint, sort_order, notes
    """
    existing = get_policy().get(feature_key)
    if not existing:
        return False, f"Feature '{feature_key}' not found"

    allowed = {'display_name', 'description', 'category', 'unit_hint', 'sort_order', 'notes'}
    updates = {k: v for k, v in changes.items() if k in allowed}
    if not updates:
        return False, "No valid fields to update"

    if 'display_name' in updates and not (updates['display_name'] or '').strip():
        return False, "display_name cannot be empty"
    if 'category' in updates and not (updates['category'] or '').strip():
        return False, "category cannot be empty"

    try:
        fields = []
        params = []
        for k, v in updates.items():
            fields.append(f"{k} = ?")
            if isinstance(v, str):
                params.append(v.strip())
            else:
                params.append(v)
        fields.append("updated_at = ?")
        params.append(get_somali_time_db())
        params.append(existing['id'])

        conn = get_db()
        conn.execute(
            f"UPDATE entitlement_features SET {', '.join(fields)} WHERE id = ?",
            params
        )
        conn.commit()

        old_snapshot = {k: existing.get(k) for k in updates.keys()}
        new_snapshot = {k: (v.strip() if isinstance(v, str) else v) for k, v in updates.items()}

        _audit(admin_id, 'feature_update', existing['id'], feature_key,
               old_value=old_snapshot, new_value=new_snapshot, reason=reason)

        reload_policy_cache()
        return True, f"Feature '{feature_key}' updated"
    except Exception as e:
        logger.error(f"update_feature failed: {e}", exc_info=True)
        return False, f"Failed to update feature: {e}"


def delete_feature(admin_id: int, feature_key: str, reason: str = '') -> Tuple[bool, str]:
    """Delete a feature and all its policies (CASCADE)."""
    existing = get_policy().get(feature_key)
    if not existing:
        return False, f"Feature '{feature_key}' not found"

    try:
        conn = get_db()
        conn.execute("DELETE FROM entitlement_features WHERE id = ?", (existing['id'],))
        conn.commit()

        _audit(admin_id, 'feature_delete', existing['id'], feature_key,
               old_value={
                   'display_name': existing['display_name'],
                   'category': existing['category'],
                   'policy_type': existing['policy_type'],
                   'policies': existing['policies'],
               }, new_value=None, reason=reason)

        reload_policy_cache()
        return True, f"Feature '{feature_key}' deleted"
    except Exception as e:
        logger.error(f"delete_feature failed: {e}", exc_info=True)
        return False, f"Failed to delete feature: {e}"


def update_policy(admin_id: int, feature_key: str, tier: str,
                  changes: Dict[str, Any], reason: str = '') -> Tuple[bool, str]:
    """
    Update one tier's policy for a feature.
    Allowed keys: is_enabled, level_value, limit_value, limit_unit
    """
    if tier not in VALID_TIERS:
        return False, f"Invalid tier '{tier}'"

    existing = get_policy().get(feature_key)
    if not existing:
        return False, f"Feature '{feature_key}' not found"

    old_policy = existing['policies'].get(tier, {})

    allowed = {'is_enabled', 'level_value', 'limit_value', 'limit_unit'}
    updates = {k: v for k, v in changes.items() if k in allowed}
    if not updates:
        return False, "No valid policy fields to update"

    ok, err = _validate_policy_update(existing['policy_type'], updates)
    if not ok:
        return False, err

    try:
        # Normalize is_enabled to int
        if 'is_enabled' in updates:
            updates['is_enabled'] = 1 if updates['is_enabled'] else 0

        fields = []
        params = []
        for k, v in updates.items():
            fields.append(f"{k} = ?")
            params.append(v)
        fields.append("updated_at = ?")
        params.append(get_somali_time_db())
        params.extend([existing['id'], tier])

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE entitlement_policies SET {', '.join(fields)} "
            f"WHERE feature_id = ? AND tier = ?",
            params
        )
        if cursor.rowcount == 0:
            # No policy row existed — insert one
            cursor.execute("""
                INSERT INTO entitlement_policies
                    (feature_id, tier, is_enabled, level_value, limit_value, limit_unit)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                existing['id'], tier,
                updates.get('is_enabled', 1),
                updates.get('level_value'),
                updates.get('limit_value'),
                updates.get('limit_unit'),
            ))
        conn.commit()

        _audit(admin_id, 'policy_update', existing['id'], feature_key, tier=tier,
               old_value=old_policy, new_value={**old_policy, **updates},
               reason=reason)

        reload_policy_cache()
        return True, f"Policy updated for '{feature_key}' / {tier}"
    except Exception as e:
        logger.error(f"update_policy failed: {e}", exc_info=True)
        return False, f"Failed to update policy: {e}"


def toggle_feature_active(admin_id: int, feature_key: str, active: bool,
                          reason: str = '') -> Tuple[bool, str]:
    """Enable or disable a feature globally across all tiers."""
    existing = get_policy().get(feature_key)
    if not existing:
        return False, f"Feature '{feature_key}' not found"

    old = existing['is_global_active']
    new = bool(active)
    if old == new:
        return True, "No change"

    try:
        conn = get_db()
        conn.execute(
            "UPDATE entitlement_features SET is_global_active = ?, updated_at = ? WHERE id = ?",
            (1 if new else 0, get_somali_time_db(), existing['id'])
        )
        conn.commit()

        _audit(admin_id, 'active_toggle', existing['id'], feature_key,
               old_value=old, new_value=new, reason=reason)

        reload_policy_cache()
        return True, f"Feature '{feature_key}' is now {'active' if new else 'inactive'}"
    except Exception as e:
        logger.error(f"toggle_feature_active failed: {e}", exc_info=True)
        return False, f"Failed to toggle feature: {e}"


# ============================================
# ADMIN API — EXPORT / IMPORT
# ============================================

def export_to_json() -> Dict[str, Any]:
    """Return the full policy state as a dict shaped like entitlements_seed.json."""
    features_out: List[Dict[str, Any]] = []
    for f in list_features():
        features_out.append({
            'feature_key': f['feature_key'],
            'display_name': f['display_name'],
            'description': f['description'],
            'category': f['category'],
            'policy_type': f['policy_type'],
            'unit_hint': f['unit_hint'],
            'sort_order': f['sort_order'],
            'notes': f['notes'],
            'is_global_active': 1 if f['is_global_active'] else 0,
            'policies': {tier: dict(f['policies'].get(tier, {})) for tier in VALID_TIERS},
        })
    return {'version': 1, 'features': features_out}


def import_from_json(admin_id: int, data: Dict[str, Any],
                     reason: str = '') -> Tuple[bool, str]:
    """
    Replace the entitlement configuration from a JSON payload.
    Safety rules:
      - Payload must be a superset of the existing feature keys.
        Missing features abort the import to prevent accidental lockout.
      - Runs inside a transaction.
      - Logs an audit row with the full prior state.
    """
    incoming_features = data.get('features') or []
    if not isinstance(incoming_features, list) or not incoming_features:
        return False, "JSON must contain a non-empty 'features' array"

    incoming_keys = {f.get('feature_key') for f in incoming_features if f.get('feature_key')}
    current_keys = set(get_policy().keys())

    missing = current_keys - incoming_keys
    if missing:
        return False, (
            f"Import refused — would remove existing features: {sorted(missing)}. "
            f"Delete them explicitly first."
        )

    # Take a snapshot for the audit
    prior_state = export_to_json()

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("BEGIN")

        for f in incoming_features:
            key = f['feature_key']
            if key in current_keys:
                # Update metadata
                cursor.execute("""
                    UPDATE entitlement_features
                    SET display_name = ?, description = ?, category = ?,
                        policy_type = ?, unit_hint = ?, sort_order = ?,
                        notes = ?, updated_at = ?
                    WHERE feature_key = ?
                """, (
                    f.get('display_name', key),
                    f.get('description', ''),
                    f.get('category', 'general'),
                    f.get('policy_type', 'permission'),
                    f.get('unit_hint'),
                    int(f.get('sort_order') or 0),
                    f.get('notes', ''),
                    get_somali_time_db(),
                    key,
                ))
                cursor.execute(
                    "SELECT id FROM entitlement_features WHERE feature_key = ?",
                    (key,)
                )
                feature_id = cursor.fetchone()['id']
            else:
                cursor.execute("""
                    INSERT INTO entitlement_features
                        (feature_key, display_name, description, category,
                         policy_type, unit_hint, is_global_active, sort_order, notes)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """, (
                    key,
                    f.get('display_name', key),
                    f.get('description', ''),
                    f.get('category', 'general'),
                    f.get('policy_type', 'permission'),
                    f.get('unit_hint'),
                    int(f.get('sort_order') or 0),
                    f.get('notes', ''),
                ))
                feature_id = cursor.lastrowid

            policies = f.get('policies') or {}
            for tier in VALID_TIERS:
                p = policies.get(tier, {})
                cursor.execute("""
                    INSERT INTO entitlement_policies
                        (feature_id, tier, is_enabled, level_value, limit_value, limit_unit)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(feature_id, tier) DO UPDATE SET
                        is_enabled = excluded.is_enabled,
                        level_value = excluded.level_value,
                        limit_value = excluded.limit_value,
                        limit_unit = excluded.limit_unit,
                        updated_at = datetime('now', 'localtime')
                """, (
                    feature_id, tier,
                    1 if p.get('is_enabled') else 0,
                    p.get('level_value'),
                    p.get('limit_value'),
                    p.get('limit_unit'),
                ))

        conn.commit()

        _audit(admin_id, 'import', None, None,
               old_value=prior_state, new_value=data, reason=reason)

        reload_policy_cache()
        return True, f"Imported {len(incoming_features)} features"
    except Exception as e:
        try:
            get_db().rollback()
        except Exception:
            pass
        logger.error(f"import_from_json failed: {e}", exc_info=True)
        return False, f"Import failed: {e}"