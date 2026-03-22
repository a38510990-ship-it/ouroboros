"""
bot.py — Bookshelf Catalog Telegram Bot

Main entry point. Runs the bot and dispatches incoming messages.

Commands:
    /start  — Welcome message and usage instructions
    /sheet  — Show the link to the catalog spreadsheet
    /help   — Same as /start

Photo handling:
    User sends a photo → VLM recognizes books → Google Books enriches →
    New books added to spreadsheet (with deduplication) → Reply with summary

Environment variables (bookshelf/.env):
    TELEGRAM_BOT_TOKEN   — Bot token from @BotFather
    OPENROUTER_API_KEY   — OpenRouter API key
    VLM_MODEL            — Vision model (default: google/gemini-2.0-flash-001)
    GOOGLE_SHEET_NAME    — Spreadsheet name (default: Bookshelf Catalog)

First-time setup:
    1. pip install -r bookshelf/requirements.txt
    2. python bookshelf/oauth_setup.py
    3. Create bookshelf/.env with your tokens
    4. python bookshelf/bot.py
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

from books_api import enrich_books
from sheets import BookshelfSheet
from vision import recognize_books

# ── Setup ──────────────────────────────────────────────────────────────────────

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Bookshelf Catalog")

TOKEN_PATH = HERE / "token.json"

# ── Helpers ────────────────────────────────────────────────────────────────────


def _check_setup() -> str | None:
    """
    Check if the bot is properly configured.

    Returns a human-readable error message if something is wrong,
    or None if everything is OK.
    """
    if not TELEGRAM_BOT_TOKEN:
        return "TELEGRAM_BOT_TOKEN is not set. Add it to bookshelf/.env"

    if not os.getenv("OPENROUTER_API_KEY"):
        return "OPENROUTER_API_KEY is not set. Add it to bookshelf/.env"

    if not TOKEN_PATH.exists():
        return (
            "Google Sheets not authorized.\n"
            "Run: python bookshelf/oauth_setup.py"
        )

    return None


def _get_sheet() -> BookshelfSheet:
    """Get a BookshelfSheet instance (created fresh per request for simplicity)."""
    return BookshelfSheet(sheet_name=GOOGLE_SHEET_NAME)


# ── Command handlers ───────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start and /help commands."""
    text = (
        "📚 <b>Bookshelf Catalog Bot</b>\n\n"
        "I recognize books from photos of your bookshelf and add them to a Google Sheets catalog.\n\n"
        "<b>How to use:</b>\n"
        "1. Take a photo of your bookshelf (spines facing you)\n"
        "2. Send the photo to this chat\n"
        "3. I'll recognize the books and add them to your catalog\n\n"
        "<b>Commands:</b>\n"
        "/sheet — Show link to your catalog spreadsheet\n"
        "/help — Show this message\n\n"
        "📎 <i>Each photo adds new books. Already-cataloged books are skipped automatically.</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sheet command — show the spreadsheet URL."""
    await update.message.reply_text("🔍 Opening your catalog...")

    try:
        sheet = _get_sheet()
        url = sheet.get_url()
        await update.message.reply_text(
            f"📊 <b>Your Bookshelf Catalog:</b>\n{url}",
            parse_mode=ParseMode.HTML,
        )
    except FileNotFoundError:
        await update.message.reply_text(
            "❌ Google Sheets is not authorized yet.\n\n"
            "Run this command on your machine:\n"
            "<code>python bookshelf/oauth_setup.py</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error(f"Error in /sheet: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Could not open spreadsheet: {e}"
        )


# ── Photo handler ──────────────────────────────────────────────────────────────


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle incoming photos.

    Flow:
    1. Download photo (highest resolution)
    2. Send to VLM for book recognition
    3. Enrich with Google Books API
    4. Add to Google Sheets (with dedup)
    5. Reply with summary
    """
    msg = update.message
    await msg.reply_text("📷 Processing your photo... This may take 10-30 seconds.")

    # ── Step 1: Download photo ─────────────────────────────────────────────────
    try:
        # Telegram sends multiple sizes; use the largest one
        photo = msg.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()
        image_bytes = bytes(image_bytes)
        logger.info(f"Downloaded photo: {len(image_bytes)} bytes")
    except Exception as e:
        logger.error(f"Failed to download photo: {e}", exc_info=True)
        await msg.reply_text("❌ Failed to download your photo. Please try again.")
        return

    # ── Step 2: Recognize books with VLM ──────────────────────────────────────
    await msg.reply_text("🔍 Recognizing books...")
    try:
        raw_books = await recognize_books(image_bytes)
    except RuntimeError as e:
        await msg.reply_text(f"❌ Book recognition failed:\n{e}")
        return
    except Exception as e:
        logger.error(f"Unexpected VLM error: {e}", exc_info=True)
        await msg.reply_text(
            "❌ Something went wrong during book recognition. Please try again."
        )
        return

    if not raw_books:
        await msg.reply_text(
            "🤷 I couldn't recognize any books in this photo.\n\n"
            "Tips:\n"
            "• Make sure book spines are clearly visible\n"
            "• Use good lighting\n"
            "• Try a closer shot"
        )
        return

    await msg.reply_text(f"📖 Recognized {len(raw_books)} book(s). Looking up details...")

    # ── Step 3: Enrich with Google Books ──────────────────────────────────────
    try:
        books = await enrich_books(raw_books)
    except Exception as e:
        logger.error(f"Google Books enrichment failed: {e}", exc_info=True)
        # Use raw data as fallback
        books = [
            {"Title": b["title"], "Author": b["author"],
             "Year": "", "ISBN": "", "Genre": "", "Pages": "", "Cover URL": ""}
            for b in raw_books
        ]

    # ── Step 4: Add to Google Sheets ──────────────────────────────────────────
    try:
        sheet = _get_sheet()
        added, skipped = sheet.add_books(books)
    except FileNotFoundError:
        await msg.reply_text(
            "❌ Google Sheets is not authorized.\n\n"
            "Run: <code>python bookshelf/oauth_setup.py</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception as e:
        logger.error(f"Failed to write to Google Sheets: {e}", exc_info=True)
        await msg.reply_text(
            f"❌ Could not write to Google Sheets: {e}\n\n"
            "Books recognized but not saved."
        )
        return

    # ── Step 5: Reply with summary ─────────────────────────────────────────────
    reply = _build_reply(added, skipped, sheet.get_url())
    await msg.reply_text(reply, parse_mode=ParseMode.HTML)


def _build_reply(added: list[dict], skipped: list[dict], sheet_url: str) -> str:
    """Build a human-readable summary of the operation."""
    lines = []

    if added:
        lines.append(f"✅ <b>Added {len(added)} book(s):</b>")
        for book in added:
            title = book.get("Title", "Unknown")
            author = book.get("Author", "")
            year = book.get("Year", "")
            if author and year:
                lines.append(f"  • {title} — {author} ({year})")
            elif author:
                lines.append(f"  • {title} — {author}")
            else:
                lines.append(f"  • {title}")

    if skipped:
        lines.append(f"\n⏭ <b>Skipped {len(skipped)} duplicate(s):</b>")
        for book in skipped:
            lines.append(f"  • {book.get('Title', 'Unknown')}")

    if not added and not skipped:
        lines.append("🤷 No books were processed.")

    lines.append(f"\n📊 <a href=\"{sheet_url}\">View your catalog</a>")

    return "\n".join(lines)


# ── Unsupported message types ──────────────────────────────────────────────────


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle documents (e.g., images sent as files)."""
    doc = update.message.document
    if doc and doc.mime_type and doc.mime_type.startswith("image/"):
        await update.message.reply_text(
            "📎 Please send the image as a <b>photo</b>, not as a file.\n"
            "In Telegram, choose 'Photo' instead of 'File' when attaching.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            "I only process photos of bookshelves. "
            "Send me a photo and I'll catalog the books! 📚"
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages."""
    await update.message.reply_text(
        "Send me a photo of your bookshelf and I'll catalog the books! 📚\n"
        "Use /help to see all commands."
    )


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    """Start the bot."""
    # Pre-flight checks
    error = _check_setup()
    if error:
        logger.error(f"Setup error: {error}")
        print(f"\n❌ Setup error: {error}\n")
        return

    logger.info(f"Starting Bookshelf Catalog Bot (sheet: '{GOOGLE_SHEET_NAME}')")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Register handlers
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("sheet", cmd_sheet))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
