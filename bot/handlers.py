# bot/handlers.py
# Message and callback handlers for telebot.
#
# NOTE — protect_content policy:
#   • Regular users AND regular admins → protect_content=True  (no forward, no save)
#   • Super admins only                → protect_content=False (full access)
#
# The rule lives in one place: `_protect(user_id)`.
#
# NOTE — force-join gate:
#   Before the bot delivers a stored PDF to a non-admin user, it
#   verifies the user has joined the configured channel and group.
#   The gate is a courtesy, not a security control, and fails open
#   on any error: an unconfigured ID, a network failure, or a
#   get_chat_member rejection all let the delivery proceed. See
#   `_force_join_config`, `_is_member_of`, `_check_force_join`.

import logging
import telebot
from telebot import types

from bot.utils import (
    is_duplicate_in_bot, save_pending_pdf,
    is_admin, is_super_admin,
)
from bot.db import (
    count_pending_pdfs, get_pending_pdf_list, get_bot_pdf_by_code,
    get_join_gate, set_join_gate, delete_join_gate,
    bump_join_gate_attempts,
)
from config import Config

logger = logging.getLogger(__name__)


# ============================================
# PROTECT CONTENT POLICY
# ============================================

def _protect(user_id: int) -> bool:
    """
    Return the value to pass as `protect_content` for this recipient.

    Super admins → False  (they may forward / save / screenshot)
    Everyone else (regular users + regular admins) → True
    """
    return not is_super_admin(user_id)


# ============================================
# FORCE-JOIN GATE
# ============================================

