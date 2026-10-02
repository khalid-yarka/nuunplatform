# bot/handlers.py
# Message and callback handlers for telebot.
#
# All texts live in bot/messages.json.
#
# Payload shapes accepted on /start <payload>:
#   subscribe_<public_id>          — lobby banner (link + subscribe)
#   pdf<code><public_id>           — PDF deeplink (link + subscribe + deliver)
#   <legacy_code>                  — legacy PDF code, deliver only
#
# Group chats are dropped before any handler runs.
# Non-text messages fall through to the platform-help reply.

import json
import logging
import os
import threading
import time

import requests
import telebot
from telebot import types

from bot.utils import (
    is_duplicate_in_bot, save_pending_pdf,
    is_admin, is_super_admin, get_super_admin_ids,
)
from bot.db import (
    count_pending_pdfs, get_pending_pdf_list, get_bot_pdf_by_code,
    get_join_gate, set_join_gate, delete_join_gate,
    bump_join_gate_attempts,
    upsert_bot_contact, mark_bot_contact_fetched_pdf,
    link_bot_contact,
)
from config import Config

logger = logging.getLogger(__name__)

_DIAGNOSTIC_DONE = {'done': False}
_warned_forward_failures = set()


# ============================================
# MESSAGES
# ============================================

_MESSAGES = None


def _load_messages():
    global _MESSAGES
    if _MESSAGES is not None:
        return _MESSAGES
    try:
        path = os.path.join(os.path.dirname(__file__), 'messages.json')
        with open(path, 'r', encoding='utf-8') as f:
            _MESSAGES = json.load(f)
        logger.info(f"Loaded {len(_MESSAGES)} message groups from messages.json")
    except Exception as e:
        logger.error(f"Failed to load messages.json: {e}", exc_info=True)
        _MESSAGES = {}
    return _MESSAGES


def _m(key, **kwargs):
    node = _load_messages()
    for part in key.split('.'):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return f"[{key}]"
    if not isinstance(node, str):
        return str(node)
    if kwargs:
        try:
            return node.format(**kwargs)
        except Exception:
            return node
    return node

# ============================================
# MAINTENANCE MODE (bot-side read)
# ============================================
# Reads the same instance/maintenance.json that app.py writes. A
# short local cache (5 seconds, matching the web layer) avoids a
# filesystem read on every Telegram update.
#
# Super admins bypass this gate. Every other user gets the Somali
# maintenance reply and their update is not otherwise processed.
# Document intake still runs (see handle_document) so the pending
# queue stays accurate during the window.
# ============================================

_MAINT_STATE_PATH = None
_MAINT_CACHE = {'loaded_at': 0.0, 'state': None}
_MAINT_CACHE_TTL = 5


