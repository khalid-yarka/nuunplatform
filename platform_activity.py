# platform_activity.py
# Platform-wide activity aggregation for the admin dashboard.
# Reads from existing tables (students, quiz_attempts, live_quiz_participants,
# pdfs, groups, notifications, upgrade_requests, activity_logs, error_logs).
# No schema changes required.

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

from db import execute_with_retry
from utils import get_somali_time, SOMALI_TIMEZONE

logger = logging.getLogger(__name__)


# ============================================
# HELPERS
# ============================================

def _scalar(sql: str, params=()) -> int:
    """Safely run a COUNT-type query and return the integer result."""
    try:
        cursor = execute_with_retry(sql, params)
        row = cursor.fetchone()
        if not row:
            return 0
        # row is a sqlite3.Row — get first column
        return int(list(row)[0] or 0)
    except Exception as e:
        logger.warning(f"platform_activity scalar failed: {e}  SQL={sql[:80]}")
        return 0


# ============================================
# TOP-LINE STATS
# ============================================

def get_platform_stats() -> Dict[str, int]:
    """Headline numbers for the platform dashboard."""
    return {
        # Users
        'users_total':      _scalar("SELECT COUNT(*) FROM students"),
        'users_today':      _scalar("SELECT COUNT(*) FROM students WHERE created_at >= datetime('now', '-1 day')"),
        'users_week':       _scalar("SELECT COUNT(*) FROM students WHERE created_at >= datetime('now', '-7 days')"),
        'users_month':      _scalar("SELECT COUNT(*) FROM students WHERE created_at >= datetime('now', '-30 days')"),
        'admins_total':     _scalar("SELECT COUNT(*) FROM students WHERE is_admin = 1"),

        # Quiz activity
        'quizzes_today':    _scalar("SELECT COUNT(*) FROM quiz_attempts WHERE completed_at >= datetime('now', '-1 day')"),
        'quizzes_week':     _scalar("SELECT COUNT(*) FROM quiz_attempts WHERE completed_at >= datetime('now', '-7 days')"),
        'quizzes_month':    _scalar("SELECT COUNT(*) FROM quiz_attempts WHERE completed_at >= datetime('now', '-30 days')"),

        # Live quiz
        'live_quizzes_active': _scalar("SELECT COUNT(*) FROM live_quizzes WHERE status IN ('waiting','scheduled','active')"),
        'live_quizzes_finished_week': _scalar(
            "SELECT COUNT(*) FROM live_quizzes WHERE status = 'finished' AND ended_at >= datetime('now', '-7 days')"
        ),

        # Content
        'pdfs_total':       _scalar("SELECT COUNT(*) FROM pdfs"),
        'groups_total':     _scalar("SELECT COUNT(*) FROM groups WHERE is_active = 1"),

        # Errors
        'errors_open':      _scalar("SELECT COUNT(*) FROM error_logs WHERE resolved = 0 AND dismissed = 0"),
        'errors_today':     _scalar("SELECT COUNT(*) FROM error_logs WHERE timestamp >= datetime('now', '-1 day')"),

        # Upgrades
        'upgrades_pending': _scalar("SELECT COUNT(*) FROM upgrade_requests WHERE status = 'pending'"),
        'upgrades_approved_month': _scalar(
            "SELECT COUNT(*) FROM upgrade_requests WHERE status = 'approved' AND approved_at >= datetime('now', '-30 days')"
        ),
    }


# ============================================
# TIME-SERIES (last N days)
# ============================================

def get_signups_series(days: int = 30) -> List[Dict[str, Any]]:
    """Signups per day for the last N days."""
    try:
        cursor = execute_with_retry("""
            SELECT
                strftime('%Y-%m-%d', created_at) AS date,
                COUNT(*) AS count
            FROM students
            WHERE created_at >= datetime('now', '-' || ? || ' days')
            GROUP BY date
            ORDER BY date ASC
        """, (days,))
        return [{'date': row['date'], 'value': row['count']} for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"get_signups_series: {e}")
        return []


def get_quizzes_series(days: int = 30) -> List[Dict[str, Any]]:
    """Quiz attempts per day for the last N days."""
    try:
        cursor = execute_with_retry("""
            SELECT
                strftime('%Y-%m-%d', completed_at) AS date,
                COUNT(*) AS count
            FROM quiz_attempts
            WHERE completed_at >= datetime('now', '-' || ? || ' days')
            GROUP BY date
            ORDER BY date ASC
        """, (days,))
        return [{'date': row['date'], 'value': row['count']} for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"get_quizzes_series: {e}")
        return []


