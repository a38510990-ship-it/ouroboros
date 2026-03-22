"""
bot.py — Telegram Bot for Bookshelf Catalog

Commands:
    /start   — welcome message and instructions
    /sheet   — link to the Google Sheet
    /help    — same as /start

Usage:
    python bookshelf/bot.py

Requirements:
    - .env file with TELEGRAM_BOT_TOKEN and OPENROUTER_API_KEY
    - credentials.json for Google OAuth
    - Run oauth_setup.py first to create token.json
"""

import asyncio
import logging
import os
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

from vision import recognize_books
from books_api import enrich_books
from sheets import BookshelfSheet

# Load .env from the same directory as bot.py
load_dotenv(Path(__file__).parent / ".env")

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")

# Paths relative to bot.py location
BOT_DIR = Path(__file__).parent
TOKEN_PATH = str(BOT_DIR / "token.json")
CREDENTIALS_PATH = str(BOT_DIR / "credentials.json")

# Global sheets client (initialized once)
_sheet: BookshelfSheet | None = None


def get_sheet() -> BookshelfSheet:
    """Get or create the global BookshelfSheet instance."""
    global _sheet
    if _sheet is None:
        _sheet = BookshelfSheet(
            sheet_name=GOOGLE_SHEET_NAME,
            token_path=TOKEN_PATH,
            credentials_path=CREDENTIALS_PATH,
        )
    return _sheet


# ─── Handlers ────────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start and /help commands."""
    await update.message.reply_text(
        "📚 *Bookshelf Catalog Bot*\n\n"
        "Я помогаю каталогизировать книги с фотографий книжных полок.\n\n"
        "*Как пользоваться:*\n"
        "1. Сфотографируй книжную полку (корешки книг должны быть видны)\n"
        "2. Отправь фото мне\n"
        "3. Я распознаю все книги и добавлю их в Google Sheets\n\n"
        "*Команды:*\n"
        "/sheet — открыть таблицу с каталогом\n"
        "/help — эта справка\n\n"
        "_Дубликаты автоматически пропускаются — можно отправлять фото одной полки несколько раз._",
        parse_mode="Markdown",
    )


async def sheet_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sheet command — send link to Google Sheet."""
    processing_msg = await update.message.reply_text("🔗 Получаю ссылку на таблицу...")

    try:
        sheet = get_sheet()
        url = await asyncio.get_event_loop().run_in_executor(None, sheet.get_url)
        await processing_msg.edit_text(
            f"📊 [Открыть каталог книг]({url})",
            parse_mode="Markdown",
        )
    except FileNotFoundError:
        await processing_msg.edit_text(
            "❌ *Google Sheets не настроен*\n\n"
            "Запусти авторизацию:\n"
            "`python bookshelf/oauth_setup.py`",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Error getting sheet URL: {e}")
        await processing_msg.edit_text(
            f"❌ Не удалось получить ссылку на таблицу: {e}"
        )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming photos — the main bot flow."""
    processing_msg = await update.message.reply_text("📷 Получил фото, обрабатываю...")

    try:
        # Step 1: Download the photo (highest resolution)
        photo = update.message.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)
        image_data = bytes(await photo_file.download_as_bytearray())
        logger.info(f"Downloaded photo: {len(image_data)} bytes")

        await processing_msg.edit_text("🔍 Распознаю книги на фото...")

        # Step 2: VLM recognition
        raw_books = await recognize_books(image_data)

        if not raw_books:
            await processing_msg.edit_text(
                "😕 Не удалось распознать книги на фото.\n\n"
                "Советы:\n"
                "• Убедись, что корешки книг хорошо видны\n"
                "• Попробуй сделать фото с лучшим освещением\n"
                "• Книги не должны быть перекрыты другими предметами"
            )
            return

        await processing_msg.edit_text(
            f"📚 Нашёл {len(raw_books)} книг(и), ищу информацию..."
        )

        # Step 3: Enrich via Google Books API
        enriched_books = await enrich_books(raw_books)

        await processing_msg.edit_text("💾 Добавляю в каталог...")

        # Step 4: Save to Google Sheets
        sheet = get_sheet()
        added, skipped = await asyncio.get_event_loop().run_in_executor(
            None, sheet.add_books, enriched_books
        )

        # Step 5: Send summary
        response = _format_result(added, skipped)
        await processing_msg.edit_text(response, parse_mode="Markdown")

    except EnvironmentError as e:
        # Missing API key etc.
        logger.error(f"Environment error: {e}")
        await processing_msg.edit_text(
            f"⚙️ *Ошибка настройки:*\n`{e}`",
            parse_mode="Markdown",
        )
    except FileNotFoundError as e:
        # Missing credentials.json or token.json
        logger.error(f"File not found: {e}")
        await processing_msg.edit_text(
            "❌ *Google Sheets не настроен*\n\n"
            "Запусти авторизацию:\n"
            "`python bookshelf/oauth_setup.py`",
            parse_mode="Markdown",
        )
    except RuntimeError as e:
        # VLM API errors
        logger.error(f"Runtime error: {e}")
        await processing_msg.edit_text(
            f"❌ Ошибка при распознавании: {e}"
        )
    except Exception as e:
        logger.exception(f"Unexpected error processing photo: {e}")
        await processing_msg.edit_text(
            f"❌ Неожиданная ошибка: {e}\n\nПопробуй ещё раз или обратись к разработчику."
        )


def _format_result(added: list[dict], skipped: list[dict]) -> str:
    """Format the bot's response after processing a photo."""
    total = len(added) + len(skipped)

    if not added and not skipped:
        return "😕 Не удалось распознать ни одной книги."

    lines = []

    if added:
        lines.append(f"✅ *Добавлено {len(added)} книг(и):*")
        for book in added[:10]:  # Limit to 10 to avoid too-long messages
            title = book.get("Title", "Без названия")
            author = book.get("Author", "")
            if author:
                lines.append(f"  • _{title}_ — {author}")
            else:
                lines.append(f"  • _{title}_")
        if len(added) > 10:
            lines.append(f"  _...и ещё {len(added) - 10} книг_")

    if skipped:
        lines.append(f"\n⏭ *Пропущено (уже в каталоге): {len(skipped)}*")
        for book in skipped[:5]:
            lines.append(f"  • _{book.get('Title', '?')}_")
        if len(skipped) > 5:
            lines.append(f"  _...и ещё {len(skipped) - 5}_")

    return "\n".join(lines)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN:
        raise EnvironmentError(
            "TELEGRAM_BOT_TOKEN not set! "
            "Copy .env.example to .env and fill in your token."
        )

    # Verify credentials exist before starting
    if not (BOT_DIR / "credentials.json").exists():
        logger.warning(
            "credentials.json not found! "
            "Google Sheets integration will not work until you run:\n"
            "  python bookshelf/oauth_setup.py"
        )

    logger.info(f"Starting Bookshelf Catalog Bot...")
    logger.info(f"Sheet name: {GOOGLE_SHEET_NAME}")

    # Build the application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler(["start", "help"], start_handler))
    app.add_handler(CommandHandler("sheet", sheet_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    # Start polling
    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
