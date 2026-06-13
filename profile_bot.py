import logging
import os
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import Database
from pdf_phone_extractor import extract_phones_from_pdf
from phone_record import PhoneRecord
from whatsapp_sender import WhatsAppSender

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ТВОЙ_ТОКЕН")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "1636373767"))

# Глобальная база данных
db = Database()

# Паттерн для проверки номера
PHONE_RE = re.compile(r"^\+7\d{10}$")


# ─────────────────────────── Вспомогательные функции ───────────────────────────

def is_admin(update: Update) -> bool:
    return update.effective_chat.id == ADMIN_CHAT_ID


def admin_main_keyboard() -> InlineKeyboardMarkup:
    """Главная клавиатура администратора"""
    stats = db.get_stats()
    buttons = [
        [
            InlineKeyboardButton(
                f"📊 База: {stats['total']} | ✅ {stats['sent']} | ⏳ {stats['unsent']}",
                callback_data="stats",
            )
        ],
        [
            InlineKeyboardButton("📨 Разослать новым", callback_data="broadcast_unsent"),
            InlineKeyboardButton("📢 Разослать всем", callback_data="broadcast_all"),
        ],
        [
            InlineKeyboardButton("✏️ Текст рассылки", callback_data="set_text"),
            InlineKeyboardButton("👁 Текущий текст", callback_data="show_text"),
        ],
        [
            InlineKeyboardButton("📋 Список номеров", callback_data="list_phones:0"),
            InlineKeyboardButton("➕ Добавить номер", callback_data="add_phone"),
        ],
        [
            InlineKeyboardButton("🔌 Статус WA", callback_data="wa_status"),
            InlineKeyboardButton("📱 Пара WA", callback_data="wa_pair"),
        ],
        [
            InlineKeyboardButton("🚪 Выйти WA", callback_data="wa_logout"),
            InlineKeyboardButton("🗑 Очистить базу", callback_data="clear_confirm"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def user_main_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для обычного пользователя"""
    buttons = [
        [InlineKeyboardButton("📄 Отправить PDF", callback_data="hint_pdf")],
        [InlineKeyboardButton("ℹ️ О боте", callback_data="about")],
    ]
    return InlineKeyboardMarkup(buttons)


def back_keyboard(callback: str = "admin_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("◀️ Назад", callback_data=callback)]]
    )


def phones_list_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Клавиатура пагинации для списка номеров"""
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"list_phones:{page - 1}"))
    nav.append(
        InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
    )
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"list_phones:{page + 1}"))

    buttons = [
        nav,
        [
            InlineKeyboardButton("🔄 Сбросить статусы", callback_data="reset_sent"),
            InlineKeyboardButton("❌ Удалить номер", callback_data="delete_phone_prompt"),
        ],
        [InlineKeyboardButton("◀️ Меню", callback_data="admin_menu")],
    ]
    return InlineKeyboardMarkup(buttons)


# ─────────────────────────── Команды ───────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Стартовое меню"""
    context.user_data.clear()

    if is_admin(update):
        await update.message.reply_text(
            "👑 *Панель администратора*\n\n"
            "Управляйте базой номеров и рассылкой через кнопки ниже.\n"
            "Любой пользователь может присылать PDF — номера попадут в базу.",
            parse_mode="Markdown",
            reply_markup=admin_main_keyboard(),
        )
    else:
        await update.message.reply_text(
            "📱 *Бот для сбора номеров*\n\n"
            "Отправьте PDF-файл — я извлеку из него номер телефона.\n"
            "Можно также написать номер напрямую в формате +79XXXXXXXXX",
            parse_mode="Markdown",
            reply_markup=user_main_keyboard(),
        )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /menu — только для админа"""
    if not is_admin(update):
        return
    context.user_data.clear()
    await update.message.reply_text(
        "👑 *Панель администратора*",
        parse_mode="Markdown",
        reply_markup=admin_main_keyboard(),
    )


# ─────────────────────────── Обработка файлов и номеров ───────────────────────

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка любых документов от любого пользователя"""
    doc = update.message.document
    if not doc:
        return

    # Принимаем PDF и текстовые файлы со списками номеров
    fname = doc.file_name.lower()

    if fname.endswith(".pdf"):
        await _process_pdf(update, context, doc)
    elif fname.endswith(".txt"):
        await _process_txt(update, context, doc)
    else:
        await update.message.reply_text(
            "❌ Поддерживаются только PDF и TXT файлы.\n"
            "TXT — список номеров по одному на строку."
        )