def _get_maintenance_state():
    global _MAINT_STATE_PATH
    if _MAINT_STATE_PATH is None:
        try:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            _MAINT_STATE_PATH = os.path.join(base, 'instance', 'maintenance.json')
        except Exception:
            _MAINT_STATE_PATH = ''

    now = time.time()
    if (_MAINT_CACHE['state'] is not None
            and now - _MAINT_CACHE['loaded_at'] < _MAINT_CACHE_TTL):
        return _MAINT_CACHE['state']

    state = {'enabled': False, 'since': None}
    try:
        if _MAINT_STATE_PATH and os.path.exists(_MAINT_STATE_PATH):
            with open(_MAINT_STATE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            state = {
                'enabled': bool(data.get('enabled', False)),
                'since': data.get('since') or None,
            }
    except Exception:
        pass

    _MAINT_CACHE['state'] = state
    _MAINT_CACHE['loaded_at'] = now
    return state

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
            logger.warning("force_join: chat %r NOT FOUND.", chat_id)
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
    text = _m('gate.channel_prompt')
    markup = types.InlineKeyboardMarkup()
    row = []
    if invite:
        row.append(types.InlineKeyboardButton(
            _m('gate.join_channel_button'), url=invite,
        ))
    row.append(types.InlineKeyboardButton(
        _m('gate.confirm_button'), callback_data='join_recheck',
    ))
    markup.row(*row)
    try:
        bot.send_message(chat_id, text, reply_markup=markup)
    except Exception as e:
        logger.error(f"_prompt_join_channel send failed: {e}", exc_info=True)


def _prompt_join_group(bot, chat_id, code):
    cfg = _force_join_config() or {}
    invite = cfg.get('group_invite') or ''
    text = _m('gate.group_prompt')
    markup = types.InlineKeyboardMarkup()
    row = []
    if invite:
        row.append(types.InlineKeyboardButton(
            _m('gate.join_group_button'), url=invite,
        ))
    row.append(types.InlineKeyboardButton(
        _m('gate.confirm_button'), callback_data='join_recheck',
    ))
    markup.row(*row)
    try:
        bot.send_message(chat_id, text, reply_markup=markup)
    except Exception as e:
        logger.error(f"_prompt_join_group send failed: {e}", exc_info=True)


# ============================================
# PLATFORM HELP + KEYBOARD
# ============================================

def _super_admin_whatsapp_url():
    try:
        raw = (getattr(Config, 'SUPER_ADMIN_PHONE', '') or '').strip()
    except Exception:
        return None
    if not raw:
        return None
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    return f"https://wa.me/{digits}"


def _platform_button(label_key, path_key):
    base = (Config.BASE_URL or '').rstrip('/')
    if not base:
        return None
    label = _m(f'common.buttons.{label_key}')
    path = _m(f'common.paths.{path_key}')
    if not label or not path or label.startswith('[') or path.startswith('['):
        return None
    return types.InlineKeyboardButton(label, url=f"{base}{path}")


def _build_platform_keyboard(for_admin=False):
    """
    Four rows: home + live_quiz | pdfs + practice |
    history + achievements | contact (full width).
    Admin callback appended as an extra full-width row.
    """
    markup = types.InlineKeyboardMarkup(row_width=2)

    def _row(k1, k2):
        b1 = _platform_button(k1, k1)
        b2 = _platform_button(k2, k2)
        row = [b for b in (b1, b2) if b]
        if row:
            markup.row(*row)

    _row('home', 'live_quiz')
    _row('pdfs', 'practice')
    _row('history', 'achievements')

    wa = _super_admin_whatsapp_url()
    if wa:
        markup.row(types.InlineKeyboardButton(
            _m('common.buttons.contact'), url=wa,
        ))

    if for_admin:
        markup.row(types.InlineKeyboardButton(
            _m('common.buttons.pending_pdfs'),
            callback_data='pdf_admin_pending',
        ))

    return markup


def _send_platform_help(bot, message, *, first_time=True):
    user_id = message.from_user.id
    chat_id = message.chat.id
    first_name = (message.from_user.first_name or '').strip()

    if first_time:
        greeting = (
            _m('welcome.greeting', first_name=first_name)
            if first_name else _m('welcome.greeting_no_name')
        )
        text = (
            f"{greeting}\n\n"
            f"{_m('welcome.body')}\n\n"
            f"{_m('welcome.features_title')}\n"
            f"{_m('welcome.features')}\n\n"
            f"{_m('welcome.cta')}"
        )
    else:
        text = f"{_m('unknown.body')}\n\n{_m('unknown.cta')}"

    try:
        markup = _build_platform_keyboard(for_admin=is_admin(user_id))
        bot.send_message(chat_id, text, reply_markup=markup)
    except Exception as e:
        logger.error(f"_send_platform_help failed: {e}", exc_info=True)


def _send_subscribe_welcome(bot, message, public_id=None):
    user_id = message.from_user.id
    chat_id = message.chat.id
    first_name = (message.from_user.first_name or '').strip()

    if public_id:
        try:
            link_bot_contact(chat_id, public_id,
                             mark_subscribed=True, mark_fetched=False)
        except Exception as e:
            logger.debug(f"link_bot_contact (subscribe) non-fatal: {e}")

    greeting = (
        _m('welcome.greeting', first_name=first_name)
        if first_name else _m('welcome.greeting_no_name')
    )
    text = (
        f"{greeting}\n\n"
        f"{_m('subscribe.body')}\n\n"
        f"{_m('subscribe.info')}\n\n"
        f"{_m('subscribe.off_hint')}\n\n"
        f"{_m('subscribe.cta')}"
    )
    try:
        markup = _build_platform_keyboard(for_admin=is_admin(user_id))
        bot.send_message(chat_id, text, reply_markup=markup)
    except Exception as e:
        logger.error(f"_send_subscribe_welcome failed: {e}", exc_info=True)


# ============================================
# FORWARD TO SUPER ADMINS
# ============================================

def _forward_to_super_admins(bot, message):
    threading.Thread(
        target=_do_forward_to_super_admins,
        args=(bot, message),
        daemon=True,
        name='forward-super-admins',
    ).start()


def _do_forward_to_super_admins(bot, message):
    try:
        admin_ids = get_super_admin_ids()
    except Exception as e:
        logger.debug(f"forward: could not read super admin ids: {e}")
        return
    if not admin_ids:
        return

    from_chat_id = message.chat.id
    message_id = message.message_id

    for aid in admin_ids:
        try:
            bot.forward_message(
                chat_id=aid,
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
        except Exception as e:
            if aid not in _warned_forward_failures:
                _warned_forward_failures.add(aid)
                logger.warning(
                    f"forward to super admin {aid} failed — "
                    f"the admin must start the bot at least once: {e}"
                )


# ============================================
# PDF DELIVERY
# ============================================

def _deliver_pdf(bot, chat_id, user_id, code, bot_pdf, public_id=None):
    """
    Deliver a stored PDF. If public_id is present, the chat has
    already been linked by the caller (or the caller passes it in
    so we can mark fetched_pdf on the linked row). Otherwise we
    fall back to mark_bot_contact_fetched_pdf for legacy callers.
    """
    try:
        file_id = bot_pdf['file_id']

        caption = _m('pdf.delivery_caption_title', title=bot_pdf['title'])
        if bot_pdf.get('description'):
            caption += f"\n\n{bot_pdf['description']}"
        caption += f"\n\n{_m('pdf.delivery_caption_code', code=code)}"
        caption += f"\n{_m('pdf.delivery_caption_footer')}"

        base_url = Config.BASE_URL.rstrip('/')
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            _m('pdf.delivery_button'),
            url=f"{base_url}/pdfs",
        ))

        bot.send_document(
            chat_id,
            file_id,
            caption=caption,
            parse_mode='Markdown',
            reply_markup=markup,
            protect_content=_protect(user_id),
        )
        logger.info("force_join: delivered code=%r to user=%s", code, user_id)

        try:
            if public_id:
                # Already linked upstream; ensure fetched_pdf set.
                link_bot_contact(chat_id, public_id,
                                 mark_subscribed=True, mark_fetched=True)
            else:
                mark_bot_contact_fetched_pdf(chat_id)
        except Exception as e:
            logger.debug(f"post-delivery mark non-fatal: {e}")

    except Exception as e:
        logger.error(f"_deliver_pdf failed for code {code}: {e}", exc_info=True)
        try:
            bot.send_message(
                chat_id,
                _m('pdf.delivery_error'),
                protect_content=_protect(user_id),
            )
        except Exception:
            pass


