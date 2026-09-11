#!/usr/bin/env python3
# seed_test_users.py
# One-time script: insert test users + admin directly into the database.
# For local testing only.
#
# Usage:
#   python seed_test_users.py
#
# Re-running updates existing users' passwords and re-activates them.

import os
import sys
import sqlite3
import secrets
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import Config


# ============================================
# TEST ACCOUNTS
# ============================================

TEST_ACCOUNTS = [
    # ---- Admin (verified) ----
    {
        'phone':    '610000000',
        'password': 'admin1234',
        'first':    'Admin',
        'middle':   'System',
        'last':     'Root',
        'location': 'SO',
        'city':     'Mogadishu',
        'school':   'NuunPlatform HQ',
        'grade':    'F4',
        'tier':     'pro',
        'verified': 1,
        'is_admin': 1,
    },
    # ---- Free, unverified (banner test) ----
    {
        'phone':    '610000001',
        'password': 'test1234',
        'first':    'Ahmed',
        'middle':   'Hassan',
        'last':     'Ali',
        'location': 'SO',
        'city':     'Mogadishu',
        'school':   'Test Secondary School',
        'grade':    'G7',
        'tier':     'free',
        'verified': 0,
    },
    # ---- Premium, verified ----
    {
        'phone':    '610000002',
        'password': 'test1234',
        'first':    'Fatima',
        'middle':   'Omar',
        'last':     'Yusuf',
        'location': 'PL',
        'city':     'Garowe',
        'school':   'Test High School',
        'grade':    'G8',
        'tier':     'premium',
        'verified': 1,
        'curriculum': 'general',
    },
    # ---- Pro, verified ----
    {
        'phone':    '610000003',
        'password': 'test1234',
        'first':    'Mohamed',
        'middle':   'Abdullah',
        'last':     'Ibrahim',
        'location': 'SL',
        'city':     'Hargeisa',
        'school':   'Test Academy',
        'grade':    'F3',
        'tier':     'pro',
        'verified': 1,
    },
]


# ============================================
# HELPERS
# ============================================

def ensure_schema(conn):
    schema_path = os.path.join(BASE_DIR, 'schema.sql')
    if not os.path.exists(schema_path):
        print("❌ schema.sql not found in project root. Cannot proceed.")
        sys.exit(1)

    with open(schema_path, 'r', encoding='utf-8') as f:
        schema = f.read()

    conn.executescript(schema)
    conn.commit()
    print("✅ Schema applied (tables verified / created).")


def generate_public_id(conn, chars='ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789'):
    for _ in range(50):
        candidate = ''.join(secrets.choice(chars) for _ in range(4))
        row = conn.execute(
            "SELECT 1 FROM students WHERE public_id = ?", (candidate,)
        ).fetchone()
        if not row:
            return candidate
    raise RuntimeError("Could not generate a unique public ID")


def normalize_phone(phone):
    digits = ''.join(c for c in phone if c.isdigit())
    if digits.startswith('252'):
        digits = digits[3:]
    return '+252' + digits


def upsert_student(conn, acc):
    phone = normalize_phone(acc['phone'])
    password_hash = generate_password_hash(acc['password'])

    existing = conn.execute(
        "SELECT id, public_id FROM students WHERE phone_number = ?",
        (phone,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE students
            SET password = ?, first_name = ?, middle_name = ?, last_name = ?,
                location = ?, city = ?, school = ?, grade = ?,
                curriculum = ?, tier = ?, tier_expires_at = NULL,
                tier_updated_at = datetime('now', 'localtime'),
                is_admin = ?, is_verified = ?
            WHERE id = ?
        """, (
            password_hash,
            acc['first'], acc['middle'], acc['last'],
            acc['location'], acc['city'], acc['school'], acc['grade'],
            acc.get('curriculum'),
            acc['tier'],
            acc.get('is_admin', 0),
            acc.get('verified', 0),
            existing['id'],
        ))
        conn.commit()
        return ('updated', phone, existing['public_id'])

    public_id = generate_public_id(conn)
    conn.execute("""
        INSERT INTO students (
            public_id, phone_number, password,
            first_name, middle_name, last_name,
            location, city, school, grade, curriculum,
            total_points, is_admin, is_verified, tier, tier_expires_at,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, NULL, datetime('now', 'localtime'))
    """, (
        public_id, phone, password_hash,
        acc['first'], acc['middle'], acc['last'],
        acc['location'], acc['city'], acc['school'], acc['grade'],
        acc.get('curriculum'),
        acc.get('is_admin', 0),
        acc.get('verified', 0),
        acc['tier'],
    ))
    conn.commit()
    return ('created', phone, public_id)


# ============================================
# MAIN
# ============================================

def main():
    db_path = Config.DATABASE_PATH
    print(f"📁 Database: {db_path}")

    if not os.path.exists(db_path):
        print("ℹ️  Database file not found. It will be created.")

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        ensure_schema(conn)

        print()
        print("=" * 70)
        print("SEEDING TEST USERS")
        print("=" * 70)

        for acc in TEST_ACCOUNTS:
            try:
                action, phone, public_id = upsert_student(conn, acc)
                icon = "🆕" if action == 'created' else "♻️ "
                label = "Admin" if acc.get('is_admin') else acc['tier'].capitalize()
                verified = "✅" if acc.get('verified') else "❌"
                print(f"{icon}  {label:8s} {verified}  {phone}  #{public_id}")
            except Exception as e:
                print(f"❌ Failed to seed {acc['phone']}: {e}")

        # Touch the flag file so any active sessions refresh
        try:
            instance_dir = os.path.join(BASE_DIR, 'instance')
            os.makedirs(instance_dir, exist_ok=True)
            flag_path = os.path.join(instance_dir, 'user_state_changes.flag')
            with open(flag_path, 'w') as f:
                f.write(str(__import__('time').time()))
        except Exception:
            pass

        print()
        print("=" * 70)
        print("LOGIN CREDENTIALS (enter digits only, no +252)")
        print("=" * 70)
        print(f"{'Phone':<14} {'Password':<14} {'Tier':<10} {'Verified':<10} {'Admin'}")
        print("-" * 70)
        for acc in TEST_ACCOUNTS:
            phone = normalize_phone(acc['phone'])
            tier = acc['tier'].upper()
            verified = "YES" if acc.get('verified') else "NO"
            admin = 'YES' if acc.get('is_admin') else '—'
            print(f"{phone:<14} {acc['password']:<14} {tier:<10} {verified:<10} {admin}")

        print()
        print("✅ Done. Log in at /login")
        print()

    finally:
        conn.close()


if __name__ == '__main__':
    main()