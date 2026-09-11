# live_quiz_state.py
"""
Redis‑free Live Quiz State Manager for NuunPlatform.
Designed for single‑worker PythonAnywhere deployment.
All state is held in memory; SQLite used for checkpoints and events.
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
    answers: Dict[int, Dict] = field(default_factory=dict)  # question_id -> {answer, correct, skipped}
    ratings: Dict[int, str] = field(default_factory=dict)   # question_id -> 'HAA' or 'MAY'
    status: str = 'active'   # active, completed, left
    is_ready: bool = False
    rank: Optional[int] = None  # only set during finalization
    # NEW: interaction fields
    likes: List[int] = field(default_factory=list)           # question_ids liked
    saves: List[int] = field(default_factory=list)           # question_ids saved
    reports: Dict[int, Dict] = field(default_factory=dict)   # question_id -> {reason, comment}

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

    def __init__(self, quiz_id: int, metadata: dict, question_ids: List[int], questions_cache: Dict[int, dict]):
        self.id = quiz_id
        self.metadata = metadata
        self.question_ids = question_ids
        self.questions_cache = questions_cache  # question_id -> question data
        self.participants: Dict[int, ParticipantState] = {}
        self.leaderboard: List[Tuple[int, int]] = []  # (user_id, score), sorted desc
        self.lock = threading.RLock()
        self.version = 0
        self.status = metadata.get('status', 'waiting')
        self.started_at = metadata.get('started_at')
        self.ended_at = None
        self.finalized = False
        self.dirty = True  # indicates state changed since last checkpoint
        self._checkpoint_in_progress = False  # prevent concurrent checkpointing

    # ---------- Participant Management ----------

    def add_participant(self, user_id: int, name: str, public_id: str) -> bool:
        with self.lock:
            if user_id in self.participants:
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
        """Return a list of participant summaries (for UI)."""
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
        """Check if all active participants have finished all questions."""
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
        """Return the question data for the participant's current index, or None if finished."""
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status == 'left':
                return None
            idx = p.current_question_index
            if idx >= len(self.question_ids):
                return None  # participant completed
            qid = self.question_ids[idx]
            return self.questions_cache.get(qid)

    def get_question_data(self, question_id: int) -> Optional[dict]:
        with self.lock:
            return self.questions_cache.get(question_id)

    # ---------- Answer Submission (Atomic) ----------

    def submit_answer(self, user_id: int, question_id: int, answer: str) -> Tuple[bool, dict]:
        """
        Atomically process an answer.
        Returns (success, result) where result contains:
            - 'correct': bool
            - 'correct_answer': str
            - 'explanation': str
            - 'new_score': int
            - 'error': str (if success is False)
        """
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status != 'active':
                return False, {'error': 'Not an active participant'}

            if self.status != 'active':
                return False, {'error': 'Quiz not active'}

            # Check if participant has already answered this question
            if str(question_id) in p.answers:
                return False, {'error': 'Already answered this question'}

            # Verify this is the current question for the participant
            if p.current_question_index >= len(self.question_ids):
                return False, {'error': 'Quiz already completed'}

            qid = self.question_ids[p.current_question_index]
            if qid != question_id:
                return False, {'error': 'Question mismatch'}

            # Get correct answer
            q_data = self.questions_cache.get(question_id)
            if not q_data:
                return False, {'error': 'Question data missing'}

            correct = (answer == q_data['correct_answer'])
            points = 2 if correct else 0

            # Update state
            p.score += points
            p.correct_count += 1 if correct else 0
            p.wrong_count += 0 if correct else 1
            p.answers[question_id] = {'answer': answer, 'correct': correct, 'skipped': False}

            # Update leaderboard
            self._update_leaderboard()

            self.version += 1
            self.dirty = True

            return True, {
                'correct': correct,
                'correct_answer': q_data['correct_answer'],
                'explanation': q_data.get('explanation', ''),
                'new_score': p.score
            }

    # ---------- Skip ----------

    def skip_question(self, user_id: int, question_id: int) -> Tuple[bool, str]:
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status != 'active':
                return False, 'Not an active participant'

            if self.status != 'active':
                return False, 'Quiz not active'

            if p.current_question_index >= len(self.question_ids):
                return False, 'Quiz already completed'

            qid = self.question_ids[p.current_question_index]
            if qid != question_id:
                return False, 'Question mismatch'

            if str(question_id) in p.answers:
                return False, 'Already answered'

            p.answers[question_id] = {'answer': None, 'correct': False, 'skipped': True}
            p.skipped_count += 1
            p.current_question_index += 1
            self.version += 1
            self.dirty = True
            return True, 'skipped'

    # ---------- Rating ----------

    def submit_rating(self, user_id: int, question_id: int, rating: str) -> Tuple[bool, str]:
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status != 'active':
                return False, 'Not an active participant'

            if self.status != 'active':
                return False, 'Quiz not active'

            if str(question_id) in p.ratings:
                return False, 'Already rated'

            p.ratings[question_id] = rating
            p.current_question_index += 1
            self.version += 1
            self.dirty = True

            # If participant finished all questions, mark as completed
            if p.current_question_index >= len(self.question_ids):
                p.status = 'completed'

            return True, 'rated'

    # ---------- Interaction Methods (NEW) ----------

    def toggle_like(self, user_id: int, question_id: int) -> bool:
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status == 'left':
                return False
            if question_id in p.likes:
                p.likes.remove(question_id)
            else:
                p.likes.append(question_id)
            self.version += 1
            self.dirty = True
            return True

    def toggle_save(self, user_id: int, question_id: int) -> bool:
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status == 'left':
                return False
            if question_id in p.saves:
                p.saves.remove(question_id)
            else:
                p.saves.append(question_id)
            self.version += 1
            self.dirty = True
            return True

    def add_report(self, user_id: int, question_id: int, reason: str, comment: str = '') -> bool:
        with self.lock:
            p = self.participants.get(user_id)
            if not p or p.status == 'left':
                return False
            if question_id in p.reports:
                return False
            p.reports[question_id] = {'reason': reason, 'comment': comment}
            self.version += 1
            self.dirty = True
            return True

    # ---------- Leaderboard ----------

    def _update_leaderboard(self):
        """Rebuild leaderboard from participants."""
        scores = [(uid, p.score) for uid, p in self.participants.items() if p.status != 'left']
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
                        'score': score
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
        """
        Serialize all participant states for checkpointing.
        """
        with self.lock:
            data = {
                'quiz_id': self.id,
                'metadata': self.metadata,
                'question_ids': self.question_ids,
                'status': self.status,
                'started_at': self.started_at,
                'participants': {str(uid): p.to_dict() for uid, p in self.participants.items()},
                'version': self.version,
                'leaderboard': self.leaderboard,
                'interactions': {
                    'likes': {str(uid): p.likes for uid, p in self.participants.items()},
                    'saves': {str(uid): p.saves for uid, p in self.participants.items()},
                    'reports': {str(uid): p.reports for uid, p in self.participants.items()}
                }
            }
            return data

    def mark_clean(self):
        """Call this after checkpoint has been successfully persisted."""
        with self.lock:
            self.dirty = False

    def restore_from_checkpoint(self, checkpoint_data: dict):
        """Restore state from checkpoint (used during recovery)."""
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
            # Restore interactions
            interactions = checkpoint_data.get('interactions', {})
            for uid, likes in interactions.get('likes', {}).items():
                if int(uid) in self.participants:
                    self.participants[int(uid)].likes = likes
            for uid, saves in interactions.get('saves', {}).items():
                if int(uid) in self.participants:
                    self.participants[int(uid)].saves = saves
            for uid, reports in interactions.get('reports', {}).items():
                if int(uid) in self.participants:
                    self.participants[int(uid)].reports = reports
            self.dirty = False

    # ---------- Finalization (Atomic) ----------

    def finalize(self) -> dict:
        """
        Compute final results and ranks.
        This does not modify persistent state; it only returns the data.
        The caller must persist to SQLite and then call mark_finalized().
        """
        with self.lock:
            if self.finalized:
                return {'error': 'Already finalized'}
            # Compute ranks (memory only)
            sorted_participants = sorted(
                [(uid, p.score) for uid, p in self.participants.items() if p.status != 'left'],
                key=lambda x: x[1], reverse=True
            )
            for rank, (uid, _) in enumerate(sorted_participants, 1):
                p = self.participants.get(uid)
                if p:
                    p.rank = rank

            # Build final data
            final_data = {
                'quiz_id': self.id,
                'ended_at': get_somali_time_db(),
                'participants': []
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
                    'status': p.status
                })
            return final_data

    def mark_finalized(self):
        """Call this after final data has been durably persisted."""
        with self.lock:
            self.status = 'finished'
            self.ended_at = get_somali_time_db()
            self.finalized = True
            self.dirty = True  # we want a final checkpoint

    # ---------- Cleanup ----------

    def cleanup(self):
        """Release resources; called after quiz is deleted or finished."""
        # Nothing special; just let GC do its work


