#!/usr/bin/env python3
# ============================================
# NUUNPLATFORM BACKUP SYSTEM v2.1
# ============================================
# Enhanced with absolute paths and improved health checking
# ============================================

import os
import sys
import sqlite3
import json
import gzip
import shutil
import hashlib
import time
import argparse
import logging
import fcntl
import tempfile
#import readline
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# ============================================
# CONFIGURATION - Absolute Paths
# ============================================

VERSION = "2.1.0"
APP_NAME = "NUUNPLATFORM BACKUP SYSTEM"

# Get base directory
BASE_DIR = Path(__file__).resolve().parent

# Import Config – no fallback, use directly
from config import Config

# Use Config values directly
DEFAULT_DB_PATH = Config.DATABASE_PATH
DEFAULT_BACKUP_DIR = Config.BACKUP_DIR
LOG_DIR = Config.LOG_DIR

# Retention settings from Config
RETENTION = {
    'daily': Config.BACKUP_RETENTION_DAILY,
    'weekly': Config.BACKUP_RETENTION_WEEKLY,
    'monthly': Config.BACKUP_RETENTION_MONTHLY,
    'manual': None,
}

# Ensure directories exist
for directory in [DEFAULT_BACKUP_DIR, LOG_DIR]:
    if not os.path.exists(directory):
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception:
            pass

DEFAULT_LOG_FILE = os.path.join(LOG_DIR, 'backup.log')
BACKUP_LOCK_FILE = os.path.join(DEFAULT_BACKUP_DIR, 'backup.lock')

BACKUP_TYPES = {
    'daily': {'label': 'Daily', 'retention': RETENTION['daily'], 'emoji': '📅'},
    'weekly': {'label': 'Weekly', 'retention': RETENTION['weekly'], 'emoji': '📆'},
    'monthly': {'label': 'Monthly', 'retention': RETENTION['monthly'], 'emoji': '📊'},
    'manual': {'label': 'Manual', 'retention': None, 'emoji': '👤'},
}

# ANSI color codes
COLOR_GREEN = '\033[92m'
COLOR_RED = '\033[91m'
COLOR_YELLOW = '\033[93m'
COLOR_BLUE = '\033[94m'
COLOR_CYAN = '\033[96m'
COLOR_MAGENTA = '\033[95m'
COLOR_WHITE = '\033[97m'
COLOR_RESET = '\033[0m'
COLOR_BOLD = '\033[1m'
COLOR_UNDERLINE = '\033[4m'
COLOR_DIM = '\033[2m'

# ============================================
# FEATURE 1: BACKUP LOCKING SYSTEM
# ============================================

def acquire_backup_lock(lock_file=BACKUP_LOCK_FILE, timeout=30):
    """Acquire exclusive lock for backup."""
    try:
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)
        fd = open(lock_file, 'w')
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fd.write(str(os.getpid()))
                fd.flush()
                return fd
            except (IOError, OSError):
                time.sleep(1)
        return None
    except Exception as e:
        logging.getLogger(__name__).error(f"Failed to acquire backup lock: {e}")
        return None

def release_backup_lock(fd):
    """Release backup lock."""
    if fd:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to release backup lock: {e}")

def is_backup_locked(lock_file=BACKUP_LOCK_FILE):
    """Check if backup is currently locked."""
    try:
        fd = open(lock_file, 'r')
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            return False
        except (IOError, OSError):
            return True
        finally:
            fd.close()
    except FileNotFoundError:
        return False
    except Exception:
        return False

# ============================================
# FEATURE 2: LOGGING SETUP
# ============================================

def setup_logging(verbose=False, log_file=None):
    """Configure logging for backup operations."""
    handlers = []
    log_path = log_file or DEFAULT_LOG_FILE
    
    # Ensure log directory exists
    try:
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass
    
    try:
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            '%Y-%m-%d %H:%M:%S'
        ))
        handlers.append(file_handler)
    except Exception as e:
        # Fallback to basic file handler
        try:
            file_handler = logging.FileHandler(log_path)
            file_handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s',
                '%Y-%m-%d %H:%M:%S'
            ))
            handlers.append(file_handler)
        except Exception:
            pass

    if verbose or not handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            '%Y-%m-%d %H:%M:%S'
        ))
        handlers.append(stream_handler)

    if not handlers:
        logging.basicConfig(level=logging.INFO)
        return logging.getLogger(__name__)

    logging.basicConfig(
        level=logging.INFO if not verbose else logging.DEBUG,
        handlers=handlers
    )
    return logging.getLogger(__name__)

# ============================================
# COLOR HELPERS
# ============================================

def green(text): return COLOR_GREEN + text + COLOR_RESET
def red(text): return COLOR_RED + text + COLOR_RESET
def yellow(text): return COLOR_YELLOW + text + COLOR_RESET
def cyan(text): return COLOR_CYAN + text + COLOR_RESET
def magenta(text): return COLOR_MAGENTA + text + COLOR_RESET
def blue(text): return COLOR_BLUE + text + COLOR_RESET
def bold(text): return COLOR_BOLD + text + COLOR_RESET
def underline(text): return COLOR_UNDERLINE + text + COLOR_RESET
def dim(text): return COLOR_DIM + text + COLOR_RESET

# ============================================
# BACKUP MANAGER CLASS
# ============================================

