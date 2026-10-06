# services/pdf_history.py
# ============================================================
# Central log and query layer for PDF interactions.
#
# Logging is fire-and-forget: every write is wrapped in
# try/except and never raises into the caller's request path.
#
# Two categories:
#   · student  — view / download / telegram_fetch / save /
#                unsave / report / quota_exhausted
#   · admin    — create / publish / edit / code_change /
#                premium_on / premium_off / verify /
#                unverify / delete / refetch_size
#   · system   — size_resolved / auto_publish / cleanup_removed
# ============================================================

import json
import logging
from typing import Optional, Dict, List, Any, Iterable

from db import execute_with_retry

logger = logging.getLogger(__name__)


# ─── Event vocabularies ────────────────────────────────────

STUDENT_EVENTS = (
    'view', 'download', 'telegram_fetch', 'save', 'unsave',
    'report', 'quota_exhausted', 'share',
)

ADMIN_EVENTS = (
    'create', 'publish', 'edit', 'code_change', 'premium_on',
    'premium_off', 'verify', 'unverify', 'delete', 'refetch_size',
    'staging_edit', 'export',
)

SYSTEM_EVENTS = (
    'size_resolved', 'auto_publish', 'cleanup_removed',
)

ALL_EVENTS = STUDENT_EVENTS + ADMIN_EVENTS + SYSTEM_EVENTS


_EVENT_ICONS = {
    'view': 'fa-eye',
    'download': 'fa-download',
    'telegram_fetch': 'fa-paper-plane',
    'save': 'fa-bookmark',
    'unsave': 'fa-bookmark',
    'report': 'fa-flag',
    'quota_exhausted': 'fa-lock',
    'share': 'fa-share',
    'create': 'fa-plus',
    'publish': 'fa-cloud-upload-alt',
    'edit': 'fa-pen',
    'code_change': 'fa-barcode',
    'premium_on': 'fa-gem',
    'premium_off': 'fa-gem',
    'verify': 'fa-circle-check',
    'unverify': 'fa-circle-xmark',
    'delete': 'fa-trash',
    'refetch_size': 'fa-sync',
    'staging_edit': 'fa-layer-group',
    'export': 'fa-file-csv',
    'size_resolved': 'fa-cog',
    'auto_publish': 'fa-robot',
    'cleanup_removed': 'fa-broom',
}

_EVENT_LABELS = {
    'view': 'viewed',
    'download': 'downloaded',
    'telegram_fetch': 'fetched via Telegram',
    'save': 'saved',
    'unsave': 'removed from saved',
    'report': 'reported',
    'quota_exhausted': 'hit daily quota',
    'share': 'shared',
    'create': 'created',
    'publish': 'published',
    'edit': 'edited',
    'code_change': 'changed code',
    'premium_on': 'marked premium',
    'premium_off': 'removed premium',
    'verify': 'verified',
    'unverify': 'unverified',
    'delete': 'deleted',
    'refetch_size': 'refetched size',
    'staging_edit': 'edited staging',
    'export': 'exported',
    'size_resolved': 'resolved file size',
    'auto_publish': 'auto-published',
    'cleanup_removed': 'removed by cleanup',
}


def event_icon(event_type: str) -> str:
    return _EVENT_ICONS.get(event_type, 'fa-circle')


def event_label(event_type: str) -> str:
    return _EVENT_LABELS.get(event_type, event_type.replace('_', ' '))


def event_category(event_type: str) -> str:
    if event_type in STUDENT_EVENTS:
        return 'student'
    if event_type in ADMIN_EVENTS:
        return 'admin'
    return 'system'


# ─── Write side ────────────────────────────────────────────

