# live_quiz_state.py
"""
Redis-free Live Quiz State Manager for NuunPlatform.
Designed for single-worker PythonAnywhere deployment.
All state is held in memory; SQLite used for checkpoints and events.

Idempotency contract:
  ── submit_answer: if the question was already answered (by this user,
     for this question), the recorded result is returned unchanged.
     A second submit for the same question is a no-op that returns the
     same payload as the first. If the recorded answer differs from the
     new submission, the RECORDED answer is returned (server is the
     source of truth).
  ── skip_question: if the question was already skipped, returns
     (True, 'already_skipped') — not an error.
  ── advance_question: if the participant has already moved past the
     given question, returns (True, 'already_advanced'). Advancing an
     already-advanced question is a successful no-op.

Convergence contract:
  ── Any endpoint that would return an error for a benign race instead
     returns the current authoritative state, so the client can render
     it and move on.

Membership contract (added):
  ── The database is authoritative for quiz membership. A checkpoint
     may lag by up to one flush interval, so after restoring from a
     checkpoint the state manager MUST merge every participant present
     in `live_quiz_participants` into the in-memory QuizState.
  ── A JOIN event is idempotent and creating: if the participant is
     not already in memory, the event synthesises a ParticipantState.
     This closes the window where a checkpoint taken before the join
     would otherwise discard the join event on replay.
"""

import json
import threading
import time
import logging
import queue
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

from db import execute_with_retry, get_db, now, to_json, from_json
from utils import get_somali_time_db

logger = logging.getLogger(__name__)


# ============================================
# Data Classes
# ============================================