# ============================================
# State Manager Singleton
# ============================================

class LiveQuizStateManager:
    """Manages all active quizzes in memory."""

    def __init__(self):
        self._quizzes: Dict[int, QuizState] = {}
        self._lock = threading.RLock()  # for adding/removing quizzes
        self._event_queue = queue.Queue()
        self._writer_thread = None
        self._running = False
        self._checkpoint_interval = 5  # seconds
        self._checkpoint_timer = None
        self._shutdown_event = threading.Event()
        self._event_retry_backoff = 0.1  # initial delay for retry
        self._max_retry_delay = 5.0

    def start(self):
        """Start background writer thread and checkpoint timer."""
        if self._running:
            return
        self._running = True
        self._shutdown_event.clear()
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
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

    # ---------- Quiz Management ----------

    def create_quiz(self, quiz_id: int, metadata: dict, question_ids: List[int], questions_cache: Dict[int, dict]) -> QuizState:
        """Create a new quiz state."""
        with self._lock:
            if quiz_id in self._quizzes:
                raise ValueError(f"Quiz {quiz_id} already exists")
            quiz = QuizState(quiz_id, metadata, question_ids, questions_cache)
            self._quizzes[quiz_id] = quiz
            return quiz

    def get_quiz(self, quiz_id: int) -> Optional[QuizState]:
        with self._lock:
            return self._quizzes.get(quiz_id)

    def delete_quiz(self, quiz_id: int):
        """Remove quiz from memory (after finalization)."""
        with self._lock:
            quiz = self._quizzes.pop(quiz_id, None)
            if quiz:
                quiz.cleanup()
                logger.info(f"Removed quiz {quiz_id} from memory")

    def get_all_active_quizzes(self) -> List[int]:
        with self._lock:
            return list(self._quizzes.keys())

    # ---------- On‑demand recovery (for multi‑worker fallback) ----------
    def ensure_quiz_in_memory(self, quiz_id: int) -> bool:
        """Attempt to recover quiz from SQLite if not in memory."""
        with self._lock:
            if quiz_id in self._quizzes:
                return True
        # Recover from DB
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
            # Load checkpoint
            cursor = execute_with_retry(
                "SELECT checkpoint_data FROM live_quiz_checkpoints WHERE quiz_id = ? ORDER BY version DESC LIMIT 1",
                (quiz_id,)
            )
            row = cursor.fetchone()
            if row:
                cp_data = json.loads(row['checkpoint_data'])
                quiz = QuizState(quiz_id, quiz_data, question_ids, questions_cache)
                quiz.restore_from_checkpoint(cp_data)
                # Replay events after checkpoint version
                self._replay_events_after(quiz, cp_data['version'])
                with self._lock:
                    self._quizzes[quiz_id] = quiz
                logger.info(f"Recovered quiz {quiz_id} from checkpoint on demand")
                return True
            else:
                # No checkpoint - load participants from SQLite
                quiz = QuizState(quiz_id, quiz_data, question_ids, questions_cache)
                self._load_participants_from_db(quiz)
                quiz._update_leaderboard()
                quiz.dirty = True
                with self._lock:
                    self._quizzes[quiz_id] = quiz
                logger.info(f"Recovered quiz {quiz_id} from participants (no checkpoint) on demand")
                return True
        except Exception as e:
            logger.error(f"Failed to recover quiz {quiz_id} on demand: {e}", exc_info=True)
            return False

    # ---------- Event Queue ----------

    def enqueue_event(self, event: dict):
        """Add event to the persistence queue."""
        if not self._running:
            logger.warning("Event enqueued while manager is not running")
            return
        if 'created_at' not in event:
            event['created_at'] = get_somali_time_db()
        if 'sequence' not in event:
            event['sequence'] = 0  # will be set by writer
        self._event_queue.put(event)

    def _writer_loop(self):
        """Background thread: batch writes events to SQLite with retry."""
        batch = []
        last_flush = time.time()
        while self._running and not self._shutdown_event.is_set():
            try:
                item = self._event_queue.get(timeout=1)
                batch.append(item)
            except queue.Empty:
                pass
            now = time.time()
            if len(batch) >= 50 or (batch and now - last_flush >= 2):
                self._flush_events_with_retry(batch)
                batch = []
                last_flush = now
        # Flush any remaining events on shutdown
        if batch:
            self._flush_events_with_retry(batch)

    def _flush_events_with_retry(self, events: List[dict], max_attempts=5):
        """Flush events with exponential backoff and requeue on failure."""
        if not events:
            return
        attempt = 0
        delay = self._event_retry_backoff
        while attempt < max_attempts:
            try:
                self._flush_events(events)
                return  # success
            except Exception as e:
                logger.error(f"Event flush attempt {attempt+1} failed: {e}")
                attempt += 1
                if attempt >= max_attempts:
                    logger.critical(f"Failed to flush events after {max_attempts} attempts. Events lost!")
                    return
                time.sleep(delay)
                delay = min(delay * 2, self._max_retry_delay)

    def _flush_events(self, events: List[dict]):
        """Write events to SQLite in a single transaction."""
        if not events:
            return
        conn = get_db()
        cursor = conn.cursor()
        # Get current max sequence per quiz for ordering
        quiz_ids = set(ev['quiz_id'] for ev in events)
        seq_map = {}
        for qid in quiz_ids:
            cursor.execute(
                "SELECT COALESCE(MAX(sequence), 0) as max_seq FROM live_quiz_events WHERE quiz_id = ?",
                (qid,)
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
                ev.get('created_at', get_somali_time_db())
            ))
        cursor.executemany(sql, params)
        conn.commit()
        logger.debug(f"Flushed {len(events)} events")

    # ---------- Checkpointing ----------

    def _start_checkpoint_timer(self):
        """Start periodic checkpointing."""
        def checkpoint_loop():
            while self._running and not self._shutdown_event.is_set():
                time.sleep(self._checkpoint_interval)
                self._checkpoint_all()
        self._checkpoint_timer = threading.Thread(target=checkpoint_loop, daemon=True)
        self._checkpoint_timer.start()

    def _checkpoint_all(self):
        """Checkpoint all active quizzes that have changed."""
        with self._lock:
            for quiz_id, quiz in list(self._quizzes.items()):
                if quiz.dirty and not quiz._checkpoint_in_progress and not quiz.finalized:
                    self._checkpoint_quiz(quiz)

    def _checkpoint_quiz(self, quiz: QuizState):
        """Write checkpoint data to SQLite, ensuring dirty flag is set only after success."""
        quiz._checkpoint_in_progress = True
        try:
            data = quiz.checkpoint()  # does NOT modify dirty
            payload = json.dumps(data)
            execute_with_retry("""
                INSERT OR REPLACE INTO live_quiz_checkpoints (quiz_id, checkpoint_data, version, created_at)
                VALUES (?, ?, ?, ?)
            """, (quiz.id, payload, data['version'], get_somali_time_db()), commit=True)
            # Only after successful persistence, mark clean
            quiz.mark_clean()
            logger.debug(f"Checkpointed quiz {quiz.id}, version {data['version']}")
        except Exception as e:
            logger.error(f"Checkpoint failed for quiz {quiz.id}: {e}", exc_info=True)
            # dirty remains True, so it will be retried
        finally:
            quiz._checkpoint_in_progress = False

    # ---------- Recovery ----------

    def recover_active_quizzes(self):
        """Load active quizzes from SQLite and restore their state."""
        try:
            # Get quizzes that are not finished
            cursor = execute_with_retry(
                "SELECT id, title, subject_code, question_count, status, join_code, "
                "max_participants, time_per_question, question_ids, started_at, ended_at, created_at "
                "FROM live_quizzes "
                "WHERE status IN ('waiting', 'scheduled', 'active')"
            )
            rows = cursor.fetchall()
            for row in rows:
                quiz_meta = dict(row)
                quiz_id = quiz_meta['id']
                question_ids = json.loads(quiz_meta['question_ids']) if quiz_meta['question_ids'] else []
                questions_cache = self._load_questions(question_ids)

                # Try to load checkpoint
                cp_cursor = execute_with_retry(
                    "SELECT checkpoint_data, version FROM live_quiz_checkpoints WHERE quiz_id = ? ORDER BY version DESC LIMIT 1",
                    (quiz_id,)
                )
                cp_row = cp_cursor.fetchone()

                if cp_row:
                    cp_data = json.loads(cp_row['checkpoint_data'])
                    quiz = QuizState(quiz_id, quiz_meta, question_ids, questions_cache)
                    quiz.restore_from_checkpoint(cp_data)
                    # Replay events after checkpoint version
                    self._replay_events_after(quiz, cp_data['version'])
                    with self._lock:
                        self._quizzes[quiz_id] = quiz
                    logger.info(f"Recovered quiz {quiz_id} from checkpoint version {cp_data['version']}")
                else:
                    # No checkpoint – create fresh state from SQLite participants
                    quiz = QuizState(quiz_id, quiz_meta, question_ids, questions_cache)
                    self._load_participants_from_db(quiz)
                    quiz._update_leaderboard()
                    quiz.dirty = True
                    with self._lock:
                        self._quizzes[quiz_id] = quiz
                    logger.info(f"Recovered quiz {quiz_id} from participants (no checkpoint)")
        except Exception as e:
            logger.error(f"Recovery error: {e}", exc_info=True)

    def _load_questions(self, question_ids: List[int]) -> Dict[int, dict]:
        """Load question data from SQLite."""
        if not question_ids:
            return {}
        placeholders = ','.join(['?'] * len(question_ids))
        cursor = execute_with_retry(
            f"SELECT id, question_text, options, correct_answer, explanation FROM questions WHERE id IN ({placeholders})",
            question_ids
        )
        rows = cursor.fetchall()
        qdict = {}
        for row in rows:
            q = dict(row)
            q['options'] = json.loads(q['options']) if isinstance(q['options'], str) else q['options']
            qdict[q['id']] = q
        return qdict

    def _load_participants_from_db(self, quiz: QuizState):
        """Load participants from live_quiz_participants table."""
        cursor = execute_with_retry(
            "SELECT student_id, score, current_question_index, correct_count, wrong_count, skipped_count, "
            "answers, ratings, status, is_ready "
            "FROM live_quiz_participants WHERE quiz_id = ?",
            (quiz.id,)
        )
        rows = cursor.fetchall()
        for pr in rows:
            student = execute_with_retry(
                "SELECT first_name, last_name, public_id FROM students WHERE id = ?",
                (pr['student_id'],)
            ).fetchone()
            if student:
                name = f"{student['first_name']} {student['last_name']}".strip()
                public_id = student['public_id']
                p = ParticipantState(
                    user_id=pr['student_id'],
                    name=name,
                    public_id=public_id,
                    score=pr['score'],
                    correct_count=pr['correct_count'],
                    wrong_count=pr['wrong_count'],
                    skipped_count=pr['skipped_count'],
                    current_question_index=pr['current_question_index'],
                    answers=json.loads(pr['answers']) if pr['answers'] else {},
                    ratings=json.loads(pr['ratings']) if pr['ratings'] else {},
                    status=pr['status'],
                    is_ready=bool(pr['is_ready'])
                )
                quiz.participants[pr['student_id']] = p

    def _replay_events_after(self, quiz: QuizState, version: int):
        """
        Replay events after the given version to bring state up to date.
        """
        cursor = execute_with_retry(
            "SELECT * FROM live_quiz_events WHERE quiz_id = ? AND sequence > ? ORDER BY sequence ASC",
            (quiz.id, version)
        )
        rows = cursor.fetchall()
        for row in rows:
            event_type = row['event_type']
            payload = json.loads(row['payload']) if row['payload'] else {}
            user_id = row['user_id']
            question_id = row['question_id']

            if event_type == 'ANSWER':
                p = quiz.participants.get(user_id)
                if p and str(question_id) not in p.answers:
                    answer = payload.get('answer')
                    q_data = quiz.questions_cache.get(question_id)
                    correct = (answer == q_data['correct_answer']) if q_data else False
                    points = 2 if correct else 0
                    p.score += points
                    p.correct_count += 1 if correct else 0
                    p.wrong_count += 0 if correct else 1
                    p.answers[question_id] = {'answer': answer, 'correct': correct, 'skipped': False}
                    p.current_question_index += 1
                    quiz._update_leaderboard()
                    quiz.version += 1
            elif event_type == 'SKIP':
                p = quiz.participants.get(user_id)
                if p and str(question_id) not in p.answers:
                    p.answers[question_id] = {'answer': None, 'correct': False, 'skipped': True}
                    p.skipped_count += 1
                    p.current_question_index += 1
                    quiz.version += 1
            elif event_type == 'RATING':
                p = quiz.participants.get(user_id)
                if p and str(question_id) not in p.ratings:
                    rating = payload.get('rating')
                    p.ratings[question_id] = rating
                    p.current_question_index += 1
                    quiz.version += 1
            elif event_type == 'LEAVE':
                p = quiz.participants.get(user_id)
                if p:
                    p.status = 'left'
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
            # Also handle JOIN event? It's just for logging; no state change needed.
        # After replay, mark dirty if any changes
        quiz.dirty = True
        logger.info(f"Replayed {len(rows)} events for quiz {quiz.id}")

    # ---------- Cleanup finished quizzes ----------
    def cleanup_finished_quizzes(self, max_age_seconds=300):
        """Remove finished quizzes from memory after a grace period."""
        now_ts = time.time()
        with self._lock:
            to_remove = []
            for qid, quiz in self._quizzes.items():
                if quiz.finalized and quiz.ended_at:
                    try:
                        ended_ts = datetime.fromisoformat(quiz.ended_at).timestamp()
                        if now_ts - ended_ts > max_age_seconds:
                            to_remove.append(qid)
                    except Exception:
                        pass
            for qid in to_remove:
                self._quizzes.pop(qid, None)
                logger.info(f"Cleaned up finished quiz {qid} after grace period")
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
                # Start a periodic cleanup thread for finished quizzes
                def cleanup_loop():
                    while True:
                        time.sleep(60)
                        _state_manager.cleanup_finished_quizzes()
                threading.Thread(target=cleanup_loop, daemon=True).start()
    return _state_manager


def initialize_state_manager():
    """Call during app startup to ensure tables exist and start manager."""
    from database import ensure_live_quiz_tables
    ensure_live_quiz_tables()
    get_live_quiz_state_manager()


def recover_active_quizzes():
    """Recover active quizzes from SQLite."""
    manager = get_live_quiz_state_manager()
    manager.recover_active_quizzes()