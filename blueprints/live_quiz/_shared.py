# ============================================================
# blueprints/live_quiz/_shared.py
# ============================================================
# Shared state for the live-quiz package. Every cross-module
# helper, constant, and module-level lock lives here so it is
# created exactly ONCE. Route modules import from this file —
# never re-declare — because duplicated locks would silently
# break the concurrency guarantees the original module relied on.
#
# The four module-level locks that MUST be singletons:
#   _finalize_lock / _finalize_in_progress  (finalize.py)
#   _chat_rate_lock / _chat_rate            (chat.py)
#   _ping_lock / _ping_last_sent            (chat.py)
# ============================================================

import json
import random
import threading
import logging
import sqlite3

from db import (
    get_db,
    execute_with_retry,
    get_student_by_id,
    get_live_quiz_by_id,
    get_live_quiz_participant,
    rejoin_live_quiz as db_rejoin_live_quiz,
    is_admin,
)

from config import Config
from utils import get_somali_time_db
from subjects_config import get_subject
from question_utils import (
    UI_GRADES, DEFAULT_GRADE, normalize_grade,
)
from live_quiz_state import get_live_quiz_state_manager
from cache import get_cache_manager, InvalidationHelper

logger = logging.getLogger(__name__)


# ============================================================
# CONSTANTS
# ============================================================
MAX_PARTICIPANTS = Config.LIVE_QUIZ_MAX_PARTICIPANTS
TIME_PER_QUESTION = Config.LIVE_QUIZ_TIME_PER_QUESTION
RATING_TIME = Config.RATING_TIME

CACHE_TTL = getattr(Config, 'CACHE_TTL', {}).get('quiz', {})
QUIZ_STATE_TTL = CACHE_TTL.get('state', 60)


# ============================================================
# FINALIZE LOCK — owned here, imported by finalize.py
# ============================================================
# Prevents two concurrent requests from running the DB loop and
# the notification loop twice for the same quiz.
#
# Keys are always ints. Routes declare <int:quiz_id>, and
# finalize_live_quiz() casts defensively, so the dict cannot hold
# both 42 and '42'.
_finalize_lock = threading.Lock()
_finalize_in_progress = {}


# ============================================================
# CORE HELPERS
# ============================================================

def get_state_manager():
    return get_live_quiz_state_manager()


def invalidate_quiz_cache(quiz_id):
    try:
        InvalidationHelper.invalidate_quiz(quiz_id)
        cache = get_cache_manager()
        cache.invalidate_pattern(f"quiz:*:{quiz_id}:*")
    except Exception:
        pass


# ============================================================
# GRADE HELPERS
# ============================================================

def profile_grade(user_id):
    user = get_student_by_id(user_id) or {}
    g = (user.get('grade') or '').strip().upper()
    return g if g in UI_GRADES else UI_GRADES[0]


def grade_choices():
    from question_utils import grade_label
    return [{'code': g, 'label': grade_label(g)} for g in UI_GRADES]


def is_grade_locked_for_viewer(quiz_row, user_id, user_tier):
    if user_tier == 'premium':
        return False
    quiz_grade = normalize_grade(quiz_row.get('grade') if quiz_row else None)
    if quiz_grade not in UI_GRADES:
        quiz_grade = UI_GRADES[0]
    return quiz_grade != profile_grade(user_id)


# ============================================================
# ACTIVE QUIZ LOOKUP
# ============================================================

def enrich_active_quiz(active_quiz, user_id):
    if not active_quiz:
        return active_quiz
    active_quiz['is_creator'] = (active_quiz.get('creator_id') == user_id)
    subj = get_subject(active_quiz.get('subject_code'))
    active_quiz['subject_name'] = subj['name'] if subj else active_quiz.get('subject_code')
    active_quiz['subject_icon'] = subj.get('icon', '📚') if subj else '📚'
    if not active_quiz.get('grade'):
        active_quiz['grade'] = DEFAULT_GRADE
    return active_quiz


def get_active_quiz_for_user(user_id):
    from db import get_user_active_quiz
    active_id = get_user_active_quiz(user_id)
    if not active_id:
        return None
    quiz = get_live_quiz_by_id(active_id)
    if not quiz:
        return None
    return enrich_active_quiz(quiz, user_id)


# ============================================================
# ATOMIC ADD PARTICIPANT
# ============================================================
# Fixes the check-then-insert race: the count and the INSERT
# happen inside a single BEGIN IMMEDIATE transaction.
#
# Returns (True, 'ok') on success, or (False, reason) where reason
# is one of: 'full', 'already_joined', 'error'.

