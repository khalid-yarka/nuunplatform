# blueprints/dashboard_bp.py
# Tier-aware home dashboard.
#
# Insights gate (FIX):
#   Previous code gated insights with `analytics_level >= 3`, where
#   analytics_level = get_feature_level('basic_statistics').
#   The seed defines basic_statistics as 2 for both Premium and Pro,
#   so the ">= 3" check never fired for Pro users — they saw the
#   "Upgrade" prompt on their own dashboard.
#
#   The correct feature is `personal_learning_insights`:
#       free     level 0  (no insights)
#       premium  level 1  (basic insights)
#       pro      level 2  (full insights)
#   We gate on `insights_level >= 1` and let the level decide the
#   richness of what we build.

from datetime import datetime, timedelta
from flask import Blueprint, render_template, session, flash, redirect, url_for

from db import (
    get_student_by_id,
    get_user_quiz_history,
    get_user_subject_performance,
    get_user_recent_scores,
    get_total_correct_answers,
    get_distinct_subjects_attempted,
    get_user_active_quiz,
    get_live_quiz_by_id,
    execute_with_retry,
)
from services.tier_service import (
    get_current_user_tier,
    get_analytics_level,
    get_insights_level,
    get_quiz_attempts_remaining,
    get_history_retention_days,
    get_history_max_entries,
)
from utils import get_somali_time

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='')


