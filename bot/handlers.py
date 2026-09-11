# bot/handlers.py
# Message and callback handlers for telebot

import logging
import telebot
from telebot import types

from bot.utils import (
    is_duplicate_in_bot, save_pending_pdf, is_admin
)
from bot.db import count_pending_pdfs, get_pending_pdf_list, get_bot_pdf_by_code
from config import Config

logger = logging.getLogger(__name__)

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
    if message.text:
        if message.text.startswith('/start'):
            if len(message.text.split()) > 1:
                handle_start_with_code(bot, message)
            else:
                handle_start(bot, message)
        elif message.text.startswith('/help'):
            handle_help(bot, message)
        else:
            # Other text messages
            pass
    elif message.document:
        handle_document(bot, message)

def handle_callback(bot, call):
    if call.data.startswith('pdf_admin_'):
        handle_admin_pending(bot, call)

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

def handle_start_with_code(bot, message):
    """Handle /start <code> to send a PDF with rich description and button."""
    text = message.text
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        code = parts[1].strip()
        bot_pdf = get_bot_pdf_by_code(code)
        if bot_pdf:
            try:
                file_id = bot_pdf['file_id']
                
                # Build rich caption
                caption = f"📄 *{bot_pdf['title']}*\n\n"
                if bot_pdf.get('description'):
                    caption += f"{bot_pdf['description']}\n\n"
                caption += f"📌 *Code:* `{code}`\n"
                caption += f"🔗 *Available on our platform with more study materials!*"
                
                # Build inline keyboard with "More PDFs" button
                base_url = Config.BASE_URL.rstrip('/')
                markup = types.InlineKeyboardMarkup()
                markup.add(
                    types.InlineKeyboardButton(
                        "📚 Browse More PDFs",
                        url=f"{base_url}/pdfs"
                    )
                )
                
                # Send with protect_content=True (prevents forwarding, saving, screenshotting)
                bot.send_document(
                    message.chat.id,
                    file_id,
                    caption=caption,
                    parse_mode='Markdown',
                    reply_markup=markup,
                    protect_content=True
                )
                return
            except Exception as e:
                logger.error(f"Error sending PDF with code {code}: {e}")
                bot.reply_to(message, "❌ Sorry, I couldn't retrieve the PDF. Please try again later.")
                return
    # If no code or invalid, show help
    handle_start(bot, message)

def handle_help(bot, message):
    bot.send_message(
        message.chat.id,
        "📖 Help:\n\n"
        "Send me a PDF document (any file with .pdf extension).\n"
        "I will check if it's already in the system.\n"
        "If it's new, it will be added to the pending queue for admin review.\n\n"
        "Admins: Use the Pending PDFs button to manage uploads."
    )

def handle_document(bot, message):
    document = message.document
    if not document:
        bot.reply_to(message, "❌ Please send a document file (PDF).")
        return

    if document.mime_type != 'application/pdf' and not document.file_name.endswith('.pdf'):
        bot.reply_to(message, "❌ Only PDF files are accepted.")
        return

    file_id = document.file_id
    file_unique_id = document.file_unique_id
    filename = document.file_name or 'unknown.pdf'
    user_id = message.from_user.id

    if is_duplicate_in_bot(file_unique_id):
        bot.reply_to(
            message,
            "⚠️ This PDF is already in the system (either already published or pending review).",
            protect_content=True
        )
        return

    pending_id = save_pending_pdf(file_id, file_unique_id, filename, user_id)
    if pending_id:
        # Build rich confirmation with "More PDFs" button
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
            protect_content=True
        )
    else:
        bot.reply_to(
            message,
            "❌ Failed to save the PDF. Please try again later.",
            protect_content=True
        )

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
            # Get the admin panel URL
            base_url = Config.BASE_URL.rstrip('/')
            secret_path = Config.PDF_ADMIN_SECRET_PATH or '/pdf-admin'
            text += f"Web panel: {base_url}{secret_path}"
        else:
            text += "No pending PDFs."
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)