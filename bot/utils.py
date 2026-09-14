# bot/utils.py
# Shared utilities for the bot.

import logging
from typing import List

import telebot

from config import Config
from bot.db import (
    insert_pending_pdf,
    get_pending_pdf_by_id,
    get_pending_pdf_list,
    count_pending_pdfs,
    delete_pending_pdf,
    is_pending_duplicate,
    get_bot_pdf_by_code,
    get_bot_pdf_by_id,
    get_bot_pdfs,
    count_bot_pdfs,
    update_bot_pdf,
    delete_bot_pdf,
    is_bot_duplicate,
    is_duplicate_in_bot,
)

logger = logging.getLogger(__name__)

# Global bot instance
_bot = None


def get_bot_token() -> str:
    """Return the Telegram bot token from Config."""
    token = Config.TELEGRAM_BOT_TOKEN
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not configured in Config")
    return token


def get_bot() -> telebot.TeleBot:
    """Return the global TeleBot instance (create on first use)."""
    global _bot
    if _bot is None:
        token = get_bot_token()
        _bot = telebot.TeleBot(token, threaded=False)
        logger.info("TeleBot instance created.")
    return _bot


# ============================================
# TELEGRAM ID RESOLUTION
# ============================================

def _parse_ids(raw) -> List[int]:
    if not raw:
        return []
    out: List[int] = []
    for part in str(raw).split(','):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except (ValueError, TypeError):
            logger.warning(f"bot.utils: ignoring invalid Telegram id {part!r}")
    return out


def get_admin_ids() -> List[int]:
    """Return the list of admin Telegram user IDs."""
    return _parse_ids(Config.TELEGRAM_ADMIN_IDS or '')


def get_super_admin_ids() -> List[int]:
    """
    Return the list of SUPER admin Telegram user IDs.

    Falls back to TELEGRAM_ADMIN_IDS when TELEGRAM_SUPER_ADMIN_IDS
    is not set, so the bot keeps working on installs that never
    configured the new variable.
    """
    super_ids = _parse_ids(getattr(Config, 'TELEGRAM_SUPER_ADMIN_IDS', '') or '')
    if super_ids:
        return super_ids
    return get_admin_ids()


def get_all_admin_recipients() -> List[int]:
    """
    Return the union of super admin and admin IDs, deduped.

    Useful when a notification should reach both groups.
    """
    combined = list(dict.fromkeys(get_super_admin_ids() + get_admin_ids()))
    return combined


def is_admin(user_id: int) -> bool:
    """True if the Telegram user ID belongs to a regular or super admin."""
    return user_id in get_admin_ids() or user_id in get_super_admin_ids()


def is_super_admin(user_id: int) -> bool:
    """True if the Telegram user ID belongs to a super admin."""
    return user_id in get_super_admin_ids()


# ============================================
# Re-exports for convenience
# ============================================

save_pending_pdf = insert_pending_pdf

__all__ = [
    'get_bot',
    'get_bot_token',
    'get_admin_ids',
    'get_super_admin_ids',
    'get_all_admin_recipients',
    'is_admin',
    'is_super_admin',
    'save_pending_pdf',
    'get_pending_pdf_by_id',
    'get_pending_pdf_list',
    'count_pending_pdfs',
    'delete_pending_pdf',
    'is_pending_duplicate',
    'get_bot_pdf_by_code',
    'get_bot_pdf_by_id',
    'get_bot_pdfs',
    'count_bot_pdfs',
    'update_bot_pdf',
    'delete_bot_pdf',
    'is_bot_duplicate',
    'is_duplicate_in_bot',
]