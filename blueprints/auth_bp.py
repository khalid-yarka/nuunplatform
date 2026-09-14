# blueprints/auth_bp.py
# Authentication routes: register, login, logout, help, check-phone.
#
# Password hashing: werkzeug.security (generate/check).
# No email. No verification step. No password reset.
# New users register with is_verified=0. Admin verifies manually.
#
# The help() route builds a WhatsApp prefill message that includes the
# user's identity and a direct admin profile link when the user is
# logged in, so support requests arrive with everything the admin needs.

import time
import secrets
import logging
import re
from urllib.parse import quote as urlquote

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, jsonify,
)
from werkzeug.security import generate_password_hash, check_password_hash

from config import Config
from db import (
    execute_with_retry,
    get_student_by_phone,
    get_student_by_id,
    get_somali_time_db,
)
from utils import ensure_csrf_token, validate_csrf

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__, url_prefix='')


# ============================================
# RATE LIMITING (in-memory, per-IP)
# ============================================

_rate_buckets = {}


def _rate_limit(key: str, max_calls: int, window_seconds: int) -> bool:
    now = time.time()
    bucket = _rate_buckets.setdefault(key, [])
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= max_calls:
        return False
    bucket.append(now)
    return True


def _client_ip() -> str:
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr or 'unknown'


# ============================================
# VALIDATION HELPERS
# ============================================

def _valid_phone(phone: str) -> bool:
    digits = re.sub(r'\D', '', phone or '')
    return len(digits) == 9


def _valid_name(name: str) -> bool:
    name = (name or '').strip()
    return len(name) >= 4 and name.isalpha()


def _valid_city(city: str) -> bool:
    city = (city or '').strip()
    return len(city) >= 5 and all(c.isalpha() or c.isspace() for c in city)


def _normalize_phone(phone: str) -> str:
    phone = re.sub(r'\D', '', phone or '')
    if phone.startswith('252'):
        phone = phone[3:]
    return '+252' + phone


VALID_GRADES = ('G7', 'G8', 'F3', 'F4')


# ============================================
# CHECK PHONE (AJAX — for registration form)
# ============================================

@auth_bp.route('/auth/check-phone', methods=['POST'])
def check_phone():
    if not _rate_limit(f'checkphone:{_client_ip()}', max_calls=30, window_seconds=600):
        return jsonify({'valid': False, 'taken': False, 'error': 'rate_limited'}), 429

    data = request.get_json(silent=True) or {}
    phone_raw = (data.get('phone') or '').strip()

    if not _valid_phone(phone_raw):
        return jsonify({'valid': False, 'taken': False}), 200

    phone = _normalize_phone(phone_raw)

    try:
        existing = get_student_by_phone(phone)
    except Exception as e:
        logger.warning(f"check_phone lookup failed: {e}")
        return jsonify({'valid': True, 'taken': False}), 200

    return jsonify({'valid': True, 'taken': bool(existing)}), 200


