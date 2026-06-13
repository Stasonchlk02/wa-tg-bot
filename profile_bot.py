import logging
import os
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters, ContextTypes)
from pdf_phone_extractor import extract_phones_from_pdf
from phone_record import PhoneRecord
from whatsapp_sender import WhatsAppSender

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ТВОЙ_ТОКЕН")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "1636373767"))

# In-memory storage for phone records (for demo)
phone_records = []

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📱 Бот для отправки сообщений в WhatsApp\n"
        "Отправьте PDF-файл – я извлеку номера телефонов.\n"
        "Используйте /send для отправки сообщения на извлеченные номера.\n"
        "/status – статус WA Bridge\n"
        "/pair – получить код пары для WhatsApp\n"
        "/logout – выйти из WhatsApp"
    )

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        return
    doc = update.message.document
    if not doc.file_name.lower().endswith('.pdf'):
        await update.message.reply_text("❌ Пожалуйста, отправьте PDF файл.")
        return
    # Download PDF
    file = await context.bot.get_file(doc.file_id)
    file_path = f"/tmp/{doc.file_name}"
    await file.download_to_drive(file_path)
    # Extract phones
    phones = extract_phones_from_pdf(file_path)
    # Clean up
    os.remove(file_path)
    if not phones:
        await update.message.reply_text("📭 Не найдено номеров телефонов в PDF.")
        return
    # Store records
    username = update.message.from_user.username or ""
    first_name = update.message.from_user.first_name or ""
    for phone in phones:
        record = PhoneRecord(phone, doc.file_name, username, first_name)
        phone_records.append(record)
    # Show result
    phones_str = "\n".join(phones[:10])
    more = f"\n... и ещё {len(phones)-10}" if len(phones) > 10 else ""
    await update.message.reply_text(
        f"✅ Найдено {len(phones)} номер(ов):\n{phones_str}{more}\n"
        "Используйте /send для рассылки."
    )

async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not phone_records:
        await update.message.reply_text("Нет номеров. Сначала отправьте PDF.")
        return
    # Get unique phones
    unique_phones = list({rec.phone for rec in phone_records})
    await update.message.reply_text(
        f"📨 Будет отправлено сообщение на {len(unique_phones)} номеров.\n"
        "Напишите текст сообщения:"
    )
    context.user_data['awaiting_message'] = True

async def sendall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ Только для админа.")
        return
    if not phone_records:
        await update.message.reply_text("Нет номеров.")
        return
    unique_phones = list({rec.phone for rec in phone_records})
    await update.message.reply_text(
        f"👑 Админ: будет отправлено {len(unique_phones)} сообщений.\n"
        "Введите текст:"
    )
    context.user_data['awaiting_admin_message'] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Regular user message for sending
    if context.user_data.get('awaiting_message'):
        text = update.message.text
        unique_phones = list({rec.phone for rec in phone_records})
        # Send via bridge
        status = WhatsAppSender.get_status()
        if not status.get('connected'):
            await update.message.reply_text("❌ WhatsApp не подключён. Используйте /pair")
            context.user_data['awaiting_message'] = False
            return
        # Send bulk
        result = WhatsAppSender.send_bulk(unique_phones, text)
        if result.get('success'):
            await update.message.reply_text(f"✅ Отправлено {result.get('sent',0)} из {result.get('total',0)}")
        else:
            await update.message.reply_text(f"❌ Ошибка: {result.get('error')}")
        context.user_data['awaiting_message'] = False
    elif context.user_data.get('awaiting_admin_message') and update.effective_chat.id == ADMIN_CHAT_ID:
        text = update.message.text
        unique_phones = list({rec.phone for rec in phone_records})
        status = WhatsAppSender.get_status()
        if not status.get('connected'):
            await update.message.reply_text("❌ WhatsApp не подключён.")
            context.user_data['awaiting_admin_message'] = False
            return
        result = WhatsAppSender.send_bulk(unique_phones, text)
        if result.get('success'):
            await update.message.reply_text(f"✅ Админ: отправлено {result.get('sent')}")
        else:
            await update.message.reply_text(f"❌ Ошибка: {result.get('error')}")
        context.user_data['awaiting_admin_message'] = False
    else:
        await update.message.reply_text("Отправьте PDF или используйте /send")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = WhatsAppSender.get_status()
    if status.get('connected'):
        await update.message.reply_text(f"✅ WhatsApp подключён\nСтатус: {status.get('status')}")
    else:
        await update.message.reply_text("❌ WhatsApp НЕ подключён. Используйте /pair")

async def pair_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Запрашиваю код пары для WhatsApp...")
    result = WhatsAppSender.pair()
    if result.get('success'):
        code = result.get('code')
        await update.message.reply_text(f"📱 Код пары: `{code}`\nВведите его в WhatsApp Web.", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"❌ Ошибка: {result.get('error')}")

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = WhatsAppSender.logout()
    if result.get('success'):
        await update.message.reply_text("👋 Выполнен выход из WhatsApp.")
    else:
        await update.message.reply_text(f"❌ Ошибка: {result.get('error')}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("send", send_command))
    app.add_handler(CommandHandler("sendall", sendall_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("pair", pair_command))
    app.add_handler(CommandHandler("logout", logout_command))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
