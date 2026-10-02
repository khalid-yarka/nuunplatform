# app.py – Complete file with modular admin system + maintenance mode
#         All import-time blockers removed (deferred to first request)

import os
import sys
import time
import json
import secrets
import logging
import atexit
import threading
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, g, flash,
)

from config import Config
from db import (
    get_student_by_phone, get_student_by_id, create_student, is_admin,
    close_db_connections, close_db, execute_with_retry,
)
from utils import (
    get_somali_time_display, validate_csrf, ensure_csrf_token, time_ago,
    get_accent_colours, ACCENT_MAP, get_somali_time_db, SOMALI_TIMEZONE,
    somali_dt_filter, somali_time_only_filter, somali_date_only_filter,
    format_somali_time,
)
from tier_config import normalize_tier
from startup import verify_startup, get_startup_health
from database import get_database_health
from errors import register_error_handlers
from error_models import get_error_stats, get_error_log_count


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


BG_THREADS_ENABLED = os.environ.get('ENABLE_BG_THREADS', '0') == '1'
DEFER_HEAVY_IMPORTS = os.environ.get('DEFER_HEAVY_IMPORTS', '1') == '1'


# ============================================
# BLUEPRINT IMPORTS
# ============================================
from docs import docs_bp

from blueprints.auth_bp import auth_bp
from blueprints.dashboard_bp import dashboard_bp
from blueprints.onboarding_bp import onboarding_bp
from blueprints.groups_bp import groups_bp
from blueprints.pdfs_bp import pdfs_bp
from blueprints.quiz_bp import quiz_bp
from blueprints.live_quiz import live_quiz_bp
from blueprints.notifications_bp import notifications_bp
from blueprints.push_bp import push_bp
from blueprints.saved_content_bp import saved_content_bp
from blueprints.achievements_bp import achievements_bp
from blueprints.focus_bp import focus_bp

from blueprints.admin import register_admin_blueprints

from blueprints.settings_bp import settings_bp
from blueprints.profile_bp import profile_bp
from blueprints.interactions_bp import interactions_bp
from blueprints.history_bp import history_bp


from history_logger import recover_pending_entries
from activity_logger import (
    log_activity, log_admin_action, log_quiz_complete,
    log_backup_event, init_activity_logger,
)
from services.i18n_service import register_jinja as register_i18n


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = Config.LOG_DIR

if not os.path.exists(LOG_DIR):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass


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
# ONBOARDING / DISCOUNT WINDOW
# ============================================
DISCOUNT_WINDOW_DAYS = 7