def get_live_quizzes_series(days: int = 30) -> List[Dict[str, Any]]:
    """Live quizzes created per day."""
    try:
        cursor = execute_with_retry("""
            SELECT
                strftime('%Y-%m-%d', created_at) AS date,
                COUNT(*) AS count
            FROM live_quizzes
            WHERE created_at >= datetime('now', '-' || ? || ' days')
            GROUP BY date
            ORDER BY date ASC
        """, (days,))
        return [{'date': row['date'], 'value': row['count']} for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"get_live_quizzes_series: {e}")
        return []


# ============================================
# DISTRIBUTIONS
# ============================================

def get_tier_distribution() -> Dict[str, int]:
    """How many users per tier."""
    try:
        cursor = execute_with_retry("""
            SELECT tier, COUNT(*) AS count
            FROM students
            GROUP BY tier
        """)
        dist = {'danbe': 0, 'dhexe': 0, 'hore': 0}
        for row in cursor.fetchall():
            t = (row['tier'] or 'danbe').lower()
            dist[t] = row['count']
        return dist
    except Exception as e:
        logger.error(f"get_tier_distribution: {e}")
        return {'danbe': 0, 'dhexe': 0, 'hore': 0}


def get_location_distribution() -> Dict[str, int]:
    """How many users per location."""
    try:
        cursor = execute_with_retry("""
            SELECT COALESCE(location, 'unknown') AS loc, COUNT(*) AS count
            FROM students
            GROUP BY loc
        """)
        dist = {}
        for row in cursor.fetchall():
            dist[row['loc']] = row['count']
        return dist
    except Exception as e:
        logger.error(f"get_location_distribution: {e}")
        return {}


def get_top_subjects(limit: int = 5) -> List[Dict[str, Any]]:
    """Top subjects by number of quiz attempts."""
    try:
        cursor = execute_with_retry("""
            SELECT subject_code, COUNT(*) AS attempts
            FROM quiz_attempts
            WHERE completed_at >= datetime('now', '-30 days')
            GROUP BY subject_code
            ORDER BY attempts DESC
            LIMIT ?
        """, (limit,))
        return [{'subject': row['subject_code'], 'attempts': row['attempts']}
                for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"get_top_subjects: {e}")
        return []


def get_top_performers(limit: int = 5) -> List[Dict[str, Any]]:
    """Most active users by quiz count (last 30 days)."""
    try:
        cursor = execute_with_retry("""
            SELECT s.id, s.public_id, s.first_name, s.last_name,
                   COUNT(qa.id) AS attempts,
                   AVG((qa.score * 100.0) / NULLIF(qa.total_questions, 0)) AS avg_score
            FROM students s
            JOIN quiz_attempts qa ON qa.student_id = s.id
            WHERE qa.completed_at >= datetime('now', '-30 days')
            GROUP BY s.id
            ORDER BY attempts DESC
            LIMIT ?
        """, (limit,))
        out = []
        for row in cursor.fetchall():
            out.append({
                'id': row['id'],
                'public_id': row['public_id'] or '----',
                'name': f"{row['first_name'] or ''} {row['last_name'] or ''}".strip() or 'Unknown',
                'attempts': row['attempts'],
                'avg_score': round(row['avg_score'] or 0, 1),
            })
        return out
    except Exception as e:
        logger.error(f"get_top_performers: {e}")
        return []


# ============================================
# ACTIVITY FEED (unified timeline)
# ============================================