# ============================================
# REGISTRATION
# ============================================

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard.home'))

    if request.method == 'GET':
        ensure_csrf_token()
        return render_template('auth/register.html')

    if not _rate_limit(f'register:{_client_ip()}', max_calls=5, window_seconds=600):
        flash('Too many attempts. Please wait a few minutes.', 'error')
        return render_template('auth/register.html')

    if not validate_csrf():
        flash('Invalid request. Please refresh and try again.', 'error')
        return render_template('auth/register.html')

    phone_raw = (request.form.get('phone') or '').strip()
    password = request.form.get('password') or ''
    confirm = request.form.get('confirm_password') or ''
    first_name = (request.form.get('first_name') or '').strip()
    middle_name = (request.form.get('middle_name') or '').strip()
    last_name = (request.form.get('last_name') or '').strip()
    location = (request.form.get('location') or '').strip()
    city = (request.form.get('city') or '').strip()
    school = (request.form.get('school') or '').strip()
    school_manual = (request.form.get('school_manual') or '').strip()
    grade = (request.form.get('grade') or '').strip()
    curriculum = (request.form.get('curriculum') or '').strip()

    if not _valid_phone(phone_raw):
        flash('Please enter a valid 9-digit phone number.', 'error')
        return render_template('auth/register.html')

    if len(password) < 8:
        flash('Password must be at least 8 characters.', 'error')
        return render_template('auth/register.html')

    if password != confirm:
        flash('Passwords do not match.', 'error')
        return render_template('auth/register.html')

    if not _valid_name(first_name):
        flash('First name must be at least 4 letters.', 'error')
        return render_template('auth/register.html')

    if not _valid_name(middle_name):
        flash('Middle name must be at least 4 letters.', 'error')
        return render_template('auth/register.html')

    if not _valid_name(last_name):
        flash('Last name must be at least 4 letters.', 'error')
        return render_template('auth/register.html')

    if location not in ('SO', 'PL', 'SL'):
        flash('Please select a valid location.', 'error')
        return render_template('auth/register.html')

    if not _valid_city(city):
        flash('City must be at least 5 letters.', 'error')
        return render_template('auth/register.html')

    if grade not in VALID_GRADES:
        flash('Please select a valid grade.', 'error')
        return render_template('auth/register.html')

    school_value = school_manual if school == 'manual' and school_manual else school
    if not school_value:
        flash('Please select or enter your school.', 'error')
        return render_template('auth/register.html')

    if location == 'PL':
        if curriculum not in ('general', 'science', 'arts'):
            flash('Please select your curriculum.', 'error')
            return render_template('auth/register.html')
    else:
        curriculum = None

    phone = _normalize_phone(phone_raw)

    try:
        existing = get_student_by_phone(phone)
        if existing:
            flash('This phone number is already registered. Please login.', 'error')
            return render_template('auth/register.html')
    except Exception as e:
        logger.error(f"Registration uniqueness check failed: {e}", exc_info=True)
        flash('An error occurred. Please try again.', 'error')
        return render_template('auth/register.html')

    password_hash = generate_password_hash(password)

    try:
        execute_with_retry("""
            INSERT INTO students (
                public_id, phone_number, password,
                first_name, middle_name, last_name,
                location, city, school, grade, curriculum,
                total_points, is_admin, is_verified,
                tier, tier_expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 'free', NULL, ?)
        """, (
            _generate_unique_public_id(),
            phone,
            password_hash,
            first_name,
            middle_name,
            last_name,
            location,
            city,
            school_value,
            grade,
            curriculum,
            get_somali_time_db(),
        ), commit=True)
    except Exception as e:
        logger.error(f"Failed to insert new student: {e}", exc_info=True)
        flash('An error occurred while saving your details. Please try again.', 'error')
        return render_template('auth/register.html')

    flash('Registration successful! Please login.', 'success')
    return redirect(url_for('auth.login'))


# ============================================
# LOGIN
# ============================================

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard.home'))

    if request.method == 'GET':
        ensure_csrf_token()
        return render_template('auth/login.html')

    if not _rate_limit(f'login:{_client_ip()}', max_calls=5, window_seconds=60):
        flash('Too many login attempts. Please wait a minute.', 'error')
        return render_template('auth/login.html')

    if not validate_csrf():
        flash('Invalid request. Please refresh and try again.', 'error')
        return render_template('auth/login.html')

    phone_raw = (request.form.get('phone') or '').strip()
    password = request.form.get('password') or ''

    if not _valid_phone(phone_raw):
        flash('Invalid phone number or password.', 'error')
        return render_template('auth/login.html')

    phone = _normalize_phone(phone_raw)

    try:
        student = get_student_by_phone(phone)
    except Exception as e:
        logger.error(f"Login DB error: {e}", exc_info=True)
        flash('An error occurred. Please try again.', 'error')
        return render_template('auth/login.html')

    if not student:
        flash('Invalid phone number or password.', 'error')
        return render_template('auth/login.html')

    if not check_password_hash(student.get('password', ''), password):
        flash('Invalid phone number or password.', 'error')
        return render_template('auth/login.html')

    # ------------------------------------------------------------------
    # SESSION BOOTSTRAP
    # ------------------------------------------------------------------
    # session['session_version'] is stored here so that
    # app.py::refresh_user_state_if_needed can detect a force-logout.
    # ------------------------------------------------------------------
    session.clear()
    session['user_id'] = student['id']
    session['public_id'] = student.get('public_id', '----')
    session['user_name'] = student['first_name']
    session['user_phone'] = student['phone_number']
    session['is_admin'] = bool(student.get('is_admin', 0))
    session['is_verified'] = int(student.get('is_verified', 0))
    session['curriculum'] = student.get('curriculum')
    session['tier'] = student.get('tier', 'free')
    session['tier_expires_at'] = student.get('tier_expires_at')
    session['tier_loaded_at'] = time.time()
    session['session_version'] = int(student.get('session_version', 0) or 0)
    session['user_state_loaded_at'] = time.time()
    session['csrf_token'] = secrets.token_hex(32)
    session.permanent = True
    session.modified = True

    try:
        execute_with_retry(
            "UPDATE students SET last_login_at = ?, last_login_ip = ? WHERE id = ?",
            (get_somali_time_db(), request.remote_addr or '', student['id']),
            commit=True,
        )
    except Exception as e:
        logger.warning(f"Could not record last_login: {e}")

    try:
        from user_settings import get_user_settings
        session['settings'] = get_user_settings(student['id'])
    except Exception:
        session['settings'] = {}

    session.modified = True

    next_url = request.args.get('next')
    if next_url and next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)
    return redirect(url_for('dashboard.home'))