# ============================================
# LOGGING
# ============================================
def _format_somali_log_time(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(SOMALI_TIMEZONE)
    hour12 = dt.hour % 12
    if hour12 == 0:
        hour12 = 12
    am_pm = "am" if dt.hour < 12 else "pm"
    weekday = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[dt.weekday()]
    return (
        f"{dt.year}/{dt.month}/{dt.day} "
        f"{hour12}:{dt.minute:02d}:{dt.second:02d} {am_pm} {weekday}"
    )


class SomaliFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return _format_somali_log_time(record.created)


class ModuleRoutingFilter(logging.Filter):
    def __init__(self, allow_prefixes):
        super().__init__()
        self.allow_prefixes = tuple(allow_prefixes)

    def filter(self, record):
        return record.name.startswith(self.allow_prefixes)


class RequestIDFilter(logging.Filter):
    def filter(self, record):
        try:
            record.request_id = getattr(g, 'request_id', 'no-req')
        except RuntimeError:
            record.request_id = 'no-req'
        return True


LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s'
log_formatter = SomaliFormatter(LOG_FORMAT)
request_id_filter = RequestIDFilter()

_root_level = logging.DEBUG if Config.DEBUG else getattr(logging, Config.LOG_LEVEL, logging.INFO)
_console_level = logging.DEBUG if Config.DEBUG else logging.ERROR
_file_size = (5 * 1024 * 1024) if Config.DEBUG else Config.LOG_MAX_BYTES
_file_backups = 5 if Config.DEBUG else Config.LOG_BACKUP_COUNT

WEB_PREFIXES = ('blueprints.', 'services.', 'nuun.', 'app', 'utils', '__main__', 'templates.',)
WORKER_PREFIXES = ('db', 'cache', 'live_quiz_state', 'history_logger', 'activity_logger',
                   'platform_activity', 'redis_state', 'bot.', 'startup', 'migrate',
                   'daily_tasks',)


def _make_rotating_handler(path, level, allow_prefixes=None):
    try:
        h = RotatingFileHandler(path, maxBytes=_file_size, backupCount=_file_backups)
    except Exception:
        h = logging.FileHandler(path)
    h.setLevel(level)
    h.setFormatter(log_formatter)
    h.addFilter(request_id_filter)
    if allow_prefixes is not None:
        h.addFilter(ModuleRoutingFilter(allow_prefixes))
    return h


app_handler = _make_rotating_handler(os.path.join(LOG_DIR, 'app.log'), _root_level, WEB_PREFIXES)
workers_handler = _make_rotating_handler(os.path.join(LOG_DIR, 'workers.log'), _root_level, WORKER_PREFIXES)
error_file_handler = _make_rotating_handler(os.path.join(LOG_DIR, 'error.log'), logging.ERROR, None)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(_console_level)
console_handler.setFormatter(log_formatter)
console_handler.addFilter(request_id_filter)

root_logger = logging.getLogger()
root_logger.setLevel(_root_level)
for _h in (app_handler, workers_handler, error_file_handler, console_handler):
    root_logger.addHandler(_h)

for _lib in ('urllib3', 'requests', 'PIL', 'werkzeug', 'matplotlib', 'telebot', 'asyncio'):
    logging.getLogger(_lib).setLevel(logging.CRITICAL)

logger = logging.getLogger(__name__)


logger.info("=" * 60)
logger.info("NUUNPLATFORM STARTUP - Starting verification")
logger.info("=" * 60)

if not verify_startup():
    logger.critical("=" * 60)
    logger.critical("STARTUP VERIFICATION FAILED")
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
        is_backup_locked, BACKUP_LOCK_FILE,
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


logger.info("Cache manager will initialize lazily on first use.")


# ============================================
# FLASK APP
# ============================================
app = Flask(__name__)
app.config['SECRET_KEY'] = Config.SECRET_KEY
app.config['PERMANENT_SESSION_LIFETIME'] = Config.PERMANENT_SESSION_LIFETIME
app.debug = Config.DEBUG
app.config['DEBUG'] = Config.DEBUG
app.config['TESTING'] = False

app.config['SESSION_COOKIE_SECURE'] = Config.SESSION_COOKIE_SECURE
app.config['SESSION_COOKIE_HTTPONLY'] = Config.SESSION_COOKIE_HTTPONLY
app.config['SESSION_COOKIE_SAMESITE'] = Config.SESSION_COOKIE_SAMESITE

app._started_at = time.time()


# ============================================
# JINJA FILTERS / GLOBALS
# ============================================
app.jinja_env.filters['time_ago']       = time_ago
app.jinja_env.filters['somali_dt']      = somali_dt_filter
app.jinja_env.filters['somali_time']    = somali_time_only_filter
app.jinja_env.filters['somali_date']    = somali_date_only_filter

# Short aliases — the timestamp format fix uses `| dt`, `| dt_time`, `| dt_date`.
app.jinja_env.filters['dt']        = somali_dt_filter
app.jinja_env.filters['dt_time']   = somali_time_only_filter
app.jinja_env.filters['dt_date']   = somali_date_only_filter

app.jinja_env.globals['normalize_tier'] = normalize_tier
app.jinja_env.globals['fmt_dt']         = format_somali_time

register_i18n(app)


# ============================================
# REQUEST CONTEXT
# ============================================
@app.before_request
def set_request_id():
    g.request_id = request.headers.get('X-Request-ID') or secrets.token_hex(8)[:8]
    g.start_time = time.time()


# ============================================
# SECURITY HEADERS
# ============================================
# Added globally to every response. These close the clickjacking,
# MIME-sniffing, referrer-leak, and (partially) XSS classes of attack.
#
# CSP notes:
#   - The codebase has inline <script> and <style> everywhere, plus
#     inline `onclick=` handlers. A strict CSP would break the app,
#     so 'unsafe-inline' is required for script-src and style-src.
#   - Even with unsafe-inline, CSP still blocks loads from arbitrary
#     origins, forbids framing (frame-ancestors), forbids plugins
#     (object-src), and prevents form hijacking (form-action).
#   - If the app is ever refactored to external .js files and
#     nonce-based script tags, remove 'unsafe-inline' from script-src.
#
# If the request is served over HTTPS, Strict-Transport-Security is
# sent. On plain HTTP (dev), HSTS is skipped so localhost isn't
# permanently pinned to HTTPS by the browser.
#
# PER-PAGE EXEMPTION:
#   The admin PDF edit page embeds a blob-URL PDF inside an iframe.
#   Chrome's PDF viewer refuses to render when the parent page
#   carries any Content-Security-Policy — even one that explicitly
#   allows `frame-src blob:` and `object-src blob:`. The exact
#   reason is that the viewer runs as a browser extension and its
#   internal origin mismatches against any CSP-declared origin set.
#
#   We therefore skip the CSP header entirely on that ONE endpoint.
#   It is admin-only and gated by @admin_can('pdfs.edit'). Every
#   other page retains the strict policy.
# ============================================

_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
    "font-src 'self' https://cdnjs.cloudflare.com data:; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self'; "
    "media-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

# Endpoints that are exempt from the CSP header entirely.
# Matched against request.endpoint (exact) and request.path prefix.
_CSP_EXEMPT_ENDPOINTS = frozenset({
    'admin_content.pdf_edit',
})


def _endpoint_is_csp_exempt(endpoint) -> bool:
    if not endpoint:
        return False
    if endpoint in _CSP_EXEMPT_ENDPOINTS:
        return True
    # Fallback: any endpoint on the admin_content blueprint whose name
    # ends with `pdf_edit`. Covers renamed blueprints.
    if endpoint.startswith('admin_content.') and endpoint.endswith('pdf_edit'):
        return True
    return False


@app.after_request
def add_security_headers(response):
    ctype = (response.headers.get('Content-Type') or '').lower()
    is_pdf = 'application/pdf' in ctype

    # ── PDF responses are exempt from the global policy ──
    # Chrome's built-in PDF viewer runs as a browser extension
    # (mhjfbmdgcfjbbpaeojofohoefgiehjai). Any X-Frame-Options or
    # CSP frame-ancestors value on the PDF response causes Chrome to
    # refuse to display it with "This content is blocked." The PDF
    # bytes themselves are inert data — they don't need framing
    # restrictions. Routes that serve PDFs handle their own headers.
    if not is_pdf:
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault(
            'Permissions-Policy',
            'geolocation=(), microphone=(), camera=(), payment=(), usb=()',
        )
        if 'text/html' in ctype:
            # Skip CSP entirely for the admin PDF edit page so its
            # blob-URL preview iframe can render the PDF viewer.
            try:
                endpoint = request.endpoint
            except Exception:
                endpoint = None

            if not _endpoint_is_csp_exempt(endpoint):
                response.headers.setdefault('Content-Security-Policy', _CSP_POLICY)
            # else: no CSP header at all for this response.
            # We also remove any CSP that might have been set upstream.
            else:
                response.headers.pop('Content-Security-Policy', None)

    # HSTS only over HTTPS
    if request.is_secure:
        response.headers.setdefault(
            'Strict-Transport-Security',
            'max-age=31536000; includeSubDomains',
        )
    return response


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
# MAINTENANCE MODE
# ============================================
_MAINTENANCE_STATE_PATH = os.path.join(INSTANCE_DIR, 'maintenance.json')
_MAINTENANCE_CACHE = {'loaded_at': 0.0, 'state': None}
_MAINTENANCE_CACHE_TTL = 5


def _load_maintenance_state():
    now = time.time()
    if (_MAINTENANCE_CACHE['state'] is not None
            and now - _MAINTENANCE_CACHE['loaded_at'] < _MAINTENANCE_CACHE_TTL):
        return _MAINTENANCE_CACHE['state']

    state = {'enabled': False, 'title': '', 'message': '', 'eta': ''}
    try:
        if os.path.exists(_MAINTENANCE_STATE_PATH):
            with open(_MAINTENANCE_STATE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            state = {
                'enabled': bool(data.get('enabled', False)),
                'title': data.get('title') or "We'll be back soon",
                'message': data.get('message')
                    or "We're performing scheduled maintenance. Please check back shortly.",
                'eta': data.get('eta') or '',
            }
    except Exception as e:
        logger.warning(f"Could not read maintenance state: {e}")

    _MAINTENANCE_CACHE['state'] = state
    _MAINTENANCE_CACHE['loaded_at'] = now
    return state

_MAINT_EXPIRED_LOGGED = False


def _maintenance_is_expired(state) -> bool:
    """
    True when a maintenance window has been on longer than
    MAINTENANCE_MAX_MINUTES. Used as a self-heal guard: if the daily
    runner crashed mid-flight and left the flag ON with no thread
    alive to clear it, the web layer stops honoring the flag once
    the window ages out.

    The flag itself is not rewritten here — the next admin action or
    the next trigger still sees it, and the log records the event.
    """
    if not state.get('enabled'):
        return False
    since_raw = state.get('since')
    if not since_raw:
        # No timestamp — cannot evaluate. Do not expire.
        return False
    try:
        since = datetime.fromisoformat(str(since_raw).replace('Z', '+00:00'))
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        age_minutes = (datetime.now(timezone.utc) - since).total_seconds() / 60.0
        return age_minutes > Config.MAINTENANCE_MAX_MINUTES
    except Exception:
        return False

@app.route('/maintenance', endpoint='maintenance_page')
def maintenance_page():
    state = _load_maintenance_state()
    return render_template(
        'maintenance.html',
        title=state.get('title'),
        message=state.get('message'),
        eta=state.get('eta'),
    ), 503


@app.before_request
def enforce_maintenance_mode():
    path = request.path or '/'

    # ── Always-allowed paths ──
    if path.startswith('/static/'):
        return None
    if path in ('/login', '/logout', '/auth/check-phone'):
        return None
    if path == '/maintenance':
        return None
    if path in ('/favicon.ico', '/health'):
        return None

    # ── Webhook and legacy telegram endpoint ──
    # These must remain reachable so the bot handler can decide what
    # to reply. If they were redirected, Telegram would see a 3xx
    # and treat it as delivery failure, queueing updates until the
    # window closed. The bot's own guard (in bot/handlers.py) is what
    # actually blocks non-admin replies during maintenance.
    if path.startswith('/webhook/'):
        return None
    if path.startswith('/telegram/'):
        return None

    # ── Daily trigger ──
    # The route itself is responsible for auth and for refusing to
    # run while maintenance is already on. It must not be redirected
    # to the maintenance page, or the external caller would get a 3xx
    # it cannot interpret.
    if path == '/daily/trigger':
        return None

    state = _load_maintenance_state()
    if not state.get('enabled'):
        return None

    # ── Self-heal: expire a stuck window ──
    global _MAINT_EXPIRED_LOGGED
    if _maintenance_is_expired(state):
        if not _MAINT_EXPIRED_LOGGED:
            logger.critical(
                f"Maintenance window exceeded {Config.MAINTENANCE_MAX_MINUTES} "
                f"minutes — auto-recovering. Traffic is flowing again. "
                f"Flag remains ON (since={state.get('since')}); clear it "
                f"via /admin/system/maintenance."
            )
            _MAINT_EXPIRED_LOGGED = True
        return None

    user_id = session.get('user_id')
    if user_id and session.get('is_admin'):
        return None

    try:
        from services.admin.roles import is_super_admin
        if is_super_admin():
            return None
    except Exception:
        pass

    wants_json = (
        path.startswith('/api/')
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.accept_mimetypes.best == 'application/json'
    )
    if wants_json:
        return jsonify({
            'error': 'maintenance',
            'message': state.get('message'),
            'title': state.get('title'),
        }), 503

    return redirect(url_for('maintenance_page'))

# ============================================
# CSRF PROTECTION
# ============================================
@app.before_request
def generate_csrf_if_needed():
    ensure_csrf_token()


# ============================================
# SESSION VERSION CHECK
# ============================================
@app.before_request
def refresh_user_state_if_needed():
    if 'user_id' not in session:
        return
    if request.path.startswith('/static/'):
        return

    should_reload = False

    try:
        if os.path.exists(USER_STATE_FLAG):
            flag_mtime = os.path.getmtime(USER_STATE_FLAG)
            loaded_at = session.get('user_state_loaded_at', 0)
            if flag_mtime > loaded_at:
                should_reload = True
    except Exception:
        pass

    if not should_reload:
        loaded_at = session.get('user_state_loaded_at', 0)
        if time.time() - loaded_at > 3600:
            should_reload = True

    if not should_reload:
        try:
            row = execute_with_retry(
                "SELECT session_version FROM students WHERE id = ?",
                (session['user_id'],),
            ).fetchone()
            if row is not None:
                db_sv = int(row['session_version'] or 0)
                session_sv = int(session.get('session_version', 0) or 0)
                if db_sv != session_sv:
                    logger.info(
                        f"Session version mismatch for user {session['user_id']}: "
                        f"session={session_sv}, db={db_sv} — forcing re-login."
                    )
                    session.clear()
                    if request.path.startswith('/api/'):
                        return jsonify({'error': 'Session expired'}), 401
                    return redirect(url_for('auth.login', next=request.path))
        except Exception as e:
            logger.warning(f"session_version check failed: {e}")
        return

    # Full reload path
    try:
        student = get_student_by_id(session['user_id'])
        if student:
            session['tier'] = normalize_tier(student.get('tier', 'free'))
            session['tier_expires_at'] = student.get('tier_expires_at')
            session['is_verified'] = int(student.get('is_verified', 0))
            session['is_admin'] = bool(student.get('is_admin', 0))
            session['session_version'] = int(student.get('session_version', 0) or 0)
            session['user_state_loaded_at'] = time.time()
            session['created_at'] = student.get('created_at')
            try:
                session['first_discount_used'] = int(student.get('first_discount_used') or 0)
            except (TypeError, ValueError):
                session['first_discount_used'] = 0
            session.modified = True
    except Exception as e:
        logger.warning(f"Failed to refresh user state: {e}")


# ============================================
# DEFERRED BACKGROUND WORK
# ============================================
_bg_lock = threading.Lock()
_bg_state = {
    'webhook_started': False,
    'recovery_started': False,
    'activity_logger_started': False,
    'live_quiz_started': False,
    'history_recovery_started': False,
    'bot_db_started': False,
}


def _spawn(name, fn):
    t = threading.Thread(target=fn, daemon=True, name=name)
    t.start()
    logger.info(f"Deferred task scheduled: {name}")


def _run_webhook_setup():
    try:
        from bot.bot import start_bot
        start_bot()
        logger.info("Bot webhook configured successfully (deferred).")
    except Exception as e:
        logger.error(f"Bot webhook setup failed (deferred): {e}")


def _run_history_recovery():
    try:
        recover_pending_entries()
        logger.info("History queue recovery checked (deferred).")
    except Exception as e:
        logger.error(f"History recovery error (deferred): {e}")


def _run_live_quiz_recovery():
    try:
        from live_quiz_state import initialize_state_manager, recover_active_quizzes
        initialize_state_manager()
        logger.info("Live Quiz State Manager initialized (deferred).")
        recover_active_quizzes()
        logger.info("Active quizzes recovered (deferred).")
    except Exception as e:
        logger.error(f"Live Quiz recovery failed (deferred): {e}", exc_info=True)


def _run_activity_logger_init():
    try:
        init_activity_logger(app)
        logger.info("Activity logger initialized (deferred).")
    except Exception as e:
        logger.error(f"Activity logger init failed (deferred): {e}")


def _run_bot_db_init():
    try:
        from bot.db import init_bot_db
        init_bot_db()
        logger.info("Bot database initialized (deferred).")
    except Exception as e:
        logger.error(f"Bot database init failed (deferred): {e}")


@app.before_request
def _kick_deferred_work():
    if not DEFER_HEAVY_IMPORTS:
        return

    with _bg_lock:
        all_done = all(_bg_state.values())
    if all_done:
        return

    with _bg_lock:
        if not _bg_state['webhook_started']:
            _bg_state['webhook_started'] = True
            _spawn('webhook-setup', _run_webhook_setup)
        if not _bg_state['history_recovery_started']:
            _bg_state['history_recovery_started'] = True
            _spawn('history-recovery', _run_history_recovery)
        if not _bg_state['live_quiz_started']:
            _bg_state['live_quiz_started'] = True
            _spawn('live-quiz-recovery', _run_live_quiz_recovery)
        if not _bg_state['activity_logger_started']:
            _bg_state['activity_logger_started'] = True
            _spawn('activity-logger-init', _run_activity_logger_init)
        if not _bg_state['bot_db_started']:
            _bg_state['bot_db_started'] = True
            _spawn('bot-db-init', _run_bot_db_init)
        _bg_state['recovery_started'] = True


# ============================================
# REGISTER BLUEPRINTS
# ============================================
app.register_blueprint(onboarding_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(groups_bp)
app.register_blueprint(pdfs_bp)
app.register_blueprint(quiz_bp)
app.register_blueprint(live_quiz_bp)
app.register_blueprint(notifications_bp)
app.register_blueprint(saved_content_bp)
app.register_blueprint(achievements_bp)
app.register_blueprint(focus_bp)

app.register_blueprint(settings_bp)
app.register_blueprint(profile_bp)
app.register_blueprint(interactions_bp)
app.register_blueprint(history_bp)
app.register_blueprint(push_bp)
app.register_blueprint(docs_bp)

register_admin_blueprints(app)

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
    try:
        from bot.bot import stop_bot
        stop_bot()
    except Exception:
        pass
    try:
        close_db_connections()
    except Exception as e:
        logger.warning(f"Cleanup error: {e}")


# ============================================
# TELEGRAM WEBHOOK ROUTE
# ============================================
@app.route('/webhook/<token>', methods=['POST'])
def telegram_webhook(token):
    expected_token = Config.TELEGRAM_BOT_TOKEN
    # Constant-time comparison to defeat timing side-channels.
    if not expected_token or not secrets.compare_digest(token, expected_token):
        logger.warning("Webhook token mismatch.")
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        update_data = request.get_json()
        if not update_data:
            return jsonify({'error': 'Invalid data'}), 400

        from bot.bot import get_bot
        from bot.handlers import process_telegram_update
        bot = get_bot()
        process_telegram_update(bot, update_data)
        return jsonify({'status': 'ok'}), 200
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({'error': 'Internal error'}), 500


@app.route('/telegram/webhook', methods=['POST'])
def telegram_webhook_legacy():
    """Legacy path — returns 200 so Telegram stops retrying. No-op."""
    return jsonify({'ok': True}), 200


# ============================================
# ROUTES
# ============================================
@app.route('/favicon.ico')
def favicon():
    return '', 204


from flask import send_from_directory


@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json',
                               mimetype='application/manifest+json')

@app.route('/robots.txt')
def robots_txt():
    """
    Serve robots.txt for crawlers. Public pages are crawlable;
    admin, webhook, and heavy PDF endpoints are disallowed.
    """
    base = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')

    lines = [
        'User-agent: *',
        'Allow: /$',
        'Allow: /login',
        'Allow: /register',
        'Allow: /help',
        'Allow: /home',
        'Allow: /pdfs/$',
        'Allow: /groups/$',
        'Allow: /leaderboard',
        '',
        '# Private / admin areas',
        'Disallow: /admin/',
        'Disallow: /webhook/',
        'Disallow: /telegram/',
        'Disallow: /backup/',
        'Disallow: /api/',
        'Disallow: /push/',
        'Disallow: /auth/',
        'Disallow: /settings/',
        'Disallow: /notifications/',
        'Disallow: /saved/',
        'Disallow: /focus/',
        'Disallow: /history/',
        'Disallow: /profile/',
        'Disallow: /quiz/',
        'Disallow: /live-quiz/',
        '',
        '# Heavy / streaming endpoints',
        'Disallow: /pdfs/stream/',
        'Disallow: /pdfs/preview/',
        'Disallow: /pdfs/download/',
        '',
        '# Well-known bot traps',
        'Disallow: /*?pdf=',
        'Disallow: /*?next=',
        'Disallow: /*?saved=',
    ]

    if base:
        lines.append('')
        lines.append(f'Sitemap: {base}/sitemap.xml')

    body = '\n'.join(lines) + '\n'
    return Response(body, mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap_xml():
    """Dynamic sitemap: home, auth, groups, and every public PDF."""
    from db import get_all_pdfs

    base = (getattr(Config, 'BASE_URL', '') or '').rstrip('/')
    if not base:
        return Response('', mimetype='application/xml'), 204

    urls = [
        ('/',           'weekly',  '1.0'),
        ('/login',      'monthly', '0.3'),
        ('/register',   'monthly', '0.3'),
        ('/help',       'monthly', '0.4'),
        ('/pdfs/',      'daily',   '0.9'),
        ('/groups/',    'weekly',  '0.7'),
        ('/leaderboard','weekly',  '0.6'),
    ]

    try:
        pdfs = get_all_pdfs(limit=5000, offset=0)
    except Exception:
        pdfs = []

    for p in pdfs:
        pid = p.get('id')
        if not pid:
            continue
        urls.append((f'/pdfs/?pdf={pid}', 'monthly', '0.5'))

    parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for path, freq, prio in urls:
        parts.append('  <url>')
        parts.append(f'    <loc>{base}{path}</loc>')
        parts.append(f'    <changefreq>{freq}</changefreq>')
        parts.append(f'    <priority>{prio}</priority>')
        parts.append('  </url>')
    parts.append('</urlset>')

    return Response('\n'.join(parts), mimetype='application/xml')

@app.route('/sw.js')
def service_worker():
    resp = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.route('/offline.html')
def offline():
    return render_template('offline.html'), 200


@app.route('/health', methods=['GET'])
def health_check():
    """
    Detailed health report. Only full-content when the caller is
    authenticated as a super admin; otherwise returns a minimal
    status payload so the shape of the platform is not leaked.
    """
    db_health = get_database_health()
    startup_health = get_startup_health()

    critical_issues = []
    if not db_health.get('exists'):
        critical_issues.append('Database does not exist')
    if not db_health.get('openable'):
        critical_issues.append('Database cannot be opened')
    if not db_health.get('tables_ok'):
        critical_issues.append('Missing required tables')
    if not db_health.get('wal_enabled'):
        critical_issues.append('WAL mode is disabled')

    is_healthy = len(critical_issues) == 0
    status_code = 200 if is_healthy else 503

    # Who's asking?
    is_admin_caller = False
    try:
        if session.get('user_id') and session.get('is_admin'):
            from services.admin.roles import is_super_admin
            is_admin_caller = is_super_admin()
    except Exception:
        is_admin_caller = False

    if not is_admin_caller:
        # Minimal public payload. No paths, no backup state, no config.
        return jsonify({
            'status': 'healthy' if is_healthy else 'critical',
            'timestamp': get_somali_time_display(),
        }), status_code

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

    return jsonify({
        'status': 'healthy' if is_healthy else 'critical',
        'timestamp': get_somali_time_display(),
        'request_id': getattr(g, 'request_id', 'no-req'),
        'components': {
            'database': db_health,
            'backup': backup_health,
            'cache': cache_health,
            'errors': error_stats,
            'deferred_tasks': dict(_bg_state),
        },
        'critical_issues': critical_issues,
    }), status_code


@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard.home'))
    return render_template('start.html')


# ============================================
# BACKUP TRIGGER ENDPOINT
# ============================================
# POST-only, and refuses to run when the token is the well-known
# default. Previously this was a GET endpoint that accepted a
# default-valued token, letting anyone who read the source code
# force a backup (disk exhaustion) or observe token acceptance.
#
# If you are running a cron-based scheduled backup, switch the cron
# to POST:
#     curl -X POST -H "X-Backup-Token: <TOKEN>" \
#          https://<host>/backup/trigger?type=daily
# ============================================

_DEFAULT_BACKUP_TOKENS = {
    '',
    'change_this_token_in_production',
    'changeme',
    'default',
}


def _read_backup_token_from_request():
    # Prefer the header (does not appear in access logs).
    header = request.headers.get('X-Backup-Token')
    if header:
        return header.strip()
    # Fall back to query string for legacy callers.
    return (request.args.get('token') or '').strip()


@app.route('/backup/trigger', methods=['POST'])
def trigger_backup():
    if not Config.BACKUP_ENABLED:
        return jsonify({'status': 'disabled', 'message': 'Backup system is disabled'}), 503

    expected = (Config.BACKUP_TRIGGER_TOKEN or '').strip()
    if not expected or expected.lower() in _DEFAULT_BACKUP_TOKENS:
        logger.critical(
            "Backup trigger refused: BACKUP_TRIGGER_TOKEN is unset or is "
            "a known default. Set a strong random value in .env."
        )
        return jsonify({
            'error': 'Backup trigger is not configured securely. '
                     'Set BACKUP_TRIGGER_TOKEN in .env to a strong random value.',
        }), 503

    provided = _read_backup_token_from_request()
    if not provided or not secrets.compare_digest(provided, expected):
        logger.warning(
            f"Unauthorized backup trigger attempt from {request.remote_addr}"
        )
        return jsonify({'error': 'Unauthorized'}), 401

    backup_type = request.args.get('type', 'daily')
    if backup_type not in ('daily', 'weekly', 'monthly', 'manual'):
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
        }), 200

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
# DAILY TASK TRIGGER
# ============================================
# POST-only. Token-authenticated. Refuses to run when the token is
# unset, is a known default, or when a run is already in progress.
#
# The external caller receives 202 Accepted before any work begins.
# The work runs in a daemon thread inside this worker. When the
# thread finishes — successfully or by exception — it clears the
# maintenance flag in a finally block. If the worker itself is
# reaped mid-run, the max-age guard in enforce_maintenance_mode is
# the safety net.
# ============================================

_DEFAULT_DAILY_TOKENS = {
    '',
    'change_this_token_in_production',
    'changeme',
    'default',
}


def _read_daily_token_from_request():
    header = request.headers.get('X-Daily-Token')
    if header:
        return header.strip()
    return (request.args.get('token') or '').strip()


def _write_maintenance_json(payload):
    """Atomically replace maintenance.json and bust the cache."""
    try:
        tmp_path = _MAINTENANCE_STATE_PATH + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, _MAINTENANCE_STATE_PATH)
    except Exception as e:
        logger.error(f"Could not write maintenance state: {e}")
        raise
    _MAINTENANCE_CACHE['state'] = None
    _MAINTENANCE_CACHE['loaded_at'] = 0.0