@dataclass
class ParticipantState:
    user_id: int
    name: str
    public_id: str
    score: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    skipped_count: int = 0
    current_question_index: int = 0
    answers: Dict[str, Dict] = field(default_factory=dict)
    ratings: Dict[str, str] = field(default_factory=dict)
    status: str = 'active'
    is_ready: bool = False
    rank: Optional[int] = None
    likes: List[int] = field(default_factory=list)
    saves: List[int] = field(default_factory=list)
    reports: Dict[str, Dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['answers'] = json.dumps(d['answers'])
        d['ratings'] = json.dumps(d['ratings'])
        d['likes'] = json.dumps(d['likes'])
        d['saves'] = json.dumps(d['saves'])
        d['reports'] = json.dumps(d['reports'])
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'ParticipantState':
        if isinstance(data.get('answers'), str):
            data['answers'] = json.loads(data['answers'])
        if isinstance(data.get('ratings'), str):
            data['ratings'] = json.loads(data['ratings'])
        if isinstance(data.get('likes'), str):
            data['likes'] = json.loads(data['likes'])
        if isinstance(data.get('saves'), str):
            data['saves'] = json.loads(data['saves'])
        if isinstance(data.get('reports'), str):
            data['reports'] = json.loads(data['reports'])
        return cls(**data)


class QuizState:
    """
    State for a single live quiz.
    All mutations are serialised via its RLock.
    """

    def __init__(self, quiz_id: int, metadata: dict,
                 question_ids: List[int], questions_cache: Dict[int, dict]):
        self.id = quiz_id
        self.metadata = metadata
        self.question_ids = question_ids
        self.questions_cache = questions_cache
        self.participants: Dict[int, ParticipantState] = {}
        self.leaderboard: List[Tuple[int, int]] = []
        self.lock = threading.RLock()
        self.version = 0
        self.status = metadata.get('status', 'waiting')
        self.started_at = metadata.get('started_at')
        self.ended_at = None
        self.finalized = False
        self.dirty = True
        self._checkpoint_in_progress = False
        self._final_checkpoint_done = False

    # ---------- Participant Management ----------

    def add_participant(self, user_id: int, name: str, public_id: str) -> bool:
        with self.lock:
            existing = self.participants.get(user_id)
            if existing is not None and existing.status != 'left':
                return False
            self.participants[user_id] = ParticipantState(
                user_id=user_id,
                name=name,
                public_id=public_id
            )
            self._update_leaderboard()
            self.version += 1
            self.dirty = True
            return True

    def restore_participant(self, user_id: int,
                            name: Optional[str] = None,
                            public_id: Optional[str] = None,
                            score: Optional[int] = None,
                            current_question_index: Optional[int] = None,
                            answers: Optional[Dict] = None,
                            correct_count: Optional[int] = None,
                            wrong_count: Optional[int] = None,
                            skipped_count: Optional[int] = None,
                            is_ready: Optional[bool] = None,
                            status: Optional[str] = None) -> bool:
        """
        Bring a participant back into the active set.

        If optional snapshot values are supplied (score, current index,
        answers, etc.) they are applied to the participant. This is
        used by the auto-recovery path in live_quiz_bp when a
        participant exists in the database but is missing from memory.

        Existing fields are only overwritten when a value is explicitly
        supplied — never cleared by an unset argument.
        """
        with self.lock:
            existing = self.participants.get(user_id)

            if existing is None:
                p = ParticipantState(
                    user_id=user_id,
                    name=name or 'Participant',
                    public_id=public_id or '----',
                    score=score if score is not None else 0,
                    current_question_index=(
                        current_question_index
                        if current_question_index is not None else 0
                    ),
                    answers=answers if answers is not None else {},
                    correct_count=correct_count or 0,
                    wrong_count=wrong_count or 0,
                    skipped_count=skipped_count or 0,
                    is_ready=bool(is_ready) if is_ready is not None else False,
                    status=status or 'active',
                )
                self.participants[user_id] = p
                self._update_leaderboard()
                self.version += 1
                self.dirty = True
                return True

            if name:
                existing.name = name
            if public_id:
                existing.public_id = public_id
            if score is not None:
                existing.score = score
            if current_question_index is not None:
                existing.current_question_index = current_question_index
            if answers is not None:
                existing.answers = answers
            if correct_count is not None:
                existing.correct_count = correct_count
            if wrong_count is not None:
                existing.wrong_count = wrong_count
            if skipped_count is not None:
                existing.skipped_count = skipped_count
            if is_ready is not None:
                existing.is_ready = bool(is_ready)
            if status is not None:
                existing.status = status
            elif existing.status == 'left':
                existing.status = 'active'

            self._update_leaderboard()
            self.version += 1
            self.dirty = True
            return True

    def remove_participant(self, user_id: int) -> bool:
        with self.lock:
            p = self.participants.get(user_id)
            if not p:
                return False
            p.status = 'left'
            self._update_leaderboard()
            self.version += 1
            self.dirty = True
            return True

    def set_participant_ready(self, user_id: int, ready: bool) -> bool:
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status == 'left':
                return False
            p.is_ready = ready
            self.version += 1
            self.dirty = True
            return True

    def get_participant(self, user_id: int) -> Optional[ParticipantState]:
        with self.lock:
            return self.participants.get(user_id)

    def get_participant_copy(self, user_id: int) -> Optional[dict]:
        with self.lock:
            p = self.participants.get(user_id)
            if p:
                return p.to_dict()
            return None

    def get_all_participants(self) -> List[dict]:
        with self.lock:
            result = []
            for uid, p in self.participants.items():
                result.append({
                    'student_id': uid,
                    'name': p.name,
                    'public_id': p.public_id,
                    'status': p.status,
                    'is_ready': p.is_ready,
                    'is_creator': (uid == self.metadata.get('creator_id'))
                })
            return result

    def get_active_participants(self) -> List[dict]:
        with self.lock:
            result = []
            for uid, p in self.participants.items():
                if p.status == 'left':
                    continue
                result.append({
                    'student_id': uid,
                    'name': p.name,
                    'public_id': p.public_id,
                    'status': p.status,
                    'is_ready': p.is_ready,
                    'is_creator': (uid == self.metadata.get('creator_id'))
                })
            return result

    def get_active_count(self) -> int:
        with self.lock:
            return sum(1 for p in self.participants.values() if p.status != 'left')

    # ---------- Quiz Control ----------

    def start(self) -> bool:
        with self.lock:
            if self.status not in ('waiting', 'scheduled'):
                return False
            self.status = 'active'
            self.started_at = get_somali_time_db()
            self.version += 1
            self.dirty = True
            return True

    def is_active(self) -> bool:
        return self.status == 'active'

    def is_finished(self) -> bool:
        return self.status == 'finished'

    def is_completed(self) -> bool:
        with self.lock:
            if not self.participants:
                return False
            total = len(self.question_ids)
            active = [p for p in self.participants.values() if p.status != 'left']
            if not active:
                return True
            return all(p.current_question_index >= total for p in active)

    # ---------- Question Handling ----------

    def get_current_question_for_participant(self, user_id: int) -> Optional[dict]:
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status == 'left':
                return None
            idx = p.current_question_index
            if idx >= len(self.question_ids):
                return None
            qid = self.question_ids[idx]
            return self.questions_cache.get(qid)

    def get_question_data(self, question_id: int) -> Optional[dict]:
        with self.lock:
            return self.questions_cache.get(question_id)

    # ---------- Answer Submission (IDEMPOTENT) ----------

    def submit_answer(self, user_id: int, question_id: int,
                      answer: str) -> Tuple[bool, dict]:
        """
        Record an answer. Idempotent — a repeated submit for the same
        (user, question) returns the previously recorded result.

        Never raises out of this method; internal errors are returned
        as a synthetic payload so the HTTP layer can build a valid
        response for the client.
        """
        try:
            return self._submit_answer_inner(user_id, question_id, answer)
        except Exception as e:
            logger.error(
                f"submit_answer inner failed for user {user_id} "
                f"quiz {self.id}: {e}", exc_info=True,
            )
            return True, {
                'correct': False,
                'correct_answer': '',
                'explanation': '',
                'new_score': 0,
                'deferred': True,
            }

    def _submit_answer_inner(self, user_id: int, question_id: int,
                             answer: str) -> Tuple[bool, dict]:
        with self.lock:
            try:
                question_id = int(question_id)
            except (TypeError, ValueError):
                return False, {'error': 'Invalid question_id', 'resync': True}

            p = self.participants.get(user_id)
            if not p or p.status != 'active':
                return False, {
                    'error': 'Not an active participant',
                    'resync': True,
                }

            if self.status != 'active':
                return False, {'error': 'Quiz not active', 'resync': True}

            q_data = self.questions_cache.get(question_id) or {}

            # ── Idempotent path ──
            # Already answered this question — return the RECORDED result.
            # The recorded answer is the source of truth. A duplicate
            # submit, a retry after a lost response, and a mobile
            # double-fire all land here and get a consistent reply.
            if str(question_id) in p.answers:
                recorded = p.answers[str(question_id)]
                return True, {
                    'correct': bool(recorded.get('correct', False)),
                    'correct_answer': q_data.get('correct_answer', ''),
                    'explanation': q_data.get('explanation', ''),
                    'new_score': p.score,
                    'already_answered': True,
                    'submitted_answer': recorded.get('answer'),
                }

            # ── Convergence path ──
            # Not answered, but the question id doesn't match the
            # current index. This means the client is behind (or a
            # stale poll is in flight). Return the current state so
            # the client can resync, rather than rejecting outright.
            if p.current_question_index >= len(self.question_ids):
                return False, {
                    'error': 'Quiz already completed',
                    'completed': True,
                }

            current_qid = self.question_ids[p.current_question_index]
            if current_qid != question_id:
                return False, {
                    'error': 'Question has moved on',
                    'resync': True,
                    'current_index': p.current_question_index,
                    'current_question_id': current_qid,
                }

            # ── Fresh answer ──
            correct = (answer == q_data.get('correct_answer'))
            points = 2 if correct else 0

            p.score += points
            p.correct_count += 1 if correct else 0
            p.wrong_count += 0 if correct else 1
            p.answers[str(question_id)] = {
                'answer': answer,
                'correct': correct,
                'skipped': False,
            }

            self._update_leaderboard()
            self.version += 1
            self.dirty = True

            return True, {
                'correct': correct,
                'correct_answer': q_data.get('correct_answer', ''),
                'explanation': q_data.get('explanation', ''),
                'new_score': p.score,
            }

    # ---------- Skip (IDEMPOTENT) ----------

    def skip_question(self, user_id: int, question_id: int) -> Tuple[bool, str]:
        """
        Skip a question. Idempotent — a repeated skip of the same
        question returns (True, 'already_skipped').
        """
        try:
            return self._skip_question_inner(user_id, question_id)
        except Exception as e:
            logger.error(
                f"skip_question inner failed for user {user_id} "
                f"quiz {self.id}: {e}", exc_info=True,
            )
            return True, 'deferred'

    def _skip_question_inner(self, user_id: int,
                             question_id: int) -> Tuple[bool, str]:
        with self.lock:
            try:
                question_id = int(question_id)
            except (TypeError, ValueError):
                return False, 'Invalid question_id'

            p = self.participants.get(user_id)
            if not p or p.status != 'active':
                return False, 'Not an active participant'

            if self.status != 'active':
                return False, 'Quiz not active'

            # Already answered or skipped — return success, no-op.
            if str(question_id) in p.answers:
                recorded = p.answers[str(question_id)]
                if recorded.get('skipped'):
                    return True, 'already_skipped'
                return True, 'already_answered'

            if p.current_question_index >= len(self.question_ids):
                return True, 'already_completed'

            current_qid = self.question_ids[p.current_question_index]
            if current_qid != question_id:
                # Question is past — treat as already advanced past it.
                return True, 'already_advanced'

            p.answers[str(question_id)] = {
                'answer': None,
                'correct': False,
                'skipped': True,
            }
            p.skipped_count += 1
            self.version += 1
            self.dirty = True
            return True, 'skipped'

    # ---------- Advance (IDEMPOTENT) ----------

    def advance_question(self, user_id: int,
                         question_id: int) -> Tuple[bool, str]:
        """
        Advance past a question. Idempotent — advancing a question the
        participant has already passed returns (True, 'already_advanced')
        rather than a mismatch error.
        """
        try:
            return self._advance_question_inner(user_id, question_id)
        except Exception as e:
            logger.error(
                f"advance_question inner failed for user {user_id} "
                f"quiz {self.id}: {e}", exc_info=True,
            )
            return True, 'deferred'

    def _advance_question_inner(self, user_id: int,
                                question_id: int) -> Tuple[bool, str]:
        with self.lock:
            try:
                question_id = int(question_id)
            except (TypeError, ValueError):
                return False, 'Invalid question_id'

            p = self.participants.get(user_id)
            if not p or p.status != 'active':
                return False, 'Not an active participant'

            if self.status != 'active':
                return False, 'Quiz not active'

            if p.current_question_index >= len(self.question_ids):
                return True, 'already_completed'

            current_qid = self.question_ids[p.current_question_index]

            # ── Idempotent path ──
            # Client is asking to advance past a question that's
            # already behind us. Treat as success.
            if current_qid != question_id:
                try:
                    past_index = self.question_ids.index(question_id)
                except ValueError:
                    # Unknown question id — should not happen.
                    return False, 'Unknown question'

                if past_index < p.current_question_index:
                    return True, 'already_advanced'

                # Client is asking to advance a FUTURE question. Do
                # not advance — this is a client desync. Return success
                # with the current state hint so the client resyncs
                # via get-question on the next tick.
                return False, 'Question mismatch'

            # ── Fresh advance ──
            if str(question_id) not in p.answers:
                return False, 'Must answer or skip first'

            p.current_question_index += 1
            if p.current_question_index >= len(self.question_ids):
                p.status = 'completed'

            self.version += 1
            self.dirty = True
            return True, 'advanced'

    # ---------- Legacy rating (dormant) ----------

    def submit_rating(self, user_id: int, question_id: int,
                      rating: str) -> Tuple[bool, str]:
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status != 'active':
                return False, 'Not an active participant'
            if self.status != 'active':
                return False, 'Quiz not active'
            if str(question_id) in p.ratings:
                return True, 'already_rated'
            p.ratings[str(question_id)] = rating
            p.current_question_index += 1
            self.version += 1
            self.dirty = True
            if p.current_question_index >= len(self.question_ids):
                p.status = 'completed'
            return True, 'rated'

    # ---------- Reaction methods (idempotent-friendly) ----------

    def toggle_like(self, user_id: int, question_id: int) -> bool:
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status == 'left':
                return False
            try:
                qid = int(question_id)
            except (TypeError, ValueError):
                return False
            if qid in p.likes:
                p.likes.remove(qid)
            else:
                p.likes.append(qid)
            self.version += 1
            self.dirty = True
            return True

    def toggle_save(self, user_id: int, question_id: int) -> bool:
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status == 'left':
                return False
            try:
                qid = int(question_id)
            except (TypeError, ValueError):
                return False
            if qid in p.saves:
                p.saves.remove(qid)
            else:
                p.saves.append(qid)
            self.version += 1
            self.dirty = True
            return True

    def add_report(self, user_id: int, question_id: int, reason: str,
                   comment: str = '') -> bool:
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status == 'left':
                return False
            qid_str = str(question_id)
            if qid_str in p.reports:
                return True  # idempotent
            p.reports[qid_str] = {'reason': reason, 'comment': comment}
            self.version += 1
            self.dirty = True
            return True

    def get_reaction_status(self, user_id: int, question_id: int) -> dict:
        with self.lock:
            p = self.participants.get(user_id)
            if not p:
                return {'liked': False, 'saved': False, 'reported': False}
            try:
                qid_int = int(question_id)
            except (TypeError, ValueError):
                qid_int = question_id
            qid_str = str(question_id)
            return {
                'liked':    qid_int in p.likes,
                'saved':    qid_int in p.saves,
                'reported': qid_str in p.reports,
            }

    # ---------- Leaderboard ----------

    def _update_leaderboard(self):
        scores = [(uid, p.score) for uid, p in self.participants.items()
                  if p.status != 'left']
        scores.sort(key=lambda x: x[1], reverse=True)
        self.leaderboard = scores

    def get_leaderboard(self, limit: int = 10) -> List[dict]:
        with self.lock:
            top = self.leaderboard[:limit]
            result = []
            for user_id, score in top:
                p = self.participants.get(user_id)
                if p:
                    result.append({
                        'user_id': user_id,
                        'name': p.name,
                        'score': score,
                    })
            return result

    def get_user_rank(self, user_id: int) -> Optional[int]:
        with self.lock:
            for idx, (uid, _) in enumerate(self.leaderboard, 1):
                if uid == user_id:
                    return idx
            return None

    # ---------- Checkpointing ----------

    def checkpoint(self) -> dict:
        with self.lock:
            return {
                'quiz_id': self.id,
                'metadata': self.metadata,
                'question_ids': self.question_ids,
                'status': self.status,
                'started_at': self.started_at,
                'participants': {
                    str(uid): p.to_dict()
                    for uid, p in self.participants.items()
                },
                'version': self.version,
                'leaderboard': self.leaderboard,
            }

    def mark_clean(self):
        with self.lock:
            self.dirty = False

    def restore_from_checkpoint(self, checkpoint_data: dict):
        with self.lock:
            self.metadata = checkpoint_data['metadata']
            self.question_ids = checkpoint_data['question_ids']
            self.status = checkpoint_data['status']
            self.started_at = checkpoint_data['started_at']
            self.version = checkpoint_data['version']
            self.leaderboard = checkpoint_data.get('leaderboard', [])
            self.participants = {}
            for uid, pdata in checkpoint_data['participants'].items():
                p = ParticipantState.from_dict(pdata)
                self.participants[int(uid)] = p
            self.dirty = False

    # ---------- Finalization ----------

    def finalize(self) -> dict:
        with self.lock:
            if self.finalized:
                return {'error': 'Already finalized'}

            self.finalized = True
            self.status = 'finished'
            self.ended_at = get_somali_time_db()
            self.dirty = True

            sorted_participants = sorted(
                [(uid, p.score) for uid, p in self.participants.items()
                 if p.status != 'left'],
                key=lambda x: x[1], reverse=True,
            )
            for rank, (uid, _) in enumerate(sorted_participants, 1):
                p = self.participants.get(uid)
                if p:
                    p.rank = rank

            final_data = {
                'quiz_id': self.id,
                'ended_at': self.ended_at,
                'participants': [],
            }
            for uid, p in self.participants.items():
                final_data['participants'].append({
                    'user_id': uid,
                    'score': p.score,
                    'correct_count': p.correct_count,
                    'wrong_count': p.wrong_count,
                    'skipped_count': p.skipped_count,
                    'answers': p.answers,
                    'ratings': p.ratings,
                    'likes': p.likes,
                    'saves': p.saves,
                    'reports': p.reports,
                    'rank': getattr(p, 'rank', None),
                    'status': p.status,
                })
            return final_data

    def mark_finalized(self):
        with self.lock:
            already = self.finalized
            self.status = 'finished'
            if not self.ended_at:
                self.ended_at = get_somali_time_db()
            self.finalized = True
            self.dirty = True

        if not already:
            try:
                manager = get_live_quiz_state_manager()
                manager._force_final_checkpoint(self)
            except Exception as e:
                logger.warning(f"mark_finalized force-checkpoint failed: {e}")

    def cleanup(self):
        pass


# ============================================
# State Manager Singleton
# ============================================

class LiveQuizStateManager:
    """Manages all active quizzes in memory."""

    def __init__(self):
        self._quizzes: Dict[int, QuizState] = {}
        self._lock = threading.RLock()
        self._event_queue = queue.Queue()
        self._writer_thread = None
        self._running = False
        self._checkpoint_interval = 5
        self._checkpoint_timer = None
        self._shutdown_event = threading.Event()
        self._event_retry_backoff = 0.1
        self._max_retry_delay = 5.0
        self._pending_events: Dict[int, int] = {}
        self._pending_lock = threading.Lock()
        self._threads_lock = threading.Lock()

    # ---------- Lifecycle ----------

    def start(self):
        with self._threads_lock:
            if self._running and self._writer_thread and self._writer_thread.is_alive():
                return
            self._running = True
            self._shutdown_event.clear()
            self._writer_thread = threading.Thread(
                target=self._writer_loop, daemon=True, name='lq-writer',
            )
            self._writer_thread.start()
            self._start_checkpoint_timer()
            logger.info("LiveQuizStateManager started with background writer.")

    def stop(self):
        self._running = False
        self._shutdown_event.set()
        if self._writer_thread:
            self._writer_thread.join(timeout=5)
        if self._checkpoint_timer:
            self._checkpoint_timer.join(timeout=2)
        logger.info("LiveQuizStateManager stopped.")

    def _ensure_threads_alive(self):
        """Restart worker threads if they died. Called from entry points."""
        with self._threads_lock:
            if not self._running:
                self.start()
                return
            if not self._writer_thread or not self._writer_thread.is_alive():
                logger.warning("Writer thread died — restarting.")
                self._writer_thread = threading.Thread(
                    target=self._writer_loop, daemon=True, name='lq-writer',
                )
                self._writer_thread.start()
            if not self._checkpoint_timer or not self._checkpoint_timer.is_alive():
                logger.warning("Checkpoint thread died — restarting.")
                self._start_checkpoint_timer()

    # ---------- Quiz CRUD ----------

    def create_quiz(self, quiz_id: int, metadata: dict,
                    question_ids: List[int],
                    questions_cache: Dict[int, dict]) -> QuizState:
        with self._lock:
            if quiz_id in self._quizzes:
                return self._quizzes[quiz_id]
            quiz = QuizState(quiz_id, metadata, question_ids, questions_cache)
            self._quizzes[quiz_id] = quiz
            return quiz

    def get_quiz(self, quiz_id: int) -> Optional[QuizState]:
        with self._lock:
            return self._quizzes.get(quiz_id)

    def delete_quiz(self, quiz_id: int):
        with self._lock:
            quiz = self._quizzes.pop(quiz_id, None)
            if quiz:
                quiz.cleanup()
                logger.info(f"Removed quiz {quiz_id} from memory")

    def get_all_active_quizzes(self) -> List[int]:
        with self._lock:
            return list(self._quizzes.keys())

    # ---------- Auto-Recovery ----------

    def ensure_quiz_in_memory(self, quiz_id: int) -> bool:
        """
        Ensure the quiz is in memory. On a miss, rebuild from checkpoint
        or DB. Safe under concurrent callers.

        After restoring from a checkpoint the DB participants are merged
        in — the DB is authoritative for membership, the checkpoint is
        authoritative for transient answer state. A checkpoint that
        predates a join can therefore never hide the join.
        """
        self._ensure_threads_alive()

        with self._lock:
            if quiz_id in self._quizzes:
                return True
        try:
            from db import get_live_quiz_by_id, get_question_by_id
            quiz_data = get_live_quiz_by_id(quiz_id)
            if not quiz_data:
                return False
            question_ids = quiz_data.get('question_ids', [])
            questions_cache = {}
            for qid in question_ids:
                q = get_question_by_id(qid)
                if q:
                    questions_cache[qid] = q

            cursor = execute_with_retry(
                "SELECT checkpoint_data FROM live_quiz_checkpoints "
                "WHERE quiz_id = ? ORDER BY version DESC LIMIT 1",
                (quiz_id,),
            )
            row = cursor.fetchone()
            if row:
                cp_data = json.loads(row['checkpoint_data'])
                quiz = QuizState(quiz_id, quiz_data, question_ids, questions_cache)
                quiz.restore_from_checkpoint(cp_data)
                replay_from = cp_data.get(
                    'as_of_sequence', cp_data.get('version', 0),
                )
                self._replay_events_after(quiz, replay_from)
                # Defence in depth: DB is authoritative for membership.
                # Anything the checkpoint missed is added here.
                self._merge_participants_from_db(quiz)
            else:
                quiz = QuizState(quiz_id, quiz_data, question_ids, questions_cache)
                self._load_participants_from_db(quiz)
                quiz._update_leaderboard()
                quiz.dirty = True

            with self._lock:
                if quiz_id in self._quizzes:
                    return True
                self._quizzes[quiz_id] = quiz
            logger.info(f"Recovered quiz {quiz_id} from storage on demand")
            return True
        except Exception as e:
            logger.error(f"Failed to recover quiz {quiz_id} on demand: {e}",
                         exc_info=True)
            return False

    def _merge_participants_from_db(self, quiz: QuizState):
        """
        Ensure every DB participant is present in the in-memory QuizState.

        A checkpoint is written on a 5s cadence by a background thread;
        a JOIN event is written on a 2s cadence by a different thread.
        In the window between a join and the next checkpoint, the
        checkpoint does not know about the new participant — but the
        DB does. Restoring from that checkpoint alone would hide the
        participant from every subsequent /quiz-state poll.

        This method closes that window. Existing participants are
        synced by status only (answer state may be newer in memory);
        missing participants are hydrated from the DB entirely.
        """
        try:
            cursor = execute_with_retry(
                "SELECT student_id, score, current_question_index, "
                "correct_count, wrong_count, skipped_count, "
                "answers, ratings, status, is_ready "
                "FROM live_quiz_participants WHERE quiz_id = ?",
                (quiz.id,),
            )
            rows = cursor.fetchall()
        except Exception as e:
            logger.warning(
                f"_merge_participants_from_db read failed for "
                f"quiz {quiz.id}: {e}"
            )
            return

        merged = 0
        for pr in rows:
            sid = pr['student_id']
            if sid in quiz.participants:
                existing = quiz.participants[sid]
                db_status = pr['status'] or 'active'
                if existing.status != db_status:
                    existing.status = db_status
                    quiz._update_leaderboard()
                    quiz.version += 1
                continue

            # Missing from memory — hydrate from DB.
            try:
                student = execute_with_retry(
                    "SELECT first_name, last_name, public_id "
                    "FROM students WHERE id = ?",
                    (sid,),
                ).fetchone()
            except Exception:
                student = None
            if not student:
                continue

            name = (
                f"{student['first_name']} {student['last_name']}"
            ).strip() or 'Participant'
            try:
                answers = json.loads(pr['answers']) if pr['answers'] else {}
            except Exception:
                answers = {}

            quiz.restore_participant(
                user_id=sid,
                name=name,
                public_id=student['public_id'] or '----',
                score=pr['score'] or 0,
                current_question_index=pr['current_question_index'] or 0,
                answers=answers,
                correct_count=pr['correct_count'] or 0,
                wrong_count=pr['wrong_count'] or 0,
                skipped_count=pr['skipped_count'] or 0,
                is_ready=bool(pr['is_ready']),
                status=pr['status'] or 'active',
            )
            merged += 1

        if merged:
            logger.info(
                f"Merged {merged} DB participant(s) into quiz {quiz.id} "
                f"(missing from checkpoint)"
            )

    def ensure_participant_in_memory(self, quiz_id: int,
                                     user_id: int) -> bool:
        """
        Ensure a participant exists in the in-memory state.

        If the quiz is not in memory, this rebuilds it first. If the
        participant record is missing from memory but present in the
        database, the record is restored from the database values —
        score, index, answers, and status all preserved.

        Returns True when the participant is present after the call.
        """
        if not self.ensure_quiz_in_memory(quiz_id):
            return False

        quiz = self.get_quiz(quiz_id)
        if not quiz:
            return False

        p = quiz.get_participant(user_id)
        if p is not None:
            return True

        # Not in memory — attempt DB recovery.
        try:
            from db import get_student_by_id
            cursor = execute_with_retry(
                "SELECT score, current_question_index, correct_count, "
                "wrong_count, skipped_count, answers, ratings, status, is_ready "
                "FROM live_quiz_participants "
                "WHERE quiz_id = ? AND student_id = ?",
                (quiz_id, user_id),
            )
            row = cursor.fetchone()
            if not row:
                return False

            student = get_student_by_id(user_id) or {}
            name = (
                f"{student.get('first_name', '')} "
                f"{student.get('last_name', '')}"
            ).strip() or 'Participant'
            public_id = student.get('public_id', '----')

            try:
                answers = json.loads(row['answers']) if row['answers'] else {}
            except Exception:
                answers = {}

            quiz.restore_participant(
                user_id=user_id,
                name=name,
                public_id=public_id,
                score=row['score'] or 0,
                current_question_index=row['current_question_index'] or 0,
                answers=answers,
                correct_count=row['correct_count'] or 0,
                wrong_count=row['wrong_count'] or 0,
                skipped_count=row['skipped_count'] or 0,
                is_ready=bool(row['is_ready']),
                status=row['status'] or 'active',
            )
            logger.info(
                f"Auto-recovered participant {user_id} into quiz {quiz_id}"
            )
            return True
        except Exception as e:
            logger.error(
                f"ensure_participant_in_memory failed for "
                f"quiz {quiz_id} user {user_id}: {e}", exc_info=True,
            )
            return False

    # ---------- Event queue ----------

    def enqueue_event(self, event: dict):
        self._ensure_threads_alive()
        if not self._running:
            logger.warning("Event enqueued while manager is not running")
            return
        if 'created_at' not in event:
            event['created_at'] = get_somali_time_db()
        if 'sequence' not in event:
            event['sequence'] = 0
        qid = event['quiz_id']
        with self._pending_lock:
            self._pending_events[qid] = self._pending_events.get(qid, 0) + 1
        self._event_queue.put(event)

    def _writer_loop(self):
        batch = []
        last_flush = time.time()
        while self._running and not self._shutdown_event.is_set():
            try:
                item = self._event_queue.get(timeout=1)
                batch.append(item)
            except queue.Empty:
                pass
            now_ts = time.time()
            if len(batch) >= 50 or (batch and now_ts - last_flush >= 2):
                self._flush_events_with_retry(batch)
                batch = []
                last_flush = now_ts
        if batch:
            self._flush_events_with_retry(batch)

    def _flush_events_with_retry(self, events: List[dict], max_attempts=5):
        if not events:
            return
        attempt = 0
        delay = self._event_retry_backoff
        while attempt < max_attempts:
            try:
                self._flush_events(events)
                return
            except Exception as e:
                logger.error(f"Event flush attempt {attempt+1} failed: {e}")
                attempt += 1
                if attempt >= max_attempts:
                    logger.critical(
                        f"Failed to flush events after {max_attempts} attempts."
                    )
                    self._decrement_pending(events)
                    return
                time.sleep(delay)
                delay = min(delay * 2, self._max_retry_delay)

    def _decrement_pending(self, events: List[dict]):
        with self._pending_lock:
            for ev in events:
                qid = ev['quiz_id']
                if qid in self._pending_events:
                    self._pending_events[qid] = max(
                        0, self._pending_events[qid] - 1,
                    )

    def _flush_events(self, events: List[dict]):
        if not events:
            return
        conn = get_db()
        cursor = conn.cursor()
        quiz_ids = set(ev['quiz_id'] for ev in events)
        seq_map = {}
        for qid in quiz_ids:
            cursor.execute(
                "SELECT COALESCE(MAX(sequence), 0) as max_seq "
                "FROM live_quiz_events WHERE quiz_id = ?",
                (qid,),
            )
            row = cursor.fetchone()
            seq_map[qid] = row['max_seq'] if row else 0

        sql = """
            INSERT INTO live_quiz_events
            (quiz_id, user_id, event_type, question_id, payload, sequence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        params = []
        for ev in events:
            seq_map[ev['quiz_id']] += 1
            params.append((
                ev['quiz_id'],
                ev.get('user_id'),
                ev['event_type'],
                ev.get('question_id'),
                ev.get('payload'),
                seq_map[ev['quiz_id']],
                ev.get('created_at', get_somali_time_db()),
            ))
        cursor.executemany(sql, params)
        conn.commit()
        self._decrement_pending(events)
        logger.debug(f"Flushed {len(events)} events")

    # ---------- Checkpointing ----------

    def _start_checkpoint_timer(self):
        def checkpoint_loop():
            while self._running and not self._shutdown_event.is_set():
                time.sleep(self._checkpoint_interval)
                try:
                    self._checkpoint_all()
                except Exception as e:
                    logger.error(f"Checkpoint loop error: {e}", exc_info=True)
        self._checkpoint_timer = threading.Thread(
            target=checkpoint_loop, daemon=True, name='lq-checkpoint',
        )
        self._checkpoint_timer.start()

    def _checkpoint_all(self):
        with self._lock:
            quizzes = list(self._quizzes.values())
        for quiz in quizzes:
            with self._pending_lock:
                pending = self._pending_events.get(quiz.id, 0)
            if pending > 0:
                continue
            if not quiz.dirty or quiz._checkpoint_in_progress:
                continue
            if quiz.finalized and quiz._final_checkpoint_done:
                continue
            self._checkpoint_quiz(quiz)

    def _checkpoint_quiz(self, quiz: QuizState):
        quiz._checkpoint_in_progress = True
        try:
            data = quiz.checkpoint()
            cursor = execute_with_retry(
                "SELECT COALESCE(MAX(sequence), 0) AS s "
                "FROM live_quiz_events WHERE quiz_id = ?",
                (quiz.id,),
            )
            row = cursor.fetchone()
            data['as_of_sequence'] = (
                int(row['s']) if row and row['s'] is not None else 0
            )
            payload = json.dumps(data)
            execute_with_retry("""
                INSERT OR REPLACE INTO live_quiz_checkpoints
                    (quiz_id, checkpoint_data, version, created_at)
                VALUES (?, ?, ?, ?)
            """, (quiz.id, payload, data['version'],
                  get_somali_time_db()), commit=True)
            quiz.mark_clean()
            if quiz.finalized:
                quiz._final_checkpoint_done = True
        except Exception as e:
            logger.error(f"Checkpoint failed for quiz {quiz.id}: {e}",
                         exc_info=True)
        finally:
            quiz._checkpoint_in_progress = False

    def _force_final_checkpoint(self, quiz: QuizState):
        try:
            data = quiz.checkpoint()
            cursor = execute_with_retry(
                "SELECT COALESCE(MAX(sequence), 0) AS s "
                "FROM live_quiz_events WHERE quiz_id = ?",
                (quiz.id,),
            )
            row = cursor.fetchone()
            data['as_of_sequence'] = (
                int(row['s']) if row and row['s'] is not None else 0
            )
            payload = json.dumps(data)
            execute_with_retry("""
                INSERT OR REPLACE INTO live_quiz_checkpoints
                    (quiz_id, checkpoint_data, version, created_at)
                VALUES (?, ?, ?, ?)
            """, (quiz.id, payload, data['version'],
                  get_somali_time_db()), commit=True)
            quiz._final_checkpoint_done = True
            quiz.mark_clean()
            logger.info(f"Forced final checkpoint for quiz {quiz.id}")
        except Exception as e:
            logger.warning(
                f"_force_final_checkpoint failed for quiz {quiz.id}: {e}"
            )

    # ---------- Startup Recovery ----------

    def recover_active_quizzes(self):
        self._ensure_threads_alive()
        try:
            cursor = execute_with_retry(
                "SELECT id, title, subject_code, grade, question_count, "
                "status, join_code, max_participants, time_per_question, "
                "question_ids, started_at, ended_at, created_at "
                "FROM live_quizzes "
                "WHERE status IN ('waiting', 'scheduled', 'active')"
            )
            rows = cursor.fetchall()
            for row in rows:
                quiz_meta = dict(row)
                quiz_id = quiz_meta['id']
                try:
                    question_ids = (
                        json.loads(quiz_meta['question_ids'])
                        if quiz_meta['question_ids'] else []
                    )
                except Exception:
                    question_ids = []
                questions_cache = self._load_questions(question_ids)

                cp_cursor = execute_with_retry(
                    "SELECT checkpoint_data, version "
                    "FROM live_quiz_checkpoints WHERE quiz_id = ? "
                    "ORDER BY version DESC LIMIT 1",
                    (quiz_id,),
                )
                cp_row = cp_cursor.fetchone()

                if cp_row:
                    cp_data = json.loads(cp_row['checkpoint_data'])
                    quiz = QuizState(quiz_id, quiz_meta,
                                     question_ids, questions_cache)
                    quiz.restore_from_checkpoint(cp_data)
                    replay_from = cp_data.get(
                        'as_of_sequence', cp_data.get('version', 0),
                    )
                    self._replay_events_after(quiz, replay_from)
                    self._merge_participants_from_db(quiz)
                    with self._lock:
                        self._quizzes[quiz_id] = quiz
                    logger.info(
                        f"Recovered quiz {quiz_id} from checkpoint"
                    )
                else:
                    quiz = QuizState(quiz_id, quiz_meta,
                                     question_ids, questions_cache)
                    self._load_participants_from_db(quiz)
                    quiz._update_leaderboard()
                    quiz.dirty = True
                    with self._lock:
                        self._quizzes[quiz_id] = quiz
                    logger.info(
                        f"Recovered quiz {quiz_id} from participants"
                    )
        except Exception as e:
            logger.error(f"Recovery error: {e}", exc_info=True)

    def _load_questions(self, question_ids: List[int]) -> Dict[int, dict]:
        if not question_ids:
            return {}
        placeholders = ','.join(['?'] * len(question_ids))
        try:
            cursor = execute_with_retry(
                f"SELECT id, question_text, options, correct_answer, explanation "
                f"FROM questions WHERE id IN ({placeholders})",
                question_ids,
            )
            rows = cursor.fetchall()
        except Exception as e:
            logger.error(f"_load_questions failed: {e}")
            return {}
        qdict = {}
        for row in rows:
            q = dict(row)
            try:
                q['options'] = (
                    json.loads(q['options'])
                    if isinstance(q['options'], str) else q['options']
                )
            except Exception:
                q['options'] = {}
            qdict[q['id']] = q
        return qdict

    def _load_participants_from_db(self, quiz: QuizState):
        try:
            cursor = execute_with_retry(
                "SELECT student_id, score, current_question_index, "
                "correct_count, wrong_count, skipped_count, "
                "answers, ratings, status, is_ready "
                "FROM live_quiz_participants WHERE quiz_id = ?",
                (quiz.id,),
            )
            rows = cursor.fetchall()
        except Exception as e:
            logger.error(f"_load_participants_from_db failed: {e}")
            return

        for pr in rows:
            try:
                student = execute_with_retry(
                    "SELECT first_name, last_name, public_id "
                    "FROM students WHERE id = ?",
                    (pr['student_id'],),
                ).fetchone()
            except Exception:
                student = None

            if not student:
                continue
            name = f"{student['first_name']} {student['last_name']}".strip()
            public_id = student['public_id']
            try:
                answers = json.loads(pr['answers']) if pr['answers'] else {}
            except Exception:
                answers = {}
            try:
                ratings = json.loads(pr['ratings']) if pr['ratings'] else {}
            except Exception:
                ratings = {}

            p = ParticipantState(
                user_id=pr['student_id'],
                name=name,
                public_id=public_id,
                score=pr['score'] or 0,
                correct_count=pr['correct_count'] or 0,
                wrong_count=pr['wrong_count'] or 0,
                skipped_count=pr['skipped_count'] or 0,
                current_question_index=pr['current_question_index'] or 0,
                answers=answers,
                ratings=ratings,
                status=pr['status'] or 'active',
                is_ready=bool(pr['is_ready']),
            )
            quiz.participants[pr['student_id']] = p

    def _replay_events_after(self, quiz: QuizState, as_of_sequence: int):
        try:
            cursor = execute_with_retry(
                "SELECT * FROM live_quiz_events "
                "WHERE quiz_id = ? AND sequence > ? ORDER BY sequence ASC",
                (quiz.id, as_of_sequence),
            )
            rows = cursor.fetchall()
        except Exception as e:
            logger.error(f"_replay_events_after read failed: {e}")
            return

        for row in rows:
            event_type = row['event_type']
            try:
                payload = json.loads(row['payload']) if row['payload'] else {}
            except Exception:
                payload = {}
            user_id = row['user_id']
            question_id = row['question_id']

            try:
                if event_type == 'ANSWER':
                    p = quiz.participants.get(user_id)
                    if p and str(question_id) not in p.answers:
                        answer = payload.get('answer')
                        q_data = quiz.questions_cache.get(question_id)
                        correct = (
                            answer == q_data['correct_answer']
                            if q_data else False
                        )
                        points = 2 if correct else 0
                        p.score += points
                        p.correct_count += 1 if correct else 0
                        p.wrong_count += 0 if correct else 1
                        p.answers[str(question_id)] = {
                            'answer': answer,
                            'correct': correct,
                            'skipped': False,
                        }
                        quiz._update_leaderboard()
                        quiz.version += 1

                elif event_type == 'SKIP':
                    p = quiz.participants.get(user_id)
                    if p and str(question_id) not in p.answers:
                        p.answers[str(question_id)] = {
                            'answer': None,
                            'correct': False,
                            'skipped': True,
                        }
                        p.skipped_count += 1
                        quiz.version += 1

                elif event_type == 'ADVANCE':
                    p = quiz.participants.get(user_id)
                    if p:
                        expected_qid = None
                        if p.current_question_index < len(quiz.question_ids):
                            expected_qid = quiz.question_ids[
                                p.current_question_index
                            ]
                        if expected_qid is not None and \
                                str(expected_qid) == str(question_id):
                            p.current_question_index += 1
                            if p.current_question_index >= len(
                                    quiz.question_ids):
                                p.status = 'completed'
                            quiz.version += 1

                elif event_type == 'RATING':
                    p = quiz.participants.get(user_id)
                    if p and str(question_id) not in p.ratings:
                        rating = payload.get('rating')
                        p.ratings[str(question_id)] = rating
                        p.current_question_index += 1
                        quiz.version += 1

                elif event_type == 'LEAVE':
                    p = quiz.participants.get(user_id)
                    if p:
                        p.status = 'left'
                        quiz._update_leaderboard()
                        quiz.version += 1

                elif event_type == 'JOIN':
                    # A JOIN event is idempotent and creating. If the
                    # participant is already in memory it just flips
                    # status back to active; otherwise it synthesises
                    # the ParticipantState from the event payload. This
                    # is what closes the "checkpoint taken before the
                    # join discards the join on replay" hole.
                    p = quiz.participants.get(user_id)
                    if p is None:
                        name = (payload or {}).get('name') or 'Participant'
                        quiz.restore_participant(user_id, name=name)
                    else:
                        p.status = 'active'
                    quiz._update_leaderboard()
                    quiz.version += 1

                elif event_type == 'START':
                    quiz.status = 'active'
                    quiz.started_at = row['created_at']
                    quiz.version += 1

                elif event_type == 'COMPLETE':
                    quiz.status = 'finished'
                    quiz.ended_at = row['created_at']
                    quiz.finalized = True
                    quiz.version += 1
            except Exception as e:
                logger.warning(
                    f"Replay of {event_type} for quiz {quiz.id} "
                    f"skipped: {e}"
                )

        quiz.dirty = True
        logger.info(f"Replayed {len(rows)} events for quiz {quiz.id}")

    def cleanup_finished_quizzes(self, max_age_seconds=300):
        now_ts = time.time()
        with self._lock:
            to_remove = []
            for qid, quiz in self._quizzes.items():
                if quiz.finalized and quiz.ended_at:
                    try:
                        ended_ts = datetime.fromisoformat(
                            quiz.ended_at
                        ).timestamp()
                        if now_ts - ended_ts > max_age_seconds:
                            to_remove.append(qid)
                    except Exception:
                        pass
            for qid in to_remove:
                self._quizzes.pop(qid, None)
                logger.info(f"Cleaned up finished quiz {qid}")
        return len(to_remove)


# ============================================
# Singleton Instance
# ============================================

_state_manager = None
_state_manager_lock = threading.Lock()


def get_live_quiz_state_manager() -> LiveQuizStateManager:
    global _state_manager
    if _state_manager is None:
        with _state_manager_lock:
            if _state_manager is None:
                _state_manager = LiveQuizStateManager()
                _state_manager.start()

                def cleanup_loop():
                    while True:
                        time.sleep(60)
                        try:
                            _state_manager.cleanup_finished_quizzes()
                        except Exception as e:
                            logger.warning(f"cleanup loop error: {e}")

                threading.Thread(
                    target=cleanup_loop, daemon=True, name='lq-cleanup',
                ).start()
    return _state_manager


def initialize_state_manager():
    from database import ensure_live_quiz_tables
    ensure_live_quiz_tables()
    get_live_quiz_state_manager()


def recover_active_quizzes():
    manager = get_live_quiz_state_manager()
    manager.recover_active_quizzes()