async def _process_pdf(update, context, doc):
    """Извлечь номера из PDF"""
    status_msg = await update.message.reply_text("⏳ Обрабатываю PDF...")
    try:
        file = await context.bot.get_file(doc.file_id)
        file_path = f"/tmp/{doc.file_name}"
        await file.download_to_drive(file_path)
        phones = extract_phones_from_pdf(file_path)
        os.remove(file_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка обработки: {e}")
        return

    if not phones:
        await status_msg.edit_text("📭 Номеров телефонов в PDF не найдено.")
        return

    await _save_phones(update, status_msg, phones, doc.file_name)


async def _process_txt(update, context, doc):
    """Извлечь номера из TXT"""
    status_msg = await update.message.reply_text("⏳ Обрабатываю TXT...")
    try:
        file = await context.bot.get_file(doc.file_id)
        file_path = f"/tmp/{doc.file_name}"
        await file.download_to_drive(file_path)
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        os.remove(file_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка обработки: {e}")
        return

    phones = []
    for line in lines:
        line = line.strip()
        cleaned = _clean_phone(line)
        if cleaned and PHONE_RE.match(cleaned):
            phones.append(cleaned)

    if not phones:
        await status_msg.edit_text("📭 Номеров не найдено в файле.")
        return

    await _save_phones(update, status_msg, phones, doc.file_name)


def _clean_phone(raw: str) -> str:
    """Нормализовать номер телефона"""
    cleaned = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if cleaned.startswith("+7"):
        phone = cleaned[:12]
    elif cleaned.startswith("8") and len(cleaned) == 11:
        phone = "+7" + cleaned[1:]
    elif cleaned.startswith("7") and len(cleaned) == 11:
        phone = "+7" + cleaned[1:]
    else:
        phone = cleaned
    return phone


async def _save_phones(update, status_msg, phones: list, file_name: str):
    """Сохранить номера в базу и уведомить"""
    user = update.message.from_user
    username = user.username or ""
    first_name = user.first_name or ""

    added = 0
    skipped = 0
    for phone in phones:
        rec = PhoneRecord(phone, file_name, username, first_name)
        if db.add_record(rec):
            added += 1
        else:
            skipped += 1

    preview = "\n".join(f"• {p}" for p in phones[:5])
    more = f"\n_...и ещё {len(phones) - 5}_" if len(phones) > 5 else ""

    text = (
        f"✅ *Обработан файл:* `{file_name}`\n\n"
        f"📞 Найдено: {len(phones)}\n"
        f"➕ Добавлено новых: {added}\n"
        f"⏭ Уже в базе: {skipped}\n\n"
        f"{preview}{more}"
    )

    # Уведомить отправителя
    await status_msg.edit_text(text, parse_mode="Markdown")

    # Если это не админ — уведомить админа
    if update.effective_chat.id != ADMIN_CHAT_ID:
        try:
            admin_text = (
                f"📥 *Новый файл от пользователя*\n"
                f"👤 {first_name} (@{username})\n"
                f"📄 `{file_name}`\n"
                f"📞 Найдено: {len(phones)} | Новых: {added}"
            )
            await update.get_bot().send_message(
                ADMIN_CHAT_ID,
                admin_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📊 Открыть меню", callback_data="admin_menu")]]
                ),
            )
        except Exception as e:
            logger.warning(f"Не удалось уведомить админа: {e}")


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    text = update.message.text.strip()
    state = context.user_data.get("state")

    # ── Состояния (FSM) ──────────────────────────────────────────────────────

    if state == "awaiting_broadcast_text":
        if not is_admin(update):
            return
        db.set_broadcast_text(text)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Текст рассылки сохранён:\n\n_{text}_",
            parse_mode="Markdown",
            reply_markup=admin_main_keyboard(),
        )
        return

    if state == "awaiting_manual_phone":
        if not is_admin(update):
            return
        phone = _clean_phone(text)
        if not PHONE_RE.match(phone):
            await update.message.reply_text(
                "❌ Неверный формат. Введите номер в виде +79XXXXXXXXX или 89XXXXXXXXX:",
                reply_markup=back_keyboard("admin_menu"),
            )
            return
        added = db.add_phone_manual(phone, added_by=update.effective_user.username or "admin")
        context.user_data.clear()
        if added:
            await update.message.reply_text(
                f"✅ Номер `{phone}` добавлен в базу.",
                parse_mode="Markdown",
                reply_markup=admin_main_keyboard(),
            )
        else:
            await update.message.reply_text(
                f"⚠️ Номер `{phone}` уже есть в базе.",
                parse_mode="Markdown",
                reply_markup=admin_main_keyboard(),
            )
        return

    if state == "awaiting_delete_phone":
        if not is_admin(update):
            return
        phone = _clean_phone(text)
        deleted = db.delete_phone(phone)
        context.user_data.clear()
        msg = f"✅ Номер `{phone}` удалён." if deleted else f"❌ Номер `{phone}` не найден."
        await update.message.reply_text(
            msg, parse_mode="Markdown", reply_markup=admin_main_keyboard()
        )
        return

    if state == "awaiting_single_phone_send":
        if not is_admin(update):
            return
        # Формат: "+79001234567 Текст сообщения"
        parts = text.split(None, 1)
        if len(parts) < 2:
            await update.message.reply_text(
                "❌ Введите: `+79001234567 Текст сообщения`",
                parse_mode="Markdown",
            )
            return
        phone = _clean_phone(parts[0])
        msg_text = parts[1]
        if not PHONE_RE.match(phone):
            await update.message.reply_text("❌ Неверный формат номера.")
            return
        context.user_data.clear()
        status = WhatsAppSender.get_status()
        if not status.get("connected"):
            await update.message.reply_text(
                "❌ WhatsApp не подключён. Используйте кнопку *Пара WA*.",
                parse_mode="Markdown",
                reply_markup=admin_main_keyboard(),
            )
            return
        result = WhatsAppSender.send_message(phone, msg_text)
        if result.get("success"):
            await update.message.reply_text(
                f"✅ Сообщение отправлено на `{phone}`",
                parse_mode="Markdown",
                reply_markup=admin_main_keyboard(),
            )
        else:
            await update.message.reply_text(
                f"❌ Ошибка: {result.get('error')}",
                reply_markup=admin_main_keyboard(),
            )
        return

    # ── Попытка распознать номер телефона из текста ────────────────────────

    phone = _clean_phone(text)
    if PHONE_RE.match(phone):
        user = update.message.from_user
        rec = PhoneRecord(phone, "direct_message", user.username or "", user.first_name or "")
        added = db.add_record(rec)

        if added:
            reply = f"✅ Номер `{phone}` сохранён!"
        else:
            reply = f"ℹ️ Номер `{phone}` уже есть в базе."

        await update.message.reply_text(reply, parse_mode="Markdown")

        # Уведомить админа
        if update.effective_chat.id != ADMIN_CHAT_ID:
            try:
                await update.get_bot().send_message(
                    ADMIN_CHAT_ID,
                    f"📞 Новый номер от {user.first_name} (@{user.username or '?'}): `{phone}`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return

    # ── Иначе — подсказка ──────────────────────────────────────────────────

    if is_admin(update):
        await update.message.reply_text(
            "Используйте кнопки меню 👇",
            reply_markup=admin_main_keyboard(),
        )
    else:
        await update.message.reply_text(
            "Отправьте PDF-файл или номер телефона (+79XXXXXXXXX).",
            reply_markup=user_main_keyboard(),
        )


# ─────────────────────────── Callback Query ────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # Только админ управляет
    if not is_admin(update) and data not in ("hint_pdf", "about"):
        await query.edit_message_text("⛔ Только для администратора.")
        return

    # ── Главное меню ────────────────────────────────────────────────────────

    if data == "admin_menu":
        context.user_data.clear()
        await query.edit_message_text(
            "👑 *Панель администратора*\n\nВыберите действие:",
            parse_mode="Markdown",
            reply_markup=admin_main_keyboard(),
        )

    # ── Статистика ──────────────────────────────────────────────────────────

    elif data == "stats":
        stats = db.get_stats()
        text = (
            f"📊 *Статистика базы*\n\n"
            f"📞 Всего номеров: {stats['total']}\n"
            f"✅ Отправлено: {stats['sent']}\n"
            f"⏳ Не отправлено: {stats['unsent']}\n"
            f"📝 Текст рассылки: {'✅ задан' if db.broadcast_text else '❌ не задан'}"
        )
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=back_keyboard("admin_menu")
        )

    # ── Текст рассылки ──────────────────────────────────────────────────────

    elif data == "set_text":
        context.user_data["state"] = "awaiting_broadcast_text"
        current = f"\n\nТекущий текст:\n_{db.broadcast_text}_" if db.broadcast_text else ""
        await query.edit_message_text(
            f"✏️ *Установка текста рассылки*{current}\n\n"
            "Напишите новый текст сообщения для рассылки:",
            parse_mode="Markdown",
            reply_markup=back_keyboard("admin_menu"),
        )

    elif data == "show_text":
        if db.broadcast_text:
            text = f"📝 *Текущий текст рассылки:*\n\n{db.broadcast_text}"
        else:
            text = "❌ Текст рассылки не задан.\nНажмите *Текст рассылки* чтобы установить."
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=back_keyboard("admin_menu")
        )

    # ── Рассылка новым (unsent) ─────────────────────────────────────────────

    elif data == "broadcast_unsent":
        phones = db.get_unsent_phones()
        if not phones:
            await query.edit_message_text(
                "✅ Все номера уже получили рассылку!\n\n"
                "Нажмите *Сбросить статусы* в списке номеров, чтобы разослать повторно.",
                parse_mode="Markdown",
                reply_markup=back_keyboard("admin_menu"),
            )
            return
        if not db.broadcast_text:
            await query.edit_message_text(
                "❌ Текст рассылки не задан!\n"
                "Сначала нажмите *Текст рассылки* и установите текст.",
                parse_mode="Markdown",
                reply_markup=back_keyboard("admin_menu"),
            )
            return
        buttons = [
            [
                InlineKeyboardButton(
                    f"✅ Да, разослать {len(phones)} номерам",
                    callback_data="confirm_broadcast_unsent",
                )
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="admin_menu")],
        ]
        preview = db.broadcast_text[:200] + ("..." if len(db.broadcast_text) > 200 else "")
        await query.edit_message_text(
            f"📨 *Рассылка новым номерам*\n\n"
            f"Получателей: *{len(phones)}*\n\n"
            f"Текст:\n_{preview}_\n\n"
            "Подтвердить?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data == "confirm_broadcast_unsent":
        phones = db.get_unsent_phones()
        await _do_broadcast(query, context, phones, mark_sent=True)

    # ── Рассылка всем ────────────────────────────────────────────────────────

    elif data == "broadcast_all":
        phones = db.get_all_phones()
        if not phones:
            await query.edit_message_text(
                "❌ База номеров пуста.",
                reply_markup=back_keyboard("admin_menu"),
            )
            return
        if not db.broadcast_text:
            await query.edit_message_text(
                "❌ Текст рассылки не задан!",
                reply_markup=back_keyboard("admin_menu"),
            )
            return
        buttons = [
            [
                InlineKeyboardButton(
                    f"✅ Да, разослать всем {len(phones)}",
                    callback_data="confirm_broadcast_all",
                )
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="admin_menu")],
        ]
        preview = db.broadcast_text[:200] + ("..." if len(db.broadcast_text) > 200 else "")
        await query.edit_message_text(
            f"📢 *Рассылка ВСЕМ номерам*\n\n"
            f"Получателей: *{len(phones)}*\n\n"
            f"Текст:\n_{preview}_\n\n"
            "Подтвердить?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data == "confirm_broadcast_all":
        phones = db.get_all_phones()
        await _do_broadcast(query, context, phones, mark_sent=True)

    # ── Список номеров ──────────────────────────────────────────────────────

    elif data.startswith("list_phones:"):
        page = int(data.split(":")[1])
        records, total_pages = db.get_records_page(page)

        if not records:
            await query.edit_message_text(
                "📭 База номеров пуста.",
                reply_markup=admin_main_keyboard(),
            )
            return

        lines = [f"📋 *Список номеров* (стр. {page + 1}/{total_pages})\n"]
        for i, r in enumerate(records, start=page * 10 + 1):
            status_icon = "✅" if r.sent else "⏳"
            ts = r.timestamp.strftime("%d.%m %H:%M")
            lines.append(
                f"{i}. {status_icon} `{r.phone}`\n"
                f"   📄 {r.file_name} | 👤 @{r.sender_username or '?'} | 🕐 {ts}"
            )

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=phones_list_keyboard(page, total_pages),
        )

    # ── Добавление номера ────────────────────────────────────────────────────

    elif data == "add_phone":
        context.user_data["state"] = "awaiting_manual_phone"
        await query.edit_message_text(
            "➕ *Добавление номера*\n\n"
            "Введите номер телефона:\n"
            "Форматы: `+79001234567` или `89001234567`",
            parse_mode="Markdown",
            reply_markup=back_keyboard("admin_menu"),
        )

    # ── Удаление номера ──────────────────────────────────────────────────────

    elif data == "delete_phone_prompt":
        context.user_data["state"] = "awaiting_delete_phone"
        await query.edit_message_text(
            "❌ *Удаление номера*\n\n"
            "Введите номер для удаления:",
            parse_mode="Markdown",
            reply_markup=back_keyboard("list_phones:0"),
        )

    # ── Сброс статусов ───────────────────────────────────────────────────────

    elif data == "reset_sent":
        all_phones = db.get_all_phones()
        db.mark_unsent(all_phones)
        await query.edit_message_text(
            f"🔄 Статусы сброшены для {len(all_phones)} номеров.\n"
            "Теперь все они считаются не отправленными.",
            reply_markup=back_keyboard("list_phones:0"),
        )

    # ── Очистка базы ─────────────────────────────────────────────────────────

    elif data == "clear_confirm":
        stats = db.get_stats()
        buttons = [
            [
                InlineKeyboardButton(
                    f"🗑 Да, удалить все {stats['total']} номеров",
                    callback_data="clear_execute",
                )
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="admin_menu")],
        ]
        await query.edit_message_text(
            f"⚠️ *Очистка базы*\n\nБудет удалено {stats['total']} номеров.\nЭто необратимо!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data == "clear_execute":
        db.clear_all()
        await query.edit_message_text(
            "✅ База очищена.",
            reply_markup=admin_main_keyboard(),
        )

    # ── WhatsApp ─────────────────────────────────────────────────────────────

    elif data == "wa_status":
        status = WhatsAppSender.get_status()
        if status.get("connected"):
            text = f"✅ *WhatsApp подключён*\nСтатус: `{status.get('status')}`"
        else:
            text = (
                f"❌ *WhatsApp НЕ подключён*\n"
                f"Статус: `{status.get('status', 'unknown')}`\n\n"
                "Нажмите *Пара WA* для подключения."
            )
        buttons = [
            [InlineKeyboardButton("🔄 Обновить", callback_data="wa_status")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_menu")],
        ]
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data == "wa_pair":
        await query.edit_message_text("🔄 Запрашиваю код пары...")
        result = WhatsAppSender.pair()
        if result.get("success"):
            code = result.get("code")
            text = (
                f"📱 *Код пары WhatsApp:*\n\n"
                f"`{code}`\n\n"
                "Введите этот код в WhatsApp:\n"
                "Настройки → Связанные устройства → Привязать устройство → Введите код"
            )
        else:
            text = f"❌ Ошибка: {result.get('error')}"
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=back_keyboard("admin_menu")
        )

    elif data == "wa_logout":
        buttons = [
            [InlineKeyboardButton("✅ Да, выйти", callback_data="wa_logout_confirm")],
            [InlineKeyboardButton("❌ Отмена", callback_data="admin_menu")],
        ]
        await query.edit_message_text(
            "⚠️ Выйти из WhatsApp?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data == "wa_logout_confirm":
        result = WhatsAppSender.logout()
        if result.get("success"):
            text = "👋 Выполнен выход из WhatsApp."
        else:
            text = f"❌ Ошибка: {result.get('error')}"
        await query.edit_message_text(text, reply_markup=back_keyboard("admin_menu"))

    # ── Отправка одному номеру ────────────────────────────────────────────────

    elif data == "send_single":
        context.user_data["state"] = "awaiting_single_phone_send"
        await query.edit_message_text(
            "📤 *Отправка одному номеру*\n\n"
            "Напишите в формате:\n`+79001234567 Текст сообщения`",
            parse_mode="Markdown",
            reply_markup=back_keyboard("admin_menu"),
        )

    # ── Для обычных пользователей ─────────────────────────────────────────────

    elif data == "hint_pdf":
        await query.edit_message_text(
            "📄 Просто перетащите PDF-файл в чат!\n"
            "Бот автоматически извлечёт из него номер телефона.",
            reply_markup=user_main_keyboard(),
        )

    elif data == "about":
        await query.edit_message_text(
            "ℹ️ *О боте*\n\n"
            "Бот собирает номера телефонов из PDF-файлов и текстовых сообщений.\n"
            "Отправьте PDF — и номер будет сохранён.",
            parse_mode="Markdown",
            reply_markup=user_main_keyboard(),
        )

    elif data == "noop":
        pass  # Кнопка текущей страницы, ничего не делаем


# ─────────────────────────── Внутренняя рассылка ──────────────────────────────

async def _do_broadcast(query, context, phones: list, mark_sent: bool = True):
    """Выполнить рассылку по списку номеров"""
    if not phones:
        await query.edit_message_text(
            "❌ Нет номеров для рассылки.",
            reply_markup=back_keyboard("admin_menu"),
        )
        return

    status = WhatsAppSender.get_status()
    if not status.get("connected"):
        await query.edit_message_text(
            "❌ WhatsApp не подключён!\n"
            "Нажмите *Пара WA* для подключения.",
            parse_mode="Markdown",
            reply_markup=back_keyboard("admin_menu"),
        )
        return

    await query.edit_message_text(
        f"⏳ Начинаю рассылку на {len(phones)} номеров...\n"
        "Это может занять некоторое время."
    )

    result = WhatsAppSender.send_bulk(phones, db.broadcast_text)

    if result.get("success"):
        sent = result.get("sent", 0)
        total = result.get("total", len(phones))
        errors = result.get("errors", [])

        if mark_sent:
            # Пометить успешно отправленные
            sent_phones = [p for p in phones if p not in {e["phone"] for e in errors}]
            db.mark_sent(sent_phones)

        error_text = ""
        if errors:
            error_lines = [f"• `{e['phone']}`: {e['error']}" for e in errors[:5]]
            error_text = f"\n\n❌ Ошибки ({len(errors)}):\n" + "\n".join(error_lines)
            if len(errors) > 5:
                error_text += f"\n_...и ещё {len(errors) - 5} ошибок_"

        await query.edit_message_text(
            f"✅ *Рассылка завершена*\n\n"
            f"📤 Отправлено: {sent}/{total}{error_text}",
            parse_mode="Markdown",
            reply_markup=admin_main_keyboard(),
        )
    else:
        await query.edit_message_text(
            f"❌ Ошибка рассылки: {result.get('error')}",
            reply_markup=back_keyboard("admin_menu"),
        )


# ─────────────────────────── Запуск ────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))

    # Файлы (от любого пользователя)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Текстовые сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Кнопки
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("Бот запущен")
    app.run_polling(
    drop_pending_updates=True,
    allowed_updates=Update.ALL_TYPES,
    close_loop=False,
)


if __name__ == "__main__":
    main()
