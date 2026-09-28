# blueprints/profile_bp.py
# Profile view + edit + public-ID management.
#
# Tier model: free / premium only. No pro.
#
# Public ID rules (two-tier):
#   free     — cannot manage the public ID at all
#   premium  — can regenerate (random) or edit (custom, must be unique)
#
# Public ID format: exactly 4 uppercase letters or digits.

from flask import (
    Blueprint, render_template, request, session, jsonify,
    redirect, url_for, flash,
)
from functools import wraps
import re
import secrets
import string

from db import (
    get_student_by_id,
    execute_with_retry,
    get_student_by_public_id,
)
from services.tier_service import get_current_user_tier

profile_bp = Blueprint('profile', __name__, url_prefix='/profile')


# ============================================================
# CONSTANTS
# ============================================================

# Public-ID charset: A-Z plus digits 1-9 (zero excluded to avoid
# confusion with O). Matches the admin generator in admin_users_db.py.
_PUBLIC_ID_CHARS = string.ascii_uppercase + '123456789'
_PUBLIC_ID_REGEX = re.compile(r'[A-Z0-9]{4}')


# ============================================================
# DECORATORS
# ============================================================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first.', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


# ============================================================
# HELPERS
# ============================================================

def _generate_unique_public_id(max_attempts: int = 10):
    """
    Return a fresh 4-character public ID not currently in use,
    or None if the retry budget is exhausted.
    """
    for _ in range(max_attempts):
        candidate = ''.join(
            secrets.choice(_PUBLIC_ID_CHARS) for _ in range(4)
        )
        if not get_student_by_public_id(candidate):
            return candidate
    return None


# ============================================================
# ROUTES
# ============================================================

@profile_bp.route('/')
@login_required
def index():
    user_id = session['user_id']
    student = get_student_by_id(user_id)
    tier = get_current_user_tier()
    return render_template(
        'dashboard/profile.html',
        student=student,
        tier=tier,
    )


@profile_bp.route('/edit', methods=['POST'])
@login_required
def edit():
    user_id = session['user_id']
    data = request.get_json() or {}
    errors = {}

    first = data.get('first_name', '').strip()
    last = data.get('last_name', '').strip()
    middle = data.get('middle_name', '').strip()
    school = data.get('school', '').strip()
    grade = data.get('grade', '').strip()
    city = data.get('city', '').strip()
    location = data.get('location', '').strip()
    curriculum = data.get('curriculum', '').strip()

    def validate_name(name):
        return bool(name) and len(name) >= 4 and re.fullmatch(r'[A-Za-z]+', name)

    def validate_school(school):
        words = school.strip().split()
        return len(words) >= 2 and all(
            len(w) >= 4 and re.fullmatch(r'[A-Za-z]+', w) for w in words
        )

    def validate_city(city):
        return bool(city) and len(city) >= 5 and re.fullmatch(r'[A-Za-z\s]+', city)

    if not validate_name(first):
        errors['first_name'] = 'First name must be at least 4 letters and contain only letters.'
    if not validate_name(last):
        errors['last_name'] = 'Last name must be at least 4 letters and contain only letters.'
    if middle and not re.fullmatch(r'[A-Za-z]+', middle):
        errors['middle_name'] = 'Middle name must contain only letters.'
    if not validate_school(school):
        errors['school'] = 'School must have at least 2 words, each 4+ letters, only letters.'
    if grade not in ['7aad', '8aad', 'Sare 3aad', 'Sare 4aad']:
        errors['grade'] = 'Invalid grade.'
    if not validate_city(city):
        errors['city'] = 'City must be at least 5 characters and contain only letters and spaces.'
    if location not in ['SO', 'PL', 'SL']:
        errors['location'] = 'Invalid location.'
    if location == 'PL' and curriculum not in ['general', 'science', 'arts']:
        errors['curriculum'] = 'Curriculum is required for Puntland.'
    elif location != 'PL':
        curriculum = None

    if errors:
        return jsonify({'success': False, 'errors': errors}), 400

    updates = {
        'first_name': first,
        'last_name': last,
        'middle_name': middle,
        'school': school,
        'grade': grade,
        'city': city,
        'location': location,
        'curriculum': curriculum,
    }
    for field, value in updates.items():
        execute_with_retry(
            f"UPDATE students SET {field} = ? WHERE id = ?",
            (value, user_id),
            commit=True,
        )
    return jsonify({'success': True, 'message': 'Profile updated successfully.'})


@profile_bp.route('/public-id', methods=['POST'])
@login_required
def public_id():
    """
    Manage the user's public ID.

    Two-tier rules:
      free     → blocked
      premium  → 'regenerate' (random unique) or 'edit' (custom, unique)
    """
    user_id = session['user_id']
    tier = get_current_user_tier()
    data = request.get_json() or {}
    action = (data.get('action') or '').strip().lower()
    requested_id = (data.get('public_id') or '').strip().upper()

    if tier != 'premium':
        return jsonify({
            'error': 'Public ID management requires Premium.'
        }), 403

    new_id = None

    if action == 'regenerate':
        new_id = _generate_unique_public_id()
        if not new_id:
            return jsonify({
                'error': 'Could not generate a unique ID. Please try again.'
            }), 500

    elif action == 'edit':
        if not _PUBLIC_ID_REGEX.fullmatch(requested_id):
            return jsonify({
                'error': 'ID must be exactly 4 uppercase letters or digits.'
            }), 400
        if get_student_by_public_id(requested_id):
            return jsonify({
                'error': 'This ID is already taken.'
            }), 400
        new_id = requested_id

    else:
        return jsonify({'error': 'Invalid action.'}), 400

    execute_with_retry(
        "UPDATE students SET public_id = ? WHERE id = ?",
        (new_id, user_id),
        commit=True,
    )
    session['public_id'] = new_id
    session.modified = True
    return jsonify({
        'success': True,
        'public_id': new_id,
        'message': 'Public ID updated.',
    })