def log_pdf_event(
    pdf_id: Optional[int],
    event_type: str,
    *,
    pdf_code: Optional[str] = None,
    pdf_title: Optional[str] = None,
    actor_id: Optional[int] = None,
    actor_public_id: Optional[str] = None,
    actor_name: Optional[str] = None,
    actor_role: str = 'system',
    source: str = 'web',
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Fire-and-forget write. NEVER raises. If the write fails,
    we log at debug level and move on.
    """
    if event_type not in ALL_EVENTS:
        logger.debug(f"pdf_history: unknown event_type {event_type!r}")
        return

    try:
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
    except Exception:
        meta_json = None

    try:
        execute_with_retry(
            """
            INSERT INTO pdf_events
                (pdf_id, pdf_code, pdf_title, event_type, event_category,
                 actor_id, actor_public_id, actor_name, actor_role,
                 source, ip_address, user_agent, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pdf_id,
                (pdf_code or '')[:24] or None,
                (pdf_title or '')[:200] or None,
                event_type,
                event_category(event_type),
                actor_id,
                actor_public_id,
                (actor_name or '')[:80] or None,
                actor_role,
                source,
                (ip_address or '')[:64] or None,
                (user_agent or '')[:300] or None,
                meta_json,
            ),
            commit=True,
        )
    except Exception as e:
        logger.debug(f"pdf_history: write failed for {event_type}: {e}")


