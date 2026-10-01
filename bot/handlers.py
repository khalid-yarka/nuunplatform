# bot/handlers.py
# Message and callback handlers for telebot.

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
    upsert_bot_contact, mark_bot_contact_fetched_pdf,
)
from config import Config

logger = logging.getLogger(__name__)

_DIAGNOSTIC_DONE = {'done': False}


# ============================================
# PROTECT CONTENT POLICY
# ============================================

def _protect(user_id: int) -> bool:
    return not is_super_admin(user_id)


# ============================================
# ONE-SHOT GATE DIAGNOSTIC
# ============================================

def _diagnose_gate_once(chat_id):
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
                "first_name=%r.",
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
                "title=%r username=%r.",
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
                    "status=%s.",
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
        return cfg
    except Exception as e:
        logger.error(f"force_join: config read failed: {e}", exc_info=True)
        return None


def _extract_status(member):
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
                "force_join: CHAT_ADMIN_REQUIRED chat=%r user=%s.",
                chat_id, user_id,
            )
            return None

        if 'member list is inaccessible' in err_lower or \
                'member_list_is_inaccessible' in err_lower:
            logger.warning(
                "force_join: bot lacks member-read permission in %r.",
                chat_id,
            )
            return None

        if 'chat not found' in err_lower:
            logger.warning(
                "force_join: chat %r NOT FOUND.", chat_id,
            )
            return None

        logger.warning(
            "force_join: get_chat_member failed chat=%r user=%s: %s",
            chat_id, user_id, err_text,
        )
        return None

    status = _extract_status(member)
    if status is None:
        return None

    if status in ('creator', 'administrator', 'member', 'restricted'):
        return True
    if status in ('left', 'kicked'):
        return False
    return None


def _check_force_join(bot, user_id, cfg):
    if not cfg:
        return 'ok'
    channel_id = cfg.get('channel_id')
    group_id = cfg.get('group_id')

    if channel_id:
        result = _is_member_of(bot, channel_id, user_id)
        if result is False:
            return 'channel'
    if group_id:
        result = _is_member_of(bot, group_id, user_id)
        if result is False:
            return 'group'
    return 'ok'


def _delete_prompt_message(bot, prompt_message):
    if not prompt_message:
        return
    try:
        chat_id, message_id = prompt_message
    except (TypeError, ValueError):
        return
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def _prompt_join_channel(bot, chat_id, code):
    cfg = _force_join_config() or {}
    invite = cfg.get('channel_invite') or ''
    text = (
        "📢 *Hal tallaabo oo kooban ka hor PDF-kaaga:*\n\n"
        "Fadlan marka hore ku biir *channel-keena*.\n"
        "Kadibna taabo *✅ Waan ku biiray* hoosta."
    )
    markup = types.InlineKeyboardMarkup()
    row = []
    if invite:
        row.append(types.InlineKeyboardButton("📢 Ku biir channel", url=invite))
    row.append(types.InlineKeyboardButton(
        "✅ Waan ku biiray", callback_data='join_recheck',
    ))
    markup.row(*row)
    try:
        bot.send_message(
            chat_id, text, parse_mode='Markdown', reply_markup=markup,
        )
    except Exception as e:
        logger.error(f"_prompt_join_channel send failed: {e}", exc_info=True)


def _prompt_join_group(bot, chat_id, code):
    cfg = _force_join_config() or {}
    invite = cfg.get('group_invite') or ''
    text = (
        "👥 *Ku dhow baad tahay:*\n\n"
        "Fadlan marka hore ku biir *group-keena*.\n"
        "Kadibna taabo *✅ Waan ku biiray* hoosta."
    )
    markup = types.InlineKeyboardMarkup()
    row = []
    if invite:
        row.append(types.InlineKeyboardButton("👥 Ku biir group", url=invite))
    row.append(types.InlineKeyboardButton(
        "✅ Waan ku biiray", callback_data='join_recheck',
    ))
    markup.row(*row)
    try:
        bot.send_message(
            chat_id, text, parse_mode='Markdown', reply_markup=markup,
        )
    except Exception as e:
        logger.error(f"_prompt_join_group send failed: {e}", exc_info=True)


def _deliver_pdf(bot, chat_id, user_id, code, bot_pdf):
    """
    Send a stored PDF. On success, marks the contact as having
    fetched a PDF so it becomes eligible for the new-quiz broadcast.
    """
    try:
        file_id = bot_pdf['file_id']

        caption = f"📄 *{bot_pdf['title']}*\n\n"
        if bot_pdf.get('description'):
            caption += f"{bot_pdf['description']}\n\n"
        caption += f"📌 *Code:* `{code}`\n"
        caption += f"🔗 *Waxaa laga heli karaa madashayada oo leh waxbarasho dheeraad ah!*"

        base_url = Config.BASE_URL.rstrip('/')
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "📚 Eeg PDF-yo Kale",
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
        logger.info("force_join: delivered code=%r to user=%s", code, user_id)

        # Mark contact as eligible for broadcast
        try:
            mark_bot_contact_fetched_pdf(chat_id)
        except Exception as e:
            logger.debug(f"mark_bot_contact_fetched_pdf non-fatal: {e}")

    except Exception as e:
        logger.error(f"_deliver_pdf failed for code {code}: {e}", exc_info=True)
        try:
            bot.send_message(
                chat_id,
                "❌ Waan ka xumahay, ma heli karo PDF-ka. "
                "Fadlan isku day mar dambe.",
                protect_content=_protect(user_id),
            )
        except Exception:
            pass


