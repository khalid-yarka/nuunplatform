# cache/worker.py
"""
Background worker for Live Quiz checkpointing and other async tasks.
"""

import time
import json
import logging
import threading
from typing import Dict, Any, Optional

from .manager import get_cache_manager

try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)

# Stream key (used for other updates, not needed for checkpointing)
STREAM_KEY = "live_quiz_updates"
GROUP_NAME = "nuunplatform_workers"
CONSUMER_NAME = f"worker_{threading.get_native_id()}"
BLOCK_MS = 2000
COUNT = 100

# Checkpoint interval (seconds)
CHECKPOINT_INTERVAL = 5  # every 5 seconds

# Redis client
_redis_client = None

def get_redis_client():
    global _redis_client
    if _redis_client is None:
        try:
            from config import Config
            if Config.REDIS_URL:
                _redis_client = redis.Redis.from_url(Config.REDIS_URL)
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            _redis_client = None
    return _redis_client

def _process_stream(redis_client: redis.Redis) -> int:
    """Process stream messages (existing logic)."""
    try:
        # Create consumer group if it doesn't exist
        try:
            redis_client.xgroup_create(
                STREAM_KEY, GROUP_NAME, id='0', mkstream=True
            )
        except redis.exceptions.ResponseError as e:
            if 'BUSYGROUP' not in str(e):
                logger.error(f"Failed to create consumer group: {e}")

        result = redis_client.xreadgroup(
            GROUP_NAME, CONSUMER_NAME,
            {STREAM_KEY: '>'},
            count=COUNT,
            block=BLOCK_MS,
        )

        if not result:
            return 0

        processed = 0
        stream_messages = result[0][1]
        for message_id, fields in stream_messages:
            try:
                entry_data = json.loads(fields[b'data'].decode('utf-8'))
            except (KeyError, ValueError) as e:
                logger.error(f"Invalid message format: {e}")
                redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)
                continue

            # Process entry (existing logic)
            # For now, we just acknowledge (extend as needed)
            redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)
            processed += 1

        return processed
    except Exception as e:
        logger.error(f"Stream processing error: {e}")
        return 0

def _checkpoint_active_quizzes(redis_client: redis.Redis):
    """Find all active quizzes and checkpoint their state to SQLite."""
    try:
        pattern = "livequiz:participant:*"
        quizzes = set()
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=100)
            for key in keys:
                parts = key.decode('utf-8').split(':')
                if len(parts) >= 3:
                    quiz_id = int(parts[2])
                    quizzes.add(quiz_id)
            if cursor == 0:
                break

        from redis_state import LiveQuizState
        state = LiveQuizState(redis_client)
        for qid in quizzes:
            from db import get_live_quiz_by_id
            quiz = get_live_quiz_by_id(qid)
            if quiz and quiz['status'] == 'active':
                state.checkpoint(qid)
                logger.debug(f"Checkpointed quiz {qid}")
    except Exception as e:
        logger.error(f"Error in checkpointing: {e}")

def _worker_loop(stop_event: threading.Event):
    """Main worker loop: process streams and checkpoint."""
    logger.info("Cache worker started")
    redis_client = get_redis_client()
    if not redis_client:
        logger.error("Redis not available, worker cannot run.")
        return

    last_checkpoint = time.time()

    while not stop_event.is_set():
        # Process stream (existing logic)
        _process_stream(redis_client)

        # Checkpoint every CHECKPOINT_INTERVAL seconds
        now = time.time()
        if now - last_checkpoint >= CHECKPOINT_INTERVAL:
            _checkpoint_active_quizzes(redis_client)
            last_checkpoint = now

        # Sleep a bit
        time.sleep(1)

    logger.info("Cache worker stopped")

# Global worker management
_worker_thread = None
_worker_stop_event = None
_worker_started = False
_worker_lock = threading.Lock()

def start_worker():
    global _worker_thread, _worker_stop_event, _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_stop_event = threading.Event()
        _worker_thread = threading.Thread(
            target=_worker_loop,
            args=(_worker_stop_event,),
            daemon=True,
            name="cache_worker"
        )
        _worker_thread.start()
        _worker_started = True
        logger.info("Cache worker thread started")

def stop_worker():
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            return
        if _worker_stop_event:
            _worker_stop_event.set()
        if _worker_thread and _worker_thread.is_alive():
            _worker_thread.join(timeout=5)
        _worker_started = False
        logger.info("Cache worker stopped")