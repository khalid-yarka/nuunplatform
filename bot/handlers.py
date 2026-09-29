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
#   on any error.
#
# ── DELETION OF THE JOIN PROMPT
#   When the user taps "✅ I joined" and the check passes, the bot
#   deletes the prompt message that carried the button before
#   proceeding to the next step (or delivering the file). If the
#   check fails, the prompt is kept so the user can tap again.
#
# ── SUPER ADMIN COMMANDS
#   Commands are dispatched by bot.admin_handlers.dispatch() at the
#   top of handle_message. See bot/admin_handlers.py for the full
#   list and the authorization guard.

import logging
import requests
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

# Module-level flag so the diagnostic runs at most once per process.
_DIAGNOSTIC_DONE = {'done': False}


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
# ONE-SHOT GATE DIAGNOSTIC
# ============================================

def _diagnose_gate_once(chat_id):
    """
    Called once on the first CHAT_ADMIN_REQUIRED failure. Logs the
    bot identity and the chat identity so the operator can see
    exactly what Telegram sees.
    """
    if _DIAGNOSTIC_DONE['done']:
        return
    _DIAGNOSTIC_DONE['done'] = True

    try:
        token = Config.TELEGRAM_BOT_TOKEN
    except Exception:
        logger.warning("force_join DIAG: no bot token in config")
        return

    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=10,
        ).json()
        if r.get('ok'):
            me = r.get('result') or {}
            logger.warning(
                "force_join DIAG: bot identity — id=%s username=%s "
                "first_name=%r. Confirm this is the bot you added as "
                "admin to the channel and group.",
                me.get('id'), me.get('username'), me.get('first_name'),
            )
        else:
            logger.warning(
                "force_join DIAG: getMe returned non-ok: %s",
                (r.get('description') or r)[:200],
            )
    except Exception as e:
        logger.warning(f"force_join DIAG: getMe failed: {e}")

    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getChat",
            params={"chat_id": chat_id},
            timeout=10,
        ).json()
        if r.get('ok'):
            ch = r.get('result') or {}
            logger.warning(
                "force_join DIAG: chat identity — id=%s type=%s "
                "title=%r username=%r. Confirm this is the channel "
                "or group you expect.",
                ch.get('id'), ch.get('type'), ch.get('title'),
                ch.get('username'),
            )
        else:
            logger.warning(
                "force_join DIAG: getChat(%r) returned non-ok: %s",
                chat_id, (r.get('description') or r)[:200],
            )
    except Exception as e:
        logger.warning(f"force_join DIAG: getChat failed: {e}")

    try:
        me_r = requests.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=10,
        ).json()
        bot_id = (me_r.get('result') or {}).get('id') if me_r.get('ok') else None
        if bot_id:
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getChatMember",
                params={"chat_id": chat_id, "user_id": bot_id},
                timeout=10,
            ).json()
            if r.get('ok'):
                m = r.get('result') or {}
                logger.warning(
                    "force_join DIAG: bot's own status in chat %r — "
                    "status=%s. If this is not 'administrator', the "
                    "bot is not admin here regardless of what the app "
                    "shows.",
                    chat_id, m.get('status'),
                )
            else:
                logger.warning(
                    "force_join DIAG: getChatMember(bot) in chat %r "
                    "returned non-ok: %s",
                    chat_id, (r.get('description') or r)[:200],
                )
    except Exception as e:
        logger.warning(f"force_join DIAG: getChatMember(bot) failed: {e}")


# ============================================
# FORCE-JOIN GATE
# ============================================

