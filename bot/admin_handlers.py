# bot/admin_handlers.py
# Admin keyboard and callback handlers for Telegram

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.bot import is_admin
from bot.utils import get_pending_pdfs_count, get_pending_pdf_list

logger = logging.getLogger(__name__)

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await query.answer("You are not authorized.", show_alert=True)
        return

    await query.answer()

    data = query.data
    if data == "pdf_admin_pending":
        count = get_pending_pdfs_count()
        pending_list = get_pending_pdf_list(limit=5)
        text = f"📚 Pending PDFs: {count}\n\n"
        if pending_list:
            for p in pending_list:
                text += f"• {p['filename']} (ID: {p['id']}) - uploaded {p['uploaded_at']}\n"
            text += "\nUse the web admin panel to process them.\n"
            text += "Web panel: <your-secret-url> (configured by admin)"
        else:
            text += "No pending PDFs."
        await query.edit_message_text(text, reply_markup=None)