def _run_daily_from_trigger():
    """
    Runs inside the daemon thread spawned by /daily/trigger.
    Acquires the daily lock, runs the full task suite, then clears
    maintenance. Never raises.
    """
    import daily_tasks as _dt
    import argparse as _argparse

    cleared = False
    try:
        if not _dt.acquire_lock():
            logger.warning("daily trigger: could not acquire daily lock — aborting")
            return

        try:
            # Build the same namespace main() would build. We call
            # run_all directly, bypassing argparse and setup_logging,
            # because setup_logging() removes the root logger's
            # handlers — which would silence the Flask logs for the
            # duration of the run.
            args = _argparse.Namespace(
                dry_run=False,
                preview_telegram=False,
                task=None,
                category=None,
                list=False,
                no_telegram=False,
                no_snapshot=False,
                verbose=False,
            )
            rc = _dt.run_all(args, _dt.log)
            logger.info(f"daily trigger: run finished rc={rc}")
        finally:
            try:
                _dt.release_lock()
            except Exception:
                pass
            try:
                _dt.close_db_connections()
            except Exception:
                pass

    except Exception as e:
        logger.error(f"daily trigger: run crashed: {e}", exc_info=True)

    finally:
        try:
            _write_maintenance_json({
                'enabled': False,
                'title': "We'll be back soon",
                'message': (
                    "We're performing scheduled maintenance. "
                    "Please check back shortly."
                ),
                'eta': '',
                'since': None,
            })
            cleared = True
            logger.info("daily trigger: maintenance cleared")
        except Exception as e:
            logger.error(f"daily trigger: could not clear maintenance: {e}")