# ============================================
# GATE RECHECK
# ============================================

def _recheck_join(bot, chat_id, user_id, prompt_message=None, public_id=None):
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
                _deliver_pdf(bot, chat_id, user_id, code, bot_pdf, public_id=public_id)
        return

    attempts = bump_join_gate_attempts(user_id)
    if attempts is None:
        return
    max_attempts = cfg.get('max_attempts', 10)
    if attempts > max_attempts:
        _delete_prompt_message(bot, prompt_message)
        delete_join_gate(user_id)
        try:
            bot.send_message(chat_id, _m('gate.too_many_attempts'))
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
                        _deliver_pdf(bot, chat_id, user_id, code, bot_pdf, public_id=public_id)
            return
        result = _is_member_of(bot, cfg['channel_id'], user_id)
        if result is False:
            try:
                bot.send_message(chat_id, _m('gate.not_yet_channel'))
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
                    _deliver_pdf(bot, chat_id, user_id, code, bot_pdf, public_id=public_id)
        return

    if stage == 'group':
        if not cfg.get('group_id'):
            _delete_prompt_message(bot, prompt_message)
            delete_join_gate(user_id)
            if code:
                bot_pdf = get_bot_pdf_by_code(code)
                if bot_pdf:
                    _deliver_pdf(bot, chat_id, user_id, code, bot_pdf, public_id=public_id)
            return
        result = _is_member_of(bot, cfg['group_id'], user_id)
        if result is False:
            try:
                bot.send_message(chat_id, _m('gate.not_yet_group'))
            except Exception:
                pass
            return
        _delete_prompt_message(bot, prompt_message)
        delete_join_gate(user_id)
        if code:
            bot_pdf = get_bot_pdf_by_code(code)
            if bot_pdf:
                _deliver_pdf(bot, chat_id, user_id, code, bot_pdf, public_id=public_id)
        return


