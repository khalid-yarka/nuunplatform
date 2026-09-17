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
    print(f"    capped             = {p['capped']}")
    print(f"    skipped            = {p['skipped']}")
    print(f"    elapsed            = {elapsed:.2f}s")
    print()
    return 0


# ═══════════════════════════════════════════════════════════════════
# PRUNE DEAD SUBSCRIPTIONS
# ═══════════════════════════════════════════════════════════════════
def cmd_prune(dry_run=False):
    header("Prune dead subscriptions")

    conn = db_conn()
    try:
        rows = conn.execute(
            "SELECT id, user_id, endpoint FROM push_subscriptions"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        warn("No subscriptions to check.")
        return 0

    print(f"  Testing {len(rows)} endpoint(s)…\n")

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        fail("pywebpush not installed")
        return 1

    from config import Config

    dead_ids = []
    for r in rows:
        sub = conn.execute(
            "SELECT p256dh, auth FROM push_subscriptions WHERE id = ?",
            (r['id'],)
        ).fetchone()
        try:
            # Send a payload that some services will reject silently;
            # we just want to know if the endpoint is alive.
            webpush(
                subscription_info={
                    'endpoint': r['endpoint'],
                    'keys': {'p256dh': sub['p256dh'], 'auth': sub['auth']},
                },
                data='{"title":"_prune_probe"}',
                vapid_private_key=Config.VAPID_PRIVATE_KEY,
                vapid_claims={'sub': Config.VAPID_SUBJECT},
                timeout=8,
            )
            print(f"  {green('live')}   id={r['id']} uid={r['user_id']}")
        except WebPushException as exc:
            status = getattr(exc.response, 'status_code', None)
            if status in (404, 410):
                print(f"  {red('dead')}   id={r['id']} uid={r['user_id']} (HTTP {status})")
                dead_ids.append(r['id'])
            else:
                print(f"  {yellow('?')}      id={r['id']} uid={r['user_id']} (HTTP {status})")
        except Exception as e:
            print(f"  {yellow('?')}      id={r['id']} uid={r['user_id']} ({e})")

    print()
    if not dead_ids:
        ok("No dead subscriptions found.")
        return 0

    if dry_run:
        warn(f"DRY-RUN — would delete {len(dead_ids)} subscription(s).")
        return 0

    answer = input(f"  Delete {len(dead_ids)} dead subscription(s)? [y/N] ").strip().lower()
    if answer not in ('y', 'yes'):
        warn("Cancelled.")
        return 0

    conn = db_conn()
    try:
        for sid in dead_ids:
            conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (sid,))
        conn.commit()
        ok(f"Deleted {len(dead_ids)} subscription(s).")
    finally:
        conn.close()

    print()
    return 0


# ═══════════════════════════════════════════════════════════════════
# USER DETAIL
# ═══════════════════════════════════════════════════════════════════
def cmd_user_detail(identifier):
    header(f"User detail: {identifier}")

    if isinstance(identifier, int):
        user = find_user(user_id=identifier)
    elif re.fullmatch(r'\d{4,9}', str(identifier)):
        user = find_user(phone=str(identifier))
    else:
        user = find_user(public_id=str(identifier))

    if not user:
        fail(f"No user found: {identifier}")
        return 1

    print(f"  {bold('Identity')}")
    print(f"    id           {user['id']}")
    print(f"    public_id    @{user.get('public_id')}")
    print(f"    name         {user.get('first_name')} {user.get('last_name')}")
    print(f"    phone        {user.get('phone_number')}")
    print(f"    tier         {user.get('tier')}")
    print()

    conn = db_conn()
    try:
        subs = conn.execute(
            "SELECT id, endpoint, created_at FROM push_subscriptions WHERE user_id = ?",
            (user['id'],)
        ).fetchall()

        print(f"  {bold('Subscriptions')}")
        if not subs:
            warn("None")
        else:
            for s in subs:
                host = s['endpoint'].split('/')[2] if '://' in s['endpoint'] else 'unknown'
                print(f"    id={s['id']}  {host}")
                print(f"        {dim(s['created_at'])}")
        print()

        # Settings
        us = conn.execute(
            "SELECT settings FROM user_settings WHERE user_id = ?",
            (user['id'],)
        ).fetchone()

        print(f"  {bold('Push settings')}")
        if us and us['settings']:
            try:
                s = json.loads(us['settings'])
                for k in (
                    'notifications.push_enabled',
                    'notifications.push_live_quiz_start',
                    'notifications.push_live_quiz_result',
                    'notifications.push_participant_joined',
                    'notifications.push_admin_announcement',
                    'notifications.push_quiz_complete',
                ):
                    v = s.get(k, '(missing)')
                    print(f"    {k:<45} {v}")
            except Exception as e:
                warn(f"Could not parse settings JSON: {e}")
        else:
            warn("No user_settings row")
    finally:
        conn.close()

    print()
    return 0


# ═══════════════════════════════════════════════════════════════════
# INTERACTIVE MENU
# ═══════════════════════════════════════════════════════════════════
MENU = """
  {b}1){/b}  Send test push to a user
  {b}2){/b}  Send test push to ALL subscriptions
  {b}3){/b}  Send custom message
  {b}4){/b}  Simulate a real event (live_quiz_start, etc.)
  {b}5){/b}  Broadcast announcement (admin path)
  {b}6){/b}  Show user detail + settings
  {b}7){/b}  List all subscriptions
  {b}8){/b}  Run full diagnostic
  {b}9){/b}  Prune dead subscriptions
  {b}0){/b}  Exit
""".replace('{b}', '\033[1m').replace('{/b}', '\033[0m')


