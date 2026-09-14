#!/usr/bin/env python3
# daily_tasks.py — NuunPlatform Daily Task Runner
# ===============================================================
# Single-file daily maintenance + reporting system.
# Uses telebot (pyTelegramBotAPI) via bot.utils.get_bot() for delivery.
#
# Usage:
#   python daily_tasks.py                    # full run
#   python daily_tasks.py --dry-run          # no mutations, preview to console
#   python daily_tasks.py --preview-telegram # send real report, no mutations
#   python daily_tasks.py --list             # list all tasks
#   python daily_tasks.py --task NAME        # run one task
#   python daily_tasks.py --category cleanup
#   python daily_tasks.py --no-telegram
#   python daily_tasks.py --no-snapshot
#   python daily_tasks.py -v                 # verbose
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
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------
# PATH & ENV SETUP
# ---------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
except Exception:
    pass

import telebot

from config import Config
from db import get_db, execute_with_retry, close_db_connections
from utils import SOMALI_TIMEZONE


# ---------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------
LOG_FILE = os.path.join(Config.LOG_DIR, 'daily_tasks.log')
LOCK_FILE = os.path.join(str(BASE_DIR), '.daily_tasks.lock')
REPORT_DIR = os.path.join(Config.LOG_DIR, 'daily_reports')
SNAPSHOT_DIR = os.path.join(Config.LOG_DIR, 'daily_snapshots')

BASE_URL = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')

RETENTION_NOTIFICATIONS_DAYS = 30
RETENTION_ERRORS_DAYS = 30
RETENTION_ACTIVITY_DAYS = 60
RETENTION_LIVE_QUIZ_DAYS = 7

LOCK_STALE_SECONDS = 2 * 3600
MESSAGE_TRIM_THRESHOLD = 3500


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
# TIME HELPERS
# ---------------------------------------------------------------
def somali_now() -> datetime:
    return datetime.now(SOMALI_TIMEZONE)


def somali_format(dt: Optional[datetime] = None, style: str = 'long') -> str:
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
    return ''.join(('\\' + c) if c in _MDV2_SPECIAL else c for c in str(text))


def md_bold(text: Any) -> str:
    return f'*{mdv2_escape(text)}*'


def md_italic(text: Any) -> str:
    return f'_{mdv2_escape(text)}_'


def md_link(text: Any, url: str) -> str:
    safe_url = url.replace('\\', '\\\\').replace(')', '\\)')
    return f'[{mdv2_escape(text)}]({safe_url})'


def _fmt_num(v: Any) -> str:
    if isinstance(v, int) and abs(v) >= 1000:
        return f'{v:,}'
    if isinstance(v, float):
        if v == int(v):
            return f'{int(v):,}' if abs(v) >= 1000 else str(int(v))
        return f'{v:,.1f}'
    return str(v)


class Msg:
    def __init__(self) -> None:
        self.lines: List[str] = []

    def raw(self, s: str) -> 'Msg':
        self.lines.append(s)
        return self

    def text(self, s: str) -> 'Msg':
        self.lines.append(mdv2_escape(s))
        return self

    def italic(self, s: str) -> 'Msg':
        self.lines.append(md_italic(s))
        return self

    def blank(self) -> 'Msg':
        self.lines.append('')
        return self

    def divider(self) -> 'Msg':
        self.lines.append('━' * 18)
        return self

    def h1(self, text: str) -> 'Msg':
        self.lines.append(md_bold(text))
        return self

    def h2(self, text: str) -> 'Msg':
        self.lines.append(md_bold(text))
        return self

    def kv(self, label: str, value: Any, icon: Optional[str] = None) -> 'Msg':
        lead = f'{icon}  ' if icon else '•  '
        self.lines.append(
            f'{mdv2_escape(lead + label + " — ")}*{mdv2_escape(_fmt_num(value))}*'
        )
        return self

    def status_line(self, icon: str, label: str, value: Any) -> 'Msg':
        self.lines.append(
            f'{icon}  {mdv2_escape(label)} — *{mdv2_escape(_fmt_num(value))}*'
        )
        return self

    def chip_line(self, *chips: Tuple[str, Any]) -> 'Msg':
        parts = [
            f'{mdv2_escape(label + " ")}*{mdv2_escape(_fmt_num(value))}*'
            for label, value in chips
        ]
        self.lines.append('   ' + '  ·  '.join(parts))
        return self

    def action(self, msg: str, url: Optional[str] = None) -> 'Msg':
        if url:
            self.lines.append(f'▸  {mdv2_escape(msg)}\n   → {md_link("Open", url)}')
        else:
            self.lines.append(f'▸  {mdv2_escape(msg)}')
        return self

    def warn(self, msg: str) -> 'Msg':
        self.lines.append(f'⚠️  {mdv2_escape(msg)}')
        return self

    def to_string(self) -> str:
        return '\n'.join(self.lines)


# ---------------------------------------------------------------
# TASK REGISTRY
# ---------------------------------------------------------------
_TASKS: List[Dict[str, Any]] = []


def daily_task(name: str, category: str = 'general',
               critical: bool = False, order: int = 500):
    def decorator(fn: Callable):
        _TASKS.append({
            'name': name, 'category': category,
            'critical': critical, 'order': order, 'fn': fn,
        })
        _TASKS.sort(key=lambda t: (t['order'], t['name']))
        return fn
    return decorator


class TaskContext:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.now = somali_now()
        self.metrics: Dict[str, Dict[str, Any]] = {}
        self.warnings: List[str] = []
        self.action_items: List[Dict[str, str]] = []
        self.tables: Dict[str, Dict[str, Any]] = {}
        self.kv_sections: Dict[str, List[Tuple[str, Any]]] = {}
        self.log = log

    def set_metric(self, key: str, value: Any, category: str = 'general',
                   unit: Optional[str] = None, text: Optional[str] = None) -> None:
        self.metrics[key] = {
            'value': value, 'text': text,
            'category': category, 'unit': unit,
        }

    def add_table(self, key: str, title: str, headers: List[str],
                  rows: List[Tuple[Any, ...]],
                  order: int = 500, category: str = 'general',
                  note: Optional[str] = None) -> None:
        """Store a table for the markdown report."""
        self.tables[key] = {
            'title': title, 'headers': headers, 'rows': rows,
            'order': order, 'category': category, 'note': note,
        }

    def add_kv_section(self, key: str, title: str,
                       pairs: List[Tuple[str, Any]],
                       order: int = 500, category: str = 'general') -> None:
        """Store a key→value section for the markdown report."""
        self.kv_sections[key] = {
            'title': title, 'pairs': pairs,
            'order': order, 'category': category,
        }

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def action(self, msg: str, link: Optional[str] = None) -> None:
        self.action_items.append({'msg': msg, 'link': link})

    def mv(self, key: str, default: Any = 0) -> Any:
        return self.metrics.get(key, {}).get('value', default)


# ---------------------------------------------------------------
# SCHEMA
# ---------------------------------------------------------------
def ensure_tables() -> None:
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
        CREATE INDEX IF NOT EXISTS idx_dtr_run_date ON daily_task_runs(run_date DESC);
        CREATE INDEX IF NOT EXISTS idx_dtr_task ON daily_task_runs(task_name, run_date DESC);
        CREATE INDEX IF NOT EXISTS idx_dtr_status ON daily_task_runs(status);

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
        CREATE INDEX IF NOT EXISTS idx_pm_date ON platform_metrics(metric_date DESC);
        CREATE INDEX IF NOT EXISTS idx_pm_key ON platform_metrics(metric_key, metric_date DESC);
        CREATE INDEX IF NOT EXISTS idx_pm_category ON platform_metrics(category, metric_date DESC);
    """)
    conn.commit()


def record_task_run(run_date, task_name, category, status, items=0,
                    error=None, started=None, ended=None, duration_ms=0):
    try:
        execute_with_retry("""
            INSERT INTO daily_task_runs
                (run_date, task_name, category, status, items_affected,
                 error_message, started_at, ended_at, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (run_date, task_name, category, status, items,
              (error[:1000] if error else None),
              started, ended, duration_ms), commit=True)
    except Exception as e:
        log.error(f"Failed to record task run: {e}")


def persist_metrics(run_date, metrics, dry_run=False):
    if dry_run or not metrics:
        return 0
    conn = get_db()
    count = 0
    for key, payload in metrics.items():
        try:
            conn.execute("""
                INSERT INTO platform_metrics
                    (metric_date, metric_key, value, value_text, category, unit)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(metric_date, metric_key) DO UPDATE SET
                    value = excluded.value,
                    value_text = excluded.value_text,
                    category = excluded.category,
                    unit = excluded.unit
            """, (run_date, key, payload.get('value'), payload.get('text'),
                  payload.get('category', 'general'), payload.get('unit')))
            count += 1
        except Exception as e:
            log.warning(f"Metric persist failed for {key}: {e}")
    try:
        conn.commit()
    except Exception:
        pass
    return count


# ---------------------------------------------------------------
# LOCK
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
def run_one_task(task, ctx, run_date):
    name = task['name']
    cat = task['category']
    ctx.log = logging.getLogger(f'task.{name}')
    started_iso = somali_format(somali_now(), 'iso')
    t0 = time.time()
    result = {'name': name, 'category': cat, 'status': 'success',
              'items': 0, 'error': None, 'duration_ms': 0}
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
    record_task_run(run_date, name, cat, result['status'], result['items'],
                    result['error'], started_iso, ended_iso, result['duration_ms'])
    log.info(f"[{result['status']:>7}] {name:<38} "
             f"items={result['items']:<5} {result['duration_ms']}ms"
             + (f"  ERR={result['error']}" if result['error'] else ''))
    return result


def select_tasks(args):
    tasks = list(_TASKS)
    if args.task:
        tasks = [t for t in tasks if t['name'] == args.task]
        if not tasks:
            print(f"No task found: {args.task}"); sys.exit(2)
    if args.category:
        tasks = [t for t in tasks if t['category'] == args.category]
        if not tasks:
            print(f"No tasks in category: {args.category}"); sys.exit(2)
    return tasks


def print_task_list():
    print(f"{'ORDER':<6} {'CATEGORY':<12} {'CRIT':<5} {'NAME':<44}")
    print('-' * 80)
    for t in _TASKS:
        print(f"{t['order']:<6} {t['category']:<12} "
              f"{'YES' if t['critical'] else 'no':<5} {t['name']:<44}")


def _scalar(sql, params=()):
    try:
        c = execute_with_retry(sql, params)
        r = c.fetchone()
        return int(list(r)[0] or 0) if r else 0
    except Exception as e:
        log.warning(f"scalar failed: {e} | {sql[:60]}")
        return 0


def _rows(sql, params=()):
    try:
        c = execute_with_retry(sql, params)
        return [dict(r) for r in c.fetchall()]
    except Exception as e:
        log.warning(f"rows failed: {e} | {sql[:60]}")
        return []


# ===============================================================
# HEALTH TASKS
# ===============================================================

