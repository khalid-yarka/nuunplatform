"""
services/telegram_notify.py
===========================
Unified Telegram dispatcher for platform events targeting super admins.

Public API
----------
    notify_super_admins(...)      -> Dict[str, Any]
    build_markdown_document(...)  -> str
    build_short_message(...)      -> str
    resolve_recipients(...)       -> List[int]
    make_report_filename(...)     -> str
    summary_row(icon, label, value) -> Dict[str, str]

Delivery model
--------------
For each recipient, ONE chat thread receives:
    1. A short HTML-formatted Telegram message (parse_mode='HTML').
    2. The full markdown report attached as a .md file.

Human-readable parts always use Somali time format
(``format_somali_time_with_seconds``). ISO timestamps appear ONLY
inside the YAML frontmatter of the .md file for machine parsing.

Design goals
------------
- Never raises. All failures are collected into the result dict.
- HTML parse mode for the summary (fewer escaping pitfalls than MDv2).
- ``<blockquote expandable>`` for collapsible summary blocks.
- ``InputFile`` for reliable filename on attached documents.
- Recipient resolution: super admins first, admins as fallback.
"""

from __future__ import annotations

import io
import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from config import Config
from utils import (
    format_somali_time,
    format_somali_time_with_seconds,
    get_somali_time,
)

logger = logging.getLogger(__name__)


# ============================================================
# PUBLIC CONSTANTS
# ============================================================

SEVERITY_ICONS: Dict[str, str] = {
    'info':     'ℹ️',
    'success':  '✅',
    'warning':  '⚠️',
    'error':    '❗',
    'critical': '🚨',
}

EVENT_ICONS: Dict[str, str] = {
    'error':            '🚨',
    'bug_report':       '🐞',
    'question_report':  '🚩',
    'user_report':      '📣',
    'system_alert':     '📡',
    'upgrade_request':  '🚀',
    'daily_brief':      '📊',
    'security_alert':   '🔐',
    'generic':          '📬',
}


# ============================================================
# TELEGRAM-SAFE HELPERS
# ============================================================

def escape_html(text: Any) -> str:
    """Escape text for Telegram HTML parse mode."""
    if text is None:
        return ''
    s = str(text)
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def truncate(text: Any, limit: int = 300) -> str:
    """Truncate long text with an ellipsis suffix."""
    if text is None:
        return ''
    s = str(text)
    if len(s) <= limit:
        return s
    return s[: limit - 1] + '…'


def summary_row(icon: str, label: str, value: Any) -> Dict[str, str]:
    """Build a summary row consumed by build_short_message()."""
    return {'icon': icon, 'label': label, 'value': str(value)}


def sanitize_filename(name: str) -> str:
    """Strip characters that don't belong in a filename."""
    safe = ''.join(
        c if (c.isalnum() or c in '-_') else '_'
        for c in str(name)
    )
    return safe[:120] or 'report'


def make_report_filename(prefix: str, reference_id: Optional[str] = None) -> str:
    """
    Build a filename such as:
        error_f1c5fea3_20260914_105447.md
        bug_report_20260914_105447.md
    """
    ts = get_somali_time().strftime('%Y%m%d_%H%M%S')
    ref = sanitize_filename(reference_id) if reference_id else ''
    parts = [sanitize_filename(prefix)]
    if ref:
        parts.append(ref)
    parts.append(ts)
    return '_'.join(parts) + '.md'


def utc_iso_now() -> str:
    """Machine-friendly ISO timestamp in Somali timezone (+03:00)."""
    return get_somali_time().isoformat()


def somali_now_long() -> str:
    return format_somali_time_with_seconds(get_somali_time())


def somali_now_short() -> str:
    return format_somali_time(get_somali_time())


# ============================================================
# RECIPIENT RESOLUTION
# ============================================================

def _parse_id_list(raw: Optional[str]) -> List[int]:
    if not raw:
        return []
    ids: List[int] = []
    for part in str(raw).split(','):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except (ValueError, TypeError):
            logger.warning(f"telegram_notify: ignoring invalid id {part!r}")
    return ids


def resolve_recipients(extra_ids: Optional[Iterable[int]] = None) -> List[int]:
    """
    Return the list of Telegram chat ids to notify.

    Priority:
        1. TELEGRAM_SUPER_ADMIN_IDS
        2. TELEGRAM_ADMIN_IDS (only when #1 is empty)
        3. extra_ids are appended and deduped
    """
    super_ids = _parse_id_list(getattr(Config, 'TELEGRAM_SUPER_ADMIN_IDS', '') or '')
    admin_ids = _parse_id_list(getattr(Config, 'TELEGRAM_ADMIN_IDS', '') or '')

    base = super_ids if super_ids else admin_ids
    result = list(dict.fromkeys(base))

    if extra_ids:
        for raw in extra_ids:
            try:
                v = int(raw)
            except (ValueError, TypeError):
                continue
            if v not in result:
                result.append(v)

    return result


