import logging
import os
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from pdf_phone_extractor import extract_phones_from_pdf
from phone_record import PhoneRecord
from whatsapp_sender import WhatsAppSender

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ТВОЙ_ТОКЕН")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "1636373767"))

# Шаблоны приветственных сообщений
DEFAULT_TEMPLATES = {
    "greeting1": "Здравствуйте! Нашёл ваш контакт в профиле. Хотел бы обсудить возможное сотрудничество. Будет удобно пообщаться?",
    "greeting2": "Добрый день! Меня зовут [Имя]. Увидел ваш профиль и хотел бы предложить сотрудничество. Когда будет удобно обсудить?",
    "greeting3": "Привет! Нашёл ваш номер в каталоге. Есть интересное предложение — напишите, когда будет минутка."
}


class ProfileBot:
    def __init__(self):
        self.phone_records = []
        self.selected_phone = None
        self.user_mode = {}  # chat_id -> mode
        self.pending_template = None
        self.custom_templates = dict(DEFAULT_TEMPLATES)
        self.daily_sent_count = 0
        self.sent_log = []  # лог отправок

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if chat_id != ADMIN_CHAT_ID:
            await update.message.reply_text("❌ У вас нет доступа к этому боту.")
            return

        status = WhatsAppSender.get_status()
        wa_status = "✅ Подключён" if status.get("connected") else "❌ Не подключён"

        if status.get("pairing_code"):
            wa_status += f"\n🔑 Код привязки: `{status['pairing_code']}`"

        await update.message.reply_text(
            f"🤖 **Бот WhatsApp Рассылки**\n\n"
            f"📱 WhatsApp: {wa_status}\n"
            f"📊 Отправлено сегодня: {self.daily_sent_count}/40\n\n"
            f"**Команды:**\n"
            f"/pdf — загрузить PDF с номерами\n"
            f"/numbers — список номеров\n"
            f"/send — отправить одному номеру из списка\n"
            f"/sendall — отправить всем из списка\n"
            f"/sendmanual — отправить на любой номер\n"
            f"/templates — шаблоны сообщений\n"
            f"/wastatus — статус WhatsApp\n"
            f"/wapair — привязать WhatsApp\n"
            f"/walogout — отвязать WhatsApp\n"
            f"/clear — очистить список номеров\n"
            f"/log — лог отправок",
            parse_mode="Markdown"
        )

    # ─── WhatsApp управление ───

    async def wa_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ADMIN_CHAT_ID:
            return

        status = WhatsAppSender.get_status()

        text = f"📱 **Статус WhatsApp**\n\n"
        text += f"Подключение: {'✅ Да' if status.get('connected') else '❌ Нет'}\n"
        text += f"Статус: {status.get('status', 'unknown')}\n"

        if status.get('pairing_code'):
            text += f"\n🔑 **Код привязки:** `{status['pairing_code']}`\n"
            text += f"Откройте WhatsApp → Настройки → Связанные устройства → Привязать по номеру"

        if status.get('error'):
            text += f"\n⚠️ Ошибка: {status['error']}"

        await update.message.reply_text(text, parse_mode="Markdown")

    async def wa_pair(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ADMIN_CHAT_ID:
            return

        if not context.args:
            await update.message.reply_text(
                "📱 Использование: `/wapair 79XXXXXXXXX`\n"
                "Укажите ваш номер WhatsApp без + и пробелов",
                parse_mode="Markdown"
            )
            return

        phone = context.args[0].strip()
        phone = phone.replace('+', '').replace(' ', '').replace('-', '')

        await update.message.reply_text(f"⏳ Запрашиваю код привязки для {phone}...")

        result = WhatsAppSender.pair(phone)

        if result.get('success'):
            await update.message.reply_text(
                f"✅ Запрос отправлен!\n\n"
                f"Подождите 10 секунд и используйте /wastatus чтобы увидеть код.\n\n"
                f"Затем откройте WhatsApp на телефоне:\n"
                f"⚙️ Настройки → Связанные устройства → Привязать устройство → Привязать по номеру телефона"
            )
        else:
            await update.message.reply_text(f"❌ Ошибка: {result.get('error')}")

    async def wa_logout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ADMIN_CHAT_ID:
            return

        result = WhatsAppSender.logout()
        if result.get('success'):
            await update.message.reply_text("✅ WhatsApp отключён")
        else:
            await update.message.reply_text(f"❌ Ошибка: {result.get('error')}")

    # ─── Номера ───

    async def numbers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ADMIN_CHAT_ID:
            return

        if not self.phone_records:
            await update.message.reply_text("📋 Список номеров пуст. Отправьте PDF файл.")
            return

        lines = [f"📋 **Список номеров ({len(self.phone_records)}):**\n"]
        for i, record in enumerate(self.phone_records):
            lines.append(f"{i+1}. `{record.phone}` — {record.file_name}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ADMIN_CHAT_ID:
            return
        count = len(self.phone_records)
        self.phone_records.clear()
        await update.message.reply_text(f"🗑 Удалено {count} номеров")

    # ─── Шаблоны ───

    async def templates(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ADMIN_CHAT_ID:
            return

        text = "📝 **Шаблоны сообщений:**\n\n"
        keyboard = []

        for key, tmpl in self.custom_templates.items():
            text += f"**{key}:**\n{tmpl}\n\n"

        text += "Чтобы добавить свой шаблон: `/addtemplate имя Текст сообщения`"

        await update.message.reply_text(text, parse_mode="Markdown")

    async def add_template(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ADMIN_CHAT_ID:
            return

        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "Использование: `/addtemplate имя Текст шаблона`",
                parse_mode="Markdown"
            )
            return

        name = context.args[0]
        text = ' '.join(context.args[1:])
        self.custom_templates[name] = text
        await update.message.reply_text(f"✅ Шаблон `{name}` сохранён", parse_mode="Markdown")

    # ─── Отправка одному ───

    async def send_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ADMIN_CHAT_ID:
            return

        if not self.phone_records:
            await update.message.reply_text("❌ Список номеров пуст.")
            return

        keyboard = []
        for record in self.phone_records[:20]:  # макс 20 кнопок
            keyboard.append([InlineKeyboardButton(
                record.phone, callback_data=f"pick_{record.phone}"
            )])

        await update.message.reply_text(
            "📱 Выберите номер:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def send_manual(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ADMIN_CHAT_ID:
            return

        self.user_mode[ADMIN_CHAT_ID] = "manual_input"
        await update.message.reply_text(
            "✏️ Введите номер и текст через пробел:\n"
            "`+79123456789 Привет, это тестовое сообщение`\n\n"
            "Или номер и имя шаблона:\n"
            "`+79123456789 #greeting1`",
            parse_mode="Markdown"
        )

    # ─── Рассылка всем ───

    async def send_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ADMIN_CHAT_ID:
            return

        if not self.phone_records:
            await update.message.reply_text("❌ Список пуст.")
            return

        # Показываем шаблоны для выбора
        keyboard = []
        for key in self.custom_templates:
            keyboard.append([InlineKeyboardButton(
                f"📝 {key}", callback_data=f"bulktpl_{key}"
            )])
        keyboard.append([InlineKeyboardButton(
            "✏️ Свой текст", callback_data="bulktpl_custom"
        )])

        await update.message.reply_text(
            f"📨 Рассылка на {len(self.phone_records)} номеров\n"
            f"Выберите шаблон или введите свой текст:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # ─── Лог ───

    async def show_log(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ADMIN_CHAT_ID:
            return

        if not self.sent_log:
            await update.message.reply_text("📊 Лог пуст")
            return

        lines = ["📊 **Последние отправки:**\n"]
        for entry in self.sent_log[-20:]:
            status = "✅" if entry["success"] else "❌"
            lines.append(f"{status} {entry['phone']} — {entry.get('error', 'OK')}")

        lines.append(f"\nВсего сегодня: {self.daily_sent_count}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    # ─── Callbacks ───

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if query.message.chat_id != ADMIN_CHAT_ID:
            return

        data = query.data

        # Выбор номера для одиночной отправки
        if data.startswith("pick_"):
            phone = data[5:]
            self.selected_phone = phone

            # Предлагаем шаблон
            keyboard = []
            for key in self.custom_templates:
                keyboard.append([InlineKeyboardButton(
                    f"📝 {key}", callback_data=f"tpl_{key}"
                )])
            keyboard.append([InlineKeyboardButton(
                "✏️ Свой текст", callback_data="tpl_custom"
            )])

            await query.edit_message_text(
                f"📱 Номер: `{phone}`\nВыберите шаблон или введите текст:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

        # Выбор шаблона для одиночной отправки
        elif data.startswith("tpl_"):
            tpl_name = data[4:]

            if tpl_name == "custom":
                self.user_mode[ADMIN_CHAT_ID] = "custom_text"
                await query.edit_message_text(
                    f"✏️ Введите текст сообщения для {self.selected_phone}:"
                )
            else:
                message_text = self.custom_templates.get(tpl_name, "")
                if message_text and self.selected_phone:
                    await query.edit_message_text(f"⏳ Отправка на {self.selected_phone}...")
                    await self._do_send(query.message, self.selected_phone, message_text)
                    self.selected_phone = None

        # Выбор шаблона для массовой рассылки
        elif data.startswith("bulktpl_"):
            tpl_name = data[8:]

            if tpl_name == "custom":
                self.user_mode[ADMIN_CHAT_ID] = "bulk_custom_text"
                await query.edit_message_text("✏️ Введите текст для массовой рассылки:")
            else:
                message_text = self.custom_templates.get(tpl_name, "")
                if message_text:
                    await self._do_bulk_send(query.message, message_text)

    # ─── Обработка сообщений ───

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if chat_id != ADMIN_CHAT_ID:
            return

        # PDF
        if update.message.document and update.message.document.mime_type == "application/pdf":
            await self.process_pdf(update, context)
            return

        if not update.message.text:
            return

        text = update.message.text
        mode = self.user_mode.get(chat_id)

        # Ручной ввод номера+текста
        if mode == "manual_input":
            self.user_mode.pop(chat_id, None)
            await self._handle_manual_input(update, text)
            return

        # Свой текст для одиночной отправки
        if mode == "custom_text" and self.selected_phone:
            self.user_mode.pop(chat_id, None)
            await update.message.reply_text(f"⏳ Отправка на {self.selected_phone}...")
            await self._do_send(update.message, self.selected_phone, text)
            self.selected_phone = None
            return

        # Свой текст для массовой рассылки
        if mode == "bulk_custom_text":
            self.user_mode.pop(chat_id, None)
            await self._do_bulk_send(update.message, text)
            return

    async def _handle_manual_input(self, update: Update, text: str):
        parts = text.split(' ', 1)
        if len(parts) < 2:
            await update.message.reply_text("❌ Формат: `+79XXXXXXXXX текст`", parse_mode="Markdown")
            return

        phone = parts[0].strip()
        message = parts[1].strip()

        if not re.match(r'\+?7\d{10}$', phone.replace('+', '+')):
            await update.message.reply_text("❌ Неверный формат номера")
            return

        # Если ссылка на шаблон
        if message.startswith('#'):
            tpl_name = message[1:]
            if tpl_name in self.custom_templates:
                message = self.custom_templates[tpl_name]
            else:
                await update.message.reply_text(f"❌ Шаблон `{tpl_name}` не найден", parse_mode="Markdown")
                return

        await update.message.reply_text(f"⏳ Отправка на {phone}...")
        await self._do_send(update.message, phone, message)

    async def _do_send(self, message, phone: str, text: str):
        """Отправить одно сообщение"""
        if self.daily_sent_count >= 40:
            await message.reply_text("⚠️ Достигнут лимит 40 сообщений в день!")
            return

        result = WhatsAppSender.send_message(phone, text)

        log_entry = {
            "phone": phone,
            "success": result.get("success", False),
            "error": result.get("error", "")
        }
        self.sent_log.append(log_entry)

        if result.get("success"):
            self.daily_sent_count += 1
            await message.reply_text(
                f"✅ Отправлено на {phone}\n"
                f"📊 Отправок сегодня: {self.daily_sent_count}/40"
            )
        else:
            error = result.get("error", "Неизвестная ошибка")

            if "не подключён" in error or "bridge_offline" in result.get("status", ""):
                await message.reply_text(
                    f"❌ WhatsApp не подключён!\n"
                    f"Используйте /wapair для привязки"
                )
            else:
                await message.reply_text(f"❌ Ошибка: {error}")

    async def _do_bulk_send(self, message, text: str):
        """Массовая рассылка"""
        phones = [r.phone for r in self.phone_records]
        remaining = 40 - self.daily_sent_count

        if remaining <= 0:
            await message.reply_text("⚠️ Лимит 40 сообщений в день исчерпан!")
            return

        if len(phones) > remaining:
            phones = phones[:remaining]
            await message.reply_text(
                f"⚠️ Из-за лимита будет отправлено только {remaining} из {len(self.phone_records)}"
            )

        result = WhatsAppSender.send_bulk(phones, text, delay=30)

        if result.get("success"):
            self.daily_sent_count += len(phones)
            await message.reply_text(
                f"📨 {result.get('message', 'Рассылка запущена')}\n"
                f"⏱ {result.get('estimated_time', '')}\n\n"
                f"Сообщения отправляются в фоне с интервалом 30-40 сек для безопасности."
            )
        else:
            await message.reply_text(f"❌ Ошибка: {result.get('error')}")

    async def process_pdf(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        document = update.message.document
        file_name = document.file_name

        file = await context.bot.get_file(document.file_id)
        temp_path = f"/tmp/{file_name}"
        await file.download_to_drive(temp_path)

        try:
            phones = extract_phones_from_pdf(temp_path)

            if not phones:
                await update.message.reply_text("⚠️ В PDF не найдено номеров.")
            else:
                added = 0
                existing_phones = {r.phone for r in self.phone_records}

                for phone in phones:
                    if phone not in existing_phones:
                        record = PhoneRecord(
                            phone, file_name,
                            update.message.from_user.username or "unknown",
                            update.message.from_user.first_name or "unknown"
                        )
                        self.phone_records.append(record)
                        added += 1

                await update.message.reply_text(
                    f"✅ Найдено {len(phones)} номеров, добавлено {added} новых\n"
                    f"📋 Всего в списке: {len(self.phone_records)}\n\n"
                    f"Используйте /send или /sendall"
                )
        except Exception as e:
            logger.error(f"Ошибка PDF: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


def main():
    # Проверяем WA Bridge
    try:
        WhatsAppSender.init()
    except Exception as e:
        logger.warning(f"⚠️ WA Bridge недоступен: {e}")
        logger.warning("Бот запустится без WhatsApp. Привяжите через /wapair")

    bot = ProfileBot()
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("numbers", bot.numbers))
    application.add_handler(CommandHandler("send", bot.send_command))
    application.add_handler(CommandHandler("sendall", bot.send_all))
    application.add_handler(CommandHandler("sendmanual", bot.send_manual))
    application.add_handler(CommandHandler("templates", bot.templates))
    application.add_handler(CommandHandler("addtemplate", bot.add_template))
    application.add_handler(CommandHandler("wastatus", bot.wa_status))
    application.add_handler(CommandHandler("wapair", bot.wa_pair))
    application.add_handler(CommandHandler("walogout", bot.wa_logout))
    application.add_handler(CommandHandler("clear", bot.clear))
    application.add_handler(CommandHandler("log", bot.show_log))
    application.add_handler(CallbackQueryHandler(bot.handle_callback))
    application.add_handler(MessageHandler(filters.ALL, bot.handle_message))

    logger.info("✅ Бот запущен!")
    application.run_polling()


if __name__ == "__main__":
    main()