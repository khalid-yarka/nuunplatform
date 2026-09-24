import os
from dotenv import load_dotenv
from datetime import timedelta
from pathlib import Path

# ============================================
# LOAD .env FILE EXPLICITLY
# ============================================
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(env_path)

BASE_DIR = Path(__file__).resolve().parent


def _env_bool(key: str, default: str = 'false') -> bool:
    return os.getenv(key, default).lower() in ('true', '1', 'yes', 'on')


class Config:
    # ============================================
    # DEBUG / DEV MODE
    # ============================================
    DEBUG = False

    # ============================================
    # SECURITY
    # ============================================
    SECRET_KEY = os.getenv('SECRET_KEY')
    ADMIN_ERROR_PASSWORD = os.getenv('ADMIN_ERROR_PASSWORD')
    SUPER_ADMIN_PHONE = os.getenv('SUPER_ADMIN_PHONE', '')

    if not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable must be set")
    if not ADMIN_ERROR_PASSWORD:
        raise ValueError("ADMIN_ERROR_PASSWORD environment variable must be set")

    # ============================================
    # DATABASE (Main)
    # ============================================
    DATABASE_PATH = os.getenv('DATABASE_PATH')
    if DATABASE_PATH:
        if not os.path.isabs(DATABASE_PATH):
            DATABASE_PATH = str(BASE_DIR / DATABASE_PATH)
    else:
        DATABASE_PATH = str(BASE_DIR / 'nuunplatform.db')

    # ============================================
    # BOT DATABASE (separate)
    # ============================================
    BOT_DATABASE_PATH = os.getenv('BOT_DATABASE_PATH', str(BASE_DIR / 'bot_data.db'))

    DB_TIMEOUT = float(os.getenv('DB_TIMEOUT', '30.0'))
    DB_BUSY_TIMEOUT = int(os.getenv('DB_BUSY_TIMEOUT', '30000'))
    DB_RETRY_ATTEMPTS = int(os.getenv('DB_RETRY_ATTEMPTS', '7'))
    DB_RETRY_INITIAL_DELAY = float(os.getenv('DB_RETRY_INITIAL_DELAY', '0.1'))
    DB_MAX_RETRY_DELAY = float(os.getenv('DB_MAX_RETRY_DELAY', '3.0'))
    DB_RETRY_BACKOFF_MULTIPLIER = float(os.getenv('DB_RETRY_BACKOFF_MULTIPLIER', '2.0'))

    # ============================================
    # SESSION
    # ============================================
    SESSION_TYPE = 'filesystem'
    PERMANENT_SESSION_LIFETIME_DAYS = int(os.getenv('PERMANENT_SESSION_LIFETIME_DAYS', '1'))
    PERMANENT_SESSION_LIFETIME = timedelta(days=PERMANENT_SESSION_LIFETIME_DAYS)
    SESSION_COOKIE_SECURE = _env_bool('SESSION_COOKIE_SECURE', 'false')
    SESSION_COOKIE_HTTPONLY = _env_bool('SESSION_COOKIE_HTTPONLY', 'true')
    SESSION_COOKIE_SAMESITE = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')
    ADMIN_SESSION_TIMEOUT = int(os.getenv('ADMIN_SESSION_TIMEOUT', '1800'))

    # ============================================
    # WEB PUSH (VAPID)
    # ============================================
    VAPID_PUBLIC_KEY  = os.getenv('VAPID_PUBLIC_KEY', '')
    VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', '')
    VAPID_SUBJECT     = os.getenv('VAPID_SUBJECT', 'mailto:admin@yourdomain.com')
    PUSH_ENABLED      = bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)

    # ============================================
    # PATHS
    # ============================================
    BACKUP_DIR = os.getenv('BACKUP_DIR')
    if BACKUP_DIR:
        if not os.path.isabs(BACKUP_DIR):
            BACKUP_DIR = str(BASE_DIR / BACKUP_DIR)
    else:
        BACKUP_DIR = str(BASE_DIR / 'BACKUPS')

    LOG_DIR = os.getenv('LOG_DIR')
    if LOG_DIR:
        if not os.path.isabs(LOG_DIR):
            LOG_DIR = str(BASE_DIR / LOG_DIR)
    else:
        LOG_DIR = str(BASE_DIR / 'logs')

    UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', 'static/uploads')
    if not os.path.isabs(UPLOAD_FOLDER):
        UPLOAD_FOLDER = str(BASE_DIR / UPLOAD_FOLDER)

    MAX_CONTENT_LENGTH = 50 * 1024 * 1024

    # ============================================
    # BACKUP
    # ============================================
    BACKUP_ENABLED = _env_bool('BACKUP_ENABLED', 'true')
    BACKUP_TRIGGER_TOKEN = os.getenv('BACKUP_TRIGGER_TOKEN', 'change_this_token_in_production')
    BACKUP_RETENTION_DAILY = int(os.getenv('BACKUP_RETENTION_DAILY', '7'))
    BACKUP_RETENTION_WEEKLY = int(os.getenv('BACKUP_RETENTION_WEEKLY', '4'))
    BACKUP_RETENTION_MONTHLY = int(os.getenv('BACKUP_RETENTION_MONTHLY', '12'))

    # ============================================
    # QUIZ
    # ============================================
    RATING_TIME = int(os.getenv('RATING_TIME', '10'))
    LIVE_QUIZ_TIME_PER_QUESTION = int(os.getenv('LIVE_QUIZ_TIME_PER_QUESTION', '30'))
    LIVE_QUIZ_MAX_PARTICIPANTS = int(os.getenv('LIVE_QUIZ_MAX_PARTICIPANTS', '50'))

    # ============================================
    # RATE LIMITING
    # ============================================
    RATE_LIMIT_DEFAULT = os.getenv('RATE_LIMIT_DEFAULT', '200 per day;50 per hour')
    RATE_LIMIT_LOGIN = os.getenv('RATE_LIMIT_LOGIN', '5 per minute')
    RATE_LIMIT_ADMIN = os.getenv('RATE_LIMIT_ADMIN', '10 per minute')

    # ============================================
    # LOGGING
    # ============================================
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
    LOG_MAX_BYTES = int(os.getenv('LOG_MAX_BYTES', str(2 * 1024 * 1024)))
    LOG_BACKUP_COUNT = int(os.getenv('LOG_BACKUP_COUNT', '3'))

    LOG_CONSOLE = _env_bool('LOG_CONSOLE', 'false')
    LOG_ACCESS = _env_bool('LOG_ACCESS', 'false')
    LOG_SQL = _env_bool('LOG_SQL', 'false')

    # ============================================
    # EMAIL (admin-only, for error dashboard)
    # ============================================
    SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    SMTP_FROM = os.getenv('SMTP_FROM', '')
    SMTP_TO = os.getenv('SMTP_TO', '')
    EMAIL_ENABLED = bool(SMTP_USER and SMTP_PASSWORD and SMTP_TO)
    ERROR_EMAIL_DEDUP_WINDOW = int(os.getenv('ERROR_EMAIL_DEDUP_WINDOW', '300'))

    # ============================================
    # ERROR LOGGING
    # ============================================
    ERROR_RETENTION_DAYS = int(os.getenv('ERROR_RETENTION_DAYS', '30'))
    ERROR_LOG_SAMPLE_RATE = float(os.getenv('ERROR_LOG_SAMPLE_RATE', '1.0'))

    # ============================================
    # CACHE
    # ============================================
    REDIS_URL = os.getenv('REDIS_URL', '')
    CACHE_LOCAL_MAX_SIZE = int(os.getenv('CACHE_LOCAL_MAX_SIZE', '1000'))
    CACHE_LOCAL_TTL = int(os.getenv('CACHE_LOCAL_TTL', '60'))
    CACHE_SERIALIZATION = os.getenv('CACHE_SERIALIZATION', 'json')
    REDIS_MAX_CONNECTIONS = int(os.getenv('REDIS_MAX_CONNECTIONS', '10'))
    CACHE_WORKER_ENABLED = _env_bool('CACHE_WORKER_ENABLED', 'false')

    CACHE_TTL = {
        'user': {'profile': 300, 'preferences': 600},
        'subject': {'list': 3600, 'data': 1800},
        'quiz': {'state': 60, 'participants': 30, 'leaderboard': 10},
        'leaderboard': {'global': 30, 'subject': 30},
        'pdf': {'list': 600, 'metadata': 600},
        'group': {'list': 600, 'data': 600},
        'notification': {'unread': 10, 'list': 60},
        'admin': {'stats': 300},
        'session': {'data': 86400},
    }

    # ============================================
    # TELEGRAM BOT (Webhook Mode)
    # ============================================
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    TELEGRAM_BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME', 'nuunplatform_bot')
    TELEGRAM_ADMIN_IDS = os.getenv('TELEGRAM_ADMIN_IDS', '')
    TELEGRAM_SUPER_ADMIN_IDS = os.getenv('TELEGRAM_SUPER_ADMIN_IDS', '')
    BASE_URL = os.getenv('BASE_URL', 'https://yourdomain.com')

    # ============================================
    # SOCIAL LINKS (dashboard footer + FAB)
    # ============================================
    # WhatsApp uses SUPER_ADMIN_PHONE (defined above) for personal
    # contact. WHATSAPP_GROUP_URL is the community group invite link,
    # surfaced by the floating action button in dashboard_base.html.
    # Leave it blank to hide the FAB entirely.
    TIKTOK_URL = os.getenv('TIKTOK_URL', 'https://www.tiktok.com/@nuunplatform')
    YOUTUBE_URL = os.getenv('YOUTUBE_URL', 'https://www.youtube.com/@nuunplatform')
    WHATSAPP_GROUP_URL = os.getenv('WHATSAPP_GROUP_URL', '')

    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable must be set for Telegram bot functionality")

    # ============================================
    # FLASK / RUN
    # ============================================
    FLASK_DEBUG = DEBUG
    PORT = int(os.getenv('PORT', 5000))

    # ============================================
    # DIRECTORY CREATION & VALIDATION
    # ============================================
    @classmethod
    def ensure_directories(cls):
        directories = [
            cls.BACKUP_DIR,
            cls.LOG_DIR,
            os.path.dirname(cls.UPLOAD_FOLDER),
            os.path.dirname(cls.DATABASE_PATH),
            os.path.dirname(cls.BOT_DATABASE_PATH),
        ]
        for directory in directories:
            if directory and not os.path.exists(directory):
                try:
                    os.makedirs(directory, exist_ok=True)
                    print(f"Created directory: {directory}")
                except Exception as e:
                    print(f"Warning: Could not create directory {directory}: {e}")

    @classmethod
    def validate(cls):
        errors = []
        if not cls.SECRET_KEY or cls.SECRET_KEY == 'dev-secret-key-change-in-production':
            errors.append("SECRET_KEY must be set to a secure value in production")
        if not cls.ADMIN_ERROR_PASSWORD:
            errors.append("ADMIN_ERROR_PASSWORD must be set in .env")
        if cls.EMAIL_ENABLED:
            if not cls.SMTP_USER:
                errors.append("SMTP_USER is missing")
            if not cls.SMTP_PASSWORD:
                errors.append("SMTP_PASSWORD is missing")
            if not cls.SMTP_TO:
                errors.append("SMTP_TO is missing")
        if not cls.TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN must be set for Telegram bot functionality")
        if not cls.BASE_URL or cls.BASE_URL == 'https://yourdomain.com':
            errors.append("BASE_URL must be set to a valid domain")
        return errors

    # ============================================
    # GROUP JOIN RULES
    # ============================================
    GROUP_JOIN_RULES = """
📋 Qodobadan akhri intadan ku biirin groupyda!

1. Groupka ujeedkiisu waa wxbarasho kaliya.
2. Lama ogola wax kabaxsan wax barasho iyo kla faidaysi.
3. Wax " link " lagama ogola in laisla wadaago.
4. Anshaxa waxbarasho ha baal marin.
5. Wasarada waxbarashada xeerarka ka yaal imtixanadka meel loma dhaafo.

Taabo proceeding, hadad u hogaansamayso.
    """


# ============================================
# CREATE DIRECTORIES AFTER CLASS DEFINITION
# ============================================
Config.ensure_directories()

print(f"✅ Config loaded successfully!")
print(f"   Debug mode: {Config.DEBUG}")
print(f"   Database: {Config.DATABASE_PATH}")
print(f"   Backup Dir: {Config.BACKUP_DIR}")
print(f"   Log Dir: {Config.LOG_DIR}")
print(f"   Upload Dir: {Config.UPLOAD_FOLDER}")
print(f"   Bot Database: {Config.BOT_DATABASE_PATH}")
print(f"   Super admin phone: {'configured' if Config.SUPER_ADMIN_PHONE else 'NOT configured'}")
print(f"   Web Push: {'enabled' if Config.PUSH_ENABLED else 'disabled (VAPID keys not set)'}")