def _atomic_add_participant(quiz_id, student_id, question_ids, max_participants):
    shuffled = question_ids[:]
    random.shuffle(shuffled)
    answers_json = json.dumps({'__shuffled_ids': shuffled})
    ratings_json = json.dumps({})
    now_iso = get_somali_time_db()

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            row = cursor.execute(
                "SELECT COUNT(*) AS c FROM live_quiz_participants WHERE quiz_id = ?",
                (quiz_id,)
            ).fetchone()
            current = row['c'] if row else 0
            if current >= max_participants:
                conn.rollback()
                return False, 'full'

            cursor.execute("""
                INSERT INTO live_quiz_participants (
                    quiz_id, student_id, score, current_question_index,
                    correct_count, wrong_count, skipped_count, answers, ratings,
                    ranking, status, joined_at
                ) VALUES (?, ?, 0, 0, 0, 0, 0, ?, ?, NULL, 'active', ?)
            """, (quiz_id, student_id, answers_json, ratings_json, now_iso))
            conn.commit()
            return True, 'ok'
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    except sqlite3.IntegrityError as e:
        msg = str(e).lower()
        if 'unique' in msg:
            return False, 'already_joined'
        logger.warning(f"_atomic_add_participant integrity: {e}")
        return False, 'error'
    except Exception as e:
        logger.error(f"_atomic_add_participant failed: {e}", exc_info=True)
        return False, 'error'


# ============================================================
# REJOIN IF LEFT
# ============================================================
# Flip a 'left' participant back to active in both the DB and
# memory. Returns True only when BOTH sides reflect the active
# status. A partial failure returns False so the caller does not
# report success while the room still shows them as left.

def _rejoin_if_left(quiz_id, user_id):
    try:
        participant = get_live_quiz_participant(quiz_id, user_id)
    except Exception as e:
        logger.warning(f"_rejoin_if_left: DB read failed for quiz {quiz_id} user {user_id}: {e}")
        return False

    if not participant:
        return False

    current_status = participant.get('status')
    if current_status != 'left':
        return True

    try:
        ok = db_rejoin_live_quiz(quiz_id, user_id)
    except Exception as e:
        logger.error(f"_rejoin_if_left: db_rejoin_live_quiz raised: {e}", exc_info=True)
        return False

    if not ok:
        logger.info(f"_rejoin_if_left: db_rejoin returned False for quiz {quiz_id} user {user_id}")
        return False

    try:
        manager = get_state_manager()
        if not manager.ensure_quiz_in_memory(quiz_id):
            logger.error(f"_rejoin_if_left: quiz {quiz_id} not in memory and could not be recovered")
            return False

        quiz_state = manager.get_quiz(quiz_id)
        if not quiz_state:
            logger.error(f"_rejoin_if_left: quiz {quiz_id} has no in-memory state")
            return False

        user = get_student_by_id(user_id) or {}
        name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'Participant'
        public_id = user.get('public_id', '----')

        restored = quiz_state.restore_participant(user_id, name=name, public_id=public_id)
        if not restored:
            logger.error("_rejoin_if_left: restore_participant returned False")
            return False

        manager.enqueue_event({
            'quiz_id': quiz_id,
            'user_id': user_id,
            'event_type': 'JOIN',
            'payload': json.dumps({'name': name}),
        })

        invalidate_quiz_cache(quiz_id)
        logger.info(f"_rejoin_if_left: auto-rejoined user {user_id} into quiz {quiz_id}")
        return True
    except Exception as e:
        logger.error(f"_rejoin_if_left: memory update failed: {e}", exc_info=True)
        return False


# ============================================================
# JOIN NOTIFICATION (best-effort, non-blocking)
# ============================================================

def notify_creator_of_join(creator_id, joiner_name, quiz_title, quiz_id):
    import threading as _t
    def _work():
        try:
            from services.notification_service import send_notification
            send_notification(
                user_id=creator_id,
                notification_type='participant_joined',
                title='👋 Ka-qaybgal cusub!',
                body=f'{joiner_name} wuxuu ku biiray "{quiz_title}"',
                link=f'/live-quiz/waiting-room/{quiz_id}',
                icon='👤',
                force=True,
            )
        except Exception as e:
            logger.warning(f"participant_joined notification failed: {e}")
    try:
        _t.Thread(target=_work, daemon=True).start()
    except Exception as e:
        logger.warning(f"could not spawn join notification: {e}")


# ============================================================
# ADD PARTICIPANT TO STATE (shared by join handlers)
# ============================================================
# Encapsulates the three lines that every join handler repeats:
# ensure_quiz_in_memory, get_quiz, add_participant, enqueue_event.
#
# Returns True when the participant is in memory (newly added or
# already there). Returns False when the state manager could not
# be loaded — the caller should surface a real error, not a
# redirect into a state that does not know the user.

def ensure_participant_in_state(quiz_id, user_id):
    manager = get_state_manager()

    if not manager.ensure_quiz_in_memory(quiz_id):
        logger.error(f"ensure_participant_in_state: quiz {quiz_id} could not be loaded")
        return False

    quiz_state = manager.get_quiz(quiz_id)
    if quiz_state is None:
        logger.error(f"ensure_participant_in_state: quiz {quiz_id} has no in-memory state")
        return False

    user = get_student_by_id(user_id)
    name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'Participant'
    public_id = user.get('public_id', '----') if user else '----'

    added = quiz_state.add_participant(user_id, name, public_id)
    if added:
        manager.enqueue_event({
            'quiz_id': quiz_id,
            'user_id': user_id,
            'event_type': 'JOIN',
            'payload': json.dumps({'name': name}),
        })
    return True