# ============================================
# LOGOUT
# ============================================

@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out. Any unsaved changes were discarded.', 'warning')
    return redirect(url_for('auth.login'))


# ============================================
# HELP
# ============================================

def _build_help_message(student, user_id):
    """
    Compose the WhatsApp prefill for the help button.

    When the user is logged in and has a public_id, the message contains:
      - their name
      - their public id
      - their phone number
      - a direct admin profile URL (BASE_URL + /admin/users/<id>)
    Otherwise a generic message asks them to include their phone manually.
    """
    base_url = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')

    if not student or not user_id:
        return (
            'Hello Admin, I need help with my NuunPlatform account. '
            'Please assist.\n\n'
            '(My phone number: __________)'
        )

    public_id = student.get('public_id') or '----'
    name = (
        f"{student.get('first_name', '')} {student.get('last_name', '')}".strip()
        or 'Student'
    )
    phone = student.get('phone_number') or '—'

    profile_url = f"{base_url}/admin/users/{user_id}" if base_url else None

    lines = [
        'Hello Admin, I need help with my NuunPlatform account.',
        '',
        f'👤 Name: {name}',
        f'🆔 Public ID: {public_id}',
        f'📞 Phone: {phone}',
    ]
    if profile_url:
        lines.append(f'🔗 Profile: {profile_url}')
    lines.append('')
    lines.append('Please assist.')
    return '\n'.join(lines)


@auth_bp.route('/help')
def help():
    ensure_csrf_token()

    phone = Config.SUPER_ADMIN_PHONE or ''
    phone_clean = re.sub(r'\D', '', phone) if phone else ''

    # Build the prefill message based on whether the user is logged in.
    student = None
    user_id = session.get('user_id')
    if user_id:
        try:
            student = get_student_by_id(user_id)
        except Exception as e:
            logger.warning(f"help(): could not load student {user_id}: {e}")

    prefill = _build_help_message(student, user_id)
    help_wa_url = None
    if phone_clean:
        help_wa_url = (
            f"https://wa.me/{phone_clean}?text={urlquote(prefill)}"
        )

    return render_template(
        'auth/help.html',
        super_admin_phone=phone_clean,
        help_wa_url=help_wa_url,
        help_prefill=prefill,
    )


# ============================================
# INTERNAL HELPERS
# ============================================

_PUBLIC_ID_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789'


def _generate_unique_public_id() -> str:
    for _ in range(20):
        candidate = ''.join(secrets.choice(_PUBLIC_ID_CHARS) for _ in range(4))
        cursor = execute_with_retry(
            "SELECT id FROM students WHERE public_id = ?",
            (candidate,),
        )
        if not cursor.fetchone():
            return candidate
    return secrets.token_hex(2).upper()