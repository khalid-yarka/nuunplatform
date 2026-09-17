#!/usr/bin/env python3
"""
NuunPlatform — Interactive Push Test Tool

Run directly for a menu:

    python3 test_push.py

Or pass arguments for scripting:

    python3 test_push.py --public-id A4K7
    python3 test_push.py --phone 612345678
    python3 test_push.py --user-id 42
    python3 test_push.py --all
    python3 test_push.py --custom --public-id A4K7 --title "Hi" --body "Test"
    python3 test_push.py --diagnose
    python3 test_push.py --prune
    python3 test_push.py --list

The tool never modifies user data unless you explicitly ask it to.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import textwrap
import time
from datetime import datetime

# ─── Project root resolution ────────────────────────────────────────
try:
    import config as _cfg
    ROOT = os.path.dirname(os.path.abspath(_cfg.__file__))
except Exception:
    ROOT = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, ROOT)


# ═══════════════════════════════════════════════════════════════════
# COLOR HELPERS
# ═══════════════════════════════════════════════════════════════════
_IS_TTY = sys.stdout.isatty()


def _c(code, text):
    if not _IS_TTY:
        return text
    return f"\033[{code}m{text}\033[0m"


def green(t):  return _c('92', t)
def red(t):    return _c('91', t)
def yellow(t): return _c('93', t)
def blue(t):   return _c('94', t)
def dim(t):    return _c('2',  t)
def bold(t):   return _c('1',  t)
def cyan(t):   return _c('96', t)


def hr(ch='─', n=68):
    print(dim(ch * n))


def header(title):
    print()
    hr('═')
    print(f"  {bold(title)}")
    hr('═')
    print()


def ok(msg):   print(f"  {green('✓')} {msg}")
def fail(msg): print(f"  {red('✗')} {msg}")
def warn(msg): print(f"  {yellow('⚠')} {msg}")
def info(msg): print(f"  {blue('·')} {msg}")


# ═══════════════════════════════════════════════════════════════════
# CORE SETUP
# ═══════════════════════════════════════════════════════════════════
def load_stack():
    """Load Config + push_service. Exit on failure."""
    try:
        from config import Config
    except Exception as e:
        fail(f"Cannot import config.py: {e}")
        sys.exit(1)

    try:
        from services import push_service
    except Exception as e:
        fail(f"Cannot import services.push_service: {e}")
        sys.exit(1)

    return Config, push_service


def db_path():
    from config import Config
    return Config.DATABASE_PATH


def db_conn():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ═══════════════════════════════════════════════════════════════════
# LOOKUP
# ═══════════════════════════════════════════════════════════════════
def normalize_phone(raw):
    digits = re.sub(r'\D', '', raw or '')
    if digits.startswith('252'):
        digits = digits[3:]
    return '+252' + digits


def find_user(user_id=None, public_id=None, phone=None):
    """
    Return a student dict or None. Tries in order:
    user_id → public_id → phone.
    """
    conn = db_conn()
    try:
        if user_id:
            row = conn.execute(
                "SELECT * FROM students WHERE id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

        if public_id:
            row = conn.execute(
                "SELECT * FROM students WHERE UPPER(public_id) = UPPER(?)",
                (public_id.strip(),)
            ).fetchone()
            return dict(row) if row else None

        if phone:
            normalized = normalize_phone(phone)
            row = conn.execute(
                "SELECT * FROM students WHERE phone_number = ?",
                (normalized,)
            ).fetchone()
            if row:
                return dict(row)
            # fallback: match by digit-only
            digits = re.sub(r'\D', '', phone)
            row = conn.execute(
                "SELECT * FROM students WHERE phone_number LIKE ?",
                (f'%{digits}%',)
            ).fetchone()
            return dict(row) if row else None

        return None
    finally:
        conn.close()


def describe_user(u):
    if not u:
        return '(unknown)'
    name = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
    return f"{name} (#{u['id']}, @{u.get('public_id')}, {u.get('phone_number')})"


# ═══════════════════════════════════════════════════════════════════
# DIAGNOSTIC REPORT
# ═══════════════════════════════════════════════════════════════════
def cmd_diagnose():
    header("Push System Diagnostic")
    Config, push_service = load_stack()

    # ── 1. Config ────────────────────────────────────────────────
    print(bold("  1. Config"))
    if Config.VAPID_PUBLIC_KEY:
        ok(f"VAPID_PUBLIC_KEY   ({len(Config.VAPID_PUBLIC_KEY)} chars)")
    else:
        fail("VAPID_PUBLIC_KEY missing")

    if Config.VAPID_PRIVATE_KEY:
        ok(f"VAPID_PRIVATE_KEY  ({len(Config.VAPID_PRIVATE_KEY)} chars)")
    else:
        fail("VAPID_PRIVATE_KEY missing")

    if Config.VAPID_SUBJECT and Config.VAPID_SUBJECT.startswith(('mailto:', 'https://')):
        ok(f"VAPID_SUBJECT      {Config.VAPID_SUBJECT}")
    else:
        fail(f"VAPID_SUBJECT malformed: {Config.VAPID_SUBJECT!r}")

    if Config.PUSH_ENABLED:
        ok(f"PUSH_ENABLED       True")
    else:
        fail("PUSH_ENABLED       False — push is disabled")
    print()

    # ── 2. Dependencies ──────────────────────────────────────────
    print(bold("  2. Dependencies"))
    try:
        import cryptography
        ok(f"cryptography       {cryptography.__version__}")
    except Exception as e:
        fail(f"cryptography       {e}")

    try:
        import pywebpush
        v = getattr(pywebpush, '__version__', 'unknown')
        ok(f"pywebpush          {v}")
    except Exception as e:
        fail(f"pywebpush          {e}")

    if push_service.is_available():
        ok("push_service       available")
    else:
        fail("push_service       NOT available")
    print()

    # ── 3. Database ──────────────────────────────────────────────
    print(bold("  3. Database"))
    path = db_path()
    if os.path.exists(path):
        ok(f"DB exists          {path}")
    else:
        fail(f"DB missing         {path}")
        return

    conn = db_conn()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='push_subscriptions'"
        ).fetchone()
        if row:
            ok("table              push_subscriptions")
        else:
            fail("table              push_subscriptions MISSING")
            return

        total = conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
        users = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM push_subscriptions"
        ).fetchone()[0]
        ok(f"subscriptions      {total} row(s), {users} unique user(s)")

        orphans = conn.execute(
            "SELECT COUNT(*) FROM push_subscriptions ps "
            "LEFT JOIN students s ON s.id = ps.user_id WHERE s.id IS NULL"
        ).fetchone()[0]
        if orphans:
            warn(f"orphans            {orphans} row(s) with no matching student")
        else:
            ok("orphans            none")
    finally:
        conn.close()
    print()

    # ── 4. Recent log lines ──────────────────────────────────────
    print(bold("  4. Recent push log lines"))
    log_path = os.path.join(ROOT, 'logs', 'workers.log')
    if os.path.exists(log_path):
        with open(log_path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 100_000))
            tail = f.read().decode('utf-8', errors='replace')

        hits = [
            ln for ln in tail.splitlines()
            if re.search(r'push|vapid', ln, re.I)
        ]
        if hits:
            for ln in hits[-15:]:
                print(f"    {dim(ln)}")
        else:
            ok("no push-related lines in recent log")
    else:
        warn(f"log file not found: {log_path}")
    print()


# ═══════════════════════════════════════════════════════════════════
# LIST SUBSCRIPTIONS
# ═══════════════════════════════════════════════════════════════════
def cmd_list_subscriptions():
    header("Push Subscriptions")
    conn = db_conn()
    try:
        rows = conn.execute("""
            SELECT ps.id, ps.user_id, ps.endpoint, ps.created_at,
                   s.public_id, s.first_name, s.last_name, s.phone_number
            FROM push_subscriptions ps
            LEFT JOIN students s ON s.id = ps.user_id
            ORDER BY ps.created_at DESC
        """).fetchall()

        if not rows:
            warn("No subscriptions on file.")
            print()
            print(dim("  Users must toggle push on in Settings → Notifications."))
            print()
            return

        print(f"  {len(rows)} subscription(s) on file.\n")
        for r in rows:
            name = f"{r['first_name'] or '?'} {r['last_name'] or ''}".strip()
            pid = r['public_id'] or '----'
            host = r['endpoint'].split('/')[2] if '://' in r['endpoint'] else 'unknown'
            print(f"  {cyan(str(r['id']).rjust(4))}  "
                  f"{name:<28} @{pid:<6} "
                  f"uid={str(r['user_id']).rjust(4)}  "
                  f"{host}")
            print(f"        {dim('endpoint:')} {dim('…' + r['endpoint'][-52:])}")
            print(f"        {dim('created:')}  {r['created_at']}")
            print()
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# SEND ONE PUSH
# ═══════════════════════════════════════════════════════════════════
def send_test(user_id, title='NuunPlatform', body=None,
              url='/settings/#notifications', tag='nuun-cli-test'):
    """Deliver one push and print the result. Returns result dict."""
    Config, push_service = load_stack()

    if body is None:
        body = 'Push test from the CLI tool.'

    result = push_service.deliver(
        user_id,
        title=title,
        body=body,
        url=url,
        tag=tag,
        data={'type': 'cli_test'},
    )

    print(f"    sent={result['sent']}  failed={result['failed']}  pruned={result['pruned']}")

    if result['sent'] > 0:
        ok("Delivery accepted by the push service.")
    elif result['pruned'] > 0:
        warn(f"All {result['pruned']} endpoint(s) were dead and pruned.")
    elif result['failed'] > 0:
        fail("Delivery failed — check logs.")
    else:
        warn("Nothing to deliver — user has no subscriptions.")

    return result


def cmd_send_to_user(identifier):
    """identifier is an int, phone, or public_id — auto-detect."""
    header(f"Send test push to: {identifier}")

    user = None
    if isinstance(identifier, int):
        user = find_user(user_id=identifier)
    elif re.fullmatch(r'\d{4,9}', str(identifier)):
        user = find_user(phone=str(identifier))
    else:
        user = find_user(public_id=str(identifier))

    if not user:
        fail(f"No user found for: {identifier}")
        return 1

    name = f"{user.get('first_name','')} {user.get('last_name','')}".strip()
    print(f"  User: {name}")
    print(f"  ID:   {user['id']}")
    print(f"  PID:  @{user.get('public_id')}")
    print(f"  Phone: {user.get('phone_number')}")
    print()

    # Show subscription count
    conn = db_conn()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM push_subscriptions WHERE user_id=?",
            (user['id'],)
        ).fetchone()[0]
    finally:
        conn.close()

    print(f"  Subscriptions: {count}")
    print()

    if count == 0:
        warn("This user has no push subscriptions.")
        print(dim("  Ask them to open Settings → Notifications and toggle push on."))
        print()
        return 1

    print("  Sending…")
    result = send_test(user['id'])
    print()
    return 0 if result['sent'] > 0 else 1


# ═══════════════════════════════════════════════════════════════════
# SEND TO ALL
# ═══════════════════════════════════════════════════════════════════
def cmd_send_to_all(title='NuunPlatform', body=None, confirm=True):
    header("Send test push to ALL subscriptions")

    if body is None:
        body = 'Broadcast test from the CLI tool.'

    conn = db_conn()
    try:
        user_ids = [
            r['user_id'] for r in conn.execute(
                "SELECT DISTINCT user_id FROM push_subscriptions"
            ).fetchall()
        ]
    finally:
        conn.close()

    if not user_ids:
        warn("No subscriptions in the database.")
        return 1

    print(f"  {len(user_ids)} user(s) have at least one subscription.\n")

    if confirm:
        answer = input(f"  Send to all {len(user_ids)} user(s)? [y/N] ").strip().lower()
        if answer not in ('y', 'yes'):
            warn("Cancelled.")
            return 1
        print()

    Config, push_service = load_stack()

    totals = {'sent': 0, 'failed': 0, 'pruned': 0, 'users_reached': 0}
    for uid in user_ids:
        r = push_service.deliver(uid, title=title, body=body,
                                 tag='nuun-cli-broadcast',
                                 data={'type': 'cli_broadcast'})
        totals['sent']          += r['sent']
        totals['failed']        += r['failed']
        totals['pruned']        += r['pruned']
        if r['sent'] > 0:
            totals['users_reached'] += 1

    print(f"  {bold('Aggregate results')}")
    print(f"    users_reached = {totals['users_reached']}/{len(user_ids)}")
    print(f"    sent          = {totals['sent']}")
    print(f"    failed        = {totals['failed']}")
    print(f"    pruned        = {totals['pruned']}")
    print()
    return 0


# ═══════════════════════════════════════════════════════════════════
# CUSTOM MESSAGE
# ═══════════════════════════════════════════════════════════════════
def cmd_custom(user_identifier, title, body, url):
    header("Send custom push")
    print(f"  Title: {title}")
    print(f"  Body:  {body}")
    print(f"  URL:   {url}")
    print()

    if user_identifier is None:
        user_identifier = input("  User (id, public_id, or phone): ").strip()
        if not user_identifier:
            warn("Cancelled.")
            return 1

    if isinstance(user_identifier, int):
        user = find_user(user_id=user_identifier)
    elif re.fullmatch(r'\d{4,9}', str(user_identifier)):
        user = find_user(phone=str(user_identifier))
    else:
        user = find_user(public_id=str(user_identifier))

    if not user:
        fail(f"No user found for: {user_identifier}")
        return 1

    print(f"  Target: {describe_user(user)}")
    print()

    result = send_test(user['id'], title=title, body=body, url=url,
                       tag='nuun-cli-custom')
    print()
    return 0 if result['sent'] > 0 else 1


# ═══════════════════════════════════════════════════════════════════
# SIMULATE REAL EVENTS
# ═══════════════════════════════════════════════════════════════════
def cmd_simulate(event_type, user_identifier=None):
    """
    Simulate a real notification through notification_service so the
    full fan-out path is exercised (per-type checks, master toggle, etc.)
    """
    header(f"Simulate: {event_type}")

    if user_identifier is None:
        user_identifier = input("  User (id, public_id, or phone): ").strip()

    if isinstance(user_identifier, int):
        user = find_user(user_id=user_identifier)
    elif re.fullmatch(r'\d{4,9}', str(user_identifier)):
        user = find_user(phone=str(user_identifier))
    else:
        user = find_user(public_id=str(user_identifier))

    if not user:
        fail(f"No user found for: {user_identifier}")
        return 1

    print(f"  Target: {describe_user(user)}\n")

    # Verify preconditions
    try:
        from user_settings import get_user_setting
        master = get_user_setting(user['id'], 'notifications.push_enabled', 0)
        print(f"  push_enabled       = {bool(master)}")

        type_map = {
            'live_quiz_start':    'notifications.push_live_quiz_start',
            'live_quiz_result':   'notifications.push_live_quiz_result',
            'participant_joined': 'notifications.push_participant_joined',
            'admin_announcement': 'notifications.push_admin_announcement',
            'quiz_complete':      'notifications.push_quiz_complete',
        }
        key = type_map.get(event_type)
        if key:
            v = get_user_setting(user['id'], key, 0)
            print(f"  {key:<42} = {bool(v)}")
        print()
    except Exception as e:
        warn(f"Could not read settings: {e}")

    # Build message per event
    if event_type == 'live_quiz_start':
        title = 'Live quiz started'
        body  = 'A live quiz you joined is starting now.'
        link  = '/live-quiz/'
    elif event_type == 'live_quiz_result':
        title = 'Live quiz finished'
        body  = 'Your live quiz results are ready.'
        link  = '/live-quiz/'
    elif event_type == 'participant_joined':
        title = 'Someone joined your quiz'
        body  = 'A participant has joined your live quiz lobby.'
        link  = '/live-quiz/'
    elif event_type == 'admin_announcement':
        title = 'Announcement'
        body  = 'This is a simulated platform announcement.'
        link  = '/notifications/'
    elif event_type == 'quiz_complete':
        title = 'Quiz saved'
        body  = 'Your quiz result was saved to history.'
        link  = '/history/'
    else:
        fail(f"Unknown event type: {event_type}")
        return 1

    try:
        from services import notification_service
    except Exception as e:
        fail(f"Cannot import notification_service: {e}")
        return 1

    print("  Calling notification_service.send_notification(..., force=True)…")
    print()

    ok_flag = notification_service.send_notification(
        user_id=user['id'],
        notification_type=event_type,
        title=title,
        body=body,
        link=link,
        icon='📢',
        force=True,
    )

    if ok_flag:
        ok("In-app notification written. Push fan-out attempted.")
        print(dim("  Check the target device — a push should arrive in a few seconds."))
    else:
        fail("send_notification returned False")

    print()

    # Report the fan-out state
    print(f"  {bold('Why push might not have fired:')}")
    print(dim("    • master push_enabled = 0"))
    print(dim("    • per-type push toggle = 0"))
    print(dim("    • user has no push_subscriptions row"))
    print(dim("    • VAPID keys missing"))
    print()
    return 0


# ═══════════════════════════════════════════════════════════════════
# BROADCAST ANNOUNCEMENT (admin path)
# ═══════════════════════════════════════════════════════════════════
def cmd_broadcast_announcement(title, body):
    header("Broadcast announcement (with push)")

    print(f"  Title: {title}")
    print(f"  Body:  {body}")
    print()

    answer = input("  Send to ALL users with push enabled? [y/N] ").strip().lower()
    if answer not in ('y', 'yes'):
        warn("Cancelled.")
        return 1

    try:
        from services.notification_service import broadcast_announcement
    except Exception as e:
        fail(f"Cannot import broadcast_announcement: {e}")
        return 1

    print()
    print("  Sending…")
    t0 = time.time()
    result = broadcast_announcement(
        title=title,
        body=body,
        link='/notifications/',
        icon='📢',
        also_push=True,
    )
    elapsed = time.time() - t0

    p = result['push']
    print()
    print(f"  {bold('Result')}")
    print(f"    in-app rows        = {result['in_app']}")
    print(f"    push recipients    = {p['recipients']}")
    print(f"    push sent          = {p['sent']}")
    print(f"    push failed        = {p['failed']}")
    print(f"    push pruned        = {p['pruned']}")
    print(f"    capped             = {p['capp