@dashboard_bp.route('/home')
def home():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    tier = get_current_user_tier()

    # ----- Feature levels -----
    # ----- Feature levels -----
    analytics_level = get_analytics_level(user_id)   # basic stats
    insights_level = get_insights_level(user_id)     # personal_learning_insights

    student = get_student_by_id(user_id)
    if not student:
        session.clear()
        flash('Session expired. Please login again.', 'error')
        return redirect(url_for('auth.login'))

    # ---------- Basic stats (all tiers) ----------
    attempts = get_user_quiz_history(user_id, 50)
    quiz_count = len(attempts)
    total_points = student.get('total_points', 0)

    # ---------- Gamification ----------
    level = (total_points // 10) + 1 if total_points >= 0 else 1
    xp_in_level = total_points % 10
    xp_needed = 10
    xp_percent = int(round((xp_in_level / xp_needed) * 100)) if xp_needed else 0
    xp_to_next = xp_needed - xp_in_level

    # ---------- Performance ----------
    total_correct = get_total_correct_answers(user_id)
    subjects_attempted = get_distinct_subjects_attempted(user_id)

    if attempts:
        total_questions = sum(a.get('total_questions', 10) or 0 for a in attempts)
        success_rate = round((total_correct / total_questions) * 100) if total_questions > 0 else 0
    else:
        success_rate = 0

    # ---------- Subject mastery ----------
    subject_performance = get_user_subject_performance(user_id)

    # ---------- Chart data (tier-limited window) ----------
    if analytics_level >= 3:
        chart_limit = 30
    elif analytics_level >= 2:
        chart_limit = 20
    else:
        chart_limit = 5

    recent_scores = get_user_recent_scores(user_id, chart_limit)
    chart_labels = []
    chart_data = []
    for a in recent_scores:
        try:
            date_str = (a.get('completed_at') or '')[:10]
        except Exception:
            date_str = ''
        chart_labels.append(date_str)
        total_q = a.get('total_questions') or 0
        pct = round((a.get('score', 0) / total_q) * 100) if total_q else 0
        chart_data.append(pct)

    # ---------- Streak ----------
    streak = _compute_streak(user_id)

    # ---------- Live quiz banner ----------
    active_quiz_id = get_user_active_quiz(user_id)
    active_quiz = get_live_quiz_by_id(active_quiz_id) if active_quiz_id else None

    # ---------- Recent activity ----------
    limit = 5 if analytics_level == 1 else 10 if analytics_level == 2 else 20
    recent_activity = []
    for q in attempts[:limit]:
        subject_name = 'Unknown'
        if q.get('subject'):
            subject_name = q['subject'].get('name') or subject_name
        elif q.get('subject_code'):
            subject_name = q['subject_code']

        score = q.get('score', 0)
        total_q = q.get('total_questions') or 10
        pct = round((score / total_q) * 100) if total_q else 0
        recent_activity.append({
            'type': 'quiz',
            'icon': '📝',
            'color': 'green' if pct >= 70 else 'amber' if pct >= 40 else 'red',
            'title': f'Completed a <strong>{subject_name}</strong> quiz',
            'meta': f'Score: {score}/{total_q} ({pct}%)',
            'points': f'+{score} XP',
            'time': (q.get('completed_at') or '')[:16],
        })

    # ---------- Insights (FIX: gate on insights_level, not analytics_level) ----------
    insights = []
    if insights_level >= 1:
        insights = _build_insights(
            subject_performance=subject_performance,
            success_rate=success_rate,
            quiz_count=quiz_count,
            streak=streak,
            insights_level=insights_level,
        )

    # ---------- Tier quotas ----------
    quiz_remaining = get_quiz_attempts_remaining(user_id)
    history_retention = get_history_retention_days(user_id)
    history_max = get_history_max_entries(user_id)

    # ---------- Greeting ----------
    hour = get_somali_time().hour
    if hour < 12:
        greeting = 'Good Morning'
        greeting_icon = '🌅'
    elif hour < 17:
        greeting = 'Good Afternoon'
        greeting_icon = '☀️'
    else:
        greeting = 'Good Evening'
        greeting_icon = '🌙'

    # ---------- Tier upgrade hint ----------
    next_tier = None
    upgrade_hint = None
    if tier == 'free':
        next_tier = 'premium'
        upgrade_hint = 'Unlock analytics, live quiz hosting, and 3× more quiz attempts.'
    elif tier == 'premium':
        next_tier = 'pro'
        upgrade_hint = 'Get unlimited access, premium PDFs, and full live quiz hosting.'

    return render_template(
        'dashboard/home.html',
        student=student,
        greeting=greeting,
        greeting_icon=greeting_icon,
        tier=tier,
        next_tier=next_tier,
        upgrade_hint=upgrade_hint,
        level=level,
        xp_in_level=xp_in_level,
        xp_needed=xp_needed,
        xp_percent=xp_percent,
        xp_to_next=xp_to_next,
        quiz_count=quiz_count,
        total_points=total_points,
        total_correct=total_correct,
        subjects_attempted=subjects_attempted,
        success_rate=success_rate,
        streak=streak,
        recent_activity=recent_activity,
        subject_performance=subject_performance,
        chart_labels=chart_labels,
        chart_data=chart_data,
        active_quiz=active_quiz,
        analytics_level=analytics_level,
        insights_level=insights_level,
        insights=insights,
        quiz_remaining=quiz_remaining,
        history_retention=history_retention,
        history_max=history_max,
    )


@dashboard_bp.route('/profile')
def profile():
    if 'user_id' not in session:
        flash('Please login first.', 'error')
        return redirect(url_for('auth.login'))

    student = get_student_by_id(session['user_id'])
    if not student:
        session.clear()
        flash('Session expired. Please login again.', 'error')
        return redirect(url_for('auth.login'))

    return render_template('dashboard/profile.html', student=student)


# ============================================
# HELPERS
# ============================================

def _compute_streak(user_id: int) -> int:
    """Count consecutive days with at least one quiz attempt, ending today/yesterday."""
    try:
        cursor = execute_with_retry("""
            SELECT DISTINCT substr(completed_at, 1, 10) AS day
            FROM quiz_attempts
            WHERE student_id = ?
            ORDER BY day DESC
            LIMIT 60
        """, (user_id,))
        rows = [r['day'] for r in cursor.fetchall() if r['day']]

        if not rows:
            return 0

        today = get_somali_time().date()
        try:
            days = [datetime.strptime(d, '%Y-%m-%d').date() for d in rows]
        except Exception:
            return 0

        if days[0] not in (today, today - timedelta(days=1)):
            return 0

        streak = 1
        for i in range(1, len(days)):
            if days[i - 1] - days[i] == timedelta(days=1):
                streak += 1
            else:
                break
        return streak
    except Exception:
        return 0


def _build_insights(subject_performance, success_rate, quiz_count,
                    streak, insights_level):
    """
    Build a list of personalized insight dicts.

    insights_level:
        0 → caller does not invoke this function
        1 (premium) → 2–3 basic insights
        2 (pro)     → up to 6 richer insights
    """
    insights = []

    if not subject_performance and quiz_count == 0:
        # Fresh user — a single welcoming hint
        insights.append({
            'icon': '🚀',
            'text': 'Take your first quiz to unlock personalized insights here.',
        })
        return insights

    # ---- Best / worst subject ----
    if subject_performance:
        try:
            best = max(subject_performance, key=lambda x: x.get('avg_score', 0))
            worst = min(subject_performance, key=lambda x: x.get('avg_score', 0))
        except Exception:
            best = worst = None

        if best and best.get('avg_score', 0) >= 60:
            insights.append({
                'icon': '🌟',
                'text': (
                    f"Your strongest subject is "
                    f"<strong>{best['subject_name']}</strong> "
                    f"at {best['avg_score']:.0f}%."
                ),
            })

        if worst and best and worst.get('subject_name') != best.get('subject_name'):
            insights.append({
                'icon': '🎯',
                'text': (
                    f"Focus on <strong>{worst['subject_name']}</strong> "
                    f"({worst.get('avg_score', 0):.0f}%) — it's your lowest so far."
                ),
            })

    # ---- Overall performance message ----
    if success_rate >= 70:
        insights.append({
            'icon': '💪',
            'text': f"Consistency is paying off — you're averaging <strong>{success_rate}%</strong>.",
        })
    elif success_rate > 0:
        insights.append({
            'icon': '📈',
            'text': (
                f"Your average is <strong>{success_rate}%</strong>. "
                f"A little daily practice will lift it fast."
            ),
        })

    # ---- Streak insight ----
    if streak >= 3:
        insights.append({
            'icon': '🔥',
            'text': f"You're on a <strong>{streak}-day streak</strong> — keep it alive!",
        })

    # ---- Level 2+ (Pro) — extra nuance ----
    if insights_level >= 2:
        if quiz_count >= 20:
            insights.append({
                'icon': '📚',
                'text': (
                    f"You've taken <strong>{quiz_count} quizzes</strong> recently "
                    f"— great habit."
                ),
            })

        if subject_performance and len(subject_performance) >= 3:
            try:
                top3 = sorted(subject_performance,
                              key=lambda x: x.get('avg_score', 0),
                              reverse=True)[:3]
                names = ', '.join(s['subject_name'] for s in top3)
                insights.append({
                    'icon': '🏅',
                    'text': f"Your top subjects right now: <strong>{names}</strong>.",
                })
            except Exception:
                pass

    return insights[:6]