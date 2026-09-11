# app.py – Complete file with auth blueprint + user state refresh

import os
import sys
import time
import secrets
import logging
import atexit
from datetime import datetime
from logging.handlers import RotatingFileHandler

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g, flash

from config import Config
from db import (
    get_student_by_phone, get_student_by_id, create_student, is_admin,
    close_db_connections, close_db, execute_with_retry,
)
from utils import (
    get_somali_time_display, validate_csrf, ensure_csrf_token, time_ago,
    get_accent_colours, ACCENT_MAP, get_somali_time_db,
)
from startup import verify_startup, get_startup_health
from database import get_database_health
from errors import register_error_handlers
from error_models import get_error_stats, get_error_log_count

# ============================================
# DEPLOYMENT SAFETY CHECK: Single worker
# ============================================
def ensure_single_worker():
    if os.environ.get('FORCE_MULTI_WORKER') == '1':
        return
    if 'GUNICORN_WORKER' in os.environ:
        logging.critical(
            "WARNING: Gunicorn detected. Live Quiz state is per-process and WILL BE INCONSISTENT "
            "if more than one worker is used. Please set --workers=1 in your Gunicorn command."
        )
    try:
        import multiprocessing
        if multiprocessing.cpu_count() > 1 and 'GUNICORN_WORKER' not in os.environ:
            logging.warning("Multiple CPU cores detected; ensure only one process serves Live Quiz requests.")
    except Exception:
        pass

ensure_single_worker()

# ============================================
# BLUEPRINT IMPORTS
# ============================================

from blueprints.auth_bp import auth_bp
from blueprints.dashboard_bp import dashboard_bp
from blueprints.groups_bp import groups_bp
from blueprints.pdfs_bp import pdfs_bp
from blueprints.admin_bp import admin_bp
from blueprints.admin_errors_bp import admin_errors_bp
from blueprints.quiz_bp import quiz_bp
from blueprints.live_quiz_bp import live_quiz_bp
from blueprints.notifications_bp import notifications_bp
from blueprints.saved_content_bp import saved_content_bp
from blueprints.achievements_bp import achievements_bp
from blueprints.admin_activity_bp import admin_activity_bp
from blueprints.admin_backup_bp import admin_backup_bp
from blueprints.upgrade_bp import upgrade_bp
from blueprints.admin_platform_bp import admin_platform_bp

# ============================================
# PDF ADMIN BLUEPRINT & TELEGRAM BOT (Webhook)
# ============================================
from blueprints.pdf_admin_bp import pdf_admin_bp
from bot.bot import start_bot, stop_bot, get_bot
from bot.handlers import process_telegram_update
from bot.db import init_bot_db

# ============================================
# INTERACTIONS BLUEPRINT
# ============================================
from blueprints.interactions_bp import interactions_bp

# ============================================
# HISTORY BLUEPRINT
# ============================================
from blueprints.history_bp import history_bp
from history_logger import recover_pending_entries

# ============================================
# SETTINGS & PROFILE BLUEPRINTS
# ============================================
from blueprints.settings_bp import settings_bp
from blueprints.profile_bp import profile_bp

# ============================================
# Activity logger
# ============================================
from activity_logger import (
    log_activity, log_admin_action, log_quiz_complete,
    log_backup_event, init_activity_logger
)

# ============================================
# BASE DIRECTORY & LOGGING
# ============================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = Config.LOG_DIR

if not os.path.exists(LOG_DIR):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass

# ============================================
# INSTANCE DIRECTORY (flag files)
# ============================================

INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
try:
    os.makedirs(INSTANCE_DIR, exist_ok=True)
except Exception:
    pass

USER_STATE_FLAG = os.path.join(INSTANCE_DIR, 'user_state_changes.flag')
if not os.path.exists(USER_STATE_FLAG):
    try:
        with open(USER_STATE_FLAG, 'w') as f:
            f.write('0')
    except Exception:
        pass

# ============================================
# REQUEST ID FILTER
# ============================================

class RequestIDFilter(logging.Filter):
    def filter(self, record):
        try:
            record.request_id = getattr(g, 'request_id', 'no-req')
        except RuntimeError:
            record.request_id = 'no-req'
        return True

# ============================================
# LOGGING SETUP
# ============================================

log_format = '%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s'
log_datefmt = '%Y-%m-%d %H:%M:%S'

