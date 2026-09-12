# services/i18n_service.py
# ------------------------------------------------------------------
# Lightweight i18n runtime for NuunPlatform.
#
# Design:
#   - Two language catalogs on disk: translations/en.json, translations/so.json
#   - Both loaded into process memory on first access, cached for life.
#   - t(key, **kwargs) looks up the current language (resolved per-request
#     via Flask's `g`), falls back to English, then to the key itself.
#
# Language resolution (resolve_language):
#   1. No user → "en"
#   2. User's setting `appearance.language` != "so" → "en"
#   3. User LACKS the `language_somali` feature → "en"  (silent fallback)
#   4. Otherwise → "so"
#
# THE ENTITLEMENT CHECK IS AUTHORITATIVE.
# Admin can grant or revoke `language_somali` for any tier at any time
# through the entitlement admin panel. Expiry, downgrade, and manual
# revocation are all handled automatically by this fallback — no code
# change needed when the admin policy changes.
#
# Jinja globals exposed:
#   t()                — main translation function
#   tr(), _()          — aliases (useful when `t` is shadowed in a template)
#   current_language() — resolved language code for the current request
# ------------------------------------------------------------------

import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional

from flask import g, session

logger = logging.getLogger(__name__)


# ============================================
# CONSTANTS
# ============================================

BASE_DIR = Path(__file__).resolve().parent.parent
TRANSLATIONS_DIR = BASE_DIR / 'translations'

DEFAULT_LANGUAGE = 'en'
SUPPORTED_LANGUAGES = ('en', 'so')

# The entitlement feature that gates language choice.
# Matches entitlements_seed.json.
FEATURE_KEY = 'language_somali'

# The user_settings key that stores the user's preference.
SETTING_KEY = 'appearance.language'


# ============================================
# CATALOG CACHE
# ============================================

_CATALOGS: Dict[str, Dict[str, str]] = {}
_CATALOG_LOCK = threading.RLock()


def _load_catalog(lang: str) -> Dict[str, str]:
    """Read one language catalog from disk. Returns {} if missing."""
    path = TRANSLATIONS_DIR / f'{lang}.json'
    if not path.exists():
        logger.warning(f"Translation catalog not found: {path}")
        return {}
    try:
        with path.open('r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.error(f"Catalog {path} is not a JSON object")
            return {}
        return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        logger.error(f"Failed to load catalog {path}: {e}", exc_info=True)
        return {}


def _get_catalog(lang: str) -> Dict[str, str]:
    """Return the cached catalog for a language, loading it on first use."""
    if lang not in _CATALOGS:
        with _CATALOG_LOCK:
            if lang not in _CATALOGS:
                _CATALOGS[lang] = _load_catalog(lang)
    return _CATALOGS.get(lang, {})


def invalidate_catalog_cache(lang: Optional[str] = None) -> None:
    """Drop catalog cache. Called on deploy or admin reload."""
    with _CATALOG_LOCK:
        if lang is None:
            _CATALOGS.clear()
        elif lang in _CATALOGS:
            del _CATALOGS[lang]


# ============================================
# LANGUAGE RESOLUTION
# ============================================

def resolve_language(user_id: Optional[int] = None) -> str:
    """
    Return the effective display language for a user.

    Silent fallback to English happens automatically whenever the user:
      - is not logged in
      - has not selected Somali in settings
      - has lost access to `language_somali` (tier expired, downgraded,
        feature revoked for their tier by an admin)
    """
    if user_id is None:
        try:
            user_id = session.get('user_id')
        except Exception:
            user_id = None

    if not user_id:
        return DEFAULT_LANGUAGE

    # 1. Read user's stated preference
    pref = None
    try:
        settings = session.get('settings') or {}
        pref = settings.get(SETTING_KEY)
    except Exception:
        pref = None

    if pref not in SUPPORTED_LANGUAGES:
        try:
            from services.settings_service import SettingsService
            pref = SettingsService.get_value(user_id, SETTING_KEY)
        except Exception:
            pref = None

    if pref != 'so':
        return DEFAULT_LANGUAGE

    # 2. Gate the preference through the entitlement system.
    #    The admin controls who has access to `language_somali` —
    #    this check reads their policy at request time.
    try:
        from services import entitlement_service
        if not entitlement_service.check(user_id, FEATURE_KEY):
            return DEFAULT_LANGUAGE
    except Exception as e:
        logger.warning(f"Language entitlement check failed: {e}")
        return DEFAULT_LANGUAGE

    return 'so'


def get_current_language() -> str:
    """
    Per-request language. Cached on Flask's `g` so it resolves once
    per request even if called dozens of times during template rendering.
    """
    try:
        cached = getattr(g, '_i18n_lang', None)
        if cached is not None:
            return cached
        lang = resolve_language()
        setattr(g, '_i18n_lang', lang)
        return lang
    except RuntimeError:
        # No request context (background thread, CLI) — default
        return DEFAULT_LANGUAGE


# ============================================
# TRANSLATION LOOKUP
# ============================================

def t(key: str, **kwargs) -> str:
    """
    Translate a key in the current language.

    - Missing keys fall back to English, then to the key itself.
    - `{name}` style placeholders are filled from kwargs.
    - In DEBUG, missing keys are rendered as `[key]` so they're easy to spot.
    """
    if not key:
        return ''

    lang = get_current_language()
    catalog = _get_catalog(lang)
    text = catalog.get(key)

    if text is None and lang != DEFAULT_LANGUAGE:
        text = _get_catalog(DEFAULT_LANGUAGE).get(key)

    if text is None:
        from config import Config
        if getattr(Config, 'DEBUG', False):
            return f'[{key}]'
        return key

    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


# ============================================
# JINJA WIRING
# ============================================

def register_jinja(app) -> None:
    """
    Expose translation functions to every Jinja template.

    Templates can call any of:
        {{ t('key') }}     — main function
        {{ tr('key') }}    — alias (safe if `t` is shadowed)
        {{ _('key') }}     — gettext-style alias
    """
    app.jinja_env.globals['t'] = t
    app.jinja_env.globals['tr'] = t
    app.jinja_env.globals['_'] = t
    app.jinja_env.globals['current_language'] = get_current_language