# blueprints/auth_bp.py
# Authentication routes: register, login, logout, help, check-phone,
# school-suggestions.
#
# Password hashing: werkzeug.security (generate/check).
# No email. No verification step. No password reset.
# New users register with is_verified=0. Admin verifies manually.
#
# `next` propagation:
#   The query-string `next` from a gated route (e.g. /pdfs/?pdf=42) is
#   captured on any GET to /login or /register and stored in the session.
#   It survives POST form submission and the register → login round-trip,
#   and is consumed on successful authentication. Values older than
#   AUTH_NEXT_MAX_AGE seconds are ignored.

import time
import secrets
import logging
import re
import threading
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
# NEXT-URL RELAY
# ============================================

_AUTH_NEXT_SESSION = '_auth_next_url'
_AUTH_NEXT_TS_SESSION = '_auth_next_ts'
AUTH_NEXT_MAX_AGE = 20 * 60  # 20 minutes


def _is_safe_next(url: str) -> bool:
    """Only allow same-site paths. Rejects '//host' and anything else."""
    if not url:
        return False
    if not url.startswith('/'):
        return False
    if url.startswith('//'):
        return False
    return True


def _capture_next_from_request():
    """
    Read `next` from the current request's query string and store it in
    the session. Never overwrites an existing value when the current
    request doesn't provide one — this is what lets register → login
    carry the value forward across the handoff.

    Returns the captured value (or None).
    """
    raw = (request.args.get('next') or '').strip()
    if _is_safe_next(raw):
        session[_AUTH_NEXT_SESSION] = raw
        session[_AUTH_NEXT_TS_SESSION] = time.time()
        return raw
    return None


def _peek_next():
    """Return the pending next URL without consuming it, or ''."""
    value = session.get(_AUTH_NEXT_SESSION)
    ts = session.get(_AUTH_NEXT_TS_SESSION)
    if not _is_safe_next(value):
        return ''
    try:
        if ts and (time.time() - float(ts)) > AUTH_NEXT_MAX_AGE:
            return ''
    except (TypeError, ValueError):
        return ''
    return value


def _consume_next():
    """
    Return the pending next URL (if any) and remove it from the session.
    Used on successful login.
    """
    value = session.pop(_AUTH_NEXT_SESSION, None)
    ts = session.pop(_AUTH_NEXT_TS_SESSION, None)
    if not _is_safe_next(value):
        return ''
    try:
        if ts and (time.time() - float(ts)) > AUTH_NEXT_MAX_AGE:
            return ''
    except (TypeError, ValueError):
        return ''
    return value