# ============================================
# PAYLOAD PARSING
# ============================================

def _parse_pdf_payload(payload):
    """
    Parse 'pdf' + <code> + <public_id>.
    public_id is always the LAST 4 characters. code is everything
    between the leading 'pdf' and the last 4 chars.
    Returns (code, public_id) or (None, None).
    """
    if not payload or not payload.startswith('pdf'):
        return None, None
    rest = payload[3:]
    if len(rest) < 5:
        return None, None
    public_id = rest[-4:]
    code = rest[:-4]
    if not code or not public_id:
        return None, None
    return code, public_id


def _parse_subscribe_payload(payload):
    """
    Parse 'subscribe' or 'subscribe_<public_id>'.
    Returns '' (bare), a public_id string, or None (not a subscribe).
    """
    if payload == 'subscribe':
        return ''
    if payload.startswith('subscribe_'):
        return payload[len('subscribe_'):].strip()
    return None


# ============================================
# UPDATE ROUTER
# ============================================

def _intake_document_silently(bot, message):
    """
    Save a document into pending_pdfs without replying to the user.
    Called only from the maintenance gate. PDFs only; non-PDFs are
    forwarded to super admins as usual. Never raises.
    """
    try:
        document = message.document
        if not document:
            return
        user_id = message.from_user.id
        file_name = document.file_name or ''
        is_pdf = (
            document.mime_type == 'application/pdf'
            or file_name.lower().endswith('.pdf')
        )
        if not is_pdf:
            if not is_admin(user_id):
                _forward_to_super_admins(bot, message)
            return
        try:
            if is_duplicate_in_bot(document.file_unique_id):
                return
        except Exception:
            pass
        try:
            save_pending_pdf(
                document.file_id,
                document.file_unique_id,
                file_name or 'unknown.pdf',
                user_id,
            )
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"_intake_document_silently non-fatal: {e}")