def get_activity_feed(limit: int = 40, source: str = 'all') -> List[Dict[str, Any]]:
    """
    Unified activity stream from multiple sources:
      - New signups
      - Quiz completions
      - Live quiz starts
      - Tier upgrades
      - PDF uploads
    Returns a sorted list of event dicts.
    """
    events: List[Dict[str, Any]] = []

    try:
        # Signups
        if source in ('all', 'users'):
            cursor = execute_with_retry("""
                SELECT id, first_name, last_name, public_id, created_at
                FROM students
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))
            for row in cursor.fetchall():
                name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip() or 'Unknown'
                events.append({
                    'kind': 'signup',
                    'icon': '👤',
                    'color': 'blue',
                    'title': f'New user registered: <strong>{name}</strong>',
                    'subtitle': f"#{row['public_id'] or '----'}",
                    'timestamp': row['created_at'],
                    'link': None,
                })

        # Quiz completions
        if source in ('all', 'quizzes'):
            cursor = execute_with_retry("""
                SELECT qa.id, qa.subject_code, qa.score, qa.total_questions,
                       qa.completed_at, s.first_name, s.last_name, s.public_id
                FROM quiz_attempts qa
                JOIN students s ON qa.student_id = s.id
                ORDER BY qa.completed_at DESC
                LIMIT ?
            """, (limit,))
            for row in cursor.fetchall():
                name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip() or 'Unknown'
                pct = round((row['score'] / row['total_questions']) * 100) if row['total_questions'] else 0
                events.append({
                    'kind': 'quiz',
                    'icon': '📝',
                    'color': 'green' if pct >= 70 else 'amber' if pct >= 40 else 'red',
                    'title': f'<strong>{name}</strong> completed a quiz',
                    'subtitle': f"{row['subject_code']} · {row['score']}/{row['total_questions']} ({pct}%)",
                    'timestamp': row['completed_at'],
                    'link': None,
                })

        # Live quiz started
        if source in ('all', 'live'):
            cursor = execute_with_retry("""
                SELECT lq.id, lq.title, lq.subject_code, lq.status,
                       lq.started_at, lq.created_at, s.first_name, s.last_name
                FROM live_quizzes lq
                JOIN students s ON lq.creator_id = s.id
                WHERE lq.started_at IS NOT NULL
                ORDER BY lq.started_at DESC
                LIMIT ?
            """, (limit,))
            for row in cursor.fetchall():
                name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip() or 'Unknown'
                events.append({
                    'kind': 'live_quiz',
                    'icon': '⚡',
                    'color': 'purple',
                    'title': f'<strong>{name}</strong> started a live quiz',
                    'subtitle': row['title'] or row['subject_code'],
                    'timestamp': row['started_at'],
                    'link': f"/live-quiz/results/{row['id']}",
                })

        # Tier upgrades
        if source in ('all', 'upgrades'):
            cursor = execute_with_retry("""
                SELECT ur.request_id, ur.requested_tier, ur.duration,
                       ur.final_price_cents, ur.approved_at,
                       s.first_name, s.last_name
                FROM upgrade_requests ur
                JOIN students s ON ur.user_id = s.id
                WHERE ur.status = 'approved' AND ur.approved_at IS NOT NULL
                ORDER BY ur.approved_at DESC
                LIMIT ?
            """, (limit,))
            for row in cursor.fetchall():
                name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip() or 'Unknown'
                price = (row['final_price_cents'] or 0) / 100
                events.append({
                    'kind': 'upgrade',
                    'icon': '🚀',
                    'color': 'pink',
                    'title': f'<strong>{name}</strong> upgraded to {row["requested_tier"].upper()}',
                    'subtitle': f"{row['duration']} · ${price:.2f}",
                    'timestamp': row['approved_at'],
                    'link': f"/upgrade/admin/upgrade-requests/{row['request_id']}",
                })

        # Recent PDFs
        if source in ('all', 'content'):
            cursor = execute_with_retry("""
                SELECT id, title, subject, curriculum, uploaded_at
                FROM pdfs
                ORDER BY uploaded_at DESC
                LIMIT ?
            """, (limit,))
            for row in cursor.fetchall():
                events.append({
                    'kind': 'pdf',
                    'icon': '📄',
                    'color': 'red',
                    'title': f'New PDF: <strong>{row["title"]}</strong>',
                    'subtitle': f"{row['subject'] or '-'} · {row['curriculum'] or '-'}",
                    'timestamp': row['uploaded_at'],
                    'link': None,
                })

    except Exception as e:
        logger.error(f"get_activity_feed: {e}", exc_info=True)

    # Sort by timestamp desc (newest first)
    def sort_key(ev):
        return ev.get('timestamp') or ''

    events.sort(key=sort_key, reverse=True)
    return events[:limit]


# ============================================
# ADMINS – NOTIFY EVERYONE OR SELECTED
# ============================================

def get_all_admin_ids() -> List[int]:
    """Return the IDs of every admin user."""
    try:
        cursor = execute_with_retry(
            "SELECT id FROM students WHERE is_admin = 1"
        )
        return [row['id'] for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"get_all_admin_ids: {e}")
        return []


def broadcast_to_admins(
    title: str,
    body: str,
    link: str = '/admin/platform',
    icon: str = '📡',
    exclude_admin_id: int = None,
) -> int:
    """
    Send a notification to every admin user.
    Returns the number of notifications sent.
    """
    from db import create_notification

    admin_ids = get_all_admin_ids()
    sent = 0
    for aid in admin_ids:
        if exclude_admin_id and aid == exclude_admin_id:
            continue
        try:
            create_notification(
                user_id=aid,
                type='admin_broadcast',
                title=title,
                body=body,
                link=link,
                icon=icon,
            )
            sent += 1
        except Exception as e:
            logger.warning(f"broadcast_to_admins: failed for admin {aid}: {e}")
    return sent