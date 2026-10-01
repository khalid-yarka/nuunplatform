# ============================================================
# blueprints/live_quiz/finalize.py
# ============================================================
# Quiz finalization and scheduled auto-start.
#
# The finalize lock is imported from _shared — never re-declared
# here — so concurrent calls serialize on the same object.

import json
import logging
from datetime import datetime, timezone

from db import (
    get_live_quiz_by_id,
    get_live_quiz_participant,
    update_live_quiz,
    update_live_quiz_participant,
    execute_with_retry,
)
from utils import get_somali_time_db
from question_utils import DEFAULT_GRADE
from services.notification_service import send_notification
from history_logger import add_history_entry

from ._shared import (
    get_state_manager,
    invalidate_quiz_cache,
    _finalize_lock,
    _finalize_in_progress,
)

logger = logging.getLogger(__name__)


# ============================================================
# FINALIZE
# ============================================================
# Concurrency:
#   - _finalize_lock + _finalize_in_progress prevents two
#     concurrent requests from both running the DB loop.
#   - QuizState.finalize() claims the in-memory finalized flag
#     under the state lock, so a second caller cannot re-run.
#   - update_live_quiz(status='finished') is the FIRST DB write,
#     so even if the participant loop later fails, the DB
#     reflects the terminal status.

def finalize_live_quiz(quiz_id):
    try:
        quiz_id = int(quiz_id)
    except (TypeError, ValueError):
        return {'error': 'Invalid quiz_id'}

    with _finalize_lock:
        if _finalize_in_progress.get(quiz_id):
            return {'error': 'Finalization already in progress'}
        _finalize_in_progress[quiz_id] = True

    try:
        manager = get_state_manager()
        quiz_state = manager.get_quiz(quiz_id)
        if not quiz_state:
            return {'error': 'Quiz not in memory'}

        db_quiz = get_live_quiz_by_id(quiz_id)
        if db_quiz and db_quiz.get('status') == 'finished':
            return {'success': True, 'already_finalized': True}

        final_data = quiz_state.finalize()
        if 'error' in final_data:
            return final_data

        try:
            manager._force_final_checkpoint(quiz_state)
        except Exception as e:
            logger.warning(f"Forced final checkpoint failed for {quiz_id}: {e}")

        try:
            update_live_quiz(quiz_id, {
                'status': 'finished',
                'ended_at': final_data['ended_at'],
            })

            for pdata in final_data['participants']:
                db_p = get_live_quiz_participant(quiz_id, pdata['user_id'])
                if db_p:
                    update_live_quiz_participant(db_p['id'], {
                        'score': pdata['score'],
                        'correct_count': pdata['correct_count'],
                        'wrong_count': pdata['wrong_count'],
                        'skipped_count': pdata['skipped_count'],
                        'answers': pdata['answers'],
                        'ratings': pdata['ratings'],
                        'ranking': pdata['rank'],
                        'status': pdata['status'],
                    })

                try:
                    from services.interaction_service import flush_quiz_reactions
                    flush_quiz_reactions(
                        pdata['user_id'],
                        pdata.get('likes') or [],
                        pdata.get('saves') or [],
                    )
                except Exception as e:
                    logger.error(
                        f"flush_quiz_reactions failed for user {pdata['user_id']} "
                        f"in quiz {quiz_id}: {e}"
                    )

                add_history_entry(
                    user_id=pdata['user_id'],
                    entry_type='live_quiz',
                    action='completed',
                    metadata={
                        'subject': quiz_state.metadata.get('subject_code', 'Unknown'),
                        'grade': quiz_state.metadata.get('grade', DEFAULT_GRADE),
                        'score': pdata['score'],
                        'rank': pdata.get('rank'),
                        'total_questions': len(quiz_state.question_ids),
                    },
                )

            manager.enqueue_event({
                'quiz_id': quiz_id,
                'event_type': 'COMPLETE',
                'payload': json.dumps(final_data),
            })

            participants = quiz_state.get_all_participants()
            for p in participants:
                send_notification(
                    user_id=p['student_id'],
                    notification_type='live_quiz_result',
                    title='🏆 Quiz Complete!',
                    body=(
                        f'"{quiz_state.metadata.get("title", "Quiz")}" finished. '
                        f'Your rank: #{p.get("rank", "N/A")}, '
                        f'Score: {p.get("score", 0)}'
                    ),
                    link=f'/live-quiz/results/{quiz_id}',
                    icon='🏅',
                )

            invalidate_quiz_cache(quiz_id)
            logger.info(f"Finalized quiz {quiz_id}")
            return {'success': True, 'final_data': final_data}
        except Exception as e:
            logger.error(f"Finalization DB loop failed for {quiz_id}: {e}", exc_info=True)
            return {'error': str(e)}
    finally:
        with _finalize_lock:
            _finalize_in_progress.pop(quiz_id, None)