def log_student_event(
    pdf_row: Optional[Dict[str, Any]],
    event_type: str,
    *,
    user: Optional[Dict[str, Any]] = None,
    source: str = 'web',
    request=None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Convenience wrapper for student-side calls.
    Extracts pdf_id / pdf_code / pdf_title from the row dict,
    and actor fields from the user dict.
    """
    ip = None
    ua = None
    if request is not None:
        try:
            ip = request.headers.get('X-Forwarded-For', '').split(',')[0].strip() \
                 or request.remote_addr
            ua = request.headers.get('User-Agent', '')
        except Exception:
            pass

    pdf_id = None
    pdf_code = None
    pdf_title = None
    if pdf_row:
        pdf_id = pdf_row.get('id')
        pdf_code = pdf_row.get('code')
        pdf_title = pdf_row.get('title')

    actor_id = None
    actor_public_id = None
    actor_name = None
    if user:
        actor_id = user.get('id')
        actor_public_id = user.get('public_id')
        fn = user.get('first_name') or ''
        ln = user.get('last_name') or ''
        actor_name = f"{fn} {ln[0] + '.' if ln else ''}".strip() or None

    log_pdf_event(
        pdf_id,
        event_type,
        pdf_code=pdf_code,
        pdf_title=pdf_title,
        actor_id=actor_id,
        actor_public_id=actor_public_id,
        actor_name=actor_name,
        actor_role='student',
        source=source,
        ip_address=ip,
        user_agent=ua,
        metadata=metadata,
    )


def log_admin_event(
    pdf_row: Optional[Dict[str, Any]],
    event_type: str,
    *,
    admin: Optional[Dict[str, Any]] = None,
    source: str = 'admin',
    request=None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Convenience wrapper for admin-side calls.
    `admin` should be a dict with at least `id`, and optionally
    `first_name`, `last_name`, `public_id`, `is_admin`.
    """
    ip = None
    ua = None
    if request is not None:
        try:
            ip = request.headers.get('X-Forwarded-For', '').split(',')[0].strip() \
                 or request.remote_addr
            ua = request.headers.get('User-Agent', '')
        except Exception:
            pass

    pdf_id = None
    pdf_code = None
    pdf_title = None
    if pdf_row:
        pdf_id = pdf_row.get('id')
        pdf_code = pdf_row.get('code')
        pdf_title = pdf_row.get('title')

    actor_id = None
    actor_public_id = None
    actor_name = 'Admin'
    actor_role = 'admin'
    if admin:
        actor_id = admin.get('id')
        actor_public_id = admin.get('public_id')
        fn = admin.get('first_name') or ''
        ln = admin.get('last_name') or ''
        actor_name = f"{fn} {ln}".strip() or 'Admin'
        actor_role = 'super_admin' if admin.get('is_super') else 'admin'

    log_pdf_event(
        pdf_id,
        event_type,
        pdf_code=pdf_code,
        pdf_title=pdf_title,
        actor_id=actor_id,
        actor_public_id=actor_public_id,
        actor_name=actor_name,
        actor_role=actor_role,
        source=source,
        ip_address=ip,
        user_agent=ua,
        metadata=metadata,
    )


def log_system_event(
    pdf_row: Optional[Dict[str, Any]],
    event_type: str,
    *,
    source: str = 'task',
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    log_pdf_event(
        (pdf_row or {}).get('id'),
        event_type,
        pdf_code=(pdf_row or {}).get('code'),
        pdf_title=(pdf_row or {}).get('title'),
        actor_role='system',
        source=source,
        metadata=metadata,
    )


# ─── Read side ─────────────────────────────────────────────

def _serialize(row) -> Dict[str, Any]:
    d = dict(row)
    if d.get('metadata'):
        try:
            d['metadata'] = json.loads(d['metadata'])
        except Exception:
            d['metadata'] = None
    d['icon'] = event_icon(d.get('event_type'))
    d['label'] = event_label(d.get('event_type'))
    return d


def get_pdf_events(
    pdf_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
    event_types: Optional[Iterable[str]] = None,
    actor_role: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    where = ["pdf_id = ?"]
    params: List[Any] = [pdf_id]

    if event_types:
        placeholders = ','.join('?' for _ in event_types)
        where.append(f"event_type IN ({placeholders})")
        params.extend(list(event_types))

    if actor_role:
        where.append("actor_role = ?")
        params.append(actor_role)

    if since:
        where.append("created_at >= ?")
        params.append(since)

    if until:
        where.append("created_at <= ?")
        params.append(until)

    sql = (
        f"SELECT * FROM pdf_events WHERE {' AND '.join(where)} "
        f"ORDER BY created_at DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    try:
        cursor = execute_with_retry(sql, tuple(params))
        return [_serialize(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.warning(f"get_pdf_events failed: {e}")
        return []


def get_global_events(
    *,
    limit: int = 50,
    offset: int = 0,
    event_types: Optional[Iterable[str]] = None,
    actor_role: Optional[str] = None,
    pdf_code: Optional[str] = None,
    pdf_id: Optional[int] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    where = ["1=1"]
    params: List[Any] = []

    if event_types:
        placeholders = ','.join('?' for _ in event_types)
        where.append(f"event_type IN ({placeholders})")
        params.extend(list(event_types))

    if actor_role:
        where.append("actor_role = ?")
        params.append(actor_role)

    if pdf_code:
        where.append("pdf_code = ?")
        params.append(pdf_code)

    if pdf_id is not None:
        where.append("pdf_id = ?")
        params.append(pdf_id)

    if since:
        where.append("created_at >= ?")
        params.append(since)

    if until:
        where.append("created_at <= ?")
        params.append(until)

    sql = (
        f"SELECT * FROM pdf_events WHERE {' AND '.join(where)} "
        f"ORDER BY created_at DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    try:
        cursor = execute_with_retry(sql, tuple(params))
        return [_serialize(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.warning(f"get_global_events failed: {e}")
        return []


def count_global_events(
    *,
    event_types: Optional[Iterable[str]] = None,
    actor_role: Optional[str] = None,
    pdf_code: Optional[str] = None,
    pdf_id: Optional[int] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> int:
    where = ["1=1"]
    params: List[Any] = []

    if event_types:
        placeholders = ','.join('?' for _ in event_types)
        where.append(f"event_type IN ({placeholders})")
        params.extend(list(event_types))
    if actor_role:
        where.append("actor_role = ?")
        params.append(actor_role)
    if pdf_code:
        where.append("pdf_code = ?")
        params.append(pdf_code)
    if pdf_id is not None:
        where.append("pdf_id = ?")
        params.append(pdf_id)
    if since:
        where.append("created_at >= ?")
        params.append(since)
    if until:
        where.append("created_at <= ?")
        params.append(until)

    try:
        cursor = execute_with_retry(
            f"SELECT COUNT(*) AS n FROM pdf_events WHERE {' AND '.join(where)}",
            tuple(params)
        )
        row = cursor.fetchone()
        return row['n'] if row else 0
    except Exception:
        return 0


def get_pdf_event_summary(pdf_id: int) -> Dict[str, int]:
    """Counts per event_type for a single PDF."""
    summary = {}
    try:
        cursor = execute_with_retry(
            "SELECT event_type, COUNT(*) AS n FROM pdf_events "
            "WHERE pdf_id = ? GROUP BY event_type",
            (pdf_id,)
        )
        for r in cursor.fetchall():
            summary[r['event_type']] = r['n']
    except Exception:
        pass
    return summary


def get_today_summary() -> Dict[str, int]:
    """
    Counts for the dashboard's "today" panel.
    Only student events are included (admin counts are small
    and already visible in the KPI tiles).
    """
    out = {
        'views': 0, 'downloads': 0, 'telegram_fetches': 0,
        'saves': 0, 'reports': 0, 'quota_hits': 0,
        'total_today': 0,
    }
    try:
        cursor = execute_with_retry(
            "SELECT event_type, COUNT(*) AS n FROM pdf_events "
            "WHERE DATE(created_at) = DATE('now', 'localtime') "
            "GROUP BY event_type"
        )
        for r in cursor.fetchall():
            et = r['event_type']
            n = r['n']
            if et == 'view': out['views'] = n
            elif et == 'download': out['downloads'] = n
            elif et == 'telegram_fetch': out['telegram_fetches'] = n
            elif et == 'save': out['saves'] = n
            elif et == 'report': out['reports'] = n
            elif et == 'quota_exhausted': out['quota_hits'] = n
            out['total_today'] += n
    except Exception:
        pass
    return out


def get_recent_events_for_pdf(pdf_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    return get_pdf_events(pdf_id, limit=limit)


def group_events_by_day(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Turn a flat list of events into a day-grouped structure
    suitable for the timeline UI.
    """
    from collections import OrderedDict
    groups = OrderedDict()
    for e in events:
        ts = e.get('created_at') or ''
        day = ts[:10] if ts else 'Unknown'
        groups.setdefault(day, []).append(e)
    return [{'day': k, 'events': v} for k, v in groups.items()]


# ─── Retention ─────────────────────────────────────────────

def purge_old_events() -> Dict[str, int]:
    """
    Enforce the retention policy. Called from daily_tasks.
    Returns counts of deleted rows per tier.
    """
    out = {'views_downloads': 0, 'student_mid': 0, 'system': 0}

    try:
        cur = execute_with_retry(
            """
            DELETE FROM pdf_events
            WHERE created_at < datetime('now', '-90 days')
              AND event_type IN ('view', 'download', 'telegram_fetch')
            """,
            commit=True,
        )
        out['views_downloads'] = cur.rowcount or 0
    except Exception as e:
        logger.warning(f"purge_old_events (tier 1) failed: {e}")

    try:
        cur = execute_with_retry(
            """
            DELETE FROM pdf_events
            WHERE created_at < datetime('now', '-365 days')
              AND event_category = 'student'
              AND event_type NOT IN ('view', 'download', 'telegram_fetch')
            """,
            commit=True,
        )
        out['student_mid'] = cur.rowcount or 0
    except Exception as e:
        logger.warning(f"purge_old_events (tier 2) failed: {e}")

    try:
        cur = execute_with_retry(
            """
            DELETE FROM pdf_events
            WHERE created_at < datetime('now', '-365 days')
              AND event_category = 'system'
            """,
            commit=True,
        )
        out['system'] = cur.rowcount or 0
    except Exception as e:
        logger.warning(f"purge_old_events (tier 3) failed: {e}")

    return out