# ============================================================
# MARKDOWN DOCUMENT BUILDER
# ============================================================

def _yaml_frontmatter(meta: Dict[str, Any]) -> str:
    lines = ['---']
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, bool):
            v = 'true' if value else 'false'
        elif isinstance(value, (int, float)):
            v = str(value)
        else:
            s = str(value).replace('\\', '\\\\').replace('"', '\\"')
            v = f'"{s}"'
        lines.append(f'{key}: {v}')
    lines.append('---')
    return '\n'.join(lines)


def build_markdown_document(
    title: str,
    severity: str = 'info',
    meta: Optional[Dict[str, Any]] = None,
    sections: Optional[Sequence[Tuple[str, str]]] = None,
    footer_id: Optional[str] = None,
    extra_footer: Optional[str] = None,
) -> str:
    """
    Compose a clean, GitHub-renderable markdown document.

    Args:
        title:        Document title (rendered as H1).
        severity:     One of SEVERITY_ICONS keys (lowercase).
        meta:         YAML frontmatter dict (request_id, url, user_id, ...).
        sections:     Ordered list of (heading, body). Body is raw markdown.
        footer_id:    Request/report id shown in the footer.
        extra_footer: Additional footer text.
    """
    sev = (severity or 'info').lower()
    icon = SEVERITY_ICONS.get(sev, 'ℹ️')
    now_somali = format_somali_time_with_seconds(get_somali_time())

    fm = dict(meta or {})
    fm.setdefault('platform', 'NuunPlatform')
    fm.setdefault('severity', sev)
    fm.setdefault('generated_at_somali', now_somali)
    fm.setdefault('generated_at_iso', get_somali_time().isoformat())

    parts: List[str] = []
    parts.append(_yaml_frontmatter(fm))
    parts.append('')
    parts.append(f'# {icon} {title}')
    parts.append('')
    parts.append(f'> **Generated:** {now_somali}  ')
    if footer_id:
        parts.append(f'> **Reference:** `{footer_id}`  ')
    parts.append('')
    parts.append('---')
    parts.append('')

    if sections:
        for heading, body in sections:
            if heading:
                parts.append(f'## {heading}')
                parts.append('')
            if body:
                parts.append(body)
                parts.append('')
            parts.append('---')
            parts.append('')

    footer_line = (
        f'<sub>📎 Generated by **NuunPlatform Bot** · {now_somali}'
        + (f' · `{footer_id}`' if footer_id else '')
        + (f' · {extra_footer}' if extra_footer else '')
        + '</sub>'
    )
    parts.append(footer_line)

    return '\n'.join(parts).rstrip() + '\n'


# ============================================================
# SHORT HTML MESSAGE BUILDER
# ============================================================

def build_short_message(
    title: str,
    severity: str = 'info',
    icon: Optional[str] = None,
    summary: Optional[Sequence[Dict[str, str]]] = None,
    primary_url: Optional[str] = None,
    primary_url_label: str = 'Open in admin panel',
    reference_id: Optional[str] = None,
    attach_notice: str = '📎 Full report attached below',
) -> str:
    """Build the short HTML message that goes into the Telegram chat."""
    sev = (severity or 'info').lower()
    sev_icon = icon or SEVERITY_ICONS.get(sev, 'ℹ️')

    lines: List[str] = [f'{sev_icon} <b>{escape_html(title)}</b>']
    lines.append(
        f'🕐 <code>{escape_html(format_somali_time_with_seconds(get_somali_time()))}</code>'
    )

    if reference_id:
        lines.append(f'🆔 <code>{escape_html(reference_id)}</code>')

    if summary:
        block: List[str] = []
        for item in summary:
            item_icon = escape_html(item.get('icon', '•'))
            label = escape_html(item.get('label', ''))
            value = escape_html(item.get('value', ''))
            if label:
                block.append(f'{item_icon} <b>{label}</b>\n<code>{value}</code>')
            else:
                block.append(f'{item_icon} <code>{value}</code>')
        if block:
            joined = '\n\n'.join(block)
            lines.append('')
            lines.append(f'<blockquote expandable>{joined}</blockquote>')

    if primary_url:
        lines.append('')
        lines.append(
            f'🔗 <a href="{escape_html(primary_url)}">{escape_html(primary_url_label)}</a>'
        )

    if attach_notice:
        lines.append('')
        lines.append(f'<i>{escape_html(attach_notice)}</i>')

    return '\n'.join(lines)


