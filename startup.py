# ============================================
# APPLICATION STARTUP VERIFICATION
# ============================================
# Verifies all critical components before starting Flask
# ============================================

import os
import sys
import logging
from typing import Dict
from config import Config
from database import initialize_database_startup, verify_database_full

logger = logging.getLogger(__name__)


def verify_startup() -> bool:
    """
    Verify all critical components before starting the application.
    Returns True if everything is ready, False otherwise.
    """
    errors = []
    
    # 1. Validate configuration
    logger.info("Validating configuration...")
    config_errors = Config.validate()
    if config_errors:
        for error in config_errors:
            logger.critical(f"Config error: {error}")
            errors.append(f"Config: {error}")
    
    # 2. Initialize database (creates if missing, verifies, but does NOT run migrations)
    logger.info("Initializing database...")
    db_success, db_errors = initialize_database_startup()
    if not db_success:
        for error in db_errors:
            logger.critical(f"Database error: {error}")
            errors.append(f"Database: {error}")
    
    # 3. Ensure error table exists - using direct connection
    logger.info("Ensuring error table exists...")
    try:
        from error_models import ensure_error_table
        if not ensure_error_table():
            logger.critical("Failed to create error_logs table")
            errors.append("Database: Cannot create error_logs table")
        else:
            logger.info("Error table verified")
    except Exception as e:
        logger.critical(f"Error table creation failed: {e}")
        errors.append(f"Database: Error table - {e}")
    
    # 4. Verify backup directory
    logger.info("Verifying backup directory...")
    try:
        if not os.path.exists(Config.BACKUP_DIR):
            os.makedirs(Config.BACKUP_DIR, exist_ok=True)
            logger.info(f"Created backup directory: {Config.BACKUP_DIR}")
    except Exception as e:
        logger.critical(f"Backup directory error: {e}")
        errors.append(f"Backup: Cannot create directory - {e}")
    
    # 5. Verify log directory
    logger.info("Verifying log directory...")
    try:
        if not os.path.exists(Config.LOG_DIR):
            os.makedirs(Config.LOG_DIR, exist_ok=True)
            logger.info(f"Created log directory: {Config.LOG_DIR}")
    except Exception as e:
        logger.critical(f"Log directory error: {e}")
        errors.append(f"Logs: Cannot create directory - {e}")
    
    # Report errors
    if errors:
        error_message = "\n".join(errors)
        logger.critical(f"Startup verification FAILED:\n{error_message}")
        
        # Try to send email for critical startup errors (gracefully handle failures)
        try:
            from errors import send_error_email
            send_error_email({
                'request_id': 'startup',
                'timestamp': __import__('datetime').datetime.now().isoformat(),
                'severity': 'CRITICAL',
                'status_code': 500,
                'url': 'STARTUP',
                'method': 'SYSTEM',
                'user_id': None,
                'ip_address': 'localhost',
                'error_type': 'StartupVerificationFailed',
                'error_message': error_message[:1000],
                'stack_trace': error_message,
                'user_description': 'Application startup failed',
                'occurrence_count': 1,
                'error_hash': 'startup_' + str(int(__import__('time').time()))
            })
        except Exception as e:
            logger.error(f"Failed to send startup error email: {e}")
        
        return False
    
    logger.info("Startup verification COMPLETE - All systems ready")
    return True


def get_startup_health() -> dict:
    """
    Get detailed startup health information.
    Used by the /health endpoint.
    """
    health = {
        'config_valid': False,
        'database': {},
        'error_table': False,
        'backup_dir': False,
        'log_dir': False,
        'errors': []
    }
    
    # Check config
    config_errors = Config.validate()
    health['config_valid'] = len(config_errors) == 0
    if config_errors:
        health['errors'].extend(config_errors)
    
    # Check database
    try:
        from database import get_database_health
        health['database'] = get_database_health()
    except Exception as e:
        health['errors'].append(f"Database health check: {e}")
    
    # Check error table
    try:
        from error_models import ensure_error_table
        health['error_table'] = ensure_error_table()
        if not health['error_table']:
            health['errors'].append('Error table could not be created')
    except Exception as e:
        health['errors'].append(f'Error table: {e}')
    
    # Check directories
    health['backup_dir'] = os.path.exists(Config.BACKUP_DIR)
    health['log_dir'] = os.path.exists(Config.LOG_DIR)
    
    if not health['backup_dir']:
        health['errors'].append(f'Backup directory missing: {Config.BACKUP_DIR}')
    if not health['log_dir']:
        health['errors'].append(f'Log directory missing: {Config.LOG_DIR}')
    
    return health