log_file = os.path.join(LOG_DIR, 'app.log')
try:
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=Config.LOG_MAX_BYTES,
        backupCount=Config.LOG_BACKUP_COUNT
    )
    file_handler.setFormatter(logging.Formatter(log_format, log_datefmt))
    file_handler.setLevel(getattr(logging, Config.LOG_LEVEL, logging.WARNING))
except Exception:
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter(log_format, log_datefmt))
    file_handler.setLevel(logging.WARNING)

error_log_file = os.path.join(LOG_DIR, 'error.log')
try:
    error_file_handler = RotatingFileHandler(
        error_log_file,
        maxBytes=Config.LOG_MAX_BYTES,
        backupCount=Config.LOG_BACKUP_COUNT
    )
    error_file_handler.setFormatter(logging.Formatter(log_format, log_datefmt))
    error_file_handler.setLevel(logging.ERROR)
except Exception:
    error_file_handler = logging.FileHandler(error_log_file)
    error_file_handler.setFormatter(logging.Formatter(log_format, log_datefmt))
    error_file_handler.setLevel(logging.ERROR)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter(log_format, log_datefmt))
console_handler.setLevel(logging.ERROR)

request_id_filter = RequestIDFilter()
for handler in [file_handler, error_file_handler, console_handler]:
    handler.addFilter(request_id_filter)

root_logger = logging.getLogger()
root_logger.setLevel(getattr(logging, Config.LOG_LEVEL, logging.WARNING))
root_logger.addHandler(file_handler)
root_logger.addHandler(error_file_handler)
root_logger.addHandler(console_handler)

logging.getLogger('werkzeug').setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

# ============================================
# STARTUP VERIFICATION
# ============================================

logger.info("=" * 60)
logger.info("NUUNPLATFORM STARTUP - Starting verification")
logger.info("=" * 60)

if not verify_startup():
    logger.critical("=" * 60)
    logger.critical("STARTUP VERIFICATION FAILED")
    logger.critical("Application cannot start. Please check the logs.")
    logger.critical("=" * 60)

logger.info("Startup verification PASSED")
logger.info("=" * 60)

# ============================================
# BACKUP INTEGRATION
# ============================================

BACKUP_AVAILABLE = False
BACKUP_LOCK_FILE = None
try:
    from backup import (
        BackupManager, acquire_backup_lock, release_backup_lock,
        is_backup_locked, BACKUP_LOCK_FILE
    )
    BACKUP_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Backup module not available: {e}")

BACKUP_TRIGGER_TOKEN = Config.BACKUP_TRIGGER_TOKEN
BACKUP_ENABLED = Config.BACKUP_ENABLED

_backup_manager = None

def get_backup_manager():
    global _backup_manager
    if _backup_manager is None and BACKUP_AVAILABLE:
        try:
            _backup_manager = BackupManager()
        except Exception as e:
            logger.error(f"Failed to initialize backup manager: {e}")
    return _backup_manager

def execute_backup(backup_type='daily'):
    if not BACKUP_AVAILABLE:
        return {'success': False, 'message': 'Backup module not available'}
    try:
        manager = get_backup_manager()
        if manager is None:
            return {'success': False, 'message': 'Backup manager not available'}
        if is_backup_locked():
            return {'success': False, 'message': 'Backup already running'}
        lock_fd = acquire_backup_lock()
        if lock_fd is None:
            return {'success': False, 'message': 'Could not acquire backup lock'}
        try:
            result = manager.create_backup(backup_type)
            if result['success']:
                logger.info(f"Backup successful: {result['filename']}")
            else:
                logger.error(f"Backup failed: {result['message']}")
            return result
        finally:
            release_backup_lock(lock_fd)
    except Exception as e:
        logger.error(f"Backup error: {e}", exc_info=True)
        return {'success': False, 'message': str(e)}

# ============================================
# CACHE INITIALIZATION
# ============================================

try:
    from cache import get_cache_manager, start_worker
    cache_manager = get_cache_manager()
    logger.info("Cache manager initialized successfully.")

    if Config.REDIS_URL and Config.REDIS_URL.strip():
        if Config.CACHE_WORKER_ENABLED:
            start_worker()
            logger.info("Cache worker started.")
        else:
            logger.info("Cache worker disabled (set CACHE_WORKER_ENABLED=true to enable).")
    else:
        logger.info("Redis not configured; cache worker not started.")
except Exception as e:
    logger.error(f"Cache initialization failed: {e}")

# ============================================
# FLASK APP
# ============================================