# ============================================
# NO-CACHE HEADERS ON AUTH PAGES
# ============================================
@auth_bp.after_request
def _no_cache_for_auth_pages(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


# ============================================
# RATE LIMITING (in-memory, per-IP)
# ============================================

_rate_buckets = {}
_rate_lock = threading.Lock()


def _rate_limit(key: str, max_calls: int, window_seconds: int) -> bool:
    now = time.time()
    with _rate_lock:
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
# PER-ACCOUNT LOGIN LOCKOUT
# ============================================

LOGIN_MAX_FAILURES = 10
LOGIN_FAILURE_WINDOW = 15 * 60       # 15 minutes
LOGIN_LOCKOUT_DURATION = 15 * 60     # 15 minutes

_login_failures = {}   # phone -> [timestamp, ...]
_login_lockouts = {}   # phone -> locked_until_timestamp
_login_state_lock = threading.Lock()


def _login_is_locked(phone: str) -> tuple:
    """Return (locked, seconds_remaining)."""
    now = time.time()
    with _login_state_lock:
        until = _login_lockouts.get(phone, 0)
        if until > now:
            return True, int(until - now)
        if until and until <= now:
            _login_lockouts.pop(phone, None)
        return False, 0


def _login_record_failure(phone: str) -> None:
    now = time.time()
    cutoff = now - LOGIN_FAILURE_WINDOW
    with _login_state_lock:
        bucket = _login_failures.setdefault(phone, [])
        bucket[:] = [t for t in bucket if t > cutoff]
        bucket.append(now)
        if len(bucket) >= LOGIN_MAX_FAILURES:
            _login_lockouts[phone] = now + LOGIN_LOCKOUT_DURATION
            _login_failures.pop(phone, None)
            logger.warning(
                f"Account lockout triggered for phone ending ...{phone[-4:]} "
                f"after {LOGIN_MAX_FAILURES} failures"
            )


def _login_clear_failures(phone: str) -> None:
    with _login_state_lock:
        _login_failures.pop(phone, None)
        _login_lockouts.pop(phone, None)


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


def _escape_like(value: str) -> str:
    if not value:
        return ''
    return (
        value.replace('!', '!!')
             .replace('%', '!%')
             .replace('_', '!_')
    )


VALID_GRADES = ('G7', 'G8', 'F3', 'F4')


# ============================================
# TELEGRAM NOTIFICATION ON NEW REGISTRATION
# ============================================

def _notify_new_registration(student: dict) -> None:
    try:
        from services.telegram_notify import (
            notify_super_admins,
            build_markdown_document,
            make_report_filename,
            summary_row,
            truncate,
        )
    except Exception as e:
        logger.warning(f"telegram_notify import failed: {e}")
        return

    base_url = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')
    user_id = student.get('id')
    admin_url = f"{base_url}/admin/users/{user_id}" if (base_url and user_id) else None

    full_name = (
        f"{student.get('first_name', '')} "
        f"{student.get('middle_name', '') or ''} "
        f"{student.get('last_name', '')}"
    ).strip() or 'Unknown'
    public_id = student.get('public_id') or '----'
    phone = student.get('phone_number') or '—'

    meta = {
        'type': 'user_registered',
        'user_id': user_id,
        'public_id': public_id,
        'phone': phone,
        'location': student.get('location'),
        'school': student.get('school'),
        'grade': student.get('grade'),
    }

    sections = []

    identity_table = '\n'.join([
        '| Field | Value |',
        '|:--|:--|',
        f'| Name | {full_name} |',
        f'| Public ID | `{public_id}` |',
        f'| Phone | `{phone}` |',
    ])
    sections.append(('👤 Identity', identity_table))

    edu_table = '\n'.join([
        '| Field | Value |',
        '|:--|:--|',
        f"| Location | {student.get('location') or '—'} |",
        f"| City | {student.get('city') or '—'} |",
        f"| School | {student.get('school') or '—'} |",
        f"| Grade | {student.get('grade') or '—'} |",
        f"| Curriculum | {student.get('curriculum') or '—'} |",
    ])
    sections.append(('🎓 Education', edu_table))

    actions = [
        '- [ ] Open the user panel and review the profile',
        '- [ ] Verify the account so the user can log in',
        '- [ ] Contact the user on WhatsApp if anything looks wrong',
    ]
    if admin_url:
        actions.append(f'- [ ] [Open user panel →]({admin_url})')
    sections.append(('🛠️ Next Steps', '\n'.join(actions)))

    try:
        md_body = build_markdown_document(
            title=f"New registration — {full_name}",
            severity='info',
            meta=meta,
            sections=sections,
            footer_id=f'USR-{public_id}',
        )
    except Exception as e:
        logger.error(f"build_markdown_document failed: {e}", exc_info=True)
        return

    filename = make_report_filename('new_user', public_id)

    summary = [
        summary_row('👤', 'Name', full_name),
        summary_row('📞', 'Phone', phone),
        summary_row('📍', 'Location', student.get('location') or '—'),
        summary_row('🏫', 'School', truncate(student.get('school') or '—', 60)),
    ]

    try:
        notify_super_admins(
            event_type='user_registered',
            title=f'New user registered: {full_name}',
            md_body=md_body,
            md_filename=filename,
            summary=summary,
            primary_url=admin_url,
            primary_url_label='Open user panel',
            severity='info',
            reference_id=f'USR-{public_id}',
            icon='🆕',
        )
    except Exception as e:
        logger.error(f"notify_super_admins failed: {e}", exc_info=True)


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
# SCHOOL SUGGESTIONS (AJAX — for registration form)
# ============================================

@auth_bp.route('/auth/school-suggestions', methods=['GET'])
def school_suggestions():
    if not _rate_limit(f'school:{_client_ip()}', max_calls=60, window_seconds=60):
        return jsonify({'suggestions': []}), 429

    q = (request.args.get('q') or '').strip()
    location = (request.args.get('location') or '').strip().upper()

    if len(q) < 3:
        return jsonify({'suggestions': []}), 200

    if location not in ('SO', 'PL', 'SL'):
        return jsonify({'suggestions': []}), 200

    pattern = _escape_like(q) + '%'

    try:
        cursor = execute_with_retry("""
            SELECT school, COUNT(*) AS n
            FROM students
            WHERE school IS NOT NULL AND school != ''
              AND location = ?
              AND LOWER(school) LIKE LOWER(?) ESCAPE '!'
            GROUP BY school
            ORDER BY n DESC, school ASC
            LIMIT 10
        """, (location, pattern))
        suggestions = [row['school'] for row in cursor.fetchall()]
    except Exception as e:
        logger.warning(f"school_suggestions query failed: {e}")
        suggestions = []

    return jsonify({'suggestions': suggestions}), 200


# ============================================
# REGISTRATION
# ============================================

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard.home'))

    if request.method == 'GET':
        # Capture `next` from the URL (if any). Never overwrites an
        # existing session value when the request doesn't provide one,
        # so the register → login handoff can carry it forward.
        _capture_next_from_request()
        ensure_csrf_token()
        return render_template(
            'auth/register.html',
            next_url=_peek_next(),
        )

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

    # School: at least 2 words. No per-word length or letters-only rule.
    school_words = school.split()
    if len(school_words) < 2:
        flash('School must be at least 2 words.', 'error')
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
            school,
            grade,
            curriculum,
            get_somali_time_db(),
        ), commit=True)
    except Exception as e:
        logger.error(f"Failed to insert new student: {e}", exc_info=True)
        flash('An error occurred while saving your details. Please try again.', 'error')
        return render_template('auth/register.html')

    try:
        new_user = get_student_by_phone(phone)
        if new_user:
            _notify_new_registration(new_user)
    except Exception as e:
        logger.warning(f"Registration notification failed (non-fatal): {e}")

    # Peek — do not consume. The login GET will re-capture from the URL
    # and the login POST will consume it. This makes the register → login
    # handoff preserve the `next` value.
    pending_next = _peek_next()

    flash('Registration successful! Please login.', 'success')

    if pending_next:
        return redirect(url_for('auth.login', next=pending_next))
    return redirect(url_for('auth.login'))