# ============================================================
# SCHEDULED AUTO-START
# ============================================================
# Runs inside every /quiz-state poll. No background threads — on
# the free PythonAnywhere tier, spinning up daemons is a CPU-quota
# risk. Polls fire every 3s per participant, so this is free.

def _maybe_auto_start_scheduled(quiz):
    quiz_id = quiz['id']
    sched = quiz.get('scheduled_start')
    if not sched:
        return

    try:
        sched_dt = datetime.fromisoformat(sched.replace('Z', '+00:00'))
    except Exception:
        return

    now_utc = datetime.now(timezone.utc)
    if sched_dt > now_utc:
        return

    try:
        row = execute_with_retry(
            "SELECT COUNT(*) AS c FROM live_quiz_participants "
            "WHERE quiz_id = ? AND status != 'left'",
            (quiz_id,),
        ).fetchone()
        active_count = int(row['c']) if row else 0
    except Exception as e:
        logger.warning(f"auto-start: count failed for {quiz_id}: {e}")
        return

    if active_count >= 2:
        now_str = get_somali_time_db()
        try:
            cur = execute_with_retry("""
                UPDATE live_quizzes
                SET status = 'active',
                    started_at = ?,
                    scheduled_start = NULL
                WHERE id = ? AND status = 'scheduled'
            """, (now_str, quiz_id), commit=True)
        except Exception as e:
            logger.warning(f"auto-start: UPDATE failed for {quiz_id}: {e}")
            return

        if not cur or cur.rowcount == 0:
            return

        try:
            manager = get_state_manager()
            if manager.ensure_quiz_in_memory(quiz_id):
                qs = manager.get_quiz(quiz_id)
                if qs and qs.status == 'scheduled':
                    qs.start()
                    manager.enqueue_event({
                        'quiz_id': quiz_id,
                        'event_type': 'START',
                        'payload': json.dumps({}),
                    })
        except Exception as e:
            logger.warning(f"auto-start: memory sync failed for {quiz_id}: {e}")

        try:
            execute_with_retry("""
                INSERT INTO notifications
                    (user_id, type, title, body, link, icon, is_read, created_at)
                SELECT lqp.student_id, ?, ?, ?, ?, ?, 0, ?
                FROM live_quiz_participants lqp
                WHERE lqp.quiz_id = ? AND lqp.status != 'left'
            """, (
                'live_quiz_start',
                '🚀 Tartanku wuu bilaabmay!',
                f'"{quiz.get("title") or "Live Quiz"}" wuu bilaabmay. Hadda ciyaar!',
                f'/live-quiz/play/{quiz_id}',
                '⚡',
                get_somali_time_db(),
                quiz_id,
            ), commit=True)
        except Exception as e:
            logger.warning(f"auto-start: notify failed for {quiz_id}: {e}")

        logger.info(f"Auto-started scheduled quiz {quiz_id} ({active_count} participants)")

    else:
        try:
            cur = execute_with_retry("""
                UPDATE live_quizzes
                SET status = 'waiting', scheduled_start = NULL
                WHERE id = ? AND status = 'scheduled'
            """, (quiz_id,), commit=True)
            if cur and cur.rowcount > 0:
                logger.info(
                    f"Scheduled quiz {quiz_id} flipped to manual "
                    f"({active_count} participant(s) at scheduled time)"
                )
        except Exception as e:
            logger.warning(f"auto-start: flip-to-waiting failed for {quiz_id}: {e}")