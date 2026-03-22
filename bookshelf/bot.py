"""
bot.py — Bookshelf Catalog Telegram Bot

Commands:
    /start — Welcome message with instructions
    /sheet — Show the Google Sheets catalog URL

Photos:
    User sends a photo of a bookshelf →
    Bot recognizes books with VLM →
    Enriches metadata via Google Books API →
    Adds to Google Sheets with deduplication →
    Reports results

Setup:
    1. Copy .env.example to .env and fill in your values
    2. Run oauth_setup.py once to authorize Google Sheets
    3. python bookshelf/bot.py
"""

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

from books_api import enrich_books
from sheets import BookshelfSheet
from vision import recognize_books

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Reuse a single BookshelfSheet instance across requests
_sheet = BookshelfSheet()


# ── Handlers ──────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message."""
    text = (
        "📚 <b>Bookshelf Catalog Bot</b>\n\n"
        "I can catalog books from photos of your bookshelf.\n\n"
        "<b>How to use:</b>\n"
        "1. Take a photo of your bookshelf (spines visible)\n"
        "2. Send the photo to me\n"
        "3. I'll recognize the books and add them to your Google Sheets catalog\n\n"
        "<b>Commands:</b>\n"
        "/start — Show this message\n"
        "/sheet — Get the catalog URL\n\n"
        "📷 Send your first photo to get started!"
    )
    await update.message.reply_html(text)


async def cmd_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the catalog spreadsheet URL."""
    await update.message.reply_text("🔍 Looking up your catalog...")

    try:
        url = _sheet.get_url()
        await update.message.reply_html(
            f"📊 <b>Your catalog:</b>\n{url}"
        )
    except FileNotFoundError as e:
        await update.message.reply_html(
            "❌ <b>Google Sheets not set up yet.</b>\n\n"
            "Run this command once:\n"
            "<code>python bookshelf/oauth_setup.py</code>"
        )
        logger.error(f"Sheet access error: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        logger.error(f"Unexpected error in /sheet: {e}", exc_info=True)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process a bookshelf photo."""
    logger.info(f"Photo received from user {update.effective_user.id}")

    # Send acknowledgement immediately
    processing_msg = await update.message.reply_text(
        "📷 Got your photo! Analyzing the bookshelf..."
    )

    # Step 1: Download photo (get the highest resolution version)
    photo = update.message.photo[-1]
    photo_file = await context.bot.get_file(photo.file_id)
    image_bytes = await photo_file.download_as_bytearray()
    image_bytes = bytes(image_bytes)

    logger.info(f"Downloaded photo: {len(image_bytes)} bytes")

    # Step 2: Recognize books with VLM
    await processing_msg.edit_text("🔍 Recognizing books with AI...")

    try:
        raw_books = await recognize_books(image_bytes)
    except RuntimeError as e:
        await processing_msg.edit_text(
            f"❌ <b>Book recognition failed:</b>\n{e}\n\n"
            "Make sure OPENROUTER_API_KEY is set in bookshelf/.env",
            parse_mode="HTML",
        )
        return

    if not raw_books:
        await processing_msg.edit_text(
            "😕 <b>No books found in this photo.</b>\n\n"
            "Tips for better recognition:\n"
            "• Make sure book spines are clearly visible\n"
            "• Good lighting helps a lot\n"
            "• Try a closer shot of the books",
            parse_mode="HTML",
        )
        return

    logger.info(f"Recognized {len(raw_books)} book(s)")

    # Step 3: Enrich with Google Books metadata
    await processing_msg.edit_text(
        f"📖 Found {len(raw_books)} book(s)! Looking up details..."
    )

    enriched_books = await enrich_books(raw_books)

    # Step 4: Add to Google Sheets
    await processing_msg.edit_text("📊 Adding to your catalog...")

    try:
        added, skipped = _sheet.add_books(enriched_books)
    except FileNotFoundError:
        await processing_msg.edit_text(
            "❌ <b>Google Sheets not authorized.</b>\n\n"
            "Run this first:\n"
            "<code>python bookshelf/oauth_setup.py</code>",
            parse_mode="HTML",
        )
        return
    except Exception as e:
        await processing_msg.edit_text(f"❌ Failed to save to Google Sheets: {e}")
        logger.error(f"Sheets error: {e}", exc_info=True)
        return

    # Step 5: Send result
    result_text = _format_result(added, skipped)
    await processing_msg.edit_text(result_text, parse_mode="HTML")


def _format_result(added: list[dict], skipped: list[dict]) -> str:
    """Format the result message."""
    total = len(added) + len(skipped)

    if not added and not skipped:
        return "😕 No books were processed."

    lines = []

    if added:
        lines.append(f"✅ <b>Added {len(added)} book(s):</b>")
        for book in added:
            title = book.get("Title", "Unknown")
            author = book.get("Author", "")
            year = book.get("Year", "")

            entry = f"• <b>{title}</b>"
            if author:
                entry += f" — {author}"
            if year:
                entry += f" ({year})"
            lines.append(entry)

    if skipped:
        lines.append(f"\n⏭️ <b>Skipped {len(skipped)} duplicate(s):</b>")
        for book in skipped:
            title = book.get("Title", "Unknown")
            lines.append(f"• {title}")

    lines.append(f"\n📊 Use /sheet to view your full catalog.")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Add it to bookshelf/.env")
        return

    logger.info("Starting Bookshelf Catalog Bot...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("sheet", cmd_sheet))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
