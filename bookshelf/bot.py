"""
bot.py — Telegram bot for bookshelf cataloging

Commands:
  /start  — welcome message and instructions
  /sheet  — show link to Google Sheets catalog

Photo handler:
  1. Download photo from Telegram
  2. Send to VLM (OpenRouter) for book recognition
  3. Enrich via Google Books API
  4. Add to Google Sheets (with deduplication)
  5. Reply with results
"""

import asyncio
import logging
import os
from io import BytesIO
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

# Load .env from the bookshelf/ directory
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
if not TELEGRAM_BOT_TOKEN:
    raise EnvironmentError("TELEGRAM_BOT_TOKEN not set in .env")

SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")
TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", str(Path(__file__).parent / "token.json"))
CREDS_PATH = str(Path(__file__).parent / "credentials.json")

# Shared sheet client (initialized on first use)
sheet = BookshelfSheet(
    sheet_name=SHEET_NAME,
    token_path=TOKEN_PATH,
    credentials_path=CREDS_PATH,
)

# ── Handlers ──────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message with usage instructions."""
    text = (
        "📚 *Bookshelf Catalog Bot*\n\n"
        "Отправь мне фото книжной полки — я распознаю все книги "
        "и добавлю их в твою таблицу Google Sheets\\.\n\n"
        "*Как использовать:*\n"
        "1\\. Сфотографируй полку так, чтобы корешки были видны\n"
        "2\\. Отправь фото в этот чат\n"
        "3\\. Подожди — я обработаю фото и добавлю книги\n\n"
        "*Команды:*\n"
        "/sheet — ссылка на таблицу с каталогом\n"
        "/start — это сообщение\n\n"
        "_Если книга уже есть в таблице — она не дублируется\\._"
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2")


async def cmd_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send link to the Google Sheets catalog."""
    await update.message.reply_text("⏳ Получаю ссылку на таблицу...")
    try:
        url = sheet.get_url()
        await update.message.reply_text(
            f"📊 Твой каталог книг:\n{url}",
        )
    except FileNotFoundError as e:
        await update.message.reply_text(
            f"❌ Google Sheets не настроен:\n\n{e}"
        )
    except Exception as e:
        logger.exception("Error getting sheet URL")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Main handler: photo → VLM → Google Books → Sheets → reply.
    """
    # Ack immediately
    processing_msg = await update.message.reply_text(
        "📸 Получил фото, обрабатываю... Это может занять 10-30 секунд."
    )

    try:
        # 1. Download the highest-resolution photo
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = BytesIO()
        await file.download_to_memory(image_bytes)
        image_bytes.seek(0)
        image_data = image_bytes.read()

        logger.info(f"Photo downloaded: {len(image_data)} bytes")

        # 2. VLM recognition
        await processing_msg.edit_text("🔍 Распознаю книги на фото...")
        raw_books = await recognize_books(image_data)

        if not raw_books:
            await processing_msg.edit_text(
                "😕 Не удалось распознать книги на этом фото.\n\n"
                "Попробуй:\n"
                "• Сфотографировать ближе\n"
                "• Убедиться, что корешки видны и читаемы\n"
                "• Использовать хорошее освещение"
            )
            return

        logger.info(f"VLM recognized {len(raw_books)} books")

        # 3. Enrich via Google Books API
        await processing_msg.edit_text(
            f"📖 Нашёл {len(raw_books)} книг(и), обогащаю данными..."
        )
        enriched_books = await enrich_books(raw_books)

        # 4. Add to Google Sheets (with deduplication)
        await processing_msg.edit_text("💾 Сохраняю в Google Sheets...")
        added, skipped = sheet.add_books(enriched_books)

        # 5. Reply with results
        response = _format_result(added, skipped)
        await processing_msg.edit_text(response)

    except FileNotFoundError as e:
        logger.error(f"OAuth not set up: {e}")
        await processing_msg.edit_text(
            "❌ Google Sheets не настроен.\n\n"
            "Запусти:\n`python bookshelf/oauth_setup.py`\n\n"
            f"Детали: {e}"
        )
    except Exception as e:
        logger.exception("Error processing photo")
        await processing_msg.edit_text(
            f"❌ Произошла ошибка при обработке фото:\n{e}\n\n"
            "Попробуй ещё раз или проверь логи."
        )


def _format_result(added: list[dict], skipped: list[dict]) -> str:
    """Format the result message (plain text, no MarkdownV2 complications)."""
    lines = []

    if added:
        lines.append(f"✅ Добавлено книг: {len(added)}")
        for book in added:
            title = book.get("Title", "Unknown")
            author = book.get("Author", "")
            year = book.get("Year", "")
            if author and year:
                lines.append(f"  📚 {title} — {author} ({year})")
            elif author:
                lines.append(f"  📚 {title} — {author}")
            else:
                lines.append(f"  📚 {title}")
    else:
        lines.append("ℹ️ Новых книг не добавлено")

    if skipped:
        lines.append(f"\n⏭️ Уже в каталоге: {len(skipped)}")
        for book in skipped:
            title = book.get("Title", "Unknown")
            lines.append(f"  • {title}")

    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Start the bot."""
    logger.info("Starting Bookshelf Catalog Bot...")

    # Check Google Sheets connectivity
    try:
        url = sheet.get_url()
        logger.info(f"Google Sheets connected: {url}")
    except FileNotFoundError:
        logger.warning(
            "Google Sheets not configured yet. "
            "Run: python bookshelf/oauth_setup.py"
        )
    except Exception as e:
        logger.warning(f"Google Sheets connection check failed: {e}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("sheet", cmd_sheet))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