@app.route('/daily/trigger', methods=['POST'])
def daily_trigger():
    # ── Config hygiene ──
    expected = (Config.DAILY_TRIGGER_TOKEN or '').strip()
    if not expected or expected.lower() in _DEFAULT_DAILY_TOKENS:
        logger.critical(
            "Daily trigger refused: DAILY_TRIGGER_TOKEN is unset or is "
            "a known default. Set a strong random value in .env."
        )
        return jsonify({
            'error': 'Daily trigger is not configured securely.',
        }), 503

    # ── Auth ──
    provided = _read_daily_token_from_request()
    if not provided or not secrets.compare_digest(provided, expected):
        logger.warning(
            f"Unauthorized daily trigger from {request.remote_addr}"
        )
        return jsonify({'error': 'Unauthorized'}), 401

    # ── Lock check ──
    # If a run is in progress, refuse. Do not touch maintenance.
    try:
        lock_path = os.path.join(BASE_DIR, '.daily_tasks.lock')
        if os.path.exists(lock_path):
            age = time.time() - os.path.getmtime(lock_path)
            if age < 2 * 3600:
                return jsonify({
                    'status': 'already_running',
                    'lock_age_seconds': int(age),
                }), 409
    except Exception as e:
        logger.warning(f"daily trigger: lock check failed: {e}")

    # ── Never overwrite an existing maintenance window ──
    # A window that is ON is either a manually-enabled state or a
    # previous stuck run. Either way, the trigger should not touch
    # it. An admin clears it via /admin/system/maintenance.
    current = _load_maintenance_state()
    if current.get('enabled'):
        return jsonify({
            'status': 'maintenance_already_on',
            'since': current.get('since'),
        }), 409

    # ── Import check first ──
    # If daily_tasks cannot be imported, fail before touching the
    # maintenance flag. Otherwise the flag would be written and no
    # thread would exist to clear it.
    try:
        import daily_tasks as _dt   # noqa: F401
    except Exception as e:
        logger.error(f"daily trigger: import failed: {e}", exc_info=True)
        return jsonify({
            'error': f'Daily tasks unavailable: {e}',
        }), 503

    # ── Enable maintenance ──
    since_iso = datetime.now(timezone.utc).isoformat()
    try:
        _write_maintenance_json({
            'enabled': True,
            'title': 'Daily maintenance',
            'message': (
                "The platform is running its daily maintenance. "
                "Usually back within a few minutes."
            ),
            'eta': 'A few minutes',
            'since': since_iso,
        })
    except Exception as e:
        logger.error(f"daily trigger: could not enable maintenance: {e}")
        return jsonify({'error': 'Could not enable maintenance'}), 500

    # ── Spawn the runner ──
    t = threading.Thread(
        target=_run_daily_from_trigger,
        daemon=True,
        name='daily-runner',
    )
    t.start()

    logger.info(f"daily trigger: accepted, runner started, since={since_iso}")
    return jsonify({
        'status': 'accepted',
        'since': since_iso,
    }), 202

