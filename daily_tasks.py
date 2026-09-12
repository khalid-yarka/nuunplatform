#!/usr/bin/env python3
# daily_tasks.py — NuunPlatform Daily Task Runner
# ===============================================================
# Single-file daily maintenance + reporting system.
#
# Designed for PythonAnywhere:
#   - Absolute paths, no relative imports
#   - Non-interactive, no stdin
#   - Lock-file prevents concurrent runs
#   - Idempotent tasks
#   - Every task isolated in try/except
#
# What it does each run:
#   1. Health checks      (DB, disk, backup)
#   2. Cleanup            (expired tiers, history, notifications, errors)
#   3. Focus integrity    (PDF link validation, coverage rebuild)
#   4. Live-quiz cleanup  (orphaned quizzes)
#   5. Collect metrics    (into platform_metrics table)
#   6. Create DB snapshot (gzipped, off-site copy)
#   7. Send Telegram report + attachments to super admins
#
# Usage:
#   python daily_tasks.py              # full run
#   python daily_tasks.py --dry-run    # no mutations
#   python daily_tasks.py --list       # list all tasks
#   python daily_tasks.py --task NAME  # run one task
#   python daily_tasks.py --category cleanup
#   python daily_tasks.py --no-telegram
#   python daily_tasks.py --no-snapshot
#   python daily_tasks.py -v           # verbose
# ===============================================================

import os
import sys
import argparse
import gzip
import hashlib
import logging
import shutil
import sqlite3
import time
import traceback
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------
# PATH & ENV SETUP (PythonAnywhere friendly)
# ---------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
except Exception:
    pass

import requests

from config import Config
from db import (
    get_db, execute_with_retry, close_db_connections,
    clean_history_entries,
)
from utils import SOMALI_TIMEZONE


# ---------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------
LOG_FILE = os.path.join(Config.LOG_DIR, 'daily_tasks.log')
LOCK_FILE = os.path.join(str(BASE_DIR), '.daily_tasks.lock')
REPORT_DIR = os.path.join(Config.LOG_DIR, 'daily_reports')
SNAPSHOT_DIR = os.path.join(Config.LOG_DIR, 'daily_snapshots')

# Where to look for the base URL of the admin panel (for links)
BASE_URL = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')

# Telegram API
TELEGRAM_API = 'https://api.telegram.org/bot{token}/{method}'

# Retention defaults (days) — hardcoded for v1; can be moved to entitlements later
RETENTION_NOTIFICATIONS_DAYS = 30
RETENTION_ERRORS_DAYS = 30
RETENTION_ACTIVITY_DAYS = 60
RETENTION_LIVE_QUIZ_DAYS = 7

# Lock expires after 2 hours (previous run assumed dead)
LOCK_STALE_SECONDS = 2 * 3600

# Message length threshold — above this, attach the full report as a file
MESSAGE_SPLIT_THRESHOLD = 3500


# ---------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------
def setup_logging(verbose: bool = False) -> None:
    os.makedirs(Config.LOG_DIR, exist_ok=True)
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    try:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5)
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except Exception:
        pass

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    ch.setLevel(logging.INFO)
    root.addHandler(ch)


log = logging.getLogger('daily_tasks')


# ---------------------------------------------------------------
# TIME HELPERS — Somali timezone (UTC+3) with AM/PM
# ---------------------------------------------------------------
def somali_now() -> datetime:
    return datetime.now(SOMALI_TIMEZONE)


def somali_format(dt: Optional[datetime] = None, style: str = 'long') -> str:
    """
    Format a datetime in Somali time.

    style:
        'long'  → 'Tuesday, 12 September 2026 · 3:15 AM'
        'short' → '2026-09-12 3:15 AM'
        'time'  → '3:15 AM'
        'iso'   → '2026-09-12T03:15:00+03:00'
    """
    if dt is None:
        dt = somali_now()
    hour = dt.hour % 12
    if hour == 0:
        hour = 12
    am_pm = 'AM' if dt.hour < 12 else 'PM'

    if style == 'long':
        return f"{dt.strftime('%A')}, {dt.day} {dt.strftime('%B')} {dt.year} · {hour}:{dt.minute:02d} {am_pm}"
    if style == 'short':
        return f"{dt.strftime('%Y-%m-%d')} {hour}:{dt.minute:02d} {am_pm}"
    if style == 'time':
        return f"{hour}:{dt.minute:02d} {am_pm}"
    if style == 'iso':
        return dt.isoformat()
    return dt.isoformat()


def metric_date_key(dt: Optional[datetime] = None) -> str:
    """YYYY-MM-DD key for the platform_metrics table."""
    if dt is None:
        dt = somali_now()
    return dt.strftime('%Y-%m-%d')


# ---------------------------------------------------------------
# MARKDOWN V2 HELPERS (Telegram)
# ---------------------------------------------------------------
_MDV2_SPECIAL = set('_*[]()~`>#+-=|{}.!')


def mdv2_escape(text: Any) -> str:
    if text is None:
        return ''
    s = str(text)
    return ''.join(('\\' + c) if c in _MDV2_SPECIAL else c for c in s)


def md_bold(text: Any) -> str:
    return f'*{mdv2_escape(text)}*'


def md_italic(text: Any) -> str:
    return f'_{mdv2_escape(text)}_'


def md_code(text: Any) -> str:
    return f'`{mdv2_escape(text)}`'


def md_link(text: Any, url: str) -> str:
    safe_url = url.replace('\\', '\\\\').replace(')', '\\)')
    return f'[{mdv2_escape(text)}]({safe_url})'


class Msg:
    """Tiny builder for Telegram MarkdownV2 messages."""

    def __init__(self) -> None:
        self.lines: List[str] = []

    def raw(self, s: str) -> 'Msg':
        """Already-formatted MarkdownV2 chunk."""
        self.lines.append(s)
        return self

    def text(self, s: str) -> 'Msg':
        self.lines.append(mdv2_escape(s))
        return self

    def bold(self, s: str) -> 'Msg':
        self.lines.append(md_bold(s))
        return self

    def italic(self, s: str) -> 'Msg':
        self.lines.append(md_italic(s))
        return self

    def blank(self) -> 'Msg':
        self.lines.append('')
        return self

    def divider(self) -> 'Msg':
        self.lines.append('━━━━━━━━━━━━━━━')
        return self

    def bullet(self, label: str, value: Optional[Any] = None, icon: Optional[str] = None) -> 'Msg':
        prefix = f'{icon} ' if icon else '• '
        if value is None:
            self.lines.append(f'{mdv2_escape(prefix + str(label))}')
        else:
            self.lines.append(f'{mdv2_escape(prefix + str(label) + ": ")}*{mdv2_escape(value)}*')
        return self

    def link_line(self, prefix: str, text: str, url: str, icon: Optional[str] = None) -> 'Msg':
        lead = f'{icon} ' if icon else '• '
        self.lines.append(
            f'{mdv2_escape(lead + prefix)} {md_link(text, url)}'
        )
        return self

    def to_string(self) -> str:
        return '\n'.join(self.lines)


