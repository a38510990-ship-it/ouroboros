"""
bot.py — Bookshelf Catalog Telegram Bot

Команды:
    /start — Приветствие
    /sheet — Ссылка на Google Sheets каталог

Фото:
    Пользователь шлёт фото полки →
    VLM распознаёт книги →
    Google Books обогащает метаданные →
    Данные добавляются в Google Sheets

Запуск (в Colab):
    import os
    os.environ["TELEGRAM_BOT_TOKEN"] = "..."
    os.environ["OPENROUTER_API_KEY"] = "..."
    exec(open("bookshelf/bot.py").read())
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from books_api import enrich_books
from sheets import BookshelfSheet
from vision import recognize_books

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

# ---------------------------------------------------------------------------
# Logging — stdout + rotating file (bot.log, max 2 MB × 3 backups)
# ---------------------------------------------------------------------------
_log_file = HERE / "bot.log"

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_file_handler = RotatingFileHandler(
    _log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_fmt)

logging.basicConfig(
    level=logging.INFO,
    handlers=[_console_handler, _file_handler],
)
logger = logging.getLogger(__name__)
logger.info("Logging initialised. Log file: %s", _log_file)
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

_sheet = BookshelfSheet()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📚 <b>Bookshelf Catalog Bot</b>\n\n"
        "Я каталогизирую книги с фото твоей книжной полки.\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Сфотографируй книжную полку (корешки должны быть видны)\n"
        "2. Отправь фото мне\n"
        "3. Я распознаю книги и добавлю их в таблицу Google Sheets\n\n"
        "<b>Команды:</b>\n"
        "/start — Это сообщение\n"
        "/sheet — Ссылка на таблицу\n\n"
        "📷 Отправь первое фото!"
    )
    await update.message.reply_html(text)


async def cmd_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔍 Ищу таблицу...")
    try:
        url = _sheet.get_url()
        await update.message.reply_html(f"📊 <b>Твой каталог:</b>\n{url}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка доступа к таблице: {e}")
        logger.error("Sheet access error: %s", e, exc_info=True)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    logger.info("Photo received from user %s", user_id)

    processing_msg = await update.message.reply_text("📷 Фото получено! Анализирую полку...")

    # Скачать фото (максимальное разрешение)
    photo = update.message.photo[-1]
    photo_file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await photo_file.download_as_bytearray())
    logger.info("Downloaded photo: %d bytes (user=%s)", len(image_bytes), user_id)

    # Распознать книги через VLM
    await processing_msg.edit_text("🔍 Распознаю книги с помощью AI...")
    try:
        raw_books = await recognize_books(image_bytes)
    except RuntimeError as e:
        logger.error("VLM recognition failed (user=%s): %s", user_id, e, exc_info=True)
        await processing_msg.edit_text(
            f"❌ <b>Не удалось распознать книги:</b>\n{e}\n\n"
            "Проверь, что OPENROUTER_API_KEY задан.",
            parse_mode="HTML",
        )
        return

    if not raw_books:
        logger.info("No books detected in photo (user=%s)", user_id)
        await processing_msg.edit_text(
            "😕 <b>Книги не найдены.</b>\n\n"
            "Советы:\n"
            "• Корешки должны быть чётко видны\n"
            "• Хорошее освещение помогает\n"
            "• Попробуй сфотографировать ближе",
            parse_mode="HTML",
        )
        return

    logger.info("Recognized %d book(s) (user=%s)", len(raw_books), user_id)

    # Обогатить через Google Books API
    await processing_msg.edit_text(f"📖 Нашёл {len(raw_books)} книг(и)! Ищу детали...")
    enriched_books = await enrich_books(raw_books)
    logger.info(
        "Enriched %d/%d book(s) via Google Books (user=%s)",
        sum(1 for b in enriched_books if b.get("ISBN")),
        len(enriched_books),
        user_id,
    )

    # Добавить в Google Sheets
    await processing_msg.edit_text("📊 Добавляю в каталог...")
    try:
        added, skipped = _sheet.add_books(enriched_books)
    except Exception as e:
        logger.error("Sheets write error (user=%s): %s", user_id, e, exc_info=True)
        await processing_msg.edit_text(f"❌ Не удалось сохранить в таблицу: {e}")
        return

    logger.info(
        "Catalog updated: added=%d skipped=%d (user=%s)", len(added), len(skipped), user_id
    )
    await processing_msg.edit_text(_format_result(added, skipped), parse_mode="HTML")


def _format_result(added: list[dict], skipped: list[dict]) -> str:
    if not added and not skipped:
        return "😕 Книги не обработаны."

    lines = []
    if added:
        lines.append(f"✅ <b>Добавлено {len(added)} книг(и):</b>")
        for book in added:
            title = book.get("Title", "?")
            author = book.get("Author", "")
            year = book.get("Year", "")
            entry = f"• <b>{title}</b>"
            if author:
                entry += f" — {author}"
            if year:
                entry += f" ({year})"
            lines.append(entry)

    if skipped:
        lines.append(f"\n⏭️ <b>Пропущено дублей: {len(skipped)}</b>")
        for book in skipped:
            lines.append(f"• {book.get('Title', '?')}")

    lines.append("\n📊 /sheet — открыть каталог")
    return "\n".join(lines)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан.")
        return

    logger.info("Starting Bookshelf Catalog Bot...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("sheet", cmd_sheet))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
