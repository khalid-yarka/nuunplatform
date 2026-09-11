# bot/utils.py
# Shared utilities for the bot

import os
import logging
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
    is_duplicate_in_bot
)

logger = logging.getLogger(__name__)

# Global bot instance
_bot = None

def get_bot_token():
    """Get the Telegram bot token from Config."""
    token = Config.TELEGRAM_BOT_TOKEN
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not configured in Config")
    return token

def get_bot() -> telebot.TeleBot:
    """Get the global TeleBot instance (creates if needed)."""
    global _bot
    if _bot is None:
        token = get_bot_token()
        _bot = telebot.TeleBot(token, threaded=False)
    return _bot

def get_admin_ids():
    """Parse and return a list of admin Telegram user IDs from Config."""
    ids_str = Config.TELEGRAM_ADMIN_IDS or ''
    if ids_str:
        return [int(x.strip()) for x in ids_str.split(',') if x.strip()]
    return []

def is_admin(user_id: int) -> bool:
    """Check if a Telegram user ID is an admin."""
    return user_id in get_admin_ids()

# ============================================
# Re‑export DB functions for convenience
# ============================================

save_pending_pdf = insert_pending_pdf

# Make these available at the module level
__all__ = [
    'get_bot',
    'get_bot_token',
    'get_admin_ids',
    'is_admin',
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