# ---------------------------------------------------------------
# TASK REGISTRY
# ---------------------------------------------------------------
_TASKS: List[Dict[str, Any]] = []


def daily_task(name: str, category: str = 'general',
               critical: bool = False, order: int = 500):
    """Decorator to register a task."""
    def decorator(fn: Callable):
        _TASKS.append({
            'name': name,
            'category': category,
            'critical': critical,
            'order': order,
            'fn': fn,
        })
        _TASKS.sort(key=lambda t: (t['order'], t['name']))
        return fn
    return decorator


class TaskContext:
    """Passed to every task function."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.now = somali_now()
        self.metrics: Dict[str, Dict[str, Any]] = {}
        self.warnings: List[str] = []
        self.action_items: List[Dict[str, str]] = []
        self.log = log

    def set_metric(self, key: str, value: Any, category: str = 'general',
                   unit: Optional[str] = None, text: Optional[str] = None) -> None:
        self.metrics[key] = {
            'value': value,
            'text': text,
            'category': category,
            'unit': unit,
        }

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def action(self, msg: str, link: Optional[str] = None) -> None:
        self.action_items.append({'msg': msg, 'link': link})


# ---------------------------------------------------------------
# SCHEMA
# ---------------------------------------------------------------
def ensure_tables() -> None:
    """Create daily_task_runs and platform_metrics if missing."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            task_name TEXT NOT NULL,
            category TEXT,
            status TEXT NOT NULL,
            items_affected INTEGER DEFAULT 0,
            error_message TEXT,
            started_at TEXT,
            ended_at TEXT,
            duration_ms INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_dtr_run_date
            ON daily_task_runs(run_date DESC);
        CREATE INDEX IF NOT EXISTS idx_dtr_task
            ON daily_task_runs(task_name, run_date DESC);
        CREATE INDEX IF NOT EXISTS idx_dtr_status
            ON daily_task_runs(status);

        CREATE TABLE IF NOT EXISTS platform_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_date TEXT NOT NULL,
            metric_key TEXT NOT NULL,
            value REAL,
            value_text TEXT,
            category TEXT,
            unit TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(metric_date, metric_key)
        );
        CREATE INDEX IF NOT EXISTS idx_pm_date
            ON platform_metrics(metric_date DESC);
        CREATE INDEX IF NOT EXISTS idx_pm_key
            ON platform_metrics(metric_key, metric_date DESC);
        CREATE INDEX IF NOT EXISTS idx_pm_category
            ON platform_metrics(category, metric_date DESC);
    """)
    conn.commit()


def record_task_run(run_date: str, task_name: str, category: str, status: str,
                    items: int = 0, error: Optional[str] = None,
                    started: Optional[str] = None, ended: Optional[str] = None,
                    duration_ms: int = 0) -> None:
    try:
        execute_with_retry("""
            INSERT INTO daily_task_runs
                (run_date, task_name, category, status, items_affected,
                 error_message, started_at, ended_at, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_date, task_name, category, status, items,
            (error[:1000] if error else None),
            started, ended, duration_ms,
        ), commit=True)
    except Exception as e:
        log.error(f"Failed to record task run: {e}")


def persist_metrics(run_date: str, metrics: Dict[str, Dict[str, Any]],
                    dry_run: bool = False) -> int:
    if dry_run:
        return 0
    if not metrics:
        return 0

    conn = get_db()
    count = 0
    for key, payload in metrics.items():
        try:
            value = payload.get('value')
            text = payload.get('text')
            category = payload.get('category', 'general')
            unit = payload.get('unit')
            conn.execute("""
                INSERT INTO platform_metrics
                    (metric_date, metric_key, value, value_text, category, unit)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(metric_date, metric_key) DO UPDATE SET
                    value = excluded.value,
                    value_text = excluded.value_text,
                    category = excluded.category,
                    unit = excluded.unit
            """, (run_date, key, value, text, category, unit))
            count += 1
        except Exception as e:
            log.warning(f"Metric persist failed for {key}: {e}")
    try:
        conn.commit()
    except Exception:
        pass
    return count


# ---------------------------------------------------------------
# LOCK FILE
# ---------------------------------------------------------------
_LOCK_FD = None


def acquire_lock() -> bool:
    global _LOCK_FD
    if os.path.exists(LOCK_FILE):
        try:
            age = time.time() - os.path.getmtime(LOCK_FILE)
            if age < LOCK_STALE_SECONDS:
                log.warning(f"Lock held by another run ({age:.0f}s old). Exiting.")
                return False
            log.warning(f"Stale lock detected (age {age:.0f}s). Overriding.")
        except Exception:
            pass
    try:
        fd = open(LOCK_FILE, 'w')
        fd.write(f'{os.getpid()}\n{somali_format(style="iso")}\n')
        fd.flush()
        _LOCK_FD = fd
        return True
    except Exception as e:
        log.error(f"Failed to acquire lock: {e}")
        return False