def _recheck_join(bot, chat_id, user_id, prompt_message=None):
    try:
        row = get_join_gate(user_id)
    except Exception:
        row = None

    if not row:
        return

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
                "❗ Isku dayo badan. Fadlan mar kale dir `/start <code>` "
                "si aad dib ugu bilowdo.",
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
                    "❌ Weli ma aadan ku biirin channel-ka. "
                    "Fadlan ku biir, kadibna taabo *✅ Waan ku biiray*.",
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
                    "❌ Weli ma aadan ku biirin group-ka. "
                    "Fadlan ku biir, kadibna taabo *✅ Waan ku biiray*.",
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
            _track_contact_from_user(update.message.from_user)
            handle_message(bot, update.message)
        elif update.callback_query:
            _track_contact_from_user(update.callback_query.from_user)
            handle_callback(bot, update.callback_query)
        else:
            logger.debug("Unhandled update type.")
    except Exception as e:
        logger.error(f"Error processing update: {e}", exc_info=True)


def _track_contact_from_user(user):
    """
    Upsert the incoming user into bot_contacts on every update.
    Never raises. Called from the update router.
    """
    if not user:
        return
    try:
        upsert_bot_contact(
            chat_id=user.id,
            username=getattr(user, 'username', '') or '',
            first_name=getattr(user, 'first_name', '') or '',
            last_name=getattr(user, 'last_name', '') or '',
        )
    except Exception as e:
        logger.debug(f"_track_contact_from_user non-fatal: {e}")


def handle_message(bot, message):
    user_id = message.from_user.id
    text = message.text or ''

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
# /start (no code)
# ============================================

def handle_start(bot, message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or ''
    text = (
        f"👋 Salaan {first_name}!\n\n"
        "Waxaan ahay bot-ka soo dhoweynta PDF-yada ee madashayada.\n"
        "Ii soo dir PDF, waxaan u gudbinayaa maamulka si loo eego.\n\n"
        "Amarrada:\n"
        "/start - Muuji fariintan\n"
        "/help - Muuji caawinaad"
    )
    markup = None
    if is_admin(user_id):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            "📚 PDF-yada Sugaya",
            callback_data="pdf_admin_pending",
        ))
    bot.send_message(message.chat.id, text, reply_markup=markup)


# ============================================
# /start <code>  (delivers a stored PDF)
# ============================================

def handle_start_with_code(bot, message):
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
    except Exception:
        pass

    if is_admin(user_id):
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
            _deliver_pdf(bot, message.chat.id, user_id, code, bot_pdf)
            return
        _prompt_join_channel(bot, message.chat.id, code)
        return

    if status == 'group':
        if not set_join_gate(user_id, 'group', code):
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
        "📖 Caawinaad:\n\n"
        "Ii soo dir PDF (fayl kasta oo leh .pdf).\n"
        "Waxaan hubinayaa haddii uu horey u jiray nidaamka.\n"
        "Haddii uu cusub yahay, waxaa lagu darayaa liiska sugayaasha.\n\n"
        "Maamulayaasha: Isticmaal badhanka PDF-yada Sugaya si aad u maamusho."
    )


# ============================================
# Document intake
# ============================================

def handle_document(bot, message):
    user_id = message.from_user.id
    protect = _protect(user_id)

    document = message.document
    if not document:
        bot.reply_to(message, "❌ Fadlan soo dir fayl PDF ah.", protect_content=protect)
        return

    if document.mime_type != 'application/pdf' and not document.file_name.endswith('.pdf'):
        bot.reply_to(message, "❌ Kaliya faylalka PDF ayaa la aqbalaa.", protect_content=protect)
        return

    file_id = document.file_id
    file_unique_id = document.file_unique_id
    filename = document.file_name or 'unknown.pdf'

    if is_duplicate_in_bot(file_unique_id):
        bot.reply_to(
            message,
            "⚠️ PDF-kan horey ayaa loo helay (ama la daabacay ama wali sugaya).",
            protect_content=protect
        )
        return

    pending_id = save_pending_pdf(file_id, file_unique_id, filename, user_id)
    if pending_id:
        base_url = Config.BASE_URL.rstrip('/')
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📚 Eeg PDF-yo Kale", url=f"{base_url}/pdfs"))
        bot.reply_to(
            message,
            f"✅ PDF-ka waa la helay oo wuxuu sugayaa maamulka.\n\n"
            f"📄 *{filename}*\n"
            f"🆔 ID: #{pending_id}\n\n"
            f"🔗 *Waxaa laga heli karaa madashayada oo leh waxbarasho dheeraad ah!*",
            parse_mode='Markdown',
            reply_markup=markup,
            protect_content=protect
        )
    else:
        bot.reply_to(
            message,
            "❌ Ma keydin karo PDF-ka. Fadlan isku day mar dambe.",
            protect_content=protect
        )


# ============================================
# Join-gate recheck callback
# ============================================

def handle_join_recheck(bot, call):
    user_id = call.from_user.id
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    prompt_message = None
    try:
        prompt_message = (call.message.chat.id, call.message.message_id)
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
        bot.answer_callback_query(call.id, "Ma lihid fasax.", show_alert=True)
        return

    bot.answer_callback_query(call.id)

    if call.data == "pdf_admin_pending":
        count = count_pending_pdfs()
        pending_list = get_pending_pdf_list(limit=5)
        text = f"📚 PDF-yada Sugaya: {count}\n\n"
        if pending_list:
            for p in pending_list:
                text += f"• {p['filename']} (ID: {p['id']}) - {p['uploaded_at']}\n"
            text += "\nIsticmaal guddiga maamulka ee web-ka.\n"
            base_url = Config.BASE_URL.rstrip('/')
            secret_path = getattr(Config, 'PDF_ADMIN_SECRET_PATH', None) or '/pdf-admin'
            text += f"Web: {base_url}{secret_path}"
        else:
            text += "PDF-yo sugaya ma jiraan."

        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None
        )