# bot/admin_handlers.py
# Super admin commands for pyTelegramBotAPI (telebot).
#
# Commands:
#   /admin, /adminhelp       — command index
#   /pdfstats, /pdfs         — PDF library stats
#   /pending                 — list pending PDFs
#   /gate, /gatestatus       — force-join config + live health check
#   /gateusers               — users currently in the join gate
#   /cleargate <id|all>      — clear a gate row (or every row)
#   /joinstats               — join gate metrics
#
# All commands are gated by is_super_admin(user_id). Regular admins
# see "not authorized" — this is deliberate, since the commands
# expose config, internal state, and can clear gate rows for any
# user.

import logging

from bot.utils import is_super_admin
from bot.db import (
    _get_connection,
    count_pending_pdfs,
    get_pending_pdf_list,
    get_join_gate,
    delete_join_gate,
)
from config import Config

logger = logging.getLogger(__name__)


# ============================================
# HELPERS
# ============================================

def _super_admin_only(bot, message) -> bool:
    """
    Return True if the sender is a super admin.
    Sends a 'not authorized' reply and returns False otherwise.
    """
    user_id = message.from_user.id
    if is_super_admin(user_id):
        return True
    try:
        bot.reply_to(message, "🔒 Super admin only.")
    except Exception:
        pass
    return False


def _safe(s, max_len=None):
    """
    Strip Markdown-significant characters from user-supplied text so
    it can be interpolated into a Markdown message without breaking
    Telegram's parser. Also truncates if max_len is set.
    """
    if s is None:
        return ''
    s = str(s)
    for ch in ('_', '*', '`', '[', ']'):
        s = s.replace(ch, '')
    if max_len:
        s = s[:max_len]
    return s


def _send_long(bot, chat_id, text, chunk_size=3500):
    """
    Send a long message, splitting at chunk_size if needed.
    All content is passed through _safe() before this call, so the
    only Markdown the parser sees is the formatting we wrote.
    """
    try:
        if len(text) <= chunk_size:
            bot.send_message(
                chat_id, text,
                parse_mode='Markdown',
                disable_web_page_preview=True,
            )
            return
        chunks = []
        current = []
        size = 0
        for line in text.split('\n'):
            if size + len(line) + 1 > chunk_size and current:
                chunks.append('\n'.join(current))
                current = []
                size = 0
            current.append(line)
            size += len(line) + 1
        if current:
            chunks.append('\n'.join(current))
        for chunk in chunks:
            bot.send_message(
                chat_id, chunk,
                parse_mode='Markdown',
                disable_web_page_preview=True,
            )
    except Exception as e:
        logger.error(f"_send_long failed: {e}", exc_info=True)
        # Last resort: send as plain text
        try:
            bot.send_message(chat_id, text[:3500])
        except Exception:
            pass


# ============================================
# COMMAND INDEX
# ============================================

def cmd_admin_help(bot, message):
    if not _super_admin_only(bot, message):
        return
    text = (
        "🛠️ *Super Admin Commands*\n"
        "\n"
        "*PDF*\n"
        "/pdfstats — PDF library stats\n"
        "/pending — List pending PDFs\n"
        "\n"
        "*Force-Join Gate*\n"
        "/gate — Current config + health check\n"
        "/gateusers — Users currently in the gate\n"
        "/cleargate <id> — Clear a specific user\n"
        "/cleargate all — Clear every gate row\n"
        "/joinstats — Join-gate metrics\n"
    )
    try:
        bot.send_message(
            message.chat.id, text,
            parse_mode='Markdown',
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"cmd_admin_help failed: {e}")


# ============================================
# PDF STATS
# ============================================