app = Flask(__name__)
app.config['SECRET_KEY'] = Config.SECRET_KEY
app.config['PERMANENT_SESSION_LIFETIME'] = Config.PERMANENT_SESSION_LIFETIME
app.debug = False
app.config['DEBUG'] = False
app.config['TESTING'] = False

app.config['SESSION_COOKIE_SECURE'] = Config.SESSION_COOKIE_SECURE
app.config['SESSION_COOKIE_HTTPONLY'] = Config.SESSION_COOKIE_HTTPONLY
app.config['SESSION_COOKIE_SAMESITE'] = Config.SESSION_COOKIE_SAMESITE

app.jinja_env.filters['time_ago'] = time_ago

# ============================================
# REQUEST CONTEXT
# ============================================

@app.before_request
def set_request_id():
    g.request_id = request.headers.get('X-Request-ID') or secrets.token_hex(8)[:8]
    g.start_time = time.time()

@app.after_request
def log_request_end(response):
    if hasattr(g, 'start_time'):
        duration = (time.time() - g.start_time) * 1000
        if duration > 1000:
            logger.warning(
                f"SLOW: {request.method} {request.path} {duration:.0f}ms - "
                f"status={response.status_code}"
            )
    if hasattr(g, 'request_id'):
        response.headers['X-Request-ID'] = g.request_id
    return response

# ============================================
# CSRF PROTECTION
# ============================================

@app.before_request
def generate_csrf_if_needed():
    ensure_csrf_token()

# ============================================
# USER STATE REFRESH (verification, tier)
# ============================================
# Checks the flag file mtime against session['user_state_loaded_at'].
# On change (or 1-hour fallback), reloads tier + is_verified from DB.
# Zero DB cost on the common path: one stat() syscall per request.

@app.before_request
def refresh_user_state_if_needed():
    if 'user_id' not in session:
        return
    if request.path.startswith('/static/'):
        return

    should_reload = False

    # Check 1: flag file mtime
    try:
        if os.path.exists(USER_STATE_FLAG):
            flag_mtime = os.path.getmtime(USER_STATE_FLAG)
            loaded_at = session.get('user_state_loaded_at', 0)
            if flag_mtime > loaded_at:
                should_reload = True
    except Exception:
        pass

    # Check 2: 1-hour fallback
    if not should_reload:
        loaded_at = session.get('user_state_loaded_at', 0)
        if time.time() - loaded_at > 3600:
            should_reload = True

    if not should_reload:
        return

    try:
        student = get_student_by_id(session['user_id'])
        if student:
            session['tier'] = student.get('tier', 'free')
            session['tier_expires_at'] = student.get('tier_expires_at')
            session['is_verified'] = int(student.get('is_verified', 0))
            session['is_admin'] = bool(student.get('is_admin', 0))
            session['user_state_loaded_at'] = time.time()
            session.modified = True
    except Exception as e:
        logger.warning(f"Failed to refresh user state: {e}")

# ============================================
# REGISTER BLUEPRINTS
# ============================================

# Auth first — owns /register, /login, /logout, /help
app.register_blueprint(auth_bp)