def _parse_chat_id(raw):
    """
    Normalize a channel/group id from config into an int (or None).
    Accepts '-1001234567890', '-1001234567890 ' etc. Non-numeric
    identifiers (e.g. '@channelname') are accepted as strings.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        # Telegram also accepts @username for public chats.
        return s


def _force_join_config():
    """
    Return a dict of gate settings, or None if the gate is disabled
    or misconfigured. Never raises.
    """
    try:
        if not getattr(Config, 'TELEGRAM_FORCE_JOIN_ENABLED', False):
            return None
        channel_id = _parse_chat_id(
            getattr(Config, 'TELEGRAM_FORCE_CHANNEL_ID', '') or ''
        )
        group_id = _parse_chat_id(
            getattr(Config, 'TELEGRAM_FORCE_GROUP_ID', '') or ''
        )
        if not channel_id and not group_id:
            return None
        return {
            'channel_id': channel_id,
            'channel_invite': (
                getattr(Config, 'TELEGRAM_FORCE_CHANNEL_INVITE', '') or ''
            ).strip(),
            'group_id': group_id,
            'group_invite': (
                getattr(Config, 'TELEGRAM_FORCE_GROUP_INVITE', '') or ''
            ).strip(),
            'max_attempts': int(
                getattr(Config, 'TELEGRAM_FORCE_JOIN_MAX_ATTEMPTS', 10) or 10
            ),
        }
    except Exception as e:
        logger.warning(f"_force_join_config failed: {e}")
        return None


def _is_member_of(bot, chat_id, user_id):
    """
    Three-state membership check.

    Returns:
        True  → user is a member (creator, administrator, member, or
                restricted-but-in-the-chat)
        False → user is definitively not in the chat (left or kicked)
        None  → unknown; the caller must fail open
    """
    try:
        member = bot.get_chat_member(chat_id, user_id)
        status = getattr(member, 'status', None)
        if status in ('creator', 'administrator', 'member', 'restricted'):
            return True
        if status in ('left', 'kicked'):
            return False
        logger.warning(
            f"_is_member_of: unexpected status {status!r} for "
            f"chat={chat_id} user={user_id}"
        )
        return None
    except Exception as e:
        # Bot not admin, chat not found, network error, Telegram 5xx —
        # all fall here and all fail open.
        logger.warning(
            f"_is_member_of failed for chat={chat_id} user={user_id}: {e}"
        )
        return None


def _check_force_join(bot, user_id, cfg):
    """
    Two-step gate check.

    Returns:
        'ok'      → user may proceed (both required chats cleared, or
                    the check was inconclusive and failed open)
        'channel' → user must join the channel first
        'group'   → user must join the group first
    """
    if not cfg:
        return 'ok'

    channel_id = cfg.get('channel_id')
    group_id = cfg.get('group_id')

    if channel_id:
        result = _is_member_of(bot, channel_id, user_id)
        if result is False:
            return 'channel'
        # True or None → proceed

    if group_id:
        result = _is_member_of(bot, group_id, user_id)
        if result is False:
            return 'group'

    return 'ok'


def _prompt_join_channel(bot, chat_id, code):
    """Send the channel-join prompt with an inline 'I joined' button."""
    cfg = _force_join_config() or {}
    invite = cfg.get('channel_invite') or ''

    text = (
        "📢 *One quick step before your PDF:*\n\n"
        "Please join our *channel* first.\n"
        "Then tap *✅ I joined* below."
    )

    markup = types.InlineKeyboardMarkup()
    row = []
    if invite:
        row.append(types.InlineKeyboardButton("📢 Join channel", url=invite))
    row.append(types.InlineKeyboardButton(
        "✅ I joined", callback_data='join_recheck',
    ))
    markup.row(*row)

    try:
        bot.send_message(
            chat_id, text, parse_mode='Markdown', reply_markup=markup,
        )
    except Exception as e:
        logger.error(f"_prompt_join_channel send failed: {e}")


def _prompt_join_group(bot, chat_id, code):
    """Send the group-join prompt with an inline 'I joined' button."""
    cfg = _force_join_config() or {}
    invite = cfg.get('group_invite') or ''

    text = (
        "👥 *Almost there:*\n\n"
        "Please join our *group* first.\n"
        "Then tap *✅ I joined* below."
    )

    markup = types.InlineKeyboardMarkup()
    row = []
    if invite:
        row.append(types.InlineKeyboardButton("👥 Join group", url=invite))
    row.append(types.InlineKeyboardButton(
        "✅ I joined", callback_data='join_recheck',
    ))
    markup.row(*row)

    try:
        bot.send_message(
            chat_id, text, parse_mode='Markdown', reply_markup=markup,
        )
    except Exception as e:
        logger.error(f"_prompt_join_group send failed: {e}")


def _deliver_pdf(bot, chat_id, user_id, code, bot_pdf):
    """
    Send a stored PDF with its description and a Browse More button.
    Mirrors the original handle_start_with_code behaviour, but takes
    the resolved PDF row as an argument so both the gate path and
    the direct path can share it.
    """
    try:
        file_id = bot_pdf['file_id']

        caption = f"📄 *{bot_pdf['title']}*\n\n"
        if bot_pdf.get('description'):
            caption += f"{bot_pdf['description']}\n\n"
        caption += f"📌 *Code:* `{code}`\n"
        caption += f"🔗 *Available on our platform with more study materials!*"

        base_url = Config.BASE_URL.rstrip('/')
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "📚 Browse More PDFs",
                url=f"{base_url}/pdfs",
            )
        )

        bot.send_document(
            chat_id,
            file_id,
            caption=caption,
            parse_mode='Markdown',
            reply_markup=markup,
            protect_content=_protect(user_id),
        )
    except Exception as e:
        logger.error(f"_deliver_pdf failed for code {code}: {e}")
        try:
            bot.send_message(
                chat_id,
                "❌ Sorry, I couldn't retrieve the PDF. "
                "Please try again later.",
                protect_content=_protect(user_id),
            )
        except Exception:
            pass


def _recheck_join(bot, chat_id, user_id):
    """
    The recheck path used by both the inline button and the message
    fallback. Reads the pending gate row, re-verifies membership,
    and either advances the stage or delivers.

    Never raises. Fails open on any inconclusive membership check.
    """
    try:
        row = get_join_gate(user_id)
    except Exception:
        row = None

    if not row:
        return

    cfg = _force_join_config()
    if not cfg:
        # Gate was disabled after the row was written. Deliver and clear.
        code = row.get('code')
        delete_join_gate(user_id)
        if code:
            bot_pdf = get_bot_pdf_by_code(code)
            if bot_pdf:
                _deliver_pdf(bot, chat_id, user_id, code, bot_pdf)
        return

    # Attempts counter — the cutoff is per gate row, not per stage.
    attempts = bump_join_gate_attempts(user_id)
    if attempts is None:
        # Row vanished mid-flight; nothing to do.
        return
    max_attempts = cfg.get('max_attempts', 10)
    if attempts > max_attempts:
        delete_join_gate(user_id)
        try:
            bot.send_message(
                chat_id,
                "❗ Too many attempts. Please send `/start <code>` again "
                "to restart.",
                parse_mode='Markdown',
            )
        except Exception:
            pass
        return

    stage = row.get('stage')
    code = row.get('code')

    if stage == 'channel':
        if not cfg.get('channel_id'):
            # Config dropped the channel — skip to next stage.
            if cfg.get('group_id'):
                set_join_gate(user_id, 'group', code)
                _prompt_join_group(bot, chat_id, code)
            else:
                delete_join_gate(user_id)
                if code:
                    bot_pdf = get_bot_pdf_by_code(code)
                    if bot_pdf:
                        _deliver_pdf(bot, chat_id, user_id, code, bot_pdf)
            return

        result = _is_member_of(bot, cfg['channel_id'], user_id)
        if result is False:
            try:
                bot.send_message(
                    chat_id,
                    "❌ You haven't joined the channel yet. "
                    "Please join, then tap *✅ I joined* again.",
                    parse_mode='Markdown',
                )
            except Exception:
                pass
            return

        # result is True or None — advance or deliver.
        if cfg.get('group_id'):
            set_join_gate(user_id, 'group', code)
            _prompt_join_group(bot, chat_id, code)
        else:
            delete_join_gate(user_id)
            if code:
                bot_pdf = get_bot_pdf_by_code(code)
                if bot_pdf:
                    _deliver_pdf(bot, chat_id, user_id, code, bot_pdf)
        return

    if stage == 'group':
        if not cfg.get('group_id'):
            delete_join_gate(user_id)
            if code:
                bot_pdf = get_bot_pdf_by_code(code)
                if bot_pdf:
                    _deliver_pdf(bot, chat_id, user_id, code, bot_pdf)
            return

        result = _is_member_of(bot, cfg['group_id'], user_id)
        if result is False:
            try:
                bot.send_message(
                    chat_id,
                    "❌ You haven't joined the group yet. "
                    "Please join, then tap *✅ I joined* again.",
                    parse_mode='Markdown',
                )
            except Exception:
                pass
            return

        # True or None — deliver.
        delete_join_gate(user_id)
        if code:
            bot_pdf = get_bot_pdf_by_code(code)
            if bot_pdf:
                _deliver_pdf(bot, chat_id, user_id, code, bot_pdf)
        return


# ============================================
# UPDATE ROUTER
# ============================================

def process_telegram_update(bot: telebot.TeleBot, update_data: dict):
    try:
        update = types.Update.de_json(update_data)
        if update.message:
            handle_message(bot, update.message)
        elif update.callback_query:
            handle_callback(bot, update.callback_query)
        else:
            logger.debug("Unhandled update type.")
    except Exception as e:
        logger.error(f"Error processing update: {e}", exc_info=True)


def handle_message(bot, message):
    user_id = message.from_user.id
    text = message.text or ''

    # /start <code> always restarts the flow — handle_start_with_code
    # clears any pending gate row before checking.
    is_start = text.startswith('/start')
    is_start_with_code = is_start and len(text.split()) > 1

    if is_start_with_code:
        handle_start_with_code(bot, message)
        return

    # Message fallback for the force-join gate:
    #   Any text message from a non-admin user with a pending gate row
    #   is treated as a recheck. /start <code> is excluded above so
    #   re-entry works.
    if text and not is_admin(user_id):
        try:
            pending_gate = get_join_gate(user_id)
        except Exception:
            pending_gate = None
        if pending_gate:
            _recheck_join(bot, message.chat.id, user_id)
            return

    if message.text:
        if message.text.startswith('/start'):
            handle_start(bot, message)
        elif message.text.startswith('/help'):
            handle_help(bot, message)
        else:
            pass
    elif message.document:
        handle_document(bot, message)


def handle_callback(bot, call):
    # Force-join recheck — fires when the user taps "✅ I joined".
    if call.data == 'join_recheck':
        handle_join_recheck(bot, call)
        return

    if call.data.startswith('pdf_admin_'):
        handle_admin_pending(bot, call)


# ============================================
# /start  (no code)
# ============================================

def handle_start(bot, message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or ''
    text = (
        f"👋 Hello {first_name}!\n\n"
        "I am the PDF intake bot for the learning platform.\n"
        "Send me a PDF document and it will be forwarded to the admin for review.\n\n"
        "Commands:\n"
        "/start - Show this message\n"
        "/help - Show help"
    )
    markup = None
    if is_admin(user_id):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📚 Pending PDFs", callback_data="pdf_admin_pending"))
    bot.send_message(message.chat.id, text, reply_markup=markup)


# ============================================
# /start <code>  (delivers a stored PDF)
# ============================================

def handle_start_with_code(bot, message):
    """
    Handle /start <code>.

    Flow:
      1. Validate the PDF code. If invalid, fall through to handle_start.
      2. /start <code> is always a fresh start — clear any pending gate.
      3. Admin / super admin bypass the gate.
      4. If the gate is disabled or misconfigured, deliver directly.
      5. Otherwise, check memberships. Deliver, or prompt the next step.
    """
    user_id = message.from_user.id
    text = message.text
    parts = text.split(maxsplit=1)

    if len(parts) != 2:
        handle_start(bot, message)
        return

    code = parts[1].strip()
    bot_pdf = get_bot_pdf_by_code(code)

    # Validate the code BEFORE gating, so users don't join for nothing.
    if not bot_pdf:
        handle_start(bot, message)
        return

    # /start <code> restarts the flow — clear any pending gate row.
    try:
        delete_join_gate(user_id)
    except Exception as e:
        logger.warning(f"delete_join_gate failed: {e}")

    # Admins bypass the gate entirely.
    if is_admin(user_id):
        _deliver_pdf(bot, message.chat.id, user_id, code, bot_pdf)
        return

    # Gate disabled or misconfigured → deliver directly.
    cfg = _force_join_config()
    if not cfg:
        _deliver_pdf(bot, message.chat.id, user_id, code, bot_pdf)
        return

    status = _check_force_join(bot, user_id, cfg)

    if status == 'ok':
        _deliver_pdf(bot, message.chat.id, user_id, code, bot_pdf)
        return

    if status == 'channel':
        if not set_join_gate(user_id, 'channel', code):
            # DB write failed — fail open and deliver.
            logger.warning(
                f"set_join_gate failed for user {user_id}; delivering anyway"
            )
            _deliver_pdf(bot, message.chat.id, user_id, code, bot_pdf)
            return
        _prompt_join_channel(bot, message.chat.id, code)
        return

    if status == 'group':
        if not set_join_gate(user_id, 'group', code):
            logger.warning(
                f"set_join_gate failed for user {user_id}; delivering anyway"
            )
            _deliver_pdf(bot, message.chat.id, user_id, code, bot_pdf)
            return
        _prompt_join_group(bot, message.chat.id, code)
        return

    # Unknown status — fail open.
    _deliver_pdf(bot, message.chat.id, user_id, code, bot_pdf)


# ============================================
# /help
# ============================================

def handle_help(bot, message):
    bot.send_message(
        message.chat.id,
        "📖 Help:\n\n"
        "Send me a PDF document (any file with .pdf extension).\n"
        "I will check if it's already in the system.\n"
        "If it's new, it will be added to the pending queue for admin review.\n\n"
        "Admins: Use the Pending PDFs button to manage uploads."
    )


# ============================================
# Document intake
# ============================================

def handle_document(bot, message):
    user_id = message.from_user.id
    protect = _protect(user_id)

    document = message.document
    if not document:
        bot.reply_to(
            message,
            "❌ Please send a document file (PDF).",
            protect_content=protect
        )
        return

    if document.mime_type != 'application/pdf' and not document.file_name.endswith('.pdf'):
        bot.reply_to(
            message,
            "❌ Only PDF files are accepted.",
            protect_content=protect
        )
        return

    file_id = document.file_id
    file_unique_id = document.file_unique_id
    filename = document.file_name or 'unknown.pdf'

    if is_duplicate_in_bot(file_unique_id):
        bot.reply_to(
            message,
            "⚠️ This PDF is already in the system (either already published or pending review).",
            protect_content=protect
        )
        return

    pending_id = save_pending_pdf(file_id, file_unique_id, filename, user_id)
    if pending_id:
        base_url = Config.BASE_URL.rstrip('/')
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "📚 Browse More PDFs",
                url=f"{base_url}/pdfs"
            )
        )

        bot.reply_to(
            message,
            f"✅ PDF received and is pending admin review.\n\n"
            f"📄 *{filename}*\n"
            f"🆔 Pending ID: #{pending_id}\n\n"
            f"🔗 *Available on our platform with more study materials!*",
            parse_mode='Markdown',
            reply_markup=markup,
            protect_content=protect
        )
    else:
        bot.reply_to(
            message,
            "❌ Failed to save the PDF. Please try again later.",
            protect_content=protect
        )


# ============================================
# Join-gate recheck callback
# ============================================

def handle_join_recheck(bot, call):
    """User tapped the '✅ I joined' inline button."""
    user_id = call.from_user.id
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    try:
        chat_id = call.message.chat.id
    except Exception:
        chat_id = user_id
    _recheck_join(bot, chat_id, user_id)


# ============================================
# Admin: pending list callback
# ============================================

def handle_admin_pending(bot, call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "You are not authorized.", show_alert=True)
        return

    bot.answer_callback_query(call.id)

    if call.data == "pdf_admin_pending":
        count = count_pending_pdfs()
        pending_list = get_pending_pdf_list(limit=5)
        text = f"📚 Pending PDFs: {count}\n\n"
        if pending_list:
            for p in pending_list:
                text += f"• {p['filename']} (ID: {p['id']}) - uploaded {p['uploaded_at']}\n"
            text += "\nUse the web admin panel to process them.\n"
            base_url = Config.BASE_URL.rstrip('/')
            secret_path = getattr(Config, 'PDF_ADMIN_SECRET_PATH', None) or '/pdf-admin'
            text += f"Web panel: {base_url}{secret_path}"
        else:
            text += "No pending PDFs."

        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None
        )