# activity_logger.py
import time
import json
import threading
import queue
import logging
from flask import request, session, current_app
from typing import Optional, Dict, Any
from db import execute_with_retry
from utils import get_somali_time_db

logger = logging.getLogger(__name__)

_activity_queue = queue.Queue()
_worker_running = False
BATCH_SIZE = 50
FLUSH_INTERVAL = 2

# Reference to the Flask app (set when initializing)
_app = None

def init_activity_logger(app):
    """Initialize the activity logger with the Flask app instance."""
    global _app
    _app = app
    _ensure_worker()

def log_activity(
    activity_type: str,
    message: str,
    severity: str = 'info',
    user_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
) -> None:
    if user_id is None:
        user_id = session.get('user_id')
    if ip_address is None and request:
        ip_address = request.remote_addr
    if user_agent is None and request:
        user_agent = request.headers.get('User-Agent', '')
    if session_id is None:
        session_id = session.get('_id')

    entry = {
        'user_id': user_id,
        'session_id': session_id,
        'activity_type': activity_type,
        'severity': severity,
        'message': message[:500],
        'metadata': json.dumps(metadata) if metadata else None,
        'ip_address': ip_address[:45] if ip_address else None,
        'user_agent': user_agent[:200] if user_agent else None,
        'created_at': get_somali_time_db()
    }
    _activity_queue.put(entry)
    _ensure_worker()

def _ensure_worker():
    global _worker_running
    if not _worker_running:
        _worker_running = True
        threading.Thread(target=_worker_loop, daemon=True).start()
        logger.info("Activity logger worker started.")

def _worker_loop():
    batch = []
    last_flush = time.time()
    while True:
        try:
            item = _activity_queue.get(timeout=1)
            batch.append(item)
        except queue.Empty:
            pass
        now = time.time()
        if len(batch) >= BATCH_SIZE or (batch and (now - last_flush) >= FLUSH_INTERVAL):
            _flush_batch(batch)
            batch = []
            last_flush = now

def _flush_batch(batch):
    if not batch:
        return
    # Ensure we run within the application context
    global _app
    if _app is None:
        logger.error("Activity logger not initialized with app context. Call init_activity_logger(app).")
        return

    try:
        with _app.app_context():
            from db import get_db
            conn = get_db()
            cursor = conn.cursor()
            sql = """
                INSERT INTO activity_logs (
                    user_id, session_id, activity_type, severity,
                    message, metadata, ip_address, user_agent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            params = [(e['user_id'], e['session_id'], e['activity_type'], e['severity'],
                       e['message'], e['metadata'], e['ip_address'], e['user_agent'], e['created_at']) for e in batch]
            cursor.executemany(sql, params)
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to flush activity batch: {e}")

# Convenience wrappers
def log_admin_action(action: str, details: str = None, severity: str = 'warning', **extra):
    meta = {'admin_action': action}
    if details:
        meta['details'] = details
    meta.update(extra)
    log_activity('admin', f"Admin action: {action}", severity, metadata=meta)

def log_quiz_complete(user_id: int, subject_id: int, score: int, total: int):
    log_activity('quiz.complete', f"Completed quiz on subject {subject_id} with {score}/{total}",
                 'info', user_id, {'subject_id': subject_id, 'score': score, 'total': total})

def log_backup_event(operation: str, filename: str = None, status: str = 'success', message: str = None):
    log_activity('backup', f"Backup {operation} {status}", 'info' if status == 'success' else 'critical',
                 metadata={'operation': operation, 'filename': filename, 'status': status})