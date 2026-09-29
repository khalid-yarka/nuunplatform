# bot/bot.py
# Telegram bot initialization using webhook (no polling)
#
# ── Webhook lifecycle (this revision):
#    set_webhook() now performs a HEALTH check, not just a config check.
#
#    A healthy webhook → skip entirely. No delete, no set. Nothing
#    is touched on Telegram's side, so diagnostics and pending
#    updates are preserved across restarts.
#
#    An unhealthy webhook → explicit deleteWebhook, then setWebhook.
#    The delete first makes the transition deterministic in the case
#    where the bot token changed, or Telegram still holds a stale
#    registration that setWebhook alone would not fully replace.
#
#    "Unhealthy" means any of:
#      • the URL differs from what this app expects
#      • the allowed_updates set differs
#      • Telegram reports a recent delivery error
#      • Telegram reports a recent sync error
#
#    A stale error (older than the freshness window) is ignored —
#    webhooks recover, and an error from three days ago is not a
#    reason to tear down a working webhook.

import os
import time
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
# WEBHOOK HEALTH CONSTANTS
# ============================================

# An error timestamp older than this is ignored. The webhook is
# considered recovered if the last error is older than the window.
_WEBHOOK_ERROR_FRESHNESS_SECONDS = 10 * 60  # 10 minutes

# Above this pending count, combined with a recent error, the
# webhook is treated as unhealthy. Below it, a small backlog is
# normal during a brief outage and is delivered on recovery.
_WEBHOOK_PENDING_UNHEALTHY_THRESHOLD = 50


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


def _url_matches(info, expected_url):
    """True if Telegram's registered URL equals the expected URL."""
    current_url = (info.get('url') or '').strip()
    return current_url == expected_url


def _allowed_updates_match(info, expected_allowed):
    """
    True if the allowed_updates set matches exactly.

    Telegram returns None (or omits the field) when all update types
    are accepted. Since this app always registers an explicit list,
    a missing/empty value means the webhook was set by something
    else — treat as not matching.
    """
    current_allowed = info.get('allowed_updates')
    if current_allowed is None:
        current_allowed = []
    return set(current_allowed) == set(expected_allowed)


def _has_recent_error(info):
    """
    True if Telegram reports a recent delivery or sync error.

    `last_error_date` is set when Telegram fails to deliver an
    update to the URL. `last_synchronization_error_date` is set when
    Telegram fails to reach the URL during a health probe. Either,
    inside the freshness window, means the endpoint is currently
    broken.
    """
    now = time.time()
    for key in ('last_error_date', 'last_synchronization_error_date'):
        ts = info.get(key)
        if not ts:
            continue
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            continue
        if (now - ts) < _WEBHOOK_ERROR_FRESHNESS_SECONDS:
            return True
    return False


def _webhook_is_healthy(info, expected_url, expected_allowed):
    """
    Return True only if the webhook is BOTH correctly configured AND
    currently healthy.

    Checks in order:
      1. URL matches exactly.
      2. allowed_updates set matches exactly.
      3. No fresh delivery error in the freshness window.
      4. No fresh sync error in the freshness window.
      5. Pending update count is below the unhealthy threshold when
         a recent error is present (defensive double-check).
    """
    if info is None:
        return False

    if not _url_matches(info, expected_url):
        return False

    if not _allowed_updates_match(info, expected_allowed):
        return False

    has_err = _has_recent_error(info)

    if has_err:
        pending = info.get('pending_update_count', 0) or 0
        try:
            pending = int(pending)
        except (TypeError, ValueError):
            pending = 0
        if pending >= _WEBHOOK_PENDING_UNHEALTHY_THRESHOLD:
            return False
        # A fresh error with a small pending count still counts as
        # unhealthy — the endpoint is failing, the queue just has
        # not built up yet.
        return False

    return True


# ============================================
# WEBHOOK MANAGEMENT
# ============================================

def set_webhook():
    """
    Ensure the webhook is registered with Telegram and healthy.

    Healthy path:  no-op. Nothing is deleted, nothing is set.
    Unhealthy path: deleteWebhook, then setWebhook with
                    drop_pending_updates=True.

    Why check first?
      • Avoids unnecessary API calls on every app boot.
      • Preserves Telegram's server-side webhook diagnostics
        (last_error_message, last_error_date, pending_update_count)
        that setWebhook resets.
      • Avoids discarding queued updates that accumulated during a
        brief outage — they are delivered on the healthy path.
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

        # Log a warning for any recent error, whether or not the
        # webhook is otherwise healthy. Useful for diagnostics.
        last_err = info.get('last_error_message')
        last_err_date = info.get('last_error_date')
        if last_err:
            logger.warning(
                "Telegram reports last webhook error: %s (at %s)",
                last_err, last_err_date,
            )

        if _webhook_is_healthy(info, webhook_url, expected_allowed):
            pending = info.get('pending_update_count', 0) or 0
            logger.info(
                "Webhook healthy — skipping registration: %s (pending=%d)",
                webhook_url, pending,
            )
            return True

        # Not healthy — log exactly what differs so the cause is
        # visible in the log.
        if not _url_matches(info, webhook_url):
            logger.info(
                "Webhook URL mismatch — current=%r expected=%r. "
                "Re-registering.",
                current_url, webhook_url,
            )
        elif not _allowed_updates_match(info, expected_allowed):
            logger.info(
                "Webhook URL matches but allowed_updates differ — "
                "current=%r expected=%r. Re-registering.",
                info.get('allowed_updates'), expected_allowed,
            )
        elif _has_recent_error(info):
            logger.warning(
                "Webhook reports recent error — last_error_message=%r "
                "last_error_date=%s pending=%s. Re-registering.",
                last_err,
                last_err_date,
                info.get('pending_update_count'),
            )
        else:
            logger.info(
                "Webhook flagged unhealthy for an unrecognized reason — "
                "re-registering."
            )
    else:
        logger.info(
            "Could not read current webhook state from Telegram — "
            "will delete and re-register unconditionally."
        )

    # ── Step 2: delete the existing webhook (best-effort) ──
    # A delete first makes the transition deterministic when the
    # token changed, or when Telegram holds a stale registration
    # that setWebhook alone would not replace cleanly.
    try:
        del_url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        del_resp = requests.post(
            del_url,
            json={"drop_pending_updates": True},
            timeout=10,
        )
        if del_resp.status_code == 200 and del_resp.json().get('ok'):
            logger.info("Previous webhook deleted.")
        else:
            # Non-fatal — setWebhook below will overwrite whatever
            # is still registered.
            logger.warning(
                "deleteWebhook returned non-ok response: %s",
                (del_resp.text or '')[:200],
            )
    except Exception as e:
        logger.warning("deleteWebhook failed (non-fatal): %s", e)

    # ── Step 3: register the new webhook ──
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
        response = requests.post(url, timeout=10)
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