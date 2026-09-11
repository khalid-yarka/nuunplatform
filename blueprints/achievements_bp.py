# blueprints/achievements_bp.py
# Achievements and badges.

from flask import Blueprint, render_template, session, abort
from functools import wraps
from services.achievement_service import get_visible_achievements, get_showcase_badges, get_user_achievement_ids, get_all_achievements
from services.tier_service import get_achievement_history_level, get_badge_showcase_level

achievements_bp = Blueprint('achievements', __name__, url_prefix='/achievements')

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            abort(401)
        return f(*args, **kwargs)
    return decorated

@achievements_bp.route('/')
@login_required
def index():
    user_id = session['user_id']
    achievements = get_visible_achievements(user_id)
    showcase = get_showcase_badges(user_id)
    history_level = get_achievement_history_level(user_id)
    showcase_level = get_badge_showcase_level(user_id)
    return render_template('dashboard/achievements.html',
                           achievements=achievements,
                           showcase=showcase,
                           history_level=history_level,
                           showcase_level=showcase_level)