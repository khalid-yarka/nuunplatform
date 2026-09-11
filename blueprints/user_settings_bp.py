# blueprints/user_settings_bp.py
from flask import Blueprint, render_template, request, session, flash, redirect, url_for, jsonify
from functools import wraps
from db import get_student_by_id, is_admin, execute_with_retry, get_student_by_public_id, get_user_subject_list
from user_settings import get_user_settings, update_user_settings, get_user_setting
from services.tier_service import get_feature_level, get_current_user_tier, can_create_live_quiz, has_feature
from activity_logger import log_activity
import re
import secrets
import string

user_settings_bp = Blueprint('user_settings', __name__, url_prefix='/settings')

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Please login first.'}), 401
        return f(*args, **kwargs)
    return decorated

# ---------- Helper validation (same as registration) ----------
def validate_name(name):
    return bool(name) and len(name) >= 4 and re.fullmatch(r'[A-Za-z]+', name)

def validate_school(school):
    words = school.strip().split()
    return len(words) >= 2 and all(len(w) >= 4 and re.fullmatch(r'[A-Za-z]+', w) for w in words)

def validate_city(city):
    return bool(city) and len(city) >= 5 and re.fullmatch(r'[A-Za-z\s]+', city)

def validate_public_id(pid):
    return bool(pid) and len(pid) == 4 and re.fullmatch(r'[A-Z0-9]{4}', pid)

# ---------- Page ----------
@user_settings_bp.route('/')
@login_required
def index():
    user_id = session['user_id']
    student = get_student_by_id(user_id)
    settings = get_user_settings(user_id)
    tier = get_current_user_tier()
    profile_level = get_feature_level("profile_customization", user_id)
    notif_level = get_feature_level("notification_settings", user_id)
    can_create = can_create_live_quiz()
    # Get user subjects for dropdown
    user_subjects = get_user_subject_list(user_id)  # returns list of dicts with code, name, icon
    return render_template('dashboard/user_settings.html',
                           student=student,
                           settings=settings,
                           tier=tier,
                           profile_level=profile_level,
                           notif_level=notif_level,
                           can_create=can_create,
                           user_subjects=user_subjects)

# ---------- Profile ----------
@user_settings_bp.route('/update-profile', methods=['POST'])
@login_required
def update_profile():
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

    # Validate first name
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

    # Update student table
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
    log_activity('user.profile_update', f"User {user_id} updated profile", 'info', user_id=user_id)
    return jsonify({'success': True, 'message': 'Profile updated successfully.'})

# ---------- Public ID ----------
@user_settings_bp.route('/update-public-id', methods=['POST'])
@login_required
def update_public_id():
    user_id = session['user_id']
    tier = get_current_user_tier()
    data = request.get_json() or {}
    action = data.get('action')  # 'regenerate' or 'edit'
    new_id = data.get('public_id', '').strip().upper()

    if tier == 'danbe':
        return jsonify({'error': 'Public ID management is not available for your tier. Upgrade to Dhexe or Hore.'}), 403

    if tier == 'dhexe':
        if action != 'regenerate':
            return jsonify({'error': 'Dhexe tier can only regenerate a new random ID.'}), 400
        # Generate a new unique public ID
        attempts = 0
        while attempts < 10:
            candidate = ''.join(secrets.choice(string.ascii_uppercase + '123456789') for _ in range(4))
            if not get_student_by_public_id(candidate):
                new_id = candidate
                break
            attempts += 1
        else:
            return jsonify({'error': 'Could not generate a unique public ID. Please try again.'}), 500

    elif tier == 'hore':
        if action == 'regenerate':
            # Hore can also regenerate (same as dhexe)
            attempts = 0
            while attempts < 10:
                candidate = ''.join(secrets.choice(string.ascii_uppercase + '123456789') for _ in range(4))
                if not get_student_by_public_id(candidate):
                    new_id = candidate
                    break
                attempts += 1
            else:
                return jsonify({'error': 'Could not generate a unique public ID. Please try again.'}), 500
        elif action == 'edit':
            if not validate_public_id(new_id):
                return jsonify({'error': 'Public ID must be exactly 4 characters: uppercase letters and digits.'}), 400
            if get_student_by_public_id(new_id):
                return jsonify({'error': 'This public ID is already taken. Please choose another.'}), 400
        else:
            return jsonify({'error': 'Invalid action.'}), 400
    else:
        return jsonify({'error': 'Invalid tier.'}), 403

    # Update the user's public_id
    execute_with_retry(
        "UPDATE students SET public_id = ? WHERE id = ?",
        (new_id, user_id),
        commit=True
    )
    session['public_id'] = new_id  # update session
    log_activity('user.public_id_update', f"User {user_id} updated public ID to {new_id}", 'info', user_id=user_id)
    return jsonify({'success': True, 'public_id': new_id, 'message': 'Public ID updated successfully.'})

