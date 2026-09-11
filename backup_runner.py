#!/usr/bin/env python3
# backup_runner.py
import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Import config and db
from config import Config
from db import execute_with_retry
from backup import BackupManager, acquire_backup_lock, release_backup_lock, is_backup_locked

LOG_DIR = Config.LOG_DIR
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, 'backup_runner.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def get_scheduled_config():
    """Read schedule from backup_config table."""
    try:
        cursor = execute_with_retry(
            "SELECT scheduled_enabled, scheduled_type, scheduled_time FROM backup_config WHERE id = 1"
        )
        row = cursor.fetchone()
        if row and row['scheduled_enabled'] == 1:
            return {
                'enabled': True,
                'type': row['scheduled_type'],
                'time': row['scheduled_time']
            }
        return {'enabled': False}
    except Exception as e:
        logger.error(f"Failed to read backup config: {e}")
        return {'enabled': False}


def should_run_now(scheduled_time):
    """Check if current time matches scheduled_time (HH:MM)."""
    try:
        now = datetime.now().strftime('%H:%M')
        return now == scheduled_time
    except Exception:
        return False


def run_backup(backup_type='daily'):
    """Run a backup and log."""
    start_time = time.time()
    logger.info(f"Starting scheduled {backup_type} backup...")

    try:
        manager = BackupManager()
        if is_backup_locked():
            logger.warning("Backup already running, skipping")
            return False

        lock_fd = acquire_backup_lock()
        if lock_fd is None:
            logger.error("Could not acquire backup lock")
            return False

        try:
            result = manager.create_backup(backup_type)
            duration = time.time() - start_time

            if result['success']:
                logger.info(
                    f"✅ Backup successful: {result['filename']} "
                    f"({result['size_bytes'] / 1024:.2f} KB) "
                    f"in {duration:.2f}s"
                )
                # Run maintenance
                maint_result = manager.run_maintenance(dry_run=False)
                if maint_result['deleted']:
                    logger.info(f"Cleaned {len(maint_result['deleted'])} old backups")
                return True
            else:
                logger.error(f"❌ Backup failed: {result['message']}")
                return False

        finally:
            release_backup_lock(lock_fd)

    except ImportError as e:
        logger.error(f"Backup import error: {e}")
        return False
    except Exception as e:
        logger.error(f"Backup error: {e}", exc_info=True)
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description='NUUNPLATFORM Backup Runner')
    parser.add_argument('--type', default='daily',
                        choices=['daily', 'weekly', 'monthly', 'manual'],
                        help='Type of backup to run (ignored if scheduled)')
    parser.add_argument('--check', action='store_true',
                        help='Check backup health and exit')
    parser.add_argument('--run-scheduled', action='store_true',
                        help='Run only if scheduled and time matches')
    args = parser.parse_args()

    if args.check:
        # Health check...
        from backup import BackupManager
        manager = BackupManager()
        health = manager.health_check()
        print(f"Backup System Health: {health['status']}")
        sys.exit(0)

    if args.run_scheduled:
        config = get_scheduled_config()
        if not config['enabled']:
            logger.info("Scheduled backups are disabled.")
            sys.exit(0)
        if not should_run_now(config['time']):
            logger.info("Scheduled time not reached, exiting.")
            sys.exit(0)
        backup_type = config['type']
        success = run_backup(backup_type)
        sys.exit(0 if success else 1)
    else:
        # Manual run
        success = run_backup(args.type)
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()