app.register_blueprint(dashboard_bp)
app.register_blueprint(groups_bp)
app.register_blueprint(pdfs_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(admin_errors_bp)
app.register_blueprint(quiz_bp)
app.register_blueprint(live_quiz_bp)
app.register_blueprint(notifications_bp)
app.register_blueprint(saved_content_bp)
app.register_blueprint(achievements_bp)
app.register_blueprint(admin_activity_bp)
app.register_blueprint(admin_backup_bp)
app.register_blueprint(upgrade_bp)
app.register_blueprint(admin_platform_bp)

app.register_blueprint(settings_bp)
app.register_blueprint(profile_bp)

# ============================================
# REGISTER PDF ADMIN BLUEPRINT (Secret Path)
# ============================================
PDF_ADMIN_SECRET = Config.PDF_ADMIN_SECRET_PATH
if not PDF_ADMIN_SECRET:
    PDF_ADMIN_SECRET = '/pdf-admin-' + os.urandom(8).hex()
elif not PDF_ADMIN_SECRET.startswith('/'):
    PDF_ADMIN_SECRET = '/' + PDF_ADMIN_SECRET
app.register_blueprint(pdf_admin_bp, url_prefix=PDF_ADMIN_SECRET)
logger.info(f"PDF Admin panel mounted at {PDF_ADMIN_SECRET}")

# ============================================
# REGISTER INTERACTIONS + HISTORY BLUEPRINTS
# ============================================
app.register_blueprint(interactions_bp)
app.register_blueprint(history_bp)

# ============================================
# REGISTER ERROR HANDLERS
# ============================================

register_error_handlers(app)

# ============================================
# TEARDOWN
# ============================================

@app.teardown_appcontext
def close_db_connection(exception=None):
    close_db(exception)

@atexit.register
def cleanup():
    logger.info("Application shutdown initiated.")
    stop_bot()
    try:
        close_db_connections()
    except Exception as e:
        logger.warning(f"Cleanup error: {e}")

# ============================================
# INITIALIZE BOT DATABASE
# ============================================
try:
    init_bot_db()
    logger.info("Bot database initialized")
except Exception as e:
    logger.error(f"Failed to initialize bot database: {e}")

# ============================================
# TELEGRAM WEBHOOK ROUTE
# ============================================

@app.route('/webhook/<token>', methods=['POST'])
def telegram_webhook(token):
    expected_token = Config.TELEGRAM_BOT_TOKEN
    if not expected_token or token != expected_token:
        logger.warning(f"Webhook token mismatch.")
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        update_data = request.get_json()
        if not update_data:
            return jsonify({'error': 'Invalid data'}), 400

        bot = get_bot()
        process_telegram_update(bot, update_data)
        return jsonify({'status': 'ok'}), 200
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({'error': 'Internal error'}), 500

# ============================================
# START BOT (Set Webhook, No Polling)
# ============================================
try:
    start_bot()
    logger.info("Bot webhook configured successfully.")
except Exception as e:
    logger.error(f"Failed to configure bot webhook: {e}")

# ============================================
# HISTORY SYSTEM – RECOVER PENDING ENTRIES
# ============================================
try:
    recover_pending_entries()
    logger.info("History queue recovery checked.")
except Exception as e:
    logger.error(f"History recovery error: {e}")

# ============================================
# ROUTES
# ============================================

@app.route('/favicon.ico')
def favicon():
    return '', 204


@app.route('/health', methods=['GET'])
def health_check():
    db_health = get_database_health()
    startup_health = get_startup_health()

    backup_health = {'status': 'unknown'}
    if BACKUP_AVAILABLE:
        try:
            manager = get_backup_manager()
            if manager:
                backup_health = manager.health_check()
            else:
                backup_health = {'status': 'error', 'issues': ['Backup manager unavailable']}
        except Exception as e:
            backup_health = {'status': 'error', 'issues': [str(e)]}
    else:
        backup_health = {'status': 'disabled'}

    cache_health = {'status': 'unknown'}
    try:
        from cache import get_cache_manager
        cache = get_cache_manager()
        stats = cache.get_metrics()
        cache_health = {'status': 'healthy', 'stats': stats}
    except Exception as e:
        cache_health = {'status': 'error', 'error': str(e)}

    error_stats = get_error_stats()

    critical_issues = []
    if not db_health.get('exists'):
        critical_issues.append('Database does not exist')
    if not db_health.get('openable'):
        critical_issues.append('Database cannot be opened')
    if not db_health.get('integrity'):
        critical_issues.append('Database integrity check failed')
    if not db_health.get('tables_ok'):
        critical_issues.append('Missing required tables')
    if not db_health.get('wal_enabled'):
        critical_issues.append('WAL mode is disabled')

    is_healthy = len(critical_issues) == 0
    status_code = 200 if is_healthy else 503

    return jsonify({
        'status': 'healthy' if is_healthy else 'critical',
        'timestamp': get_somali_time_display(),
        'request_id': getattr(g, 'request_id', 'no-req'),
        'components': {
            'database': db_health,
            'backup': backup_health,
            'cache': cache_health,
            'errors': error_stats,
        },
        'critical_issues': critical_issues
    }), status_code


@app.route('/docs')
def docs():
    return render_template('docs.html')


@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard.home'))
    return redirect(url_for('auth.login'))


# ============================================
# BACKUP TRIGGER ENDPOINTS
# ============================================

@app.route('/backup/trigger', methods=['GET'])
def trigger_backup():
    if not Config.BACKUP_ENABLED:
        return jsonify({'status': 'disabled', 'message': 'Backup system is disabled'}), 503

    token = request.args.get('token')
    if token != Config.BACKUP_TRIGGER_TOKEN:
        logger.warning(f"Unauthorized backup trigger attempt from {request.remote_addr}")
        return jsonify({'error': 'Unauthorized'}), 401

    backup_type = request.args.get('type', 'daily')
    if backup_type not in ['daily', 'weekly', 'monthly', 'manual']:
        backup_type = 'daily'

    if not BACKUP_AVAILABLE:
        return jsonify({'status': 'error', 'message': 'Backup module not available'}), 503

    if is_backup_locked():
        return jsonify({'status': 'skipped', 'message': 'Backup already running'}), 409

    start_time = time.time()
    result = execute_backup(backup_type)
    duration = time.time() - start_time

    if result and result['success']:
        return jsonify({
            'status': 'success',
            'message': f'Backup created: {result["filename"]}',
            'filename': result['filename'],
            'size_kb': round(result['size_bytes'] / 1024, 2),
            'duration_seconds': round(duration, 2),
            'timestamp': get_somali_time_display(),
            'warning': 'Web-triggered backups are not recommended. Use scheduled tasks.'
        }), 200
    else:
        error_msg = result.get('message', 'Backup failed') if result else 'Backup failed'
        return jsonify({'status': 'error', 'message': error_msg}), 500


@app.route('/backup/status', methods=['GET'])
def backup_status():
    if 'user_id' not in session or not is_admin(session['user_id']):
        return jsonify({'error': 'Unauthorized'}), 401

    if not BACKUP_AVAILABLE:
        return jsonify({'error': 'Backup module not available'}), 503

    manager = get_backup_manager()
    if manager is None:
        return jsonify({'error': 'Backup manager not available'}), 503

    try:
        health = manager.health_check()
        return jsonify(health)
    except Exception as e:
        logger.error(f"Backup status error: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================
# CONTEXT PROCESSOR
# ============================================

@app.context_processor
def utility_processor():
    token = ensure_csrf_token()
    settings = {}
    accent_colours = {'hex': '#FF3138', 'hover': '#E62B32', 'light': '#FFEBE8'}

    if 'user_id' in session:
        if 'settings' in session:
            settings = session['settings']
        else:
            try:
                from services.settings_service import SettingsService
                settings = SettingsService.get_all(session['user_id'])
                session['settings'] = settings
                session.modified = True
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to load settings: {e}")
                settings = {}

        accent = settings.get('appearance.accent', 'red')
        theme = settings.get('appearance.theme', 'system')
        is_dark = (theme == 'dark')
        from utils import get_accent_colours as get_accent
        accent_colours = get_accent(accent, is_dark)

    # Pending upgrade requests count (admins only)
    pending_upgrades_count = 0
    if session.get('is_admin'):
        try:
            cursor = execute_with_retry(
                "SELECT COUNT(*) AS cnt FROM upgrade_requests WHERE status = 'pending'"
            )
            row = cursor.fetchone()
            pending_upgrades_count = row['cnt'] if row else 0
        except Exception:
            pending_upgrades_count = 0

    return {
        'session': session,
        'is_admin': session.get('is_admin', False),
        'somali_time': get_somali_time_display,
        'csrf_token': token,
        'settings': settings,
        'accent_colours': accent_colours,
        'pending_upgrades_count': pending_upgrades_count,
        'super_admin_phone': Config.SUPER_ADMIN_PHONE,
    }

# ============================================
# INITIALIZE ACTIVITY LOGGER
# ============================================

init_activity_logger(app)

# ============================================
# INITIALIZE LIVE QUIZ STATE MANAGER
# ============================================

try:
    from live_quiz_state import initialize_state_manager, recover_active_quizzes
    initialize_state_manager()
    recover_active_quizzes()
    logger.info("Live Quiz State Manager initialized and recovered active quizzes.")
except Exception as e:
    logger.error(f"Live Quiz State Manager initialization failed: {e}", exc_info=True)

# ============================================
# RUN APP
# ============================================

if __name__ == '__main__':
    debug_mode = Config.FLASK_DEBUG
    port = Config.PORT
    logger.info(f"Server starting at: {get_somali_time_display()}")
    logger.info(f"Database path: {Config.DATABASE_PATH}")
    logger.info(f"Bot database path: {Config.BOT_DATABASE_PATH}")
    logger.info(f"Log directory: {Config.LOG_DIR}")
    logger.info(f"Backup directory: {Config.BACKUP_DIR}")
    logger.info(f"Redis URL: {Config.REDIS_URL or 'Not configured'}")
    logger.info(f"Debug mode: {debug_mode}")
    app.run(debug=debug_mode, host='0.0.0.0', port=port)