def release_lock() -> None:
    global _LOCK_FD
    if _LOCK_FD:
        try:
            _LOCK_FD.close()
        except Exception:
            pass
        _LOCK_FD = None
    try:
        os.remove(LOCK_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------
# TASK RUNNER
# ---------------------------------------------------------------
def run_one_task(task: Dict[str, Any], ctx: TaskContext, run_date: str) -> Dict[str, Any]:
    name = task['name']
    cat = task['category']

    ctx.log = logging.getLogger(f'task.{name}')
    started_dt = somali_now()
    started_iso = somali_format(started_dt, 'iso')
    t0 = time.time()

    result = {
        'name': name,
        'category': cat,
        'status': 'success',
        'items': 0,
        'error': None,
        'duration_ms': 0,
    }

    try:
        out = task['fn'](ctx) or {}
        result['items'] = int(out.get('items', 0))
        if out.get('status'):
            result['status'] = out['status']
    except Exception as e:
        result['status'] = 'failed'
        result['error'] = f"{type(e).__name__}: {e}"
        log.error(f"Task {name} failed: {e}\n{traceback.format_exc()}")
        if task['critical']:
            ctx.warn(f"CRITICAL task failed: {name}")

    result['duration_ms'] = int((time.time() - t0) * 1000)
    ended_iso = somali_format(somali_now(), 'iso')

    record_task_run(
        run_date=run_date,
        task_name=name,
        category=cat,
        status=result['status'],
        items=result['items'],
        error=result['error'],
        started=started_iso,
        ended=ended_iso,
        duration_ms=result['duration_ms'],
    )

    log.info(
        f"[{result['status']:>7}] {name:<38} "
        f"items={result['items']:<5} {result['duration_ms']}ms"
        + (f"  ERR={result['error']}" if result['error'] else '')
    )

    return result


def select_tasks(args) -> List[Dict[str, Any]]:
    tasks = list(_TASKS)
    if args.task:
        tasks = [t for t in tasks if t['name'] == args.task]
        if not tasks:
            print(f"No task found with name: {args.task}")
            sys.exit(2)
    if args.category:
        tasks = [t for t in tasks if t['category'] == args.category]
        if not tasks:
            print(f"No tasks in category: {args.category}")
            sys.exit(2)
    return tasks


def print_task_list() -> None:
    print(f"{'ORDER':<6} {'CATEGORY':<12} {'CRIT':<5} {'NAME':<40}")
    print('-' * 80)
    for t in _TASKS:
        print(f"{t['order']:<6} {t['category']:<12} "
              f"{'YES' if t['critical'] else 'no':<5} {t['name']:<40}")


# ===============================================================
# TASKS
# ===============================================================

# ------------------- HEALTH -------------------

@daily_task('health.database', category='health', critical=True, order=100)
def task_health_database(ctx: TaskContext) -> Dict[str, Any]:
    from database import get_database_health
    health = get_database_health()

    ok = (
        health.get('exists')
        and health.get('openable')
        and health.get('integrity')
        and health.get('wal_enabled')
    )
    ctx.set_metric('health.db_ok', 1 if ok else 0, 'health', 'bool')

    ctx._health_db = {
        'ok': ok,
        'text': 'OK · WAL active' if ok else 'FAILED',
        'details': health,
    }
    if not ok:
        ctx.warn('Database health check failed')
    return {'items': 0}


@daily_task('health.disk', category='health', critical=True, order=110)
def task_health_disk(ctx: TaskContext) -> Dict[str, Any]:
    try:
        stat = os.statvfs(Config.BACKUP_DIR)
        free_bytes = stat.f_bavail * stat.f_frsize
        total_bytes = stat.f_blocks * stat.f_frsize
        free_mb = free_bytes / (1024 * 1024)
        used_pct = 100.0 * (1 - free_bytes / total_bytes) if total_bytes else 0
    except Exception as e:
        ctx._health_disk = {'ok': False, 'text': f'error: {e}'}
        ctx.warn(f"Disk check failed: {e}")
        return {'items': 0}

    ok = free_mb > 200
    ctx.set_metric('health.disk_free_mb', round(free_mb, 1), 'health', 'MB')
    ctx.set_metric('health.disk_used_pct', round(used_pct, 1), 'health', '%')

    ctx._health_disk = {
        'ok': ok,
        'text': f'{used_pct:.0f}% used · {free_mb:.1f} MB free',
        'free_mb': free_mb,
    }
    if not ok:
        ctx.warn(f'Low disk space: {free_mb:.0f} MB free')
    return {'items': 0}


@daily_task('health.backup', category='health', critical=False, order=120)
def task_health_backup(ctx: TaskContext) -> Dict[str, Any]:
    try:
        from backup import BackupManager
        mgr = BackupManager()
        summary = mgr.get_backup_health_summary()
    except Exception as e:
        ctx._health_backup = {'ok': False, 'text': f'error: {e}'}
        ctx.warn(f'Backup check failed: {e}')
        return {'items': 0}

    valid = summary.get('valid_backups', 0)
    last_good = summary.get('last_good') or 'None'
    last_created = summary.get('last_created') or 'never'
    ok = valid > 0 and bool(summary.get('last_good'))

    ctx.set_metric('health.backups_valid', valid, 'health', 'count')
    ctx.set_metric('health.backup_ok', 1 if ok else 0, 'health', 'bool')

    ctx._health_backup = {
        'ok': ok,
        'text': f'{valid} valid · last {last_good}',
        'last_created': last_created,
    }
    if not ok:
        ctx.warn('No valid backups found')
    return {'items': 0}


# ------------------- CLEANUP -------------------

@daily_task('cleanup.expired_tiers', category='cleanup', critical=True, order=200)
def task_cleanup_expired_tiers(ctx: TaskContext) -> Dict[str, Any]:
    """Downgrade any user whose paid tier has expired."""
    now_iso = somali_format(somali_now(), 'iso')
    cursor = execute_with_retry("""
        SELECT id, first_name, tier, tier_expires_at
        FROM students
        WHERE tier IN ('premium', 'pro')
          AND tier_expires_at IS NOT NULL
          AND tier_expires_at < ?
    """, (now_iso,))
    rows = cursor.fetchall()

    count = 0
    for r in rows:
        uid = r['id']
        old_tier = r['tier']
        if ctx.dry_run:
            count += 1
            continue
        try:
            execute_with_retry(
                "UPDATE students SET tier = 'free', tier_updated_at = ? WHERE id = ?",
                (now_iso, uid),
                commit=True,
            )
            # Notify user
            try:
                execute_with_retry("""
                    INSERT INTO notifications
                        (user_id, type, title, body, link, icon, is_read, created_at)
                    VALUES (?, 'tier_expired', ?, ?, ?, '⏰', 0, datetime('now','localtime'))
                """, (
                    uid,
                    'Your premium tier has expired',
                    f'Your {old_tier.upper()} access ended today. Renew to keep using premium features.',
                    '/settings#tier',
                ), commit=True)
            except Exception:
                pass
            count += 1
        except Exception as e:
            log.warning(f"Failed to expire tier for user {uid}: {e}")

    ctx.set_metric('cleanup.tiers_downgraded', count, 'cleanup', 'count')
    ctx._health_summary = ctx._health_summary if hasattr(ctx, '_health_summary') else {}

    if count > 0:
        ctx.action(
            f'{count} user(s) downgraded to Free — consider a re-engagement push',
            f'{BASE_URL}/admin/users' if BASE_URL else None,
        )
    return {'items': count}


@daily_task('cleanup.history', category='cleanup', critical=False, order=210)
def task_cleanup_history(ctx: TaskContext) -> Dict[str, Any]:
    """Trim each user's history according to their tier retention."""
    if ctx.dry_run:
        return {'items': 0, 'status': 'skipped'}

    try:
        from services.tier_service import (
            get_history_retention_days, get_history_max_entries,
        )
    except Exception as e:
        ctx.warn(f'tier_service unavailable: {e}')
        return {'items': 0, 'status': 'skipped'}

    cursor = execute_with_retry("SELECT id FROM students")
    users = cursor.fetchall()

    deleted_total = 0
    from datetime import datetime as _dt, timedelta as _td

    for u in users:
        uid = u['id']
        retention = get_history_retention_days(uid)
        max_entries = get_history_max_entries(uid)

        if retention and retention > 0:
            cutoff = (_dt.now() - _td(days=retention)).isoformat()
            try:
                c = execute_with_retry(
                    "DELETE FROM history_entries WHERE user_id = ? AND created_at < ?",
                    (uid, cutoff), commit=True,
                )
                deleted_total += c.rowcount or 0
            except Exception:
                pass

        if max_entries and max_entries > 0:
            try:
                c = execute_with_retry(
                    "SELECT id FROM history_entries WHERE user_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (uid, max_entries),
                )
                keep = [r['id'] for r in c.fetchall()]
                if keep:
                    placeholders = ','.join('?' * len(keep))
                    c2 = execute_with_retry(
                        f"DELETE FROM history_entries WHERE user_id = ? "
                        f"AND id NOT IN ({placeholders})",
                        [uid] + keep, commit=True,
                    )
                    deleted_total += c2.rowcount or 0
                else:
                    c2 = execute_with_retry(
                        "DELETE FROM history_entries WHERE user_id = ?",
                        (uid,), commit=True,
                    )
                    deleted_total += c2.rowcount or 0
            except Exception:
                pass

    ctx.set_metric('cleanup.history_trimmed', deleted_total, 'cleanup', 'count')
    return {'items': deleted_total}


@daily_task('cleanup.notifications', category='cleanup', critical=False, order=220)
def task_cleanup_notifications(ctx: TaskContext) -> Dict[str, Any]:
    if ctx.dry_run:
        return {'items': 0, 'status': 'skipped'}
    try:
        c = execute_with_retry(
            "DELETE FROM notifications "
            "WHERE is_read = 1 AND created_at < datetime('now', ? || ' days')",
            (f'-{RETENTION_NOTIFICATIONS_DAYS}',),
            commit=True,
        )
        n = c.rowcount or 0
    except Exception as e:
        ctx.warn(f'Notification cleanup failed: {e}')
        n = 0
    ctx.set_metric('cleanup.notifications_purged', n, 'cleanup', 'count')
    return {'items': n}


@daily_task('cleanup.errors', category='cleanup', critical=False, order=230)
def task_cleanup_errors(ctx: TaskContext) -> Dict[str, Any]:
    if ctx.dry_run:
        return {'items': 0, 'status': 'skipped'}
    try:
        c = execute_with_retry(
            "DELETE FROM error_logs "
            "WHERE resolved = 1 AND timestamp < datetime('now', ? || ' days')",
            (f'-{RETENTION_ERRORS_DAYS}',),
            commit=True,
        )
        n = c.rowcount or 0
    except Exception as e:
        ctx.warn(f'Error cleanup failed: {e}')
        n = 0
    ctx.set_metric('cleanup.errors_purged', n, 'cleanup', 'count')
    return {'items': n}


@daily_task('cleanup.activity_logs', category='cleanup', critical=False, order=240)
def task_cleanup_activity_logs(ctx: TaskContext) -> Dict[str, Any]:
    if ctx.dry_run:
        return {'items': 0, 'status': 'skipped'}
    try:
        c = execute_with_retry(
            "DELETE FROM activity_logs "
            "WHERE created_at < datetime('now', ? || ' days')",
            (f'-{RETENTION_ACTIVITY_DAYS}',),
            commit=True,
        )
        n = c.rowcount or 0
    except Exception as e:
        ctx.warn(f'Activity log cleanup failed: {e}')
        n = 0
    ctx.set_metric('cleanup.activity_purged', n, 'cleanup', 'count')
    return {'items': n}


# ------------------- FOCUS -------------------

@daily_task('focus.validate_pdf_links', category='focus', critical=False, order=300)
def task_focus_validate_pdf_links(ctx: TaskContext) -> Dict[str, Any]:
    """Find questions pointing to PDF codes that no longer exist."""
    cursor = execute_with_retry("""
        SELECT DISTINCT pdf_code
        FROM questions
        WHERE pdf_code IS NOT NULL AND pdf_code != ''
          AND status = 'active'
    """)
    codes = [r['pdf_code'] for r in cursor.fetchall()]
    if not codes:
        ctx.set_metric('focus.broken_links', 0, 'focus', 'count')
        return {'items': 0}

    # Check against main DB
    placeholders = ','.join('?' * len(codes))
    cursor = execute_with_retry(
        f"SELECT code FROM pdfs WHERE code IN ({placeholders})",
        codes,
    )
    main_codes = {r['code'] for r in cursor.fetchall()}

    # Check against bot DB
    missing = [c for c in codes if c not in main_codes]
    bot_codes: set = set()
    if missing:
        try:
            from bot.db import get_bot_pdf_by_code
            for c in missing:
                if get_bot_pdf_by_code(c):
                    bot_codes.add(c)
        except Exception:
            pass

    broken = [c for c in codes if c not in main_codes and c not in bot_codes]

    ctx.set_metric('focus.broken_links', len(broken), 'focus', 'count')
    if broken:
        ctx.warn(f'{len(broken)} question(s) point to missing PDFs')
        ctx.action(
            f'{len(broken)} questions have broken PDF source',
            f'{BASE_URL}/admin/questions?pdf=missing' if BASE_URL else None,
        )
    return {'items': len(broken)}


@daily_task('focus.rebuild_coverage', category='focus', critical=False, order=310)
def task_focus_rebuild_coverage(ctx: TaskContext) -> Dict[str, Any]:
    """Compute PDF-link coverage per subject."""
    try:
        cursor = execute_with_retry("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN pdf_code IS NOT NULL AND pdf_code != '' THEN 1 ELSE 0 END) AS linked
            FROM questions WHERE status = 'active'
        """)
        row = cursor.fetchone()
        total = row['total'] or 0
        linked = row['linked'] or 0
        pct = round(100.0 * linked / total, 1) if total else 0
        ctx.set_metric('content.questions_total', total, 'content', 'count')
        ctx.set_metric('content.questions_linked_pct', pct, 'content', '%')
        ctx.set_metric('content.questions_linked', linked, 'content', 'count')
    except Exception as e:
        ctx.warn(f'Coverage rebuild failed: {e}')
    return {'items': 0}


# ------------------- LIVE QUIZ -------------------

@daily_task('live_quiz.cleanup_orphaned', category='live_quiz', critical=False, order=400)
def task_live_quiz_cleanup_orphaned(ctx: TaskContext) -> Dict[str, Any]:
    """Remove waiting/scheduled quizzes that were abandoned."""
    if ctx.dry_run:
        return {'items': 0, 'status': 'skipped'}

    cutoff_waiting = somali_format(somali_now() - timedelta(hours=48), 'iso')
    cutoff_scheduled = somali_format(somali_now() - timedelta(days=RETENTION_LIVE_QUIZ_DAYS), 'iso')

    total = 0
    try:
        c = execute_with_retry(
            "SELECT id FROM live_quizzes WHERE status = 'waiting' AND created_at < ?",
            (cutoff_waiting,),
        )
        wait_ids = [r['id'] for r in c.fetchall()]
        for qid in wait_ids:
            try:
                execute_with_retry("DELETE FROM live_quiz_participants WHERE quiz_id = ?",
                                   (qid,), commit=True)
                execute_with_retry("DELETE FROM live_quizzes WHERE id = ?",
                                   (qid,), commit=True)
                total += 1
            except Exception:
                pass

        c = execute_with_retry(
            "SELECT id FROM live_quizzes "
            "WHERE status = 'scheduled' AND scheduled_start IS NOT NULL AND scheduled_start < ?",
            (cutoff_scheduled,),
        )
        sched_ids = [r['id'] for r in c.fetchall()]
        for qid in sched_ids:
            try:
                execute_with_retry("DELETE FROM live_quiz_participants WHERE quiz_id = ?",
                                   (qid,), commit=True)
                execute_with_retry("DELETE FROM live_quizzes WHERE id = ?",
                                   (qid,), commit=True)
                total += 1
            except Exception:
                pass
    except Exception as e:
        ctx.warn(f'Live-quiz cleanup failed: {e}')

    ctx.set_metric('cleanup.live_quizzes_purged', total, 'cleanup', 'count')
    return {'items': total}


# ------------------- ANALYTICS -------------------

@daily_task('analytics.collect_core', category='analytics', critical=False, order=500)
def task_collect_core_metrics(ctx: TaskContext) -> Dict[str, Any]:
    """Gather the headline numbers used in the daily report."""

    def scalar(sql: str, params: tuple = ()) -> int:
        try:
            c = execute_with_retry(sql, params)
            r = c.fetchone()
            if not r:
                return 0
            return int(list(r)[0] or 0)
        except Exception as e:
            log.warning(f"scalar failed: {e} | {sql[:60]}")
            return 0

    # Users
    ctx.set_metric('users.total', scalar("SELECT COUNT(*) FROM students"), 'users', 'count')
    ctx.set_metric('users.new_today',
                   scalar("SELECT COUNT(*) FROM students WHERE created_at >= datetime('now', '-1 day')"),
                   'users', 'count')
    ctx.set_metric('users.new_7d',
                   scalar("SELECT COUNT(*) FROM students WHERE created_at >= datetime('now', '-7 days')"),
                   'users', 'count')
    ctx.set_metric('users.active_24h',
                   scalar("SELECT COUNT(DISTINCT student_id) FROM quiz_attempts "
                          "WHERE completed_at >= datetime('now', '-1 day')"),
                   'users', 'count')
    ctx.set_metric('users.verified',
                   scalar("SELECT COUNT(*) FROM students WHERE is_verified = 1"),
                   'users', 'count')

    # Tier distribution
    ctx.set_metric('users.tier_free',
                   scalar("SELECT COUNT(*) FROM students WHERE tier = 'free'"),
                   'users', 'count')
    ctx.set_metric('users.tier_premium',
                   scalar("SELECT COUNT(*) FROM students WHERE tier = 'premium'"),
                   'users', 'count')
    ctx.set_metric('users.tier_pro',
                   scalar("SELECT COUNT(*) FROM students WHERE tier = 'pro'"),
                   'users', 'count')

    # Quizzes
    quizzes_today = scalar(
        "SELECT COUNT(*) FROM quiz_attempts WHERE completed_at >= datetime('now', '-1 day')"
    )
    ctx.set_metric('quizzes.taken_today', quizzes_today, 'quizzes', 'count')
    ctx.set_metric('quizzes.taken_7d',
                   scalar("SELECT COUNT(*) FROM quiz_attempts WHERE completed_at >= datetime('now', '-7 days')"),
                   'quizzes', 'count')

    # Avg score today
    try:
        c = execute_with_retry("""
            SELECT AVG(CAST(score AS REAL) / NULLIF(total_questions, 0)) AS avg_pct
            FROM quiz_attempts
            WHERE completed_at >= datetime('now', '-1 day')
        """)
        row = c.fetchone()
        avg_pct = round((row['avg_pct'] or 0) * 100, 1) if row else 0
    except Exception:
        avg_pct = 0
    ctx.set_metric('quizzes.avg_score_today', avg_pct, 'quizzes', '%')

    # Content
    ctx.set_metric('content.pdfs_total', scalar("SELECT COUNT(*) FROM pdfs"), 'content', 'count')
    ctx.set_metric('content.questions_total',
                   scalar("SELECT COUNT(*) FROM questions WHERE status = 'active'"),
                   'content', 'count')

    # Focus
    ctx.set_metric('focus.bookmarks_total',
                   scalar("SELECT COUNT(DISTINCT question_id) FROM question_interactions "
                          "WHERE interaction_type IN ('save', 'like')"),
                   'focus', 'count')
    ctx.set_metric('focus.bookmarks_today',
                   scalar("SELECT COUNT(*) FROM question_interactions "
                          "WHERE interaction_type IN ('save', 'like') "
                          "AND created_at >= datetime('now', '-1 day')"),
                   'focus', 'count')

    # Revenue
    ctx.set_metric('revenue.approvals_today',
                   scalar("SELECT COUNT(*) FROM upgrade_requests "
                          "WHERE status = 'approved' AND approved_at >= datetime('now', '-1 day')"),
                   'revenue', 'count')
    try:
        c = execute_with_retry("""
            SELECT COALESCE(SUM(final_price_cents), 0) AS cents
            FROM upgrade_requests
            WHERE status = 'approved' AND approved_at >= datetime('now', '-1 day')
        """)
        r = c.fetchone()
        cents = int(r['cents'] or 0)
    except Exception:
        cents = 0
    ctx.set_metric('revenue.amount_today_cents', cents, 'revenue', 'cents')
    ctx.set_metric('revenue.amount_today_usd', round(cents / 100.0, 2), 'revenue', 'USD')
    ctx.set_metric('revenue.pending_requests',
                   scalar("SELECT COUNT(*) FROM upgrade_requests WHERE status = 'pending'"),
                   'revenue', 'count')

    # Errors
    ctx.set_metric('health.errors_new_today',
                   scalar("SELECT COUNT(*) FROM error_logs WHERE timestamp >= datetime('now', '-1 day')"),
                   'health', 'count')
    ctx.set_metric('health.errors_open',
                   scalar("SELECT COUNT(*) FROM error_logs WHERE resolved = 0 AND dismissed = 0"),
                   'health', 'count')

    # Live quiz
    ctx.set_metric('live_quizzes.scheduled_today',
                   scalar("SELECT COUNT(*) FROM live_quizzes WHERE status = 'scheduled'"),
                   'live_quizzes', 'count')
    ctx.set_metric('live_quizzes.finished_7d',
                   scalar("SELECT COUNT(*) FROM live_quizzes "
                          "WHERE status = 'finished' AND ended_at >= datetime('now', '-7 days')"),
                   'live_quizzes', 'count')

    return {'items': 0}


# ===============================================================
# DB SNAPSHOT
# ===============================================================

def create_db_snapshot() -> Tuple[Optional[str], int, Optional[str]]:
    """
    Create a fresh compressed snapshot of the main DB.
    Returns (path, size_bytes, sha256_hex) or (None, 0, None) on failure.
    """
    src_path = Config.DATABASE_PATH
    if not os.path.exists(src_path):
        log.error(f"Source DB not found: {src_path}")
        return None, 0, None

    try:
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        stamp = somali_now().strftime('%Y%m%d_%H%M')
        temp_db = os.path.join(SNAPSHOT_DIR, f'nuunplatform_{stamp}.db')
        out_gz = os.path.join(SNAPSHOT_DIR, f'nuunplatform_{stamp}.db.gz')

        # Use sqlite backup API (safe while DB is live)
        src = sqlite3.connect(src_path, timeout=30)
        src.execute("PRAGMA journal_mode = WAL")
        dst = sqlite3.connect(temp_db, timeout=30)
        src.backup(dst)
        dst.close()
        src.close()

        with open(temp_db, 'rb') as fin:
            with gzip.open(out_gz, 'wb', compresslevel=6) as fout:
                shutil.copyfileobj(fin, fout)

        try:
            os.remove(temp_db)
        except Exception:
            pass

        size = os.path.getsize(out_gz)
        sha = hashlib.sha256()
        with open(out_gz, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                sha.update(chunk)

        return out_gz, size, sha.hexdigest()
    except Exception as e:
        log.error(f"Snapshot failed: {e}\n{traceback.format_exc()}")
        return None, 0, None


# ===============================================================
# TELEGRAM DELIVERY
# ===============================================================

def get_super_admin_ids() -> List[int]:
    ids_str = getattr(Config, 'TELEGRAM_SUPER_ADMIN_IDS', '') or ''
    if not ids_str.strip():
        ids_str = getattr(Config, 'TELEGRAM_ADMIN_IDS', '') or ''
    try:
        return [int(x.strip()) for x in ids_str.split(',') if x.strip()]
    except Exception:
        return []


def tg_call(method: str, data: Optional[Dict] = None,
            files: Optional[Dict] = None, timeout: int = 30) -> Dict:
    token = getattr(Config, 'TELEGRAM_BOT_TOKEN', '') or ''
    if not token:
        return {'ok': False, 'error': 'no_token'}

    url = TELEGRAM_API.format(token=token, method=method)
    try:
        if files:
            r = requests.post(url, data=data or {}, files=files, timeout=timeout)
        else:
            r = requests.post(url, json=data or {}, timeout=timeout)
        return r.json()
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def build_report_message(ctx: TaskContext, run_summary: Dict[str, Any]) -> str:
    """Compose the main Telegram message (MarkdownV2)."""
    m = Msg()

    # ------- HEADER -------
    m.bold('📊 NUUNPLATFORM — DAILY REPORT')
    m.italic(somali_format(ctx.now))
    duration = run_summary.get('duration_seconds', 0)
    m.raw(mdv2_escape(f'Run {run_summary.get("started_at", "")} — {run_summary.get("ended_at", "")} ({duration:.0f}s)'))
    m.blank()
    m.divider()
    m.blank()

    # ------- HEALTH -------
    m.bold('🟢 SYSTEM HEALTH')
    h_db = getattr(ctx, '_health_db', None)
    h_disk = getattr(ctx, '_health_disk', None)
    h_backup = getattr(ctx, '_health_backup', None)
    if h_db:
        m.raw(f'{("✅" if h_db["ok"] else "❌")} Database — {mdv2_escape(h_db["text"])}')
    if h_disk:
        m.raw(f'{("✅" if h_disk["ok"] else "⚠️")} Disk — {mdv2_escape(h_disk["text"])}')
    if h_backup:
        m.raw(f'{("✅" if h_backup["ok"] else "⚠️")} Backup — {mdv2_escape(h_backup["text"])}')
    m.raw(f'⚠️ Errors new (24h) — *{mdv2_escape(ctx.metrics.get("health.errors_new_today", {}).get("value", 0))}*')
    m.blank()
    m.divider()
    m.blank()

    # ------- USERS -------
    m.bold('👥 USERS')
    m.raw(f'• Total — *{mdv2_escape(ctx.metrics.get("users.total", {}).get("value", 0))}*')
    m.raw(f'• New today — *{mdv2_escape(ctx.metrics.get("users.new_today", {}).get("value", 0))}*')
    m.raw(f'• New this week — *{mdv2_escape(ctx.metrics.get("users.new_7d", {}).get("value", 0))}*')
    m.raw(f'• Active 24h — *{mdv2_escape(ctx.metrics.get("users.active_24h", {}).get("value", 0))}*')
    m.raw(f'• Verified — *{mdv2_escape(ctx.metrics.get("users.verified", {}).get("value", 0))}*')
    m.blank()
    m.raw('Tier distribution')
    m.raw(f'  Free — *{mdv2_escape(ctx.metrics.get("users.tier_free", {}).get("value", 0))}* · '
          f'Premium — *{mdv2_escape(ctx.metrics.get("users.tier_premium", {}).get("value", 0))}* · '
          f'Pro — *{mdv2_escape(ctx.metrics.get("users.tier_pro", {}).get("value", 0))}*')
    if ctx.metrics.get('cleanup.tiers_downgraded', {}).get('value'):
        m.raw(f'⬇️ Downgraded — *{mdv2_escape(ctx.metrics["cleanup.tiers_downgraded"]["value"])}*')
    m.blank()
    m.divider()
    m.blank()

    # ------- QUIZZES -------
    m.bold('📝 QUIZZES')
    m.raw(f'• Taken today — *{mdv2_escape(ctx.metrics.get("quizzes.taken_today", {}).get("value", 0))}*')
    m.raw(f'• Taken 7d — *{mdv2_escape(ctx.metrics.get("quizzes.taken_7d", {}).get("value", 0))}*')
    m.raw(f'• Avg score — *{mdv2_escape(ctx.metrics.get("quizzes.avg_score_today", {}).get("value", 0))}%*')
    m.blank()
    m.divider()
    m.blank()

    # ------- CONTENT -------
    m.bold('📄 CONTENT')
    m.raw(f'• PDFs total — *{mdv2_escape(ctx.metrics.get("content.pdfs_total", {}).get("value", 0))}*')
    m.raw(f'• Questions — *{mdv2_escape(ctx.metrics.get("content.questions_total", {}).get("value", 0))}*')
    cov = ctx.metrics.get('content.questions_linked_pct', {}).get('value', 0)
    m.raw(f'• PDF-linked — *{mdv2_escape(cov)}%*')
    broken = ctx.metrics.get('focus.broken_links', {}).get('value', 0)
    if broken:
        m.raw(f'⚠️ Broken source links — *{mdv2_escape(broken)}*')
    m.blank()
    m.divider()
    m.blank()

    # ------- FOCUS -------
    m.bold('🎯 FOCUS')
    m.raw(f'• Bookmarks total — *{mdv2_escape(ctx.metrics.get("focus.bookmarks_total", {}).get("value", 0))}*')
    m.raw(f'• New today — *{mdv2_escape(ctx.metrics.get("focus.bookmarks_today", {}).get("value", 0))}*')
    m.blank()
    m.divider()
    m.blank()

    # ------- REVENUE -------
    m.bold('💰 REVENUE')
    m.raw(f'• Approvals today — *{mdv2_escape(ctx.metrics.get("revenue.approvals_today", {}).get("value", 0))}*')
    m.raw(f'• Amount today — *${mdv2_escape(ctx.metrics.get("revenue.amount_today_usd", {}).get("value", 0))}*')
    m.raw(f'• Pending — *{mdv2_escape(ctx.metrics.get("revenue.pending_requests", {}).get("value", 0))}*')
    m.blank()
    m.divider()
    m.blank()

    # ------- MAINTENANCE -------
    m.bold('🧹 MAINTENANCE')
    def _mv(key):
        return ctx.metrics.get(key, {}).get('value', 0)
    m.raw(f'• Tier downgrades — *{mdv2_escape(_mv("cleanup.tiers_downgraded"))}*')
    m.raw(f'• History trimmed — *{mdv2_escape(_mv("cleanup.history_trimmed"))}*')
    m.raw(f'• Notifications purged — *{mdv2_escape(_mv("cleanup.notifications_purged"))}*')
    m.raw(f'• Errors purged — *{mdv2_escape(_mv("cleanup.errors_purged"))}*')
    m.raw(f'• Activity purged — *{mdv2_escape(_mv("cleanup.activity_purged"))}*')
    m.raw(f'• Orphan live quizzes — *{mdv2_escape(_mv("cleanup.live_quizzes_purged"))}*')
    m.blank()
    m.divider()
    m.blank()

    # ------- TASK SUMMARY -------
    m.bold('⚙️ TASKS')
    m.raw(f'• Executed — *{mdv2_escape(run_summary.get("tasks_total", 0))}*')
    m.raw(f'• Succeeded — *{mdv2_escape(run_summary.get("tasks_success", 0))}*')
    failed = run_summary.get('tasks_failed', 0)
    if failed:
        m.raw(f'• ❌ Failed — *{mdv2_escape(failed)}*')
        for f in run_summary.get('failed_names', [])[:5]:
            m.raw(f'   – {mdv2_escape(f)}')

    # ------- ANOMALIES -------
    if ctx.warnings:
        m.blank()
        m.divider()
        m.blank()
        m.bold('⚠️ ANOMALIES')
        for w in ctx.warnings:
            m.raw(f'• {mdv2_escape(w)}')

    # ------- ACTIONS -------
    if ctx.action_items:
        m.blank()
        m.divider()
        m.blank()
        m.bold('🎯 ACTION NEEDED')
        for a in ctx.action_items:
            msg = a.get('msg', '')
            link = a.get('link')
            if link:
                m.raw(f'• {md_italic(msg)} → {md_link("Open", link)}')
            else:
                m.raw(f'• {mdv2_escape(msg)}')

    return m.to_string()


def build_plain_markdown_report(ctx: TaskContext, run_summary: Dict[str, Any]) -> str:
    """A plain markdown version of the report — attached as a file."""
    L: List[str] = []
    L.append('# NuunPlatform — Daily Report')
    L.append(f'*{somali_format(ctx.now)}*')
    L.append('')
    L.append(f'Duration: {run_summary.get("duration_seconds", 0):.0f}s · '
             f'Tasks: {run_summary.get("tasks_total", 0)} '
             f'({run_summary.get("tasks_success", 0)} ok, {run_summary.get("tasks_failed", 0)} failed)')
    L.append('')
    L.append('## Metrics')
    L.append('')
    L.append('| Metric | Value |')
    L.append('|--------|-------|')
    for k, v in sorted(ctx.metrics.items()):
        L.append(f'| `{k}` | {v.get("value")} {v.get("unit") or ""} |')
    L.append('')

    if ctx.warnings:
        L.append('## Warnings')
        for w in ctx.warnings:
            L.append(f'- {w}')
        L.append('')

    if ctx.action_items:
        L.append('## Action Items')
        for a in ctx.action_items:
            L.append(f'- {a.get("msg")}' + (f' ({a.get("link")})' if a.get('link') else ''))
        L.append('')

    L.append('## Task Results')
    L.append('')
    L.append('| Task | Status | Items | ms |')
    L.append('|------|--------|-------|-----|')
    for t in run_summary.get('results', []):
        L.append(f'| {t["name"]} | {t["status"]} | {t["items"]} | {t["duration_ms"]} |')

    return '\n'.join(L)


def send_report_to_super_admins(ctx: TaskContext, run_summary: Dict[str, Any],
                                snapshot_path: Optional[str] = None,
                                snapshot_size: int = 0,
                                snapshot_sha: Optional[str] = None) -> int:
    admins = get_super_admin_ids()
    if not admins:
        log.warning("No super admins configured — skipping Telegram delivery.")
        return 0

    if BASE_URL:
        log.info(f"Base URL: {BASE_URL}")

    message = build_report_message(ctx, run_summary)
    plain_report = build_plain_markdown_report(ctx, run_summary)

    # Save plain report to disk (always) — attached AND kept locally
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_filename = f'daily_report_{ctx.now.strftime("%Y%m%d")}.md'
    report_path = os.path.join(REPORT_DIR, report_filename)
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(plain_report)
    except Exception as e:
        log.warning(f"Could not write plain report: {e}")
        report_path = None

    sent = 0
    for admin_id in admins:
        # 1) Send the main message
        r = tg_call('sendMessage', {
            'chat_id': admin_id,
            'text': message,
            'parse_mode': 'MarkdownV2',
            'disable_web_page_preview': True,
        })
        if not r.get('ok'):
            log.error(f"sendMessage failed for admin {admin_id}: {r}")
            # Retry once
            time.sleep(2)
            r = tg_call('sendMessage', {
                'chat_id': admin_id,
                'text': message,
                'parse_mode': 'MarkdownV2',
                'disable_web_page_preview': True,
            })
            if not r.get('ok'):
                log.error(f"sendMessage retry failed for admin {admin_id}: {r}")
                continue

        # 2) Attach DB snapshot
        if snapshot_path and os.path.exists(snapshot_path):
            try:
                size_mb = snapshot_size / (1024 * 1024)
                caption = (
                    f'📎 Database snapshot · {size_mb:.2f} MB\n'
                    f'SHA-256: {snapshot_sha[:16] if snapshot_sha else "unknown"}...'
                )
                with open(snapshot_path, 'rb') as f:
                    r2 = tg_call('sendDocument', data={
                        'chat_id': admin_id,
                        'caption': caption,
                    }, files={'document': f})
                if not r2.get('ok'):
                    log.warning(f"sendDocument (snapshot) failed for admin {admin_id}: {r2}")
            except Exception as e:
                log.warning(f"Snapshot upload error: {e}")

        # 3) Attach full plain-text report
        if report_path and os.path.exists(report_path):
            try:
                with open(report_path, 'rb') as f:
                    r3 = tg_call('sendDocument', data={
                        'chat_id': admin_id,
                        'caption': '📄 Full daily report',
                    }, files={'document': f})
                if not r3.get('ok'):
                    log.warning(f"sendDocument (report) failed for admin {admin_id}: {r3}")
            except Exception as e:
                log.warning(f"Report upload error: {e}")

        sent += 1

    return sent


# ===============================================================
# MAIN RUN
# ===============================================================

def run_all(args, log_: logging.Logger) -> int:
    t_start = time.time()
    run_date = metric_date_key()
    ctx = TaskContext(dry_run=args.dry_run)

    started_at = somali_format(ctx.now, 'time')
    log_.info('=' * 60)
    log_.info(f'DAILY TASK RUN — {run_date}')
    log_.info(f'Started: {started_at} (Somali) · dry_run={args.dry_run}')
    log_.info('=' * 60)

    try:
        ensure_tables()
    except Exception as e:
        log_.error(f"Schema setup failed: {e}\n{traceback.format_exc()}")
        return 1

    tasks = select_tasks(args)
    log_.info(f"Running {len(tasks)} task(s).")

    results: List[Dict[str, Any]] = []
    for t in tasks:
        r = run_one_task(t, ctx, run_date)
        results.append(r)

    elapsed = time.time() - t_start
    ended_at = somali_format(somali_now(), 'time')

    failed = [r for r in results if r['status'] == 'failed']
    success = [r for r in results if r['status'] == 'success']

    run_summary = {
        'started_at': started_at,
        'ended_at': ended_at,
        'duration_seconds': elapsed,
        'tasks_total': len(results),
        'tasks_success': len(success),
        'tasks_failed': len(failed),
        'failed_names': [f['name'] for f in failed],
        'results': results,
    }

    log_.info('-' * 60)
    log_.info(f"Tasks: {len(success)} ok / {len(failed)} failed  ·  {elapsed:.1f}s")
    log_.info('-' * 60)

    # ---------- Persist metrics ----------
    if not args.dry_run:
        n_metrics = persist_metrics(run_date, ctx.metrics, dry_run=False)
        log_.info(f"Persisted {n_metrics} metrics to platform_metrics")
    else:
        log_.info("DRY RUN — skipping metric persistence")

    # ---------- Snapshot ----------
    snapshot_path: Optional[str] = None
    snapshot_size = 0
    snapshot_sha: Optional[str] = None

    if not args.no_snapshot and not args.dry_run:
        snapshot_path, snapshot_size, snapshot_sha = create_db_snapshot()
        if snapshot_path:
            log_.info(f"Snapshot created: {snapshot_path} "
                      f"({snapshot_size / (1024 * 1024):.2f} MB)")
        else:
            log_.warning("Snapshot creation failed")
            ctx.warn('DB snapshot creation failed')

    # ---------- Telegram ----------
    if not args.no_telegram and not args.dry_run:
        sent = send_report_to_super_admins(
            ctx, run_summary,
            snapshot_path=snapshot_path,
            snapshot_size=snapshot_size,
            snapshot_sha=snapshot_sha,
        )
        log_.info(f"Report sent to {sent} super admin(s)")
    elif args.dry_run:
        log_.info("DRY RUN — printing report preview")
        preview = build_report_message(ctx, run_summary)
        print()
        print('=' * 60)
        print('MESSAGE PREVIEW')
        print('=' * 60)
        print(preview)
        print('=' * 60)

    log_.info('=' * 60)
    log_.info(f'DAILY TASK RUN COMPLETE — {somali_format(style="short")}')
    log_.info('=' * 60)

    # Exit 0 even with task failures (so PythonAnywhere doesn't retry)
    return 0


# ===============================================================
# CLI
# ===============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description='NuunPlatform Daily Task Runner',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Run without mutating anything')
    parser.add_argument('--task', metavar='NAME',
                        help='Run a single task by name')
    parser.add_argument('--category', metavar='CAT',
                        help='Run tasks in one category')
    parser.add_argument('--list', action='store_true',
                        help='List all registered tasks and exit')
    parser.add_argument('--no-telegram', action='store_true',
                        help='Skip Telegram delivery')
    parser.add_argument('--no-snapshot', action='store_true',
                        help='Skip DB snapshot creation')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose logging')

    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    if args.list:
        print_task_list()
        return 0

    if not acquire_lock():
        log.warning("Could not acquire lock — another run is in progress.")
        return 0

    try:
        return run_all(args, log)
    except Exception as e:
        log.error(f"Fatal error in run: {e}\n{traceback.format_exc()}")
        return 1
    finally:
        release_lock()
        try:
            close_db_connections()
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())