# ---------- Appearance ----------
@user_settings_bp.route('/update-appearance', methods=['POST'])
@login_required
def update_appearance():
    user_id = session['user_id']
    data = request.get_json() or {}
    theme = data.get('theme')
    if theme not in ['light', 'dark', 'system']:
        return jsonify({'error': 'Invalid theme.'}), 400
    accent = data.get('accent', 'red')
    if accent not in ['red', 'blue', 'green', 'purple', 'orange']:
        return jsonify({'error': 'Invalid accent colour.'}), 400
    font_size = data.get('font_size', 'medium')
    if font_size not in ['small', 'medium', 'large']:
        return jsonify({'error': 'Invalid font size.'}), 400
    compact = 1 if data.get('compact_mode') else 0

    update_user_settings(user_id, {
        'theme': theme,
        'accent': accent,
        'font_size': font_size,
        'compact_mode': compact
    })
    log_activity('user.theme_update', f"User {user_id} changed appearance", 'info', user_id=user_id)
    return jsonify({'success': True, 'message': 'Appearance updated!'})

# ---------- Quiz Preferences ----------
@user_settings_bp.route('/update-quiz-preferences', methods=['POST'])
@login_required
def update_quiz_preferences():
    user_id = session['user_id']
    data = request.get_json() or {}

    # Validate
    try:
        q_count = int(data.get('default_question_count', 10))
        if q_count not in [5, 10, 15, 20, 25, 30]:
            return jsonify({'error': 'Invalid question count.'}), 400
    except ValueError:
        return jsonify({'error': 'Invalid question count.'}), 400

    diff = int(data.get('default_difficulty', 1))
    if diff not in range(1, 6):
        return jsonify({'error': 'Invalid difficulty.'}), 400

    subject = data.get('default_subject', '').strip()
    if subject:
        from subjects_config import get_subject
        if not get_subject(subject):
            return jsonify({'error': 'Invalid subject.'}), 400

    show_correct = 1 if data.get('show_correct_immediately') in [True, 'true', 1] else 0
    skip_rating = 1 if data.get('skip_rating_after_quiz') in [True, 'true', 1] else 0

    # Auto-advance only if tier allows
    auto_skip = 1 if data.get('auto_skip_enabled') in [True, 'true', 1] else 0
    if auto_skip and not has_feature("auto_skip", user_id):
        return jsonify({'error': 'Auto‑advance is not available for your tier.'}), 403

    updates = {
        'default_question_count': q_count,
        'default_difficulty': diff,
        'default_subject': subject,
        'show_correct_immediately': show_correct,
        'skip_rating_after_quiz': skip_rating,
        'auto_skip_enabled': auto_skip
    }
    update_user_settings(user_id, updates)
    log_activity('user.quiz_preferences_update', f"User {user_id} updated quiz preferences", 'info', user_id=user_id)
    return jsonify({'success': True, 'message': 'Quiz preferences saved!'})

# ---------- Notifications ----------
@user_settings_bp.route('/update-notifications', methods=['POST'])
@login_required
def update_notifications():
    user_id = session['user_id']
    data = request.get_json() or {}

    base_keys = [
        'notify_quiz_complete',
        'notify_live_quiz_start',
        'notify_live_quiz_result',
        'notify_admin_announcement',
        'notify_participant_joined',
        'notify_new_pdf'
    ]
    expanded_keys = [
        'notify_daily_digest',
        'notify_achievement_unlock',
        'notify_live_quiz_reminder'
    ]
    advanced_keys = [
        'notify_weekly_summary'
    ]

    notif_level = get_feature_level("notification_settings", user_id)
    allowed_keys = base_keys.copy()
    if notif_level >= 2:
        allowed_keys.extend(expanded_keys)
    if notif_level >= 3:
        allowed_keys.extend(advanced_keys)

    updates = {}
    for key in allowed_keys:
        if key in data:
            updates[key] = 1 if data[key] in [True, 'true', 1] else 0

    if updates:
        update_user_settings(user_id, updates)
        log_activity('user.notification_preferences_update', f"User {user_id} updated notification preferences", 'info', user_id=user_id)
    return jsonify({'success': True, 'message': 'Notification preferences saved!'})

# ---------- Privacy ----------
@user_settings_bp.route('/update-privacy', methods=['POST'])
@login_required
def update_privacy():
    user_id = session['user_id']
    data = request.get_json() or {}
    updates = {}
    for key in ['show_on_leaderboard', 'show_public_id']:
        if key in data:
            updates[key] = 1 if data[key] in [True, 'true', 1] else 0
    if updates:
        update_user_settings(user_id, updates)
        log_activity('user.privacy_update', f"User {user_id} updated privacy settings", 'info', user_id=user_id)
    return jsonify({'success': True, 'message': 'Privacy settings saved!'})

# ---------- Password ----------
@user_settings_bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    user_id = session['user_id']
    data = request.get_json() or {}
    current = data.get('current_password', '')
    new = data.get('new_password', '')
    confirm = data.get('confirm_password', '')

    if not current or not new or not confirm:
        return jsonify({'error': 'All fields are required.'}), 400
    if new != confirm:
        return jsonify({'error': 'New passwords do not match.'}), 400
    if len(new) < 8:
        return jsonify({'error': 'New password must be at least 8 characters.'}), 400

    student = get_student_by_id(user_id)
    if not student or student['password'] != current:
        return jsonify({'error': 'Current password is incorrect.'}), 400

    execute_with_retry(
        "UPDATE students SET password = ? WHERE id = ?",
        (new, user_id),
        commit=True
    )
    log_activity('user.password_change', f"User {user_id} changed password", 'warning', user_id=user_id)
    return jsonify({'success': True, 'message': 'Password changed. Please log in again.'})