def process_telegram_update(bot: telebot.TeleBot, update_data: dict):
    try:
        update = types.Update.de_json(update_data)

        chat = None
        if update.message:
            chat = update.message.chat
        elif update.callback_query and update.callback_query.message:
            chat = update.callback_query.message.chat

        # ── 1. Group lock ──
        if chat is not None and getattr(chat, 'type', 'private') != 'private':
            logger.debug(
                "dropping update from chat type=%s chat_id=%s",
                getattr(chat, 'type', '?'), getattr(chat, 'id', '?'),
            )
            return

        # ── 2. Contact tracking ──
        # Runs before the maintenance gate, so the audience keeps
        # growing even while maintenance is on.
        sender_id = None
        if update.message and update.message.from_user:
            sender_id = update.message.from_user.id
            _track_contact_from_user(update.message.from_user)
        elif update.callback_query and update.callback_query.from_user:
            sender_id = update.callback_query.from_user.id
            _track_contact_from_user(update.callback_query.from_user)

        # ── 3. Super admin bypass ──
        is_sa = False
        if sender_id is not None:
            try:
                is_sa = bool(is_super_admin(sender_id))
            except Exception:
                is_sa = False

        # ── 4. Maintenance gate ──
        # Non-super-admin users get a single reply and their update
        # is not otherwise processed. Documents are still saved by
        # the intake path (see handle_document) so the pending queue
        # stays accurate — but the sender gets the maintenance reply,
        # not the intake confirmation.
        if not is_sa:
            state = _get_maintenance_state()
            if state.get('enabled'):
                chat_id = getattr(chat, 'id', None) if chat else None
                if chat_id:
                    # If this is a document, still save it silently
                    # before replying. Losing a mid-window upload
                    # would be worse than the small write.
                    try:
                        if update.message and update.message.document:
                            _intake_document_silently(bot, update.message)
                    except Exception as e:
                        logger.debug(f"silent intake during maintenance: {e}")

                    try:
                        bot.send_message(chat_id, _m('maintenance.bot_message'))
                    except Exception as e:
                        logger.debug(f"maintenance reply send failed: {e}")
                return

        # ── 5. Normal dispatch ──
        if update.message:
            handle_message(bot, update.message)
        elif update.callback_query:
            handle_callback(bot, update.callback_query)
        else:
            logger.debug("Unhandled update type.")
    except Exception as e:
        logger.error(f"Error processing update: {e}", exc_info=True)

def _track_contact_from_user(user):
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
    text = (message.text or '').strip()

    # 1. Admin commands
    if text.startswith('/'):
        try:
            from bot import admin_handlers
            if admin_handlers.dispatch(bot, message):
                return
        except Exception as e:
            logger.warning(f"admin_handlers dispatch failed: {e}")

    # 2. /start <payload>
    if text.startswith('/start') and len(text.split()) > 1:
        handle_start_with_code(bot, message)
        return

    # 3. Gate guard — any message (text or not) from a gated user
    if not is_admin(user_id):
        try:
            pending_gate = get_join_gate(user_id)
        except Exception:
            pending_gate = None
        if pending_gate:
            _recheck_join(bot, message.chat.id, user_id)
            return

    # 4. Documents
    if message.document:
        handle_document(bot, message)
        return

    # 5. /start (no payload)
    if text.startswith('/start'):
        handle_start(bot, message)
        return

    # 6. /help
    if text.startswith('/help'):
        handle_help(bot, message)
        return

    # 7. Everything else
    _send_platform_help(bot, message, first_time=False)


def handle_callback(bot, call):
    if call.data == 'join_recheck':
        handle_join_recheck(bot, call)
        return
    if call.data.startswith('pdf_admin_'):
        handle_admin_pending(bot, call)


# ============================================
# /start
# ============================================

def handle_start(bot, message):
    _send_platform_help(bot, message, first_time=True)