@daily_task('health.database', category='health', critical=True, order=100)
def task_health_database(ctx):
    from database import get_database_health
    health = get_database_health()
    ok = (health.get('exists') and health.get('openable')
          and health.get('integrity') and health.get('wal_enabled'))
    ctx.set_metric('health.db_ok', 1 if ok else 0, 'health', 'bool')
    ctx._health_db = {'ok': ok, 'text': 'OK · WAL active' if ok else 'FAILED'}
    if not ok:
        ctx.warn('Database health check failed')
    return {'items': 0}


@daily_task('health.disk', category='health', critical=True, order=110)
def task_health_disk(ctx):
    try:
        stat = os.statvfs(Config.BACKUP_DIR)
        free_bytes = stat.f_bavail * stat.f_frsize
        total_bytes = stat.f_blocks * stat.f_frsize
        free_mb = free_bytes / (1024 * 1024)
        used_pct = 100.0 * (1 - free_bytes / total_bytes) if total_bytes else 0
    except Exception as e:
        ctx._health_disk = {'ok': False, 'text': f'error: {e}'}
        ctx.warn(f'Disk check failed: {e}')
        return {'items': 0}
    ok = free_mb > 200
    ctx.set_metric('health.disk_free_mb', round(free_mb, 1), 'health', 'MB')
    ctx.set_metric('health.disk_used_pct', round(used_pct, 1), 'health', '%')
    ctx._health_disk = {'ok': ok, 'text': f'{used_pct:.0f}% used · {free_mb:.1f} MB free'}
    if not ok:
        ctx.warn(f'Low disk space: {free_mb:.0f} MB free')
    return {'items': 0}


@daily_task('health.backup', category='health', critical=False, order=120)
def task_health_backup(ctx):
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
    ok = valid > 0 and bool(summary.get('last_good'))
    ctx.set_metric('health.backups_valid', valid, 'health', 'count')
    ctx.set_metric('health.backup_ok', 1 if ok else 0, 'health', 'bool')
    ctx._health_backup = {'ok': ok, 'text': f'{valid} valid · last {last_good}'}
    if not ok:
        ctx.warn('No valid backups found')
    return {'items': 0}


@daily_task('health.db_table_sizes', category='health', order=130)
def task_db_table_sizes(ctx):
    tables = ['students', 'questions', 'quiz_attempts', 'pdfs',
              'groups', 'live_quizzes', 'live_quiz_participants',
              'notifications', 'history_entries', 'question_interactions',
              'error_logs', 'activity_logs', 'daily_task_runs', 'platform_metrics']
    rows = []
    total_rows = 0
    for t in tables:
        n = _scalar(f"SELECT COUNT(*) FROM {t}")
        total_rows += n
        rows.append((t, f'{n:,}'))
    ctx.add_table(
        'health.table_sizes',
        'Database Table Sizes',
        ['Table', 'Rows'],
        rows,
        order=130, category='health',
        note=f'Total across all tables: {total_rows:,} rows'
    )
    ctx.set_metric('health.db_total_rows', total_rows, 'health', 'rows')
    return {'items': len(rows)}


# ===============================================================
# CLEANUP TASKS
# ===============================================================

@daily_task('cleanup.expired_tiers', category='cleanup', critical=True, order=200)
def task_cleanup_expired_tiers(ctx):
    now_iso = somali_format(somali_now(), 'iso')
    rows_in = _rows("""
        SELECT id, first_name, last_name, public_id, tier, tier_expires_at
        FROM students
        WHERE tier IN ('premium', 'pro') AND tier_expires_at IS NOT NULL
          AND tier_expires_at < ?
        LIMIT 100
    """, (now_iso,))
    count = 0
    downgraded_list = []
    for r in rows_in:
        uid = r['id']
        if ctx.dry_run:
            count += 1
            downgraded_list.append(r)
            continue
        try:
            execute_with_retry(
                "UPDATE students SET tier = 'free', tier_updated_at = ? WHERE id = ?",
                (now_iso, uid), commit=True)
            try:
                execute_with_retry("""
                    INSERT INTO notifications
                        (user_id, type, title, body, link, icon, is_read, created_at)
                    VALUES (?, 'tier_expired', ?, ?, ?, '⏰', 0, datetime('now','localtime'))
                """, (uid, 'Your premium tier has expired',
                      f'Your {r["tier"].upper()} access ended today. Renew to keep using premium features.',
                      '/settings#tier'), commit=True)
            except Exception:
                pass
            downgraded_list.append(r)
            count += 1
        except Exception as e:
            log.warning(f"Failed to expire tier for user {uid}: {e}")
    ctx.set_metric('cleanup.tiers_downgraded', count, 'cleanup', 'count')
    if downgraded_list:
        ctx.add_table(
            'cleanup.downgraded_users',
            'Users Downgraded Today',
            ['Name', 'Public ID', 'Previous Tier', 'Expired'],
            [(f"{r['first_name']} {r['last_name'] or ''}".strip(),
              r['public_id'] or '----', r['tier'].upper(),
              (r['tier_expires_at'] or '')[:10]) for r in downgraded_list[:50]],
            order=201, category='cleanup',
            note=f'Total: {len(downgraded_list)} (showing first 50)'
        )
    if count > 0:
        ctx.action(f'{count} user(s) downgraded to Free — consider re-engagement',
                   f'{BASE_URL}/admin/users' if BASE_URL else None)
    return {'items': count}


