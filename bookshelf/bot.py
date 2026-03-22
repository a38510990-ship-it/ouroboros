#!/usr/bin/env python3
"""
bot.py — Bookshelf Catalog Telegram Bot

Sends photos of bookshelves → recognizes books via VLM → enriches with
Google Books API → saves to Google Sheets.

Usage:
    python bookshelf/bot.py

Environment variables (bookshelf/.env):
    TELEGRAM_BOT_TOKEN   — from @BotFather
    OPENROUTER_API_KEY   — from openrouter.ai/keys
    GOOGLE_SHEET_NAME    — spreadsheet name (default: "Bookshelf Catalog")

Before first run:
    python bookshelf/oauth_setup.py  ← authorize Google Sheets access

Commands:
    /start   — welcome message and instructions
    /sheet   — show link to the catalog spreadsheet
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Load .env from bookshelf/ directory
HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# ── Config ─────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")


# ── Command: /start ────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message."""
    await update.message.reply_text(
        "📚 <b>Bookshelf Catalog Bot</b>\n\n"
        "Send me a photo of your bookshelf and I'll:\n"
        "1. Recognize all the books visible on the spines\n"
        "2. Look them up in Google Books for full details\n"
        "3. Add them to your Google Sheets catalog\n\n"
        "<b>Commands:</b>\n"
        "/sheet — view your catalog spreadsheet\n\n"
        "Just send a photo to get started! 📸",
        parse_mode=ParseMode.HTML,
    )


# ── Command: /sheet ────────────────────────────────────────────────────────────
async def cmd_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the link to the spreadsheet."""
    from sheets import BookshelfSheet

    await update.message.reply_text("🔍 Looking up your spreadsheet...")

    try:
        sheet = BookshelfSheet(sheet_name=GOOGLE_SHEET_NAME)
        url = sheet.get_url()
        await update.message.reply_text(
            f"📊 <b>Your catalog:</b>\n{url}",
            parse_mode=ParseMode.HTML,
        )
    except FileNotFoundError:
        await update.message.reply_text(
            "❌ <b>Google Sheets not set up yet</b>\n\n"
            "Run this command first:\n"
            "<code>python bookshelf/oauth_setup.py</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error(f"Error in /sheet: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Error accessing spreadsheet: {e}"
        )


# ── Photo handler ──────────────────────────────────────────────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Process a bookshelf photo:
    1. Download the image
    2. Recognize books with VLM
    3. Enrich with Google Books API
    4. Add to Google Sheets (with dedup)
    5. Reply with results
    """
    from books_api import enrich_books
    from sheets import BookshelfSheet
    from vision import recognize_books

    # Tell user we're working on it
    processing_msg = await update.message.reply_text("🔍 Analyzing your bookshelf...")

    try:
        # ── Download photo ─────────────────────────────────────────
        # Telegram sends multiple sizes; use the largest one
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()
        image_bytes = bytes(image_bytes)
        logger.info(f"Downloaded photo: {len(image_bytes)} bytes")

        # ── Recognize books with VLM ───────────────────────────────
        await processing_msg.edit_text("🤖 Recognizing books...")

        raw_books = await recognize_books(image_bytes)
        logger.info(f"VLM recognized {len(raw_books)} books")

        if not raw_books:
            await processing_msg.edit_text(
                "😕 I couldn't recognize any books in this photo.\n\n"
                "Tips for better results:\n"
                "• Make sure book spines are clearly visible\n"
                "• Good lighting helps a lot\n"
                "• Avoid blurry photos"
            )
            return

        # ── Enrich with Google Books API ───────────────────────────
        await processing_msg.edit_text(
            f"📖 Found {len(raw_books)} book(s). Looking up details..."
        )

        enriched_books = await enrich_books(raw_books)
        logger.info(f"Enriched {len(enriched_books)} books")

        # ── Add to Google Sheets ───────────────────────────────────
        await processing_msg.edit_text("📊 Saving to your catalog...")

        try:
            sheet = BookshelfSheet(sheet_name=GOOGLE_SHEET_NAME)
            added, skipped = sheet.add_books(enriched_books)
            sheet_url = sheet.get_url()
        except FileNotFoundError:
            await processing_msg.edit_text(
                "❌ <b>Google Sheets not authorized</b>\n\n"
                "Run this once to set it up:\n"
                "<code>python bookshelf/oauth_setup.py</code>\n\n"
                f"I recognized these books:\n{_format_book_list(enriched_books)}",
                parse_mode=ParseMode.HTML,
            )
            return

        # ── Build response ─────────────────────────────────────────
        response_lines = []

        if added:
            response_lines.append(f"✅ <b>Added {len(added)} book(s):</b>")
            for book in added:
                response_lines.append(_format_book_line(book))

        if skipped:
            response_lines.append(f"\n⏭️ <b>Skipped {len(skipped)} duplicate(s):</b>")
            for book in skipped:
                title = book.get("Title", "?")
                response_lines.append(f"  • {title} (already in catalog)")

        response_lines.append(f"\n📊 <a href='{sheet_url}'>Open catalog</a>")

        await processing_msg.edit_text(
            "\n".join(response_lines),
            parse_mode=ParseMode.HTML,
        )

        logger.info(f"Done: added={len(added)}, skipped={len(skipped)}")

    except RuntimeError as e:
        # Known errors (API key missing, VLM timeout, etc.)
        logger.error(f"RuntimeError processing photo: {e}")
        await processing_msg.edit_text(f"❌ {e}")

    except Exception as e:
        logger.error(f"Unexpected error processing photo: {e}", exc_info=True)
        await processing_msg.edit_text(
            "❌ Something went wrong. Please try again.\n"
            f"Error: {type(e).__name__}: {e}"
        )


# ── Formatting helpers ─────────────────────────────────────────────────────────

def _format_book_line(book: dict) -> str:
    """Format a single book as a text line."""
    title = book.get("Title", "?")
    author = book.get("Author", "")
    year = book.get("Year", "")

    parts = [f"  • <b>{title}</b>"]
    if author:
        parts.append(f" by {author}")
    if year:
        parts.append(f" ({year})")

    return "".join(parts)


def _format_book_list(books: list[dict]) -> str:
    """Format a list of books."""
    if not books:
        return "(none)"
    return "\n".join(_format_book_line(b) for b in books)


# ── Error handler ──────────────────────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log unexpected errors."""
    logger.error(f"Unhandled error: {context.error}", exc_info=context.error)


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN is not set")
        print("  Create bookshelf/.env and add: TELEGRAM_BOT_TOKEN=your_token_here")
        return

    if not os.getenv("OPENROUTER_API_KEY"):
        print("❌ OPENROUTER_API_KEY is not set")
        print("  Add to bookshelf/.env: OPENROUTER_API_KEY=your_key_here")
        return

    # Check for token.json
    token_path = HERE / "token.json"
    if not token_path.exists():
        print("⚠️  token.json not found — Google Sheets access not authorized")
        print("  Run: python bookshelf/oauth_setup.py")
        print("  (The bot will start anyway, but /sheet and photo processing will fail)")
        print()

    logger.info(f"Starting bot (sheet: '{GOOGLE_SHEET_NAME}')")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("sheet", cmd_sheet))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_error_handler(error_handler)

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
