# blueprints/profile_bp.py
from flask import Blueprint, render_template, request, session, jsonify, redirect, url_for, flash
from functools import wraps
from db import get_student_by_id, execute_with_retry, get_student_by_public_id
from services.tier_service import get_current_user_tier
import re
import secrets
import string

profile_bp = Blueprint('profile', __name__, url_prefix='/profile')

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first.', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated

@profile_bp.route('/')
@login_required
def index():
    user_id = session['user_id']
    student = get_student_by_id(user_id)
    tier = get_current_user_tier()
    return render_template('dashboard/profile.html', student=student, tier=tier)

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
        return len(words) >= 2 and all(len(w) >= 4 and re.fullmatch(r'[A-Za-z]+', w) for w in words)
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
        'curriculum': curriculum
    }
    for field, value in updates.items():
        execute_with_retry(
            f"UPDATE students SET {field} = ? WHERE id = ?",
            (value, user_id),
            commit=True
        )
    return jsonify({'success': True, 'message': 'Profile updated successfully.'})

@profile_bp.route('/public-id', methods=['POST'])
@login_required
def public_id():
    user_id = session['user_id']
    tier = get_current_user_tier()
    data = request.get_json() or {}
    action = data.get('action')
    new_id = data.get('public_id', '').strip().upper()

    if tier == 'danbe':
        return jsonify({'error': 'Public ID management requires Dhexe or Hore.'}), 403

    if tier == 'dhexe':
        if action != 'regenerate':
            return jsonify({'error': 'Dhexe can only regenerate a random ID.'}), 400
        attempts = 0
        while attempts < 10:
            candidate = ''.join(secrets.choice(string.ascii_uppercase + '123456789') for _ in range(4))
            if not get_student_by_public_id(candidate):
                new_id = candidate
                break
            attempts += 1
        else:
            return jsonify({'error': 'Could not generate unique ID.'}), 500

    elif tier == 'hore':
        if action == 'regenerate':
            attempts = 0
            while attempts < 10:
                candidate = ''.join(secrets.choice(string.ascii_uppercase + '123456789') for _ in range(4))
                if not get_student_by_public_id(candidate):
                    new_id = candidate
                    break
                attempts += 1
            else:
                return jsonify({'error': 'Could not generate unique ID.'}), 500
        elif action == 'edit':
            if not re.fullmatch(r'[A-Z0-9]{4}', new_id):
                return jsonify({'error': 'ID must be exactly 4 uppercase letters/digits.'}), 400
            if get_student_by_public_id(new_id):
                return jsonify({'error': 'This ID is already taken.'}), 400
        else:
            return jsonify({'error': 'Invalid action.'}), 400
    else:
        return jsonify({'error': 'Invalid tier.'}), 403

    execute_with_retry(
        "UPDATE students SET public_id = ? WHERE id = ?",
        (new_id, user_id),
        commit=True
    )
    session['public_id'] = new_id
    return jsonify({'success': True, 'public_id': new_id, 'message': 'Public ID updated.'})