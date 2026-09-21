# history_logger.py
# Crash-safe append-only JSONL queue flushed in bulk to history_entries.
#
# Locking:
#   fcntl.flock is attempted first. On filesystems that don't implement
#   it (Android FUSE — errno 38 ENOSYS, ENOLCK, EOPNOTSUPP), the lock is
#   skipped and the module falls back to atomic-rename semantics, which
#   are safe for a single worker process.

import os
import json
import time
import errno
import logging
import fcntl
from datetime import datetime, timezone, timedelta

from config import Config
from db import execute_many_with_retry, ensure_question_miss_stats_table  # noqa: F401

logger = logging.getLogger(__name__)

# ============================================
# PATHS
# ============================================

_QUEUE_FILE = os.environ.get(
    'HISTORY_QUEUE_FILE',
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 'history_queue.jsonl')
)
_PROCESSING_FILE = _QUEUE_FILE + '.processing'
_ERROR_FILE = _QUEUE_FILE + '.error'

SOMALI_TZ = timezone(timedelta(hours=3))

# _FLOCK_UNSUPPORTED is set True the first time flock fails with
# a "not implemented" style error, so we stop trying on subsequent calls.
_FLOCK_UNSUPPORTED = False


def _now_iso():
    return datetime.now(SOMALI_TZ).isoformat()


# ============================================
# LOCKING (best-effort)
# ============================================

def _try_lock(fd, op):
    """
    Attempt an flock. Returns True if locked, False if the OS doesn't
    support flock (and sets _FLOCK_UNSUPPORTED to short-circuit future
    attempts).
    """
    global _FLOCK_UNSUPPORTED
    if _FLOCK_UNSUPPORTED:
        return False
    try:
        fcntl.flock(fd, op)
        return True
    except OSError as e:
        # ENOLCK=37, ENOSYS=38, EOPNOTSUPP=95, EINVAL=22
        if e.errno in (errno.ENOLCK, errno.ENOSYS,
                       getattr(errno, 'EOPNOTSUPP', 95), errno.EINVAL):
            _FLOCK_UNSUPPORTED = True
            logger.info(
                "history_logger: flock not supported on this filesystem "
                "(errno %s). Running without file lock.", e.errno
            )
            return False
        raise


def _try_unlock(fd):
    if _FLOCK_UNSUPPORTED:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


# ============================================
# PUBLIC API
# ============================================

def add_history_entry(user_id, entry_type, action, entry_id=None, metadata=None):
    """
    Append an entry to the queue file. Never raises.
    The entry is flushed in the background or on next flush call.
    """
    entry = {
        'user_id': user_id,
        'entry_type': entry_type,
        'action': action,
        'entry_id': entry_id,
        'metadata': metadata or {},
        'created_at': _now_iso(),
    }

    try:
        with open(_QUEUE_FILE, 'a', encoding='utf-8') as f:
            _try_lock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            finally:
                _try_unlock(f.fileno())
        return True
    except Exception as e:
        logger.error(f"add_history_entry failed: {e}")
        return False


def flush_history_queue(limit=None):
    """
    Move the queue to .processing, then bulk insert. Returns a status dict.
    Safe to call from any thread; a concurrent caller may find the queue
    already moved and will return a zero-count status.
    """
    status = {'flushed': 0, 'failed': 0, 'skipped': 0, 'error': None}

    if not os.path.exists(_QUEUE_FILE):
        return status

    try:
        os.replace(_QUEUE_FILE, _PROCESSING_FILE)
    except FileNotFoundError:
        return status
    except Exception as e:
        status['error'] = f"rename failed: {e}"
        logger.error(f"flush_history_queue rename failed: {e}")
        return status

    entries = []
    bad_lines = 0
    try:
        with open(_PROCESSING_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    bad_lines += 1
    except Exception as e:
        status['error'] = f"read failed: {e}"
        logger.error(f"flush_history_queue read failed: {e}")
        return status

    if limit and len(entries) > limit:
        # Push the rest back to the queue file for next flush
        overflow = entries[limit:]
        entries = entries[:limit]
        try:
            with open(_QUEUE_FILE, 'a', encoding='utf-8') as f:
                for e in overflow:
                    f.write(json.dumps(e, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.error(f"flush overflow requeue failed: {e}")

    params = []
    for e in entries:
        try:
            params.append((
                int(e['user_id']),
                str(e['entry_type']),
                str(e['action']),
                e.get('entry_id'),
                json.dumps(e.get('metadata') or {}, ensure_ascii=False),
                e.get('created_at') or _now_iso(),
            ))
        except Exception:
            status['skipped'] += 1

    if params:
        try:
            execute_many_with_retry(
                "INSERT INTO history_entries "
                "(user_id, entry_type, action, entry_id, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                params,
                commit=True,
                operation_name=f"flush_history_queue({len(params)})",
            )
            status['flushed'] = len(params)
        except Exception as e:
            status['failed'] = len(params)
            status['error'] = str(e)
            logger.error(f"flush_history_queue insert failed: {e}")
            # Push everything back to the queue file so nothing is lost
            try:
                with open(_QUEUE_FILE, 'a', encoding='utf-8') as f:
                    for e in entries:
                        f.write(json.dumps(e, ensure_ascii=False) + '\n')
            except Exception as eq:
                logger.error(f"flush requeue after failure: {eq}")

    # Archive bad lines so they aren't lost silently
    if bad_lines:
        try:
            with open(_ERROR_FILE, 'a', encoding='utf-8') as f:
                f.write(f"# {_now_iso()} — {bad_lines} unparseable line(s)\n")
        except Exception:
            pass

    # Clean up the processing file
    try:
        os.remove(_PROCESSING_FILE)
    except Exception:
        pass

    if status['flushed']:
        logger.info(f"history_logger: flushed {status['flushed']} entries")

    return status


def force_flush_queue():
    return flush_history_queue()


def recover_pending_entries():
    """
    If a previous flush was interrupted, .processing may still exist.
    Merge it back into the queue file.
    """
    if not os.path.exists(_PROCESSING_FILE):
        return 0
    try:
        count = 0
        with open(_PROCESSING_FILE, 'r', encoding='utf-8') as src, \
             open(_QUEUE_FILE, 'a', encoding='utf-8') as dst:
            for line in src:
                dst.write(line)
                count += 1
        os.remove(_PROCESSING_FILE)
        if count:
            logger.info(f"history_logger: recovered {count} entries")
        return count
    except Exception as e:
        logger.error(f"recover_pending_entries failed: {e}")
        return 0


def get_queue_stats():
    stats = {'pending': 0, 'processing': 0, 'errors': 0, 'queue_bytes': 0}
    for path, key in [
        (_QUEUE_FILE, 'pending'),
        (_PROCESSING_FILE, 'processing'),
        (_ERROR_FILE, 'errors'),
    ]:
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    stats[key] = sum(1 for _ in f if _.strip())
                if key == 'pending':
                    stats['queue_bytes'] = os.path.getsize(path)
        except Exception:
            pass
    return stats