def _parse_chat_id(raw):
    """
    Normalize a channel/group id from config into an int (or None).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return s


def _force_join_config():
    """
    Return a dict of gate settings, or None if the gate is disabled
    or misconfigured. Never raises.
    """
    try:
        if not getattr(Config, 'TELEGRAM_FORCE_JOIN_ENABLED', False):
            logger.info("force_join: disabled via config")
            return None
        channel_id = _parse_chat_id(
            getattr(Config, 'TELEGRAM_FORCE_CHANNEL_ID', '') or ''
        )
        group_id = _parse_chat_id(
            getattr(Config, 'TELEGRAM_FORCE_GROUP_ID', '') or ''
        )
        if not channel_id and not group_id:
            logger.warning(
                "force_join: ENABLED but neither CHANNEL_ID nor GROUP_ID "
                "is configured — gate is a no-op"
            )
            return None
        cfg = {
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
        logger.info(
            "force_join: config loaded channel_id=%r group_id=%r "
            "channel_invite_set=%s group_invite_set=%s",
            cfg['channel_id'], cfg['group_id'],
            bool(cfg['channel_invite']), bool(cfg['group_invite']),
        )
        return cfg
    except Exception as e:
        logger.error(f"force_join: config read failed: {e}", exc_info=True)
        return None


def _extract_status(member):
    """
    Return the membership status string from a telebot get_chat_member
    result, handling object / dict / to_dict() shapes.
    """
    if member is None:
        return None
    if isinstance(member, dict):
        return member.get('status')
    status = getattr(member, 'status', None)
    if status is not None:
        return status
    try:
        as_dict = member.to_dict()
        if isinstance(as_dict, dict):
            return as_dict.get('status')
    except Exception:
        pass
    return None


def _is_member_of(bot, chat_id, user_id):
    """
    Three-state membership check.

    Returns:
        True  → user is a member
        False → user is definitively not in the chat
        None  → unknown; the caller must fail open
    """
    try:
        member = bot.get_chat_member(chat_id, user_id)
    except Exception as e:
        err_text = str(e)
        err_lower = err_text.lower()

        if 'chat_admin_required' in err_lower or \
                'chat admin required' in err_lower or \
                'not enough rights' in err_lower:
            _diagnose_gate_once(chat_id)
            logger.warning(
                "force_join: TELEGRAM REFUSED — CHAT_ADMIN_REQUIRED "
                "for chat=%r user=%s. From Telegram's perspective, "
                "the bot calling this method is not an admin of that "
                "chat. (error: %s)",
                chat_id, user_id, err_text,
            )
            return None

        if 'member list is inaccessible' in err_lower or \
                'member_list_is_inaccessible' in err_lower:
            logger.warning(
                "force_join: bot IS admin of chat %r BUT LACKS the "
                "member-read permission. Enable 'Add Users' (groups) "
                "or 'Add Subscribers' (channels) for the bot. "
                "(error: %s)",
                chat_id, err_text,
            )
            return None

        if 'chat not found' in err_lower:
            logger.warning(
                "force_join: chat %r NOT FOUND. Check the ID in "
                "TELEGRAM_FORCE_CHANNEL_ID / TELEGRAM_FORCE_GROUP_ID "
                "in .env. (error: %s)",
                chat_id, err_text,
            )
            return None

        logger.warning(
            "force_join: get_chat_member failed for chat=%r "
            "user=%s: %s",
            chat_id, user_id, err_text,
        )
        return None

    status = _extract_status(member)
    if status is None:
        logger.warning(
            "force_join: could not read status from get_chat_member "
            "result for chat=%r user=%s — result type=%s",
            chat_id, user_id, type(member).__name__,
        )
        return None

    logger.info(
        "force_join: chat=%r user=%s status=%r",
        chat_id, user_id, status,
    )

    if status in ('creator', 'administrator', 'member', 'restricted'):
        return True
    if status in ('left', 'kicked'):
        return False
    logger.warning(
        "force_join: unexpected status %r for chat=%r user=%s",
        status, chat_id, user_id,
    )
    return None


def _check_force_join(bot, user_id, cfg):
    """
    Two-step gate check.

    Returns:
        'ok'      → user may proceed
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
            logger.info(
                "force_join: user %s must join channel %r",
                user_id, channel_id,
            )
            return 'channel'

    if group_id:
        result = _is_member_of(bot, group_id, user_id)
        if result is False:
            logger.info(
                "force_join: user %s must join group %r",
                user_id, group_id,
            )
            return 'group'

    return 'ok'


def _delete_prompt_message(bot, prompt_message):
    """
    Delete the prompt message that carried the 'I joined' button.
    Never raises — too-old messages, already-deleted messages, or
    any other Telegram error is logged and ignored.
    """
    if not prompt_message:
        return
    try:
        chat_id, message_id = prompt_message
    except (TypeError, ValueError):
        return
    try:
        bot.delete_message(chat_id, message_id)
        logger.info(
            "force_join: deleted prompt message chat=%s id=%s",
            chat_id, message_id,
        )
    except Exception as e:
        logger.info(
            "force_join: could not delete prompt chat=%s id=%s: %s",
            chat_id, message_id, e,
        )


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
        logger.info(
            "force_join: sent channel prompt to chat=%s code=%r",
            chat_id, code,
        )
    except Exception as e:
        logger.error(f"_prompt_join_channel send failed: {e}", exc_info=True)


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
        logger.info(
            "force_join: sent group prompt to chat=%s code=%r",
            chat_id, code,
        )
    except Exception as e:
        logger.error(f"_prompt_join_group send failed: {e}", exc_info=True)


