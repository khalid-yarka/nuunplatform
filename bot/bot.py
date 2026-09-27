# bot/bot.py
# Telegram bot initialization using webhook (no polling)

import os
import logging
import requests
import telebot
from telebot import types

from bot.handlers import process_telegram_update
from bot.utils import get_bot_token, get_bot as get_bot_instance
from config import Config

logger = logging.getLogger(__name__)

# Global bot instance
_bot = None

def get_bot():
    """Get the global TeleBot instance (creates if needed)."""
    global _bot
    if _bot is None:
        token = get_bot_token()
        _bot = telebot.TeleBot(token, threaded=False)
        logger.info("TeleBot instance created.")
    return _bot

# ============================================
# WEBHOOK STATE INSPECTION
# ============================================

def _get_webhook_info(token):
    """
    Fetch current webhook state from Telegram.

    Returns the `result` dict on success, or None if the API call
    failed (network, bad token, non-ok response). Callers must treat
    None as "unknown state" and fall back to setting the webhook
    unconditionally — better to over-set than to leave the bot
    unreachable.
    """
    try:
        url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                return data.get('result') or {}
        logger.warning(
            "getWebhookInfo returned non-ok response: %s",
            (response.text or '')[:200],
        )
        return None
    except Exception as e:
        logger.warning("getWebhookInfo failed: %s", e)
        return None


def _webhook_is_current(info, expected_url, expected_allowed):
    """
    Compare Telegram's current webhook state against what this app
    expects to have registered.

    Returns True only if BOTH the URL and the allowed_updates set
    match exactly. A URL match with the wrong update types would
    silently break callbacks / messages, so we don't treat URL-only
    equality as "correct".
    """
    if info is None:
        return False

    current_url = (info.get('url') or '').strip()
    if current_url != expected_url:
        return False

    # Telegram returns None (or omits the field) when all update types
    # are accepted. Since this app always registers an explicit list,
    # a missing/empty value means the webhook was set by something
    # else — treat as not matching.
    current_allowed = info.get('allowed_updates')
    if current_allowed is None:
        current_allowed = []
    current_set = set(current_allowed)
    expected_set = set(expected_allowed)

    if current_set != expected_set:
        return False

    return True


# ============================================
# WEBHOOK MANAGEMENT
# ============================================

def set_webhook():
    """
    Ensure the webhook is registered with Telegram.

    If Telegram already has the correct URL and allowed_updates, this
    is a no-op. Otherwise the webhook is (re)registered.

    Why check first?
      • Avoids an unnecessary setWebhook call on every app boot.
      • Preserves Telegram's server-side webhook diagnostics
        (last_error_message, last_error_date, pending_update_count)
        which setWebhook resets.
      • Avoids the destructive `drop_pending_updates=True` flag
        discarding queued updates when the webhook is already fine.
    """
    token = get_bot_token()
    base_url = Config.BASE_URL
    if not base_url:
        logger.error("BASE_URL not configured in Config! Cannot set webhook.")
        return False

    webhook_path = f"/webhook/{token}"
    webhook_url = f"{base_url}{webhook_path}"
    expected_allowed = ["message", "callback_query"]

    # ── Step 1: inspect current state ──
    info = _get_webhook_info(token)

    if info is not None:
        current_url = (info.get('url') or '').strip()

        if _webhook_is_current(info, webhook_url, expected_allowed):
            # Already correct — leave Telegram's state intact.
            pending = info.get('pending_update_count', 0) or 0
            last_err = info.get('last_error_message')
            logger.info(
                "Webhook already correctly set: %s (pending=%d)",
                webhook_url, pending,
            )
            if last_err:
                logger.warning(
                    "Telegram reports last webhook error: %s (at %s)",
                    last_err, info.get('last_error_date'),
                )
            return True

        # Mismatch — log what differs so the cause is visible in logs.
        if current_url != webhook_url:
            logger.info(
                "Webhook URL mismatch — current=%r expected=%r. Re-registering.",
                current_url, webhook_url,
            )
        else:
            current_allowed = info.get('allowed_updates') or []
            logger.info(
                "Webhook URL matches but allowed_updates differ — "
                "current=%r expected=%r. Re-registering.",
                current_allowed, expected_allowed,
            )
    else:
        logger.info(
            "Could not read current webhook state from Telegram — "
            "will call setWebhook unconditionally."
        )

    # ── Step 2: (re)register ──
    try:
        url = f"https://api.telegram.org/bot{token}/setWebhook"
        payload = {
            "url": webhook_url,
            "allowed_updates": expected_allowed,
            "drop_pending_updates": True,
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200 and response.json().get('ok'):
            logger.info(f"Webhook set successfully: {webhook_url}")
            return True
        else:
            logger.error(f"Failed to set webhook: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error setting webhook: {e}")
        return False


def delete_webhook():
    """Delete the webhook."""
    token = get_bot_token()
    try:
        url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        response = requests.get(url, timeout=10)
        if response.status_code == 200 and response.json().get('ok'):
            logger.info("Webhook deleted successfully.")
        else:
            logger.warning(f"Failed to delete webhook: {response.text}")
    except Exception as e:
        logger.warning(f"Error deleting webhook: {e}")


def start_bot():
    """Set up the webhook (no polling)."""
    set_webhook()
    logger.info("Bot configured to use webhook.")


def stop_bot():
    """Clean up webhook (optional)."""
    delete_webhook()
    logger.info("Bot webhook removed.")


# ============================================
# BOT COMMAND HANDLERS (for testing / fallback)
# ============================================

def register_handlers(bot):
    """Register message handlers (for polling mode, if ever used)."""

    @bot.message_handler(commands=['start'])
    def handle_start(message):
        from bot.handlers import handle_start_with_code, handle_start
        # Check if there's a code after /start
        if len(message.text.split()) > 1:
            handle_start_with_code(bot, message)
        else:
            handle_start(bot, message)

    @bot.message_handler(commands=['help'])
    def handle_help(message):
        from bot.handlers import handle_help
        handle_help(bot, message)

    @bot.message_handler(content_types=['document'])
    def handle_document(message):
        from bot.handlers import handle_document
        handle_document(bot, message)

    @bot.callback_query_handler(func=lambda call: call.data.startswith('pdf_admin_'))
    def handle_callback(call):
        from bot.handlers import handle_callback
        handle_callback(bot, call)

    logger.info("Bot handlers registered.")