# ============================================
# LOGIN
# ============================================

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard.home'))

    if request.method == 'GET':
        _capture_next_from_request()
        ensure_csrf_token()
        return render_template(
            'auth/login.html',
            next_url=_peek_next(),
        )

    # Layer 1: per-IP rate limit (existing behavior).
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

    # Layer 2: per-account lockout.
    locked, seconds_left = _login_is_locked(phone)
    if locked:
        minutes = max(1, seconds_left // 60)
        flash(
            f'This account is temporarily locked after too many failed '
            f'attempts. Try again in {minutes} minute(s).',
            'error',
        )
        logger.warning(f"Login attempt against locked account ...{phone[-4:]}")
        return render_template('auth/login.html')

    try:
        student = get_student_by_phone(phone)
    except Exception as e:
        logger.error(f"Login DB error: {e}", exc_info=True)
        flash('An error occurred. Please try again.', 'error')
        return render_template('auth/login.html')

    # Unified failure message: never reveal whether the phone exists.
    if not student:
        _login_record_failure(phone)
        flash('Invalid phone number or password.', 'error')
        return render_template('auth/login.html')

    if not check_password_hash(student.get('password', ''), password):
        _login_record_failure(phone)
        flash('Invalid phone number or password.', 'error')
        return render_template('auth/login.html')

    # Success — clear lockout state for this account.
    _login_clear_failures(phone)

    # Read the pending `next` BEFORE session.clear() wipes it.
    next_url = _consume_next()

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

    if next_url:
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
        help_wa_url = f"https://wa.me/{phone_clean}?text={urlquote(prefill)}"

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