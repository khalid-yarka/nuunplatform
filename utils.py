# utils.py – Complete file with canonical Somali time format

import re
import secrets
from datetime import datetime, timezone, timedelta
from flask import request, session
import logging

logger = logging.getLogger(__name__)


# ============================================
# SOMALI TIME ZONE (UTC+3)
# ============================================

SOMALI_TIMEZONE = timezone(timedelta(hours=3))

# Locale-independent English weekday abbreviations.
WEEKDAY_ABBR = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


# ============================================
# INTERNAL HELPERS
# ============================================

def _to_somali(dt):
    """Convert any datetime (naive or aware) to Somali timezone."""
    if dt is None:
        return datetime.now(SOMALI_TIMEZONE)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SOMALI_TIMEZONE)


def _format_core(dt, include_seconds=False):
    """
    Canonical formatter.
      without seconds:  2027/9/12 11:09 pm Mon
      with seconds:     2027/9/12 11:09:42 pm Mon
    """
    hour12 = dt.hour % 12
    if hour12 == 0:
        hour12 = 12
    am_pm = 'am' if dt.hour < 12 else 'pm'
    weekday = WEEKDAY_ABBR[dt.weekday()]

    if include_seconds:
        time_part = f"{hour12}:{dt.minute:02d}:{dt.second:02d}"
    else:
        time_part = f"{hour12}:{dt.minute:02d}"

    return f"{dt.year}/{dt.month}/{dt.day} {time_part} {am_pm} {weekday}"


# ============================================
# CANONICAL FORMATTERS
# ============================================

def format_somali_time(dt=None) -> str:
    """
    Canonical display format used platform-wide.
    Example:  2027/9/12 11:09 pm Mon
    """
    return _format_core(_to_somali(dt), include_seconds=False)


def format_somali_time_with_seconds(dt=None) -> str:
    """
    Same as format_somali_time but includes seconds.
    Used in log files where precision matters.
    Example:  2027/9/12 11:09:42 pm Mon
    """
    return _format_core(_to_somali(dt), include_seconds=True)


def get_somali_time() -> datetime:
    """Current time in Somali timezone (UTC+3)."""
    return datetime.now(SOMALI_TIMEZONE)


def get_somali_time_display() -> str:
    """Current time formatted for display."""
    return format_somali_time()


def get_somali_time_db() -> str:
    """
    Current time in ISO 8601 for database storage.
    Storage always stays ISO (sortable, parseable).
    Display formatting happens at the edge.
    """
    return get_somali_time().isoformat()


# ============================================
# PARSING
# ============================================

_ISO_TRY = (
    lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(SOMALI_TIMEZONE)
)

_SLASH_RE = re.compile(
    r'^(\d{4})/(\d{1,2})/(\d{1,2})'           # YYYY/M/D
    r'\s+(\d{1,2}):(\d{2})(?::(\d{2}))?'       # h:mm or h:mm:ss
    r'\s*([AaPp][Mm])'                         # am/pm
    r'(?:\s+[A-Za-z]{3})?$'                    # optional weekday
)

_DASH_RE = re.compile(
    r'^(\d{4})-(\d{1,2})-(\d{1,2})'
    r'\s+(\d{1,2}):(\d{2})(?::(\d{2}))?'
    r'\s*([AaPp][Mm])'
    r'(?:\s+[A-Za-z]{3})?$'
)


def parse_somali_time(time_str: str) -> datetime:
    """
    Parse any of these forms back to a Somali-timezone datetime:
        2027/9/12 11:09 pm Mon
        2027/9/12 11:09:42 pm Mon
        2027-9-12 11:09 pm
        2026-09-14T12:00:00+03:00        (ISO — from DB)
    """
    if not time_str:
        raise ValueError("Empty time string")

    s = str(time_str).strip()

    # 1. ISO 8601
    try:
        return _ISO_TRY(s)
    except (ValueError, TypeError):
        pass

    # 2. Slash format
    m = _SLASH_RE.match(s)
    if not m:
        m = _DASH_RE.match(s)
    if not m:
        raise ValueError(f"Could not parse time string: {time_str}")

    year = int(m.group(1))
    month = int(m.group(2))
    day = int(m.group(3))
    hour = int(m.group(4))
    minute = int(m.group(5))
    second = int(m.group(6)) if m.group(6) else 0
    am_pm = m.group(7).lower()

    if am_pm == 'pm' and hour != 12:
        hour += 12
    elif am_pm == 'am' and hour == 12:
        hour = 0

    return datetime(year, month, day, hour, minute, second, tzinfo=SOMALI_TIMEZONE)


# ============================================
# JINJA FILTERS
# ============================================