def _deliver_pdf(bot, chat_id, user_id, code, bot_pdf):
    """Send a stored PDF with its description and a Browse More button."""
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
        logger.info(
            "force_join: delivered code=%r to user=%s", code, user_id,
        )
    except Exception as e:
        logger.error(f"_deliver_pdf failed for code {code}: {e}", exc_info=True)
        try:
            bot.send_message(
                chat_id,
                "❌ Sorry, I couldn't retrieve the PDF. "
                "Please try again later.",
                protect_content=_protect(user_id),
            )
        except Exception:
            pass


def _recheck_join(bot, chat_id, user_id, prompt_message=None):
    """
    The recheck path used by both the inline button and the message
    fallback. `prompt_message` is an optional (chat_id, message_id)
    tuple — when supplied, the prompt is deleted as soon as the
    check passes.
    """
    try:
        row = get_join_gate(user_id)
    except Exception:
        row = None

    if not row:
        logger.info(
            "force_join: recheck for user=%s with no pending gate row — "
            "ignoring", user_id,
        )
        return

    logger.info(
        "force_join: recheck user=%s stage=%r code=%r attempts=%s",
        user_id, row.get('stage'), row.get('code'), row.get('attempts'),
    )

    cfg = _force_join_config()
    if not cfg:
        _delete_prompt_message(bot, prompt_message)
        code = row.get('code')
        delete_join_gate(user_id)
        if code:
            bot_pdf = get_bot_pdf_by_code(code)
            if bot_pdf:
                _deliver_pdf(bot, chat_id, user_id, code, bot_pdf)
        return

    attempts = bump_join_gate_attempts(user_id)
    if attempts is None:
        return
    max_attempts = cfg.get('max_attempts', 10)
    if attempts > max_attempts:
        _delete_prompt_message(bot, prompt_message)
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
            _delete_prompt_message(bot, prompt_message)
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

        _delete_prompt_message(bot, prompt_message)
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
            _delete_prompt_message(bot, prompt_message)
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

        _delete_prompt_message(bot, prompt_message)
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

    # ── Super admin commands ──
    # Dispatched first, before anything else, so admin commands fire
    # even when the sender has a pending gate row or is in the middle
    # of any other flow. Non-commands and unknown commands fall through
    # to the normal routing below.
    if text.startswith('/'):
        try:
            from bot import admin_handlers
            if admin_handlers.dispatch(bot, message):
                return
        except Exception as e:
            logger.warning(f"admin_handlers dispatch failed: {e}")

    is_start = text.startswith('/start')
    is_start_with_code = is_start and len(text.split()) > 1

    if is_start_with_code:
        handle_start_with_code(bot, message)
        return

    if text and not is_admin(user_id):
        try:
            pending_gate = get_join_gate(user_id)
        except Exception:
            pending_gate = None
        if pending_gate:
            # No prompt to delete on the message-fallback path.
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
    """Handle /start <code>."""
    user_id = message.from_user.id
    text = message.text
    parts = text.split(maxsplit=1)

    if len(parts) != 2:
        handle_start(bot, message)
        return

    code = parts[1].strip()
    bot_pdf = get_bot_pdf_by_code(code)

    if not bot_pdf:
        handle_start(bot, message)
        return

    try:
        delete_join_gate(user_id)
    except Exception as e:
        logger.warning(f"delete_join_gate failed: {e}")

    if is_admin(user_id):
        logger.info(
            "force_join: user=%s is admin — bypassing gate", user_id,
        )
        _deliver_pdf(bot, message.chat.id, user_id, code, bot_pdf)
        return

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
    """
    User tapped the '✅ I joined' inline button.
    Passes the prompt message's (chat_id, message_id) so the recheck
    can delete it once the check passes.
    """
    user_id = call.from_user.id
    logger.info(
        "force_join: 'I joined' tapped by user=%s data=%r",
        user_id, call.data,
    )
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    prompt_message = None
    try:
        prompt_message = (
            call.message.chat.id,
            call.message.message_id,
        )
        chat_id = call.message.chat.id
    except Exception:
        chat_id = user_id

    _recheck_join(bot, chat_id, user_id, prompt_message=prompt_message)


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