def _prompt(msg):
    try:
        return input(msg).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None


def interactive_menu():
    header("NuunPlatform — Push Test Tool")

    while True:
        print(MENU)
        choice = _prompt("  Choice: ")
        if choice is None:
            break

        if choice == '0':
            print()
            info("Bye.")
            return 0

        elif choice == '1':
            ident = _prompt("  User (id, public_id, or phone): ")
            if ident:
                try:
                    ident_i = int(ident)
                    cmd_send_to_user(ident_i)
                except ValueError:
                    cmd_send_to_user(ident)

        elif choice == '2':
            cmd_send_to_all()

        elif choice == '3':
            ident = _prompt("  User (id, public_id, or phone): ")
            if not ident:
                continue
            title = _prompt("  Title: ") or 'NuunPlatform'
            body  = _prompt("  Body: ")  or 'Test from CLI'
            url   = _prompt("  URL [/settings/#notifications]: ") or '/settings/#notifications'
            try:
                ident_i = int(ident)
                cmd_custom(ident_i, title, body, url)
            except ValueError:
                cmd_custom(ident, title, body, url)

        elif choice == '4':
            event = _prompt(
                "  Event (live_quiz_start / live_quiz_result / participant_joined / "
                "admin_announcement / quiz_complete): "
            )
            if not event:
                continue
            ident = _prompt("  User: ")
            if not ident:
                continue
            try:
                ident_i = int(ident)
                cmd_simulate(event, ident_i)
            except ValueError:
                cmd_simulate(event, ident)

        elif choice == '5':
            title = _prompt("  Title: ") or 'Announcement'
            body  = _prompt("  Body: ")  or 'Test broadcast from CLI'
            cmd_broadcast_announcement(title, body)

        elif choice == '6':
            ident = _prompt("  User: ")
            if not ident:
                continue
            try:
                ident_i = int(ident)
                cmd_user_detail(ident_i)
            except ValueError:
                cmd_user_detail(ident)

        elif choice == '7':
            cmd_list_subscriptions()

        elif choice == '8':
            cmd_diagnose()

        elif choice == '9':
            cmd_prune()

        else:
            warn("Unknown choice.")

        print()


# ═══════════════════════════════════════════════════════════════════
# ARGUMENT PARSING
# ═══════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description='NuunPlatform push test tool.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python3 test_push.py                          # interactive menu
              python3 test_push.py --diagnose               # full system check
              python3 test_push.py --list                   # all subscriptions
              python3 test_push.py --public-id A4K7         # test one user
              python3 test_push.py --phone 612345678
              python3 test_push.py --user-id 42
              python3 test_push.py --all                    # every subscriber
              python3 test_push.py --custom --public-id A4K7 \\
                  --title "Hi" --body "Testing"
              python3 test_push.py --simulate live_quiz_start --user-id 42
              python3 test_push.py --announce --title "Hi" --body "Msg"
              python3 test_push.py --prune --dry-run
        """),
    )

    # Target selection
    parser.add_argument('--user-id', type=int)
    parser.add_argument('--public-id', type=str)
    parser.add_argument('--phone', type=str)
    parser.add_argument('--all', action='store_true')

    # Actions
    parser.add_argument('--diagnose', action='store_true')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--prune', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--detail', action='store_true')

    # Custom message
    parser.add_argument('--custom', action='store_true')
    parser.add_argument('--title', type=str, default='NuunPlatform')
    parser.add_argument('--body',  type=str, default='Test from CLI')
    parser.add_argument('--url',   type=str, default='/settings/#notifications')

    # Simulate real event
    parser.add_argument('--simulate', type=str,
        choices=['live_quiz_start', 'live_quiz_result', 'participant_joined',
                 'admin_announcement', 'quiz_complete'])

    # Broadcast
    parser.add_argument('--announce', action='store_true')

    args = parser.parse_args()

    # No args → interactive
    if len(sys.argv) == 1:
        return interactive_menu()

    # Determine target identifier
    target = None
    if args.user_id is not None:
        target = args.user_id
    elif args.public_id:
        target = args.public_id
    elif args.phone:
        target = args.phone

    # Dispatch
    if args.diagnose:
        return cmd_diagnose() or 0

    if args.list:
        return cmd_list_subscriptions() or 0

    if args.prune:
        return cmd_prune(dry_run=args.dry_run) or 0

    if args.detail:
        if not target:
            fail("--detail requires --user-id, --public-id, or --phone")
            return 1
        return cmd_user_detail(target) or 0

    if args.simulate:
        if not target:
            fail("--simulate requires a user selector")
            return 1
        return cmd_simulate(args.simulate, target) or 0

    if args.announce:
        return cmd_broadcast_announcement(args.title, args.body) or 0

    if args.custom:
        return cmd_custom(target, args.title, args.body, args.url) or 0

    if args.all:
        return cmd_send_to_all(title=args.title, body=args.body, confirm=True) or 0

    if target is not None:
        return cmd_send_to_user(target) or 0

    # Unknown combination → menu
    return interactive_menu()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        print(dim("  Interrupted."))
        sys.exit(130)
    except Exception as e:
        print()
        fail(f"Unhandled error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)