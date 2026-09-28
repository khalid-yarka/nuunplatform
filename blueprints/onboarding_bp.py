# ============================================================
# blueprints/onboarding_bp.py
# ============================================================
# First-login onboarding modal.
#
#   GET  /welcome          → show the Free vs Premium comparison
#   POST /welcome/choose   → record the user's choice, grant trial
#                            if requested, redirect
#
# Behaviour:
#   - Only renders when the user is logged in AND
#     students.onboarding_dismissed = 0.
#   - Every choice sets onboarding_dismissed = 1 so the modal
#     never shows again on this account.
#   - 'trial' grants 24h of Premium (configurable via TRIAL_HOURS).
#   - 'free' and 'upgrade' leave the user on Free.
#
# Constants you may want to tune:
# ============================================================

from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, session, redirect, url_for,
    request, jsonify,
)

from db import (
    get_student_by_id,
    execute_with_retry,
)
from utils import ensure_csrf_token, validate_csrf, SOMALI_TIMEZONE


onboarding_bp = Blueprint(
    'onboarding', __name__, url_prefix='/welcome'
)


# ============================================================
# TUNABLE CONSTANTS
# ============================================================
TRIAL_HOURS = 24
DISCOUNT_WINDOW_DAYS = 7
# ============================================================


def _redirect_if_done():
    """
    Return a redirect response if the user has already dismissed
    onboarding, or if they are not logged in. Otherwise None.
    """
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    student = get_student_by_id(session['user_id'])
    if not student:
        session.clear()
        return redirect(url_for('auth.login'))

    try:
        done = int(student.get('onboarding_dismissed') or 0)
    except (TypeError, ValueError):
        done = 0

    if done:
        return redirect(url_for('dashboard.home'))
    return None


def _mark_done(user_id: int) -> None:
    try:
        execute_with_retry(
            "UPDATE students SET onboarding_dismissed = 1 WHERE id = ?",
            (user_id,), commit=True,
        )
        session['onboarding_dismissed'] = 1
        session.modified = True
    except Exception:
        # Non-fatal: the modal may briefly reappear on next login.
        pass


def _grant_trial(user_id: int) -> None:
    """
    Grant 24h of Premium and flag the session for refresh.
    """
    try:
        expires = (datetime.now(SOMALI_TIMEZONE)
                   + timedelta(hours=TRIAL_HOURS))
    except Exception:
        expires = datetime.utcnow() + timedelta(hours=TRIAL_HOURS)

    try:
        execute_with_retry(
            "UPDATE students "
            "SET tier = 'premium', tier_expires_at = ?, "
            "    tier_updated_at = datetime('now','localtime') "
            "WHERE id = ?",
            (expires.isoformat(), user_id), commit=True,
        )
        session['tier'] = 'premium'
        session['tier_expires_at'] = expires.isoformat()
        session.modified = True

        try:
            from services.entitlement_service import refresh_user
            refresh_user(user_id)
        except Exception:
            pass

        try:
            from db import create_notification
            create_notification(
                user_id=user_id,
                type='trial_started',
                title='🎁 Trial started',
                body=(
                    f"You have {TRIAL_HOURS} hours of Premium. "
                    f"Enjoy everything — no charge, no card."
                ),
                link='/home',
                icon='🎁',
            )
        except Exception:
            pass
    except Exception:
        pass


# ============================================================
# ROUTES
# ============================================================

@onboarding_bp.route('/', methods=['GET'], endpoint='welcome')
def welcome():
    guard = _redirect_if_done()
    if guard:
        return guard

    ensure_csrf_token()
    student = get_student_by_id(session['user_id'])

    return render_template(
        'onboarding/welcome.html',
        student=student,
        trial_hours=TRIAL_HOURS,
    )


@onboarding_bp.route('/choose', methods=['POST'], endpoint='choose')
def choose():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401

    # Accept JSON body or form field
    data = request.get_json(silent=True) or {}
    choice = (data.get('choice') or request.form.get('choice') or '').strip().lower()

    if choice not in ('free', 'upgrade', 'trial'):
        return jsonify({'success': False, 'error': 'Invalid choice'}), 400

    # CSRF — the meta tag sends X-CSRF-Token
    if not validate_csrf():
        return jsonify({'success': False, 'error': 'Invalid session'}), 403

    user_id = session['user_id']

    if choice == 'trial':
        _grant_trial(user_id)
        _mark_done(user_id)
        return jsonify({
            'success': True,
            'redirect': url_for('dashboard.home'),
        })

    if choice == 'upgrade':
        # Dismiss onboarding and hand off to /home with a flag
        # that tells the page to open the upgrade sheet.
        _mark_done(user_id)
        return jsonify({
            'success': True,
            'redirect': url_for('dashboard.home') + '?show_upgrade=1',
        })

    # choice == 'free'
    _mark_done(user_id)
    return jsonify({
        'success': True,
        'redirect': url_for('dashboard.home'),
    })