class BackupManager:
    """Main backup management system with enhanced health checking."""

    def __init__(self, db_path: str = None, backup_dir: str = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.backup_dir = backup_dir or DEFAULT_BACKUP_DIR
        self.manifest_path = os.path.join(self.backup_dir, 'manifest.json')
        self.last_good_link = os.path.join(self.backup_dir, 'LAST_KNOWN_GOOD')
        self.temp_dir = os.path.join(self.backup_dir, 'temp')
        self.version = VERSION

        # Ensure directories exist
        os.makedirs(self.backup_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)

        self.manifest = self._load_manifest()
        self.logger = logging.getLogger(__name__)

    # ============================================
    # MANIFEST MANAGEMENT
    # ============================================

    def _load_manifest(self) -> Dict:
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                self.logger.warning("Manifest corrupted, creating new.")
                return self._create_empty_manifest()
        return self._create_empty_manifest()

    def _create_empty_manifest(self) -> Dict:
        return {
            'backups': [],
            'last_good': None,
            'last_created': None,
            'total_backups': 0,
            'valid_backups': 0,
            'version': '1.0',
            'created_at': datetime.now().isoformat()
        }

    def _save_manifest(self):
        temp_path = os.path.join(self.temp_dir, 'manifest_temp.json')
        with open(temp_path, 'w') as f:
            json.dump(self.manifest, f, indent=2)
        shutil.move(temp_path, self.manifest_path)

    # ============================================
    # CHECKSUM GENERATION
    # ============================================

    @staticmethod
    def calculate_checksum(file_path: str) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                sha256.update(chunk)
        return sha256.hexdigest()

    # ============================================
    # DATABASE INTEGRITY VERIFICATION
    # ============================================

    @staticmethod
    def verify_database_integrity(db_path: str) -> Tuple[bool, str]:
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()
            conn.close()
            if result and result[0] == 'ok':
                return True, "ok"
            return False, result[0] if result else "unknown error"
        except Exception as e:
            return False, str(e)

    # ============================================
    # TABLE STRUCTURE VERIFICATION
    # ============================================

    @staticmethod
    def verify_tables_exist(db_path: str, required_tables=None) -> bool:
        if required_tables is None:
            required_tables = ['students', 'questions', 'subjects', 'quiz_attempts']
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            conn.close()
            for table in required_tables:
                if table not in tables:
                    return False
            return True
        except Exception:
            return False

    # ============================================
    # BACKUP VERIFICATION
    # ============================================

    def verify_backup(self, backup_path: str, skip_checksum: bool = False) -> Tuple[bool, Dict]:
        details = {
            'integrity': False,
            'tables': False,
            'checksum_match': False,
            'size_bytes': 0,
            'error': None,
            'verified_at': datetime.now().isoformat()
        }

        if not os.path.exists(backup_path):
            details['error'] = "File not found"
            return False, details

        details['size_bytes'] = os.path.getsize(backup_path)

        temp_db = os.path.join(self.temp_dir, f"verify_{int(time.time())}.db")
        try:
            with gzip.open(backup_path, 'rb') as f_in:
                with open(temp_db, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)

            integrity_ok, msg = self.verify_database_integrity(temp_db)
            details['integrity'] = integrity_ok
            details['integrity_msg'] = msg

            if not integrity_ok:
                details['error'] = f"Integrity check failed: {msg}"
                os.remove(temp_db)
                return False, details

            tables_ok = self.verify_tables_exist(temp_db)
            details['tables'] = tables_ok
            if not tables_ok:
                details['error'] = "Required tables missing"
                os.remove(temp_db)
                return False, details

            if not skip_checksum:
                entry = self._find_backup_entry(backup_path)
                if entry and 'checksum' in entry:
                    current_checksum = self.calculate_checksum(backup_path)
                    if current_checksum == entry['checksum']:
                        details['checksum_match'] = True
                    else:
                        details['checksum_match'] = False
                        details['error'] = "Checksum mismatch"
                        os.remove(temp_db)
                        return False, details

            os.remove(temp_db)
            return True, details

        except Exception as e:
            details['error'] = str(e)
            if os.path.exists(temp_db):
                os.remove(temp_db)
            return False, details

    def _find_backup_entry(self, backup_path: str) -> Optional[Dict]:
        filename = os.path.basename(backup_path)
        for entry in self.manifest['backups']:
            if entry['filename'] == filename:
                return entry
        return None

    # ============================================
    # BACKUP CREATION
    # ============================================

    def create_backup(self, backup_type: str = 'daily') -> Dict:
        start_time = time.time()
        result = {
            'success': False,
            'filename': None,
            'message': '',
            'verified': False,
            'size_bytes': 0,
            'checksum': None,
            'duration': 0,
            'type': backup_type
        }

        try:
            if backup_type not in BACKUP_TYPES:
                result['message'] = f"Invalid backup type: {backup_type}"
                return result

            if not os.path.exists(self.db_path):
                result['message'] = f"Database not found: {self.db_path}"
                return result

            if not self._check_disk_space():
                result['message'] = "Insufficient disk space"
                return result

            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M')
            if backup_type == 'manual':
                filename = f"manual_{timestamp}.db.gz"
            else:
                filename = f"{backup_type}_{timestamp}.db.gz"
            full_path = os.path.join(self.backup_dir, filename)
            temp_backup = os.path.join(self.temp_dir, f"backup_TEMP_{timestamp}.db")
            temp_gz = os.path.join(self.temp_dir, f"backup_TEMP_{timestamp}.db.gz")

            self.logger.info(f"Creating {backup_type} backup...")
            
            # Use WAL mode for consistent backup
            source_conn = sqlite3.connect(self.db_path, timeout=30)
            source_conn.execute("PRAGMA journal_mode = WAL")
            
            # Create backup
            dest_conn = sqlite3.connect(temp_backup, timeout=30)
            source_conn.backup(dest_conn)
            dest_conn.commit()
            source_conn.close()
            dest_conn.close()

            self.logger.info("Compressing backup...")
            with open(temp_backup, 'rb') as f_in:
                with gzip.open(temp_gz, 'wb', compresslevel=6) as f_out:
                    shutil.copyfileobj(f_in, f_out)

            self.logger.info("Verifying compressed backup...")
            ver_ok, ver_details = self.verify_backup(temp_gz, skip_checksum=True)
            if not ver_ok:
                result['message'] = f"Verification failed: {ver_details.get('error')}"
                os.remove(temp_gz)
                os.remove(temp_backup)
                return result

            checksum = self.calculate_checksum(temp_gz)
            shutil.move(temp_gz, full_path)
            os.remove(temp_backup)

            entry = {
                'filename': filename,
                'type': backup_type,
                'timestamp': datetime.now().isoformat(),
                'size_bytes': os.path.getsize(full_path),
                'checksum': checksum,
                'integrity_status': 'valid',
                'integrity_result': 'ok',
                'tables_verified': True,
                'verification_timestamp': datetime.now().isoformat(),
                'creation_duration_seconds': round(time.time() - start_time, 2),
                'notes': ''
            }
            self.manifest['backups'].append(entry)
            self.manifest['total_backups'] += 1
            self.manifest['valid_backups'] += 1
            self.manifest['last_created'] = entry['timestamp']
            self.manifest['last_good'] = filename

            self._update_last_good_link(full_path)
            self._save_manifest()

            result['success'] = True
            result['filename'] = filename
            result['message'] = f"{backup_type.capitalize()} backup created successfully"
            result['verified'] = True
            result['size_bytes'] = entry['size_bytes']
            result['checksum'] = checksum
            result['duration'] = entry['creation_duration_seconds']

            self.logger.info(f"Backup created: {filename} ({result['duration']}s)")
            return result

        except Exception as e:
            self.logger.error(f"Backup creation failed: {e}", exc_info=True)
            result['message'] = str(e)
            for f in [temp_backup, temp_gz]:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except:
                        pass
            return result

    # ============================================
    # DISK SPACE MANAGEMENT
    # ============================================

    def _check_disk_space(self) -> bool:
        try:
            stat = os.statvfs(self.backup_dir)
            free_bytes = stat.f_frsize * stat.f_bavail
            free_mb = free_bytes / (1024 * 1024)
            if free_mb < 50:
                self.logger.warning(f"Low disk space: {free_mb:.2f} MB free")
                return False
            return True
        except Exception:
            return True

    # ============================================
    # LAST KNOWN GOOD LINK
    # ============================================

    def _update_last_good_link(self, backup_path: str):
        try:
            if os.path.exists(self.last_good_link) or os.path.islink(self.last_good_link):
                os.unlink(self.last_good_link)
            rel_path = os.path.relpath(backup_path, os.path.dirname(self.last_good_link))
            os.symlink(rel_path, self.last_good_link)
        except Exception as e:
            self.logger.warning(f"Could not update LAST_KNOWN_GOOD link: {e}")

    # ============================================
    # BACKUP RESTORE
    # ============================================

    def restore_latest(self, force: bool = False) -> Dict:
        latest = self.get_latest_valid_backup()
        if not latest:
            return {'success': False, 'message': 'No valid backup found'}
        return self.restore_backup(latest, force=force)

    def restore_backup(self, filename: str, force: bool = False) -> Dict:
        result = {
            'success': False,
            'message': '',
            'backup_verified': False,
            'safety_copy_created': False,
            'temp_restore_verified': False
        }

        if os.environ.get('BACKUP_NON_INTERACTIVE') and not force:
            result['message'] = "Non-interactive mode: use --force to restore"
            return result

        backup_path = os.path.join(self.backup_dir, filename)
        if not os.path.exists(backup_path):
            result['message'] = f"Backup file not found: {filename}"
            return result

        self.logger.info(f"Verifying backup: {filename}")
        ver_ok, ver_details = self.verify_backup(backup_path)
        if not ver_ok:
            result['message'] = f"Backup verification failed: {ver_details.get('error')}"
            return result
        result['backup_verified'] = True

        if os.path.exists(self.db_path):
            safety_path = os.path.join(
                self.temp_dir,
                f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            )
            try:
                shutil.copy2(self.db_path, safety_path)
                result['safety_copy_created'] = True
                self.logger.info(f"Safety copy created: {safety_path}")
            except Exception as e:
                result['message'] = f"Failed to create safety copy: {e}"
                return result

        temp_restore = os.path.join(self.temp_dir, f"temp_restore_{int(time.time())}.db")
        try:
            with gzip.open(backup_path, 'rb') as f_in:
                with open(temp_restore, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)

            ver_ok2, ver_details2 = self.verify_database_integrity(temp_restore)
            if not ver_ok2:
                result['message'] = f"Restored database integrity check failed: {ver_details2}"
                os.remove(temp_restore)
                return result

            tables_ok = self.verify_tables_exist(temp_restore)
            if not tables_ok:
                result['message'] = "Restored database missing required tables"
                os.remove(temp_restore)
                return result

            result['temp_restore_verified'] = True

            wal_path = self.db_path + '-wal'
            shm_path = self.db_path + '-shm'
            for p in [wal_path, shm_path]:
                if os.path.exists(p):
                    os.remove(p)

            shutil.move(temp_restore, self.db_path)

            live_ok, live_msg = self.verify_database_integrity(self.db_path)
            if not live_ok:
                self.logger.error(f"Live DB verification failed after restore: {live_msg}")
                if result['safety_copy_created']:
                    shutil.move(safety_path, self.db_path)
                    result['message'] = "Restore failed, reverted to safety copy"
                    return result
                else:
                    result['message'] = "Restore failed and no safety copy available"
                    return result

            result['success'] = True
            result['message'] = f"Database restored successfully from {filename}"
            self.manifest['last_good'] = filename
            self._update_last_good_link(backup_path)
            self._save_manifest()

            self.logger.info(f"Restore successful from {filename}")
            return result

        except Exception as e:
            self.logger.error(f"Restore failed: {e}", exc_info=True)
            result['message'] = str(e)
            if os.path.exists(temp_restore):
                os.remove(temp_restore)
            return result

    # ============================================
    # BACKUP QUERIES
    # ============================================

    def get_latest_valid_backup(self) -> Optional[str]:
        valid = [b for b in self.manifest['backups'] if b.get('integrity_status') == 'valid']
        if not valid:
            return None
        valid.sort(key=lambda x: x['timestamp'], reverse=True)
        return valid[0]['filename']

    def get_backup_list(self, valid_only=False) -> List[Dict]:
        backups = self.manifest['backups']
        if valid_only:
            backups = [b for b in backups if b.get('integrity_status') == 'valid']
        backups.sort(key=lambda x: x['timestamp'], reverse=True)
        return backups

    def get_backup_stats(self) -> Dict:
        total = len(self.manifest['backups'])
        valid = sum(1 for b in self.manifest['backups'] if b.get('integrity_status') == 'valid')
        invalid = total - valid
        total_size = sum(b.get('size_bytes', 0) for b in self.manifest['backups'])
        
        by_type = {}
        for b in self.manifest['backups']:
            btype = b.get('type', 'manual')
            by_type[btype] = by_type.get(btype, 0) + 1
        
        return {
            'total': total,
            'valid': valid,
            'invalid': invalid,
            'total_size_mb': total_size / (1024 * 1024),
            'by_type': by_type
        }

    # ============================================
    # HEALTH CHECK - Enhanced
    # ============================================

    def health_check(self) -> Dict:
        result = {
            'status': 'healthy',
            'issues': [],
            'backup_count': len(self.manifest['backups']),
            'valid_count': 0,
            'invalid_count': 0,
            'last_good': self.manifest.get('last_good'),
            'last_created': self.manifest.get('last_created'),
            'db_integrity': False,
            'disk_free_mb': 0,
            'backup_dir_writable': True,
            'manifest_version': self.manifest.get('version', '1.0'),
            'created_at': self.manifest.get('created_at', 'Unknown'),
            'total_size_mb': 0,
            'db_path': self.db_path,
            'backup_dir': self.backup_dir,
            'wal_enabled': False
        }

        # Check database and WAL mode
        if os.path.exists(self.db_path):
            db_ok, msg = self.verify_database_integrity(self.db_path)
            result['db_integrity'] = db_ok
            if not db_ok:
                result['issues'].append(f"Live database integrity check failed: {msg}")
            
            # Check WAL mode
            try:
                conn = sqlite3.connect(self.db_path, timeout=5)
                cursor = conn.execute("PRAGMA journal_mode")
                wal_result = cursor.fetchone()
                if wal_result and wal_result[0].upper() == 'WAL':
                    result['wal_enabled'] = True
                else:
                    result['issues'].append(f"WAL mode not enabled: {wal_result[0] if wal_result else 'unknown'}")
                conn.close()
            except Exception as e:
                result['issues'].append(f"Could not check WAL mode: {e}")

        # Check backup files
        total_size = 0
        for entry in self.manifest['backups']:
            if entry.get('integrity_status') == 'valid':
                result['valid_count'] += 1
            else:
                result['invalid_count'] += 1
            total_size += entry.get('size_bytes', 0)
        
        result['total_size_mb'] = total_size / (1024 * 1024)

        # Check disk space
        try:
            stat = os.statvfs(self.backup_dir)
            free_bytes = stat.f_frsize * stat.f_bavail
            result['disk_free_mb'] = free_bytes / (1024 * 1024)
            if result['disk_free_mb'] < 50:
                result['issues'].append(f"Low disk space: {result['disk_free_mb']:.2f} MB")
        except Exception:
            pass

        # Check backup directory writability
        try:
            test_file = os.path.join(self.backup_dir, '.write_test')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
        except Exception:
            result['backup_dir_writable'] = False
            result['issues'].append("Backup directory is not writable")

        # Check last good backup
        if self.manifest.get('last_good'):
            last_good_path = os.path.join(self.backup_dir, self.manifest['last_good'])
            if not os.path.exists(last_good_path):
                result['issues'].append("LAST_KNOWN_GOOD backup file missing")
        else:
            result['issues'].append("No last known good backup set")

        # Check backup age
        if self.manifest.get('last_created'):
            try:
                last_created = datetime.fromisoformat(self.manifest['last_created'])
                age = datetime.now() - last_created
                if age > timedelta(days=7):
                    result['issues'].append(f"Last backup is {age.days} days old")
            except Exception:
                pass

        # Determine overall status
        if result['issues']:
            result['status'] = 'warning'
        if result['valid_count'] == 0 or not result['db_integrity']:
            result['status'] = 'critical'
        if not result['wal_enabled']:
            result['status'] = 'warning'

        return result

    # ============================================
    # MAINTENANCE & ROTATION
    # ============================================

    def run_maintenance(self, dry_run=False) -> Dict:
        result = {
            'deleted': [],
            'kept': [],
            'errors': [],
            'dry_run': dry_run,
            'total_before': len(self.manifest['backups'])
        }

        by_type = {}
        for entry in self.manifest['backups']:
            btype = entry.get('type', 'manual')
            by_type.setdefault(btype, []).append(entry)

        last_good = self.manifest.get('last_good')

        for btype, entries in by_type.items():
            entries.sort(key=lambda x: x['timestamp'], reverse=True)
            retention = RETENTION.get(btype)
            if retention is None:
                continue

            to_keep = entries[:retention]
            to_delete = entries[retention:]

            for entry in to_keep:
                result['kept'].append(entry['filename'])

            for entry in to_delete:
                if entry['filename'] == last_good:
                    result['kept'].append(entry['filename'])
                    continue

                file_path = os.path.join(self.backup_dir, entry['filename'])
                if not dry_run:
                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                            result['deleted'].append(entry['filename'])
                            self.manifest['backups'].remove(entry)
                    except Exception as e:
                        result['errors'].append(f"Failed to delete {entry['filename']}: {e}")
                else:
                    result['deleted'].append(entry['filename'])

        result['total_after'] = len(self.manifest['backups'])

        if not dry_run:
            self._save_manifest()

        return result

    # ============================================
    # VERIFY ALL BACKUPS
    # ============================================

    def verify_all_backups(self) -> Dict:
        results = {
            'total': 0,
            'valid': 0,
            'invalid': 0,
            'missing': 0,
            'errors': [],
            'details': []
        }

        for entry in self.manifest['backups']:
            results['total'] += 1
            file_path = os.path.join(self.backup_dir, entry['filename'])
            if not os.path.exists(file_path):
                entry['integrity_status'] = 'missing'
                results['missing'] += 1
                results['errors'].append(f"Missing file: {entry['filename']}")
                continue

            ver_ok, ver_details = self.verify_backup(file_path)
            if ver_ok:
                entry['integrity_status'] = 'valid'
                entry['integrity_result'] = 'ok'
                results['valid'] += 1
            else:
                entry['integrity_status'] = 'invalid'
                entry['integrity_result'] = ver_details.get('error', 'unknown')
                results['invalid'] += 1
                results['errors'].append(f"Invalid: {entry['filename']} - {ver_details.get('error')}")

            results['details'].append({
                'filename': entry['filename'],
                'status': entry['integrity_status'],
                'checksum': entry.get('checksum', 'N/A'),
                'size_bytes': entry.get('size_bytes', 0)
            })

        self._save_manifest()
        return results

    # ============================================
    # BACKUP INFO
    # ============================================

    def get_backup_info(self, filename: str) -> Optional[Dict]:
        for entry in self.manifest['backups']:
            if entry['filename'] == filename:
                return entry
        return None

    def get_latest_good_path(self) -> Optional[str]:
        if self.manifest.get('last_good'):
            path = os.path.join(self.backup_dir, self.manifest['last_good'])
            if os.path.exists(path):
                return path
        return None

    # ============================================
    # BACKUP EXPORT
    # ============================================

    def export_backup(self, filename: str, export_path: str) -> Dict:
        result = {'success': False, 'message': ''}
        source = os.path.join(self.backup_dir, filename)
        if not os.path.exists(source):
            result['message'] = f"Backup not found: {filename}"
            return result
        
        try:
            shutil.copy2(source, export_path)
            result['success'] = True
            result['message'] = f"Backup exported to {export_path}"
            result['size_bytes'] = os.path.getsize(export_path)
        except Exception as e:
            result['message'] = f"Export failed: {e}"
        return result

    # ============================================
    # WEB-READY RESTORE (with safety copy)
    # ============================================

    def restore_web(self, filename: str, admin_id: int = None) -> Dict:
        """
        Restore a backup with full safety and rollback.
        Returns detailed status for web UI.
        """
        result = {
            'success': False,
            'message': '',
            'safety_copy': None,
            'backup_verified': False,
            'safety_copy_verified': False,
            'restore_completed': False
        }

        backup_path = os.path.join(self.backup_dir, filename)
        if not os.path.exists(backup_path):
            result['message'] = f"Backup file not found: {filename}"
            return result

        # Verify the backup file
        ver_ok, ver_details = self.verify_backup(backup_path)
        if not ver_ok:
            result['message'] = f"Backup verification failed: {ver_details.get('error')}"
            return result
        result['backup_verified'] = True

        # Create safety copy of current DB
        safety_path = os.path.join(
            self.temp_dir,
            f"safety_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        )
        try:
            shutil.copy2(self.db_path, safety_path)
            result['safety_copy'] = safety_path
            # Verify safety copy is valid
            ver_ok2, ver_details2 = self.verify_database_integrity(safety_path)
            if not ver_ok2:
                result['message'] = f"Safety copy integrity check failed: {ver_details2}"
                return result
            result['safety_copy_verified'] = True
        except Exception as e:
            result['message'] = f"Failed to create safety copy: {e}"
            return result

        # Perform restore
        try:
            # Uncompress to temp
            temp_restore = os.path.join(self.temp_dir, f"temp_restore_{int(time.time())}.db")
            with gzip.open(backup_path, 'rb') as f_in:
                with open(temp_restore, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)

            # Verify temp file
            ver_ok3, ver_details3 = self.verify_database_integrity(temp_restore)
            if not ver_ok3:
                result['message'] = f"Restored database integrity check failed: {ver_details3}"
                os.remove(temp_restore)
                return result

            # Remove WAL files
            for p in [self.db_path + '-wal', self.db_path + '-shm']:
                if os.path.exists(p):
                    os.remove(p)

            # Move temp to live DB
            shutil.move(temp_restore, self.db_path)

            # Verify live DB
            live_ok, live_msg = self.verify_database_integrity(self.db_path)
            if not live_ok:
                # Rollback to safety copy
                shutil.move(safety_path, self.db_path)
                result['message'] = f"Restore failed, reverted to safety copy. Error: {live_msg}"
                return result

            # Success
            result['success'] = True
            result['restore_completed'] = True
            result['message'] = f"Database restored successfully from {filename}"

            # Update manifest
            self.manifest['last_good'] = filename
            self._update_last_good_link(backup_path)
            self._save_manifest()

            return result

        except Exception as e:
            self.logger.error(f"Restore error: {e}", exc_info=True)
            # Try to revert to safety copy
            try:
                if os.path.exists(safety_path):
                    shutil.move(safety_path, self.db_path)
                    result['message'] = f"Restore failed, reverted to safety copy. Error: {e}"
                else:
                    result['message'] = f"Restore failed: {e}"
            except Exception as revert_error:
                result['message'] = f"Restore failed and safety copy revert failed: {revert_error}"
            return result

    # ============================================
    # GET BACKUP HEALTH SUMMARY (for UI)
    # ============================================

    def get_backup_health_summary(self) -> Dict:
        """Return a summary for the admin dashboard."""
        health = self.health_check()
        latest = self.get_latest_valid_backup()
        return {
            'status': health['status'],
            'total_backups': health['backup_count'],
            'valid_backups': health['valid_count'],
            'invalid_backups': health['invalid_count'],
            'last_good': health['last_good'],
            'last_created': health['last_created'],
            'disk_free_mb': health['disk_free_mb'],
            'total_size_mb': health['total_size_mb'],
            'db_integrity': health['db_integrity'],
            'wal_enabled': health.get('wal_enabled', False),
            'latest_backup': latest,
            'issues': health['issues'],
            'backup_dir': self.backup_dir,
            'db_path': self.db_path
        }

    # ============================================
    # GET BACKUP DETAILS (for UI)
    # ============================================

    def get_backup_details(self, filename: str) -> Optional[Dict]:
        """Return full details for a single backup from manifest."""
        for entry in self.manifest['backups']:
            if entry['filename'] == filename:
                # Add file existence and size
                file_path = os.path.join(self.backup_dir, filename)
                entry['exists'] = os.path.exists(file_path)
                if entry['exists']:
                    entry['size_bytes'] = os.path.getsize(file_path)
                else:
                    entry['size_bytes'] = 0
                return entry
        return None

# ============================================
# FEATURE 20: INTERACTIVE DASHBOARD
# ============================================

class Dashboard:
    """Interactive dashboard with numbered features."""
    
    def __init__(self, manager: BackupManager):
        self.manager = manager
        self.features = self._get_features()
        
    def _get_features(self) -> List[Dict]:
        return [
            {"num": 1, "name": "Create Backup", "desc": "Create daily/weekly/monthly/manual backup", "cmd": "1"},
            {"num": 2, "name": "List Backups", "desc": "Show all backups with details", "cmd": "2"},
            {"num": 3, "name": "List Valid Backups", "desc": "Show only valid backups", "cmd": "3"},
            {"num": 4, "name": "Restore Latest", "desc": "Restore the latest valid backup", "cmd": "4"},
            {"num": 5, "name": "Restore Specific", "desc": "Restore a specific backup", "cmd": "5"},
            {"num": 6, "name": "Verify Backup", "desc": "Verify a specific backup", "cmd": "6"},
            {"num": 7, "name": "Verify All", "desc": "Verify all backups", "cmd": "7"},
            {"num": 8, "name": "Health Check", "desc": "Run system health check", "cmd": "8"},
            {"num": 9, "name": "Maintenance", "desc": "Remove expired backups", "cmd": "9"},
            {"num": 10, "name": "Maintenance Dry Run", "desc": "Preview what would be deleted", "cmd": "10"},
            {"num": 11, "name": "Backup Statistics", "desc": "Show backup statistics", "cmd": "11"},
            {"num": 12, "name": "System Info", "desc": "Show system information", "cmd": "12"},
            {"num": 13, "name": "Export Backup", "desc": "Export a backup to external location", "cmd": "13"},
            {"num": 14, "name": "Clear Screen", "desc": "Clear the dashboard", "cmd": "14"},
            {"num": 15, "name": "Exit", "desc": "Exit the dashboard", "cmd": "15"},
        ]
    
    def show(self):
        """Display the interactive dashboard."""
        while True:
            self._clear_screen()
            self._show_header()
            self._show_status()
            self._show_menu()
            
            choice = input(f"\n{cyan('Select option')} {dim('(1-15)')}: ").strip()
            
            if choice == '15':
                self._show_exit()
                break
            elif choice == '1':
                self._create_backup()
            elif choice == '2':
                self._list_backups(valid_only=False)
            elif choice == '3':
                self._list_backups(valid_only=True)
            elif choice == '4':
                self._restore_latest()
            elif choice == '5':
                self._restore_specific()
            elif choice == '6':
                self._verify_backup()
            elif choice == '7':
                self._verify_all()
            elif choice == '8':
                self._health_check()
            elif choice == '9':
                self._maintenance(dry_run=False)
            elif choice == '10':
                self._maintenance(dry_run=True)
            elif choice == '11':
                self._show_stats()
            elif choice == '12':
                self._show_info()
            elif choice == '13':
                self._export_backup()
            elif choice == '14':
                continue
            else:
                print(red(f"Invalid option: {choice}"))
                time.sleep(1)
    
    def _clear_screen(self):
        os.system('cls' if os.name == 'nt' else 'clear')
    
    def _show_header(self):
        print(COLOR_CYAN + "=" * 70 + COLOR_RESET)
        print(COLOR_YELLOW + bold(f"  🗄️  {APP_NAME} v{VERSION}  ") + COLOR_RESET)
        print(COLOR_CYAN + "=" * 70 + COLOR_RESET)
        print(dim(f"  Dashboard Mode - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"))
        print(COLOR_CYAN + "-" * 70 + COLOR_RESET)
    
    def _show_status(self):
        health = self.manager.health_check()
        status_color = green if health['status'] == 'healthy' else (yellow if health['status'] == 'warning' else red)
        
        print(f"\n  Status: {status_color(bold(health['status'].upper()))}")
        print(f"  Backups: {health['valid_count']} valid / {health['invalid_count']} invalid / {health['backup_count']} total")
        print(f"  Database: {green('OK') if health['db_integrity'] else red('FAILED')}")
        print(f"  WAL Mode: {green('Enabled') if health.get('wal_enabled') else red('Disabled')}")
        print(f"  Disk Free: {health['disk_free_mb']:.1f} MB")
        if health['last_good']:
            print(f"  Last Good: {green(health['last_good'])}")
        if health['issues']:
            print(f"  {yellow('⚠️')} {len(health['issues'])} issue(s) detected")
        print(COLOR_CYAN + "-" * 70 + COLOR_RESET)
    
    def _show_menu(self):
        print(f"\n{COLOR_BOLD}FEATURES MENU{COLOR_RESET}")
        print(COLOR_CYAN + "-" * 70 + COLOR_RESET)
        
        for f in self.features:
            num = f"{f['num']:2d}"
            name = f['name']
            desc = f['desc']
            print(f"  {COLOR_GREEN}{num}{COLOR_RESET}. {COLOR_WHITE}{name:20}{COLOR_RESET} {dim('-')} {desc}")
        
        print(COLOR_CYAN + "-" * 70 + COLOR_RESET)
    
    def _show_exit(self):
        print(f"\n{green('👋 Goodbye!')}")
        print("Thank you for using the backup system.\n")
    
    def _create_backup(self):
        print(f"\n{COLOR_BOLD}Create Backup{COLOR_RESET}")
        print(COLOR_CYAN + "-" * 40 + COLOR_RESET)
        print("  1. Daily (keep 7)")
        print("  2. Weekly (keep 4)")
        print("  3. Monthly (keep 12)")
        print("  4. Manual (unlimited)")
        print("  5. Cancel")
        
        choice = input(f"\n{cyan('Select type')} {dim('(1-5)')}: ").strip()
        
        type_map = {'1': 'daily', '2': 'weekly', '3': 'monthly', '4': 'manual'}
        if choice in type_map:
            btype = type_map[choice]
            print(f"\n{yellow(f'Creating {btype} backup...')}")
            result = self.manager.create_backup(btype)
            if result['success']:
                print(green(f"✅ {result['message']}"))
                print(f"   Filename: {result['filename']}")
                print(f"   Size: {result['size_bytes'] / 1024:.2f} KB")
                print(f"   Duration: {result['duration']} seconds")
            else:
                print(red(f"❌ {result['message']}"))
        elif choice == '5':
            print(dim("Cancelled"))
        else:
            print(red("Invalid choice"))
        
        input(f"\n{dim('Press Enter to continue...')}")
    
    def _list_backups(self, valid_only=False):
        backups = self.manager.get_backup_list(valid_only=valid_only)
        if not backups:
            print(f"\n{yellow('No backups found.')}")
        else:
            print(f"\n{COLOR_BOLD}{'Recent Backups'}{COLOR_RESET}")
            print(COLOR_CYAN + "-" * 80 + COLOR_RESET)
            print(f"{'#':<4} {'Filename':<35} {'Type':<10} {'Size':<10} {'Status':<10}")
            print(COLOR_CYAN + "-" * 80 + COLOR_RESET)
            for i, entry in enumerate(backups[:20], 1):
                filename = entry['filename'][:34]
                btype = entry.get('type', 'manual')
                size_kb = entry.get('size_bytes', 0) / 1024
                status = entry.get('integrity_status', 'unknown')
                status_display = green("VALID") if status == 'valid' else red("INVALID") if status == 'invalid' else status
                print(f"{i:<4} {filename:<35} {btype:<10} {size_kb:<10.1f} {status_display}")
            
            if len(backups) > 20:
                print(dim(f"... and {len(backups) - 20} more"))
        
        input(f"\n{dim('Press Enter to continue...')}")
    
    def _restore_latest(self):
        print(f"\n{COLOR_BOLD}Restore Latest Backup{COLOR_RESET}")
        latest = self.manager.get_latest_valid_backup()
        if not latest:
            print(red("No valid backup found!"))
        else:
            print(f"Latest backup: {green(latest)}")
            confirm = input(f"{yellow('Are you sure?')} {dim('(y/N)')}: ").strip().lower()
            if confirm == 'y':
                result = self.manager.restore_latest()
                if result['success']:
                    print(green(f"✅ {result['message']}"))
                else:
                    print(red(f"❌ {result['message']}"))
            else:
                print(dim("Cancelled"))
        input(f"\n{dim('Press Enter to continue...')}")
    
    def _restore_specific(self):
        print(f"\n{COLOR_BOLD}Restore Specific Backup{COLOR_RESET}")
        backups = self.manager.get_backup_list(valid_only=True)
        if not backups:
            print(red("No valid backups found!"))
            input(f"\n{dim('Press Enter to continue...')}")
            return
        
        print(f"\n{COLOR_BOLD}Valid Backups:{COLOR_RESET}")
        for i, entry in enumerate(backups[:10], 1):
            print(f"  {i}. {entry['filename']}")
        
        choice = input(f"\n{cyan('Select backup number')} {dim('(or filename)')}: ").strip()
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(backups):
                filename = backups[idx]['filename']
            else:
                filename = choice
        except ValueError:
            filename = choice
        
        if filename:
            confirm = input(f"Restore {yellow(filename)}? {dim('(y/N)')}: ").strip().lower()
            if confirm == 'y':
                result = self.manager.restore_backup(filename)
                if result['success']:
                    print(green(f"✅ {result['message']}"))
                else:
                    print(red(f"❌ {result['message']}"))
            else:
                print(dim("Cancelled"))
        else:
            print(red("Invalid selection"))
        
        input(f"\n{dim('Press Enter to continue...')}")
    
    def _verify_backup(self):
        print(f"\n{COLOR_BOLD}Verify Backup{COLOR_RESET}")
        filename = input(f"{cyan('Enter backup filename')}: ").strip()
        if filename:
            print(f"{yellow('Verifying...')}")
            ver_ok, details = self.manager.verify_backup(
                os.path.join(self.manager.backup_dir, filename)
            )
            if ver_ok:
                print(green(f"✅ Backup '{filename}' is VALID"))
                print(f"   Size: {details['size_bytes'] / 1024:.2f} KB")
                print(f"   Integrity: {details.get('integrity_msg', 'ok')}")
            else:
                print(red(f"❌ Backup '{filename}' is INVALID"))
                print(f"   Error: {details.get('error')}")
        else:
            print(dim("Cancelled"))
        input(f"\n{dim('Press Enter to continue...')}")
    
    def _verify_all(self):
        print(f"\n{COLOR_BOLD}Verify All Backups{COLOR_RESET}")
        print(f"{yellow('This may take a while...')}")
        results = self.manager.verify_all_backups()
        print(f"\n  Total: {results['total']}")
        print(green(f"  ✅ Valid: {results['valid']}"))
        print(red(f"  ❌ Invalid: {results['invalid']}"))
        print(yellow(f"  ⚠️ Missing: {results['missing']}"))
        if results['errors']:
            print(f"\n{COLOR_BOLD}Errors:{COLOR_RESET}")
            for err in results['errors'][:5]:
                print(f"  - {err}")
            if len(results['errors']) > 5:
                print(dim(f"  ... and {len(results['errors']) - 5} more"))
        input(f"\n{dim('Press Enter to continue...')}")
    
    def _health_check(self):
        print(f"\n{COLOR_BOLD}Health Check Report{COLOR_RESET}")
        print(COLOR_CYAN + "=" * 60 + COLOR_RESET)
        health = self.manager.health_check()
        
        status_color = green if health['status'] == 'healthy' else (yellow if health['status'] == 'warning' else red)
        print(f"Status: {status_color(bold(health['status'].upper()))}")
        print(f"Backups: {health['valid_count']} valid / {health['invalid_count']} invalid / {health['backup_count']} total")
        print(f"Last Good: {health['last_good'] or 'None'}")
        print(f"Last Created: {health['last_created'] or 'Never'}")
        db_status = green("OK") if health['db_integrity'] else red("FAILED")
        print(f"DB Integrity: {db_status}")
        print(f"WAL Mode: {green('Enabled') if health.get('wal_enabled') else red('Disabled')}")
        print(f"Disk Free: {health['disk_free_mb']:.2f} MB")
        print(f"Total Backup Size: {health['total_size_mb']:.2f} MB")
        print(f"Backup Dir Writable: {'Yes' if health['backup_dir_writable'] else 'No'}")
        print(f"Manifest Version: {health['manifest_version']}")
        
        if health['issues']:
            print(f"\n{yellow('Issues Detected:')}")
            for issue in health['issues']:
                print(f"  ⚠️ {issue}")
        else:
            print(f"\n{green('✅ No issues detected')}")
        
        print(COLOR_CYAN + "=" * 60 + COLOR_RESET)
        input(f"\n{dim('Press Enter to continue...')}")
    
    def _maintenance(self, dry_run=False):
        title = "Maintenance - Dry Run" if dry_run else "Maintenance"
        print(f"\n{COLOR_BOLD}{title}{COLOR_RESET}")
        
        if dry_run:
            print(yellow("DRY RUN - No changes will be made"))
        
        result = self.manager.run_maintenance(dry_run=dry_run)
        
        print(f"\nBefore: {result['total_before']} backups")
        print(f"After: {result['total_after']} backups")
        print(f"Deleted: {len(result['deleted'])} files")
        
        if result['deleted']:
            print(f"\n{COLOR_BOLD}Deleted backups:{COLOR_RESET}")
            for f in result['deleted'][:10]:
                print(f"  - {f}")
            if len(result['deleted']) > 10:
                print(dim(f"  ... and {len(result['deleted']) - 10} more"))
        
        if result['errors']:
            print(f"\n{red('Errors:')}")
            for err in result['errors']:
                print(f"  - {err}")
        
        input(f"\n{dim('Press Enter to continue...')}")
    
    def _show_stats(self):
        print(f"\n{COLOR_BOLD}Backup Statistics{COLOR_RESET}")
        print(COLOR_CYAN + "-" * 40 + COLOR_RESET)
        stats = self.manager.get_backup_stats()
        print(f"Total Backups: {stats['total']}")
        print(f"Valid: {green(str(stats['valid']))}")
        print(f"Invalid: {red(str(stats['invalid']))}")
        print(f"Total Size: {stats['total_size_mb']:.2f} MB")
        print(f"\n{COLOR_BOLD}By Type:{COLOR_RESET}")
        for btype, count in stats['by_type'].items():
            emoji = BACKUP_TYPES.get(btype, {}).get('emoji', '📁')
            print(f"  {emoji} {btype.capitalize()}: {count}")
        input(f"\n{dim('Press Enter to continue...')}")
    
    def _show_info(self):
        print(f"\n{COLOR_BOLD}System Information{COLOR_RESET}")
        print(COLOR_CYAN + "-" * 40 + COLOR_RESET)
        print(f"Application: {APP_NAME}")
        print(f"Version: {VERSION}")
        print(f"Database: {self.manager.db_path}")
        print(f"Backup Directory: {self.manager.backup_dir}")
        print(f"Manifest: {self.manager.manifest_path}")
        print(f"Temp Directory: {self.manager.temp_dir}")
        print(f"\n{COLOR_BOLD}Retention Policies:{COLOR_RESET}")
        for btype, retention in RETENTION.items():
            if retention:
                print(f"  {btype.capitalize()}: {retention} copies")
            else:
                print(f"  {btype.capitalize()}: unlimited")
        input(f"\n{dim('Press Enter to continue...')}")
    
    def _export_backup(self):
        print(f"\n{COLOR_BOLD}Export Backup{COLOR_RESET}")
        filename = input(f"{cyan('Enter backup filename')}: ").strip()
        if filename:
            export_path = input(f"{cyan('Enter export path')} {dim('(full path)')}: ").strip()
            if export_path:
                result = self.manager.export_backup(filename, export_path)
                if result['success']:
                    print(green(f"✅ {result['message']}"))
                    print(f"   Size: {result['size_bytes'] / 1024:.2f} KB")
                else:
                    print(red(f"❌ {result['message']}"))
            else:
                print(dim("Cancelled"))
        else:
            print(dim("Cancelled"))
        input(f"\n{dim('Press Enter to continue...')}")

# ============================================
# FEATURE 21-34: CLI COMMANDS
# ============================================

def create_parser():
    parser = argparse.ArgumentParser(
        prog='backup',
        description=f'{APP_NAME} v{VERSION} - Complete Backup Management',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
{COLOR_BOLD}FEATURES:{COLOR_RESET}
  {COLOR_GREEN}1{COLOR_RESET}. Create Backup        {COLOR_GREEN}2{COLOR_RESET}. List Backups
  {COLOR_GREEN}3{COLOR_RESET}. List Valid Backups   {COLOR_GREEN}4{COLOR_RESET}. Restore Latest
  {COLOR_GREEN}5{COLOR_RESET}. Restore Specific     {COLOR_GREEN}6{COLOR_RESET}. Verify Backup
  {COLOR_GREEN}7{COLOR_RESET}. Verify All           {COLOR_GREEN}8{COLOR_RESET}. Health Check
  {COLOR_GREEN}9{COLOR_RESET}. Maintenance          {COLOR_GREEN}10{COLOR_RESET}. Maintenance Dry Run
  {COLOR_GREEN}11{COLOR_RESET}. Statistics          {COLOR_GREEN}12{COLOR_RESET}. System Info
  {COLOR_GREEN}13{COLOR_RESET}. Export Backup       {COLOR_GREEN}14{COLOR_RESET}. Interactive Dashboard

Examples:
  backup dashboard          # Open interactive dashboard
  backup create --type daily
  backup list --valid
  backup restore-latest
  backup health
        """
    )
    parser.add_argument('--non-interactive', action='store_true',
                        help='Run without prompts (for scheduled tasks)')
    parser.add_argument('--log-file', default=None,
                        help='Log file path (default: backup.log)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose output')
    parser.add_argument('--version', action='version', version=f'{APP_NAME} v{VERSION}')

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # Feature 21: Create Backup
    create_parser = subparsers.add_parser('create', help='Create a new backup')
    create_parser.add_argument('--type', default='daily',
                               choices=['daily', 'weekly', 'monthly', 'manual'],
                               help='Backup type (default: daily)')

    # Feature 22: List Backups
    list_parser = subparsers.add_parser('list', help='List backups')
    list_parser.add_argument('--valid', action='store_true', help='Show only valid backups')
    list_parser.add_argument('--json', action='store_true', help='Output as JSON')

    # Feature 23: Verify Backup
    verify_parser = subparsers.add_parser('verify', help='Verify a specific backup')
    verify_parser.add_argument('filename', help='Backup filename to verify')

    # Feature 24: Verify All
    subparsers.add_parser('verify-all', help='Verify all backups')

    # Feature 25: Health Check
    subparsers.add_parser('health', help='Run health check')

    # Feature 26: Restore Latest
    subparsers.add_parser('restore-latest', help='Restore the latest valid backup')

    # Feature 27: Restore Specific
    restore_parser = subparsers.add_parser('restore', help='Restore a specific backup')
    restore_parser.add_argument('filename', help='Backup filename to restore')
    restore_parser.add_argument('--force', action='store_true', help='Force restore in non-interactive mode')

    # Feature 28: Maintenance
    maintenance_parser = subparsers.add_parser('maintenance', help='Remove expired backups')
    maintenance_parser.add_argument('--dry-run', action='store_true', help='Show what would be deleted')

    # Feature 29: Statistics
    subparsers.add_parser('stats', help='Show backup statistics')

    # Feature 30: System Info
    subparsers.add_parser('info', help='Show system information')

    # Feature 31: Export Backup
    export_parser = subparsers.add_parser('export', help='Export a backup')
    export_parser.add_argument('filename', help='Backup filename to export')
    export_parser.add_argument('destination', help='Destination path')

    # Feature 32: Interactive Dashboard
    subparsers.add_parser('dashboard', help='Open interactive dashboard')

    return parser

# ============================================
# FEATURE 33: CLI OUTPUT FUNCTIONS
# ============================================

def print_backup_list(backups, manager):
    if not backups:
        print("No backups found.")
        return
    print(f"\n{'#':<4} {'Filename':<40} {'Type':<10} {'Size (KB)':<12} {'Status':<10} {'Date'}")
    print("-" * 90)
    for i, entry in enumerate(backups[:20], 1):
        filename = entry['filename']
        btype = entry.get('type', 'manual')
        size_kb = entry.get('size_bytes', 0) / 1024
        status = entry.get('integrity_status', 'unknown')
        timestamp = entry.get('timestamp', '')[:16]
        is_last = filename == manager.manifest.get('last_good')
        status_str = green("VALID") if status == 'valid' else red("INVALID") if status == 'invalid' else status
        marker = ' ⭐' if is_last else ''
        print(f"{i:<4} {filename:<40} {btype:<10} {size_kb:<12.1f} {status_str:<10} {timestamp}{marker}")
    if len(backups) > 20:
        print(dim(f"... and {len(backups) - 20} more"))
    print()

def print_health_report(health):
    print("\n" + "=" * 60)
    print("    BACKUP SYSTEM HEALTH REPORT")
    print("=" * 60)
    status_color = green if health['status'] == 'healthy' else (yellow if health['status'] == 'warning' else red)
    print(f"Status: {status_color(bold(health['status'].upper()))}")
    print(f"Total backups: {health['backup_count']}")
    print(f"Valid backups: {health['valid_count']}")
    print(f"Invalid backups: {health['invalid_count']}")
    print(f"Last good: {health['last_good'] or 'None'}")
    print(f"Last created: {health['last_created'] or 'Never'}")
    db_status = green("OK") if health['db_integrity'] else red("FAILED")
    print(f"DB integrity: {db_status}")
    print(f"WAL Mode: {green('Enabled') if health.get('wal_enabled') else red('Disabled')}")
    print(f"Disk free: {health['disk_free_mb']:.2f} MB")
    print(f"Total backup size: {health['total_size_mb']:.2f} MB")
    print(f"Backup dir writable: {'Yes' if health['backup_dir_writable'] else 'No'}")
    print(f"Manifest version: {health['manifest_version']}")
    if health['issues']:
        print("\nIssues:")
        for issue in health['issues']:
            print(f"  ⚠️ {issue}")
    print("=" * 60)

def print_stats(stats):
    print(f"\nBackup Statistics")
    print("-" * 40)
    print(f"Total Backups: {stats['total']}")
    print(f"Valid: {green(str(stats['valid']))}")
    print(f"Invalid: {red(str(stats['invalid']))}")
    print(f"Total Size: {stats['total_size_mb']:.2f} MB")
    print(f"\nBy Type:")
    for btype, count in stats['by_type'].items():
        emoji = BACKUP_TYPES.get(btype, {}).get('emoji', '📁')
        print(f"  {emoji} {btype.capitalize()}: {count}")

def print_info(manager):
    print(f"\n{APP_NAME} v{VERSION}")
    print("-" * 40)
    print(f"Database: {manager.db_path}")
    print(f"Backup directory: {manager.backup_dir}")
    print(f"Manifest: {manager.manifest_path}")
    print(f"Total backups: {len(manager.manifest['backups'])}")
    print(f"Last good: {manager.manifest.get('last_good')}")
    print(f"Last created: {manager.manifest.get('last_created')}")
    print(f"\nRetention policies:")
    for btype, retention in RETENTION.items():
        if retention:
            print(f"  {btype}: {retention} copies")
        else:
            print(f"  {btype}: unlimited")

# ============================================
# FEATURE 34: MAIN ENTRY POINT
# ============================================

def main():
    parser = create_parser()
    args = parser.parse_args()

    if args.non_interactive:
        os.environ['BACKUP_NON_INTERACTIVE'] = '1'

    log_file = args.log_file or DEFAULT_LOG_FILE
    logger = setup_logging(verbose=args.verbose, log_file=log_file)

    if args.non_interactive and is_backup_locked():
        logger.info("Backup already running, skipping")
        sys.exit(0)

    manager = BackupManager()

    # Interactive Dashboard
    if not args.command or args.command == 'dashboard':
        if args.non_interactive:
            health = manager.health_check()
            if health['status'] == 'healthy':
                print(f"Backup system healthy. Valid backups: {health['valid_count']}")
                sys.exit(0)
            else:
                print(f"Backup system issues: {health['status']}")
                for issue in health['issues']:
                    print(f"  - {issue}")
                sys.exit(1)
        else:
            dashboard = Dashboard(manager)
            dashboard.show()
        return

    # Execute commands
    if args.command == 'list':
        backups = manager.get_backup_list(valid_only=args.valid)
        if args.json:
            print(json.dumps(backups, indent=2))
        else:
            print_backup_list(backups, manager)
    
    elif args.command == 'create':
        result = manager.create_backup(args.type)
        if result['success']:
            print(green(f"✅ {result['message']}"))
            print(f"   Filename: {result['filename']}")
            print(f"   Size: {result['size_bytes'] / 1024:.2f} KB")
            print(f"   Checksum: {result['checksum'][:16]}...")
            print(f"   Duration: {result['duration']} seconds")
            sys.exit(0)
        else:
            print(red(f"❌ {result['message']}"))
            sys.exit(1)
    
    elif args.command == 'verify':
        ver_ok, details = manager.verify_backup(
            os.path.join(manager.backup_dir, args.filename)
        )
        if ver_ok:
            print(green(f"✅ Backup '{args.filename}' is VALID"))
            print(f"   Size: {details['size_bytes'] / 1024:.2f} KB")
            print(f"   Integrity: {details.get('integrity_msg', 'ok')}")
            if 'checksum_match' in details:
                print(f"   Checksum: {'OK' if details['checksum_match'] else 'MISMATCH'}")
            sys.exit(0)
        else:
            print(red(f"❌ Backup '{args.filename}' is INVALID"))
            print(f"   Error: {details.get('error')}")
            sys.exit(1)
    
    elif args.command == 'verify-all':
        results = manager.verify_all_backups()
        print(f"Total backups: {results['total']}")
        print(green(f"✅ Valid: {results['valid']}"))
        print(red(f"❌ Invalid: {results['invalid']}"))
        print(yellow(f"⚠️ Missing: {results['missing']}"))
        if results['errors']:
            print("Errors:")
            for err in results['errors'][:10]:
                print(f"  - {err}")
            if len(results['errors']) > 10:
                print(dim(f"  ... and {len(results['errors']) - 10} more"))
        if results['invalid'] > 0 or results['missing'] > 0:
            sys.exit(1)
    
    elif args.command == 'health':
        health = manager.health_check()
        print_health_report(health)
        if health['status'] == 'critical':
            sys.exit(1)
    
    elif args.command == 'restore-latest':
        result = manager.restore_latest(force=args.force if hasattr(args, 'force') else False)
        if result['success']:
            print(green(f"✅ {result['message']}"))
            print(f"   Backup verified: {result['backup_verified']}")
            print(f"   Safety copy created: {result['safety_copy_created']}")
            print(f"   Temp restore verified: {result['temp_restore_verified']}")
            sys.exit(0)
        else:
            print(red(f"❌ Restore failed: {result['message']}"))
            sys.exit(1)
    
    elif args.command == 'restore':
        result = manager.restore_backup(args.filename, force=args.force if hasattr(args, 'force') else False)
        if result['success']:
            print(green(f"✅ {result['message']}"))
            print(f"   Backup verified: {result['backup_verified']}")
            print(f"   Safety copy created: {result['safety_copy_created']}")
            print(f"   Temp restore verified: {result['temp_restore_verified']}")
            sys.exit(0)
        else:
            print(red(f"❌ Restore failed: {result['message']}"))
            sys.exit(1)
    
    elif args.command == 'maintenance':
        result = manager.run_maintenance(dry_run=args.dry_run)
        if args.dry_run:
            print(yellow("DRY RUN - Would delete the following backups:"))
        else:
            print("Deleted backups:")
        for f in result['deleted']:
            print(f"  - {f}")
        if result['errors']:
            print("Errors:")
            for err in result['errors']:
                print(f"  - {err}")
        print(f"\nBefore: {result['total_before']} backups")
        print(f"After: {result['total_after']} backups")
    
    elif args.command == 'stats':
        stats = manager.get_backup_stats()
        print_stats(stats)
    
    elif args.command == 'info':
        print_info(manager)
    
    elif args.command == 'export':
        result = manager.export_backup(args.filename, args.destination)
        if result['success']:
            print(green(f"✅ {result['message']}"))
            print(f"   Size: {result['size_bytes'] / 1024:.2f} KB")
            sys.exit(0)
        else:
            print(red(f"❌ {result['message']}"))
            sys.exit(1)

if __name__ == '__main__':
    main()