@daily_task('cleanup.history', category='cleanup', order=210)
def task_cleanup_history(ctx):
    if ctx.dry_run:
        return {'items': 0, 'status': 'skipped'}
    try:
        from services.tier_service import get_history_retention_days, get_history_max_entries
    except Exception as e:
        ctx.warn(f'tier_service unavailable: {e}')
        return {'items': 0, 'status': 'skipped'}
    users = _rows("SELECT id FROM students")
    total_del = 0
    per_user = []
    for u in users:
        uid = u['id']
        deleted = 0
        retention = get_history_retention_days(uid)
        max_entries = get_history_max_entries(uid)
        if retention and retention > 0:
            cutoff = (somali_now() - timedelta(days=retention)).isoformat()
            try:
                c = execute_with_retry(
                    "DELETE FROM history_entries WHERE user_id = ? AND created_at < ?",
                    (uid, cutoff), commit=True)
                deleted += c.rowcount or 0
            except Exception:
                pass
        if max_entries and max_entries > 0:
            try:
                c = execute_with_retry(
                    "SELECT id FROM history_entries WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (uid, max_entries))
                keep = [r['id'] for r in c.fetchall()]
                if keep:
                    ph = ','.join('?' * len(keep))
                    c2 = execute_with_retry(
                        f"DELETE FROM history_entries WHERE user_id = ? AND id NOT IN ({ph})",
                        [uid] + keep, commit=True)
                    deleted += c2.rowcount or 0
                else:
                    c2 = execute_with_retry(
                        "DELETE FROM history_entries WHERE user_id = ?",
                        (uid,), commit=True)
                    deleted += c2.rowcount or 0
            except Exception:
                pass
        if deleted:
            per_user.append((uid, deleted))
        total_del += deleted
    ctx.set_metric('cleanup.history_trimmed', total_del, 'cleanup', 'count')
    if per_user:
        top = sorted(per_user, key=lambda x: x[1], reverse=True)[:20]
        top_rows = []
        for uid, n in top:
            s = _rows("SELECT first_name, last_name, public_id FROM students WHERE id = ?", (uid,))
            if s:
                top_rows.append((f"{s[0]['first_name']} {s[0]['last_name'] or ''}".strip(),
                                 s[0]['public_id'] or '----', n))
        if top_rows:
            ctx.add_table(
                'cleanup.history_top',
                'Top 20 Users — Most History Trimmed',
                ['Name', 'Public ID', 'Deleted'],
                top_rows, order=211, category='cleanup')
    return {'items': total_del}


@daily_task('cleanup.notifications', category='cleanup', order=220)
def task_cleanup_notifications(ctx):
    if ctx.dry_run:
        return {'items': 0, 'status': 'skipped'}
    n = 0
    try:
        c = execute_with_retry(
            "DELETE FROM notifications WHERE is_read = 1 "
            "AND created_at < datetime('now', ? || ' days')",
            (f'-{RETENTION_NOTIFICATIONS_DAYS}',), commit=True)
        n = c.rowcount or 0
    except Exception as e:
        ctx.warn(f'Notification cleanup failed: {e}')
    ctx.set_metric('cleanup.notifications_purged', n, 'cleanup', 'count')
    return {'items': n}


@daily_task('cleanup.errors', category='cleanup', order=230)
def task_cleanup_errors(ctx):
    if ctx.dry_run:
        return {'items': 0, 'status': 'skipped'}
    n = 0
    try:
        c = execute_with_retry(
            "DELETE FROM error_logs WHERE resolved = 1 "
            "AND timestamp < datetime('now', ? || ' days')",
            (f'-{RETENTION_ERRORS_DAYS}',), commit=True)
        n = c.rowcount or 0
    except Exception as e:
        ctx.warn(f'Error cleanup failed: {e}')
    ctx.set_metric('cleanup.errors_purged', n, 'cleanup', 'count')
    return {'items': n}


@daily_task('cleanup.activity_logs', category='cleanup', order=240)
def task_cleanup_activity_logs(ctx):
    if ctx.dry_run:
        return {'items': 0, 'status': 'skipped'}
    n = 0
    try:
        c = execute_with_retry(
            "DELETE FROM activity_logs WHERE created_at < datetime('now', ? || ' days')",
            (f'-{RETENTION_ACTIVITY_DAYS}',), commit=True)
        n = c.rowcount or 0
    except Exception as e:
        ctx.warn(f'Activity cleanup failed: {e}')
    ctx.set_metric('cleanup.activity_purged', n, 'cleanup', 'count')
    return {'items': n}


@daily_task('cleanup.live_quizzes', category='live_quiz', order=400)
def task_cleanup_live_quizzes(ctx):
    if ctx.dry_run:
        return {'items': 0, 'status': 'skipped'}
    cutoff_waiting = somali_format(somali_now() - timedelta(hours=48), 'iso')
    cutoff_scheduled = somali_format(somali_now() - timedelta(days=RETENTION_LIVE_QUIZ_DAYS), 'iso')
    total = 0
    for qid_row in _rows("SELECT id FROM live_quizzes WHERE status = 'waiting' AND created_at < ?",
                         (cutoff_waiting,)):
        qid = qid_row['id']
        try:
            execute_with_retry("DELETE FROM live_quiz_participants WHERE quiz_id = ?", (qid,), commit=True)
            execute_with_retry("DELETE FROM live_quizzes WHERE id = ?", (qid,), commit=True)
            total += 1
        except Exception:
            pass
    for qid_row in _rows("SELECT id FROM live_quizzes WHERE status = 'scheduled' "
                         "AND scheduled_start IS NOT NULL AND scheduled_start < ?",
                         (cutoff_scheduled,)):
        qid = qid_row['id']
        try:
            execute_with_retry("DELETE FROM live_quiz_participants WHERE quiz_id = ?", (qid,), commit=True)
            execute_with_retry("DELETE FROM live_quizzes WHERE id = ?", (qid,), commit=True)
            total += 1
        except Exception:
            pass
    ctx.set_metric('cleanup.live_quizzes_purged', total, 'cleanup', 'count')
    return {'items': total}


@daily_task('focus.validate_pdf_links', category='focus', order=300)
def task_focus_validate_pdf_links(ctx):
    codes = [r['pdf_code'] for r in _rows("""
        SELECT DISTINCT pdf_code FROM questions
        WHERE pdf_code IS NOT NULL AND pdf_code != '' AND status = 'active'
    """)]
    if not codes:
        ctx.set_metric('focus.broken_links', 0, 'focus', 'count')
        return {'items': 0}
    ph = ','.join('?' * len(codes))
    main_codes = {r['code'] for r in _rows(f"SELECT code FROM pdfs WHERE code IN ({ph})", codes)}
    missing = [c for c in codes if c not in main_codes]
    bot_codes = set()
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
        sample_rows = []
        for code in broken[:20]:
            q = _rows("SELECT question_text, subject_code FROM questions "
                      "WHERE pdf_code = ? LIMIT 1", (code,))
            sample_rows.append((code,
                                q[0]['question_text'][:50] + '…' if q else '—',
                                q[0]['subject_code'] if q else '—'))
        ctx.add_table(
            'focus.broken_links_list',
            'Questions Pointing to Missing PDFs',
            ['PDF Code', 'Sample Question', 'Subject'],
            sample_rows, order=301, category='focus',
            note=f'Total broken: {len(broken)} (showing first 20)')
        ctx.warn(f'{len(broken)} question(s) point to missing PDFs')
        ctx.action(f'{len(broken)} questions have broken PDF source',
                   f'{BASE_URL}/admin/questions?pdf=missing' if BASE_URL else None)
    return {'items': len(broken)}


# ===============================================================
# ANALYTICS — HEADLINE COUNTS
# ===============================================================

@daily_task('analytics.core_counts', category='analytics', order=500)
def task_core_counts(ctx):
    ctx.set_metric('users.total', _scalar("SELECT COUNT(*) FROM students"), 'users', 'count')
    ctx.set_metric('users.new_today',
                   _scalar("SELECT COUNT(*) FROM students WHERE created_at >= datetime('now', '-1 day')"),
                   'users', 'count')
    ctx.set_metric('users.new_7d',
                   _scalar("SELECT COUNT(*) FROM students WHERE created_at >= datetime('now', '-7 days')"),
                   'users', 'count')
    ctx.set_metric('users.new_30d',
                   _scalar("SELECT COUNT(*) FROM students WHERE created_at >= datetime('now', '-30 days')"),
                   'users', 'count')
    ctx.set_metric('users.active_24h',
                   _scalar("SELECT COUNT(DISTINCT student_id) FROM quiz_attempts "
                           "WHERE completed_at >= datetime('now', '-1 day')"), 'users', 'count')
    ctx.set_metric('users.active_7d',
                   _scalar("SELECT COUNT(DISTINCT student_id) FROM quiz_attempts "
                           "WHERE completed_at >= datetime('now', '-7 days')"), 'users', 'count')
    ctx.set_metric('users.active_30d',
                   _scalar("SELECT COUNT(DISTINCT student_id) FROM quiz_attempts "
                           "WHERE completed_at >= datetime('now', '-30 days')"), 'users', 'count')
    ctx.set_metric('users.verified',
                   _scalar("SELECT COUNT(*) FROM students WHERE is_verified = 1"), 'users', 'count')
    ctx.set_metric('users.unverified',
                   _scalar("SELECT COUNT(*) FROM students WHERE is_verified = 0"), 'users', 'count')

    ctx.set_metric('quizzes.today',
                   _scalar("SELECT COUNT(*) FROM quiz_attempts "
                           "WHERE completed_at >= datetime('now', '-1 day')"), 'quizzes', 'count')
    ctx.set_metric('quizzes.week',
                   _scalar("SELECT COUNT(*) FROM quiz_attempts "
                           "WHERE completed_at >= datetime('now', '-7 days')"), 'quizzes', 'count')
    ctx.set_metric('quizzes.month',
                   _scalar("SELECT COUNT(*) FROM quiz_attempts "
                           "WHERE completed_at >= datetime('now', '-30 days')"), 'quizzes', 'count')
    ctx.set_metric('quizzes.all_time',
                   _scalar("SELECT COUNT(*) FROM quiz_attempts"), 'quizzes', 'count')

    try:
        c = execute_with_retry("""
            SELECT AVG(CAST(score AS REAL) / NULLIF(total_questions, 0)) AS avg_pct
            FROM quiz_attempts WHERE completed_at >= datetime('now', '-1 day')
        """)
        row = c.fetchone()
        ctx.set_metric('quizzes.avg_today',
                       round((row['avg_pct'] or 0) * 100, 1) if row else 0, 'quizzes', '%')
    except Exception:
        ctx.set_metric('quizzes.avg_today', 0, 'quizzes', '%')

    ctx.set_metric('content.pdfs', _scalar("SELECT COUNT(*) FROM pdfs"), 'content', 'count')
    ctx.set_metric('content.questions',
                   _scalar("SELECT COUNT(*) FROM questions WHERE status = 'active'"), 'content', 'count')
    ctx.set_metric('content.questions_archived',
                   _scalar("SELECT COUNT(*) FROM questions WHERE status = 'archived'"), 'content', 'count')
    ctx.set_metric('content.questions_linked',
                   _scalar("SELECT COUNT(*) FROM questions WHERE status = 'active' "
                           "AND pdf_code IS NOT NULL AND pdf_code != ''"), 'content', 'count')
    total_q = ctx.mv('content.questions')
    linked = ctx.mv('content.questions_linked')
    ctx.set_metric('content.coverage_pct',
                   round(100.0 * linked / total_q, 1) if total_q else 0, 'content', '%')

    ctx.set_metric('focus.bookmarks',
                   _scalar("SELECT COUNT(DISTINCT question_id) FROM question_interactions "
                           "WHERE interaction_type IN ('save', 'like')"), 'focus', 'count')
    ctx.set_metric('focus.saved',
                   _scalar("SELECT COUNT(DISTINCT question_id) FROM question_interactions "
                           "WHERE interaction_type = 'save'"), 'focus', 'count')
    ctx.set_metric('focus.liked',
                   _scalar("SELECT COUNT(DISTINCT question_id) FROM question_interactions "
                           "WHERE interaction_type = 'like'"), 'focus', 'count')
    ctx.set_metric('focus.bookmarks_today',
                   _scalar("SELECT COUNT(*) FROM question_interactions "
                           "WHERE interaction_type IN ('save', 'like') "
                           "AND created_at >= datetime('now', '-1 day')"), 'focus', 'count')

    ctx.set_metric('revenue.approvals_today',
                   _scalar("SELECT COUNT(*) FROM upgrade_requests "
                           "WHERE status = 'approved' AND approved_at >= datetime('now', '-1 day')"),
                   'revenue', 'count')
    ctx.set_metric('revenue.approvals_month',
                   _scalar("SELECT COUNT(*) FROM upgrade_requests "
                           "WHERE status = 'approved' AND approved_at >= datetime('now', '-30 days')"),
                   'revenue', 'count')
    try:
        c = execute_with_retry("""
            SELECT COALESCE(SUM(final_price_cents), 0) AS cents FROM upgrade_requests
            WHERE status = 'approved' AND approved_at >= datetime('now', '-1 day')
        """)
        r = c.fetchone()
        cents = int(r['cents'] or 0)
    except Exception:
        cents = 0
    ctx.set_metric('revenue.today_cents', cents, 'revenue', 'cents')
    ctx.set_metric('revenue.today_usd', round(cents / 100.0, 2), 'revenue', 'USD')

    try:
        c = execute_with_retry("""
            SELECT COALESCE(SUM(final_price_cents), 0) AS cents FROM upgrade_requests
            WHERE status = 'approved' AND approved_at >= datetime('now', '-30 days')
        """)
        r = c.fetchone()
        mtd_cents = int(r['cents'] or 0)
    except Exception:
        mtd_cents = 0
    ctx.set_metric('revenue.month_usd', round(mtd_cents / 100.0, 2), 'revenue', 'USD')
    ctx.set_metric('revenue.pending',
                   _scalar("SELECT COUNT(*) FROM upgrade_requests WHERE status = 'pending'"),
                   'revenue', 'count')

    ctx.set_metric('health.errors_today',
                   _scalar("SELECT COUNT(*) FROM error_logs "
                           "WHERE timestamp >= datetime('now', '-1 day')"), 'health', 'count')
    ctx.set_metric('health.errors_open',
                   _scalar("SELECT COUNT(*) FROM error_logs "
                           "WHERE resolved = 0 AND dismissed = 0"), 'health', 'count')

    ctx.set_metric('live_quizzes.total',
                   _scalar("SELECT COUNT(*) FROM live_quizzes"), 'live_quizzes', 'count')
    ctx.set_metric('live_quizzes.active',
                   _scalar("SELECT COUNT(*) FROM live_quizzes WHERE status = 'active'"), 'live_quizzes', 'count')
    ctx.set_metric('live_quizzes.scheduled',
                   _scalar("SELECT COUNT(*) FROM live_quizzes WHERE status = 'scheduled'"), 'live_quizzes', 'count')
    ctx.set_metric('live_quizzes.finished_7d',
                   _scalar("SELECT COUNT(*) FROM live_quizzes WHERE status = 'finished' "
                           "AND ended_at >= datetime('now', '-7 days')"), 'live_quizzes', 'count')

    ctx.set_metric('groups.total',
                   _scalar("SELECT COUNT(*) FROM groups WHERE is_active = 1"), 'groups', 'count')
    ctx.set_metric('groups.featured',
                   _scalar("SELECT COUNT(*) FROM groups WHERE is_featured = 1 AND is_active = 1"),
                   'groups', 'count')

    return {'items': 0}


# ===============================================================
# ANALYTICS — RECENT SIGNUPS TABLE
# ===============================================================

@daily_task('analytics.recent_signups', category='analytics', order=510)
def task_recent_signups(ctx):
    rows = _rows("""
        SELECT first_name, middle_name, last_name, public_id,
               location, city, school, grade, tier, is_verified, created_at
        FROM students
        WHERE created_at >= datetime('now', '-7 days')
        ORDER BY created_at DESC LIMIT 50
    """)
    table_rows = []
    for r in rows:
        name = f"{r['first_name']} {r['middle_name'] or ''} {r['last_name'] or ''}".replace('  ', ' ').strip()
        loc = r['location'] or '—'
        city = r['city'] or '—'
        ver = '✅' if r['is_verified'] else '⏳'
        table_rows.append((name, r['public_id'] or '----', f'{loc} / {city}',
                           (r['school'] or '—')[:20], r['grade'] or '—',
                           ver, (r['created_at'] or '')[:16]))
    if table_rows:
        ctx.add_table(
            'users.recent_signups',
            'Recent Signups (Last 7 Days · Latest 50)',
            ['Name', 'Public ID', 'Location / City', 'School', 'Grade', 'Verified', 'Joined'],
            table_rows, order=510, category='users')
    ctx.set_metric('users.new_signups_listed', len(table_rows), 'users', 'count')
    return {'items': len(table_rows)}


# ===============================================================
# ANALYTICS — TIER + LOCATION DISTRIBUTION
# ===============================================================

@daily_task('analytics.tier_distribution', category='analytics', order=520)
def task_tier_distribution(ctx):
    rows = _rows("""
        SELECT tier, COUNT(*) AS n,
               SUM(CASE WHEN created_at >= datetime('now', '-30 days') THEN 1 ELSE 0 END) AS recent
        FROM students GROUP BY tier
    """)
    total = sum(r['n'] for r in rows) or 1
    table = []
    for r in rows:
        pct = round(100.0 * r['n'] / total, 1)
        table.append((r['tier'].upper(), f"{r['n']:,}", f"{pct}%", r['recent']))
    ctx.add_table('users.tier_dist', 'Tier Distribution',
                  ['Tier', 'Users', 'Share', 'New (30d)'], table,
                  order=520, category='users')
    return {'items': len(table)}


@daily_task('analytics.location_distribution', category='analytics', order=521)
def task_location_distribution(ctx):
    rows = _rows("""
        SELECT COALESCE(location, '—') AS loc, COUNT(*) AS n
        FROM students GROUP BY loc ORDER BY n DESC LIMIT 15
    """)
    labels = {'SO': 'Somalia', 'PL': 'Puntland', 'SL': 'Somaliland', '—': 'Not set'}
    table = [(labels.get(r['loc'], r['loc']), f"{r['n']:,}") for r in rows]
    if table:
        ctx.add_table('users.location_dist', 'Users by Location',
                      ['Location', 'Users'], table, order=521, category='users')
    return {'items': len(table)}


# ===============================================================
# ANALYTICS — TOP SUBJECTS + SUBJECT DETAIL
# ===============================================================

@daily_task('analytics.subject_activity', category='analytics', order=530)
def task_subject_activity(ctx):
    rows = _rows("""
        SELECT subject_code, COUNT(*) AS attempts,
               COUNT(DISTINCT student_id) AS students,
               AVG(CAST(score AS REAL) / NULLIF(total_questions, 0) * 100) AS avg_pct
        FROM quiz_attempts
        WHERE completed_at >= datetime('now', '-30 days')
        GROUP BY subject_code ORDER BY attempts DESC LIMIT 20
    """)
    table = []
    for r in rows:
        table.append((r['subject_code'], f"{r['attempts']:,}", r['students'],
                      f"{round(r['avg_pct'] or 0, 1)}%"))
    if table:
        ctx.add_table('quiz.subjects_top', 'Top 20 Subjects (Last 30 Days)',
                      ['Subject', 'Attempts', 'Unique Users', 'Avg Score'],
                      table, order=530, category='quizzes')
    return {'items': len(table)}


# ===============================================================
# ANALYTICS — TOP ACTIVE USERS
# ===============================================================

@daily_task('analytics.top_active_users', category='analytics', order=540)
def task_top_active_users(ctx):
    rows = _rows("""
        SELECT s.first_name, s.last_name, s.public_id, s.total_points,
               COUNT(qa.id) AS attempts,
               AVG(CAST(qa.score AS REAL) / NULLIF(qa.total_questions, 0) * 100) AS avg_pct
        FROM students s
        JOIN quiz_attempts qa ON qa.student_id = s.id
        WHERE qa.completed_at >= datetime('now', '-30 days')
        GROUP BY s.id ORDER BY attempts DESC LIMIT 30
    """)
    table = []
    for i, r in enumerate(rows, 1):
        name = f"{r['first_name']} {r['last_name'] or ''}".strip()
        table.append((f"#{i}", name, r['public_id'] or '----', f"{r['attempts']:,}",
                      f"{round(r['avg_pct'] or 0, 1)}%", f"{r['total_points']:,}"))
    if table:
        ctx.add_table('users.top_active', 'Top 30 Most Active Users (Last 30 Days)',
                      ['Rank', 'Name', 'Public ID', 'Attempts', 'Avg Score', 'Points'],
                      table, order=540, category='users')
    return {'items': len(table)}


# ===============================================================
# ANALYTICS — MOST MISSED QUESTIONS
# ===============================================================

@daily_task('analytics.most_missed', category='analytics', order=550)
def task_most_missed(ctx):
    rows = _rows("""
        SELECT q.id, q.question_text, q.subject_code, q.difficulty,
               COUNT(*) AS attempts,
               SUM(CASE WHEN json_extract(value, '$.correct') = 0 THEN 1 ELSE 0 END) AS misses
        FROM quiz_attempts qa, json_each(qa.answers) AS value
        JOIN questions q ON q.id = CAST(json_extract(value, '$.question_id') AS INTEGER)
        WHERE qa.completed_at >= datetime('now', '-30 days')
        GROUP BY q.id
        HAVING misses >= 3
        ORDER BY misses DESC, attempts DESC
        LIMIT 30
    """)
    table = []
    for r in rows:
        miss_rate = round(100.0 * r['misses'] / r['attempts'], 1) if r['attempts'] else 0
        table.append((
            (r['question_text'] or '')[:55] + '…' if len(r['question_text'] or '') > 55 else r['question_text'],
            r['subject_code'], '⭐' * r['difficulty'],
            f"{r['misses']}/{r['attempts']}", f"{miss_rate}%"
        ))
    if table:
        ctx.add_table('quiz.most_missed', 'Top 30 Most-Missed Questions (Last 30 Days)',
                      ['Question', 'Subject', 'Difficulty', 'Misses', 'Rate'],
                      table, order=550, category='quizzes',
                      note='Candidates for review or re-linking to better sources.')
    ctx.set_metric('quiz.most_missed_count', len(table), 'quizzes', 'count')
    if len(table) >= 10:
        ctx.warn(f'{len(table)} questions have high miss rates (>3 in 30d)')
        ctx.action('Review high miss-rate questions',
                   f'{BASE_URL}/admin/questions' if BASE_URL else None)
    return {'items': len(table)}


# ===============================================================
# ANALYTICS — MOST BOOKMARKED QUESTIONS
# ===============================================================

@daily_task('analytics.top_bookmarked', category='analytics', order=560)
def task_top_bookmarked(ctx):
    rows = _rows("""
        SELECT q.id, q.question_text, q.subject_code,
               COUNT(DISTINCT CASE WHEN qi.interaction_type = 'save' THEN qi.user_id END) AS saves,
               COUNT(DISTINCT CASE WHEN qi.interaction_type = 'like' THEN qi.user_id END) AS likes
        FROM question_interactions qi
        JOIN questions q ON q.id = qi.question_id
        WHERE qi.interaction_type IN ('save', 'like')
        GROUP BY q.id
        ORDER BY (saves + likes) DESC, saves DESC
        LIMIT 30
    """)
    table = []
    for r in rows:
        q = (r['question_text'] or '')[:55]
        table.append((q + '…' if len(r['question_text'] or '') > 55 else q,
                      r['subject_code'], r['saves'], r['likes'],
                      r['saves'] + r['likes']))
    if table:
        ctx.add_table('focus.top_bookmarked', 'Top 30 Most-Bookmarked Questions',
                      ['Question', 'Subject', 'Saves', 'Likes', 'Total'],
                      table, order=560, category='focus')
    return {'items': len(table)}


# ===============================================================
# ANALYTICS — LIVE QUIZZES RECENT + TOP HOSTS
# ===============================================================

@daily_task('analytics.live_quiz_recent', category='analytics', order=570)
def task_live_quiz_recent(ctx):
    rows = _rows("""
        SELECT lq.id, lq.title, lq.subject_code, lq.status, lq.created_at,
               lq.question_count,
               s.first_name, s.last_name, s.public_id,
               (SELECT COUNT(*) FROM live_quiz_participants WHERE quiz_id = lq.id) AS participants
        FROM live_quizzes lq
        LEFT JOIN students s ON s.id = lq.creator_id
        ORDER BY lq.created_at DESC LIMIT 30
    """)
    table = []
    for r in rows:
        host = f"{r['first_name'] or '?'} {r['last_name'] or ''}".strip()
        table.append(((r['title'] or 'Untitled')[:40], r['subject_code'],
                      host, r['status'], r['participants'] or 0,
                      (r['created_at'] or '')[:16]))
    if table:
        ctx.add_table('live_quizzes.recent', 'Recent Live Quizzes (Latest 30)',
                      ['Title', 'Subject', 'Host', 'Status', 'Participants', 'Created'],
                      table, order=570, category='live_quizzes')

    hosts = _rows("""
        SELECT s.first_name, s.last_name, s.public_id,
               COUNT(lq.id) AS hosted,
               (SELECT COUNT(*) FROM live_quiz_participants lqp
                WHERE lqp.quiz_id IN (SELECT id FROM live_quizzes WHERE creator_id = s.id)) AS total_participants
        FROM students s JOIN live_quizzes lq ON lq.creator_id = s.id
        WHERE lq.created_at >= datetime('now', '-30 days')
        GROUP BY s.id ORDER BY hosted DESC LIMIT 15
    """)
    if hosts:
        ctx.add_table('live_quizzes.top_hosts', 'Top 15 Live Quiz Hosts (Last 30 Days)',
                      ['Host', 'Public ID', 'Quizzes Hosted', 'Total Participants'],
                      [(f"{r['first_name']} {r['last_name'] or ''}".strip(),
                        r['public_id'] or '----', r['hosted'], r['total_participants'])
                       for r in hosts],
                      order=571, category='live_quizzes')
    return {'items': len(table)}


# ===============================================================
# ANALYTICS — TOP PDFs
# ===============================================================

@daily_task('analytics.top_pdfs', category='analytics', order=580)
def task_top_pdfs(ctx):
    rows = _rows("""
        SELECT code, title, subject, curriculum, class,
               view_count, is_premium, uploaded_at
        FROM pdfs ORDER BY view_count DESC LIMIT 30
    """)
    table = []
    for i, r in enumerate(rows, 1):
        table.append((f"#{i}", r['code'], (r['title'] or '')[:40],
                      r['subject'] or '—', r['curriculum'] or '—',
                      '💎' if r['is_premium'] else '—',
                      r['view_count'] or 0))
    if table:
        ctx.add_table('content.top_pdfs', 'Top 30 Most Viewed PDFs',
                      ['Rank', 'Code', 'Title', 'Subject', 'Curriculum', 'Premium', 'Views'],
                      table, order=580, category='content')

    # PDFs with zero views (underused)
    zero = _rows("""
        SELECT code, title, subject FROM pdfs
        WHERE (view_count IS NULL OR view_count = 0)
        ORDER BY uploaded_at DESC LIMIT 20
    """)
    if zero:
        ctx.add_table('content.zero_view_pdfs', 'PDFs With Zero Views (Latest 20)',
                      ['Code', 'Title', 'Subject'],
                      [(r['code'], (r['title'] or '')[:50], r['subject'] or '—') for r in zero],
                      order=581, category='content',
                      note='Consider promoting these via Focus or announcements.')
    return {'items': len(table)}


# ===============================================================
# ANALYTICS — RECENT UPGRADES
# ===============================================================

@daily_task('analytics.recent_upgrades', category='analytics', order=590)
def task_recent_upgrades(ctx):
    rows = _rows("""
        SELECT ur.request_id, ur.requested_tier, ur.duration, ur.final_price_cents,
               ur.status, ur.approved_at, ur.created_at,
               s.first_name, s.last_name, s.public_id
        FROM upgrade_requests ur
        JOIN students s ON s.id = ur.user_id
        WHERE ur.status = 'approved' AND ur.approved_at >= datetime('now', '-30 days')
        ORDER BY ur.approved_at DESC LIMIT 30
    """)
    table = []
    for r in rows:
        table.append((r['request_id'], f"{r['first_name']} {r['last_name'] or ''}".strip(),
                      r['public_id'] or '----', r['requested_tier'].upper(),
                      r['duration'], f"${(r['final_price_cents'] or 0) / 100:.2f}",
                      (r['approved_at'] or '')[:16]))
    if table:
        ctx.add_table('revenue.recent_upgrades', 'Recent Approved Upgrades (Last 30 Days)',
                      ['Request ID', 'User', 'Public ID', 'Tier', 'Duration', 'Amount', 'Approved'],
                      table, order=590, category='revenue')

    # Top discount codes
    codes = _rows("""
        SELECT code, discount_type, discount_value, applies_to,
               used_count, max_uses, expires_at, is_active
        FROM discount_codes ORDER BY used_count DESC LIMIT 15
    """)
    if codes:
        code_rows = []
        for r in codes:
            val = f"{r['discount_value']}%" if r['discount_type'] == 'percentage' \
                  else f"${(r['discount_value'] or 0) / 100:.2f}"
            limit = r['max_uses'] or '∞'
            code_rows.append((r['code'], val, r['applies_to'],
                              f"{r['used_count'] or 0} / {limit}",
                              '✅' if r['is_active'] else '❌'))
        ctx.add_table('revenue.top_codes', 'Top 15 Discount Codes',
                      ['Code', 'Value', 'Applies To', 'Used', 'Active'],
                      code_rows, order=591, category='revenue')
    return {'items': len(table)}


# ===============================================================
# ANALYTICS — ENGAGEMENT / RETENTION
# ===============================================================

@daily_task('analytics.engagement', category='analytics', order=600)
def task_engagement(ctx):
    total = ctx.mv('users.total') or 1
    dau = ctx.mv('users.active_24h')
    wau = ctx.mv('users.active_7d')
    mau = ctx.mv('users.active_30d')

    ctx.add_kv_section(
        'analytics.engagement',
        'Engagement Ratios',
        [
            ('DAU / MAU (stickiness)', f'{round(100.0 * dau / (mau or 1), 1)}%'),
            ('WAU / MAU', f'{round(100.0 * wau / (mau or 1), 1)}%'),
            ('DAU as % of total users', f'{round(100.0 * dau / total, 1)}%'),
            ('WAU as % of total users', f'{round(100.0 * wau / total, 1)}%'),
            ('MAU as % of total users', f'{round(100.0 * mau / total, 1)}%'),
        ],
        order=600, category='analytics')

    ctx.set_metric('analytics.dau', dau, 'analytics', 'count')
    ctx.set_metric('analytics.wau', wau, 'analytics', 'count')
    ctx.set_metric('analytics.mau', mau, 'analytics', 'count')
    ctx.set_metric('analytics.stickiness_pct',
                   round(100.0 * dau / (mau or 1), 1), 'analytics', '%')

    # Hourly activity (last 7 days)
    hourly = _rows("""
        SELECT CAST(strftime('%H', completed_at) AS INTEGER) AS hour,
               COUNT(*) AS n
        FROM quiz_attempts
        WHERE completed_at >= datetime('now', '-7 days')
        GROUP BY hour ORDER BY hour
    """)
    if hourly:
        max_n = max(r['n'] for r in hourly) or 1
        rows = []
        for r in hourly:
            bar = '█' * int(round(20 * r['n'] / max_n))
            rows.append((f"{r['hour']:02d}:00", r['n'], bar))
        ctx.add_table('analytics.hourly_activity', 'Hourly Activity (Last 7 Days)',
                      ['Hour', 'Attempts', 'Distribution'], rows,
                      order=601, category='analytics')
    return {'items': 0}


# ===============================================================
# ANALYTICS — GROWTH TRENDS
# ===============================================================

@daily_task('analytics.growth', category='analytics', order=610)
def task_growth(ctx):
    def period_count(table, column, days):
        return _scalar(f"SELECT COUNT(*) FROM {table} "
                       f"WHERE {column} >= datetime('now', '-{days} days')")

    growth_rows = []
    for label, table, col in [
        ('Signups', 'students', 'created_at'),
        ('Quiz Attempts', 'quiz_attempts', 'completed_at'),
        ('Live Quizzes', 'live_quizzes', 'created_at'),
        ('PDFs Published', 'pdfs', 'uploaded_at'),
    ]:
        last_7 = period_count(table, col, 7)
        prev_7 = _scalar(f"SELECT COUNT(*) FROM {table} "
                         f"WHERE {col} >= datetime('now', '-14 days') "
                         f"AND {col} < datetime('now', '-7 days')")
        delta = last_7 - prev_7
        pct = round(100.0 * delta / prev_7, 1) if prev_7 else ('—' if last_7 == 0 else '+∞')
        arrow = '📈' if delta > 0 else '📉' if delta < 0 else '➡️'
        growth_rows.append((label, last_7, prev_7, f'{delta:+d}', f'{pct}{"%" if pct != "—" else ""}', arrow))

    ctx.add_table('analytics.growth', 'Week-over-Week Growth',
                  ['Metric', 'This Week', 'Last Week', 'Delta', 'Change', 'Trend'],
                  growth_rows, order=610, category='analytics')
    return {'items': len(growth_rows)}


# ===============================================================
# ANALYTICS — ERRORS BREAKDOWN
# ===============================================================

@daily_task('analytics.errors_breakdown', category='analytics', order=620)
def task_errors_breakdown(ctx):
    rows = _rows("""
        SELECT error_type, severity, COUNT(*) AS n,
               MAX(timestamp) AS last_seen
        FROM error_logs
        WHERE timestamp >= datetime('now', '-7 days')
        GROUP BY error_type, severity ORDER BY n DESC LIMIT 25
    """)
    table = []
    for r in rows:
        table.append(((r['error_type'] or 'Unknown')[:45], r['severity'],
                      f"{r['n']:,}", (r['last_seen'] or '')[:16]))
    if table:
        ctx.add_table('health.errors_breakdown', 'Top Error Types (Last 7 Days)',
                      ['Type', 'Severity', 'Count', 'Last Seen'],
                      table, order=620, category='health')

    # Recent unhandled errors
    recent = _rows("""
        SELECT request_id, error_type, error_message, url, timestamp
        FROM error_logs
        WHERE resolved = 0 AND dismissed = 0
        ORDER BY timestamp DESC LIMIT 20
    """)
    if recent:
        r_rows = []
        for r in recent:
            r_rows.append((r['request_id'][:8], (r['error_type'] or '')[:30],
                           (r['error_message'] or '')[:50] + '…',
                           (r['timestamp'] or '')[:16]))
        ctx.add_table('health.recent_errors', 'Recent Unresolved Errors (Latest 20)',
                      ['Req ID', 'Type', 'Message', 'Time'],
                      r_rows, order=621, category='health')
    return {'items': len(table)}


# ===============================================================
# ANALYTICS — GROUPS + ACHIEVEMENTS + NOTIFICATIONS
# ===============================================================

@daily_task('analytics.groups', category='analytics', order=630)
def task_analytics_groups(ctx):
    rows = _rows("""
        SELECT name, platform, category, click_count, is_featured, is_active
        FROM groups ORDER BY click_count DESC LIMIT 20
    """)
    table = []
    for i, r in enumerate(rows, 1):
        table.append((f"#{i}", (r['name'] or '')[:35], r['platform'],
                      r['category'] or '—', r['click_count'] or 0,
                      '⭐' if r['is_featured'] else '—',
                      '✅' if r['is_active'] else '❌'))
    if table:
        ctx.add_table('groups.top_clicked', 'Top 20 Most-Clicked Groups',
                      ['Rank', 'Name', 'Platform', 'Category', 'Clicks', 'Featured', 'Active'],
                      table, order=630, category='groups')
    return {'items': len(table)}


@daily_task('analytics.achievements', category='analytics', order=640)
def task_analytics_achievements(ctx):
    recent = _rows("""
        SELECT ua.unlocked_at, a.name, a.icon,
               s.first_name, s.last_name, s.public_id
        FROM user_achievements ua
        JOIN achievements a ON a.id = ua.achievement_id
        JOIN students s ON s.id = ua.user_id
        WHERE ua.unlocked_at >= datetime('now', '-7 days')
        ORDER BY ua.unlocked_at DESC LIMIT 30
    """)
    table = []
    for r in recent:
        table.append((r['icon'] or '🏆', r['name'],
                      f"{r['first_name']} {r['last_name'] or ''}".strip(),
                      r['public_id'] or '----', (r['unlocked_at'] or '')[:16]))
    if table:
        ctx.add_table('achievements.recent', 'Achievements Unlocked (Last 7 Days · Latest 30)',
                      ['', 'Achievement', 'User', 'Public ID', 'When'],
                      table, order=640, category='achievements')

    counts = _rows("""
        SELECT a.name, a.icon, COUNT(*) AS n FROM user_achievements ua
        JOIN achievements a ON a.id = ua.achievement_id
        GROUP BY a.id ORDER BY n DESC LIMIT 15
    """)
    if counts:
        ctx.add_table('achievements.popular', 'Top 15 Most-Unlocked Achievements',
                      ['', 'Achievement', 'Unlocks'],
                      [(r['icon'] or '🏆', r['name'], r['n']) for r in counts],
                      order=641, category='achievements')
    return {'items': len(table)}


@daily_task('analytics.notifications', category='analytics', order=650)
def task_analytics_notifications(ctx):
    total = _scalar("SELECT COUNT(*) FROM notifications")
    read = _scalar("SELECT COUNT(*) FROM notifications WHERE is_read = 1")
    unread = total - read
    read_pct = round(100.0 * read / total, 1) if total else 0
    ctx.set_metric('notifications.total', total, 'notifications', 'count')
    ctx.set_metric('notifications.read', read, 'notifications', 'count')
    ctx.set_metric('notifications.unread', unread, 'notifications', 'count')
    ctx.set_metric('notifications.read_pct', read_pct, 'notifications', '%')

    by_type = _rows("""
        SELECT type, COUNT(*) AS n,
               SUM(CASE WHEN is_read = 1 THEN 1 ELSE 0 END) AS read_n
        FROM notifications GROUP BY type ORDER BY n DESC LIMIT 20
    """)
    if by_type:
        table = []
        for r in by_type:
            pct = round(100.0 * (r['read_n'] or 0) / r['n'], 1) if r['n'] else 0
            table.append((r['type'], r['n'], r['read_n'] or 0, f'{pct}%'))
        ctx.add_table('notifications.by_type', 'Notifications by Type',
                      ['Type', 'Total', 'Read', 'Read Rate'], table,
                      order=650, category='notifications')
    return {'items': 0}


@daily_task('analytics.bookmark_stats', category='analytics', order=660)
def task_analytics_bookmarks(ctx):
    by_type = _rows("""
        SELECT interaction_type,
               COUNT(*) AS n,
               COUNT(DISTINCT user_id) AS users,
               COUNT(DISTINCT question_id) AS questions
        FROM question_interactions
        WHERE interaction_type IN ('save', 'like')
        GROUP BY interaction_type
    """)
    if by_type:
        rows = [(r['interaction_type'].upper(), r['n'], r['users'], r['questions'])
                for r in by_type]
        ctx.add_table('focus.bookmark_summary', 'Bookmark Summary',
                      ['Type', 'Total', 'Unique Users', 'Unique Questions'],
                      rows, order=660, category='focus')

    top_users = _rows("""
        SELECT s.first_name, s.last_name, s.public_id,
               COUNT(DISTINCT CASE WHEN qi.interaction_type='save' THEN qi.question_id END) AS saves,
               COUNT(DISTINCT CASE WHEN qi.interaction_type='like' THEN qi.question_id END) AS likes
        FROM question_interactions qi
        JOIN students s ON s.id = qi.user_id
        WHERE qi.interaction_type IN ('save', 'like')
        GROUP BY s.id ORDER BY (saves + likes) DESC LIMIT 20
    """)
    if top_users:
        table = []
        for i, r in enumerate(top_users, 1):
            table.append((f"#{i}", f"{r['first_name']} {r['last_name'] or ''}".strip(),
                          r['public_id'] or '----', r['saves'], r['likes'],
                          r['saves'] + r['likes']))
        ctx.add_table('focus.top_bookmarkers', 'Top 20 Users by Bookmarks',
                      ['Rank', 'Name', 'Public ID', 'Saves', 'Likes', 'Total'],
                      table, order=661, category='focus')
    return {'items': 0}


# ===============================================================
# DB SNAPSHOT
# ===============================================================

def create_db_snapshot() -> Tuple[Optional[str], int, Optional[str]]:
    src_path = Config.DATABASE_PATH
    if not os.path.exists(src_path):
        log.error(f"Source DB not found: {src_path}")
        return None, 0, None
    try:
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        stamp = somali_now().strftime('%Y%m%d_%H%M')
        temp_db = os.path.join(SNAPSHOT_DIR, f'nuunplatform_{stamp}.db')
        out_gz = os.path.join(SNAPSHOT_DIR, f'nuunplatform_{stamp}.db.gz')
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


def get_bot_safe():
    try:
        from bot.utils import get_bot
        return get_bot()
    except Exception as e:
        log.error(f"Could not initialize bot: {e}")
        return None


def build_report_message(ctx: TaskContext, run_summary: Dict[str, Any]) -> str:
    m = Msg()
    m.h1('📊  NUUNPLATFORM — DAILY BRIEF')
    m.italic(somali_format(ctx.now))
    m.text(f"Run {run_summary.get('started_at', '')} → {run_summary.get('ended_at', '')}  "
           f"({run_summary.get('duration_seconds', 0):.0f}s)")
    m.blank()
    m.divider()

    m.h2('1️⃣  SYSTEM HEALTH')
    m.blank()
    h_db = getattr(ctx, '_health_db', None)
    h_disk = getattr(ctx, '_health_disk', None)
    h_backup = getattr(ctx, '_health_backup', None)
    if h_db:
        m.raw(f'{"✅" if h_db["ok"] else "❌"}  Database — {mdv2_escape(h_db["text"])}')
    if h_disk:
        m.raw(f'{"✅" if h_disk["ok"] else "⚠️"}  Disk — {mdv2_escape(h_disk["text"])}')
    if h_backup:
        m.raw(f'{"✅" if h_backup["ok"] else "⚠️"}  Backup — {mdv2_escape(h_backup["text"])}')
    m.status_line('⚠️' if ctx.mv("health.errors_today") > 0 else '✅',
                  'Errors new (24h)', ctx.mv("health.errors_today"))
    m.status_line('⚠️' if ctx.mv("health.errors_open") > 5 else '✅',
                  'Errors open', ctx.mv("health.errors_open"))
    m.blank()
    m.divider()

    m.h2('2️⃣  USERS')
    m.blank()
    m.kv('Total registered', ctx.mv('users.total'))
    m.kv('New today', ctx.mv('users.new_today'))
    m.kv('New (7 days)', ctx.mv('users.new_7d'))
    m.kv('New (30 days)', ctx.mv('users.new_30d'))
    m.kv('Active 24h / 7d / 30d',
         f'{ctx.mv("users.active_24h")} / {ctx.mv("users.active_7d")} / {ctx.mv("users.active_30d")}')
    m.kv('Verified / Unverified',
         f'{ctx.mv("users.verified")} / {ctx.mv("users.unverified")}')
    m.blank()
    m.raw('   *Tier distribution*')
    m.chip_line(
        ('Free', ctx.mv('users.tier_free')),
        ('Premium', ctx.mv('users.tier_premium')),
        ('Pro', ctx.mv('users.tier_pro')),
    )
    m.blank()
    m.divider()

    m.h2('3️⃣  QUIZZES')
    m.blank()
    m.kv('Today', ctx.mv('quizzes.today'))
    m.kv('This week', ctx.mv('quizzes.week'))
    m.kv('This month', ctx.mv('quizzes.month'))
    m.kv('All time', ctx.mv('quizzes.all_time'))
    m.kv('Avg score (24h)', f'{ctx.mv("quizzes.avg_today")}%')
    m.blank()
    m.divider()

    m.h2('4️⃣  CONTENT & FOCUS')
    m.blank()
    m.kv('PDFs', ctx.mv('content.pdfs'))
    m.kv('Active questions', ctx.mv('content.questions'))
    m.kv('PDF-linked coverage', f'{ctx.mv("content.coverage_pct")}%')
    broken = ctx.mv('focus.broken_links')
    m.raw(f'{"⚠️" if broken else "✅"}  Broken source links — *{mdv2_escape(broken)}*')
    m.kv('Bookmarks (saved + liked)',
         f'{ctx.mv("focus.bookmarks")} ({ctx.mv("focus.saved")} saved · {ctx.mv("focus.liked")} liked)')
    m.kv('New bookmarks today', ctx.mv('focus.bookmarks_today'))
    m.blank()
    m.divider()

    m.h2('5️⃣  REVENUE')
    m.blank()
    m.kv('Approvals today', ctx.mv('revenue.approvals_today'))
    m.kv('Approvals (30d)', ctx.mv('revenue.approvals_month'))
    m.kv('Revenue today', f'${ctx.mv("revenue.today_usd"):.2f}')
    m.kv('Revenue (30d)', f'${ctx.mv("revenue.month_usd"):.2f}')
    m.kv('Pending requests', ctx.mv('revenue.pending'))
    m.blank()
    m.divider()

    m.h2('6️⃣  MAINTENANCE')
    m.blank()
    m.kv('Tier downgrades', ctx.mv('cleanup.tiers_downgraded'))
    m.kv('History trimmed', ctx.mv('cleanup.history_trimmed'))
    m.kv('Notifications purged', ctx.mv('cleanup.notifications_purged'))
    m.kv('Errors purged', ctx.mv('cleanup.errors_purged'))
    m.kv('Activity purged', ctx.mv('cleanup.activity_purged'))
    m.kv('Orphan live quizzes', ctx.mv('cleanup.live_quizzes_purged'))
    m.blank()
    m.divider()

    m.h2('7️⃣  TASK EXECUTION')
    m.blank()
    m.kv('Tasks executed', run_summary.get('tasks_total', 0))
    m.kv('Succeeded', run_summary.get('tasks_success', 0))
    failed = run_summary.get('tasks_failed', 0)
    if failed:
        m.raw(f'❌  Failed — *{mdv2_escape(failed)}*')
        for f in run_summary.get('failed_names', [])[:8]:
            m.raw(f'   – {mdv2_escape(f)}')
    else:
        m.raw(f'✅  Failed — *0*')

    if ctx.warnings:
        m.blank()
        m.divider()
        m.h2('8️⃣  ANOMALIES')
        m.blank()
        for w in ctx.warnings:
            m.warn(w)

    if ctx.action_items:
        m.blank()
        m.divider()
        m.h2('9️⃣  ACTION ITEMS')
        m.blank()
        for a in ctx.action_items:
            m.action(a.get('msg', ''), a.get('link'))

    m.blank()
    m.divider()
    m.italic('Full detailed report + DB snapshot attached below.')

    return m.to_string()


# ===============================================================
# HUGE MARKDOWN REPORT
# ===============================================================

def build_plain_markdown_report(ctx: TaskContext, run_summary: Dict[str, Any]) -> str:
    """Massive detailed markdown report — the 'huge data' file."""
    L: List[str] = []

    # ──────── HEADER ────────
    L.append('# 📊 NuunPlatform — Daily Brief')
    L.append('')
    L.append(f'**{somali_format(ctx.now)}**')
    L.append('')
    L.append(f'- Run started: `{run_summary.get("started_at", "")}`')
    L.append(f'- Run ended:   `{run_summary.get("ended_at", "")}`')
    L.append(f'- Duration:    **{run_summary.get("duration_seconds", 0):.1f}s**')
    L.append(f'- Tasks:       **{run_summary.get("tasks_total", 0)}** total · '
             f'**{run_summary.get("tasks_success", 0)}** succeeded · '
             f'**{run_summary.get("tasks_failed", 0)}** failed')
    L.append('')
    L.append('---')
    L.append('')

    # ──────── TABLE OF CONTENTS ────────
    L.append('## 📑 Table of Contents')
    L.append('')
    L.append('1. [Executive Summary](#executive-summary)')
    L.append('2. [System Health](#system-health)')
    L.append('3. [Users & Demographics](#users--demographics)')
    L.append('4. [Quiz Analytics](#quiz-analytics)')
    L.append('5. [Live Quiz Analytics](#live-quiz-analytics)')
    L.append('6. [Content & PDFs](#content--pdfs)')
    L.append('7. [Focus & Bookmarks](#focus--bookmarks)')
    L.append('8. [Groups](#groups)')
    L.append('9. [Achievements](#achievements)')
    L.append('10. [Revenue](#revenue)')
    L.append('11. [Engagement & Growth](#engagement--growth)')
    L.append('12. [Notifications](#notifications)')
    L.append('13. [Maintenance Actions](#maintenance-actions)')
    L.append('14. [Task Execution Log](#task-execution-log)')
    L.append('15. [Warnings & Action Items](#warnings--action-items)')
    L.append('')
    L.append('---')
    L.append('')

    # ──────── 1. EXECUTIVE SUMMARY ────────
    L.append('## 1. Executive Summary')
    L.append('')
    L.append('| Metric | Value |')
    L.append('|--------|-------|')
    summary = [
        ('👥 Total users', f'{ctx.mv("users.total"):,}'),
        ('🆕 New today', f'{ctx.mv("users.new_today"):,}'),
        ('🔥 Active (24h)', f'{ctx.mv("users.active_24h"):,}'),
        ('📝 Quizzes today', f'{ctx.mv("quizzes.today"):,}'),
        ('📚 Active questions', f'{ctx.mv("content.questions"):,}'),
        ('📄 PDFs', f'{ctx.mv("content.pdfs"):,}'),
        ('🎯 Bookmarks', f'{ctx.mv("focus.bookmarks"):,}'),
        ('💰 Revenue today', f'${ctx.mv("revenue.today_usd"):.2f}'),
        ('⚠️ Errors open', f'{ctx.mv("health.errors_open"):,}'),
    ]
    for k, v in summary:
        L.append(f'| {k} | **{v}** |')
    L.append('')
    L.append('---')
    L.append('')

    # ──────── 2. SYSTEM HEALTH ────────
    L.append('## 2. System Health')
    L.append('')
    h_db = getattr(ctx, '_health_db', None)
    h_disk = getattr(ctx, '_health_disk', None)
    h_backup = getattr(ctx, '_health_backup', None)
    L.append('| Component | Status | Detail |')
    L.append('|-----------|--------|--------|')
    if h_db:
        L.append(f'| Database | {"✅ OK" if h_db["ok"] else "❌ FAILED"} | {h_db["text"]} |')
    if h_disk:
        L.append(f'| Disk | {"✅ OK" if h_disk["ok"] else "⚠️ WARNING"} | {h_disk["text"]} |')
    if h_backup:
        L.append(f'| Backup | {"✅ OK" if h_backup["ok"] else "⚠️ WARNING"} | {h_backup["text"]} |')
    L.append(f'| Errors (24h) | {"⚠️" if ctx.mv("health.errors_today") > 0 else "✅"} | {ctx.mv("health.errors_today")} new |')
    L.append(f'| Errors (open) | {"⚠️" if ctx.mv("health.errors_open") > 5 else "✅"} | {ctx.mv("health.errors_open")} unresolved |')
    L.append(f'| DB total rows | ℹ️ | {ctx.mv("health.db_total_rows"):,} |')
    L.append('')

    _render_table(ctx, 'health.table_sizes', L)
    _render_table(ctx, 'health.errors_breakdown', L)
    _render_table(ctx, 'health.recent_errors', L)

    L.append('---')
    L.append('')

    # ──────── 3. USERS & DEMOGRAPHICS ────────
    L.append('## 3. Users & Demographics')
    L.append('')
    L.append('### 3.1 Headline Numbers')
    L.append('')
    L.append('| Metric | Value |')
    L.append('|--------|-------|')
    L.append(f'| Total users | **{ctx.mv("users.total"):,}** |')
    L.append(f'| New (24h) | **{ctx.mv("users.new_today"):,}** |')
    L.append(f'| New (7 days) | **{ctx.mv("users.new_7d"):,}** |')
    L.append(f'| New (30 days) | **{ctx.mv("users.new_30d"):,}** |')
    L.append(f'| Active (24h) | **{ctx.mv("users.active_24h"):,}** |')
    L.append(f'| Active (7 days) | **{ctx.mv("users.active_7d"):,}** |')
    L.append(f'| Active (30 days) | **{ctx.mv("users.active_30d"):,}** |')
    L.append(f'| Verified | **{ctx.mv("users.verified"):,}** |')
    L.append(f'| Unverified | **{ctx.mv("users.unverified"):,}** |')
    L.append('')

    _render_table(ctx, 'users.tier_dist', L)
    _render_table(ctx, 'users.location_dist', L)
    _render_table(ctx, 'users.top_active', L)
    _render_table(ctx, 'users.recent_signups', L)

    L.append('---')
    L.append('')

    # ──────── 4. QUIZ ANALYTICS ────────
    L.append('## 4. Quiz Analytics')
    L.append('')
    L.append('### 4.1 Headline Numbers')
    L.append('')
    L.append('| Metric | Value |')
    L.append('|--------|-------|')
    L.append(f'| Quizzes today | **{ctx.mv("quizzes.today"):,}** |')
    L.append(f'| Quizzes (7 days) | **{ctx.mv("quizzes.week"):,}** |')
    L.append(f'| Quizzes (30 days) | **{ctx.mv("quizzes.month"):,}** |')
    L.append(f'| Quizzes (all time) | **{ctx.mv("quizzes.all_time"):,}** |')
    L.append(f'| Avg score (24h) | **{ctx.mv("quizzes.avg_today")}%** |')
    L.append('')

    _render_table(ctx, 'quiz.subjects_top', L)
    _render_table(ctx, 'quiz.most_missed', L)

    L.append('---')
    L.append('')

    # ──────── 5. LIVE QUIZ ANALYTICS ────────
    L.append('## 5. Live Quiz Analytics')
    L.append('')
    L.append('| Metric | Value |')
    L.append('|--------|-------|')
    L.append(f'| Total live quizzes | **{ctx.mv("live_quizzes.total"):,}** |')
    L.append(f'| Currently active | **{ctx.mv("live_quizzes.active"):,}** |')
    L.append(f'| Scheduled | **{ctx.mv("live_quizzes.scheduled"):,}** |')
    L.append(f'| Finished (7 days) | **{ctx.mv("live_quizzes.finished_7d"):,}** |')
    L.append('')
    _render_table(ctx, 'live_quizzes.recent', L)
    _render_table(ctx, 'live_quizzes.top_hosts', L)
    L.append('---')
    L.append('')

    # ──────── 6. CONTENT & PDFS ────────
    L.append('## 6. Content & PDFs')
    L.append('')
    L.append('| Metric | Value |')
    L.append('|--------|-------|')
    L.append(f'| PDFs in library | **{ctx.mv("content.pdfs"):,}** |')
    L.append(f'| Active questions | **{ctx.mv("content.questions"):,}** |')
    L.append(f'| Archived questions | **{ctx.mv("content.questions_archived"):,}** |')
    L.append(f'| Questions linked to PDFs | **{ctx.mv("content.questions_linked"):,}** ({ctx.mv("content.coverage_pct")}%) |')
    L.append(f'| Broken source links | **{ctx.mv("focus.broken_links"):,}** |')
    L.append('')
    _render_table(ctx, 'focus.broken_links_list', L)
    _render_table(ctx, 'content.top_pdfs', L)
    _render_table(ctx, 'content.zero_view_pdfs', L)
    L.append('---')
    L.append('')

    # ──────── 7. FOCUS & BOOKMARKS ────────
    L.append('## 7. Focus & Bookmarks')
    L.append('')
    L.append('| Metric | Value |')
    L.append('|--------|-------|')
    L.append(f'| Total unique bookmarked questions | **{ctx.mv("focus.bookmarks"):,}** |')
    L.append(f'| Saved questions | **{ctx.mv("focus.saved"):,}** |')
    L.append(f'| Liked questions | **{ctx.mv("focus.liked"):,}** |')
    L.append(f'| New bookmarks (24h) | **{ctx.mv("focus.bookmarks_today"):,}** |')
    L.append('')
    _render_table(ctx, 'focus.bookmark_summary', L)
    _render_table(ctx, 'focus.top_bookmarked', L)
    _render_table(ctx, 'focus.top_bookmarkers', L)
    L.append('---')
    L.append('')

    # ──────── 8. GROUPS ────────
    L.append('## 8. Groups')
    L.append('')
    L.append('| Metric | Value |')
    L.append('|--------|-------|')
    L.append(f'| Active groups | **{ctx.mv("groups.total"):,}** |')
    L.append(f'| Featured groups | **{ctx.mv("groups.featured"):,}** |')
    L.append('')
    _render_table(ctx, 'groups.top_clicked', L)
    L.append('---')
    L.append('')

    # ──────── 9. ACHIEVEMENTS ────────
    L.append('## 9. Achievements')
    L.append('')
    _render_table(ctx, 'achievements.recent', L)
    _render_table(ctx, 'achievements.popular', L)
    L.append('---')
    L.append('')

    # ──────── 10. REVENUE ────────
    L.append('## 10. Revenue')
    L.append('')
    L.append('| Metric | Value |')
    L.append('|--------|-------|')
    L.append(f'| Approvals today | **{ctx.mv("revenue.approvals_today"):,}** |')
    L.append(f'| Approvals (30 days) | **{ctx.mv("revenue.approvals_month"):,}** |')
    L.append(f'| Revenue today | **${ctx.mv("revenue.today_usd"):.2f}** |')
    L.append(f'| Revenue (30 days) | **${ctx.mv("revenue.month_usd"):.2f}** |')
    L.append(f'| Pending requests | **{ctx.mv("revenue.pending"):,}** |')
    L.append('')
    _render_table(ctx, 'revenue.recent_upgrades', L)
    _render_table(ctx, 'revenue.top_codes', L)
    L.append('---')
    L.append('')

    # ──────── 11. ENGAGEMENT & GROWTH ────────
    L.append('## 11. Engagement & Growth')
    L.append('')
    _render_kv_section(ctx, 'analytics.engagement', L)
    _render_table(ctx, 'analytics.growth', L)
    _render_table(ctx, 'analytics.hourly_activity', L)
    L.append('---')
    L.append('')

    # ──────── 12. NOTIFICATIONS ────────
    L.append('## 12. Notifications')
    L.append('')
    L.append('| Metric | Value |')
    L.append('|--------|-------|')
    L.append(f'| Total notifications | **{ctx.mv("notifications.total"):,}** |')
    L.append(f'| Read | **{ctx.mv("notifications.read"):,}** |')
    L.append(f'| Unread | **{ctx.mv("notifications.unread"):,}** |')
    L.append(f'| Read rate | **{ctx.mv("notifications.read_pct")}%** |')
    L.append('')
    _render_table(ctx, 'notifications.by_type', L)
    L.append('---')
    L.append('')

    # ──────── 13. MAINTENANCE ACTIONS ────────
    L.append('## 13. Maintenance Actions')
    L.append('')
    L.append('| Action | Items |')
    L.append('|--------|-------|')
    L.append(f'| Tier downgrades | {ctx.mv("cleanup.tiers_downgraded"):,} |')
    L.append(f'| History entries trimmed | {ctx.mv("cleanup.history_trimmed"):,} |')
    L.append(f'| Notifications purged | {ctx.mv("cleanup.notifications_purged"):,} |')
    L.append(f'| Errors purged | {ctx.mv("cleanup.errors_purged"):,} |')
    L.append(f'| Activity logs purged | {ctx.mv("cleanup.activity_purged"):,} |')
    L.append(f'| Orphan live quizzes removed | {ctx.mv("cleanup.live_quizzes_purged"):,} |')
    L.append('')
    _render_table(ctx, 'cleanup.downgraded_users', L)
    _render_table(ctx, 'cleanup.history_top', L)
    L.append('---')
    L.append('')

    # ──────── 14. TASK EXECUTION LOG ────────
    L.append('## 14. Task Execution Log')
    L.append('')
    L.append('| Task | Status | Items | Duration (ms) |')
    L.append('|------|--------|-------|---------------|')
    for t in run_summary.get('results', []):
        icon = '✅' if t['status'] == 'success' else \
               '⏭️' if t['status'] == 'skipped' else '❌'
        L.append(f'| `{t["name"]}` | {icon} {t["status"]} | {t["items"]} | {t["duration_ms"]} |')
    L.append('')

    # ──────── 15. WARNINGS & ACTIONS ────────
    L.append('## 15. Warnings & Action Items')
    L.append('')
    if ctx.warnings:
        L.append('### ⚠️ Warnings')
        L.append('')
        for w in ctx.warnings:
            L.append(f'- {w}')
        L.append('')
    else:
        L.append('### ✅ No warnings')
        L.append('')

    if ctx.action_items:
        L.append('### 🎯 Action Items')
        L.append('')
        for a in ctx.action_items:
            line = f'- {a.get("msg")}'
            if a.get('link'):
                line += f' → [{a["link"]}]({a["link"]})'
            L.append(line)
        L.append('')

    # ──────── METRICS APPENDIX ────────
    L.append('---')
    L.append('')
    L.append('## Appendix — All Metrics')
    L.append('')
    grouped: Dict[str, List[Tuple[str, Any, str]]] = {}
    for k, v in sorted(ctx.metrics.items()):
        cat = v.get('category', 'general')
        grouped.setdefault(cat, []).append((k, v.get('value'), v.get('unit') or ''))
    for cat in sorted(grouped.keys()):
        L.append(f'### `{cat}`')
        L.append('')
        L.append('| Metric Key | Value | Unit |')
        L.append('|------------|-------|------|')
        for k, val, unit in grouped[cat]:
            L.append(f'| `{k}` | {val} | {unit} |')
        L.append('')

    L.append('')
    L.append('---')
    L.append('')
    L.append('*End of report. Generated by `daily_tasks.py`.*')

    return '\n'.join(L)


def _render_table(ctx: TaskContext, key: str, L: List[str]) -> None:
    t = ctx.tables.get(key)
    if not t or not t.get('rows'):
        return
    L.append(f'### {t["title"]}')
    L.append('')
    if t.get('note'):
        L.append(f'> {t["note"]}')
        L.append('')
    L.append('| ' + ' | '.join(t['headers']) + ' |')
    L.append('|' + '|'.join(['---'] * len(t['headers'])) + '|')
    for row in t['rows']:
        cells = []
        for c in row:
            s = str(c)
            s = s.replace('|', '\\|')
            cells.append(s)
        L.append('| ' + ' | '.join(cells) + ' |')
    L.append('')


def _render_kv_section(ctx: TaskContext, key: str, L: List[str]) -> None:
    s = ctx.kv_sections.get(key)
    if not s or not s.get('pairs'):
        return
    L.append(f'### {s["title"]}')
    L.append('')
    L.append('| Metric | Value |')
    L.append('|--------|-------|')
    for label, value in s['pairs']:
        L.append(f'| {label} | **{value}** |')
    L.append('')


# ===============================================================
# SEND REPORT
# ===============================================================

def send_report_to_super_admins(ctx, run_summary, snapshot_path=None,
                                snapshot_size=0, snapshot_sha=None) -> int:
    admins = get_super_admin_ids()
    if not admins:
        log.warning("No super admins configured — skipping Telegram delivery.")
        return 0
    bot = get_bot_safe()
    if bot is None:
        log.error("Telegram bot is not available.")
        return 0

    message = build_report_message(ctx, run_summary)
    plain_report = build_plain_markdown_report(ctx, run_summary)

    os.makedirs(REPORT_DIR, exist_ok=True)
    report_filename = f'daily_report_{ctx.now.strftime("%Y%m%d")}.md'
    report_path = os.path.join(REPORT_DIR, report_filename)
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(plain_report)
        log.info(f"Full report written: {report_path} ({len(plain_report):,} chars)")
    except Exception as e:
        log.warning(f"Could not write plain report: {e}")
        report_path = None

    send_message = message
    if len(message) > MESSAGE_TRIM_THRESHOLD:
        lines = message.split('\n')
        trimmed = []
        for line in lines:
            trimmed.append(line)
            if '7️⃣' in line:
                break
        trimmed.append('')
        trimmed.append(md_italic(
            '⚠️ Message trimmed — full article attached as file below.'
        ))
        send_message = '\n'.join(trimmed)
        log.info(f"Message trimmed from {len(message)} to {len(send_message)} chars")

    sent = 0
    for admin_id in admins:
        try:
            bot.send_message(admin_id, send_message,
                             parse_mode='MarkdownV2',
                             disable_web_page_preview=True)
        except Exception as e:
            log.error(f"send_message failed for admin {admin_id}: {e}")
            time.sleep(2)
            try:
                bot.send_message(admin_id, send_message,
                                 parse_mode='MarkdownV2',
                                 disable_web_page_preview=True)
            except Exception as e2:
                log.error(f"send_message retry failed: {e2}")
                continue

        if snapshot_path and os.path.exists(snapshot_path):
            try:
                size_mb = snapshot_size / (1024 * 1024)
                caption = (f'📎 Database snapshot · {size_mb:.2f} MB\n'
                           f'SHA-256: {(snapshot_sha or "unknown")[:16]}…')
                with open(snapshot_path, 'rb') as f:
                    bot.send_document(admin_id, f, caption=caption)
            except Exception as e:
                log.warning(f"send_document (snapshot) failed: {e}")

        if report_path and os.path.exists(report_path):
            try:
                size_kb = os.path.getsize(report_path) / 1024
                with open(report_path, 'rb') as f:
                    bot.send_document(
                        admin_id, f,
                        caption=f'📄 Full daily brief — {size_kb:.0f} KB · '
                                f'{len(ctx.tables)} tables · {len(ctx.metrics)} metrics'
                    )
            except Exception as e:
                log.warning(f"send_document (report) failed: {e}")

        sent += 1
    return sent


# ===============================================================
# MAIN RUN
# ===============================================================

def run_all(args, log_):
    t_start = time.time()
    run_date = metric_date_key()
    ctx = TaskContext(dry_run=args.dry_run)
    started_at = somali_format(ctx.now, 'time')
    log_.info('=' * 60)
    log_.info(f'DAILY TASK RUN — {run_date}')
    log_.info(f'Started: {started_at} · dry_run={args.dry_run}')
    log_.info('=' * 60)

    try:
        ensure_tables()
    except Exception as e:
        log_.error(f"Schema setup failed: {e}\n{traceback.format_exc()}")
        return 1

    tasks = select_tasks(args)
    log_.info(f"Running {len(tasks)} task(s).")
    results = []
    for t in tasks:
        results.append(run_one_task(t, ctx, run_date))

    elapsed = time.time() - t_start
    ended_at = somali_format(somali_now(), 'time')
    failed = [r for r in results if r['status'] == 'failed']
    success = [r for r in results if r['status'] == 'success']
    run_summary = {
        'started_at': started_at, 'ended_at': ended_at,
        'duration_seconds': elapsed,
        'tasks_total': len(results), 'tasks_success': len(success),
        'tasks_failed': len(failed),
        'failed_names': [f['name'] for f in failed],
        'results': results,
    }

    log_.info('-' * 60)
    log_.info(f"Tasks: {len(success)} ok / {len(failed)} failed  ·  {elapsed:.1f}s")
    log_.info(f"Metrics: {len(ctx.metrics)} · Tables: {len(ctx.tables)} "
              f"· Sections: {len(ctx.kv_sections)}")
    log_.info('-' * 60)

    if not args.dry_run:
        n = persist_metrics(run_date, ctx.metrics, dry_run=False)
        log_.info(f"Persisted {n} metrics to platform_metrics")
    else:
        log_.info("DRY RUN — skipping metric persistence")

    snapshot_path, snapshot_size, snapshot_sha = None, 0, None
    if not args.no_snapshot and not args.dry_run:
        snapshot_path, snapshot_size, snapshot_sha = create_db_snapshot()
        if snapshot_path:
            log_.info(f"Snapshot created: {snapshot_path} "
                      f"({snapshot_size / (1024 * 1024):.2f} MB)")
        else:
            log_.warning("Snapshot creation failed")
            ctx.warn('DB snapshot creation failed')

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
        md = build_plain_markdown_report(ctx, run_summary)
        print(f"\n[Markdown report: {len(md):,} chars, "
              f"{len(ctx.tables)} tables, {len(ctx.metrics)} metrics]\n")

    log_.info('=' * 60)
    log_.info(f'DAILY TASK RUN COMPLETE — {somali_format(style="short")}')
    log_.info('=' * 60)
    return 0


def run_preview_telegram(args):
    log.info('=' * 60)
    log.info('PREVIEW TELEGRAM — no mutations, no DB writes, no snapshot')
    log.info('=' * 60)
    try:
        ensure_tables()
    except Exception as e:
        log.error(f"Schema setup failed: {e}")
        return 1
    ctx = TaskContext(dry_run=False)

    # Run all analytics tasks (they are read-only)
    analytics_tasks = [t for t in _TASKS if t['category'] == 'analytics']
    for t in analytics_tasks:
        run_one_task(t, ctx, metric_date_key())

    ctx._health_db = {'ok': True, 'text': 'OK · WAL active (preview)'}
    ctx._health_disk = {'ok': True, 'text': 'preview · not checked'}
    ctx._health_backup = {'ok': True, 'text': 'preview · not checked'}

    run_summary = {
        'started_at': somali_format(somali_now(), 'time'),
        'ended_at': somali_format(somali_now(), 'time'),
        'duration_seconds': 1,
        'tasks_total': len(analytics_tasks),
        'tasks_success': len(analytics_tasks),
        'tasks_failed': 0,
        'failed_names': [],
        'results': [{'name': t['name'], 'status': 'success',
                     'items': 0, 'duration_ms': 0} for t in analytics_tasks],
    }

    sent = send_report_to_super_admins(ctx, run_summary)
    log.info(f"Preview sent to {sent} super admin(s).")
    if sent == 0:
        log.warning("No admins reached. Check config.")
        return 2
    return 0


# ===============================================================
# CLI
# ===============================================================

def main():
    parser = argparse.ArgumentParser(description='NuunPlatform Daily Task Runner')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--preview-telegram', action='store_true')
    parser.add_argument('--task', metavar='NAME')
    parser.add_argument('--category', metavar='CAT')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--no-telegram', action='store_true')
    parser.add_argument('--no-snapshot', action='store_true')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    if args.list:
        print_task_list()
        return 0

    if not acquire_lock():
        log.warning("Could not acquire lock — another run is in progress.")
        return 0

    try:
        if args.preview_telegram:
            return run_preview_telegram(args)
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