def somali_dt_filter(value, include_seconds=False):
    """
    Format any DB timestamp (ISO string, datetime object, or None)
    using the canonical Somali display format.

    Usage in templates:
        {{ user.created_at | somali_dt }}
        {{ error.timestamp | somali_dt(true) }}   → with seconds
    """
    if value is None or value == '':
        return ''

    try:
        if isinstance(value, datetime):
            dt = value
        else:
            s = str(value).strip()
            if not s:
                return ''
            try:
                dt = _ISO_TRY(s)
            except (ValueError, TypeError):
                # Last-resort: try the slash format, then date-only.
                try:
                    dt = parse_somali_time(s)
                except ValueError:
                    try:
                        dt = datetime.strptime(s[:10], '%Y-%m-%d')
                        dt = dt.replace(tzinfo=SOMALI_TIMEZONE)
                    except ValueError:
                        return s  # give back what we got

        return _format_core(dt, include_seconds=include_seconds)
    except Exception:
        return str(value)


def somali_time_only_filter(value):
    """
    Time-of-day only: 11:09 pm
    Useful for compact activity feeds.
    """
    if not value:
        return ''
    try:
        dt = value if isinstance(value, datetime) else _ISO_TRY(str(value))
        hour12 = dt.hour % 12
        if hour12 == 0:
            hour12 = 12
        am_pm = 'am' if dt.hour < 12 else 'pm'
        return f"{hour12}:{dt.minute:02d} {am_pm}"
    except Exception:
        return str(value)


def somali_date_only_filter(value):
    """
    Date only: 2027/9/12 Sun
    """
    if not value:
        return ''
    try:
        dt = value if isinstance(value, datetime) else _ISO_TRY(str(value))
        return f"{dt.year}/{dt.month}/{dt.day} {WEEKDAY_ABBR[dt.weekday()]}"
    except Exception:
        return str(value)


# ============================================
# TIME AGO
# ============================================

def format_time_ago(timestamp: str) -> str:
    """Convert timestamp to 'X ago' format (Somali-aware)."""
    if not timestamp:
        return 'Hadda'
    try:
        dt = parse_somali_time(timestamp)
        now = get_somali_time()
        diff = now - dt

        if diff.days > 30:
            months = diff.days // 30
            return f'{months} bilood ka hor' if months > 1 else 'bil ka hor'
        elif diff.days > 0:
            return f'{diff.days} maalin ka hor' if diff.days > 1 else 'maalin ka hor'
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f'{hours} saac ka hor' if hours > 1 else 'saac ka hor'
        elif diff.seconds > 60:
            minutes = diff.seconds // 60
            return f'{minutes} daqiiqo ka hor' if minutes > 1 else 'daqiiqo ka hor'
        else:
            return 'Hadda'
    except Exception:
        return 'Hadda'


def time_ago(dt_str: str) -> str:
    """
    Jinja filter version — English output.
    Produces 'X minutes ago' style strings.
    """
    if not dt_str:
        return "Just now"
    try:
        dt = datetime.fromisoformat(str(dt_str).replace('Z', '+00:00'))
        now = get_somali_time()
        diff = now - dt
        seconds = diff.total_seconds()

        if seconds < 60:
            return "Just now"
        minutes = int(seconds // 60)
        if minutes < 60:
            return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
        hours = int(minutes // 60)
        if hours < 24:
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        days = int(hours // 24)
        if days < 7:
            return f"{days} day{'s' if days > 1 else ''} ago"
        weeks = int(days // 7)
        if weeks < 4:
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
        months = int(days // 30)
        if months < 12:
            return f"{months} month{'s' if months > 1 else ''} ago"
        years = int(days // 365)
        return f"{years} year{'s' if years > 1 else ''} ago"
    except Exception:
        return str(dt_str)


# ============================================
# CSRF VALIDATION
# ============================================

def ensure_csrf_token():
    """Ensure a CSRF token exists in the session."""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
        session.modified = True
    return session['csrf_token']


def validate_csrf() -> bool:
    """Validate CSRF token from form or header."""
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    expected = session.get('csrf_token')
    if not expected:
        logger.warning("CSRF validation failed: no token in session.")
        return False
    if not token:
        logger.warning("CSRF validation failed: no token submitted.")
        return False
    if secrets.compare_digest(token, expected):
        return True
    logger.warning("CSRF validation failed: token mismatch.")
    return False


# ============================================
# ACCENT COLOURS
# ============================================

ACCENT_MAP = {
    'red': {
        'hex': '#FF3138',
        'hover': '#E62B32',
        'light': '#FFEBE8',
        'light_dark': '#3A1A20',
    },
    'blue': {
        'hex': '#3B82F6',
        'hover': '#2563EB',
        'light': '#E8F0FE',
        'light_dark': '#1A2A4A',
    },
    'green': {
        'hex': '#10B981',
        'hover': '#059669',
        'light': '#D1FAE5',
        'light_dark': '#1A3A2E',
    },
    'purple': {
        'hex': '#8B5CF6',
        'hover': '#7C3AED',
        'light': '#EDE9FE',
        'light_dark': '#2A1A4A',
    },
    'orange': {
        'hex': '#F59E0B',
        'hover': '#D97706',
        'light': '#FEF3C7',
        'light_dark': '#3A2E1A',
    },
}


def get_accent_colours(accent_name='red', is_dark=False):
    """Return hex colours for a given accent name."""
    data = ACCENT_MAP.get(accent_name, ACCENT_MAP['red'])
    return {
        'hex': data['hex'],
        'hover': data['hover'],
        'light': data['light_dark'] if is_dark else data['light'],
    }