def handle_start_with_code(bot, message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    parts = message.text.split(maxsplit=1)
    payload = parts[1].strip()

    # ── Subscribe deeplink ──
    sub_public_id = _parse_subscribe_payload(payload)
    if sub_public_id is not None:
        _send_subscribe_welcome(bot, message, public_id=sub_public_id or None)
        return

    # ── PDF deeplink (new format) ──
    code, pdf_public_id = _parse_pdf_payload(payload)

    # ── Legacy PDF code ──
    legacy = False
    if not code:
        code = payload
        legacy = True

    bot_pdf = get_bot_pdf_by_code(code)
    if not bot_pdf:
        # Unknown payload — fall back to the generic /start reply.
        handle_start(bot, message)
        return

    # ── Link the chat to the platform user (new format only) ──
    if not legacy and pdf_public_id:
        try:
            link_bot_contact(chat_id, pdf_public_id,
                             mark_subscribed=True, mark_fetched=False)
        except Exception as e:
            logger.debug(f"link_bot_contact (pdf deeplink) non-fatal: {e}")

    try:
        delete_join_gate(user_id)
    except Exception:
        pass

    if is_admin(user_id):
        _deliver_pdf(bot, chat_id, user_id, code, bot_pdf,
                     public_id=pdf_public_id if not legacy else None)
        return

    cfg = _force_join_config()
    if not cfg:
        _deliver_pdf(bot, chat_id, user_id, code, bot_pdf,
                     public_id=pdf_public_id if not legacy else None)
        return

    status = _check_force_join(bot, user_id, cfg)

    if status == 'ok':
        _deliver_pdf(bot, chat_id, user_id, code, bot_pdf,
                     public_id=pdf_public_id if not legacy else None)
        return

    if status == 'channel':
        if not set_join_gate(user_id, 'channel', code):
            _deliver_pdf(bot, chat_id, user_id, code, bot_pdf,
                         public_id=pdf_public_id if not legacy else None)
            return
        _prompt_join_channel(bot, chat_id, code)
        return

    if status == 'group':
        if not set_join_gate(user_id, 'group', code):
            _deliver_pdf(bot, chat_id, user_id, code, bot_pdf,
                         public_id=pdf_public_id if not legacy else None)
            return
        _prompt_join_group(bot, chat_id, code)
        return

    _deliver_pdf(bot, chat_id, user_id, code, bot_pdf,
                 public_id=pdf_public_id if not legacy else None)


# ============================================
# /help
# ============================================

def handle_help(bot, message):
    text = (
        f"{_m('help.title')}\n\n"
        f"{_m('help.intro')}\n"
        f"{_m('help.list')}\n\n"
        f"{_m('help.cta')}\n\n"
        f"{_m('help.contact_hint')}"
    )
    try:
        markup = _build_platform_keyboard(
            for_admin=is_admin(message.from_user.id)
        )
        bot.send_message(message.chat.id, text, reply_markup=markup)
    except Exception as e:
        logger.error(f"handle_help failed: {e}", exc_info=True)


# ============================================
# Documents
# ============================================

def handle_document(bot, message):
    """
    Non-admins: intake is saved silently, the document is forwarded
    to super admins in the background, and the sender gets the
    platform-help reply. Admins get the intake confirmation.
    """
    user_id = message.from_user.id
    usr_is_admin = is_admin(user_id)
    protect = _protect(user_id)

    document = message.document
    if not document:
        _send_platform_help(bot, message, first_time=False)
        return

    file_name = document.file_name or ''
    is_pdf = (
        document.mime_type == 'application/pdf'
        or file_name.lower().endswith('.pdf')
    )

    if not is_pdf:
        if not usr_is_admin:
            _forward_to_super_admins(bot, message)
        _send_platform_help(bot, message, first_time=False)
        return

    file_id = document.file_id
    file_unique_id = document.file_unique_id
    filename = file_name or 'unknown.pdf'

    duplicate = False
    try:
        duplicate = is_duplicate_in_bot(file_unique_id)
    except Exception:
        duplicate = False

    pending_id = 0
    if not duplicate:
        try:
            pending_id = save_pending_pdf(
                file_id, file_unique_id, filename, user_id,
            )
        except Exception:
            pending_id = 0

    if usr_is_admin:
        try:
            if duplicate:
                bot.reply_to(message, _m('pdf.duplicate'),
                             protect_content=protect)
            elif pending_id:
                bot.reply_to(
                    message,
                    _m('pdf.intake_confirmation',
                       filename=filename, pending_id=pending_id),
                    protect_content=protect,
                )
            else:
                bot.reply_to(message, _m('pdf.intake_error'),
                             protect_content=protect)
        except Exception:
            pass
        return

    _forward_to_super_admins(bot, message)
    _send_platform_help(bot, message, first_time=False)


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

        lines = [f"📚 PDF-yada Sugaya: {count}", ""]
        if pending_list:
            for p in pending_list:
                lines.append(
                    f"• {p['filename']} (ID: {p['id']}) - {p['uploaded_at']}"
                )
            lines.append("")
            lines.append(_m('pdf.pending_manage_hint'))
            base_url = Config.BASE_URL.rstrip('/')
            secret_path = getattr(Config, 'PDF_ADMIN_SECRET_PATH', None) or '/pdf-admin'
            lines.append(f"Web: {base_url}{secret_path}")
        else:
            lines.append(_m('pdf.pending_list_empty'))

        bot.edit_message_text(
            "\n".join(lines),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None,
        )