def cmd_pdfstats(bot, message):
    if not _super_admin_only(bot, message):
        return

    try:
        conn = _get_connection()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) AS c FROM pdfs")
        total_pdfs = cur.fetchone()['c'] or 0

        cur.execute(
            "SELECT COUNT(*) AS c FROM pdfs "
            "WHERE COALESCE(published, 0) = 1"
        )
        published = cur.fetchone()['c'] or 0

        unpublished = total_pdfs - published

        cur.execute("SELECT COUNT(*) AS c FROM pending_pdfs")
        pending = cur.fetchone()['c'] or 0

        cur.execute(
            "SELECT "
            "SUM(CASE WHEN file_size IS NULL THEN 1 ELSE 0 END) AS unknown_size, "
            "SUM(CASE WHEN file_size = -1 THEN 1 ELSE 0 END) AS retryable_size, "
            "SUM(CASE WHEN file_size = -2 THEN 1 ELSE 0 END) AS too_large_size, "
            "SUM(CASE WHEN file_size > 0 THEN 1 ELSE 0 END) AS known_size "
            "FROM pdfs"
        )
        size_row = cur.fetchone() or {}
        unknown_size = size_row['unknown_size'] or 0
        retryable_size = size_row['retryable_size'] or 0
        too_large_size = size_row['too_large_size'] or 0
        known_size = size_row['known_size'] or 0

        cur.execute(
            "SELECT COALESCE(curriculum, '—') AS c, COUNT(*) AS n "
            "FROM pdfs GROUP BY c ORDER BY n DESC"
        )
        by_curriculum = [(r['c'], r['n']) for r in cur.fetchall()]

        cur.execute(
            "SELECT COALESCE(class, '—') AS c, COUNT(*) AS n "
            "FROM pdfs GROUP BY c ORDER BY n DESC"
        )
        by_class = [(r['c'], r['n']) for r in cur.fetchall()]

        cur.execute(
            "SELECT code, title, uploaded_at, published "
            "FROM pdfs ORDER BY uploaded_at DESC LIMIT 5"
        )
        recent = [dict(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT code, title, COALESCE(view_count, 0) AS vc "
            "FROM pdfs ORDER BY vc DESC LIMIT 5"
        )
        top_viewed = [dict(r) for r in cur.fetchall()]

        conn.close()
    except Exception as e:
        logger.error(f"cmd_pdfstats DB error: {e}", exc_info=True)
        try:
            bot.reply_to(message, f"❌ Failed to read stats: {e}")
        except Exception:
            pass
        return

    lines = []
    lines.append("📚 *PDF Library Stats*")
    lines.append("")
    lines.append(f"• Total: *{total_pdfs:,}*")
    lines.append(f"• Published: *{published:,}*")
    lines.append(f"• Unpublished: *{unpublished:,}*")
    lines.append(f"• Pending intake: *{pending:,}*")
    lines.append("")
    lines.append("*File Size Resolution*")
    lines.append(f"• Known: *{known_size:,}*")
    lines.append(f"• Unknown (NULL): *{unknown_size:,}*")
    lines.append(f"• Retryable (-1): *{retryable_size:,}*")
    lines.append(f"• Too large / gone (-2): *{too_large_size:,}*")

    if by_curriculum:
        lines.append("")
        lines.append("*By Curriculum*")
        for code, n in by_curriculum:
            lines.append(f"• {_safe(code)}: *{n:,}*")

    if by_class:
        lines.append("")
        lines.append("*By Class*")
        for cls, n in by_class:
            lines.append(f"• {_safe(cls)}: *{n:,}*")

    if top_viewed:
        lines.append("")
        lines.append("*Top 5 Most Viewed*")
        for i, r in enumerate(top_viewed, 1):
            t = _safe(r.get('title'), max_len=40)
            lines.append(f"{i}. `{_safe(r['code'])}` — {t} · *{r['vc']:,}*")

    if recent:
        lines.append("")
        lines.append("*5 Most Recent*")
        for i, r in enumerate(recent, 1):
            t = _safe(r.get('title'), max_len=40)
            pub = '✅' if r.get('published') else '⏳'
            lines.append(f"{i}. {pub} `{_safe(r['code'])}` — {t}")

    _send_long(bot, message.chat.id, '\n'.join(lines))


# ============================================
# PENDING LIST
# ============================================

def cmd_pending(bot, message):
    if not _super_admin_only(bot, message):
        return

    try:
        count = count_pending_pdfs()
        pending_list = get_pending_pdf_list(limit=20)
    except Exception as e:
        logger.error(f"cmd_pending DB error: {e}", exc_info=True)
        try:
            bot.reply_to(message, f"❌ Failed to read pending: {e}")
        except Exception:
            pass
        return

    lines = [f"⏳ *Pending PDFs*: {count}"]
    lines.append("")

    if not pending_list:
        lines.append("_No pending PDFs._")
    else:
        for i, p in enumerate(pending_list, 1):
            fn = _safe(p.get('filename') or 'unnamed', max_len=40)
            uploaded = _safe((p.get('uploaded_at') or '')[:16])
            lines.append(f"{i}. `#{p['id']}` — {fn}  ·  _{uploaded}_")
        if count > len(pending_list):
            lines.append("")
            lines.append(f"_…and {count - len(pending_list)} more._")

    base_url = (Config.BASE_URL or '').rstrip('/')
    if base_url:
        lines.append("")
        lines.append(f"Manage: {base_url}/admin/pdfs")

    _send_long(bot, message.chat.id, '\n'.join(lines))


# ============================================
# GATE STATUS
# ============================================

def _check_bot_status(bot, chat_id):
    """
    Return a human-readable bot status in a chat, or an error string.
    Never raises.
    """
    try:
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            cid = chat_id
        me = bot.get_me()
        bot_id = getattr(me, 'id', None)
        if bot_id is None and isinstance(me, dict):
            bot_id = me.get('id')
        if not bot_id:
            return "⚠️ cannot read bot id"
        member = bot.get_chat_member(cid, bot_id)
        status = getattr(member, 'status', None)
        if status is None and isinstance(member, dict):
            status = member.get('status')
        if status in ('creator', 'administrator'):
            return f"✅ {status}"
        if status in ('member', 'restricted'):
            return f"⚠️ {status} (not admin — gate will fail open)"
        if status in ('left', 'kicked'):
            return f"❌ {status} (bot is not in the chat)"
        return f"⚠️ {status or 'unknown'}"
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if 'chat_admin_required' in low or 'chat admin required' in low:
            return "❌ CHAT_ADMIN_REQUIRED (bot not admin)"
        if 'member list is inaccessible' in low:
            return "❌ member list inaccessible (grant Add Users / Add Subscribers)"
        if 'chat not found' in low:
            return "❌ chat not found (wrong ID)"
        return f"❌ {msg[:80]}"


def cmd_gate(bot, message):
    if not _super_admin_only(bot, message):
        return

    enabled = getattr(Config, 'TELEGRAM_FORCE_JOIN_ENABLED', False)
    channel_id = getattr(Config, 'TELEGRAM_FORCE_CHANNEL_ID', '') or ''
    channel_invite = getattr(Config, 'TELEGRAM_FORCE_CHANNEL_INVITE', '') or ''
    group_id = getattr(Config, 'TELEGRAM_FORCE_GROUP_ID', '') or ''
    group_invite = getattr(Config, 'TELEGRAM_FORCE_GROUP_INVITE', '') or ''
    max_attempts = getattr(Config, 'TELEGRAM_FORCE_JOIN_MAX_ATTEMPTS', 10)

    lines = ["🚪 *Force-Join Gate*"]
    lines.append("")
    lines.append(f"• Enabled: {'✅ yes' if enabled else '❌ no'}")
    lines.append(f"• Max attempts: *{max_attempts}*")

    lines.append("")
    lines.append("*Channel*")
    if channel_id:
        lines.append(f"• ID: `{_safe(channel_id)}`")
        lines.append(f"• Invite: {'✅ set' if channel_invite else '❌ missing'}")
        lines.append(f"• Bot: {_check_bot_status(bot, channel_id)}")
    else:
        lines.append("_Not configured_")

    lines.append("")
    lines.append("*Group*")
    if group_id:
        lines.append(f"• ID: `{_safe(group_id)}`")
        lines.append(f"• Invite: {'✅ set' if group_invite else '❌ missing'}")
        lines.append(f"• Bot: {_check_bot_status(bot, group_id)}")
    else:
        lines.append("_Not configured_")

    _send_long(bot, message.chat.id, '\n'.join(lines))


# ============================================
# GATE USERS
# ============================================

def cmd_gateusers(bot, message):
    if not _super_admin_only(bot, message):
        return

    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT user_id, stage, code, attempts, created_at, updated_at
            FROM join_gate
            ORDER BY updated_at DESC
            LIMIT 50
        """)
        rows = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) AS c FROM join_gate")
        total = cur.fetchone()['c'] or 0
        conn.close()
    except Exception as e:
        logger.error(f"cmd_gateusers DB error: {e}", exc_info=True)
        try:
            bot.reply_to(message, f"❌ Failed to read gate rows: {e}")
        except Exception:
            pass
        return

    lines = [f"🚪 *Users in Join Gate*: {total}"]
    lines.append("")

    if not rows:
        lines.append("_No users currently in the gate._")
    else:
        for r in rows:
            stage = _safe(r.get('stage') or '?')
            code = _safe(r.get('code') or '—')
            attempts = r.get('attempts') or 0
            updated = _safe((r.get('updated_at') or '')[:16])
            lines.append(
                f"`{r['user_id']}` · *{stage}* · `{code}` · "
                f"attempts {attempts} · _{updated}_"
            )
        if total > len(rows):
            lines.append("")
            lines.append(f"_…and {total - len(rows)} more._")
        lines.append("")
        lines.append("Clear one: `/cleargate <user_id>`")
        lines.append("Clear all: `/cleargate all`")

    _send_long(bot, message.chat.id, '\n'.join(lines))


# ============================================
# CLEAR GATE
# ============================================

def cmd_cleargate(bot, message):
    if not _super_admin_only(bot, message):
        return

    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        try:
            bot.reply_to(
                message,
                "Usage: `/cleargate <user_id>` or `/cleargate all`",
                parse_mode='Markdown',
            )
        except Exception:
            pass
        return

    arg = parts[1].strip()

    if arg.lower() == 'all':
        try:
            conn = _get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM join_gate")
            n = cur.rowcount or 0
            conn.commit()
            conn.close()
            try:
                bot.reply_to(
                    message,
                    f"✅ Cleared *{n}* gate row(s).",
                    parse_mode='Markdown',
                )
            except Exception:
                pass
            logger.info(
                f"super admin {message.from_user.id} cleared all "
                f"join gate rows ({n})"
            )
        except Exception as e:
            logger.error(f"cmd_cleargate all failed: {e}", exc_info=True)
            try:
                bot.reply_to(message, f"❌ Failed: {e}")
            except Exception:
                pass
        return

    try:
        uid = int(arg)
    except (TypeError, ValueError):
        try:
            bot.reply_to(message, "❌ Invalid user_id.")
        except Exception:
            pass
        return

    row = get_join_gate(uid)
    if not row:
        try:
            bot.reply_to(
                message,
                f"ℹ️ No gate row for `{uid}`.",
                parse_mode='Markdown',
            )
        except Exception:
            pass
        return

    ok = delete_join_gate(uid)
    if ok:
        stage = _safe(row.get('stage'))
        try:
            bot.reply_to(
                message,
                f"✅ Cleared gate for `{uid}` (was stage=_{stage}_).",
                parse_mode='Markdown',
            )
        except Exception:
            pass
        logger.info(
            f"super admin {message.from_user.id} cleared gate "
            f"for user {uid}"
        )
    else:
        try:
            bot.reply_to(message, "❌ Failed to clear.")
        except Exception:
            pass


# ============================================
# JOIN STATS
# ============================================

def cmd_joinstats(bot, message):
    if not _super_admin_only(bot, message):
        return

    try:
        conn = _get_connection()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) AS c FROM join_gate")
        total = cur.fetchone()['c'] or 0

        cur.execute(
            "SELECT COUNT(*) AS c FROM join_gate "
            "WHERE created_at >= datetime('now', 'localtime', '-24 hours')"
        )
        new_24h = cur.fetchone()['c'] or 0

        cur.execute(
            "SELECT COUNT(*) AS c FROM join_gate "
            "WHERE updated_at >= datetime('now', 'localtime', '-24 hours')"
        )
        active_24h = cur.fetchone()['c'] or 0

        cur.execute(
            "SELECT COUNT(*) AS c FROM join_gate WHERE attempts >= 5"
        )
        struggling = cur.fetchone()['c'] or 0

        cur.execute(
            "SELECT COUNT(*) AS c FROM join_gate WHERE attempts >= 10"
        )
        near_cutoff = cur.fetchone()['c'] or 0

        cur.execute(
            "SELECT stage, COUNT(*) AS c FROM join_gate "
            "GROUP BY stage ORDER BY c DESC"
        )
        by_stage = [(r['stage'], r['c']) for r in cur.fetchall()]

        conn.close()
    except Exception as e:
        logger.error(f"cmd_joinstats DB error: {e}", exc_info=True)
        try:
            bot.reply_to(message, f"❌ Failed: {e}")
        except Exception:
            pass
        return

    lines = ["📊 *Join Gate Metrics*"]
    lines.append("")
    lines.append(f"• Currently in gate: *{total}*")
    lines.append(f"• New in last 24h: *{new_24h}*")
    lines.append(f"• Active in last 24h: *{active_24h}*")
    lines.append(f"• Struggling (≥5 attempts): *{struggling}*")
    lines.append(f"• Near cutoff (≥10 attempts): *{near_cutoff}*")

    if by_stage:
        lines.append("")
        lines.append("*By Stage*")
        for stage, n in by_stage:
            lines.append(f"• {_safe(stage)}: *{n}*")

    _send_long(bot, message.chat.id, '\n'.join(lines))


# ============================================
# DISPATCH
# ============================================

COMMANDS = {
    '/admin':      cmd_admin_help,
    '/adminhelp':  cmd_admin_help,
    '/pdfstats':   cmd_pdfstats,
    '/pdfs':       cmd_pdfstats,
    '/pending':    cmd_pending,
    '/gate':       cmd_gate,
    '/gatestatus': cmd_gate,
    '/gateusers':  cmd_gateusers,
    '/cleargate':  cmd_cleargate,
    '/joinstats':  cmd_joinstats,
}


def dispatch(bot, message) -> bool:
    """
    Look up the command in COMMANDS and run it.
    Returns True if the command was handled, False otherwise.

    Strips /cmd@botname to /cmd, so the same dispatch works in
    groups where Telegram appends the bot's username.
    """
    text = (message.text or '').strip()
    if not text.startswith('/'):
        return False

    cmd = text.split()[0].lower()
    if '@' in cmd:
        cmd = cmd.split('@', 1)[0]

    handler = COMMANDS.get(cmd)
    if not handler:
        return False

    try:
        handler(bot, message)
    except Exception as e:
        logger.error(f"admin command {cmd} failed: {e}", exc_info=True)
        try:
            bot.reply_to(message, f"❌ Command failed: {e}")
        except Exception:
            pass
    return True