# ============================================================
# CORE DISPATCHER
# ============================================================

def _load_bot():
    """Lazy import — bot may not be initialized during early boot."""
    try:
        from bot.utils import get_bot
        return get_bot()
    except Exception as e:
        logger.error(f"telegram_notify: could not load bot: {e}")
        return None


def _try_send_message(bot, chat_id: int, html_message: str) -> None:
    """Send an HTML message; retry once without link preview flag on error."""
    try:
        bot.send_message(
            chat_id,
            html_message,
            parse_mode='HTML',
            disable_web_page_preview=True,
        )
        return
    except TypeError:
        # Older/newer TeleBot signature mismatch — retry without the flag
        bot.send_message(chat_id, html_message, parse_mode='HTML')
        return
    except Exception as first_err:
        logger.warning(
            f"telegram_notify: send_message attempt 1 failed for {chat_id}: {first_err}"
        )
        time.sleep(2)
        try:
            bot.send_message(
                chat_id,
                html_message,
                parse_mode='HTML',
                disable_web_page_preview=True,
            )
        except TypeError:
            bot.send_message(chat_id, html_message, parse_mode='HTML')


def _send_one(
    bot,
    chat_id: int,
    html_message: str,
    md_filename: str,
    md_content: str,
    md_caption: str,
) -> Tuple[bool, Optional[str]]:
    """Send the HTML summary then the .md document to a single chat id."""
    try:
        _try_send_message(bot, chat_id, html_message)
    except Exception as e:
        return False, f"send_message failed: {e}"

    try:
        from telebot.types import InputFile
        buf = io.BytesIO(md_content.encode('utf-8'))
        doc = InputFile(buf, md_filename)
        bot.send_document(
            chat_id,
            doc,
            caption=md_caption,
            parse_mode='HTML',
        )
    except Exception as e:
        logger.error(f"telegram_notify: send_document failed for {chat_id}: {e}")
        return False, f"send_document failed: {e}"

    return True, None


def notify_super_admins(
    event_type: str,
    title: str,
    md_body: str,
    md_filename: str,
    summary: Optional[Sequence[Dict[str, str]]] = None,
    primary_url: Optional[str] = None,
    primary_url_label: str = 'Open in admin panel',
    severity: str = 'info',
    reference_id: Optional[str] = None,
    extra_ids: Optional[Iterable[int]] = None,
    icon: Optional[str] = None,
    md_caption: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Send a two-part notification to every super admin (or admin fallback).

    Returns
    -------
    {
        'sent': int,
        'failed': int,
        'recipients': List[int],
        'errors': List[str],
        'skipped': bool,
    }
    """
    result: Dict[str, Any] = {
        'sent': 0,
        'failed': 0,
        'recipients': [],
        'errors': [],
        'skipped': False,
    }

    recipients = resolve_recipients(extra_ids=extra_ids)
    result['recipients'] = recipients

    if not recipients:
        logger.warning(
            f"telegram_notify: no recipients configured for event '{event_type}'"
        )
        result['skipped'] = True
        return result

    bot = _load_bot()
    if bot is None:
        logger.error("telegram_notify: bot unavailable; notification skipped")
        result['skipped'] = True
        result['errors'].append('bot unavailable')
        return result

    if not md_filename.endswith('.md'):
        md_filename = md_filename + '.md'

    sev = (severity or 'info').lower()
    ev_icon = icon or EVENT_ICONS.get(event_type, SEVERITY_ICONS.get(sev, 'ℹ️'))

    html_message = build_short_message(
        title=title,
        severity=sev,
        icon=ev_icon,
        summary=summary,
        primary_url=primary_url,
        primary_url_label=primary_url_label,
        reference_id=reference_id,
    )

    caption = md_caption or (
        f'{ev_icon} <b>{escape_html(title)}</b>\n'
        f'<code>{escape_html(md_filename)}</code>'
    )

    for chat_id in recipients:
        try:
            ok, err = _send_one(
                bot,
                chat_id,
                html_message,
                md_filename,
                md_body,
                caption,
            )
            if ok:
                result['sent'] += 1
            else:
                result['failed'] += 1
                if err:
                    result['errors'].append(f'{chat_id}: {err}')
        except Exception as e:
            logger.error(
                f"telegram_notify: unexpected error sending to {chat_id}: {e}",
                exc_info=True,
            )
            result['failed'] += 1
            result['errors'].append(f'{chat_id}: {e}')

    logger.info(
        f"telegram_notify: event={event_type} "
        f"sent={result['sent']} failed={result['failed']}"
    )
    return result