# ============================================
# DOCS HELP URL CONTEXT
# ============================================
@app.context_processor
def _docs_help_url_context():
    try:
        from docs.help_urls import help_url_for_request
        return {'docs_help_url': help_url_for_request(request)}
    except Exception:
        return {'docs_help_url': '/docs/'}


# ============================================
# CONTEXT PROCESSOR
# ============================================
def _parse_dt_loose(raw, tz):
    """
    Accept any of these and return a tz-aware datetime:
      '2026-09-27T14:30:00+03:00'
      '2026-09-27T14:30:00'
      '2026-09-27 14:30:00'
      '2026-09-27'
    Returns None if unparseable.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt
    except (ValueError, TypeError):
        pass
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(s[:19], fmt)
            return dt.replace(tzinfo=tz)
        except (ValueError, TypeError):
            continue
    return None


@app.context_processor
def utility_processor():
    token = ensure_csrf_token()
    settings = {}
    accent_colours = {'hex': '#FF3138', 'hover': '#E62B32', 'light': '#FFEBE8'}

    # ── Discount chip + trial banner data ──
    discount_chip = None
    trial_banner = None

    if 'user_id' in session:
        try:
            _now = datetime.now(SOMALI_TIMEZONE)

            # Discount window: N days from registration, until first payment.
            try:
                first_used = int(session.get('first_discount_used') or 0)
            except (TypeError, ValueError):
                first_used = 0

            created_dt = _parse_dt_loose(session.get('created_at'), SOMALI_TIMEZONE)
            if not first_used and created_dt:
                disc_exp = created_dt + timedelta(days=DISCOUNT_WINDOW_DAYS)
                if disc_exp > _now:
                    discount_chip = {
                        'expires_at': disc_exp.isoformat(),
                    }

            # Trial / limited premium grant
            tier = (session.get('tier') or 'free')
            if tier == 'premium':
                exp_dt = _parse_dt_loose(
                    session.get('tier_expires_at'), SOMALI_TIMEZONE
                )
                if exp_dt and exp_dt > _now:
                    total_hours = 24.0
                    remaining_hours = (exp_dt - _now).total_seconds() / 3600.0
                    trial_banner = {
                        'expires_at': exp_dt.isoformat(),
                        'remaining_hours': round(remaining_hours, 1),
                        'is_trial': (first_used == 0 and total_hours >= remaining_hours),
                    }
        except Exception as e:
            logging.getLogger(__name__).warning(
                f"Discount/trial compute failed: {e}"
            )

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

    pending_upgrades_count = 0
    try:
        from services.admin.roles import is_super_admin as _sa_check
        if _sa_check():
            cursor = execute_with_retry(
                "SELECT COUNT(*) AS cnt FROM upgrade_requests WHERE status = 'pending'"
            )
            row = cursor.fetchone()
            pending_upgrades_count = row['cnt'] if row else 0
    except Exception:
        pending_upgrades_count = 0

    has_focus_access = False
    if 'user_id' in session:
        try:
            from services import entitlement_service
            _uid = session['user_id']
            _sl = entitlement_service.get_limit(_uid, 'focus_suggestions')
            _al = entitlement_service.get_level(_uid, 'focus_analytics')
            has_focus_access = ((_sl is None) or (_sl > 0)) or (_al > 0)
        except Exception:
            has_focus_access = False

    _sa_phone = (Config.SUPER_ADMIN_PHONE or '').replace('+', '').replace(' ', '').replace('-', '')
    social_whatsapp = f"https://wa.me/{_sa_phone}" if _sa_phone else ''
    social_tiktok   = Config.TIKTOK_URL or ''
    social_youtube  = Config.YOUTUBE_URL or ''

    whatsapp_group_url = Config.WHATSAPP_GROUP_URL or ''

    from services.admin.capabilities import admin_can as _admin_can
    from services.admin.roles import is_any_admin as _is_any_admin
    from services.admin.roles import is_super_admin as _is_super_admin

    # ── Upgrade sheet content ──
    # Curated benefit copy for the upgrade sheet, per user language.
    # Kept out of the JS so content edits never require a code deploy.
    upgrade_content = None
    try:
        from services.upgrade_content import get_content as _get_upgrade_content
        _user_lang = 'en'
        if 'user_id' in session:
            try:
                _s = session.get('settings') or {}
                _user_lang = (_s.get('appearance.language') or 'en')
            except Exception:
                _user_lang = 'en'
        upgrade_content = _get_upgrade_content(_user_lang)
    except Exception as e:
        logging.getLogger(__name__).warning(
            f"upgrade_content load failed: {e}"
        )
        upgrade_content = None
  
    return {
        'session': session,
        'is_admin': session.get('is_admin', False),
        'is_any_admin': _is_any_admin,
        'is_super_admin': _is_super_admin,
        'admin_can': _admin_can,
        'somali_time': get_somali_time_display,
        'csrf_token': token,
        'settings': settings,
        'accent_colours': accent_colours,
        'pending_upgrades_count': pending_upgrades_count,
        'super_admin_phone': Config.SUPER_ADMIN_PHONE,
        'has_focus_access': has_focus_access,
        'social_whatsapp': social_whatsapp,
        'social_tiktok': social_tiktok,
        'social_youtube': social_youtube,
        'whatsapp_group_url': whatsapp_group_url,
        'push_enabled': Config.PUSH_ENABLED,
        'discount_chip': discount_chip,
        'trial_banner': trial_banner,
        'upgrade_content': upgrade_content,
    }


try:
    from safe_db import prime_schema
    prime_schema()
except Exception:
    pass


# ============================================
# WELL-KNOWN FILES
# ============================================
@app.route('/.well-known/assetlinks.json', methods=['GET'])
def well_known_assetlinks():
    from flask import send_from_directory, current_app
    return send_from_directory(
        os.path.join(current_app.root_path, 'static', '.well-known'),
        'assetlinks.json',
        mimetype='application/json',
    )


@app.route('/.well-known/security.txt', methods=['GET'])
def well_known_security():
    from flask import Response
    body = (
        "Contact: mailto:security@yourdomain.com\n"
        "Preferred-Languages: en, so\n"
        "Expires: 2027-12-31T23:59:59.000Z\n"
    )
    return Response(body, mimetype='text/plain')


# ============================================
# RUN APP
# ============================================
if __name__ == '__main__':
    port = Config.PORT
    logger.info(f"Server starting at: {get_somali_time_display()}")
    logger.info(f"Database path: {Config.DATABASE_PATH}")
    logger.info(f"Debug mode: {Config.DEBUG}")
    app.run(debug=Config.DEBUG, host='0.0.0.0', port=port)