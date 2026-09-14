#!/usr/bin/env python3
"""
who_is_super.py — Diagnose & fix the super-admin phone match.

Usage:
    python who_is_super.py              # diagnose only
    python who_is_super.py --set-phone 612345678   # update .env
    python who_is_super.py --promote 5 612345678   # update DB for user 5
"""

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from config import Config


def normalize(raw):
    if not raw:
        return ''
    digits = re.sub(r'\D', '', str(raw))
    if digits.startswith('252'):
        digits = digits[3:]
    if digits.startswith('0'):
        digits = digits[1:]
    return digits


def list_users(conn):
    print("Users in the database:")
    print(f"  {'ID':<5} {'NORMALIZED':<12} {'RAW PHONE':<20} {'NAME':<30} ADMIN")
    print("  " + "-" * 80)
    for row in conn.execute(
        "SELECT id, phone_number, first_name, last_name, is_admin "
        "FROM students ORDER BY id"
    ):
        norm = normalize(row['phone_number'])
        name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
        admin_flag = '⭐' if row['is_admin'] else ''
        print(f"  {row['id']:<5} {norm:<12} {str(row['phone_number'] or ''):<20} "
              f"{name[:29]:<30} {admin_flag}")


def update_env(super_phone):
    env_path = BASE_DIR / '.env'
    if not env_path.exists():
        print(f"❌ .env not found at {env_path}")
        return False

    text = env_path.read_text()
    norm = normalize(super_phone)

    # Accept either the raw form the user gave, or a normalised form.
    line = f"SUPER_ADMIN_PHONE=+252{norm}"

    if re.search(r'^SUPER_ADMIN_PHONE\s*=.*$', text, flags=re.MULTILINE):
        text = re.sub(
            r'^SUPER_ADMIN_PHONE\s*=.*$',
            line,
            text,
            flags=re.MULTILINE,
        )
    else:
        if not text.endswith('\n'):
            text += '\n'
        text += line + '\n'

    env_path.write_text(text)
    print(f"✅ Updated .env:  {line}")
    print("   Restart the Flask app for the change to take effect.")
    return True


def promote_user(conn, user_id, phone):
    norm = normalize(phone)
    # Store as the app stores it at registration: +252<9digits>
    stored = f"+252{norm}"

    row = conn.execute(
        "SELECT id, first_name, last_name, is_admin FROM students WHERE id = ?",
        (user_id,),
    ).fetchone()

    if row is None:
        print(f"❌ User id {user_id} does not exist.")
        return False

    conn.execute(
        "UPDATE students SET phone_number = ?, is_admin = 1 WHERE id = ?",
        (stored, user_id),
    )
    conn.commit()

    name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
    print(f"✅ Updated user {user_id} ({name}):")
    print(f"     phone_number = {stored}")
    print(f"     is_admin     = 1")
    print()
    print("   Restart the Flask app and log out/in for the session to refresh.")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--set-phone', metavar='PHONE',
                        help='Update SUPER_ADMIN_PHONE in .env')
    parser.add_argument('--promote', nargs=2, metavar=('USER_ID', 'PHONE'),
                        help='Set a user\'s phone and mark them admin')
    args = parser.parse_args()

    env_super = normalize(Config.SUPER_ADMIN_PHONE)
    print("=" * 70)
    print("  SUPER ADMIN DIAGNOSTIC")
    print("=" * 70)
    print()
    print(f"SUPER_ADMIN_PHONE from .env:")
    print(f"    raw        : {Config.SUPER_ADMIN_PHONE or '(empty)'}")
    print(f"    normalized : {env_super or '(empty)'}")
    print()

    db_path = Config.DATABASE_PATH
    if not os.path.exists(db_path):
        print(f"❌ Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row

    list_users(conn)
    print()

    # Which user matches?
    match_id = None
    for row in conn.execute("SELECT id, phone_number FROM students"):
        if normalize(row['phone_number']) == env_super and env_super:
            match_id = row['id']
            break

    print("=" * 70)
    if not env_super:
        print("❌ SUPER_ADMIN_PHONE is empty.")
        print("   No user will become super admin.")
        print()
        print("   Fix: pick a user id from the table above, then run")
        print("        python who_is_super.py --promote <id> <phone>")
        print("        python who_is_super.py --set-phone <phone>")
    elif match_id is None:
        print(f"❌ SUPER_ADMIN_PHONE ({env_super}) matches NO user.")
        print("   That's why /admin/maintenance 404s.")
        print()
        print("   Two ways to fix — pick ONE:")
        print()
        print("   Option A — change the env var to match an existing user")
        print("     python who_is_super.py --set-phone <phone_of_an_existing_user>")
        print()
        print("   Option B — change an existing user's phone to match the env")
        print("     python who_is_super.py --promote <user_id> " + env_super)
    else:
        print(f"✅ Match found: user id {match_id} IS the super admin.")
        print("   If the site still shows you as a regular admin, you must")
        print("   LOG OUT and LOG BACK IN — the session is only refreshed")
        print("   at login time.")

    # Apply flags if asked
    if args.set_phone:
        print()
        print("Applying --set-phone…")
        update_env(args.set_phone)

    if args.promote:
        try:
            uid = int(args.promote[0])
        except ValueError:
            print(f"❌ Invalid user id: {args.promote[0]}")
            sys.exit(1)
        print()
        print("Applying --promote…")
        promote_user(conn, uid, args.promote[1])

    conn.close()
    print()


if __name__ == '__main__':
    main()