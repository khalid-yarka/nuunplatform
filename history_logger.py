# history_logger.py – Fixed flush with safe rename and batch insert (execute_many_with_retry)

import os
import json
import fcntl
import logging
import shutil
from typing import Dict, Any, Optional, List
from datetime import datetime
from pathlib import Path

from db import execute_many_with_retry, get_somali_time_db

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
QUEUE_FILE = os.getenv('HISTORY_QUEUE_FILE') or str(BASE_DIR / 'history_queue.jsonl')
PROCESSING_FILE = QUEUE_FILE + '.processing'
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


def add_history_entry(
    user_id: int,
    entry_type: str,
    action: str,
    entry_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """Append a history entry to the queue file."""
    if not entry_type or not action:
        return
    entry = {
        'user_id': user_id,
        'entry_type': entry_type,
        'action': action,
        'entry_id': entry_id,
        'metadata': json.dumps(metadata or {}),
        'created_at': get_somali_time_db()
    }
    try:
        os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
        with open(QUEUE_FILE, 'a') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(entry) + '\n')
            f.flush()
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        logger.error(f"Write error: {e}")


def flush_history_queue(limit: Optional[int] = None) -> Dict[str, Any]:
    """
    Flush the queue file to the database using a safe rename-then-process approach.
    Returns a dict with flush status.
    """
    result = {
        'success': False,
        'flushed': 0,
        'errors': [],
        'skipped': 0,
        'file_size_before': 0,
        'file_size_after': 0
    }

    if not os.path.exists(QUEUE_FILE):
        result['success'] = True
        return result

    result['file_size_before'] = os.path.getsize(QUEUE_FILE)

    # Rename the queue file to a processing file atomically
    try:
        with open(QUEUE_FILE, 'r') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            os.rename(QUEUE_FILE, PROCESSING_FILE)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        result['errors'].append(f"Failed to rename queue file: {e}")
        return result

    # Process the renamed file
    try:
        with open(PROCESSING_FILE, 'r') as f:
            lines = f.readlines()

        if not lines:
            os.remove(PROCESSING_FILE)
            result['success'] = True
            result['file_size_after'] = 0
            return result

        logger.info(f"Flushing {len(lines)} lines from queue file")

        entries = []
        malformed_lines = []

        for idx, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                user_id = entry.get('user_id')
                entry_type = entry.get('entry_type')
                action = entry.get('action')
                metadata = entry.get('metadata', '{}')
                created_at = entry.get('created_at')
                entry_id = entry.get('entry_id')

                if user_id is not None and entry_type and action and metadata and created_at:
                    tup = (user_id, entry_type, action, entry_id, metadata, created_at)
                    # Ensure tuple length is exactly 6
                    if len(tup) != 6:
                        logger.error(f"Line {idx} tuple length {len(tup)}, forcing to 6")
                        tup = tup[:6] + (None,) * (6 - len(tup))
                    entries.append(tup)
                else:
                    malformed_lines.append(line)
                    result['skipped'] += 1
                    logger.warning(f"Line {idx} missing required fields")
            except json.JSONDecodeError:
                malformed_lines.append(line)
                result['skipped'] += 1
                logger.warning(f"Line {idx} invalid JSON")

        if not entries:
            if malformed_lines:
                error_file = QUEUE_FILE + '.error'
                with open(error_file, 'a') as ef:
                    ef.write('\n'.join(malformed_lines) + '\n')
                logger.warning(f"Saved {len(malformed_lines)} malformed lines to {error_file}")
            os.remove(PROCESSING_FILE)
            result['success'] = True
            result['file_size_after'] = 0
            return result

        if limit and len(entries) > limit:
            entries = entries[:limit]

        # Final sanity check: all tuples must have length 6
        for i, t in enumerate(entries):
            if len(t) != 6:
                logger.warning(f"Entry {i} length {len(t)}, padding")
                entries[i] = t[:6] + (None,) * (6 - len(t))

        # Bulk insert using executemany (FIXED)
        try:
            execute_many_with_retry(
                """
                INSERT INTO history_entries
                (user_id, entry_type, action, entry_id, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                entries,
                commit=True,
                operation_name='history_flush'
            )
            result['flushed'] = len(entries)
            logger.info(f"Inserted {len(entries)} entries")
        except Exception as e:
            error_msg = str(e)
            logger.error(f"DB insert failed: {error_msg}")
            if entries:
                logger.error(f"First tuple: {entries[0]} (length {len(entries[0])})")
            result['errors'].append(error_msg)
            # Rename processing file back to queue file for retry
            os.rename(PROCESSING_FILE, QUEUE_FILE)
            result['file_size_after'] = result['file_size_before']
            return result

        # Save malformed lines to error file
        if malformed_lines:
            error_file = QUEUE_FILE + '.error'
            with open(error_file, 'a') as ef:
                ef.write('\n'.join(malformed_lines) + '\n')
            logger.warning(f"Saved {len(malformed_lines)} malformed lines to {error_file}")

        os.remove(PROCESSING_FILE)
        result['success'] = True
        result['file_size_after'] = 0

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Flush exception: {error_msg}")
        result['errors'].append(error_msg)
        if os.path.exists(PROCESSING_FILE) and not os.path.exists(QUEUE_FILE):
            os.rename(PROCESSING_FILE, QUEUE_FILE)
        result['file_size_after'] = result['file_size_before'] if os.path.exists(QUEUE_FILE) else 0

    return result


def force_flush_queue() -> Dict[str, Any]:
    return flush_history_queue()


def recover_pending_entries() -> Dict[str, Any]:
    if os.path.exists(QUEUE_FILE) and os.path.getsize(QUEUE_FILE) > 0:
        logger.info("Recovering pending entries on startup")
        return flush_history_queue()
    return {'success': True, 'flushed': 0, 'errors': [], 'skipped': 0, 'file_size_before': 0, 'file_size_after': 0}


def get_queue_stats() -> dict:
    if os.path.exists(QUEUE_FILE):
        size = os.path.getsize(QUEUE_FILE)
        with open(QUEUE_FILE, 'r') as f:
            line_count = sum(1 for _ in f)
        return {'exists': True, 'size_bytes': size, 'line_count': line_count}
    return {'exists': False, 'size_bytes': 0, 'line_count': 0}