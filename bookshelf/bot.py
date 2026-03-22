"""
bot.py — Bookshelf Catalog Telegram Bot

Main bot entry point. Handles:
    /start — welcome message and instructions
    /sheet — link to the Google Spreadsheet
    Photo messages — recognize books and add to catalog

Flow:
    User sends photo
    → Bot: "Processing..."
    → vision.recognize_books() via VLM
    → books_api.enrich_books() via Google Books API
    → sheets.add_books() with deduplication
    → Bot: summary of added/skipped books
"""

import asyncio
import logging
import os
import sys
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

# Load .env from the same directory as this file
load_dotenv(Path(__file__).parent / ".env")

# Set up logging
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Suppress noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Import our modules
from books_api import enrich_books
from sheets import BookshelfSheet
from vision import recognize_books

# Configuration from environment
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")

# Global sheet instance (initialized on first use)
_sheet: BookshelfSheet = None


def get_sheet() -> BookshelfSheet:
    """Get or create the global BookshelfSheet instance."""
    global _sheet
    if _sheet is None:
        _sheet = BookshelfSheet(sheet_name=GOOGLE_SHEET_NAME)
    return _sheet


# ─── Command Handlers ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command — welcome and instructions."""
    text = (
        "📚 <b>Bookshelf Catalog Bot</b>\n\n"
        "I recognize books from photos of your bookshelf and save them to Google Sheets.\n\n"
        "<b>How to use:</b>\n"
        "1. Take a photo of your bookshelf (book spines facing you)\n"
        "2. Send the photo to this chat\n"
        "3. I'll recognize the books and add them to your catalog\n\n"
        "<b>Commands:</b>\n"
        "/sheet — get the link to your catalog\n"
        "/start — show this message\n\n"
        "📷 <i>Send me a photo to get started!</i>"
    )
    await update.message.reply_html(text)


async def cmd_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sheet command — return link to the Google Spreadsheet."""
    await update.message.reply_text("🔍 Looking up your catalog...")

    try:
        sheet = get_sheet()
        url = sheet.get_url()
        await update.message.reply_html(
            f"📊 <b>Your Bookshelf Catalog:</b>\n{url}"
        )
    except FileNotFoundError:
        await update.message.reply_text(
            "❌ Google Sheets not configured.\n\n"
            "Please run: python bookshelf/oauth_setup.py"
        )
    except Exception as e:
        logger.error(f"Error getting sheet URL: {e}")
        await update.message.reply_text(
            f"❌ Error accessing Google Sheets:\n{e}"
        )


# ─── Photo Handler ─────────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle incoming photo:
    1. Download the largest available photo
    2. Recognize books with VLM
    3. Enrich with Google Books API
    4. Add to Google Sheets with deduplication
    5. Report back to user
    """
    # Send initial feedback
    processing_msg = await update.message.reply_text(
        "📷 Analyzing your bookshelf... This may take a moment."
    )

    try:
        # ── Step 1: Download photo ───────────────────────────────────────────
        # Telegram sends multiple sizes; pick the largest (best quality)
        photo = update.message.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)
        image_bytes = await photo_file.download_as_bytearray()
        image_bytes = bytes(image_bytes)
        logger.info(f"Downloaded photo: {len(image_bytes)} bytes")

        # ── Step 2: Recognize books with VLM ────────────────────────────────
        await processing_msg.edit_text("🔍 Recognizing books...")

        raw_books = await recognize_books(image_bytes)
        logger.info(f"VLM recognized {len(raw_books)} books")

        if not raw_books:
            await processing_msg.edit_text(
                "🤷 I couldn't identify any books in this photo.\n\n"
                "Tips for better results:\n"
                "• Make sure book spines are clearly visible\n"
                "• Good lighting helps a lot\n"
                "• Avoid extreme angles"
            )
            return

        # ── Step 3: Enrich with Google Books API ────────────────────────────
        await processing_msg.edit_text(
            f"📚 Found {len(raw_books)} book(s)! Looking up details..."
        )

        enriched_books = await enrich_books(raw_books)
        logger.info(f"Enriched {len(enriched_books)} books")

        # ── Step 4: Add to Google Sheets ────────────────────────────────────
        try:
            sheet = get_sheet()
            added, skipped = sheet.add_books(enriched_books)
        except FileNotFoundError:
            await processing_msg.edit_text(
                f"📚 Recognized {len(raw_books)} book(s), but Google Sheets is not configured.\n\n"
                "Run: python bookshelf/oauth_setup.py\n\n"
                "<b>Books found:</b>\n" + _format_book_list(enriched_books),
            )
            return

        # ── Step 5: Report results ──────────────────────────────────────────
        response = _build_response(added, skipped, sheet)
        await processing_msg.edit_text(response, parse_mode="HTML")

    except FileNotFoundError as e:
        await processing_msg.edit_text(
            f"⚙️ Setup required:\n{e}\n\n"
            "Run: python bookshelf/oauth_setup.py"
        )
    except RuntimeError as e:
        await processing_msg.edit_text(f"❌ Error: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error processing photo: {e}")
        await processing_msg.edit_text(
            f"❌ Unexpected error: {e}\n\n"
            "Please try again. If the problem persists, check the bot logs."
        )


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _format_book_list(books: list[dict], max_books: int = 20) -> str:
    """Format a list of books as a readable string."""
    lines = []
    for i, book in enumerate(books[:max_books], 1):
        title = book.get("Title", "Unknown")
        author = book.get("Author", "")
        year = book.get("Year", "")

        line = f"{i}. <b>{_escape_html(title)}</b>"
        if author:
            line += f" — {_escape_html(author)}"
        if year:
            line += f" ({year})"
        lines.append(line)

    if len(books) > max_books:
        lines.append(f"... and {len(books) - max_books} more")

    return "\n".join(lines)


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def _build_response(
    added: list[dict],
    skipped: list[dict],
    sheet: BookshelfSheet,
) -> str:
    """Build the response message after adding books."""
    parts = []

    if added:
        parts.append(f"✅ <b>Added {len(added)} book(s):</b>")
        parts.append(_format_book_list(added))
    else:
        parts.append("ℹ️ No new books added.")

    if skipped:
        skipped_titles = [b.get("Title", "?") for b in skipped[:5]]
        skipped_str = ", ".join(_escape_html(t) for t in skipped_titles)
        if len(skipped) > 5:
            skipped_str += f" and {len(skipped) - 5} more"
        parts.append(f"\n⏭️ <i>Skipped {len(skipped)} duplicate(s): {skipped_str}</i>")

    try:
        url = sheet.get_url()
        parts.append(f"\n📊 <a href='{url}'>Open Catalog</a>")
    except Exception:
        pass

    return "\n".join(parts)


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """Start the Telegram bot."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set!")
        logger.error("Create bookshelf/.env with: TELEGRAM_BOT_TOKEN=your_token_here")
        sys.exit(1)

    logger.info("Starting Bookshelf Catalog Bot...")
    logger.info(f"Sheet name: {GOOGLE_SHEET_NAME}")

    # Build application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("sheet", cmd_sheet))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Start polling
    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,  # Don't process messages sent while bot was offline
    )


if __name__ == "__main__":
    main()
