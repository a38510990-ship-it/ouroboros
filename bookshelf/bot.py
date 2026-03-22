"""
Bookshelf Catalog Telegram Bot
================================
Send a photo of your bookshelf — the bot recognizes all books and adds them
to your personal Google Sheets catalog.

Commands:
    /start   — welcome message and instructions
    /sheet   — show link to your Google Sheet catalog
    /help    — show help

Usage:
    1. Run oauth_setup.py once to set up Google OAuth
    2. Copy .env.example to .env and fill in your credentials
    3. Run: python bot.py
"""
import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from vision import recognize_books
from books_api import enrich_books
from sheets import append_books, get_sheet_url

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Config from environment
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")
GOOGLE_TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "token.json")
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemini-2.0-flash-001")

# ─────────────────────────────────────────────────────────────────────────────
# Message text
# ─────────────────────────────────────────────────────────────────────────────

WELCOME_MESSAGE = (
    "📚 *Bookshelf Catalog Bot*\n\n"
    "Send me a photo of your bookshelf and I'll:\n"
    "1\\. Recognize all visible book titles\n"
    "2\\. Look up metadata \\(author, year, ISBN\\)\n"
    "3\\. Add new books to your Google Sheets catalog \\(duplicates skipped\\!\\)\n\n"
    "*Tips for best results:*\n"
    "• Make sure book spines are visible and readable\n"
    "• Good lighting helps a lot\n"
    "• Works with both vertical and horizontal books\n\n"
    "Use /sheet to see your current catalog link\\."
)

HELP_MESSAGE = (
    "*How to use:*\n\n"
    "📸 *Send a photo* — I'll recognize all books on the shelf and add new ones\n\n"
    "*/sheet* — Get the link to your Google Sheets catalog\n"
    "*/start* — Show welcome message\n"
    "*/help* — Show this help\n\n"
    "*Your catalog* grows with each new photo\\. "
    "Duplicates are automatically skipped — feel free to re\\-scan the same shelf later\\!"
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    sheet_url = get_sheet_url(GOOGLE_SHEET_NAME, GOOGLE_TOKEN_PATH)

    keyboard = []
    if sheet_url:
        keyboard.append([InlineKeyboardButton("📊 Open Catalog", url=sheet_url)])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(
        WELCOME_MESSAGE,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.message.reply_text(HELP_MESSAGE, parse_mode="MarkdownV2")


async def sheet_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sheet and /catalog commands — show catalog link."""
    sheet_url = get_sheet_url(GOOGLE_SHEET_NAME, GOOGLE_TOKEN_PATH)

    if sheet_url:
        keyboard = [[InlineKeyboardButton("📊 Open Catalog", url=sheet_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"📚 Your catalog:\n{sheet_url}",
            reply_markup=reply_markup,
        )
    else:
        await update.message.reply_text(
            "📭 No catalog yet — send me a photo of your bookshelf to start!\n\n"
            "If you haven't set up Google Sheets yet, run:\n"
            "`python oauth_setup.py`",
            parse_mode="Markdown",
        )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle photo messages.
    Downloads the photo → VLM recognition → Google Books enrichment
    → append to Google Sheets (with deduplication) → reply with results.
    """
    msg = update.message
    status_msg = await msg.reply_text("📸 Got the photo! Analyzing your bookshelf...")

    tmp_path = None
    try:
        # Download photo (highest resolution available)
        photo = msg.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name

        await photo_file.download_to_drive(tmp_path)
        size_kb = Path(tmp_path).stat().st_size // 1024
        logger.info(f"Downloaded photo: {tmp_path} ({size_kb} KB)")

        # Step 1: VLM recognition
        await status_msg.edit_text("🔍 Recognizing books with AI vision...")

        books_raw = recognize_books(
            image_path=tmp_path,
            api_key=OPENROUTER_API_KEY,
            model=VLM_MODEL,
        )

        if not books_raw:
            await status_msg.edit_text(
                "😕 Couldn't recognize any books in this photo.\n\n"
                "Tips:\n"
                "• Make sure book spines are clearly visible\n"
                "• Try better lighting\n"
                "• Point the camera directly at the spines"
            )
            return

        await status_msg.edit_text(
            f"📖 Found {len(books_raw)} book(s)! Looking up details..."
        )

        # Step 2: Enrich with Google Books API
        books_enriched = enrich_books(books_raw)

        # Step 3: Append to Google Sheets with deduplication
        await status_msg.edit_text("📊 Saving to your Google Sheets catalog...")

        sheet_url, added_count, skipped_count = append_books(
            books=books_enriched,
            sheet_name=GOOGLE_SHEET_NAME,
            token_path=GOOGLE_TOKEN_PATH,
        )

        # Step 4: Reply with results
        open_btn = InlineKeyboardButton("📊 Open Catalog", url=sheet_url)

        if added_count == 0 and skipped_count > 0:
            await status_msg.edit_text(
                f"🔄 All {skipped_count} book(s) are already in your catalog!\n"
                "Nothing new to add — your catalog is up to date.",
                reply_markup=InlineKeyboardMarkup([[open_btn]]),
            )
            return

        book_list = _format_book_list(books_enriched)
        result_text = f"✅ Added *{added_count}* new book(s) to your catalog!"
        if skipped_count > 0:
            result_text += f"\n⏭ Skipped *{skipped_count}* duplicate(s)"
        result_text += f"\n\n{book_list}"

        await status_msg.edit_text(
            result_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[open_btn]]),
        )

    except FileNotFoundError as e:
        logger.error(f"File error: {e}")
        await status_msg.edit_text(
            "❌ Google credentials not found.\n\n"
            "Please run `python oauth_setup.py` to set up Google authentication."
        )
    except RuntimeError as e:
        logger.error(f"Runtime error: {e}")
        await status_msg.edit_text(f"❌ Error processing image:\n{str(e)[:300]}")
    except Exception as e:
        logger.exception(f"Unexpected error in photo_handler: {e}")
        await status_msg.edit_text(
            f"❌ Something went wrong. Please try again.\nError: {str(e)[:150]}"
        )
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def _format_book_list(books: list) -> str:
    """Format the list of books for a Telegram Markdown message."""
    lines = []
    for i, book in enumerate(books[:10], 1):
        title = book.get("title_confirmed") or book.get("title", "Unknown")
        author = book.get("authors_confirmed") or book.get("author", "")
        year = book.get("year", "")

        line = f"*{i}.* {title}"
        if author and author.lower() != "unknown":
            line += f" — _{author}_"
        if year:
            line += f" ({year})"
        lines.append(line)

    if len(books) > 10:
        lines.append(f"_...and {len(books) - 10} more in the catalog_")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """Start the Telegram bot."""
    logger.info("Starting Bookshelf Catalog Bot...")
    logger.info(f"Google Sheet: '{GOOGLE_SHEET_NAME}'")
    logger.info(f"VLM model: {VLM_MODEL}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("sheet", sheet_command))
    app.add_handler(CommandHandler("catalog", sheet_command))  # alias
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
